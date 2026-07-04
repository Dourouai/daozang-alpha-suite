from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ArticleContent:
    title: str
    author: str
    source_name: str
    url: str
    text: str
    published_at: datetime | None = None


@dataclass(frozen=True)
class Bar:
    code: str
    name: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float


@dataclass(frozen=True)
class NewsEvent:
    code: str
    title: str
    source: str
    url: str = ""
    published_at: datetime | None = None
    event_type: str = "neutral"
    polarity: int = 0
    importance: float = 0.0
    confidence: float = 0.0
    hard_exclude: bool = False


@dataclass(frozen=True)
class OpinionSignal:
    source_name: str
    source_author: str
    title: str
    url: str
    signal_date: datetime
    ingested_at: datetime
    published_at: datetime | None
    rule_version: str
    summary: str
    stance: str
    confidence: float
    themes: tuple[str, ...]
    symbols: tuple[str, ...]
    risk_flags: tuple[str, ...]
    key_points: tuple[str, ...]
    matched_rules: tuple[str, ...]


@dataclass(frozen=True)
class FactorScore:
    name: str
    score: int
    passed: bool
    detail: str


@dataclass(frozen=True)
class StockProfile:
    code: str
    name: str
    industry: str = ""
    themes: tuple[str, ...] = ()
    market_cap_billion: float | None = None
    primary_industry: str = ""
    secondary_industries: tuple[str, ...] = ()
    style_tags: tuple[str, ...] = ()
    concept_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketRegime:
    temperature: str
    score: int
    breadth: float | None = None
    limit_up_count: int | None = None
    limit_down_count: int | None = None
    turnover_billion: float | None = None
    index_trend: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SectorSignal:
    name: str
    score: int
    return_3d: float | None = None
    return_5d: float | None = None
    amount_ratio: float | None = None
    rank: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class MacroEvent:
    event_date: datetime
    title: str
    source: str
    event_type: str = "macro"
    stance: str = ""
    positive_sectors: tuple[str, ...] = ()
    negative_sectors: tuple[str, ...] = ()
    base_score: int = 0
    decay_days: int = 2
    confidence: float = 1.0
    detail: str = ""
    url: str = ""


@dataclass(frozen=True)
class GlobalIndicator:
    code: str
    name: str
    category: str
    source: str
    latest_date: str
    latest: float
    previous: float | None = None
    change: float | None = None
    change_pct: float | None = None
    unit: str = ""
    detail: str = ""


@dataclass(frozen=True)
class GlobalLinkageSnapshot:
    as_of: datetime
    indicators: tuple[GlobalIndicator, ...]
    posture: str
    score: int
    signals: tuple[str, ...]
    source_health: tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskCalendarEvent:
    code: str
    title: str
    source: str
    event_date: datetime | None = None
    event_type: str = "neutral"
    severity: float = 0.0
    hard_exclude: bool = False
    detail: str = ""
    url: str = ""


@dataclass(frozen=True)
class StrategyPolicy:
    cycle: str = "balanced"
    large_cap_only: bool = True
    min_market_cap_billion: float = 300.0
    excluded_themes: tuple[str, ...] = ()
    horizon: str = "ultra_short_2_3d"


@dataclass(frozen=True)
class Recommendation:
    code: str
    name: str
    score: int
    status: str
    close: float
    observation_zone: str
    confirm_price: float
    invalid_price: float
    reason: str
    risk: str
    industry: str = ""
    themes: tuple[str, ...] = ()
    market_cap_billion: float | None = None
    holding_period: str = "2-3交易日"
    take_profit_price: float | None = None
    trailing_stop_price: float | None = None
    sell_plan: str = ""
    market_temperature: str = ""
    sector_rotation: str = ""
    risk_calendar: str = ""
    candidate_score: int = 0
    candidate_breakdown: str = ""
    macro_event_score: int = 0
    macro_events: str = ""


@dataclass(frozen=True)
class RealtimeQuote:
    code: str
    name: str
    price: float
    open: float
    high: float
    low: float
    prev_close: float
    change_pct: float
    amount_billion: float | None = None
    volume_hand: float | None = None
    vwap_price: float | None = None
    quote_time: datetime | None = None
    source: str = "tencent"
    latency_ms: float | None = None
    stale: bool = False
    source_diff_pct: float | None = None
    warning: str = ""


@dataclass(frozen=True)
class RealtimeCheck:
    code: str
    status: str
    price: float | None
    gap_to_confirm_pct: float | None
    chase_limit_price: float
    quote_time: datetime | None = None
    amount_billion: float | None = None
    sector_confirmation: str = ""
    execution_score: int = 0
    execution_breakdown: str = ""
    detail: str = ""
