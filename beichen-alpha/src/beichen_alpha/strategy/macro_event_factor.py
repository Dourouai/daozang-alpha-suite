from __future__ import annotations

from datetime import datetime

from beichen_alpha.data_sources.sector_rotation_source import normalize_sector_name
from beichen_alpha.models import FactorScore, MacroEvent, StockProfile
from beichen_alpha.profile_tags import profile_industry_candidates, profile_primary_industry


def score_macro_events(
    profile: StockProfile | None,
    events: list[MacroEvent] | None,
    as_of: datetime | None = None,
) -> list[FactorScore]:
    if profile is None:
        return [FactorScore("宏观事件", 0, True, "缺少股票画像，宏观事件按中性处理")]
    if not events:
        return [FactorScore("宏观事件", 0, True, "暂无有效宏观事件")]

    profile_sectors = normalized_profile_sectors(profile)
    primary = profile_primary_industry(profile)
    primary_sector = normalize_sector_name(primary) or normalize_profile_fallback(primary)
    if not profile_sectors:
        return [FactorScore("宏观事件", 0, True, "未匹配到行业，宏观事件按中性处理")]

    contributions: list[tuple[int, str]] = []
    for event in events:
        decay = macro_time_decay(event, as_of)
        if decay <= 0:
            continue
        base = abs(event.base_score or 6)
        event_score = int(round(base * event.confidence * decay))
        if event_score == 0:
            continue

        contribution = resolve_event_contribution(event, event_score, profile_sectors, primary_sector)
        if contribution is not None:
            contributions.append(contribution)

    if not contributions:
        return [FactorScore("宏观事件", 0, True, "宏观事件未映射到该行业")]

    total = max(min(sum(score for score, _ in contributions), 16), -16)
    details = "；".join(detail for _, detail in sorted(contributions, key=lambda item: abs(item[0]), reverse=True)[:3])
    return [FactorScore("宏观事件", total, total >= 0, details)]


def normalized_profile_sectors(profile: StockProfile) -> set[str]:
    sectors = set()
    for item in profile_industry_candidates(profile):
        normalized = normalize_sector_name(item) or normalize_profile_fallback(item)
        if normalized:
            sectors.add(normalized)
    return sectors


def normalize_profile_fallback(text: str) -> str:
    value = str(text or "").strip()
    if value in {"黄金", "贵金属"}:
        return "黄金"
    if value in {"有色", "有色金属", "工业金属", "小金属", "铜", "铝", "钼", "锂", "钴", "钨", "稀土"}:
        return "工业金属"
    if value in {"资源", "煤炭石化"}:
        return "资源"
    if value in {"先进制造", "高端制造", "设备更新", "工业母机"}:
        return "先进制造"
    if value in {"基建", "建筑", "建筑工程", "工程机械", "水利", "轨交"}:
        return "基建"
    if value in {"房地产", "地产", "地产链", "建材", "家居"}:
        return "房地产"
    if value in {"家电", "汽车", "消费", "平台经济", "互联网", "中小企业"}:
        return "消费"
    if value in {"能源", "油服", "油气"}:
        return "石油石化"
    if value in {"化工下游"}:
        return "化工"
    if value in {"科技", "成长", "AI", "算力", "CPO", "光模块"}:
        return "AI硬件"
    if value in {"创新药", "医疗"}:
        return "医药"
    if value in {"电力", "水电", "红利", "防御"}:
        return "公用事业"
    if value in {"军工", "国防", "航天", "商业航天"}:
        return "军工"
    if value in {"机器人", "人形机器人", "工业机器人"}:
        return "机器人"
    if value in {"国产替代", "自主可控", "信创"}:
        return "国产替代"
    if value in {"出口链", "外需", "出口"}:
        return "出口链"
    if value in {"低空经济", "无人机"}:
        return "低空经济"
    if value in {"固态电池", "储能"}:
        return "新能源"
    return value


def resolve_event_contribution(
    event: MacroEvent,
    event_score: int,
    profile_sectors: set[str],
    primary_sector: str,
) -> tuple[int, str] | None:
    positive_matches = profile_sectors.intersection(event.positive_sectors)
    negative_matches = profile_sectors.intersection(event.negative_sectors)
    if not positive_matches and not negative_matches:
        return None
    if positive_matches and not negative_matches:
        return event_score, format_event_detail(event, positive_matches, event_score)
    if negative_matches and not positive_matches:
        return -event_score, format_event_detail(event, negative_matches, -event_score)

    positive_strength = len(positive_matches) + (2 if primary_sector in positive_matches else 0)
    negative_strength = len(negative_matches) + (2 if primary_sector in negative_matches else 0)
    if positive_strength > negative_strength:
        return event_score, format_event_detail(event, positive_matches, event_score)
    if negative_strength > positive_strength:
        return -event_score, format_event_detail(event, negative_matches, -event_score)
    return 0, f"{event.title} {event.stance} 多空主题同时命中，按中性处理"


def macro_time_decay(event: MacroEvent, as_of: datetime | None) -> float:
    if as_of is None:
        return 1.0
    age_days = max((as_of - event.event_date).total_seconds() / 86400, 0.0)
    if age_days <= 0.5:
        return 1.0
    if age_days >= event.decay_days:
        return 0.0
    return max(0.0, 1 - (age_days - 0.5) / max(event.decay_days - 0.5, 0.1))


def format_event_detail(event: MacroEvent, sectors: set[str], score: int) -> str:
    sector_text = "/".join(sorted(sectors))
    stance = f"{event.stance} " if event.stance else ""
    return f"{event.title} {stance}{sector_text}{score:+d}"
