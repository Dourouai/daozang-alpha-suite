"""Advanced factors: 股东增减持 (P2)."""

from __future__ import annotations
from datetime import datetime
from beichen_alpha.models import FactorScore
from beichen_alpha.data_sources.advanced_source import AdvancedSnapshot


def score_advanced_factors(
    code: str, snapshot: AdvancedSnapshot | None,
    as_of: datetime | None = None,
) -> list[FactorScore]:
    if snapshot is None:
        return [FactorScore("股东行为", 0, True, "股东行为数据不可用")]
    scores = []
    scores.extend(_score_shareholder(code, snapshot, as_of))
    if not scores:
        scores.append(FactorScore("股东行为", 0, True, "无股东行为数据"))
    return scores


def _score_shareholder(code, snapshot, as_of=None):
    records = snapshot.shareholder_records.get(code, [])
    if not records:
        return [FactorScore("股东增减持", 0, True, "近6月无股东增减持记录")]

    cutoff = (as_of or datetime.now()).date()
    recent = [r for r in records if r.announce_date and (cutoff - r.announce_date).days <= 90]
    if not recent:
        return [FactorScore("股东增减持", 0, True, "增减持记录超过3个月")]

    total_change = sum(r.change_amount for r in recent)  # 万股
    score = 0; passed = True; reasons = []

    if total_change >= 1000:  # 增持超1000万股
        score += 14; reasons.append(f"股东增持{total_change:.0f}万股")
    elif total_change >= 100:
        score += 8; reasons.append(f"股东增持{total_change:.0f}万股")
    elif total_change >= 10:
        score += 3; reasons.append(f"股东小幅增持{total_change:.0f}万股")
    elif total_change >= -10:
        pass
    elif total_change >= -100:
        score -= 4; reasons.append(f"股东减持{abs(total_change):.0f}万股")
    else:
        score -= 14; reasons.append(f"股东大额减持{abs(total_change):.0f}万股")
        passed = False

    # Number of shareholders acting
    unique_holders = {r.shareholder for r in recent if r.shareholder}
    if len(unique_holders) >= 2 and total_change > 0:
        score += 4; reasons.append(f"{len(unique_holders)}位股东增持")

    score = max(min(score, 22), -18)
    return [FactorScore("股东增减持", score, passed, "；".join(reasons))]
