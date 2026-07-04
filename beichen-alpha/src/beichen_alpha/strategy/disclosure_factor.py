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
    score = 0
    for event in events:
        if event.polarity <= 0:
            continue
        contribution = int(
            round(event.polarity * event.importance * event.confidence * time_decay(event, as_of) * 24)
        )
        if contribution > 0:
            score += contribution
            positive.append(event.title)

    score = min(score, 24)
    if score <= 0:
        return [FactorScore("公告风险", 0, True, "近窗无重大风险公告")]

    return [FactorScore("公告事件", score, True, "利好公告: " + "；".join(positive[:2]))]
