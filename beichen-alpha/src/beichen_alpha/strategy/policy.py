from __future__ import annotations

from beichen_alpha.models import FactorScore, StockProfile, StrategyPolicy
from beichen_alpha.profile_tags import profile_all_tags


CYCLE_THEME_WEIGHTS: dict[str, dict[str, int]] = {
    "balanced": {
        "高股息": 3,
        "防御": 2,
        "能源安全": 2,
        "先进制造": 6,
        "高端制造": 6,
        "全球竞争": 5,
        "金融稳定": 3,
        "资源": 2,
        "工业金属": 2,
    },
    "defensive": {
        "高股息": 15,
        "防御": 12,
        "能源安全": 10,
        "金融稳定": 8,
        "黄金": 8,
        "避险": 8,
        "品牌消费": 5,
        "现金流": 5,
        "先进制造": 2,
    },
    "recovery": {
        "品牌消费": 12,
        "金融稳定": 8,
        "先进制造": 8,
        "高端制造": 8,
        "能源安全": 5,
        "高股息": 4,
    },
    "growth": {
        "先进制造": 14,
        "高端制造": 14,
        "新能源": 10,
        "半导体": 10,
        "数字经济": 8,
        "全球竞争": 8,
        "高股息": 2,
    },
    "inflation": {
        "资源": 14,
        "工业金属": 12,
        "黄金": 12,
        "避险": 10,
        "能源安全": 10,
        "农业": 8,
        "高股息": 6,
    },
}


def score_policy(profile: StockProfile | None, policy: StrategyPolicy) -> list[FactorScore]:
    if profile is None:
        return [
            FactorScore("股票画像", 0, False, "缺少行业/市值画像"),
        ]

    scores: list[FactorScore] = []
    tags = profile_all_tags(profile)
    excluded = sorted(set(tags).intersection(policy.excluded_themes))
    if excluded:
        scores.append(
            FactorScore(
                "主题排除",
                -100,
                False,
                "排除主题: " + "、".join(excluded),
            )
        )

    market_cap = profile.market_cap_billion
    is_large_cap = market_cap is not None and market_cap >= policy.min_market_cap_billion
    if policy.large_cap_only:
        scores.append(
            FactorScore(
                "大盘过滤",
                15 if is_large_cap else -100,
                is_large_cap,
                (
                    f"总市值 {market_cap:.0f} 亿"
                    if market_cap is not None
                    else "缺少总市值，按不通过处理"
                ),
            )
        )

    weights = CYCLE_THEME_WEIGHTS.get(policy.cycle, CYCLE_THEME_WEIGHTS["balanced"])
    theme_score = sum(weights.get(theme, 0) for theme in tags)
    scores.append(
        FactorScore(
            "周期产业",
            theme_score,
            theme_score > 0,
            f"{policy.cycle}: {profile.industry or '未分类'} / {'、'.join(tags) or '无标签'}",
        )
    )
    return scores


def score_basic_quality(profile: StockProfile | None) -> list[FactorScore]:
    if profile is None:
        return [FactorScore("基本质量", 0, True, "缺少画像，基本质量暂按中性处理")]

    score = 0
    details = []
    if profile.market_cap_billion is not None:
        if profile.market_cap_billion >= 1000:
            score += 5
            details.append("千亿以上市值")
        elif profile.market_cap_billion >= 300:
            score += 2
            details.append("300亿以上市值")

    theme_weights = {
        "现金流": 5,
        "高股息": 4,
        "防御": 3,
        "金融稳定": 3,
        "全球竞争": 3,
        "能源安全": 2,
        "先进制造": 2,
        "高端制造": 2,
    }
    for theme in profile_all_tags(profile):
        weight = theme_weights.get(theme, 0)
        if weight:
            score += weight
            details.append(theme)

    score = min(score, 14)
    detail = "画像代理: " + "、".join(details[:5]) if details else "画像暂无质量标签，按中性处理"
    return [FactorScore("基本质量", score, True, detail)]
