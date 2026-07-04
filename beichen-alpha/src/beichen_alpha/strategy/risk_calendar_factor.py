from __future__ import annotations

from datetime import datetime

from beichen_alpha.models import FactorScore, RiskCalendarEvent


def score_risk_calendar_events(
    events: list[RiskCalendarEvent],
    as_of: datetime | None = None,
) -> list[FactorScore]:
    if not events:
        return [FactorScore("风险日历", 0, True, "未来窗口无解禁/质押硬风险，近窗无重大风险事件")]

    hard_risks = [event for event in events if event.hard_exclude]
    if hard_risks:
        return [
            FactorScore(
                "风险日历",
                -220,
                False,
                "；".join(format_event(event, as_of) for event in hard_risks[:2]),
            )
        ]

    risky_events = [event for event in events if event.severity >= 0.6]
    if risky_events:
        penalty = -min(90, sum(int(round(event.severity * 40)) for event in risky_events))
        return [
            FactorScore(
                "风险日历",
                penalty,
                False,
                "；".join(format_event(event, as_of) for event in risky_events[:2]),
            )
        ]

    return [FactorScore("风险日历", 0, True, "无高强度风险日历事件")]


def summarize_risk_calendar(events: list[RiskCalendarEvent], as_of: datetime | None = None) -> str:
    if not events:
        return "-"
    top = sorted(events, key=lambda item: (item.hard_exclude, item.severity), reverse=True)[0]
    prefix = "硬" if top.hard_exclude else "警"
    return f"{prefix}:{short_event_type(top.event_type)}"


def format_event(event: RiskCalendarEvent, as_of: datetime | None = None) -> str:
    date_text = ""
    if event.event_date is not None:
        date_text = event.event_date.strftime("%Y-%m-%d")
        if as_of is not None:
            days = (event.event_date.date() - as_of.date()).days
            if days >= 0:
                date_text += f"({days}天)"
    detail = event.detail or event.title
    return f"{short_event_type(event.event_type)} {date_text} {detail}".strip()


def short_event_type(event_type: str) -> str:
    return {
        "restricted_release": "解禁",
        "pledge_risk": "质押",
        "earnings_warning": "业绩",
        "shareholder_reduce": "减持",
        "regulatory_penalty": "监管",
        "regulatory_risk": "监管",
        "major_litigation": "诉讼",
        "litigation": "诉讼",
        "delisting_risk": "退市",
        "debt_liquidity_risk": "债务",
    }.get(event_type, event_type or "风险")
