"""Convertible bond (可转债) factor.

Scores stocks based on their associated convertible bonds.
Low/no premium → bullish signal (fairly valued, bond investors confident).
High premium → bearish signal (overvalued relative to bond conversion).
"""

from __future__ import annotations
from datetime import datetime, date

from beichen_alpha.models import FactorScore


def score_bond_signals(
    code: str,
    bond_map: dict[str, dict] | None,
    etf_scale_map: dict[str, float] | None = None,
) -> list[FactorScore]:
    """Score a stock based on convertible bond and ETF scale data.

    Args:
        code: 6-digit stock code.
        bond_map: {stock_code: {premium_pct, conv_price, bond_price, stock_price}}
        etf_scale_map: {sector: scale_change_pct} for ETF scale changes.
    """
    scores = []
    scores.extend(_score_cb(code, bond_map))
    scores.extend(_score_etf_scale(code, etf_scale_map))
    if not scores:
        scores.append(FactorScore("可转债/ETF", 0, True, "无可转债或ETF数据"))
    return scores


# -------------------------------------------------------
# 可转债因子
# -------------------------------------------------------

def _score_cb(code, bond_map):
    if not bond_map:
        return [FactorScore("可转债", 0, True, "无可转债数据")]
    info = bond_map.get(code)
    if not info:
        return [FactorScore("可转债", 0, True, "该股无关联可转债")]

    premium = info.get("premium_pct", 999)
    conv_value = info.get("conv_value", 0)
    stock_price = info.get("stock_price", 0)

    score = 0; passed = True; reasons = []

    # Premium analysis
    if premium < 0:  # Negative premium = discount (bullish!)
        score += 12; reasons.append(f"折价{abs(premium):.1f}%")
    elif premium < 5:
        score += 6; reasons.append(f"低溢价{premium:.1f}%")
    elif premium < 15:
        score += 2; reasons.append(f"溢价{premium:.1f}%")
    elif premium < 30:
        score -= 2; reasons.append(f"溢价偏高{premium:.1f}%")
    else:
        score -= 6; reasons.append(f"高溢价{premium:.1f}%"); passed = False

    # Conversion value vs stock price
    if stock_price > 0 and conv_value > 0:
        ratio = conv_value / stock_price
        if ratio > 0.95:
            score += 4; reasons.append("转股价值接近正股")

    score = max(min(score, 20), -10)
    return [FactorScore("可转债", score, passed, "；".join(reasons))]


# -------------------------------------------------------
# ETF规模因子
# -------------------------------------------------------

# Map ETF sectors to stock themes
ETF_SECTOR_MAP = {
    "医药": ("医药", "创新药", "医疗", "生物", "CRO"),
    "半导体": ("半导体", "AI硬件", "芯片", "电子"),
    "证券": ("非银金融", "券商", "保险"),
    "银行": ("银行", "金融"),
    "军工": ("军工", "国防", "航天"),
    "新能源": ("新能源", "光伏", "储能", "风电"),
    "消费": ("消费", "品牌消费", "白酒", "家电"),
    "科技": ("AI", "科技", "数字经济", "计算机"),
    "红利": ("高股息", "红利", "公用事业"),
}


def _score_etf_scale(code, etf_scale_map):
    """Score based on sector ETF share changes (proxy for capital flows)."""
    if not etf_scale_map:
        return [FactorScore("ETF资金", 0, True, "无ETF规模数据")]

    # ETF scale is per-fund, not per-sector. We'd need a sector mapping.
    # For now, this is a placeholder that returns neutral.
    # Full implementation requires mapping ETF fund codes to sectors.
    return [FactorScore("ETF资金", 0, True, "ETF规模数据已接入，行业映射待完善")]


# ---------------------------------------------------------------------------
# Data loading helpers (used by CLI to populate bond_map)
# ---------------------------------------------------------------------------

def load_bond_map(as_of: date | None = None) -> dict[str, dict]:
    """Load convertible bond data from Jisilu via AKShare.

    Returns: {stock_code: {premium_pct, conv_price, bond_price, stock_price, conv_value}}
    """
    try:
        import akshare as ak
        df = ak.bond_cb_jsl()
        result = {}
        for _, row in df.iterrows():
            code = str(row.get("正股代码", "")).strip()
            if not code or len(code) < 6:
                continue
            # Normalize to 6-digit
            code = code.zfill(6)
            result[code] = {
                "premium_pct": _fnum(row, "转股溢价率"),
                "conv_price": _fnum(row, "转股价"),
                "conv_value": _fnum(row, "转股价值"),
                "bond_price": _fnum(row, "现价"),
                "stock_price": _fnum(row, "正股价"),
            }
        return result
    except Exception:
        return {}


def load_etf_scale_map(as_of: date | None = None) -> dict[str, float]:
    """Load ETF share change data from SSE/SZSE.

    Returns: {sector_name: scale_change_pct}
    """
    try:
        import akshare as ak
        from datetime import timedelta
        today = as_of or date.today()
        prev = today - timedelta(days=5)
        curr_df = ak.fund_etf_scale_sse(date=today.strftime("%Y%m%d"))
        prev_df = ak.fund_etf_scale_sse(date=prev.strftime("%Y%m%d"))
        # Simplified: aggregate by ETF type
        result = {}
        # Map common ETF names to sectors
        for _, row in curr_df.iterrows():
            name = str(row.get("基金简称", ""))
            shares = _fnum(row, "基金份额")
            for sector, keywords in ETF_SECTOR_MAP.items():
                if any(kw in name for kw in keywords):
                    result[sector] = result.get(sector, 0) + shares
        return result
    except Exception:
        return {}


def _fnum(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None and str(v) != "nan":
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return 0.0
