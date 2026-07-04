from __future__ import annotations

import contextlib
import io
import re
from datetime import datetime, timedelta
from typing import Callable
from typing import Iterable

from beichen_alpha.events import classify_disclosure, filter_events
from beichen_alpha.models import NewsEvent


class CninfoDisclosureSource:
    def __init__(self, symbols: Iterable[str], as_of: datetime, lookback_days: int = 60) -> None:
        self.symbols = [symbol.strip() for symbol in symbols if symbol.strip()]
        self.as_of = as_of
        self.lookback_days = lookback_days

    def load(self) -> dict[str, list[NewsEvent]]:
        ak = import_akshare()
        start_date = (self.as_of - timedelta(days=self.lookback_days)).strftime("%Y%m%d")
        end_date = self.as_of.strftime("%Y%m%d")
        result: dict[str, list[NewsEvent]] = {}

        for symbol in self.symbols:
            try:
                events = fetch_cninfo_disclosures(ak, symbol, start_date, end_date)
            except Exception:
                events = []
            result[symbol] = filter_events(events, self.as_of, self.lookback_days)

        return result


def import_akshare():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "AKShare is not installed. Install it with: python3 -m pip install akshare pandas"
        ) from exc
    return ak


def fetch_cninfo_disclosures(ak, symbol: str, start_date: str, end_date: str) -> list[NewsEvent]:
    frame = quiet_call(
        ak.stock_zh_a_disclosure_report_cninfo,
        symbol=symbol,
        market="沪深京",
        keyword="",
        category="",
        start_date=start_date,
        end_date=end_date,
    )
    events: list[NewsEvent] = []
    for record in frame.to_dict(orient="records"):
        title = clean_title(record.get("公告标题"))
        if not title:
            continue
        event = classify_disclosure(
            code=symbol,
            title=title,
            source="巨潮公告",
            url=str(record.get("公告链接") or ""),
            published_at=parse_datetime(record.get("公告时间")),
        )
        if event.event_type != "neutral":
            events.append(event)

    return dedupe_events(events)


def quiet_call(func: Callable, **kwargs):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return func(**kwargs)


def clean_title(value) -> str:
    text = str(value or "").strip()
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split())


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


def dedupe_events(events: list[NewsEvent]) -> list[NewsEvent]:
    seen = set()
    result = []
    for event in events:
        key = (event.code, event.title, event.published_at)
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result
