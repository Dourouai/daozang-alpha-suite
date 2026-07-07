"""Advanced data sources: 股东增减持 (P2).

Uses AKShare THS (同花顺) source which bypasses eastmoney proxy.
Concept board and block trade are eastmoney-based → unavailable behind proxy.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from .akshare_source import import_akshare


@dataclass
class ShareholderRecord:
    """股东增减持记录."""
    code: str
    name: str = ""
    announce_date: date | None = None
    shareholder: str = ""       # 变动股东
    change_text: str = ""       # 变动描述 (e.g. "减持531.56万")
    change_amount: float = 0.0  # 变动数量(万股), positive=增持, negative=减持
    avg_price: float = 0.0      # 交易均价
    period: str = ""            # 变动期间


@dataclass
class AdvancedSnapshot:
    as_of: datetime
    shareholder_records: dict[str, list[ShareholderRecord]] = field(default_factory=dict)
    source_health: list[str] = field(default_factory=list)


class AkshareAdvancedSource:
    """Fetch 股东增减持 data."""

    def __init__(self, symbols: list[str], as_of: datetime | None = None, lookback_months: int = 6):
        self.symbols = symbols
        self.as_of = as_of or datetime.now()
        self.lookback_months = lookback_months

    def load(self) -> AdvancedSnapshot:
        ak = import_akshare()
        health: list[str] = []
        result: dict[str, list[ShareholderRecord]] = {}

        cutoff = self.as_of.date() - timedelta(days=self.lookback_months * 30)
        ok, fail = 0, 0
        for code in self.symbols:
            try:
                df = _quiet(ak.stock_shareholder_change_ths, symbol=code)
                if df is None or df.empty:
                    fail += 1; continue
                records = []
                for _, row in df.iterrows():
                    ad = _pdate(row.get("公告日期"))
                    if ad and ad >= cutoff:
                        change_text = str(row.get("变动数量", ""))
                        amt = _parse_change_amount(change_text)
                        records.append(ShareholderRecord(
                            code=code,
                            announce_date=ad,
                            shareholder=str(row.get("变动股东", "")),
                            change_text=change_text,
                            change_amount=amt,
                            avg_price=_fnum(row, "交易均价"),
                            period=str(row.get("变动期间", "")),
                        ))
                if records:
                    result[code] = records
                ok += 1
            except Exception:
                fail += 1

        health.append(f"股东增减持: {ok}只有数据, {fail}只无数据")
        return AdvancedSnapshot(as_of=self.as_of, shareholder_records=result, source_health=health)


def _parse_change_amount(text: str) -> float:
    """Parse '减持531.56万' or '增持100万' → float in 万股."""
    import re
    text = str(text).strip()
    if not text:
        return 0.0
    sign = -1 if "减持" in text else 1
    match = re.search(r'[\d.]+', text)
    if not match:
        return 0.0
    val = float(match.group())
    if "亿" in text:
        val *= 10000
    return sign * val


def _quiet(func, *args, **kwargs):
    try:
        import pandas as pd
        r = func(*args, **kwargs)
        return r if isinstance(r, pd.DataFrame) else None
    except Exception:
        return None

def _fnum(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None and str(v) not in ("nan", "未披露", ""):
            try: return float(v)
            except: pass
    return 0.0

def _pdate(val):
    if val is None: return None
    if isinstance(val, (date, datetime)):
        return val if isinstance(val, date) else val.date()
    try: return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except: return None
