from __future__ import annotations

from datetime import datetime
from typing import Iterable

from beichen_alpha.events import classify_news, filter_events
from beichen_alpha.models import NewsEvent


class AkshareNewsSource:
    def __init__(self, symbols: Iterable[str], as_of: datetime, lookback_days: int = 7) -> None:
        self.symbols = [symbol.strip() for symbol in symbols if symbol.strip()]
        self.as_of = as_of
        self.lookback_days = lookback_days

    def load(self) -> dict[str, list[NewsEvent]]:
        ak = import_akshare()
        result: dict[str, list[NewsEvent]] = {}
        for symbol in self.symbols:
            try:
                events = fetch_stock_news(ak, symbol)
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


def fetch_stock_news(ak, symbol: str) -> list[NewsEvent]:
    frame = ak.stock_news_em(symbol=symbol)
    events: list[NewsEvent] = []
    for record in frame.to_dict(orient="records"):
        title = str(record.get("新闻标题") or "").strip()
        if not title:
            continue
        events.append(
            classify_news(
                code=symbol,
                title=title,
                source=str(record.get("文章来源") or "东方财富"),
                url=str(record.get("新闻链接") or ""),
                published_at=parse_datetime(record.get("发布时间")),
                content=str(record.get("新闻内容") or ""),
            )
        )
    return events


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
