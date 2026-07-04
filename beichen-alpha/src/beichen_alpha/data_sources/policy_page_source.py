from __future__ import annotations

import csv
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from beichen_alpha.models import MacroEvent

from .macro_rss_source import MacroRssFeed, classify_policy_event, dedupe_events


@dataclass(frozen=True)
class PolicyPage:
    name: str
    url: str
    source_type: str = "policy"
    lookback_days: int = 7


class PolicyPageEventSource:
    def __init__(
        self,
        pages_path: str | Path = "config/macro_policy_pages.csv",
        as_of: datetime | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.pages_path = Path(pages_path)
        self.as_of = as_of or datetime.now()
        self.timeout = timeout

    def load(self) -> list[MacroEvent]:
        events: list[MacroEvent] = []
        for page in load_policy_pages(self.pages_path):
            try:
                html_text = fetch_text(page.url, timeout=self.timeout)
            except Exception:
                continue
            events.extend(parse_policy_page_events(html_text, page, as_of=self.as_of))
        return collapse_policy_events(dedupe_events(events))


def load_policy_pages(path: str | Path) -> list[PolicyPage]:
    target = Path(path)
    if not target.exists() or target.is_dir():
        return []
    rows = [
        line
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not rows:
        return []

    pages: list[PolicyPage] = []
    for row in csv.DictReader(rows):
        if not enabled(row.get("enabled", "true")):
            continue
        name = str(row.get("name") or "").strip()
        url = str(row.get("url") or "").strip()
        if not name or not url:
            continue
        pages.append(
            PolicyPage(
                name=name,
                url=url,
                source_type=str(row.get("source_type") or "policy").strip(),
                lookback_days=max(parse_int(row.get("lookback_days"), 7), 1),
            )
        )
    return pages


def fetch_text(url: str, timeout: float = 8.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "BeichenAlpha/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_policy_page_events(html_text: str, page: PolicyPage, as_of: datetime) -> list[MacroEvent]:
    rss_feed = MacroRssFeed(page.name, page.url, page.source_type, page.lookback_days)
    events: list[MacroEvent] = []
    for item in parse_policy_page_items(html_text, page.url, as_of=as_of):
        published_at = item["published_at"] or as_of
        if published_at > as_of:
            continue
        age_days = (as_of - published_at).total_seconds() / 86400
        if age_days > page.lookback_days:
            continue
        event = classify_policy_event(
            title=item["title"],
            text=item["title"],
            link=item["link"],
            published_at=published_at,
            feed=rss_feed,
        )
        if event is not None:
            events.append(event)
    return events


def collapse_policy_events(events: list[MacroEvent]) -> list[MacroEvent]:
    result: list[MacroEvent] = []
    seen = set()
    for event in events:
        key = (
            event.event_date.date(),
            event.stance,
            event.positive_sectors,
            event.negative_sectors,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def parse_policy_page_items(html_text: str, base_url: str, as_of: datetime | None = None) -> list[dict]:
    parser = PolicyListParser(base_url=base_url, as_of=as_of)
    parser.feed(html_text)
    return [item for item in parser.items if item["title"]]


class PolicyListParser(HTMLParser):
    def __init__(self, base_url: str, as_of: datetime | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.as_of = as_of or datetime.now()
        self.items: list[dict] = []
        self.in_link = False
        self.current_href = ""
        self.current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href") or ""
        self.in_link = True
        self.current_href = href
        self.current_text = []

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if not text:
            return
        if self.in_link:
            self.current_text.append(text)
            return
        if self.items and self.items[-1]["published_at"] is None:
            parsed = parse_policy_date(text, self.as_of)
            if parsed is not None:
                self.items[-1]["published_at"] = parsed

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self.in_link:
            return
        title = clean_text("".join(self.current_text))
        if title and looks_like_policy_link(self.current_href) and looks_like_policy_title(title):
            self.items.append(
                {
                    "title": title,
                    "link": urljoin(self.base_url, self.current_href),
                    "published_at": parse_policy_date(title, self.as_of),
                }
            )
        self.in_link = False
        self.current_href = ""
        self.current_text = []


def looks_like_policy_title(title: str) -> bool:
    if len(title) < 8 or title in NAV_TITLES or any(phrase in title for phrase in FOLLOW_UP_TITLE_PHRASES):
        return False
    return any(
        keyword in title
        for keyword in (
            "政策",
            "通知",
            "公告",
            "意见",
            "方案",
            "规划",
            "办法",
            "措施",
            "支持",
            "促进",
            "监管",
            "税",
            "费",
            "财政",
            "央行",
            "人民银行",
            "逆回购",
            "LPR",
            "MLF",
            "国务院",
            "证监会",
            "上交所",
            "深交所",
            "交易所",
            "资本市场",
            "上市公司",
            "并购",
            "重组",
            "回购",
            "增持",
            "退市",
            "融资融券",
            "程序化交易",
        )
    )


NAV_TITLES = {
    "政策",
    "通知",
    "公告",
    "政策发布",
    "政策解读",
    "规划文本",
    "财政收支",
    "积极财政政策",
    "减税降费",
    "财政视频",
    "货币政策",
    "信贷政策",
    "公告信息",
    "央行研究",
    "本所要闻",
    "热点动态",
    "辖区监管动态",
}


FOLLOW_UP_TITLE_PHRASES = {
    "答记者问",
    "专家解读",
    "一图读懂",
    "图解",
    "媒体解读",
}


def looks_like_policy_link(href: str) -> bool:
    value = str(href or "").strip().lower()
    return bool(value) and not value.startswith(("javascript:", "#", "mailto:", "tel:"))


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_policy_date(text: str, as_of: datetime) -> datetime | None:
    match = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def enabled(value: str) -> bool:
    return str(value or "").strip().lower() not in {"0", "false", "no", "n", "off", "否"}


def parse_int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
