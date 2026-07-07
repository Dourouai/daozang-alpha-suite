from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from beichen_alpha.data_sources.akshare_source import normalize_symbol
from beichen_alpha.models import RiskCalendarEvent


DEFAULT_STATIC_RISK_CALENDAR_PATH = "../daozang-alpha/data/universe/akshare_risk_calendar.csv"


def load_static_risk_calendar(
    path: str | Path = DEFAULT_STATIC_RISK_CALENDAR_PATH,
    *,
    symbols: Iterable[str] | None = None,
    as_of: datetime | None = None,
    forward_days: int | None = None,
) -> dict[str, list[RiskCalendarEvent]]:
    calendar_path = Path(path)
    if not str(path) or not calendar_path.exists() or calendar_path.is_dir():
        return {}

    symbol_set = {normalize_symbol(symbol) for symbol in (symbols or ()) if str(symbol).strip()}
    result: dict[str, list[RiskCalendarEvent]] = {}
    with calendar_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            event = risk_event_from_row(row)
            if event is None:
                continue
            if symbol_set and event.code not in symbol_set:
                continue
            if not event_in_window(event, as_of=as_of, forward_days=forward_days):
                continue
            result.setdefault(event.code, []).append(event)

    return {code: sorted(events, key=lambda item: (item.hard_exclude, item.severity), reverse=True) for code, events in result.items()}


def risk_event_from_row(row: dict) -> RiskCalendarEvent | None:
    code = normalize_symbol(str(row.get("code") or ""))
    if not code:
        return None

    tags = str(row.get("risk_tags") or "").strip()
    detail = str(row.get("risk_detail") or "").strip()
    source = str(row.get("risk_source") or "道藏风险日历").strip()
    event_date = parse_datetime(row.get("event_date"))
    severity = to_float(row.get("severity"))
    hard_exclude = parse_bool(row.get("hard_exclude"))
    event_type = infer_event_type(tags, detail)

    title = tags or detail or "风险日历事件"
    return RiskCalendarEvent(
        code=code,
        title=title,
        source=source,
        event_date=event_date,
        event_type=event_type,
        severity=severity,
        hard_exclude=hard_exclude,
        detail=detail or title,
    )


def event_in_window(
    event: RiskCalendarEvent,
    *,
    as_of: datetime | None,
    forward_days: int | None,
) -> bool:
    if as_of is None or forward_days is None or event.event_date is None:
        return True
    end = as_of + timedelta(days=forward_days)
    return as_of.date() <= event.event_date.date() <= end.date()


def infer_event_type(tags: str, detail: str) -> str:
    text = f"{tags} {detail}"
    if "解禁" in text:
        return "restricted_release"
    if "质押" in text:
        return "pledge_risk"
    if "减持" in text:
        return "shareholder_reduce"
    if "退市" in text or "ST" in text.upper():
        return "delisting_risk"
    if "监管" in text or "处罚" in text:
        return "regulatory_risk"
    if "诉讼" in text:
        return "major_litigation"
    if "财报" in text or "业绩" in text:
        return "earnings_warning"
    return "risk_calendar"


def parse_datetime(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def to_float(value) -> float:
    try:
        return float(str(value or "0").replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def parse_bool(value) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "是"}
