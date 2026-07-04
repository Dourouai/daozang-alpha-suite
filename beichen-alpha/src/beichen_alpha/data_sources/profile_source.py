from __future__ import annotations

import csv
import urllib.request
from pathlib import Path
from typing import Iterable

from beichen_alpha.models import StockProfile
from beichen_alpha.profile_tags import classify_legacy_tags

from .akshare_source import normalize_symbol, stock_market_symbol


def load_profile_csv(path: str | Path) -> dict[str, StockProfile]:
    rows: dict[str, StockProfile] = {}
    with Path(path).open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            code = normalize_symbol(row["code"])
            rows[code] = profile_from_row(code, row)
    return rows


def fetch_tencent_profiles(symbols: Iterable[str]) -> dict[str, StockProfile]:
    normalized = [normalize_symbol(symbol) for symbol in symbols if symbol.strip()]
    if not normalized:
        return {}

    profiles: dict[str, StockProfile] = {}
    for chunk in chunks(normalized, 80):
        profiles.update(fetch_tencent_profile_chunk(chunk))
    return profiles


def fetch_tencent_profile_chunk(symbols: list[str]) -> dict[str, StockProfile]:
    market_symbols = [stock_market_symbol(symbol) for symbol in symbols]
    url = "http://qt.gtimg.cn/q=" + ",".join(market_symbols)
    try:
        payload = urllib.request.urlopen(url, timeout=10).read().decode("gbk", errors="replace")
    except OSError:
        return {}

    profiles: dict[str, StockProfile] = {}
    for record in payload.split(";"):
        record = record.strip()
        if not record or '="' not in record:
            continue
        _, raw = record.split('="', 1)
        fields = raw.rstrip('"').split("~")
        if len(fields) < 46:
            continue
        code = normalize_symbol(fields[2])
        profiles[code] = StockProfile(
            code=code,
            name=fields[1] or code,
            market_cap_billion=to_optional_float(fields[45]),
        )
    return profiles


def chunks(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def merge_profiles(*profile_maps: dict[str, StockProfile]) -> dict[str, StockProfile]:
    merged: dict[str, StockProfile] = {}
    for profile_map in profile_maps:
        for code, profile in profile_map.items():
            old = merged.get(code)
            if old is None:
                merged[code] = profile
                continue
            merged[code] = StockProfile(
                code=code,
                name=profile.name if profile.name and profile.name != code else old.name,
                industry=profile.industry or old.industry,
                themes=profile.themes or old.themes,
                market_cap_billion=(
                    profile.market_cap_billion
                    if profile.market_cap_billion is not None
                    else old.market_cap_billion
                ),
                primary_industry=profile.primary_industry or old.primary_industry,
                secondary_industries=profile.secondary_industries or old.secondary_industries,
                style_tags=profile.style_tags or old.style_tags,
                concept_tags=profile.concept_tags or old.concept_tags,
            )
    return merged


def profile_from_row(code: str, row: dict) -> StockProfile:
    industry = str(row.get("industry") or "").strip()
    themes = tuple(split_themes(row.get("themes", "")))
    inferred_secondary, inferred_style, inferred_concepts = classify_legacy_tags(industry, themes)
    secondary = tuple(split_themes(row.get("secondary_industries") or row.get("secondary_industry") or ""))
    style_tags = tuple(split_themes(row.get("style_tags") or row.get("style") or ""))
    concept_tags = tuple(split_themes(row.get("concept_tags") or row.get("concepts") or ""))
    return StockProfile(
        code=code,
        name=row.get("name", code),
        industry=industry,
        themes=themes,
        market_cap_billion=to_optional_float(row.get("market_cap_billion")),
        primary_industry=str(row.get("primary_industry") or row.get("main_industry") or "").strip(),
        secondary_industries=secondary or inferred_secondary,
        style_tags=style_tags or inferred_style,
        concept_tags=concept_tags or inferred_concepts,
    )


def split_themes(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").replace("；", ";").split(";") if item.strip()]


def to_optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
