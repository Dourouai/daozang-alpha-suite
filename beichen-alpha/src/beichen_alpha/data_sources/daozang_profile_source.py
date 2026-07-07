from __future__ import annotations

import csv
from pathlib import Path

from beichen_alpha.models import StockProfile

from .akshare_source import normalize_symbol
from .profile_source import profile_from_row, split_themes


DEFAULT_ACTIVE_UNIVERSE_PATH = "../daozang-alpha/data/universe/active_universe.csv"
DEFAULT_INDUSTRY_MAP_PATH = "../daozang-alpha/data/universe/akshare_industry_map.csv"


def load_daozang_active_universe(path: str | Path = DEFAULT_ACTIVE_UNIVERSE_PATH) -> dict[str, StockProfile]:
    return load_daozang_profile_csv(path)


def load_daozang_industry_map(path: str | Path = DEFAULT_INDUSTRY_MAP_PATH) -> dict[str, StockProfile]:
    return load_daozang_profile_csv(path)


def load_daozang_profiles(
    active_universe_path: str | Path = DEFAULT_ACTIVE_UNIVERSE_PATH,
    industry_map_path: str | Path = DEFAULT_INDUSTRY_MAP_PATH,
) -> dict[str, StockProfile]:
    """Load Daozang universe/profile files into Beichen's StockProfile shape.

    The active universe has richer liquidity and market-cap fields, while the
    industry map can carry a cleaner industry label. We merge tags instead of
    letting one CSV flatten the other.
    """
    active_profiles = load_daozang_active_universe(active_universe_path)
    industry_profiles = load_daozang_industry_map(industry_map_path)
    return merge_daozang_profiles(active_profiles, industry_profiles)


def load_daozang_profile_csv(path: str | Path) -> dict[str, StockProfile]:
    profile_path = Path(path)
    if not str(path) or not profile_path.exists() or profile_path.is_dir():
        return {}

    profiles: dict[str, StockProfile] = {}
    with profile_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            code = normalize_symbol(str(row.get("code") or ""))
            if not code:
                continue
            profiles[code] = profile_from_row(code, row)
    return profiles


def merge_daozang_profiles(
    active_profiles: dict[str, StockProfile],
    industry_profiles: dict[str, StockProfile],
) -> dict[str, StockProfile]:
    result: dict[str, StockProfile] = {}
    for code in sorted(set(active_profiles) | set(industry_profiles)):
        active = active_profiles.get(code)
        industry = industry_profiles.get(code)
        if active is None:
            result[code] = industry  # type: ignore[assignment]
            continue
        if industry is None:
            result[code] = active
            continue

        result[code] = StockProfile(
            code=code,
            name=preferred_text(active.name, industry.name, code),
            industry=preferred_text(active.industry, industry.industry),
            themes=dedupe_tags((*industry.themes, *active.themes)),
            market_cap_billion=active.market_cap_billion if active.market_cap_billion is not None else industry.market_cap_billion,
            primary_industry=preferred_text(active.primary_industry, industry.primary_industry, active.industry, industry.industry),
            secondary_industries=dedupe_tags((*industry.secondary_industries, *active.secondary_industries)),
            style_tags=dedupe_tags((*industry.style_tags, *active.style_tags)),
            concept_tags=dedupe_tags((*industry.concept_tags, *active.concept_tags)),
        )
    return result


def preferred_text(*values: str) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def dedupe_tags(values) -> tuple[str, ...]:
    result: list[str] = []
    seen = set()
    for value in values:
        for item in split_themes(value) if isinstance(value, str) else (value,):
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
    return tuple(result)
