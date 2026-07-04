from __future__ import annotations

import csv
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from beichen_alpha.models import MacroEvent

from .sector_rotation_source import normalize_sector_name


@dataclass(frozen=True)
class MacroRssFeed:
    name: str
    url: str
    source_type: str = "general_macro"
    lookback_days: int = 3


class MacroRssEventSource:
    def __init__(
        self,
        feeds_path: str | Path = "config/macro_rss_feeds.csv",
        as_of: datetime | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.feeds_path = Path(feeds_path)
        self.as_of = as_of or datetime.now()
        self.timeout = timeout

    def load(self) -> list[MacroEvent]:
        events: list[MacroEvent] = []
        for feed in load_macro_rss_feeds(self.feeds_path):
            try:
                xml_text = fetch_text(feed.url, timeout=self.timeout)
            except Exception:
                continue
            events.extend(parse_rss_events(xml_text, feed, as_of=self.as_of))
        return dedupe_events(events)


def load_macro_rss_feeds(path: str | Path) -> list[MacroRssFeed]:
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

    feeds: list[MacroRssFeed] = []
    for row in csv.DictReader(rows):
        if not enabled(row.get("enabled", "true")):
            continue
        name = str(row.get("name") or "").strip()
        url = str(row.get("url") or "").strip()
        if not name or not url:
            continue
        feeds.append(
            MacroRssFeed(
                name=name,
                url=url,
                source_type=str(row.get("source_type") or "general_macro").strip(),
                lookback_days=max(parse_int(row.get("lookback_days"), 3), 1),
            )
        )
    return feeds


def fetch_text(url: str, timeout: float = 8.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "BeichenAlpha/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_rss_events(xml_text: str, feed: MacroRssFeed, as_of: datetime) -> list[MacroEvent]:
    root = ET.fromstring(xml_text)
    items = parse_rss_items(root) or parse_atom_entries(root)
    events: list[MacroEvent] = []
    for item in items:
        published_at = item.get("published_at") or as_of
        if published_at > as_of:
            continue
        age_days = (as_of - published_at).total_seconds() / 86400
        if age_days > feed.lookback_days:
            continue
        event = classify_macro_rss_item(
            title=item.get("title", ""),
            summary=item.get("summary", ""),
            link=item.get("link", ""),
            published_at=published_at,
            feed=feed,
        )
        if event is not None:
            events.append(event)
    return events


def parse_rss_items(root: ET.Element) -> list[dict]:
    result = []
    for item in root.findall(".//item"):
        result.append(
            {
                "title": child_text(item, "title"),
                "summary": child_text(item, "description"),
                "link": child_text(item, "link"),
                "published_at": parse_rss_datetime(child_text(item, "pubDate") or child_text(item, "date")),
            }
        )
    return result


def parse_atom_entries(root: ET.Element) -> list[dict]:
    result = []
    namespaces = {"atom": "http://www.w3.org/2005/Atom"}
    for item in root.findall(".//atom:entry", namespaces):
        link = ""
        link_node = item.find("atom:link", namespaces)
        if link_node is not None:
            link = str(link_node.attrib.get("href") or "")
        result.append(
            {
                "title": child_text(item, "title"),
                "summary": child_text(item, "summary") or child_text(item, "content"),
                "link": link,
                "published_at": parse_rss_datetime(child_text(item, "published") or child_text(item, "updated")),
            }
        )
    return result


def child_text(item: ET.Element, name: str) -> str:
    node = item.find(name)
    if node is None:
        for child in item:
            if child.tag.endswith("}" + name):
                node = child
                break
    return "" if node is None or node.text is None else node.text.strip()


def classify_macro_rss_item(
    title: str,
    summary: str,
    link: str,
    published_at: datetime,
    feed: MacroRssFeed,
) -> MacroEvent | None:
    text = normalize_text(f"{title} {summary}")
    if not text:
        return None

    if feed.source_type in {"fed_speech", "fed_policy"}:
        return classify_fed_event(title, text, link, published_at, feed)
    if feed.source_type == "employment":
        return classify_employment_event(title, text, link, published_at, feed)
    if feed.source_type == "inflation":
        return classify_inflation_event(title, text, link, published_at, feed)
    if feed.source_type in {"policy", "china_policy", "official_policy"}:
        return classify_policy_event(title, text, link, published_at, feed)
    return classify_general_event(title, text, link, published_at, feed)


def classify_fed_event(title: str, text: str, link: str, published_at: datetime, feed: MacroRssFeed) -> MacroEvent | None:
    if not has_any(text, ("monetary policy", "inflation", "interest rate", "fomc", "policy rate", "labor market")):
        return None
    if has_any(text, ("rate cut", "lower rates", "easing", "disinflation", "cooling inflation", "labor market softening", "downside risks")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="fed_speech",
            stance="dovish",
            positive=("黄金", "资源", "半导体", "AI硬件", "医药"),
            negative=("银行",),
            base_score=6,
            confidence=0.55,
            detail="RSS识别为美联储偏鸽语义",
        )
    if has_any(text, ("rate increase", "higher rates", "tightening", "inflation risks", "too high", "higher for longer", "not sufficiently restrictive")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="fed_speech",
            stance="hawkish",
            positive=("银行", "公用事业"),
            negative=("黄金", "半导体", "AI硬件", "医药"),
            base_score=6,
            confidence=0.55,
            detail="RSS识别为美联储偏鹰语义",
        )
    return None


def classify_employment_event(title: str, text: str, link: str, published_at: datetime, feed: MacroRssFeed) -> MacroEvent | None:
    if not has_any(text, ("employment situation", "payroll employment", "unemployment rate", "nonfarm")):
        return None
    payroll = parse_payroll_change(text)
    if payroll is not None and payroll < 120_000:
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="us_jobs",
            stance="dovish",
            positive=("黄金", "资源", "半导体", "AI硬件", "医药"),
            negative=("银行",),
            base_score=7,
            confidence=0.65,
            detail=f"RSS识别就业偏弱: 非农约 {payroll:,}",
        )
    if payroll is not None and payroll > 250_000:
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="us_jobs",
            stance="hawkish",
            positive=("银行", "公用事业"),
            negative=("黄金", "半导体", "AI硬件", "医药"),
            base_score=7,
            confidence=0.65,
            detail=f"RSS识别就业偏强: 非农约 {payroll:,}",
        )
    if has_any(text, ("changed little", "softened", "weakened", "lost jobs")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="us_jobs",
            stance="dovish",
            positive=("黄金", "资源", "半导体", "AI硬件", "医药"),
            negative=("银行",),
            base_score=5,
            confidence=0.5,
            detail="RSS识别就业偏弱语义",
        )
    return None


def classify_inflation_event(title: str, text: str, link: str, published_at: datetime, feed: MacroRssFeed) -> MacroEvent | None:
    if not has_any(text, ("cpi", "pce", "inflation", "consumer prices", "personal income and outlays")):
        return None
    if has_any(text, ("accelerated", "higher", "increased sharply", "inflation rose")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="us_inflation",
            stance="hawkish_inflation",
            positive=("银行", "能源", "资源"),
            negative=("半导体", "AI硬件", "医药"),
            base_score=6,
            confidence=0.5,
            detail="RSS识别通胀偏强语义",
        )
    if has_any(text, ("slowed", "cooled", "lower", "moderated", "disinflation")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="us_inflation",
            stance="dovish_disinflation",
            positive=("黄金", "资源", "半导体", "AI硬件", "医药"),
            negative=("银行",),
            base_score=6,
            confidence=0.5,
            detail="RSS识别通胀降温语义",
        )
    return None


def classify_general_event(title: str, text: str, link: str, published_at: datetime, feed: MacroRssFeed) -> MacroEvent | None:
    policy_event = classify_policy_event(title, text, link, published_at, feed)
    if policy_event is not None:
        return policy_event

    if has_any(text, ("central bank", "policy rate", "interest rate")) and has_any(text, ("rate hike", "raises rates", "raised rates", "tightening")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="central_bank",
            stance="hawkish_global",
            positive=("银行", "公用事业"),
            negative=("半导体", "AI硬件", "医药", "材料"),
            base_score=6,
            confidence=0.5,
            detail="RSS识别主要央行加息或紧缩事件",
        )
    if has_any(text, ("central bank", "policy rate", "interest rate")) and has_any(text, ("rate cut", "cuts rates", "lower rates", "easing")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="central_bank",
            stance="dovish_global",
            positive=("非银金融", "半导体", "AI硬件", "医药", "材料"),
            negative=("银行",),
            base_score=6,
            confidence=0.5,
            detail="RSS识别主要央行降息或宽松事件",
        )
    if has_any(text, ("export control", "sanction", "tariff", "trade restriction", "geopolitical")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="key_event",
            stance="risk_off",
            positive=("军工", "黄金", "资源", "国产替代"),
            negative=("出口链", "电子", "AI硬件"),
            base_score=6,
            confidence=0.45,
            detail="RSS识别贸易/地缘关键事件",
        )
    if has_any(text, ("breakthrough", "mass production", "commercialization", "industrial innovation", "robotics", "humanoid robot", "solid-state battery")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="industry_innovation",
            stance="innovation",
            positive=("机器人", "新能源", "半导体", "AI硬件", "材料"),
            negative=("公用事业",),
            base_score=5,
            confidence=0.45,
            detail="RSS识别产业创新事件",
        )
    if has_any(text, ("semiconductor", "chip", "ai infrastructure")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="global_chip",
            stance="risk_on",
            positive=("半导体", "AI硬件", "材料", "电子"),
            negative=("公用事业",),
            base_score=5,
            confidence=0.45,
            detail="RSS识别全球半导体链事件",
        )
    if has_any(text, ("oil", "crude", "opec")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="oil",
            stance="energy",
            positive=("石油石化", "煤炭", "资源"),
            negative=("航空", "化工"),
            base_score=5,
            confidence=0.45,
            detail="RSS识别原油能源事件",
        )
    return None


def classify_policy_event(title: str, text: str, link: str, published_at: datetime, feed: MacroRssFeed) -> MacroEvent | None:
    if not has_any(
        text,
        (
            "政策",
            "措施",
            "通知",
            "意见",
            "方案",
            "规划",
            "支持",
            "促进",
            "监管",
            "整治",
            "税",
            "费",
            "财政",
            "央行",
            "人民银行",
            "降准",
            "lpr",
            "policy",
            "regulation",
            "fiscal",
            "tax",
            "subsidy",
        ),
    ):
        return None

    if has_any(text, ("降准", "降息", "公开市场净投放", "流动性合理充裕", "货币政策支持", "lower rates", "easing policy", "liquidity support")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="policy_event",
            stance="china_liquidity_support",
            positive=("非银金融", "银行", "半导体", "AI硬件", "材料", "数字经济"),
            negative=("公用事业",),
            base_score=7,
            confidence=0.6,
            detail="政策源识别国内流动性支持",
            decay_days=3,
        )
    if has_any(text, ("减税", "降费", "退税", "税费优惠", "税收优惠", "优惠政策", "零关税", "关税减免", "财政贴息", "财政补贴", "专项债", "tax cut", "tax relief", "subsidy")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="policy_event",
            stance="fiscal_support",
            positive=("非银金融", "基建", "工程机械", "消费", "新能源", "数字经济", "先进制造"),
            negative=("公用事业",),
            base_score=7,
            confidence=0.58,
            detail="政策源识别财政/税费支持",
            decay_days=4,
        )
    if has_any(text, ("新型能源体系", "节能降碳", "绿色低碳", "双碳", "可再生能源", "新能源", "能源体系", "绿色发展")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="policy_event",
            stance="green_energy_policy",
            positive=("新能源", "材料", "工业金属", "公用事业", "先进制造"),
            negative=("煤炭", "石油石化"),
            base_score=6,
            confidence=0.56,
            detail="政策源识别绿色能源政策支持",
            decay_days=5,
        )
    if has_any(text, ("税率上调", "提高税率", "补税", "严查偷逃税", "征管趋严", "规范税收征管", "tax increase", "tax enforcement")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="policy_event",
            stance="tax_tightening",
            positive=("银行", "公用事业", "高股息"),
            negative=("地产链", "消费", "平台经济", "中小企业"),
            base_score=6,
            confidence=0.55,
            detail="政策源识别税费/征管趋严",
            decay_days=3,
        )
    if has_any(text, ("资本市场", "活跃资本市场", "长期资金入市", "并购重组", "回购增持", "提高上市公司质量", "资本市场改革")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="policy_event",
            stance="capital_market_support",
            positive=("非银金融", "银行", "数字经济", "先进制造"),
            negative=("公用事业",),
            base_score=8,
            confidence=0.62,
            detail="政策源识别资本市场支持",
            decay_days=3,
        )
    if has_any(text, ("房地产", "住房", "房贷", "首付", "限购", "认房不认贷", "城中村", "保障性住房", "property", "housing")):
        if has_any(text, ("降低", "下调", "放松", "取消限购", "支持", "去库存", "专项借款", "lower", "easing")):
            return build_event(
                title,
                feed,
                published_at,
                link,
                event_type="policy_event",
                stance="real_estate_easing",
                positive=("房地产", "地产链", "建材", "家居", "家电", "银行"),
                negative=("公用事业",),
                base_score=6,
                confidence=0.55,
                detail="政策源识别房地产链支持",
                decay_days=3,
            )
        if has_any(text, ("收紧", "上调", "提高", "调控", "从严", "整治", "tightening", "restriction")):
            return build_event(
                title,
                feed,
                published_at,
                link,
                event_type="policy_event",
                stance="real_estate_tightening",
                positive=("公用事业", "银行"),
                negative=("房地产", "地产链", "建材", "家居", "家电"),
                base_score=6,
                confidence=0.55,
                detail="政策源识别房地产链收紧",
                decay_days=3,
            )
    if has_any(text, ("消费品以旧换新", "促消费", "消费券", "家电下乡", "汽车消费", "消费补贴", "trade-in", "consumer spending")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="policy_event",
            stance="consumption_support",
            positive=("消费", "家电", "汽车", "新能源", "平台经济"),
            negative=("公用事业",),
            base_score=6,
            confidence=0.55,
            detail="政策源识别消费支持",
            decay_days=4,
        )
    if has_any(text, ("人工智能", "算力", "数据要素", "数字经济", "半导体", "先进制造", "新质生产力", "设备更新", "低空经济", "机器人")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="policy_event",
            stance="industrial_policy_support",
            positive=("AI硬件", "半导体", "数字经济", "先进制造", "机器人", "低空经济", "材料"),
            negative=("公用事业",),
            base_score=7,
            confidence=0.58,
            detail="政策源识别产业政策支持",
            decay_days=5,
        )
    if has_any(text, ("反垄断", "平台监管", "数据安全", "网络安全审查", "医药反腐", "集采", "价格治理", "专项整治", "antitrust", "regulatory crackdown")):
        return build_event(
            title,
            feed,
            published_at,
            link,
            event_type="policy_event",
            stance="regulatory_tightening",
            positive=("公用事业", "高股息"),
            negative=("平台经济", "医药", "互联网", "消费"),
            base_score=7,
            confidence=0.58,
            detail="政策源识别行业监管趋严",
            decay_days=4,
        )
    return None


def build_event(
    title: str,
    feed: MacroRssFeed,
    published_at: datetime,
    link: str,
    event_type: str,
    stance: str,
    positive: tuple[str, ...],
    negative: tuple[str, ...],
    base_score: int,
    confidence: float,
    detail: str,
    decay_days: int = 2,
) -> MacroEvent:
    return MacroEvent(
        event_date=published_at.replace(tzinfo=None),
        title=title.strip() or feed.name,
        source=f"rss:{feed.name}",
        event_type=event_type,
        stance=stance,
        positive_sectors=normalize_event_sectors(positive),
        negative_sectors=normalize_event_sectors(negative),
        base_score=base_score,
        decay_days=decay_days,
        confidence=confidence,
        detail=detail,
        url=link,
    )


def normalize_event_sectors(sectors: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for sector in sectors:
        normalized = normalize_sector_name(sector) or normalize_policy_sector(sector)
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def normalize_policy_sector(text: str) -> str:
    value = str(text or "").strip()
    if value in {"先进制造", "高端制造", "设备更新", "工业母机"}:
        return "先进制造"
    if value in {"基建", "建筑", "建筑工程", "工程机械", "水利", "轨交"}:
        return "基建"
    if value in {"房地产", "地产", "地产链", "建材", "家居"}:
        return "房地产"
    if value in {"家电", "汽车", "消费", "平台经济", "互联网", "中小企业"}:
        return "消费"
    if value in {"高股息", "红利", "防御"}:
        return "公用事业"
    if value in {"数字经济", "数据要素", "软件", "计算机"}:
        return "数字经济"
    return value


def parse_payroll_change(text: str) -> int | None:
    patterns = (
        r"payroll employment\s*\(\+?(-?[\d,]+)\)",
        r"nonfarm payroll employment\s*\(\+?(-?[\d,]+)\)",
        r"payrolls?\s+(?:rose|increased|added|up)\s+by\s+([\d,]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


def parse_rss_datetime(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        value = parsedate_to_datetime(raw)
        return value.replace(tzinfo=None)
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=None)
        except ValueError:
            pass
    return None


def dedupe_events(events: list[MacroEvent]) -> list[MacroEvent]:
    seen = set()
    result = []
    for event in events:
        key = (event.title, event.url, event.event_date.date())
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def enabled(value: str) -> bool:
    return str(value or "").strip().lower() not in {"0", "false", "no", "n", "off", "否"}


def parse_int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
