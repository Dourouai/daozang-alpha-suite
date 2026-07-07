from __future__ import annotations

from beichen_alpha.models import FactorScore


def score_model_alpha(pct_rank: float | None) -> list[FactorScore]:
    if pct_rank is None:
        return []
    pct = max(0.0, min(1.0, float(pct_rank)))
    detail = f"道藏模型分位 {pct:.1%}"
    if pct >= 0.9:
        return [FactorScore("道藏模型", 22, True, f"{detail}，统计优势强")]
    if pct >= 0.75:
        return [FactorScore("道藏模型", 14, True, f"{detail}，统计优势较强")]
    if pct >= 0.55:
        return [FactorScore("道藏模型", 6, True, f"{detail}，统计优势略正")]
    if pct >= 0.4:
        return [FactorScore("道藏模型", 0, True, f"{detail}，模型中性")]
    if pct >= 0.25:
        return [FactorScore("道藏模型", -6, False, f"{detail}，模型分位偏低")]
    return [FactorScore("道藏模型", -14, False, f"{detail}，模型分位低")]
