from __future__ import annotations

import html
import json
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime

from beichen_alpha.models import ArticleContent


class WechatArticleSource:
    def __init__(self, url: str, source_name: str = "") -> None:
        self.url = url.strip()
        self.source_name = source_name.strip()

    def load(self) -> ArticleContent:
        raw_html = fetch_html(self.url)
        title, author, published_at, text = parse_wechat_html(raw_html)
        return ArticleContent(
            title=title,
            author=author,
            source_name=self.source_name or author,
            url=self.url,
            published_at=published_at,
            text=text,
        )


def fetch_html(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")
    except (ssl.SSLError, urllib.error.URLError):
        with urllib.request.urlopen(
            request,
            timeout=15,
            context=ssl._create_unverified_context(),
        ) as response:
            return response.read().decode("utf-8", errors="replace")


def parse_wechat_html(raw_html: str) -> tuple[str, str, datetime | None, str]:
    soup = import_bs4()(raw_html, "html.parser")
    title = first_text(soup.find(id="activity-name")) or meta_content(soup, "og:title")
    author = match_js_var(raw_html, "author") or first_text(soup.find(id="js_name"))
    published_at = parse_publish_time(raw_html)

    content_node = soup.find(id="js_content")
    if content_node is None:
        raise RuntimeError("No WeChat article content found in #js_content")
    for bad_node in content_node.find_all(["script", "style", "svg"]):
        bad_node.decompose()

    text = content_node.get_text("\n", strip=True)
    text = "\n".join(line for line in (clean_text(line) for line in text.splitlines()) if line)
    if not text:
        raise RuntimeError("WeChat article content is empty")

    return clean_text(title), clean_text(author), published_at, text


def import_bs4():
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError("beautifulsoup4 is required for article ingestion") from exc
    return BeautifulSoup


def first_text(node) -> str:
    if node is None:
        return ""
    return clean_text(node.get_text(" ", strip=True))


def meta_content(soup, key: str) -> str:
    node = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    if node is None:
        return ""
    return clean_text(node.get("content", ""))


def match_js_var(raw_html: str, name: str) -> str:
    match = re.search(rf"var\s+{re.escape(name)}\s*=\s*([\"'])(.*?)\1", raw_html, re.S)
    if not match:
        return ""
    value = html.unescape(match.group(2).strip())
    try:
        return clean_text(json.loads(f'"{value}"'))
    except json.JSONDecodeError:
        return clean_text(value)


def parse_publish_time(raw_html: str) -> datetime | None:
    publish_time = match_js_var(raw_html, "publish_time")
    if publish_time:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(publish_time, fmt)
            except ValueError:
                pass

    ct_match = re.search(r'var\s+ct\s*=\s*"(\d+)"', raw_html)
    if ct_match:
        try:
            return datetime.fromtimestamp(int(ct_match.group(1)))
        except ValueError:
            pass
    return None


def clean_text(value: str) -> str:
    return " ".join(html.unescape(str(value or "")).split())
