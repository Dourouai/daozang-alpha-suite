"""Heat data sources: ETF资金流、概念板块热度、大宗交易.

Uses non-eastmoney APIs: SSE (ETF scale), THS/同花顺 (concept boards),
and stock_dzjy_mrtj (block trades from Sina source).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from .akshare_source import import_akshare


@dataclass
class EtfScaleRecord:
    fund_code: str
    fund_name: str
    date: date
    shares: float         # 基金份额
    sector: str = ""       # mapped sector


@dataclass
class ConceptHeat:
    name: str              # 概念名称
    date: date
    close: float
    change_pct: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    leading_stock: str = ""  # 龙头股


@dataclass
class BlockTradeRecord:
    code: str
    name: str
    trade_date: date
    close: float           # 收盘价
    deal_price: float      # 成交价
    premium_pct: float     # 折溢率 (negative = discount)
    deal_amount: float     # 成交总量(万股)
    deal_value: float      # 成交总额(万元)


@dataclass
class HeatSnapshot:
    as_of: datetime
    etf_sector_flow: dict[str, float] = field(default_factory=dict)  # sector -> share change %
    concept_heat: dict[str, ConceptHeat] = field(default_factory=dict)
    block_trades: dict[str, list[BlockTradeRecord]] = field(default_factory=dict)
    source_health: list[str] = field(default_factory=list)


class AkshareHeatSource:
    """Fetch ETF scale + concept heat + block trades."""

    def __init__(self, symbols: list[str], as_of: datetime | None = None):
        self.symbols = symbols
        self.as_of = as_of or datetime.now()

    def load(self) -> HeatSnapshot:
        ak = import_akshare()
        health: list[str] = []
        etf = self._load_etf_flow(ak, health)
        concept = self._load_concept_heat(ak, health)
        block = self._load_block_trades(ak, health)
        return HeatSnapshot(
            as_of=self.as_of, etf_sector_flow=etf,
            concept_heat=concept, block_trades=block,
            source_health=health,
        )

    def _load_etf_flow(self, ak, health):
        """Compute sector ETF share changes between today and 5 days ago."""
        result: dict[str, float] = {}
        try:
            today = self.as_of.date().strftime("%Y%m%d")
            prev = (self.as_of.date() - timedelta(days=5)).strftime("%Y%m%d")
            curr = _quiet(ak.fund_etf_scale_sse, date=today)
            prev_df = _quiet(ak.fund_etf_scale_sse, date=prev)
            if curr is None or curr.empty:
                health.append("ETF规模: 0条"); return result

            # Aggregate shares by sector
            def agg_sector(df):
                sec: dict[str, float] = {}
                if df is None: return sec
                for _, row in df.iterrows():
                    name = str(_cell(row, "基金简称"))
                    shares = _fnum(row, "基金份额")
                    sector = _map_etf_sector(name)
                    if sector:
                        sec[sector] = sec.get(sector, 0) + shares
                return sec

            curr_sec = agg_sector(curr)
            prev_sec = agg_sector(prev_df)
            for sector, curr_shares in curr_sec.items():
                prev_shares = prev_sec.get(sector, curr_shares)
                if prev_shares > 0:
                    result[sector] = (curr_shares - prev_shares) / prev_shares
            health.append(f"ETF资金流: {len(result)}个行业")
        except Exception as e:
            health.append(f"ETF资金流: FAIL ({e})")
        return result

    def _load_concept_heat(self, ak, health):
        """Load concept board heat from THS."""
        result: dict[str, ConceptHeat] = {}
        try:
            # Get concept names
            names_df = _quiet(ak.stock_board_concept_name_ths)
            if names_df is None:
                health.append("概念板块: 0个"); return result

            target_concepts = [
                "创新药", "医药", "医疗器械", "生物疫苗", "AI", "人形机器人",
                "机器人概念", "低空经济", "半导体", "芯片", "新能源",
                "金融科技", "证券", "银行", "军工",
            ]
            today = self.as_of.date()
            start = (today - timedelta(days=3)).strftime("%Y%m%d")
            end = today.strftime("%Y%m%d")

            for _, row in names_df.iterrows():
                name = str(_cell(row, "name", "概念名称"))
                if not any(kw in name for kw in target_concepts):
                    continue
                try:
                    idx = _quiet(ak.stock_board_concept_index_ths, symbol=name, start_date=start, end_date=end)
                    if idx is not None and not idx.empty:
                        latest = idx.iloc[-1]
                        prev = idx.iloc[0] if len(idx) >= 2 else latest
                        close = _fnum(latest, "收盘价", "close")
                        prev_close = _fnum(prev, "收盘价", "close")
                        change = (close - prev_close) / prev_close if prev_close > 0 else 0
                        result[name] = ConceptHeat(
                            name=name, date=today,
                            close=close, change_pct=change,
                            volume=_fnum(latest, "成交量", "volume"),
                            amount=_fnum(latest, "成交额", "amount"),
                        )
                except Exception:
                    pass
            health.append(f"概念板块: {len(result)}个")
        except Exception as e:
            health.append(f"概念板块: FAIL ({e})")
        return result

    def _load_block_trades(self, ak, health):
        """Load block trades for target stocks."""
        result: dict[str, list[BlockTradeRecord]] = {}
        target = {_norm(c) for c in self.symbols}
        try:
            today = self.as_of.date().strftime("%Y%m%d")
            start = (self.as_of.date() - timedelta(days=5)).strftime("%Y%m%d")
            df = _quiet(ak.stock_dzjy_mrtj, start_date=start, end_date=today)
            if df is None or df.empty:
                health.append("大宗交易: 0条"); return result
            for _, row in df.iterrows():
                code = _norm(_cell(row, "证券代码"))
                if code not in target:
                    continue
                result.setdefault(code, []).append(BlockTradeRecord(
                    code=code,
                    name=str(_cell(row, "证券简称", "")),
                    trade_date=_pdate(_cell(row, "交易日期")),
                    close=_fnum(row, "收盘价"),
                    deal_price=_fnum(row, "成交价"),
                    premium_pct=_fnum(row, "折溢率"),
                    deal_amount=_fnum(row, "成交总量"),
                    deal_value=_fnum(row, "成交总额"),
                ))
            health.append(f"大宗交易: {sum(len(v) for v in result.values())}条")
        except Exception as e:
            health.append(f"大宗交易: FAIL ({e})")
        return result


# Helpers
def _norm(c):
    t = str(c).strip().replace(".","").replace("'","").replace('"',"")
    for p in ("sh","sz","SH","SZ"):
        if t.lower().startswith(p.lower()): t = t[len(p):]; break
    return t.zfill(6)

def _cell(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None and str(v) != "nan": return v
    rl = {str(k).lower(): v for k,v in row.items()}
    for k in keys:
        v = rl.get(k.lower())
        if v is not None and str(v) != "nan": return v
    return ""

def _fnum(row, *keys):
    v = _cell(row, *keys)
    if v == "" or v is None: return 0.0
    try: return float(v)
    except: return 0.0

def _pdate(val):
    if val is None: return None
    if isinstance(val, (date, datetime)): return val if isinstance(val, date) else val.date()
    try: return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except: return None

def _quiet(func, *args, **kwargs):
    try:
        import pandas as pd
        r = func(*args, **kwargs)
        return r if isinstance(r, pd.DataFrame) else None
    except: return None

def _map_etf_sector(name: str) -> str:
    """Map ETF name to sector."""
    n = str(name)
    if any(k in n for k in ("医药","医疗","生物","创新药","中药")): return "医药"
    if any(k in n for k in ("半导体","芯片")): return "半导体"
    if any(k in n for k in ("证券","券商","非银")): return "非银金融"
    if any(k in n for k in ("银行","金融")): return "银行"
    if any(k in n for k in ("军工","国防")): return "军工"
    if any(k in n for k in ("科技","科创")): return "科技"
    if any(k in n for k in ("新能源","光伏","储能","锂电")): return "新能源"
    if any(k in n for k in ("消费","白酒","食品")): return "消费"
    if any(k in n for k in ("红利","高股息")): return "红利"
    return ""
