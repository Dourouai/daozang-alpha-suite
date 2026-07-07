"""Flow data source: 龙虎榜、北向资金、主力资金流向.

Fetches capital-flow data from AKShare for A-share candidates.
All sources are free. Eastmoney-based APIs may fail behind proxies;
the source degrades gracefully — missing data = neutral in scoring.

Verified APIs (AKShare 1.18.64):
- LHB: stock_lhb_detail_em(start_date, end_date) — daily list with net buy/sell
- Northbound: stock_hsgt_individual_em(symbol) — per-stock HSGT flow history
- Fund flow: stock_individual_fund_flow_rank(indicator) — eastmoney, proxy-sensitive
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .akshare_source import import_akshare


# ---------------------------------------------------------------------------
# Normalized data structures
# ---------------------------------------------------------------------------


@dataclass
class LhbRecord:
    """Single 龙虎榜 record for one stock on one day."""

    code: str
    name: str
    trade_date: date
    net_amount: float = 0.0       # 净买额（元）
    buy_amount: float = 0.0       # 买入额（元）
    sell_amount: float = 0.0      # 卖出额（元）
    reason: str = ""              # 上榜原因
    interpretation: str = ""      # 解读（如"机构席位买入"）
    change_pct: float = 0.0       # 涨跌幅


@dataclass
class NorthboundRecord:
    """Single 北向资金 record for one stock."""

    code: str
    trade_date: date | None = None
    net_buy_10k: float = 0.0      # 增持资金（万元）
    hold_shares: float = 0.0      # 持股数量
    hold_pct: float = 0.0         # 持股占A股百分比
    close: float = 0.0            # 当日收盘价
    consecutive_days: int = 0     # 连续净买入天数


@dataclass
class FundFlowRecord:
    """Single 主力资金流 record for one stock."""

    code: str
    name: str = ""
    trade_date: date | None = None
    main_net_inflow: float = 0.0  # 主力净流入（万元）


@dataclass
class FlowSnapshot:
    """Aggregated flow data for a set of stocks."""

    as_of: datetime
    lhb_records: dict[str, list[LhbRecord]] = field(default_factory=dict)
    northbound_records: dict[str, list[NorthboundRecord]] = field(default_factory=dict)
    fund_flow_records: dict[str, list[FundFlowRecord]] = field(default_factory=dict)
    source_health: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class AkshareFlowSource:
    """Fetch 龙虎榜 + 北向资金 + 主力资金流 from AKShare.

    Usage::

        source = AkshareFlowSource(symbols=["600036"], as_of=datetime.now())
        snapshot = source.load()
    """

    def __init__(
        self,
        symbols: list[str],
        as_of: datetime | None = None,
        lhb_lookback_days: int = 5,
        northbound_lookback_days: int = 5,
        fund_flow_lookback_days: int = 3,
    ) -> None:
        self.symbols = symbols
        self.as_of = as_of or datetime.now()
        self.lhb_lookback = lhb_lookback_days
        self.northbound_lookback = northbound_lookback_days
        self.fund_flow_lookback = fund_flow_lookback_days

    def load(self) -> FlowSnapshot:
        ak = import_akshare()
        health: list[str] = []

        lhb = self._load_lhb(ak, health)
        northbound = self._load_northbound(ak, health)
        fund_flow = self._load_fund_flow(ak, health)

        return FlowSnapshot(
            as_of=self.as_of,
            lhb_records=lhb,
            northbound_records=northbound,
            fund_flow_records=fund_flow,
            source_health=health,
        )

    # ------------------------------------------------------------------
    # LHB — stock_lhb_detail_em (eastmoney daily list)
    # Falls back to stock_lhb_detail_daily_sina (sina-based)
    # ------------------------------------------------------------------

    def _load_lhb(self, ak, health: list[str]) -> dict[str, list[LhbRecord]]:
        result: dict[str, list[LhbRecord]] = defaultdict(list)
        target_codes = {_norm(c) for c in self.symbols}
        try:
            start = (self.as_of.date() - timedelta(days=self.lhb_lookback)).strftime("%Y%m%d")
            end = self.as_of.date().strftime("%Y%m%d")
            frame = _quiet(ak.stock_lhb_detail_em, start_date=start, end_date=end)
            if frame is None or frame.empty:
                # Fallback: Sina daily API
                frames = []
                for offset in range(self.lhb_lookback):
                    day = self.as_of.date() - timedelta(days=offset)
                    f = _quiet(ak.stock_lhb_detail_daily_sina, date=day.strftime("%Y%m%d"))
                    if f is not None and not f.empty:
                        frames.append(f)
                frame = _concat(frames) if frames else None

            if frame is None or frame.empty:
                health.append("龙虎榜: 0条")
                return dict(result)

            for _, row in frame.iterrows():
                code = _norm(_cell(row, "代码", "股票代码"))
                if code not in target_codes:
                    continue
                result[code].append(LhbRecord(
                    code=code,
                    name=str(_cell(row, "名称", "股票名称", "")),
                    trade_date=_pdate(_cell(row, "上榜日", "日期")),
                    net_amount=_fnum(row, "龙虎榜净买额", "净买入额"),
                    buy_amount=_fnum(row, "龙虎榜买入额", "买入总额"),
                    sell_amount=_fnum(row, "龙虎榜卖出额", "卖出总额"),
                    reason=str(_cell(row, "上榜原因", "指标", "")),
                    interpretation=str(_cell(row, "解读", "")),
                    change_pct=_fnum(row, "涨跌幅"),
                ))
            health.append(f"龙虎榜: {sum(len(v) for v in result.values())}条")
        except Exception as exc:
            health.append(f"龙虎榜: FAIL ({exc})")
        return dict(result)

    # ------------------------------------------------------------------
    # Northbound — stock_hsgt_individual_em(symbol) per stock
    # ------------------------------------------------------------------

    def _load_northbound(self, ak, health: list[str]) -> dict[str, list[NorthboundRecord]]:
        result: dict[str, list[NorthboundRecord]] = defaultdict(list)
        ok, fail = 0, 0
        for code in self.symbols:
            try:
                frame = _quiet(ak.stock_hsgt_individual_em, symbol=code)
            except Exception:
                fail += 1
                continue
            if frame is None or frame.empty:
                fail += 1
                continue

            # Sort by date descending, take recent N rows
            rows = list(frame.iterrows())
            date_col = "持股日期" if "持股日期" in frame.columns else frame.columns[0]
            try:
                rows.sort(key=lambda x: str(_cell(x[1], date_col)), reverse=True)
            except Exception:
                pass

            recent = rows[:self.northbound_lookback]
            prev_pos = None
            cons = 0
            for _, row in reversed(recent):  # chronological order for consecutive calc
                td = _pdate(_cell(row, "持股日期"))
                hold_shares = _fnum(row, "持股数量")
                hold_pct = _fnum(row, "持股数量占A股百分比")
                net_buy = _fnum(row, "今日增持资金") / 10000  # 元→万元
                close = _fnum(row, "当日收盘价")

                if net_buy > 0:
                    cons = cons + 1 if prev_pos else 1
                else:
                    cons = 0
                prev_pos = net_buy > 0

                result[code].append(NorthboundRecord(
                    code=code,
                    trade_date=td,
                    net_buy_10k=net_buy,
                    hold_shares=hold_shares,
                    hold_pct=hold_pct,
                    close=close,
                    consecutive_days=cons,
                ))
            ok += 1

        health.append(f"北向资金: {ok}只有数据, {fail}只无数据")
        return dict(result)

    # ------------------------------------------------------------------
    # Fund flow — eastmoney API, proxy-sensitive, gracefully degrades
    # ------------------------------------------------------------------

    def _load_fund_flow(self, ak, health: list[str]) -> dict[str, list[FundFlowRecord]]:
        result: dict[str, list[FundFlowRecord]] = defaultdict(list)
        try:
            frame = _quiet(ak.stock_individual_fund_flow_rank, indicator="今日")
            if frame is not None and not frame.empty:
                target_codes = {_norm(c) for c in self.symbols}
                for _, row in frame.iterrows():
                    code = _norm(_cell(row, "代码"))
                    if code not in target_codes:
                        continue
                    result[code].append(FundFlowRecord(
                        code=code,
                        name=str(_cell(row, "名称", "")),
                        trade_date=self.as_of.date(),
                        main_net_inflow=_fnum(row, "主力净流入", "主力净流入额") / 10000,
                    ))
                health.append(f"主力资金: {sum(len(v) for v in result.values())}条")
            else:
                health.append("主力资金: 0条")
        except Exception as exc:
            health.append(f"主力资金: UNAVAILABLE ({_short_err(exc)})")
        return dict(result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm(code: str) -> str:
    """Normalize to 6-digit string."""
    text = str(code).strip().replace(".", "").replace("'", "").replace('"', "")
    for prefix in ("sh", "sz", "SH", "SZ"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):]
            break
    return text.zfill(6)


def _cell(row: Any, *keys: str) -> Any:
    """Get cell by trying multiple column names (case-insensitive fallback)."""
    for key in keys:
        val = row.get(key)
        if val is not None and str(val) != "nan":
            return val
    row_lower = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        val = row_lower.get(key.lower())
        if val is not None and str(val) != "nan":
            return val
    return ""


def _fnum(row: Any, *keys: str) -> float:
    """Safely extract float."""
    val = _cell(row, *keys)
    if val == "" or val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _pdate(val: Any) -> date | None:
    """Parse date from various formats."""
    if val is None or str(val) in ("", "nan", "None"):
        return None
    if isinstance(val, (date, datetime)):
        return val if isinstance(val, date) else val.date()
    text = str(val).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _quiet(func, *args, **kwargs):
    """Call function, return DataFrame or None."""
    try:
        import pandas as pd
        result = func(*args, **kwargs)
        return result if isinstance(result, pd.DataFrame) else None
    except Exception:
        return None


def _concat(frames: list):
    """Concatenate DataFrames."""
    import pandas as pd
    non_empty = [f for f in frames if f is not None and not f.empty]
    return pd.concat(non_empty, ignore_index=True) if non_empty else None


def _short_err(exc: Exception) -> str:
    """Short error message for health logs."""
    msg = str(exc)
    return msg[:80] + "..." if len(msg) > 80 else msg
