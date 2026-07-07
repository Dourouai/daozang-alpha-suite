from __future__ import annotations

from typing import Any


HIGH_WIN_RATE = "高胜率"
BALANCED = "均衡"
HIGH_ODDS = "高赔率"
DEFENSIVE = "防守"
NO_TRADE = "不交易"


def classify_recommendation_strategy(item: Any, realtime_check: Any | None = None) -> dict[str, Any]:
    """Classify a recommendation into a reviewable trade playbook.

    The classification is intentionally rule-based. It is used for logging,
    reports, and later outcome attribution; it must not override risk filters.
    """

    status = str(getattr(item, "status", "") or "")
    close = _float(getattr(item, "close", None))
    confirm = _float(getattr(item, "confirm_price", None))
    stop = _float(getattr(item, "invalid_price", None))
    target = _float(getattr(item, "take_profit_price", None))
    score = _int(getattr(item, "candidate_score", None) or getattr(item, "score", None))
    model_pct_rank = _float(getattr(item, "model_pct_rank", None))
    macro_event_score = _int(getattr(item, "macro_event_score", None))
    breakdown = str(getattr(item, "candidate_breakdown", "") or "")
    risk_text = str(getattr(item, "risk", "") or "")
    reason_text = str(getattr(item, "reason", "") or "")
    industry = str(getattr(item, "industry", "") or "")
    sector_rotation = str(getattr(item, "sector_rotation", "") or "")
    realtime_status = "" if realtime_check is None else str(getattr(realtime_check, "status", "") or "")

    if status in {"排除", "失效"} or (stop and close and close <= stop):
        return _profile(
            "no_trade_risk",
            "风险排除",
            NO_TRADE,
            "风险已经触发或被硬过滤，胜率统计只用于复盘，不进入买入池。",
        )

    if confirm and close and target and close >= target:
        return _profile(
            "take_profit_zone",
            "目标区不追",
            NO_TRADE,
            "已到目标区，继续买入的赔率变差，优先等待回踩或做持仓止盈复核。",
        )

    if confirm and close and close > confirm * 1.012:
        return _profile(
            "momentum_chase_risk",
            "追高风险",
            NO_TRADE,
            "价格偏离确认价超过追高线，短线胜率和盈亏比都变差，等待回踩。",
        )

    if _is_expectation_priced_in(breakdown, risk_text):
        return _profile(
            "expectation_priced_in",
            "预期透支",
            NO_TRADE,
            "利好可能已经提前反映，等回踩或等新的超预期信息，不把普通兑现当新买点。",
        )

    if _is_expectation_setup(breakdown, reason_text):
        return _profile(
            "expectation_setup",
            "潜伏预期",
            HIGH_ODDS,
            "有正向预期但价格尚未充分反映，仍需等待价格确认，适合小仓试错而非重仓押注。",
        )

    if realtime_status == "实时可买":
        return _profile(
            "breakout_confirmed",
            "突破确认",
            HIGH_WIN_RATE,
            "站稳确认价、未过追高线，适合小仓试错；收益通常不如低吸，但执行确定性更强。",
        )

    if _is_event_driven(macro_event_score, breakdown):
        return _profile(
            "event_catalyst",
            "事件催化",
            BALANCED,
            "由政策、公告、产业事件驱动，必须叠加价格确认，不能只靠故事买入。",
        )

    if _is_defensive(industry, sector_rotation, str(getattr(item, "name", "") or "")):
        return _profile(
            "defensive_rotation",
            "防守轮动",
            DEFENSIVE,
            "指数偏弱或候选失效率高时，用银行、公用事业等低波动资产保护组合。",
        )

    if _is_pullback_reversal(close, confirm, stop, model_pct_rank):
        return _profile(
            "pullback_reversal",
            "低吸反转",
            HIGH_ODDS,
            "回踩未破位、靠近风控线，赔率更好；必须等承接/VWAP修复，不能接破位下跌。",
        )

    if status in {"可执行", "条件执行", "突破"}:
        return _profile(
            "breakout_watch",
            "突破观察",
            HIGH_WIN_RATE if score >= 100 else BALANCED,
            "接近或站上确认价，但还需要盘中站稳、放量和板块共振确认。",
        )

    if status in {"观察", "等待"}:
        return _profile(
            "wait_for_trigger",
            "等待触发",
            BALANCED,
            "因子可以继续跟踪，但买点没有完成；先观察，不提前把观察票当持仓。",
        )

    return _profile(
        "general_review",
        "综合观察",
        BALANCED,
        "没有落入明确打法，先记录、复盘，不给高优先级。",
    )


def classify_buy_plan_strategy(item: Any) -> dict[str, Any]:
    status = str(getattr(item, "status", "") or "")
    close = _float(getattr(item, "close", None))
    confirm = _float(getattr(item, "confirm", None))
    stop = _float(getattr(item, "stop", None))
    group = str(getattr(item, "group", "") or "")
    score = _int(getattr(item, "candidate_score", None))
    model_pct_rank = _float(getattr(item, "model_pct_rank", None))

    if _is_defensive(group, group, str(getattr(item, "name", "") or "")):
        return _profile(
            "defensive_rotation",
            "防守轮动",
            DEFENSIVE,
            "用于弱市或资金释放后的防守替代，不追求最高弹性。",
        )
    if _is_pullback_reversal(close, confirm, stop, model_pct_rank):
        return _profile(
            "pullback_reversal",
            "低吸反转",
            HIGH_ODDS,
            "候选靠近风控区但未破位，等待承接确认后小仓试错。",
        )
    if status in {"可执行", "条件执行", "突破"}:
        return _profile(
            "breakout_watch",
            "突破观察",
            HIGH_WIN_RATE if score >= 100 else BALANCED,
            "只在站稳确认价且未过追高线时执行。",
        )
    return _profile(
        "wait_for_trigger",
        "等待触发",
        BALANCED,
        "买点尚未完成，先观察。",
    )


def classify_holding_strategy(item: Any) -> dict[str, Any]:
    action = str(getattr(item, "action", "") or "")
    pnl_pct = _float(getattr(item, "pnl_pct", None))
    price = _float(getattr(item, "price", None))
    confirm = _float(getattr(item, "confirm", None))
    stop = _float(getattr(item, "stop", None))
    model_pct_rank = _float(getattr(item, "model_pct_rank", None))

    if action in {"退出优先", "减仓优先", "买点弱化", "时间止损优先"} or (stop and price and price < stop):
        return _profile(
            "capital_release",
            "释放资金",
            DEFENSIVE,
            "持仓已经弱化，目标是降低回撤、把资金留给更强信号。",
        )
    if action == "止盈优先":
        return _profile(
            "profit_protection",
            "利润保护",
            DEFENSIVE,
            "价格接近目标区，优先保护收益而不是扩大风险。",
        )
    if action == "资金效率观察":
        return _profile(
            "capital_efficiency",
            "资金效率",
            DEFENSIVE,
            "没有明显亏损但弹性不足，适合和新候选比较机会成本。",
        )
    if _is_pullback_reversal(price, confirm, stop, model_pct_rank):
        return _profile(
            "pullback_hold",
            "回踩持有",
            BALANCED,
            "仍在风控线上方，但需要等承接修复，不能加仓摊低。",
        )
    if pnl_pct and pnl_pct > 0:
        return _profile(
            "trend_hold",
            "趋势持有",
            HIGH_WIN_RATE,
            "已有浮盈且未跌回确认价，继续跟踪并上移保护。",
        )
    return _profile(
        "neutral_hold",
        "普通持有",
        BALANCED,
        "持仓尚未给出明确方向，继续按确认价和时间止损复核。",
    )


def _profile(strategy_id: str, name: str, expectancy_style: str, note: str) -> dict[str, Any]:
    return {
        "id": strategy_id,
        "name": name,
        "expectancy_style": expectancy_style,
        "note": note,
    }


def _is_pullback_reversal(
    price: float | None,
    confirm: float | None,
    stop: float | None,
    model_pct_rank: float | None,
) -> bool:
    if not price or not confirm or not stop:
        return False
    if price <= stop:
        return False
    # Pullback candidates should be below/near the trigger but not broken.
    below_confirm = price < confirm * 1.002
    stop_buffer = price / stop - 1
    not_too_far_from_stop = 0.012 <= stop_buffer <= 0.08
    model_ok = model_pct_rank is None or model_pct_rank >= 0.30
    return below_confirm and not_too_far_from_stop and model_ok


def _is_event_driven(macro_event_score: int, breakdown: str) -> bool:
    if macro_event_score >= 12:
        return True
    return any(key in breakdown for key in ("宏观事件", "政策关键词", "新闻事件", "公告事件"))


def _is_expectation_priced_in(breakdown: str, risk_text: str) -> bool:
    text = f"{breakdown} {risk_text}"
    return any(key in text for key in ("预期透支", "利好兑现", "预期定价-"))


def _is_expectation_setup(breakdown: str, reason_text: str) -> bool:
    text = f"{breakdown} {reason_text}"
    return "预期潜伏" in text or "预期定价+" in text and "预期发酵" not in text


def _is_defensive(industry: str, sector_rotation: str, name: str) -> bool:
    text = f"{industry} {sector_rotation} {name}"
    return any(key in text for key in ("银行", "公用事业", "电力", "水电", "核电", "高股息"))


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
