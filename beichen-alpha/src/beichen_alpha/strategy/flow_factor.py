"""Flow-based scoring factors: 龙虎榜、北向资金、主力资金流.

Converts raw flow data into deterministic factor scores.
All scores designed for 2-3 day short-term horizon.
Missing data = neutral (not a penalty).
"""

from __future__ import annotations

from datetime import datetime

from beichen_alpha.data_sources.flow_source import (
    FlowSnapshot,
    FundFlowRecord,
    LhbRecord,
    NorthboundRecord,
)
from beichen_alpha.models import FactorScore


def score_flow_factors(
    code: str,
    snapshot: FlowSnapshot | None,
    as_of: datetime | None = None,
) -> list[FactorScore]:
    """Score all flow-based factors for a single stock."""
    if snapshot is None:
        return [FactorScore("资金面", 0, True, "资金面数据源不可用，按中性处理")]
    scores: list[FactorScore] = []
    scores.extend(_score_lhb(code, snapshot, as_of))
    scores.extend(_score_northbound(code, snapshot, as_of))
    scores.extend(_score_fund_flow(code, snapshot, as_of))
    if not scores:
        scores.append(FactorScore("资金面", 0, True, "无资金面数据覆盖"))
    return scores


# ---------------------------------------------------------------------------
# LHB scoring
# ---------------------------------------------------------------------------

_INST_KW = ("机构", "专用", "沪股通", "深股通", "外资", "QFII")
_RETAIL_KW = ("散户", "拉萨", "营业部", "普通席位")


def _score_lhb(code, snapshot, as_of=None):
    records = snapshot.lhb_records.get(code, [])
    if not records:
        return [FactorScore("龙虎榜", 0, True, "近5日未上榜")]
    cutoff = (as_of or datetime.now()).date()
    recent = [r for r in records if r.trade_date and (cutoff - r.trade_date).days <= 3]
    if not recent:
        return [FactorScore("龙虎榜", 0, True, "上榜日期已超3日")]

    total_net = sum(r.net_amount for r in recent)
    interps = [r.interpretation for r in recent if r.interpretation]
    has_inst = any(any(kw in t for kw in _INST_KW) for t in interps)
    has_retail = any(any(kw in t for kw in _RETAIL_KW) for t in interps)

    score = 0
    passed = True
    reasons = []

    net_wan = total_net / 10000
    if net_wan >= 5000:
        score += 20; reasons.append(f"净买{net_wan/10000:.1f}亿")
    elif net_wan >= 1000:
        score += 12; reasons.append(f"净买{net_wan/10000:.2f}亿")
    elif net_wan >= 0:
        score += 4; reasons.append(f"净买{net_wan:.0f}万")
    elif net_wan >= -1000:
        score -= 4; reasons.append(f"净卖{abs(net_wan):.0f}万")
    else:
        score -= 14; reasons.append(f"净卖{abs(net_wan)/10000:.2f}亿"); passed = False

    if has_inst and total_net > 0:
        score += 10; reasons.append("机构特征")
    if has_retail and total_net > 5000 * 10000:
        score -= 6; reasons.append("散户席位多")
    if len(recent) >= 2 and total_net > 0:
        score += 6; reasons.append(f"连续{len(recent)}日上榜")

    score = max(min(score, 40), -24)
    detail = "；".join(reasons) if reasons else "中性"
    return [FactorScore("龙虎榜", score, passed, detail)]


# ---------------------------------------------------------------------------
# Northbound scoring
# ---------------------------------------------------------------------------

def _score_northbound(code, snapshot, as_of=None):
    records = snapshot.northbound_records.get(code, [])
    if not records:
        return [FactorScore("北向资金", 0, True, "无北向个股数据")]
    sorted_recs = sorted(
        [r for r in records if r.trade_date is not None],
        key=lambda r: r.trade_date, reverse=True,
    )
    if not sorted_recs:
        return [FactorScore("北向资金", 0, True, "北向数据无有效日期")]

    latest = sorted_recs[0]
    score = 0; passed = True; reasons = []
    net_buy = latest.net_buy_10k

    if net_buy >= 10000:
        score += 18; reasons.append(f"增持{net_buy/10000:.1f}亿")
    elif net_buy >= 3000:
        score += 12; reasons.append(f"增持{net_buy/10000:.2f}亿")
    elif net_buy >= 500:
        score += 6; reasons.append(f"增持{net_buy:.0f}万")
    elif net_buy >= -500:
        pass
    elif net_buy >= -3000:
        score -= 6; reasons.append(f"减持{abs(net_buy):.0f}万")
    else:
        score -= 14; reasons.append(f"减持{abs(net_buy)/10000:.2f}亿"); passed = False

    if latest.consecutive_days >= 3:
        score += 12; reasons.append(f"连买{latest.consecutive_days}日")
    elif latest.consecutive_days >= 2:
        score += 6; reasons.append(f"连买{latest.consecutive_days}日")

    if len(sorted_recs) >= 2:
        prev = sorted_recs[1]
        hold_change = latest.hold_pct - prev.hold_pct
        if hold_change >= 0.005:
            score += 6; reasons.append(f"持股↑{hold_change:.1%}")
        elif hold_change <= -0.005:
            score -= 4; reasons.append(f"持股↓{abs(hold_change):.1%}")

    score = max(min(score, 36), -20)
    detail = "；".join(reasons) if reasons else "中性"
    return [FactorScore("北向资金", score, passed, detail)]


# ---------------------------------------------------------------------------
# Fund flow scoring
# ---------------------------------------------------------------------------

def _score_fund_flow(code, snapshot, as_of=None):
    records = snapshot.fund_flow_records.get(code, [])
    if not records:
        return [FactorScore("主力资金", 0, True, "无主力资金数据")]
    latest = records[0]
    main_net = latest.main_net_inflow
    score = 0; passed = True; reasons = []

    if main_net >= 5000:
        score += 16; reasons.append(f"净流入{main_net/10000:.1f}亿")
    elif main_net >= 1000:
        score += 10; reasons.append(f"净流入{main_net/10000:.2f}亿")
    elif main_net >= 200:
        score += 4; reasons.append(f"净流入{main_net:.0f}万")
    elif main_net >= -200:
        pass
    elif main_net >= -1000:
        score -= 6; reasons.append(f"净流出{abs(main_net):.0f}万")
    else:
        score -= 14; reasons.append(f"净流出{abs(main_net)/10000:.2f}亿"); passed = False

    score = max(min(score, 30), -24)
    detail = "；".join(reasons) if reasons else "中性"
    return [FactorScore("主力资金", score, passed, detail)]


def summarize_flow(code: str, snapshot: FlowSnapshot | None) -> str:
    """One-line flow summary for reports."""
    if snapshot is None:
        return "资金面无数据"
    parts = []
    lhb = snapshot.lhb_records.get(code, [])
    if lhb:
        net = sum(r.net_amount for r in lhb[:3]) / 10000
        if net > 1000: parts.append(f"龙虎榜净买{net/10000:.1f}亿")
        elif net > 0: parts.append(f"龙虎榜净买{net:.0f}万")
    nb = snapshot.northbound_records.get(code, [])
    if nb and nb[-1].consecutive_days >= 2:
        parts.append(f"北向连买{nb[-1].consecutive_days}日")
    ff = snapshot.fund_flow_records.get(code, [])
    if ff:
        m = ff[-1].main_net_inflow
        if m > 1000: parts.append(f"主力流入{m/10000:.1f}亿")
        elif m < -1000: parts.append(f"主力流出{abs(m)/10000:.1f}亿")
    return "；".join(parts) if parts else "资金面中性"
