from __future__ import annotations

from datetime import datetime

from beichen_alpha.models import (
    Bar,
    MacroEvent,
    MarketRegime,
    NewsEvent,
    Recommendation,
    RiskCalendarEvent,
    SectorSignal,
    StockProfile,
    StrategyPolicy,
)
from beichen_alpha.profile_tags import profile_all_tags, profile_primary_industry

from .disclosure_factor import score_disclosure_events
from .factors import score_bars
from .levels import (
    calc_confirm_price,
    calc_invalid_price,
    calc_observation_zone,
    calc_take_profit_price,
    calc_trailing_stop_price,
)
from .macro_event_factor import score_macro_events
from .market_factor import match_sector_signal, score_chain_rotation, score_market_regime, score_sector_rotation
from .news_factor import score_news_events
from .policy import score_basic_quality, score_policy
from .risk_calendar_factor import score_risk_calendar_events, summarize_risk_calendar


def build_recommendation(
    code: str,
    bars: list[Bar],
    benchmark_bars: list[Bar],
    profile: StockProfile | None = None,
    policy: StrategyPolicy | None = None,
    news_events: list[NewsEvent] | None = None,
    disclosure_events: list[NewsEvent] | None = None,
    risk_calendar_events: list[RiskCalendarEvent] | None = None,
    macro_events: list[MacroEvent] | None = None,
    market_regime: MarketRegime | None = None,
    sector_signals: dict[str, SectorSignal] | None = None,
    as_of: datetime | None = None,
) -> Recommendation:
    latest = bars[-1]
    active_policy = policy or StrategyPolicy()
    factor_scores = [
        *score_policy(profile, active_policy),
        *score_basic_quality(profile),
        *score_market_regime(market_regime),
        *score_macro_events(profile, macro_events or [], as_of=as_of),
        *score_sector_rotation(profile, sector_signals),
        *score_chain_rotation(profile, sector_signals),
        *score_risk_calendar_events(risk_calendar_events or [], as_of=as_of),
        *score_disclosure_events(disclosure_events or [], as_of=as_of),
        *score_news_events(news_events or [], as_of=as_of),
        *score_bars(bars, benchmark_bars, active_policy),
    ]
    candidate_score, candidate_breakdown = build_candidate_score(factor_scores)
    score = candidate_score
    zone_low, zone_high = calc_observation_zone(bars)
    confirm_price = calc_confirm_price(bars)
    invalid_price = calc_invalid_price(bars)
    take_profit_price = calc_take_profit_price(
        bars,
        confirm_price,
        invalid_price,
        horizon=active_policy.horizon,
    )
    trailing_stop_price = calc_trailing_stop_price(
        bars,
        invalid_price,
        horizon=active_policy.horizon,
    )

    if latest.close < invalid_price:
        status = "失效"
    elif latest.close < zone_low:
        status = "等待"
    elif zone_low <= latest.close <= zone_high and latest.close >= confirm_price:
        status = "可执行"
    elif zone_low <= latest.close <= zone_high and is_near_confirm(latest.close, confirm_price):
        status = "条件执行"
    elif zone_low <= latest.close <= zone_high:
        status = "观察"
    elif latest.close <= zone_high * 1.03:
        status = "突破"
    else:
        status = "偏离"

    failed = [item.name for item in factor_scores if not item.passed]
    if any(item.name == "主题排除" and not item.passed for item in factor_scores):
        status = "排除"
    if any(item.name == "大盘过滤" and not item.passed for item in factor_scores):
        status = "排除"
    if any(item.name == "风险日历" and not item.passed and item.score <= -180 for item in factor_scores):
        status = "排除"
    if any(item.name == "公告风险" and not item.passed for item in factor_scores):
        status = "排除"
    if any(item.name == "新闻风险" and not item.passed for item in factor_scores):
        status = "排除"

    passed = [item.name for item in factor_scores if item.passed and item.score > 0]
    reason = "通过: " + "、".join(passed) if passed else "暂无强因子通过"
    risk = "注意: " + "、".join(failed) if failed else "核心因子均通过，仍需按失效线做T+1风控"
    holding_period = holding_period_text(active_policy)
    matched_sector = match_sector_signal(profile, sector_signals or {})
    macro_score, macro_detail = summarize_macro_factor(factor_scores)
    sell_plan = build_sell_plan(
        status,
        holding_period=holding_period,
        confirm_price=confirm_price,
        take_profit_price=take_profit_price,
        trailing_stop_price=trailing_stop_price,
        invalid_price=invalid_price,
    )

    return Recommendation(
        code=code,
        name=profile.name if profile and profile.name else latest.name,
        score=score,
        status=status,
        close=latest.close,
        observation_zone=f"{zone_low:.2f}-{zone_high:.2f}",
        confirm_price=confirm_price,
        invalid_price=invalid_price,
        reason=reason,
        risk=risk,
        industry=profile_primary_industry(profile) if profile else "",
        themes=profile_all_tags(profile) if profile else (),
        market_cap_billion=profile.market_cap_billion if profile else None,
        holding_period=holding_period,
        take_profit_price=take_profit_price,
        trailing_stop_price=trailing_stop_price,
        sell_plan=sell_plan,
        market_temperature=market_regime.temperature if market_regime else "-",
        sector_rotation=format_sector_rotation(matched_sector),
        risk_calendar=summarize_risk_calendar(risk_calendar_events or [], as_of=as_of),
        candidate_score=candidate_score,
        candidate_breakdown=candidate_breakdown,
        macro_event_score=macro_score,
        macro_events=macro_detail,
    )


def rank_recommendations(
    price_map: dict[str, list[Bar]],
    benchmark_code: str,
    profiles: dict[str, StockProfile] | None = None,
    policy: StrategyPolicy | None = None,
    news_events: dict[str, list[NewsEvent]] | None = None,
    disclosure_events: dict[str, list[NewsEvent]] | None = None,
    risk_calendar_events: dict[str, list[RiskCalendarEvent]] | None = None,
    macro_events: list[MacroEvent] | None = None,
    market_regime: MarketRegime | None = None,
    sector_signals: dict[str, SectorSignal] | None = None,
    as_of: datetime | None = None,
) -> list[Recommendation]:
    if benchmark_code not in price_map:
        raise ValueError(f"missing benchmark code: {benchmark_code}")

    benchmark_bars = price_map[benchmark_code]
    recommendations = [
        build_recommendation(
            code,
            bars,
            benchmark_bars,
            profile=(profiles or {}).get(code),
            policy=policy,
            news_events=(news_events or {}).get(code),
            disclosure_events=(disclosure_events or {}).get(code),
            risk_calendar_events=(risk_calendar_events or {}).get(code),
            macro_events=macro_events,
            market_regime=market_regime,
            sector_signals=sector_signals,
            as_of=as_of,
        )
        for code, bars in price_map.items()
        if code != benchmark_code
    ]

    status_rank = {
        "可执行": 0,
        "条件执行": 1,
        "观察": 2,
        "突破": 3,
        "等待": 4,
        "偏离": 5,
        "失效": 6,
        "排除": 7,
    }
    return sorted(recommendations, key=lambda item: (status_rank.get(item.status, 9), -item.score))


def holding_period_text(policy: StrategyPolicy) -> str:
    if policy.horizon == "ultra_short_2_3d":
        return "2-3交易日"
    if policy.horizon == "short_3_5d":
        return "3-5交易日"
    return "10-20交易日"


CANDIDATE_FACTOR_GROUPS = {
    "大盘过滤": "大盘环境",
    "市场温度": "大盘环境",
    "宏观事件": "宏观事件",
    "周期产业": "风格偏向",
    "行业轮动": "行业共振",
    "产业链传导": "行业共振",
    "趋势": "个股强弱",
    "相对强弱": "个股强弱",
    "回踩承接": "个股强弱",
    "风险距离": "个股强弱",
    "短线动量": "个股强弱",
    "短线过热": "个股强弱",
    "2-3日赔率": "个股强弱",
    "3-5日赔率": "个股强弱",
    "流动性": "流动性",
    "量能": "流动性",
    "新闻事件": "观点偏向",
    "公告事件": "观点偏向",
    "基本质量": "基本质量",
    "股票画像": "风险扣分",
    "主题排除": "风险扣分",
    "风险日历": "风险扣分",
    "公告风险": "风险扣分",
    "新闻风险": "风险扣分",
    "样本": "风险扣分",
}

CANDIDATE_GROUP_ORDER = (
    "大盘环境",
    "宏观事件",
    "风格偏向",
    "行业共振",
    "个股强弱",
    "流动性",
    "观点偏向",
    "基本质量",
    "风险扣分",
)


def build_candidate_score(factor_scores: list) -> tuple[int, str]:
    groups = {name: 0 for name in CANDIDATE_GROUP_ORDER}
    for item in factor_scores:
        group = CANDIDATE_FACTOR_GROUPS.get(item.name)
        if group is None:
            group = "风险扣分" if item.score < 0 else "个股强弱"
        groups[group] += item.score
    total = sum(groups.values())
    breakdown = " ".join(f"{name}{groups[name]:+d}" for name in CANDIDATE_GROUP_ORDER if groups[name])
    return total, breakdown or "候选因子暂无方向"


def summarize_macro_factor(factor_scores: list) -> tuple[int, str]:
    for item in factor_scores:
        if item.name == "宏观事件":
            return item.score, item.detail
    return 0, ""


def format_sector_rotation(signal: SectorSignal | None) -> str:
    if signal is None:
        return "-"
    return f"{signal.name}{signal.score:+d}"


def is_near_confirm(close: float, confirm_price: float) -> bool:
    if close <= 0 or confirm_price <= 0:
        return False
    return 0 <= confirm_price / close - 1 <= 0.01


def build_sell_plan(
    status: str,
    holding_period: str,
    confirm_price: float,
    take_profit_price: float,
    trailing_stop_price: float,
    invalid_price: float,
) -> str:
    if status == "观察":
        return (
            f"只在放量站上确认价 {confirm_price:.2f} 后执行；持有 {holding_period}，"
            f"{review_text(holding_period)}，触及 {take_profit_price:.2f} 分批止盈，"
            f"收盘跌破 {trailing_stop_price:.2f} 或盘中跌破 {invalid_price:.2f} 记为风险预警，"
            "按A股T+1在次交易日处理。"
        )
    if status == "条件执行":
        return (
            f"次日只在放量站上确认价 {confirm_price:.2f} 后执行；持有 {holding_period}，"
            f"触及 {take_profit_price:.2f} 分批止盈，"
            f"收盘跌破 {trailing_stop_price:.2f} 或盘中跌破 {invalid_price:.2f} 记为风险预警，"
            "按A股T+1在次交易日处理。"
        )
    if status in {"可执行", "突破"}:
        return (
            f"持有 {holding_period}；{review_text(holding_period)}，触及 {take_profit_price:.2f} 分批止盈，"
            f"收盘跌破 {trailing_stop_price:.2f} 或盘中跌破 {invalid_price:.2f} 记为风险预警，"
            "按A股T+1在次交易日处理；第5个交易日仍未延续则降低仓位。"
        )
    if status == "等待":
        return f"等待回到观察区并重新站上确认价 {confirm_price:.2f}；未触发前不执行。"
    if status == "偏离":
        return "短线位置偏离，不追高；等回踩观察区或重新生成信号。"
    if status == "失效":
        return f"已跌破失效线 {invalid_price:.2f}，短线计划失效；若已买入，按A股T+1在次交易日处理。"
    if status == "排除":
        return "被硬过滤或风险因子排除，不进入短线执行池。"
    return f"持有 {holding_period}，以 {invalid_price:.2f} 为失效线，按A股T+1执行风控。"


def review_text(holding_period: str) -> str:
    if holding_period == "2-3交易日":
        return "第2个交易日复核强弱，第3个交易日不延续则降低仓位"
    return "第3个交易日复核强弱"
