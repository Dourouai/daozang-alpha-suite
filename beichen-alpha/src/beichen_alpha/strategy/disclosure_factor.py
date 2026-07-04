from __future__ import annotations

from datetime import datetime

from beichen_alpha.models import FactorScore, NewsEvent

from .news_factor import time_decay


def score_disclosure_events(events: list[NewsEvent], as_of: datetime | None = None) -> list[FactorScore]:
    if not events:
        return [FactorScore("公告风险", 0, True, "近窗无重大风险公告")]

    hard_risks = [event for event in events if event.hard_exclude and event.polarity < 0]
    if hard_risks:
        titles = "；".join(event.title for event in hard_risks[:2])
        return [FactorScore("公告风险", -180, False, titles)]

    positive = []
    negative = []
    score = 0
    for event in events:
        if event.polarity == 0:
            continue
        contribution = int(
            round(abs(event.polarity) * event.importance * event.confidence * time_decay(event, as_of) * 24)
        )
        if contribution <= 0:
            continue
        if event.polarity > 0:
            score += contribution
            positive.append(event.title)
        else:
            score -= contribution
            negative.append(event.title)

    score = max(min(score, 24), -32)
    if score == 0:
        return [FactorScore("公告风险", 0, True, "近窗无重大风险公告")]

    detail_parts = []
    if positive:
        detail_parts.append("利好公告: " + "；".join(positive[:2]))
    if negative:
        detail_parts.append("负面公告: " + "；".join(negative[:2]))
    return [FactorScore("公告事件", score, True, "；".join(detail_parts))]
