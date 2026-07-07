"""Heat factors: ETF资金流、概念板块热度、大宗交易."""

from __future__ import annotations
from datetime import datetime
from beichen_alpha.models import FactorScore, StockProfile
from beichen_alpha.data_sources.heat_source import HeatSnapshot
from beichen_alpha.profile_tags import profile_all_tags, profile_primary_industry


def score_heat_factors(
    code: str, profile: StockProfile | None,
    snapshot: HeatSnapshot | None,
    as_of: datetime | None = None,
) -> list[FactorScore]:
    if snapshot is None:
        return [FactorScore("板块热度", 0, True, "热度数据不可用")]
    scores = []
    scores.extend(_score_etf_flow(code, profile, snapshot))
    scores.extend(_score_concept_heat(code, profile, snapshot))
    scores.extend(_score_block_trade(code, snapshot))
    if not scores:
        scores.append(FactorScore("板块热度", 0, True, "无热度数据覆盖"))
    return scores


# -------------------------------------------------------
# ETF资金流
# -------------------------------------------------------

def _score_etf_flow(code, profile, snapshot):
    if not snapshot.etf_sector_flow:
        return [FactorScore("ETF资金", 0, True, "无ETF行业数据")]
    sectors = set()
    if profile:
        if profile.industry: sectors.add(profile.industry)
        if profile.primary_industry: sectors.add(profile.primary_industry)
        for t in profile_all_tags(profile): sectors.add(t)

    score = 0; matches = []
    for sector, change in snapshot.etf_sector_flow.items():
        if not any(s.lower() in t.lower() or sector.lower() in t.lower() for s in sectors for t in sectors):
            continue
        if change > 0.05:
            score += 8; matches.append(f"{sector}ETF份额↑{change:.0%}")
        elif change > 0.02:
            score += 4; matches.append(f"{sector}ETF份额↑{change:.0%}")
        elif change < -0.05:
            score -= 6; matches.append(f"{sector}ETF份额↓{abs(change):.0%}")

    if not matches:
        return [FactorScore("ETF资金", 0, True, "ETF资金未影射到该股行业")]
    score = max(min(score, 14), -10)
    return [FactorScore("ETF资金", score, score >= 0, "；".join(matches[:3]))]


# -------------------------------------------------------
# 概念板块热度
# -------------------------------------------------------

CONCEPT_TAG_MAP = {
    "创新药": ("医药", "创新药", "生物", "CRO", "CXO"),
    "医药": ("医药", "医疗", "生物"),
    "医疗器械": ("医药", "医疗", "器械"),
    "生物疫苗": ("医药", "生物", "疫苗"),
    "AI": ("AI硬件", "AI", "算力", "CPO", "光模块"),
    "人形机器人": ("机器人", "人形机器人", "工业机器人"),
    "机器人概念": ("机器人", "工业机器人", "先进制造"),
    "低空经济": ("低空经济", "无人机"),
    "半导体": ("半导体", "芯片", "电子"),
    "芯片": ("半导体", "芯片"),
    "新能源": ("新能源", "光伏", "储能", "风电"),
    "金融科技": ("非银金融", "金融科技"),
    "军工": ("军工", "国防", "航天"),
}


def _score_concept_heat(code, profile, snapshot):
    if not snapshot.concept_heat:
        return [FactorScore("概念热度", 0, True, "无概念板块数据")]
    stock_tags = set()
    if profile:
        if profile.industry: stock_tags.add(profile.industry)
        if profile.primary_industry: stock_tags.add(profile.primary_industry)
        for t in profile_all_tags(profile): stock_tags.add(t)

    score = 0; matches = []
    for concept_name, tags in CONCEPT_TAG_MAP.items():
        heat = snapshot.concept_heat.get(concept_name)
        if not heat:
            continue
        if not any(t.lower() in s.lower() for s in stock_tags for t in tags):
            continue
        if heat.change_pct > 0.03:
            score += 8; matches.append(f"{concept_name}↑{heat.change_pct:.1%}")
        elif heat.change_pct > 0.01:
            score += 4; matches.append(f"{concept_name}↑{heat.change_pct:.1%}")
        elif heat.change_pct < -0.03:
            score -= 4; matches.append(f"{concept_name}↓{abs(heat.change_pct):.1%}")

    if not matches:
        return [FactorScore("概念热度", 0, True, "概念热度未映射")]
    score = max(min(score, 16), -8)
    return [FactorScore("概念热度", score, score >= 0, "；".join(matches[:3]))]


# -------------------------------------------------------
# 大宗交易
# -------------------------------------------------------

def _score_block_trade(code, snapshot):
    records = snapshot.block_trades.get(code, [])
    if not records:
        return [FactorScore("大宗交易", 0, True, "近5日无大宗交易")]
    score = 0; passed = True; reasons = []
    for r in records[:3]:
        if r.premium_pct > 0.03:
            score += 10; reasons.append(f"溢价{r.premium_pct:.1%}")
        elif r.premium_pct > 0:
            score += 4; reasons.append(f"微溢价{r.premium_pct:.1%}")
        elif r.premium_pct > -0.05:
            score -= 2; reasons.append(f"折价{abs(r.premium_pct):.1%}")
        else:
            score -= 8; reasons.append(f"深度折价{abs(r.premium_pct):.1%}")
            passed = False
    score = max(min(score, 18), -14)
    return [FactorScore("大宗交易", score, passed, "；".join(reasons))]
