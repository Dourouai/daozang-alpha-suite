"""Sentiment & leverage scoring: 涨停板 + 融资融券 + 股指期货升贴水."""

from __future__ import annotations
from datetime import datetime
from beichen_alpha.models import FactorScore, StockProfile
from beichen_alpha.data_sources.sentiment_source import SentimentSnapshot


def score_sentiment_factors(
    code: str, profile: StockProfile | None,
    snapshot: SentimentSnapshot | None,
    as_of: datetime | None = None,
) -> list[FactorScore]:
    if snapshot is None:
        return [FactorScore("情绪杠杆", 0, True, "情绪/杠杆数据不可用，按中性处理")]
    scores = []
    scores.extend(_score_zt(code, snapshot))
    scores.extend(_score_margin(code, snapshot))
    scores.extend(_score_futures_bias(profile, snapshot))
    if not scores:
        scores.append(FactorScore("情绪杠杆", 0, True, "无情绪/杠杆数据覆盖"))
    return scores


# -------------------------------------------------------
# 涨停板因子
# -------------------------------------------------------

def _score_zt(code, snapshot):
    records = snapshot.zt_records.get(code, [])
    if not records:
        return [FactorScore("涨停板", 0, True, "未涨停")]
    latest = records[0]  # most recent
    score = 0; passed = True; reasons = []

    # Fresh limit-up
    score += 16; reasons.append("涨停")

    # Seal strength (封板资金/成交额)
    if latest.turnover_wan > 0:
        seal_ratio = latest.seal_amount_wan / latest.turnover_wan
        if seal_ratio > 1.0:
            score += 8; reasons.append(f"封板强({seal_ratio:.1f}x)")
        elif seal_ratio > 0.5:
            score += 4; reasons.append("封板中等")
        else:
            score -= 4; reasons.append("封板偏弱")

    # Turnover rate: low turnover = tight supply
    if latest.turnover_rate < 3:
        score += 6; reasons.append("缩量涨停")
    elif latest.turnover_rate > 15:
        score -= 4; reasons.append("高换手涨停")

    # Multi-day limit-up streak
    if len(records) >= 2:
        score += 8; reasons.append(f"连板{len(records)}日")

    score = max(min(score, 36), -8)
    return [FactorScore("涨停板", score, passed, "；".join(reasons))]


# -------------------------------------------------------
# 融资融券因子
# -------------------------------------------------------

def _score_margin(code, snapshot):
    records = snapshot.margin_records.get(code, [])
    if not records:
        return [FactorScore("融资融券", 0, True, "无融资融券数据")]
    latest = records[0]
    net = latest.net_margin_flow  # 万元
    score = 0; passed = True; reasons = []

    if net >= 5000:
        score += 14; reasons.append(f"融资净买{net/10000:.1f}亿")
    elif net >= 1000:
        score += 8; reasons.append(f"融资净买{net/10000:.2f}亿")
    elif net >= 200:
        score += 4; reasons.append(f"融资净买{net:.0f}万")
    elif net >= -200:
        pass
    elif net >= -1000:
        score -= 4; reasons.append(f"融资净卖{abs(net):.0f}万")
    else:
        score -= 10; reasons.append(f"融资净卖{abs(net)/10000:.2f}亿"); passed = False

    # Margin buy ratio (融资买入/成交额)
    if latest.margin_balance > 0 and latest.margin_buy > 0:
        ratio = latest.margin_buy / latest.margin_balance
        if ratio > 0.05:
            score += 4; reasons.append(f"融资活跃({ratio:.1%})")

    # Consecutive net buying
    if len(records) >= 2:
        prev = records[1]
        if net > 0 and prev.net_margin_flow > 0:
            score += 4; reasons.append("连续融资净买")

    score = max(min(score, 26), -16)
    return [FactorScore("融资融券", score, passed, "；".join(reasons))]


# -------------------------------------------------------
# 股指期货升贴水因子
# -------------------------------------------------------

# Map futures contracts to sector preferences
FUTURES_SECTOR_MAP = {
    "IF": (("沪深300", "银行", "非银金融", "消费"), 1),     # large-cap
    "IC": (("中证500", "先进制造", "医药", "科技", "军工"), 1),  # mid-cap
    "IM": (("中证1000", "AI", "机器人", "低空经济"), 1),         # small-cap growth
    "IH": (("上证50", "银行", "保险", "高股息"), 1),              # super large-cap
}

def _score_futures_bias(profile, snapshot):
    """Score based on 股指期货升贴水: contango (升水) = bullish, backwardation (贴水) = bearish."""
    if not profile or not snapshot.futures_basis:
        return [FactorScore("期货升贴水", 0, True, "无期货升贴水数据")]

    # Overall market bias: average basis across all contracts
    bases = [f.basis_pct for f in snapshot.futures_basis]
    if not bases:
        return [FactorScore("期货升贴水", 0, True, "无有效升贴水")]

    avg_basis = sum(bases) / len(bases)
    score = 0; reasons = []

    # Overall bias affects all stocks
    if avg_basis > 0.005:  # >0.5% premium
        overall = 6; reasons.append(f"期货升水{avg_basis:.2%}")
    elif avg_basis > 0:
        overall = 2; reasons.append("期货微升水")
    elif avg_basis > -0.005:
        overall = -2; reasons.append(f"期货贴水{abs(avg_basis):.2%}")
    else:
        overall = -8; reasons.append(f"期货深度贴水{abs(avg_basis):.2%}")

    # Sector-specific: which contract's bias is most relevant?
    stock_tags = set()
    if profile.industry:
        stock_tags.add(profile.industry)
    if profile.primary_industry:
        stock_tags.add(profile.primary_industry)

    sector_bonus = 0
    for contract, (sectors, direction) in FUTURES_SECTOR_MAP.items():
        for fb in snapshot.futures_basis:
            if fb.contract == contract:
                match = any(s.lower() in t.lower() for s in sectors for t in stock_tags)
                if match and fb.basis_pct > 0.005:
                    sector_bonus = max(sector_bonus, 4)
                    reasons.append(f"{contract}升水利好")
                elif match and fb.basis_pct < -0.005:
                    sector_bonus = min(sector_bonus, -3)
                    reasons.append(f"{contract}贴水利空")
                break

    score = max(min(overall + sector_bonus, 10), -10)
    passed = score >= -3
    return [FactorScore("期货升贴水", score, passed, "；".join(reasons) if reasons else "期货中性")]
