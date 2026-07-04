from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from beichen_alpha.models import MacroEvent

from .macro_rss_source import dedupe_events
from .policy_page_source import fetch_text, parse_policy_page_items


PBOC_OPEN_MARKET_URL = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/index.html"
PBOC_LPR_URL = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/index.html"
EASTMONEY_LPR_URL = "https://data.eastmoney.com/cjsj/globalRateLPR.html"
EASTMONEY_RRR_URL = "https://data.eastmoney.com/cjsj/ckzbj.html"
EASTMONEY_MONEY_SUPPLY_URL = "https://data.eastmoney.com/cjsj/hbgyl.html"
EASTMONEY_CREDIT_URL = "https://data.eastmoney.com/cjsj/xzxd.html"
MOFCOM_SOCIAL_FINANCING_URL = "https://data.mofcom.gov.cn/gnmy/shrzgm.shtml"


@dataclass(frozen=True)
class OpenMarketAmounts:
    reverse_repo_billion: float = 0.0
    mlf_billion: float = 0.0
    net_injection_billion: float | None = None
    rate_down_bp: float = 0.0
    rate_up_bp: float = 0.0


class PBOCMacroIndicatorSource:
    """Load PBOC liquidity and credit indicators and convert changes into MacroEvents.

    Numeric indicators use public free endpoints wrapped by AKShare when available.
    Open-market operation details are parsed from the official PBOC announcement page.
    All upstream failures are treated as missing data so early-morning runs keep moving.
    """

    def __init__(
        self,
        as_of: datetime | None = None,
        lookback_days: int = 45,
        open_market_url: str = PBOC_OPEN_MARKET_URL,
        open_market_timeout: float = 8.0,
        open_market_limit: int = 4,
    ) -> None:
        self.as_of = as_of or datetime.now()
        self.lookback_days = lookback_days
        self.open_market_url = open_market_url
        self.open_market_timeout = open_market_timeout
        self.open_market_limit = open_market_limit

    def load(self) -> list[MacroEvent]:
        events: list[MacroEvent] = []
        events.extend(load_pboc_open_market_events(
            self.open_market_url,
            as_of=self.as_of,
            lookback_days=self.lookback_days,
            timeout=self.open_market_timeout,
            limit=self.open_market_limit,
        ))

        try:
            import akshare as ak
        except ImportError:
            return dedupe_events(events)

        events.extend(safe_source_call(lambda: build_lpr_events(ak.macro_china_lpr(), self.as_of, self.lookback_days)))
        events.extend(
            safe_source_call(
                lambda: build_reserve_requirement_events(
                    ak.macro_china_reserve_requirement_ratio(),
                    self.as_of,
                    self.lookback_days,
                )
            )
        )
        events.extend(
            safe_source_call(
                lambda: build_money_supply_events(
                    ak.macro_china_money_supply(),
                    self.as_of,
                    self.lookback_days,
                )
            )
        )
        events.extend(
            safe_source_call(
                lambda: build_credit_growth_events(
                    ak.macro_china_new_financial_credit(),
                    self.as_of,
                    self.lookback_days,
                    title_prefix="新增人民币贷款",
                    value_field="当月",
                    yoy_field="当月-同比增长",
                    source="akshare:macro_china_new_financial_credit",
                    url=EASTMONEY_CREDIT_URL,
                    confidence=0.62,
                )
            )
        )
        events.extend(
            safe_source_call(
                lambda: build_social_financing_events(
                    ak.macro_china_shrzgm(),
                    self.as_of,
                    self.lookback_days,
                )
            )
        )
        return dedupe_events(events)


def safe_source_call(loader) -> list[MacroEvent]:
    try:
        return loader()
    except Exception:
        return []


def load_pboc_open_market_events(
    url: str = PBOC_OPEN_MARKET_URL,
    as_of: datetime | None = None,
    lookback_days: int = 45,
    timeout: float = 8.0,
    limit: int = 4,
) -> list[MacroEvent]:
    active_as_of = as_of or datetime.now()
    try:
        html_text = fetch_text(url, timeout=timeout)
    except Exception:
        return []

    events: list[MacroEvent] = []
    recent_items = [
        item
        for item in parse_policy_page_items(html_text, url, as_of=active_as_of)
        if is_recent_date(item.get("published_at"), active_as_of, lookback_days)
        and is_open_market_title(item.get("title", ""))
    ][: max(limit, 0)]
    for item in recent_items:
        try:
            detail_html = fetch_text(str(item["link"]), timeout=timeout)
        except Exception:
            continue
        event = parse_open_market_detail(
            title=str(item.get("title") or ""),
            raw_html=detail_html,
            url=str(item.get("link") or ""),
            published_at=item.get("published_at") or active_as_of,
        )
        if event is not None:
            events.append(event)
    return events


def is_open_market_title(title: str) -> bool:
    text = str(title or "")
    return any(keyword in text for keyword in ("公开市场", "逆回购", "MLF", "中期借贷便利"))


def parse_open_market_detail(
    title: str,
    raw_html: str,
    url: str,
    published_at: datetime,
) -> MacroEvent | None:
    text = html_to_text(raw_html)
    amounts = parse_open_market_amounts(text)
    if not has_open_market_signal(amounts):
        return None

    stance = "pboc_open_market_operation"
    event_type = "pboc_open_market"
    positive = ("非银金融", "银行", "半导体", "AI硬件", "材料", "数字经济")
    negative = ("公用事业",)
    base_score = 4
    confidence = 0.64

    if amounts.rate_down_bp > 0:
        stance = "pboc_policy_rate_cut"
        event_type = "pboc_rate"
        base_score = 7 if amounts.rate_down_bp >= 10 else 6
        confidence = 0.72
    elif amounts.rate_up_bp > 0:
        stance = "pboc_policy_rate_hike"
        event_type = "pboc_rate"
        positive = ("银行", "公用事业")
        negative = ("非银金融", "半导体", "AI硬件", "材料")
        base_score = 7 if amounts.rate_up_bp >= 10 else 6
        confidence = 0.72
    elif amounts.net_injection_billion is not None:
        if amounts.net_injection_billion > 0:
            stance = "pboc_net_injection"
            base_score = 6 if amounts.net_injection_billion >= 1000 else 5
        elif amounts.net_injection_billion < 0:
            stance = "pboc_net_drain"
            positive = ("银行", "公用事业")
            negative = ("非银金融", "半导体", "AI硬件", "材料")
            base_score = 6 if abs(amounts.net_injection_billion) >= 1000 else 5
    elif amounts.mlf_billion > 0:
        stance = "pboc_mlf_operation"
        base_score = 5 if amounts.mlf_billion >= 1000 else 4
    elif amounts.reverse_repo_billion < 1000:
        return None

    return MacroEvent(
        event_date=published_at.replace(tzinfo=None),
        title=title.strip() or "人民银行公开市场操作",
        source="official:人民银行公开市场业务",
        event_type=event_type,
        stance=stance,
        positive_sectors=positive,
        negative_sectors=negative,
        base_score=base_score,
        decay_days=2,
        confidence=confidence,
        detail=format_open_market_detail(amounts),
        url=url,
    )


def parse_open_market_amounts(text: str) -> OpenMarketAmounts:
    reverse_repo = max_amount_before_or_after(text, "逆回购")
    mlf = max_amount_before_or_after(text, "MLF", "中期借贷便利")
    net_injection = parse_net_injection(text)
    rate_down = parse_rate_move_bp(text, ("下调", "下降", "降低"))
    rate_up = parse_rate_move_bp(text, ("上调", "上升", "提高"))
    return OpenMarketAmounts(
        reverse_repo_billion=reverse_repo,
        mlf_billion=mlf,
        net_injection_billion=net_injection,
        rate_down_bp=rate_down,
        rate_up_bp=rate_up,
    )


def has_open_market_signal(amounts: OpenMarketAmounts) -> bool:
    return any(
        (
            amounts.reverse_repo_billion > 0,
            amounts.mlf_billion > 0,
            amounts.net_injection_billion not in (None, 0),
            amounts.rate_down_bp > 0,
            amounts.rate_up_bp > 0,
        )
    )


def max_amount_before_or_after(text: str, *keywords: str) -> float:
    values: list[float] = []
    for keyword in keywords:
        escaped = re.escape(keyword)
        patterns = (
            rf"(\d+(?:\.\d+)?)\s*亿元[^。；;，,]{{0,30}}{escaped}",
            rf"{escaped}[^。；;，,]{{0,30}}?(\d+(?:\.\d+)?)\s*亿元",
        )
        for pattern in patterns:
            values.extend(float(match) for match in re.findall(pattern, text, re.I))
    return max(values) if values else 0.0


def parse_net_injection(text: str) -> float | None:
    positive_patterns = (
        r"净投放\s*(\d+(?:\.\d+)?)\s*亿元",
        r"实现净投放\s*(\d+(?:\.\d+)?)\s*亿元",
    )
    negative_patterns = (
        r"净回笼\s*(\d+(?:\.\d+)?)\s*亿元",
        r"实现净回笼\s*(\d+(?:\.\d+)?)\s*亿元",
    )
    for pattern in positive_patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    for pattern in negative_patterns:
        match = re.search(pattern, text)
        if match:
            return -float(match.group(1))
    return None


def parse_rate_move_bp(text: str, verbs: tuple[str, ...]) -> float:
    verb_group = "|".join(re.escape(item) for item in verbs)
    patterns = (
        rf"(?:利率|中标利率|操作利率)[^。；;，,]{{0,30}}(?:{verb_group})\s*(\d+(?:\.\d+)?)\s*个基点",
        rf"(?:{verb_group})\s*(\d+(?:\.\d+)?)\s*个基点[^。；;，,]{{0,30}}(?:利率|中标利率|操作利率)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return 0.0


def format_open_market_detail(amounts: OpenMarketAmounts) -> str:
    parts = []
    if amounts.reverse_repo_billion:
        parts.append(f"逆回购 {amounts.reverse_repo_billion:.0f}亿元")
    if amounts.mlf_billion:
        parts.append(f"MLF {amounts.mlf_billion:.0f}亿元")
    if amounts.net_injection_billion is not None:
        label = "净投放" if amounts.net_injection_billion >= 0 else "净回笼"
        parts.append(f"{label} {abs(amounts.net_injection_billion):.0f}亿元")
    if amounts.rate_down_bp:
        parts.append(f"利率下调 {amounts.rate_down_bp:.0f}bp")
    if amounts.rate_up_bp:
        parts.append(f"利率上调 {amounts.rate_up_bp:.0f}bp")
    return "；".join(parts)


def build_lpr_events(frame, as_of: datetime, lookback_days: int) -> list[MacroEvent]:
    pair = latest_pair(frame_records(frame), ("TRADE_DATE", "日期", "公布时间"), as_of, lookback_days)
    if pair is None:
        return []
    latest_date, latest, previous = pair
    changes = []
    for field, label in (("LPR1Y", "1年期LPR"), ("LPR5Y", "5年期以上LPR")):
        latest_value = to_optional_float(latest.get(field))
        previous_value = to_optional_float(previous.get(field)) if previous else None
        if latest_value is None or previous_value is None:
            continue
        delta_bp = round((latest_value - previous_value) * 100, 2)
        if abs(delta_bp) >= 0.5:
            changes.append((label, latest_value, previous_value, delta_bp))
    if not changes:
        return []

    easing = any(delta < 0 for _, _, _, delta in changes)
    max_abs_bp = max(abs(delta) for _, _, _, delta in changes)
    detail = "；".join(
        f"{label} {previous_value:.2f}%->{latest_value:.2f}% ({delta:+.0f}bp)"
        for label, latest_value, previous_value, delta in changes
    )
    return [
        MacroEvent(
            event_date=latest_date,
            title="LPR报价调整",
            source="akshare:macro_china_lpr",
            event_type="pboc_rate",
            stance="pboc_lpr_cut" if easing else "pboc_lpr_hike",
            positive_sectors=(
                ("非银金融", "半导体", "AI硬件", "材料", "数字经济")
                if easing
                else ("银行", "公用事业")
            ),
            negative_sectors=(("银行",) if easing else ("非银金融", "半导体", "AI硬件", "材料")),
            base_score=7 if max_abs_bp >= 10 else 5,
            decay_days=2,
            confidence=0.75,
            detail=detail,
            url=EASTMONEY_LPR_URL,
        )
    ]


def build_reserve_requirement_events(frame, as_of: datetime, lookback_days: int) -> list[MacroEvent]:
    pair = latest_pair(frame_records(frame), ("公布时间", "PUBLISH_DATE", "生效时间"), as_of, lookback_days)
    if pair is None:
        return []
    latest_date, latest, _ = pair
    large_delta = to_optional_float(latest.get("大型金融机构-调整幅度"))
    small_delta = to_optional_float(latest.get("中小金融机构-调整幅度"))
    deltas = [value for value in (large_delta, small_delta) if value is not None and abs(value) >= 0.01]
    if not deltas:
        return []

    easing = any(delta < 0 for delta in deltas)
    max_abs_bp = max(abs(delta) * 100 for delta in deltas)
    detail = (
        f"大型机构调整 {format_pct_point(large_delta)}；"
        f"中小机构调整 {format_pct_point(small_delta)}"
    )
    return [
        MacroEvent(
            event_date=latest_date,
            title="存款准备金率调整",
            source="akshare:macro_china_reserve_requirement_ratio",
            event_type="pboc_rrr",
            stance="pboc_rrr_cut" if easing else "pboc_rrr_hike",
            positive_sectors=(
                ("非银金融", "银行", "半导体", "AI硬件", "材料", "数字经济")
                if easing
                else ("银行", "公用事业")
            ),
            negative_sectors=(("公用事业",) if easing else ("非银金融", "半导体", "AI硬件", "材料")),
            base_score=8 if max_abs_bp >= 50 else 6,
            decay_days=3,
            confidence=0.78,
            detail=detail,
            url=EASTMONEY_RRR_URL,
        )
    ]


def build_money_supply_events(frame, as_of: datetime, lookback_days: int) -> list[MacroEvent]:
    pair = latest_pair(frame_records(frame), ("月份", "REPORT_DATE", "TIME"), as_of, lookback_days)
    if pair is None:
        return []
    latest_date, latest, previous = pair
    latest_yoy = to_optional_float(latest.get("货币和准货币(M2)-同比增长"))
    previous_yoy = to_optional_float(previous.get("货币和准货币(M2)-同比增长")) if previous else None
    if latest_yoy is None or previous_yoy is None:
        return []
    delta = latest_yoy - previous_yoy
    if abs(delta) < 0.3:
        return []

    easing = delta > 0
    return [
        MacroEvent(
            event_date=latest_date,
            title="M2同比增速变化",
            source="akshare:macro_china_money_supply",
            event_type="pboc_credit",
            stance="m2_growth_reaccelerates" if easing else "m2_growth_slows",
            positive_sectors=(
                ("非银金融", "银行", "半导体", "AI硬件", "材料", "数字经济")
                if easing
                else ("银行", "公用事业")
            ),
            negative_sectors=(("公用事业",) if easing else ("非银金融", "半导体", "AI硬件", "材料")),
            base_score=5 if abs(delta) < 0.8 else 6,
            decay_days=3,
            confidence=0.58,
            detail=f"M2同比 {previous_yoy:.2f}%->{latest_yoy:.2f}% ({delta:+.2f}pct)",
            url=EASTMONEY_MONEY_SUPPLY_URL,
        )
    ]


def build_credit_growth_events(
    frame,
    as_of: datetime,
    lookback_days: int,
    title_prefix: str,
    value_field: str,
    yoy_field: str,
    source: str,
    url: str,
    confidence: float,
) -> list[MacroEvent]:
    pair = latest_pair(frame_records(frame), ("月份", "REPORT_DATE", "TIME"), as_of, lookback_days)
    if pair is None:
        return []
    latest_date, latest, previous = pair
    latest_yoy = to_optional_float(latest.get(yoy_field))
    previous_yoy = to_optional_float(previous.get(yoy_field)) if previous else None
    if latest_yoy is None:
        return []
    if abs(latest_yoy) < 10:
        return []

    easing = latest_yoy > 0
    detail_parts = [f"同比 {latest_yoy:+.2f}%"]
    latest_value = to_optional_float(latest.get(value_field))
    if latest_value is not None:
        detail_parts.insert(0, f"当月 {latest_value:.0f}亿元")
    if previous_yoy is not None:
        detail_parts.append(f"前值 {previous_yoy:+.2f}%")
    return [
        MacroEvent(
            event_date=latest_date,
            title=f"{title_prefix}同比{'扩张' if easing else '收缩'}",
            source=source,
            event_type="pboc_credit",
            stance="credit_growth_expands" if easing else "credit_growth_contracts",
            positive_sectors=(
                ("非银金融", "银行", "半导体", "AI硬件", "材料", "数字经济")
                if easing
                else ("银行", "公用事业")
            ),
            negative_sectors=(("公用事业",) if easing else ("非银金融", "半导体", "AI硬件", "材料")),
            base_score=6 if abs(latest_yoy) >= 20 else 5,
            decay_days=3,
            confidence=confidence,
            detail="；".join(detail_parts),
            url=url,
        )
    ]


def build_social_financing_events(frame, as_of: datetime, lookback_days: int) -> list[MacroEvent]:
    pair = latest_pair(frame_records(frame), ("月份", "REPORT_DATE", "TIME"), as_of, lookback_days)
    if pair is None:
        return []
    latest_date, latest, previous = pair
    latest_value = to_optional_float(latest.get("社会融资规模增量"))
    previous_value = to_optional_float(previous.get("社会融资规模增量")) if previous else None
    if latest_value is None or previous_value is None or previous_value <= 0:
        return []
    ratio = latest_value / previous_value
    if 0.75 < ratio < 1.25:
        return []

    easing = ratio >= 1.25
    return [
        MacroEvent(
            event_date=latest_date,
            title=f"社会融资规模增量{'放大' if easing else '回落'}",
            source="akshare:macro_china_shrzgm",
            event_type="pboc_credit",
            stance="social_financing_expands" if easing else "social_financing_slows",
            positive_sectors=(
                ("非银金融", "银行", "基建", "工程机械", "半导体", "AI硬件")
                if easing
                else ("银行", "公用事业")
            ),
            negative_sectors=(("公用事业",) if easing else ("非银金融", "半导体", "AI硬件", "材料")),
            base_score=5,
            decay_days=3,
            confidence=0.52,
            detail=(
                f"社融增量 {previous_value:.0f}亿元->{latest_value:.0f}亿元 "
                f"({ratio - 1:+.1%})；月度环比有季节性，仅作低置信度信号"
            ),
            url=MOFCOM_SOCIAL_FINANCING_URL,
        )
    ]


def frame_records(frame) -> list[dict]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        return [dict(item) for item in frame.to_dict(orient="records")]
    return [dict(item) for item in frame]


def latest_pair(
    records: list[dict],
    date_fields: Iterable[str],
    as_of: datetime,
    lookback_days: int,
) -> tuple[datetime, dict, dict | None] | None:
    rows = []
    for record in records:
        record_date = first_parsed_date(record, date_fields)
        if record_date is None or record_date > as_of:
            continue
        rows.append((record_date, record))
    if not rows:
        return None
    rows.sort(key=lambda item: item[0])
    latest_date, latest = rows[-1]
    if not is_recent_date(latest_date, as_of, lookback_days):
        return None
    previous = rows[-2][1] if len(rows) >= 2 else None
    return latest_date, latest, previous


def first_parsed_date(record: dict, fields: Iterable[str]) -> datetime | None:
    for field in fields:
        if field in record:
            parsed = parse_date_value(record.get(field))
            if parsed is not None:
                return parsed
    return None


def parse_date_value(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    text = text.replace("年", "-").replace("月份", "").replace("月", "")
    text = text.replace("/", "-").replace(".", "-")
    text = re.sub(r"\s+", "", text)
    if re.fullmatch(r"\d{6}", text):
        text = f"{text[:4]}-{text[4:6]}-01"
    elif re.fullmatch(r"\d{4}-\d{1,2}", text):
        year, month = text.split("-", 1)
        text = f"{year}-{int(month):02d}-01"
    elif re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    for fmt in ("%Y-%m-%d", "%Y-%m-%d%H:%M:%S", "%Y-%m-%d%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    match = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", text)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def is_recent_date(value, as_of: datetime, lookback_days: int) -> bool:
    if not isinstance(value, datetime):
        return False
    if value > as_of:
        return False
    return (as_of - value).total_seconds() / 86400 <= lookback_days


def to_optional_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        parsed = float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def format_pct_point(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2f}pct"


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw_html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split())
