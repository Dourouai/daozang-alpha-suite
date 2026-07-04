from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from beichen_alpha.models import MacroEvent

from .sector_rotation_source import normalize_sector_name


class CsvMacroEventSource:
    def __init__(
        self,
        path: str | Path = "config/macro_events.csv",
        as_of: datetime | None = None,
        lookback_days: int = 7,
    ) -> None:
        self.path = Path(path)
        self.as_of = as_of or datetime.now()
        self.lookback_days = lookback_days

    def load(self) -> list[MacroEvent]:
        events = load_macro_events_csv(self.path)
        return filter_active_events(events, self.as_of, self.lookback_days)


def load_macro_events_csv(path: str | Path) -> list[MacroEvent]:
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

    events: list[MacroEvent] = []
    reader = csv.DictReader(rows)
    for row in reader:
        if not parse_enabled(row.get("enabled", "true")):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        event_date = parse_event_date(str(row.get("date") or row.get("event_date") or "").strip())
        if event_date is None:
            continue
        events.append(
            MacroEvent(
                event_date=event_date,
                title=title,
                source=str(row.get("source") or "").strip() or "manual",
                event_type=str(row.get("event_type") or "macro").strip(),
                stance=str(row.get("stance") or "").strip(),
                positive_sectors=parse_sector_list(str(row.get("positive_sectors") or "")),
                negative_sectors=parse_sector_list(str(row.get("negative_sectors") or "")),
                base_score=parse_int(row.get("base_score"), default=6),
                decay_days=max(parse_int(row.get("decay_days"), default=2), 1),
                confidence=clamp_float(parse_float(row.get("confidence"), default=1.0), 0.0, 1.0),
                detail=str(row.get("detail") or "").strip(),
                url=str(row.get("url") or "").strip(),
            )
        )
    return events


def filter_active_events(events: list[MacroEvent], as_of: datetime, lookback_days: int) -> list[MacroEvent]:
    active: list[MacroEvent] = []
    for event in events:
        if event.event_date > as_of:
            continue
        age_days = (as_of - event.event_date).total_seconds() / 86400
        if age_days <= max(lookback_days, event.decay_days):
            active.append(event)
    return active


def parse_sector_list(raw: str) -> tuple[str, ...]:
    sectors: list[str] = []
    for item in re.split(r"[,，/|;；、]+", raw):
        text = item.strip()
        if not text:
            continue
        normalized = normalize_sector_name(text) or normalize_macro_sector(text)
        if normalized:
            sectors.append(normalized)
    return tuple(dict.fromkeys(sectors))


def normalize_macro_sector(text: str) -> str:
    if text in {"贵金属", "黄金"}:
        return "黄金"
    if text in {"有色", "有色金属", "工业金属", "小金属", "铜", "铝", "钼", "锂", "钴", "钨", "稀土"}:
        return "工业金属"
    if text in {"资源"}:
        return "资源"
    if text in {"先进制造", "高端制造", "设备更新", "工业母机"}:
        return "先进制造"
    if text in {"基建", "建筑", "建筑工程", "工程机械", "水利", "轨交"}:
        return "基建"
    if text in {"房地产", "地产", "地产链", "建材", "家居"}:
        return "房地产"
    if text in {"家电", "汽车", "消费", "平台经济", "互联网", "中小企业"}:
        return "消费"
    if text in {"能源", "油服", "油气"}:
        return "石油石化"
    if text in {"化工下游"}:
        return "化工"
    if text in {"创新药", "医药", "医疗"}:
        return "医药"
    if text in {"科技", "成长", "AI", "算力", "光模块", "CPO"}:
        return "AI硬件"
    if text in {"红利", "防御", "电力"}:
        return "公用事业"
    if text in {"高股息"}:
        return "公用事业"
    if text in {"军工", "国防", "航天", "商业航天"}:
        return "军工"
    if text in {"机器人", "人形机器人", "工业机器人"}:
        return "机器人"
    if text in {"国产替代", "自主可控", "信创"}:
        return "国产替代"
    if text in {"出口链", "外需", "出口"}:
        return "出口链"
    if text in {"低空经济", "无人机"}:
        return "低空经济"
    if text in {"固态电池", "储能"}:
        return "新能源"
    return text


def parse_event_date(raw: str) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt in ("%Y-%m-%d", "%Y%m%d"):
                return parsed
            return parsed
        except ValueError:
            pass
    return None


def parse_enabled(value: str) -> bool:
    return str(value or "").strip().lower() not in {"0", "false", "no", "n", "off", "否"}


def parse_int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def parse_float(value, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def clamp_float(value: float, low: float, high: float) -> float:
    return max(min(value, high), low)
