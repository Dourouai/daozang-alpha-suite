from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean

from beichen_alpha.models import Bar, FactorScore, MacroEvent, NewsEvent, StockProfile
from beichen_alpha.profile_tags import profile_all_tags

from .indicators import pct_return
from .macro_event_factor import macro_time_decay, normalized_profile_sectors
from .news_factor import event_time_decay


@dataclass(frozen=True)
class ExpectationSignal:
    strength: float
    source_count: int
    landed: bool
    titles: tuple[str, ...]


def score_expectation_pricing(
    bars: list[Bar],
    benchmark_bars: list[Bar],
    profile: StockProfile | None = None,
    news_events: list[NewsEvent] | None = None,
    macro_events: list[MacroEvent] | None = None,
    as_of: datetime | None = None,
) -> list[FactorScore]:
    """Score whether a positive expectation has already been priced in.

    This factor answers: "Is the good news still a setup, or has the market
    already bought it?" It intentionally works as a modifier on top of news
    and policy factors instead of replacing them.
    """

    if len(bars) < 6 or len(benchmark_bars) < 6:
        return [FactorScore("预期定价", 0, True, "样本不足，暂不判断预期是否透支")]

    signal = build_expectation_signal(profile, news_events or [], macro_events or [], as_of)
    if signal.strength <= 0:
        return [FactorScore("预期定价", 0, True, "暂无可量化的正向预期事件")]

    latest = bars[-1]
    stock_return_3d = pct_return([bar.close for bar in bars], 3)
    stock_return_5d = pct_return([bar.close for bar in bars], 5)
    stock_return_10d = pct_return([bar.close for bar in bars], 10)
    benchmark_return_3d = pct_return([bar.close for bar in benchmark_bars], 3)
    benchmark_return_5d = pct_return([bar.close for bar in benchmark_bars], 5)
    excess_3d = stock_return_3d - benchmark_return_3d
    excess_5d = stock_return_5d - benchmark_return_5d
    amount_ratio = calc_amount_ratio(bars)

    strong_runup = stock_return_5d >= 0.075 or excess_5d >= 0.055 or stock_return_3d >= 0.05 or excess_3d >= 0.038
    moderate_runup = stock_return_5d >= 0.035 or excess_5d >= 0.025 or stock_return_3d >= 0.025
    volume_hot = amount_ratio >= 1.25
    crowded = signal.source_count >= 2 or signal.strength >= 10
    very_crowded = signal.source_count >= 3 or signal.strength >= 14

    detail_base = (
        f"预期源{signal.source_count}个；3日{stock_return_3d:+.2%}/超额{excess_3d:+.2%}，"
        f"5日{stock_return_5d:+.2%}/超额{excess_5d:+.2%}，"
        f"10日{stock_return_10d:+.2%}，量能{amount_ratio:.2f}x"
    )
    event_text = "；".join(signal.titles[:2])
    detail = f"{detail_base}；{event_text}" if event_text else detail_base

    if strong_runup and (volume_hot or crowded):
        if signal.landed:
            return [FactorScore("利好兑现", -30, False, f"利好落地前价格已提前反映，警惕卖事实；{detail}")]
        penalty = -26 if very_crowded or volume_hot else -20
        return [FactorScore("预期透支", penalty, False, f"利好尚在预期中但价格已提前发酵，追入赔率下降；{detail}")]

    if moderate_runup and (volume_hot or crowded):
        return [FactorScore("预期发酵", 2, True, f"预期正在发酵，允许观察但不能追高；{detail}")]

    if stock_return_5d <= 0.03 and excess_5d <= 0.025 and amount_ratio <= 1.25:
        return [FactorScore("预期潜伏", 8, True, f"有正向预期但价格尚未充分反映；{detail}")]

    return [FactorScore("预期发酵", 4, True, f"预期开始反映，继续等待价格确认；{detail}")]


def build_expectation_signal(
    profile: StockProfile | None,
    news_events: list[NewsEvent],
    macro_events: list[MacroEvent],
    as_of: datetime | None,
) -> ExpectationSignal:
    strength = 0.0
    source_count = 0
    landed = False
    titles: list[str] = []

    for event in news_events:
        if event.polarity <= 0 or event.hard_exclude:
            continue
        decay = event_time_decay(event, as_of)
        contribution = event.polarity * event.importance * event.confidence * decay * 10
        if contribution <= 0.5:
            continue
        strength += contribution
        source_count += 1
        titles.append(event.title)
        landed = landed or is_news_landed(event, as_of)

    profile_sectors = normalized_profile_sectors(profile) if profile is not None else set()
    fallback_tokens = profile_tokens(profile)
    for event in macro_events:
        if not macro_event_matches_profile(event, profile_sectors, fallback_tokens):
            continue
        decay = macro_time_decay(event, as_of)
        contribution = abs(event.base_score or 6) * event.confidence * decay
        if contribution <= 0.5:
            continue
        strength += contribution
        source_count += 1
        titles.append(event.title)
        landed = landed or is_macro_landed(event, as_of)

    return ExpectationSignal(
        strength=strength,
        source_count=source_count,
        landed=landed,
        titles=tuple(dict.fromkeys(titles)),
    )


def calc_amount_ratio(bars: list[Bar]) -> float:
    if len(bars) < 2:
        return 1.0
    previous_amounts = [bar.amount for bar in bars[-6:-1] if bar.amount > 0]
    if not previous_amounts:
        return 1.0
    base = mean(previous_amounts)
    if base <= 0:
        return 1.0
    return bars[-1].amount / base


def macro_event_matches_profile(
    event: MacroEvent,
    profile_sectors: set[str],
    fallback_tokens: set[str],
) -> bool:
    positive = {str(item).strip() for item in event.positive_sectors if str(item).strip()}
    negative = {str(item).strip() for item in event.negative_sectors if str(item).strip()}
    if negative and (negative.intersection(profile_sectors) or text_overlaps(negative, fallback_tokens)):
        return False
    if positive:
        return bool(positive.intersection(profile_sectors) or text_overlaps(positive, fallback_tokens))
    return False


def profile_tokens(profile: StockProfile | None) -> set[str]:
    if profile is None:
        return set()
    tokens = {profile.industry, profile.primary_industry, profile.name}
    tokens.update(profile_all_tags(profile))
    return {str(item).strip().lower() for item in tokens if str(item).strip()}


def text_overlaps(left: set[str], right: set[str]) -> bool:
    for item in left:
        low = item.lower()
        if any(low in token or token in low for token in right):
            return True
    return False


def is_news_landed(event: NewsEvent, as_of: datetime | None) -> bool:
    if is_speculative_text(event.title):
        return False
    if as_of is None or event.published_at is None:
        return True
    age_hours = (as_of - event.published_at).total_seconds() / 3600
    return 0 <= age_hours <= 36


def is_macro_landed(event: MacroEvent, as_of: datetime | None) -> bool:
    if is_speculative_text(f"{event.title} {event.detail}"):
        return False
    if as_of is None:
        return True
    age_hours = (as_of - event.event_date).total_seconds() / 3600
    return 0 <= age_hours <= 36


def is_speculative_text(text: str) -> bool:
    return any(key in str(text or "") for key in ("预期", "有望", "或将", "传闻", "预计", "拟", "可能"))
