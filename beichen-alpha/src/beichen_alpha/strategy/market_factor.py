from __future__ import annotations

from dataclasses import dataclass

from beichen_alpha.models import FactorScore, MarketRegime, SectorSignal, StockProfile
from beichen_alpha.data_sources.sector_rotation_source import normalize_sector_name
from beichen_alpha.profile_tags import profile_industry_candidates, profile_primary_industry


@dataclass(frozen=True)
class IndustryChain:
    name: str
    stages: tuple[str, ...]


INDUSTRY_CHAINS = (
    IndustryChain("AI算力链", ("AI硬件", "半导体", "电子", "材料", "工业金属", "化工", "资源")),
    IndustryChain("新能源链", ("新能源", "材料", "工业金属", "化工", "资源")),
    IndustryChain("金属周期链", ("材料", "工业金属", "资源")),
    IndustryChain("金融链", ("非银金融", "保险", "银行")),
    IndustryChain("医药链", ("医药",)),
)


def score_market_regime(regime: MarketRegime | None) -> list[FactorScore]:
    if regime is None:
        return [FactorScore("市场温度", 0, True, "市场温度源不可用，按中性处理")]

    if regime.temperature == "过热":
        return [FactorScore("市场温度", -8, False, regime.detail or "市场过热，短线不追高")]
    passed = regime.score >= -8
    return [FactorScore("市场温度", regime.score, passed, regime.detail)]


def score_sector_rotation(
    profile: StockProfile | None,
    sector_signals: dict[str, SectorSignal] | None,
) -> list[FactorScore]:
    signal = match_sector_signal(profile, sector_signals or {})
    if profile is None:
        return [FactorScore("行业轮动", 0, False, "缺少股票画像，无法匹配行业")]
    if signal is None:
        return [FactorScore("行业轮动", 0, False, f"{profile.industry or '未分类'} 暂无行业轮动数据")]
    return [
        FactorScore(
            "行业轮动",
            signal.score,
            signal.score > 0,
            signal.detail or f"{signal.name} 轮动评分 {signal.score}",
        )
    ]


def score_chain_rotation(
    profile: StockProfile | None,
    sector_signals: dict[str, SectorSignal] | None,
) -> list[FactorScore]:
    if profile is None:
        return [FactorScore("产业链传导", 0, True, "缺少股票画像，产业链传导按中性处理")]

    signals = sector_signals or {}
    sector = normalize_profile_target(profile)
    if not sector:
        return [FactorScore("产业链传导", 0, True, "未匹配到产业链环节，按中性处理")]

    candidates = [score_single_chain(chain, sector, signals) for chain in INDUSTRY_CHAINS if sector in chain.stages]
    candidates = [item for item in candidates if item is not None]
    if not candidates:
        return [FactorScore("产业链传导", 0, True, f"{sector} 暂无内置产业链规则，按中性处理")]

    best = max(candidates, key=lambda item: abs(item.score))
    return [best]


def score_single_chain(
    chain: IndustryChain,
    target_sector: str,
    sector_signals: dict[str, SectorSignal],
) -> FactorScore | None:
    target_index = chain.stages.index(target_sector)
    target_signal = sector_signals.get(target_sector)
    target_score = target_signal.score if target_signal else 0
    target_return_5d = target_signal.return_5d if target_signal else None
    target_amount = target_signal.amount_ratio if target_signal else None

    upstream = [sector_signals[sector] for sector in chain.stages[:target_index] if sector in sector_signals]
    prev_signal = sector_signals.get(chain.stages[target_index - 1]) if target_index > 0 else None
    upstream_best = max((signal.score for signal in upstream), default=0)
    upstream_worst = min((signal.score for signal in upstream), default=0)
    prev_score = prev_signal.score if prev_signal else 0

    score = 0
    reason = []
    if target_index == 0:
        if target_score >= 18:
            score += 6
            reason.append("前排主线仍强")
        elif target_score <= -8:
            score -= 8
            reason.append("前排主线转弱")
    else:
        if upstream_best >= 18 and 0 < target_score <= 22:
            score += 20
            reason.append("上游强势后目标环节接力启动")
        elif upstream_best >= 12 and 0 < target_score <= 26:
            score += 14
            reason.append("产业链上游偏强，目标环节跟随")
        elif upstream_best >= 18 and target_score <= 0:
            score += 4
            reason.append("上游强势但目标环节尚未启动")

        if prev_score >= 12 and target_score > 0:
            score += 6
            reason.append("上一环节确认后传导")
        if upstream_worst <= -12 and target_score > 0:
            score -= 12
            reason.append("上游退潮中的补涨风险")

    if target_score >= 28 or (target_return_5d is not None and target_return_5d >= 0.10):
        score -= 10
        reason.append("目标环节短线过热")
    elif target_amount is not None and target_amount >= 1.20 and target_score > 0:
        score += 4
        reason.append("目标环节放量确认")

    score = max(min(score, 26), -20)
    detail = (
        f"{chain.name}: {target_sector}; "
        f"上游最佳 {upstream_best:+d}, 上一环节 {prev_score:+d}, 目标 {target_score:+d}; "
        + ("、".join(reason) if reason else "暂无明确传导信号")
    )
    return FactorScore("产业链传导", score, score >= 0, detail)


def match_sector_signal(
    profile: StockProfile | None,
    sector_signals: dict[str, SectorSignal],
) -> SectorSignal | None:
    if profile is None:
        return None
    candidates = [normalize_sector_name(name) or name for name in profile_industry_candidates(profile)]
    matches = [sector_signals[name] for name in candidates if name in sector_signals]
    if not matches:
        return None
    return max(matches, key=lambda signal: signal.score)


def normalize_profile_target(profile: StockProfile) -> str:
    for item in profile_industry_candidates(profile):
        normalized = normalize_sector_name(item)
        if normalized:
            return normalized
    return normalize_sector_name(profile_primary_industry(profile))
