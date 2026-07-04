from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Iterable

from beichen_alpha.models import MacroEvent

from .macro_rss_source import dedupe_events


STATS_NBS_DATA_URL = "https://www.stats.gov.cn/sj/"
EASTMONEY_PMI_URL = "https://data.eastmoney.com/cjsj/pmi.html"
EASTMONEY_CPI_URL = "https://data.eastmoney.com/cjsj/cpi.html"
EASTMONEY_PPI_URL = "https://data.eastmoney.com/cjsj/ppi.html"
EASTMONEY_RETAIL_URL = "https://data.eastmoney.com/cjsj/xfp.html"
EASTMONEY_FIXED_ASSET_URL = "https://data.eastmoney.com/cjsj/gdzctz.html"


class StatsMacroEventSource:
    """Convert China macro releases into low-confidence surprise MacroEvents.

    Some free endpoints include consensus forecasts; for official NBS series without
    forecasts we use month-to-month acceleration as a low-confidence proxy.
    """

    def __init__(self, as_of: datetime | None = None, lookback_days: int = 45) -> None:
        self.as_of = as_of or datetime.now()
        self.lookback_days = lookback_days

    def load(self) -> list[MacroEvent]:
        try:
            import akshare as ak
        except ImportError:
            return []

        events: list[MacroEvent] = []
        events.extend(safe_source_call(lambda: build_pmi_events(ak.macro_china_pmi(), self.as_of, self.lookback_days)))
        events.extend(
            safe_source_call(
                lambda: build_indicator_acceleration_events(
                    ak.macro_china_consumer_goods_retail(),
                    self.as_of,
                    self.lookback_days,
                    title="社零同比增速",
                    value_field="同比增长",
                    threshold=0.8,
                    source="akshare:macro_china_consumer_goods_retail",
                    url=EASTMONEY_RETAIL_URL,
                )
            )
        )
        events.extend(
            safe_source_call(
                lambda: build_indicator_acceleration_events(
                    ak.macro_china_gdzctz(),
                    self.as_of,
                    self.lookback_days,
                    title="固定资产投资同比增速",
                    value_field="同比增长",
                    threshold=0.8,
                    source="akshare:macro_china_gdzctz",
                    url=EASTMONEY_FIXED_ASSET_URL,
                )
            )
        )
        events.extend(
            safe_source_call(
                lambda: build_inflation_acceleration_events(
                    ak.macro_china_cpi(),
                    self.as_of,
                    self.lookback_days,
                    title="CPI同比",
                    value_field="全国-同比增长",
                    source="akshare:macro_china_cpi",
                    url=EASTMONEY_CPI_URL,
                )
            )
        )
        events.extend(
            safe_source_call(
                lambda: build_inflation_acceleration_events(
                    ak.macro_china_ppi(),
                    self.as_of,
                    self.lookback_days,
                    title="PPI同比",
                    value_field="当月同比增长",
                    source="akshare:macro_china_ppi",
                    url=EASTMONEY_PPI_URL,
                )
            )
        )
        events.extend(
            safe_source_call(
                lambda: build_consensus_surprise_events(
                    ak.macro_china_industrial_production_yoy(),
                    self.as_of,
                    self.lookback_days,
                    title="规模以上工业增加值同比",
                    threshold=0.8,
                    source="akshare:macro_china_industrial_production_yoy",
                    url=STATS_NBS_DATA_URL,
                )
            )
        )
        events.extend(
            safe_source_call(
                lambda: build_consensus_surprise_events(
                    ak.macro_china_exports_yoy(),
                    self.as_of,
                    self.lookback_days,
                    title="出口同比",
                    threshold=1.5,
                    source="akshare:macro_china_exports_yoy",
                    url=STATS_NBS_DATA_URL,
                )
            )
        )
        events.extend(
            safe_source_call(
                lambda: build_consensus_surprise_events(
                    ak.macro_china_imports_yoy(),
                    self.as_of,
                    self.lookback_days,
                    title="进口同比",
                    threshold=1.5,
                    source="akshare:macro_china_imports_yoy",
                    url=STATS_NBS_DATA_URL,
                )
            )
        )
        return dedupe_events(events)


def safe_source_call(loader) -> list[MacroEvent]:
    try:
        return loader()
    except Exception:
        return []


def build_consensus_surprise_events(
    frame,
    as_of: datetime,
    lookback_days: int,
    title: str,
    threshold: float,
    source: str,
    url: str,
) -> list[MacroEvent]:
    pair = latest_pair(frame_records(frame), ("日期", "月份", "REPORT_DATE", "TIME"), as_of, lookback_days)
    if pair is None:
        return []
    latest_date, latest, _ = pair
    actual = to_optional_float(latest.get("今值"))
    forecast = to_optional_float(latest.get("预测值"))
    previous = to_optional_float(latest.get("前值"))
    if actual is None or forecast is None:
        return []
    surprise = actual - forecast
    if abs(surprise) < threshold:
        return []
    detail = f"今值 {actual:.2f}，预测 {forecast:.2f}，差值 {surprise:+.2f}"
    if previous is not None:
        detail += f"，前值 {previous:.2f}"
    return [
        build_growth_event(
            latest_date,
            f"{title}{'好于预期' if surprise > 0 else '弱于预期'}",
            source=source,
            stance="china_growth_upside_surprise" if surprise > 0 else "china_growth_downside_surprise",
            easing=surprise > 0,
            base_score=6 if abs(surprise) >= threshold * 2 else 4,
            confidence=0.66,
            detail=detail,
            url=url,
        )
    ]


def build_pmi_events(frame, as_of: datetime, lookback_days: int) -> list[MacroEvent]:
    pair = latest_pair(frame_records(frame), ("月份", "REPORT_DATE", "TIME"), as_of, lookback_days)
    if pair is None:
        return []
    latest_date, latest, previous = pair
    events: list[MacroEvent] = []
    for field, label in (("制造业-指数", "制造业PMI"), ("非制造业-指数", "非制造业PMI")):
        latest_value = to_optional_float(latest.get(field))
        previous_value = to_optional_float(previous.get(field)) if previous else None
        if latest_value is None or previous_value is None:
            continue
        delta = latest_value - previous_value
        if abs(delta) < 0.3 and not crossed_pmi_line(latest_value, previous_value):
            continue
        easing = latest_value >= 50 and delta >= 0
        if latest_value < 50 and delta <= 0:
            easing = False
        elif delta > 0:
            easing = True
        detail = f"{label} {previous_value:.1f}->{latest_value:.1f} ({delta:+.1f})"
        events.append(
            build_growth_event(
                latest_date,
                f"{label}{'改善' if easing else '走弱'}",
                source="akshare:macro_china_pmi",
                stance="china_pmi_improves" if easing else "china_pmi_weakens",
                easing=easing,
                base_score=5 if abs(delta) >= 0.8 or crossed_pmi_line(latest_value, previous_value) else 4,
                confidence=0.56,
                detail=detail,
                url=EASTMONEY_PMI_URL,
            )
        )
    return events


def build_indicator_acceleration_events(
    frame,
    as_of: datetime,
    lookback_days: int,
    title: str,
    value_field: str,
    threshold: float,
    source: str,
    url: str,
) -> list[MacroEvent]:
    pair = latest_pair(frame_records(frame), ("月份", "REPORT_DATE", "TIME"), as_of, lookback_days)
    if pair is None:
        return []
    latest_date, latest, previous = pair
    latest_value = to_optional_float(latest.get(value_field))
    previous_value = to_optional_float(previous.get(value_field)) if previous else None
    if latest_value is None or previous_value is None:
        return []
    delta = latest_value - previous_value
    if abs(delta) < threshold:
        return []
    easing = delta > 0
    return [
        build_growth_event(
            latest_date,
            f"{title}{'加速' if easing else '放缓'}",
            source=source,
            stance="china_growth_accelerates" if easing else "china_growth_slows",
            easing=easing,
            base_score=5 if abs(delta) >= threshold * 1.5 else 4,
            confidence=0.55,
            detail=f"{title} {previous_value:.2f}%->{latest_value:.2f}% ({delta:+.2f}pct)",
            url=url,
        )
    ]


def build_inflation_acceleration_events(
    frame,
    as_of: datetime,
    lookback_days: int,
    title: str,
    value_field: str,
    source: str,
    url: str,
) -> list[MacroEvent]:
    pair = latest_pair(frame_records(frame), ("月份", "REPORT_DATE", "TIME"), as_of, lookback_days)
    if pair is None:
        return []
    latest_date, latest, previous = pair
    latest_value = to_optional_float(latest.get(value_field))
    previous_value = to_optional_float(previous.get(value_field)) if previous else None
    if latest_value is None or previous_value is None:
        return []
    delta = latest_value - previous_value
    if abs(delta) < 0.5:
        return []
    inflation_up = delta > 0
    return [
        MacroEvent(
            event_date=latest_date,
            title=f"{title}{'升温' if inflation_up else '降温'}",
            source=source,
            event_type="china_macro_surprise",
            stance="china_inflation_heats" if inflation_up else "china_inflation_cools",
            positive_sectors=(("银行", "资源", "煤炭") if inflation_up else ("非银金融", "半导体", "AI硬件", "医药")),
            negative_sectors=(("半导体", "AI硬件", "医药") if inflation_up else ("银行", "煤炭")),
            base_score=5,
            decay_days=2,
            confidence=0.54,
            detail=f"{title} {previous_value:.2f}%->{latest_value:.2f}% ({delta:+.2f}pct)",
            url=url,
        )
    ]


def build_growth_event(
    event_date: datetime,
    title: str,
    source: str,
    stance: str,
    easing: bool,
    base_score: int,
    confidence: float,
    detail: str,
    url: str,
) -> MacroEvent:
    return MacroEvent(
        event_date=event_date,
        title=title,
        source=source,
        event_type="china_macro_surprise",
        stance=stance,
        positive_sectors=(
            ("非银金融", "基建", "工程机械", "消费", "材料", "半导体", "AI硬件")
            if easing
            else ("银行", "公用事业", "高股息")
        ),
        negative_sectors=(
            ("公用事业",)
            if easing
            else ("非银金融", "半导体", "AI硬件", "材料", "消费")
        ),
        base_score=base_score,
        decay_days=2,
        confidence=confidence,
        detail=detail,
        url=url,
    )


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
    rows.sort(key=lambda item: item[0])
    if not rows:
        return None
    latest_date, latest = rows[-1]
    if (as_of - latest_date).total_seconds() / 86400 > lookback_days:
        return None
    previous = rows[-2][1] if len(rows) >= 2 else None
    return latest_date, latest, previous


def first_parsed_date(record: dict, fields: Iterable[str]) -> datetime | None:
    for field in fields:
        if field not in record:
            continue
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
    return None


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


def crossed_pmi_line(latest: float, previous: float) -> bool:
    return (latest >= 50 > previous) or (latest < 50 <= previous)
