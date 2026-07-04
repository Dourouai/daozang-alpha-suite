from __future__ import annotations

import contextlib
import io
from datetime import datetime, timedelta
from typing import Callable, Iterable

from beichen_alpha.data_sources.akshare_source import import_akshare, normalize_symbol
from beichen_alpha.models import NewsEvent, RiskCalendarEvent


class AkshareRiskCalendarSource:
    def __init__(
        self,
        symbols: Iterable[str],
        as_of: datetime,
        forward_days: int = 30,
        include_pledge: bool = True,
    ) -> None:
        self.symbols = [normalize_symbol(symbol) for symbol in symbols if symbol.strip()]
        self.as_of = as_of
        self.forward_days = forward_days
        self.include_pledge = include_pledge

    def load(self) -> dict[str, list[RiskCalendarEvent]]:
        ak = import_akshare()
        result: dict[str, list[RiskCalendarEvent]] = {}
        for symbol in self.symbols:
            events: list[RiskCalendarEvent] = []
            try:
                events.extend(fetch_restricted_release_events(ak, symbol, self.as_of, self.forward_days))
            except Exception:
                pass
            if self.include_pledge:
                try:
                    events.extend(fetch_pledge_events(ak, symbol, self.as_of))
                except Exception:
                    pass
            result[symbol] = dedupe_events(events)
        return result


def fetch_restricted_release_events(ak, symbol: str, as_of: datetime, forward_days: int) -> list[RiskCalendarEvent]:
    frame = quiet_call(ak.stock_restricted_release_queue_em, symbol=symbol)
    events = []
    end_date = as_of + timedelta(days=forward_days)
    for record in frame.to_dict(orient="records"):
        event_date = parse_datetime(record.get("解禁时间"))
        if event_date is None or not (as_of <= event_date <= end_date):
            continue
        pct_total = normalize_ratio(record.get("占总市值比例"))
        pct_float = normalize_ratio(record.get("占流通市值比例"))
        amount_value = to_float(record.get("实际解禁数量市值"))
        days = max((event_date.date() - as_of.date()).days, 0)
        severity, hard_exclude = score_release_risk(days, pct_total, pct_float)
        if severity <= 0:
            continue
        detail = (
            f"{days}天后解禁，总市值占比 {format_pct(pct_total)}，"
            f"流通市值占比 {format_pct(pct_float)}，市值 {amount_value / 100_000_000:.1f}亿"
        )
        events.append(
            RiskCalendarEvent(
                code=symbol,
                title=f"限售解禁: {event_date.strftime('%Y-%m-%d')}",
                source="东方财富限售解禁",
                event_date=event_date,
                event_type="restricted_release",
                severity=severity,
                hard_exclude=hard_exclude,
                detail=detail,
            )
        )
    return events


def fetch_pledge_events(ak, symbol: str, as_of: datetime) -> list[RiskCalendarEvent]:
    frame = quiet_call(ak.stock_gpzy_individual_pledge_ratio_detail_em, symbol=symbol)
    active_rows = []
    for record in frame.to_dict(orient="records"):
        status = str(record.get("状态") or "")
        if status and any(keyword in status for keyword in ("已解押", "解除", "到期购回")):
            continue
        pledge_ratio = to_float(record.get("占总股本比例"))
        if pledge_ratio <= 0:
            continue
        latest = to_float(record.get("最新价"))
        close_line = to_float(record.get("预估平仓线"))
        active_rows.append((record, pledge_ratio, latest, close_line))

    if not active_rows:
        return []

    total_ratio = sum(item[1] for item in active_rows)
    near_close_line = [
        (record, latest, close_line)
        for record, _, latest, close_line in active_rows
        if latest > 0 and close_line > 0 and latest <= close_line * 1.2
    ]
    severity, hard_exclude = score_pledge_risk(total_ratio, bool(near_close_line))
    if severity <= 0:
        return []

    top_holder = str(active_rows[0][0].get("股东名称") or "未知股东")
    detail = f"存续质押占总股本约 {total_ratio:.2f}%，涉及 {len(active_rows)} 笔"
    if near_close_line:
        detail += "，存在接近平仓线记录"
    return [
        RiskCalendarEvent(
            code=symbol,
            title=f"股权质押风险: {top_holder}",
            source="东方财富股权质押",
            event_date=as_of,
            event_type="pledge_risk",
            severity=severity,
            hard_exclude=hard_exclude,
            detail=detail,
        )
    ]


def disclosure_events_to_risk_calendar(
    disclosure_events: dict[str, list[NewsEvent]],
) -> dict[str, list[RiskCalendarEvent]]:
    result: dict[str, list[RiskCalendarEvent]] = {}
    for code, events in disclosure_events.items():
        converted = []
        for event in events:
            if event.polarity >= 0:
                continue
            converted.append(
                RiskCalendarEvent(
                    code=code,
                    title=event.title,
                    source=event.source,
                    event_date=event.published_at,
                    event_type=event.event_type,
                    severity=event.importance,
                    hard_exclude=event.hard_exclude,
                    detail=f"{event.source}: {event.title}",
                    url=event.url,
                )
            )
        result[code] = converted
    return result


def score_release_risk(days: int, pct_total: float | None, pct_float: float | None) -> tuple[float, bool]:
    pressure = max(pct_total or 0.0, (pct_float or 0.0) * 0.7)
    if pressure <= 0:
        return 0.0, False
    if days <= 7 and pressure >= 1.0:
        return 1.0, True
    if days <= 30 and pressure >= 5.0:
        return 0.95, True
    if days <= 30 and pressure >= 2.0:
        return 0.75, False
    return 0.0, False


def score_pledge_risk(total_ratio: float, near_close_line: bool) -> tuple[float, bool]:
    if near_close_line and total_ratio >= 3.0:
        return 0.95, True
    if total_ratio >= 20.0:
        return 0.9, True
    if total_ratio >= 10.0:
        return 0.7, False
    return 0.0, False


def merge_risk_event_maps(*event_maps: dict[str, list[RiskCalendarEvent]]) -> dict[str, list[RiskCalendarEvent]]:
    merged: dict[str, list[RiskCalendarEvent]] = {}
    for event_map in event_maps:
        for code, events in event_map.items():
            merged.setdefault(code, []).extend(events)
    return {code: dedupe_events(events) for code, events in merged.items()}


def quiet_call(func: Callable, **kwargs):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return func(**kwargs)


def parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def normalize_ratio(value) -> float | None:
    raw = to_optional_float(value)
    if raw is None:
        return None
    if 0 < raw <= 1:
        return raw * 100
    return raw


def to_float(value) -> float:
    parsed = to_optional_float(value)
    return 0.0 if parsed is None else parsed


def to_optional_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def format_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}%"


def dedupe_events(events: list[RiskCalendarEvent]) -> list[RiskCalendarEvent]:
    seen = set()
    result = []
    for event in events:
        key = (event.code, event.event_type, event.title, event.event_date)
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return sorted(result, key=lambda item: (item.hard_exclude, item.severity), reverse=True)
