"""Sentiment & leverage data sources: 涨停板复盘、融资融券个股、股指期货升贴水.

P0 data sources for A-share short-term sentiment and leverage signals.
All use AKShare free APIs. Eastmoney-based sources may fail behind proxies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .akshare_source import import_akshare


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ZtRecord:
    """涨停板记录."""
    code: str
    name: str
    trade_date: date
    change_pct: float = 0.0        # 涨跌幅
    close: float = 0.0             # 最新价
    turnover_wan: float = 0.0      # 成交额(万元)
    float_mv_wan: float = 0.0      # 流通市值(万元)
    seal_amount_wan: float = 0.0   # 封板资金(万元)
    turnover_rate: float = 0.0     # 换手率


@dataclass
class MarginRecord:
    """融资融券个股记录."""
    code: str
    name: str
    trade_date: date
    margin_balance: float = 0.0        # 融资余额(万元)
    margin_buy: float = 0.0            # 融资买入额(万元)
    margin_repay: float = 0.0          # 融资偿还额(万元)
    short_balance: float = 0.0         # 融券余量(股)
    short_sell: float = 0.0            # 融券卖出量(股)
    net_margin_flow: float = 0.0       # 融资净买入(万元)


@dataclass
class FuturesBasis:
    """股指期货升贴水."""
    contract: str          # IF/IC/IM/IH
    trade_date: date
    futures_close: float
    spot_close: float      # 对应指数收盘
    basis_pct: float       # 升贴水率 = (期货-现货)/现货
    volume: float = 0.0
    open_interest: float = 0.0


@dataclass
class SentimentSnapshot:
    """P0 sentiment & leverage snapshot."""
    as_of: datetime
    zt_records: dict[str, list[ZtRecord]] = field(default_factory=dict)
    margin_records: dict[str, list[MarginRecord]] = field(default_factory=dict)
    futures_basis: list[FuturesBasis] = field(default_factory=list)
    source_health: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Data source
# ---------------------------------------------------------------------------

class AkshareSentimentSource:
    """Fetch 涨停板 + 融资融券个股 + 股指期货升贴水."""

    def __init__(
        self,
        symbols: list[str],
        as_of: datetime | None = None,
        zt_lookback_days: int = 1,
        margin_lookback_days: int = 3,
    ):
        self.symbols = symbols
        self.as_of = as_of or datetime.now()
        self.zt_lookback = zt_lookback_days
        self.margin_lookback = margin_lookback_days

    def load(self) -> SentimentSnapshot:
        ak = import_akshare()
        health: list[str] = []
        zt = self._load_zt(ak, health)
        margin = self._load_margin(ak, health)
        futures = self._load_futures_basis(ak, health)
        return SentimentSnapshot(
            as_of=self.as_of, zt_records=zt, margin_records=margin,
            futures_basis=futures, source_health=health,
        )

    def _load_zt(self, ak, health):
        result: dict[str, list[ZtRecord]] = {}
        target = {_norm(c) for c in self.symbols}
        try:
            for offset in range(self.zt_lookback):
                day = (self.as_of.date() - timedelta(days=offset)).strftime("%Y%m%d")
                df = _quiet(ak.stock_zt_pool_em, date=day)
                if df is None or df.empty:
                    continue
                for _, row in df.iterrows():
                    code = _norm(_cell(row, "代码"))
                    if code not in target:
                        continue
                    result.setdefault(code, []).append(ZtRecord(
                        code=code,
                        name=str(_cell(row, "名称", "")),
                        trade_date=self.as_of.date() - timedelta(days=offset),
                        change_pct=_fnum(row, "涨跌幅"),
                        close=_fnum(row, "最新价"),
                        turnover_wan=_fnum(row, "成交额"),
                        float_mv_wan=_fnum(row, "流通市值"),
                        seal_amount_wan=_fnum(row, "封板资金"),
                        turnover_rate=_fnum(row, "换手率"),
                    ))
            health.append(f"涨停板: {sum(len(v) for v in result.values())}条")
        except Exception as e:
            health.append(f"涨停板: FAIL ({e})")
        return result

    def _load_margin(self, ak, health):
        result: dict[str, list[MarginRecord]] = {}
        target = {_norm(c) for c in self.symbols}
        try:
            for offset in range(self.margin_lookback):
                day = (self.as_of.date() - timedelta(days=offset)).strftime("%Y%m%d")
                for exchange_fn in (ak.stock_margin_detail_sse,):
                    df = _quiet(exchange_fn, date=day)
                    if df is None or df.empty:
                        continue
                    for _, row in df.iterrows():
                        code = _norm(_cell(row, "标的证券代码"))
                        if code not in target:
                            continue
                        mb = _fnum(row, "融资余额")
                        buy = _fnum(row, "融资买入额")
                        repay = _fnum(row, "融资偿还额")
                        result.setdefault(code, []).append(MarginRecord(
                            code=code,
                            name=str(_cell(row, "标的证券简称", "")),
                            trade_date=self.as_of.date() - timedelta(days=offset),
                            margin_balance=mb,
                            margin_buy=buy,
                            margin_repay=repay,
                            short_balance=_fnum(row, "融券余量"),
                            short_sell=_fnum(row, "融券卖出量"),
                            net_margin_flow=buy - repay,
                        ))
            health.append(f"融资融券: {sum(len(v) for v in result.values())}条")
        except Exception as e:
            health.append(f"融资融券: FAIL ({e})")
        return result

    def _load_futures_basis(self, ak, health):
        """Load 股指期货升贴水 for IF/IC/IM/IH."""
        results: list[FuturesBasis] = []
        # Map futures to spot indices
        contracts = [
            ("IF", "000300", "沪深300"),
            ("IC", "000905", "中证500"),
            ("IM", "000852", "中证1000"),
            ("IH", "000016", "上证50"),
        ]
        try:
            # Determine current contract month (nearest quarter month)
            today = self.as_of.date()
            # Use current month's contract
            ym = today.strftime("%y%m")
            for fut_code, spot_code, spot_name in contracts:
                contract = f"{fut_code}{ym}"
                df = _quiet(ak.futures_zh_daily_sina, symbol=contract)
                if df is None or df.empty:
                    continue
                latest = df.iloc[-1]
                futures_close = _fnum(latest, "close", "收盘价")
                if futures_close <= 0:
                    continue
                # Get spot close from baostock or use approximate
                spot_close = self._get_spot_close(spot_code)
                if spot_close <= 0:
                    continue
                basis = (futures_close - spot_close) / spot_close
                results.append(FuturesBasis(
                    contract=fut_code,
                    trade_date=today,
                    futures_close=futures_close,
                    spot_close=spot_close,
                    basis_pct=basis,
                    volume=_fnum(latest, "volume"),
                    open_interest=_fnum(latest, "hold"),
                ))
            health.append(f"股指期货: {len(results)}个合约升贴水")
        except Exception as e:
            health.append(f"股指期货: FAIL ({e})")
        return results

    def _get_spot_close(self, code: str) -> float:
        """Get spot index close from baostock (quick fallback)."""
        try:
            from beichen_alpha.data_sources.baostock_source import BaostockPriceSource
            bars = BaostockPriceSource(
                symbols=[code], benchmark="000300",
                start_date=(self.as_of.date() - timedelta(days=5)).strftime("%Y%m%d"),
                end_date=self.as_of.date().strftime("%Y%m%d"),
            ).load()
            if code in bars and bars[code]:
                return float(bars[code][-1].close)
        except Exception:
            pass
        return 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(code: str) -> str:
    text = str(code).strip().replace(".", "").replace("'", "").replace('"', "")
    for p in ("sh", "sz", "SH", "SZ"):
        if text.lower().startswith(p.lower()):
            text = text[len(p):]
            break
    return text.zfill(6)

def _cell(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None and str(v) != "nan":
            return v
    rl = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        v = rl.get(k.lower())
        if v is not None and str(v) != "nan":
            return v
    return ""

def _fnum(row, *keys):
    v = _cell(row, *keys)
    if v == "" or v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0

def _quiet(func, *args, **kwargs):
    try:
        import pandas as pd
        r = func(*args, **kwargs)
        return r if isinstance(r, pd.DataFrame) else None
    except Exception:
        return None
