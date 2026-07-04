from __future__ import annotations

from collections import defaultdict
import contextlib
import io
from datetime import date, timedelta
from typing import Callable

from beichen_alpha.models import Bar, SectorSignal, StockProfile
from beichen_alpha.profile_tags import profile_industry_candidates

from .akshare_source import import_akshare


class AkshareSectorRotationSource:
    def __init__(
        self,
        limit: int = 40,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> None:
        self.limit = limit
        self.start_date = start_date or default_start_date()
        self.end_date = end_date or date.today().strftime("%Y%m%d")

    def load(self) -> dict[str, SectorSignal]:
        ak = import_akshare()
        try:
            spot_rows = fetch_industry_spot_rows(ak)
            selected_rows = select_industry_rows(spot_rows, limit=self.limit)
            signals = []
            for rank, row in enumerate(selected_rows, 1):
                history_rows = fetch_industry_history_rows(
                    ak,
                    row["name"],
                    start_date=self.start_date,
                    end_date=self.end_date,
                )
                signal = score_sector_history(row["name"], history_rows, rank=rank, pct_1d=row.get("pct_change"))
                signals.append(signal)
        except Exception:
            return {}
        return merge_sector_signals(signals)


def fetch_industry_spot_rows(ak) -> list[dict]:
    frame = quiet_call(ak.stock_board_industry_name_em)
    rows = []
    for record in frame.to_dict(orient="records"):
        name = str(first_value(record, "板块名称", "名称", "name") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "pct_change": to_optional_float(first_value(record, "涨跌幅", "涨幅")),
                "turnover": to_float(first_value(record, "成交额", "成交金额")),
            }
        )
    return rows


def fetch_industry_history_rows(ak, name: str, start_date: str, end_date: str) -> list[dict]:
    frame = quiet_call(
        ak.stock_board_industry_hist_em,
        symbol=name,
        start_date=start_date,
        end_date=end_date,
        period="日k",
        adjust="",
    )
    rows = []
    for record in frame.to_dict(orient="records"):
        close = to_optional_float(first_value(record, "收盘", "close"))
        if close is None:
            continue
        rows.append(
            {
                "date": str(first_value(record, "日期", "date") or ""),
                "close": close,
                "amount": to_float(first_value(record, "成交额", "amount")),
            }
        )
    return rows


def select_industry_rows(rows: list[dict], limit: int) -> list[dict]:
    filtered = [row for row in rows if normalize_sector_name(row["name"])]
    sorted_rows = sorted(
        filtered,
        key=lambda row: (row.get("turnover", 0.0), row.get("pct_change") or -99.0),
        reverse=True,
    )
    return sorted_rows[:limit]


def score_sector_history(
    name: str,
    history_rows: list[dict],
    rank: int | None = None,
    pct_1d: float | None = None,
) -> SectorSignal:
    normalized_name = normalize_sector_name(name)
    closes = [to_float(row.get("close")) for row in history_rows if to_float(row.get("close")) > 0]
    amounts = [to_float(row.get("amount")) for row in history_rows if to_float(row.get("amount")) > 0]
    return_3d = pct_return(closes, 3)
    return_5d = pct_return(closes, 5)
    amount_ratio = calc_amount_ratio(amounts)

    momentum_value = return_3d if return_3d is not None else ((pct_1d or 0.0) / 100)
    score = 0
    if momentum_value >= 0.04:
        score += 18
    elif momentum_value >= 0.02:
        score += 12
    elif momentum_value > 0:
        score += 5
    elif momentum_value <= -0.04:
        score -= 14
    elif momentum_value < 0:
        score -= 5

    if return_5d is not None:
        if return_5d >= 0.06:
            score += 8
        elif return_5d <= -0.05:
            score -= 8

    if amount_ratio is not None:
        if amount_ratio >= 1.25:
            score += 8
        elif amount_ratio >= 1.1:
            score += 4
        elif amount_ratio < 0.75:
            score -= 4

    if rank is not None:
        if rank <= 10:
            score += 5
        elif rank <= 20:
            score += 2

    detail = (
        f"{name}: 3日 {format_pct(return_3d)}, 5日 {format_pct(return_5d)}, "
        f"量能 {amount_ratio:.2f}x" if amount_ratio is not None else
        f"{name}: 3日 {format_pct(return_3d)}, 5日 {format_pct(return_5d)}"
    )
    return SectorSignal(
        name=normalized_name,
        score=max(min(score, 35), -25),
        return_3d=return_3d,
        return_5d=return_5d,
        amount_ratio=amount_ratio,
        rank=rank,
        detail=detail,
    )


def merge_sector_signals(signals: list[SectorSignal]) -> dict[str, SectorSignal]:
    merged: dict[str, SectorSignal] = {}
    for signal in signals:
        if not signal.name:
            continue
        old = merged.get(signal.name)
        if old is None or signal.score > old.score:
            merged[signal.name] = signal
    return merged


def build_sector_signals_from_price_map(
    price_map: dict[str, list[Bar]],
    profiles: dict[str, StockProfile],
    benchmark_code: str = "000300",
    min_members: int = 1,
) -> dict[str, SectorSignal]:
    groups: dict[str, list[list[Bar]]] = defaultdict(list)
    for code, bars in price_map.items():
        if code == benchmark_code:
            continue
        profile = profiles.get(code)
        sector = normalize_profile_sector(profile)
        if sector and len(bars) >= 6:
            groups[sector].append(bars)

    metrics = []
    for sector, bars_group in groups.items():
        if len(bars_group) < min_members:
            continue
        ret_3d_values = [bar_pct_return(bars, 3) for bars in bars_group]
        ret_5d_values = [bar_pct_return(bars, 5) for bars in bars_group]
        amount_ratios = [bar_amount_ratio(bars) for bars in bars_group]
        recent_amount = sum(bars[-1].amount for bars in bars_group if bars)
        metrics.append(
            {
                "sector": sector,
                "return_3d": average_known(ret_3d_values),
                "return_5d": average_known(ret_5d_values),
                "amount_ratio": average_known(amount_ratios),
                "recent_amount": recent_amount,
                "members": len(bars_group),
            }
        )

    metrics = sorted(metrics, key=lambda row: row["recent_amount"], reverse=True)
    signals = []
    for rank, row in enumerate(metrics, 1):
        signals.append(
            score_sector_metrics(
                row["sector"],
                return_3d=row["return_3d"],
                return_5d=row["return_5d"],
                amount_ratio=row["amount_ratio"],
                rank=rank,
                label=f"候选池聚合({row['members']}只)",
            )
        )
    return merge_sector_signals(signals)


def score_sector_metrics(
    name: str,
    return_3d: float | None,
    return_5d: float | None,
    amount_ratio: float | None,
    rank: int | None = None,
    label: str | None = None,
) -> SectorSignal:
    normalized_name = normalize_sector_name(name)
    momentum_value = return_3d or 0.0
    score = 0
    if momentum_value >= 0.04:
        score += 18
    elif momentum_value >= 0.02:
        score += 12
    elif momentum_value > 0:
        score += 5
    elif momentum_value <= -0.04:
        score -= 14
    elif momentum_value < 0:
        score -= 5

    if return_5d is not None:
        if return_5d >= 0.06:
            score += 8
        elif return_5d <= -0.05:
            score -= 8

    if amount_ratio is not None:
        if amount_ratio >= 1.25:
            score += 8
        elif amount_ratio >= 1.1:
            score += 4
        elif amount_ratio < 0.75:
            score -= 4

    if rank is not None:
        if rank <= 10:
            score += 5
        elif rank <= 20:
            score += 2

    prefix = f"{label}: " if label else f"{name}: "
    detail = (
        f"{prefix}3日 {format_pct(return_3d)}, 5日 {format_pct(return_5d)}, "
        f"量能 {amount_ratio:.2f}x" if amount_ratio is not None else
        f"{prefix}3日 {format_pct(return_3d)}, 5日 {format_pct(return_5d)}"
    )
    return SectorSignal(
        name=normalized_name,
        score=max(min(score, 35), -25),
        return_3d=return_3d,
        return_5d=return_5d,
        amount_ratio=amount_ratio,
        rank=rank,
        detail=detail,
    )


def normalize_profile_sector(profile: StockProfile | None) -> str:
    if profile is None:
        return ""
    for item in profile_industry_candidates(profile):
        normalized = normalize_sector_name(item)
        if normalized:
            return normalized
    return ""


def normalize_sector_name(name: str) -> str:
    text = str(name or "")
    mapping = (
        (("银行",), "银行"),
        (("非银金融", "证券", "券商", "多元金融", "互联网金融", "金融服务"), "非银金融"),
        (("保险",), "保险"),
        (("电力", "公用事业", "燃气", "水务"), "公用事业"),
        (("煤炭",), "煤炭"),
        (("石油", "石化", "油气"), "石油石化"),
        (("贵金属", "黄金"), "黄金"),
        (("有色", "小金属", "工业金属", "稀土", "能源金属", "铜业", "铝业", "钼业", "锂矿", "锂业", "钴", "钨"), "工业金属"),
        (("材料", "新材料", "电子化学品", "化学制品", "氟化工", "铜箔", "液冷"), "材料"),
        (("资源",), "资源"),
        (("先进制造", "高端制造", "设备更新", "工业母机"), "先进制造"),
        (("基建", "建筑", "建筑工程", "工程机械", "水利", "轨交"), "基建"),
        (("房地产", "地产", "建材", "家居"), "房地产"),
        (("半导体", "芯片"), "半导体"),
        (("AI硬件", "算力", "CPO", "光模块", "光通信"), "AI硬件"),
        (("通信",), "通信"),
        (("电子", "电子元件", "消费电子", "光学光电子", "PCB"), "电子"),
        (("电池", "光伏", "风电", "新能源"), "新能源"),
        (("化学", "化工", "化肥", "塑料", "橡胶"), "化工"),
        (("医药", "创新药", "生物制品", "医疗器械", "中药"), "医药"),
        (("软件", "互联网", "计算机", "数字", "游戏"), "数字经济"),
        (("白酒", "食品", "饮料", "家电", "旅游", "商业百货", "物流"), "消费"),
    )
    for keywords, normalized in mapping:
        if any(keyword in text for keyword in keywords):
            return normalized
    return ""


def pct_return(values: list[float], window: int) -> float | None:
    if len(values) <= window:
        return None
    start = values[-window - 1]
    end = values[-1]
    if start <= 0:
        return None
    return end / start - 1


def calc_amount_ratio(amounts: list[float]) -> float | None:
    if len(amounts) < 6:
        return None
    recent = sum(amounts[-3:]) / 3
    base = sum(amounts[-8:-3]) / min(len(amounts[-8:-3]), 5)
    if base <= 0:
        return None
    return recent / base


def bar_pct_return(bars: list[Bar], window: int) -> float | None:
    if len(bars) <= window:
        return None
    start = bars[-window - 1].close
    end = bars[-1].close
    if start <= 0:
        return None
    return end / start - 1


def bar_amount_ratio(bars: list[Bar]) -> float | None:
    amounts = [bar.amount for bar in bars]
    return calc_amount_ratio(amounts)


def average_known(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    if not known:
        return None
    return sum(known) / len(known)


def first_value(record: dict, *keys: str):
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def quiet_call(func: Callable, **kwargs):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return func(**kwargs)


def to_float(value) -> float:
    parsed = to_optional_float(value)
    return 0.0 if parsed is None else parsed


def to_optional_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(str(value).replace("%", ""))
    except (TypeError, ValueError):
        return None


def format_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.2%}"


def default_start_date() -> str:
    return (date.today() - timedelta(days=45)).strftime("%Y%m%d")
