from __future__ import annotations

from datetime import datetime

from beichen_alpha.models import FactorScore, NewsEvent


def score_news_events(events: list[NewsEvent], as_of: datetime | None = None) -> list[FactorScore]:
    if not events:
        return [FactorScore("新闻事件", 0, True, "近窗无显著新闻事件")]

    hard_risks = [event for event in events if event.hard_exclude and event.polarity < 0]
    if hard_risks:
        titles = "；".join(event.title for event in hard_risks[:2])
        return [FactorScore("新闻风险", -120, False, titles)]

    score = 0
    positive = []
    negative = []
    for event in events:
        decay = event_time_decay(event, as_of)
        multiplier = 26 if event.event_type.startswith("opinion_sector") else 20
        contribution = int(round(event.polarity * event.importance * event.confidence * decay * multiplier))
        score += contribution
        if contribution > 0:
            positive.append(event.title)
        elif contribution < 0:
            negative.append(event.title)

    score = max(min(score, 20), -30)
    if score > 0:
        detail = "利好: " + "；".join(positive[:2])
        passed = True
    elif score < 0:
        detail = "利空: " + "；".join(negative[:2])
        passed = False
    else:
        detail = "近窗无显著方向性新闻"
        passed = True

    return [FactorScore("新闻事件", score, passed, detail)]


def event_time_decay(event: NewsEvent, as_of: datetime | None) -> float:
    if event.event_type.startswith("opinion"):
        return opinion_time_decay(event, as_of)
    return time_decay(event, as_of)


def opinion_time_decay(event: NewsEvent, as_of: datetime | None) -> float:
    if event.published_at is None or as_of is None:
        return 0.45

    age_hours = max((as_of - event.published_at).total_seconds() / 3600, 0.0)
    if age_hours <= 12:
        return 1.0
    if age_hours <= 24:
        return 0.72
    if age_hours <= 48:
        return 0.45
    if age_hours <= 72:
        return 0.22
    if age_hours <= 120:
        return 0.08
    return 0.0


def time_decay(event: NewsEvent, as_of: datetime | None) -> float:
    if event.published_at is None or as_of is None:
        return 0.6
    age_days = max((as_of - event.published_at).days, 0)
    if age_days <= 0:
        return 1.0
    if age_days <= 1:
        return 0.7
    if age_days <= 3:
        return 0.4
    if age_days <= 7:
        return 0.15
    return 0.0
