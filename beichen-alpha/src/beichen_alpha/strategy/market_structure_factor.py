from __future__ import annotations

from beichen_alpha.models import FactorScore, MarketStructureSnapshot


def score_market_structure(snapshot: MarketStructureSnapshot | None) -> list[FactorScore]:
    if snapshot is None:
        return [FactorScore("交易结构", 0, True, "交易结构源不可用，按中性处理")]

    score = 0
    reasons: list[str] = []

    if snapshot.breadth is not None:
        if snapshot.breadth >= 0.68:
            score += 7
            reasons.append("市场宽度偏强")
        elif snapshot.breadth >= 0.56:
            score += 3
            reasons.append("市场宽度略暖")
        elif snapshot.breadth <= 0.32:
            score -= 10
            reasons.append("市场宽度偏弱")
        elif snapshot.breadth <= 0.42:
            score -= 5
            reasons.append("上涨家数不足")

    limit_gap = (snapshot.limit_up_count or 0) - (snapshot.limit_down_count or 0)
    if limit_gap >= 60:
        score += 5
        reasons.append("涨停扩散")
    elif limit_gap <= -25:
        score -= 8
        reasons.append("跌停压力")

    if snapshot.margin_balance_change_pct is not None:
        if snapshot.margin_balance_change_pct >= 0.015:
            score += 4
            reasons.append("两融余额扩张")
        elif snapshot.margin_balance_change_pct <= -0.015:
            score -= 5
            reasons.append("两融余额收缩")

    if snapshot.margin_buy_turnover_ratio is not None:
        if snapshot.margin_buy_turnover_ratio >= 0.18 and (snapshot.breadth or 0.5) < 0.45:
            score -= 5
            reasons.append("融资买入拥挤但宽度不足")
        elif snapshot.margin_buy_turnover_ratio >= 0.10:
            score += 3
            reasons.append("融资买入活跃")

    if snapshot.northbound_net_buy_100m is not None:
        if snapshot.northbound_net_buy_100m >= 30:
            score += 6
            reasons.append("北向明显净买")
        elif snapshot.northbound_net_buy_100m <= -30:
            score -= 6
            reasons.append("北向明显净卖")

    if snapshot.northbound_5d_net_buy_100m is not None:
        if snapshot.northbound_5d_net_buy_100m >= 100:
            score += 4
            reasons.append("北向5日流入")
        elif snapshot.northbound_5d_net_buy_100m <= -100:
            score -= 4
            reasons.append("北向5日流出")

    score = max(min(score, 24), -24)
    passed = score >= -8
    detail = snapshot.detail
    if reasons:
        detail = f"{detail}；{'、'.join(reasons)}"
    return [FactorScore("交易结构", score, passed, detail)]
