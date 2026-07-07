from __future__ import annotations

import math
from datetime import date, datetime

from beichen_alpha.models import MarketStructureSnapshot

from .akshare_source import import_akshare
from .market_regime_source import fetch_market_spot_rows, summarize_spot_rows


class AkshareMarketStructureSource:
    def __init__(self, as_of: datetime | None = None) -> None:
        self.as_of = as_of or datetime.now()

    def load(self) -> MarketStructureSnapshot | None:
        ak = import_akshare()
        try:
            spot_rows = fetch_market_spot_rows(ak)
            breadth, limit_up_count, limit_down_count, turnover_100m = summarize_spot_rows(spot_rows)
        except Exception:
            breadth, limit_up_count, limit_down_count, turnover_100m = None, None, None, None

        margin = safe_margin_summary(ak)
        northbound = safe_northbound_summary(ak, as_of=self.as_of)
        if (
            breadth is None
            and margin["balance_100m"] is None
            and northbound["net_buy_100m"] is None
        ):
            return None

        detail = format_market_structure_detail(
            breadth=breadth,
            limit_up_count=limit_up_count,
            limit_down_count=limit_down_count,
            turnover_100m=turnover_100m,
            margin=margin,
            northbound=northbound,
        )
        return MarketStructureSnapshot(
            as_of=self.as_of,
            breadth=breadth,
            limit_up_count=limit_up_count,
            limit_down_count=limit_down_count,
            turnover_100m=turnover_100m,
            margin_balance_100m=margin["balance_100m"],
            margin_balance_change_pct=margin["balance_change_pct"],
            margin_buy_100m=margin["buy_100m"],
            margin_buy_turnover_ratio=calc_ratio(margin["buy_100m"], turnover_100m),
            northbound_net_buy_100m=northbound["net_buy_100m"],
            northbound_5d_net_buy_100m=northbound["net_buy_5d_100m"],
            detail=detail,
        )


def safe_margin_summary(ak) -> dict[str, float | None]:
    try:
        sh_records = frame_records(ak.macro_china_market_margin_sh())
    except Exception:
        sh_records = []
    try:
        sz_records = frame_records(ak.macro_china_market_margin_sz())
    except Exception:
        sz_records = []
    return build_margin_summary(sh_records, sz_records)


def build_margin_summary(*record_groups: list[dict]) -> dict[str, float | None]:
    series = []
    for records in record_groups:
        rows = []
        for record in records:
            record_date = parse_date(record.get("日期"))
            balance = normalize_amount_100m(to_optional_float(record.get("融资融券余额")))
            buy = normalize_amount_100m(to_optional_float(record.get("融资买入额")))
            if record_date is None or balance is None:
                continue
            rows.append((record_date, balance, buy or 0.0))
        rows.sort(key=lambda item: item[0])
        if rows:
            series.append(rows)

    if not series:
        return {"balance_100m": None, "balance_change_pct": None, "buy_100m": None}

    latest_date = max(rows[-1][0] for rows in series)
    previous_dates = [row[0] for rows in series for row in rows if row[0] < latest_date]
    previous_date = max(previous_dates) if previous_dates else None
    latest_rows = [row for rows in series if (row := last_on_or_before(rows, latest_date))]
    latest_balance = sum(row[1] for row in latest_rows)
    latest_buy = sum(row[2] for row in latest_rows)
    previous_balance = None
    if previous_date is not None:
        previous_rows = [row for rows in series if (row := last_on_or_before(rows, previous_date))]
        previous_balance = sum(row[1] for row in previous_rows)
    change_pct = calc_change_pct(latest_balance, previous_balance)
    return {
        "balance_100m": latest_balance or None,
        "balance_change_pct": change_pct,
        "buy_100m": latest_buy or None,
    }


def safe_northbound_summary(ak, as_of: datetime) -> dict[str, float | None]:
    try:
        records = frame_records(ak.stock_hsgt_hist_em(symbol="北向资金"))
    except Exception:
        records = []
    return build_northbound_summary(records, as_of=as_of)


def build_northbound_summary(records: list[dict], as_of: datetime) -> dict[str, float | None]:
    rows = []
    for record in records:
        record_date = parse_date(record.get("日期"))
        if record_date is None or record_date > as_of:
            continue
        net_buy = to_optional_float(record.get("当日成交净买额"))
        if net_buy is None:
            net_buy = to_optional_float(record.get("资金净流入"))
        if net_buy is None:
            continue
        rows.append((record_date, net_buy))
    rows.sort(key=lambda item: item[0])
    if not rows:
        return {"net_buy_100m": None, "net_buy_5d_100m": None}
    latest = rows[-1][1]
    last_5d = sum(item[1] for item in rows[-5:])
    return {"net_buy_100m": latest, "net_buy_5d_100m": last_5d}


def format_market_structure_detail(
    breadth: float | None,
    limit_up_count: int | None,
    limit_down_count: int | None,
    turnover_100m: float | None,
    margin: dict[str, float | None],
    northbound: dict[str, float | None],
) -> str:
    parts = []
    if breadth is not None:
        parts.append(f"上涨占比 {breadth:.0%}")
    if limit_up_count is not None or limit_down_count is not None:
        parts.append(f"涨停 {limit_up_count or 0}/跌停 {limit_down_count or 0}")
    if turnover_100m is not None:
        parts.append(f"成交额 {turnover_100m:.0f}亿")
    if margin["balance_100m"] is not None:
        change = format_pct(margin["balance_change_pct"])
        parts.append(f"两融余额 {margin['balance_100m']:.0f}亿({change})")
    if margin["buy_100m"] is not None:
        parts.append(f"融资买入 {margin['buy_100m']:.0f}亿")
    if northbound["net_buy_100m"] is not None:
        parts.append(f"北向净买 {northbound['net_buy_100m']:+.1f}亿")
    if northbound["net_buy_5d_100m"] is not None:
        parts.append(f"北向5日 {northbound['net_buy_5d_100m']:+.1f}亿")
    return "；".join(parts) if parts else "交易结构源暂无有效数据"


def frame_records(frame) -> list[dict]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        return [dict(item) for item in frame.to_dict(orient="records")]
    return [dict(item) for item in frame]


def last_on_or_before(rows: list[tuple[datetime, float, float]], target: datetime) -> tuple[datetime, float, float] | None:
    matches = [row for row in rows if row[0] <= target]
    return matches[-1] if matches else None


def parse_date(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def to_optional_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        number = float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_amount_100m(value: float | None) -> float | None:
    if value is None:
        return None
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return value / 100_000_000
    if magnitude >= 100_000:
        return value / 10_000
    return value


def calc_change_pct(latest: float | None, previous: float | None) -> float | None:
    if latest is None or previous is None or previous <= 0:
        return None
    return latest / previous - 1


def calc_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def format_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2%}"
