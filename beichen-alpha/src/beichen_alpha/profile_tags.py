from __future__ import annotations

from typing import Iterable


STYLE_TAGS = {
    "高股息",
    "防御",
    "能源安全",
    "金融稳定",
    "现金流",
    "全球竞争",
    "顺周期",
    "避险",
    "品牌消费",
}

CONCEPT_TAGS = {
    "先进制造",
    "高端制造",
    "新材料",
    "数字经济",
    "AI硬件",
    "新能源",
    "国产替代",
    "机器人",
    "低空经济",
    "军工",
    "创新药",
    "医药",
    "工业母机",
    "宽基指数",
}

SECONDARY_INDUSTRY_TAGS = {
    "资源",
    "黄金",
    "铜",
    "铝",
    "钼",
    "锂",
    "钴",
    "钨",
    "稀土",
    "铅锌",
    "氟化工",
    "氟资源",
    "新能源电池",
    "光模块",
    "电子",
    "通信",
    "农业",
}


def dedupe_tags(values: Iterable[str]) -> tuple[str, ...]:
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return tuple(result)


def classify_legacy_tags(industry: str, themes: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    secondary = []
    style = []
    concepts = []
    industry_text = str(industry or "").strip()
    for tag in dedupe_tags(themes):
        if tag == industry_text:
            continue
        if tag in STYLE_TAGS:
            style.append(tag)
        elif tag in CONCEPT_TAGS:
            concepts.append(tag)
        elif tag in SECONDARY_INDUSTRY_TAGS:
            secondary.append(tag)
        else:
            concepts.append(tag)
    return dedupe_tags(secondary), dedupe_tags(style), dedupe_tags(concepts)


def profile_primary_industry(profile) -> str:
    return str(getattr(profile, "primary_industry", "") or getattr(profile, "industry", "") or "").strip()


def profile_secondary_industries(profile) -> tuple[str, ...]:
    explicit = tuple(getattr(profile, "secondary_industries", ()) or ())
    if explicit:
        return dedupe_tags(explicit)
    inferred, _, _ = classify_legacy_tags(getattr(profile, "industry", ""), getattr(profile, "themes", ()) or ())
    return inferred


def profile_style_tags(profile) -> tuple[str, ...]:
    explicit = tuple(getattr(profile, "style_tags", ()) or ())
    if explicit:
        return dedupe_tags(explicit)
    _, inferred, _ = classify_legacy_tags(getattr(profile, "industry", ""), getattr(profile, "themes", ()) or ())
    return inferred


def profile_concept_tags(profile) -> tuple[str, ...]:
    explicit = tuple(getattr(profile, "concept_tags", ()) or ())
    if explicit:
        return dedupe_tags(explicit)
    _, _, inferred = classify_legacy_tags(getattr(profile, "industry", ""), getattr(profile, "themes", ()) or ())
    return inferred


def profile_industry_candidates(profile) -> tuple[str, ...]:
    return dedupe_tags(
        (
            profile_primary_industry(profile),
            getattr(profile, "industry", ""),
            *profile_secondary_industries(profile),
            *profile_concept_tags(profile),
        )
    )


def profile_all_tags(profile) -> tuple[str, ...]:
    return dedupe_tags(
        (
            profile_primary_industry(profile),
            getattr(profile, "industry", ""),
            *profile_secondary_industries(profile),
            *profile_style_tags(profile),
            *profile_concept_tags(profile),
            *(getattr(profile, "themes", ()) or ()),
        )
    )
