from __future__ import annotations

import csv
import itertools
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from beichen_alpha.models import FactorScore, RealtimeQuote, Recommendation
from beichen_alpha.strategy.final_action import (
    NO_TRADE,
    action_priority,
    decide_holding_action,
    decide_recommendation_action,
)


ACTIVE_BUY_STATUSES = {"可执行", "条件执行", "突破", "观察"}
EXECUTABLE_BUY_STATUSES = {"可执行", "条件执行", "突破"}
WEAK_BUY_STATUSES = {"失效", "排除", "偏离", "等待"}
FAILED_BUY_STATUSES = {"失效", "排除"}
LOW_HOLDING_MODEL_PCT_RANK = 0.30


@dataclass(frozen=True)
class HoldingPlan:
    code: str
    name: str
    shares: int
    cost: float
    price: float
    confirm: float
    stop: float
    target: float
    entry_date: str
    holding_trade_days: int | None
    pnl: float
    pnl_pct: float
    action: str
    trigger: str
    release_score: int = 0
    release_reason: str = ""
    price_source: str = "日线"
    execution_detail: str = ""
    execution_score: int = 0
    model_pct_rank: float | None = None
    final_action: str = ""
    action_confidence: str = ""
    action_reason: str = ""
    prediction_up_prob: float | None = None
    prediction_avg_return: float | None = None
    prediction_target_hit_prob: float | None = None
    prediction_stop_hit_prob: float | None = None
    prediction_median_return: float | None = None
    prediction_confidence: str = ""
    prediction_sample_count: int = 0
    prediction_detail: str = ""
    factor_scores: tuple[FactorScore, ...] = ()


@dataclass(frozen=True)
class BuyPlan:
    code: str
    name: str
    status: str
    group: str
    close: float
    confirm: float
    stop: float
    target: float | None
    candidate_score: int
    lot_cost: float
    max_lots: int
    model_pct_rank: float | None
    trigger: str
    risk: str
    price_source: str = "日线"
    execution_detail: str = ""
    execution_score: int = 0
    final_action: str = ""
    action_confidence: str = ""
    action_reason: str = ""
    prediction_up_prob: float | None = None
    prediction_avg_return: float | None = None
    prediction_target_hit_prob: float | None = None
    prediction_stop_hit_prob: float | None = None
    prediction_median_return: float | None = None
    prediction_confidence: str = ""
    prediction_sample_count: int = 0
    prediction_detail: str = ""
    factor_scores: tuple[FactorScore, ...] = ()


@dataclass(frozen=True)
class ThreeDayTradePlan:
    capital: float
    invested_cost: float
    available_cash: float
    rotation_cash: float
    holding_plans: tuple[HoldingPlan, ...]
    buy_plans: tuple[BuyPlan, ...]
    notes: tuple[str, ...]
    model_score_trade_date: str = ""
    model_score_rows: int = 0
    model_score_covered: int = 0
    model_score_missing: tuple[str, ...] = ()
    model_score_stale: bool = False
    model_score_note: str = ""
    risk_posture: str = "正常"
    new_buy_budget_scale: float = 1.0
    candidate_failure_ratio: float = 0.0
    candidate_executable_count: int = 0
    candidate_failed_count: int = 0


@dataclass(frozen=True)
class TradeRiskPosture:
    label: str
    budget_scale: float
    max_position_pct: float
    block_new_buys: bool
    failure_ratio: float
    executable_count: int
    failed_count: int
    notes: tuple[str, ...]


@dataclass(frozen=True)
class ModelScoreCoverage:
    path: str
    exists: bool
    trade_date: str
    rows: int
    covered: int
    missing: tuple[str, ...]
    stale: bool
    detail: str


def build_three_day_trade_plan(
    recommendations: list[Recommendation],
    positions: list[dict[str, Any]],
    capital: float = 10000.0,
    top_n: int = 3,
    lot_size: int = 100,
    max_trade_pct: float | None = None,
    model_scores: dict[str, float] | None = None,
    excluded_groups: Iterable[str] = (),
    preferred_groups: Iterable[str] = (),
    review_date: date | datetime | str | None = None,
    trading_dates: Iterable[date | datetime | str] | None = None,
    model_coverage: ModelScoreCoverage | None = None,
    realtime_quotes: dict[str, RealtimeQuote] | None = None,
) -> ThreeDayTradePlan:
    by_code = {item.code: item for item in recommendations}
    held_codes = {str(item["code"]) for item in positions}
    score_map = model_scores or {}
    invested_cost = sum(float(item["cost"]) * int(item["shares"]) for item in positions)
    available_cash = max(capital - invested_cost, 0.0)
    normalized_review_date = parse_position_date(review_date)
    normalized_trading_dates = tuple(parse_position_date(item) for item in (trading_dates or ()))

    holding_plans = tuple(
        build_holding_plan(
            item,
            by_code.get(str(item["code"])),
            model_pct_rank=holding_model_rank(str(item["code"]), by_code, score_map),
            review_date=normalized_review_date,
            trading_dates=normalized_trading_dates,
            quote=(realtime_quotes or {}).get(str(item["code"])),
        )
        for item in positions
    )
    held_groups = {infer_trade_group(plan.name) for plan in holding_plans}
    excluded_group_set = {str(item).strip() for item in excluded_groups if str(item).strip()}
    preferred_group_set = {str(item).strip() for item in preferred_groups if str(item).strip()}
    posture_recommendations = [
        item
        for item in recommendations
        if item.code not in held_codes and infer_trade_group(item.name) not in excluded_group_set
    ]
    risk_posture = assess_trade_risk_posture(
        posture_recommendations,
        model_coverage=model_coverage,
    )
    rotation_cash = min(capital, available_cash + releasable_holding_value(holding_plans))
    if max_trade_pct is not None:
        rotation_cash = min(rotation_cash, max(capital * max_trade_pct, 0.0))
    rotation_cash = 0.0 if risk_posture.block_new_buys else rotation_cash * risk_posture.budget_scale
    candidates = [
        build_buy_plan(
            item,
            cash_budget=rotation_cash,
            lot_size=lot_size,
            model_pct_rank=score_map.get(item.code, item.model_pct_rank),
            quote=(realtime_quotes or {}).get(item.code),
        )
        for item in recommendations
        if item.code not in held_codes and item.status in ACTIVE_BUY_STATUSES
    ]
    candidates = [item for item in candidates if item.group not in excluded_group_set]
    candidates = [item for item in candidates if item.final_action != NO_TRADE]
    candidates = [item for item in candidates if item.max_lots >= 1]
    selected = choose_buy_plans(
        candidates,
        top_n=top_n,
        cash_limit=rotation_cash,
        held_groups=held_groups,
        preferred_groups=preferred_group_set,
    )
    # --- Score-weighted position sizing ---
    selected = apply_score_weighted_sizing(
        selected,
        rotation_cash=rotation_cash,
        lot_size=lot_size,
        max_position_pct=risk_posture.max_position_pct,
    )
    notes_list = [
        "盈利优先：候选不等于买入，必须同时满足动作、价格、风险和资金效率。",
        "已有持仓可按交易规则卖出/减仓；只有新买入部分受T+1限制。",
        "若当前持仓跌回确认价下方且收不回，优先释放仓位，再执行新候选。",
        "若持仓弹性不足、资金效率低，10:30 前不放量脱离成本区则考虑轮动。",
    ]
    if preferred_group_set:
        notes_list.append("新增持仓优先观察: " + "、".join(sorted(preferred_group_set)) + "。")
    if excluded_group_set:
        notes_list.append("新增持仓暂不纳入: " + "、".join(sorted(excluded_group_set)) + "。")
    notes_list.extend(risk_posture.notes)
    if model_coverage is not None:
        notes_list.append(model_coverage.detail)
    holding_plans = adjust_holding_release_scores_for_opportunity(holding_plans, selected)
    return ThreeDayTradePlan(
        capital=capital,
        invested_cost=invested_cost,
        available_cash=available_cash,
        rotation_cash=rotation_cash,
        holding_plans=holding_plans,
        buy_plans=tuple(selected),
        notes=tuple(notes_list),
        model_score_trade_date="" if model_coverage is None else model_coverage.trade_date,
        model_score_rows=0 if model_coverage is None else model_coverage.rows,
        model_score_covered=0 if model_coverage is None else model_coverage.covered,
        model_score_missing=() if model_coverage is None else model_coverage.missing,
        model_score_stale=False if model_coverage is None else model_coverage.stale,
        model_score_note="" if model_coverage is None else model_coverage.detail,
        risk_posture=risk_posture.label,
        new_buy_budget_scale=risk_posture.budget_scale,
        candidate_failure_ratio=risk_posture.failure_ratio,
        candidate_executable_count=risk_posture.executable_count,
        candidate_failed_count=risk_posture.failed_count,
    )


def build_holding_plan(
    position: dict[str, Any],
    recommendation: Recommendation | None,
    *,
    model_pct_rank: float | None = None,
    review_date: date | None = None,
    trading_dates: Iterable[date | None] = (),
    quote: RealtimeQuote | None = None,
) -> HoldingPlan:
    code = str(position["code"])
    name = str(position.get("name") or code)
    shares = int(position["shares"])
    cost = float(position["cost"])
    # Use dynamically calculated levels from recommendation if available,
    # fall back to stored values from positions JSON.
    if recommendation is not None:
        confirm = recommendation.confirm_price
        stop = recommendation.invalid_price
        target = recommendation.take_profit_price or float(position["target"])
    else:
        confirm = float(position["confirm"])
        stop = float(position["invalid"])
        target = float(position["target"])
    entry_date = parse_position_date(position.get("entry_date"))
    entry_date_text = entry_date.isoformat() if entry_date is not None else str(position.get("entry_date") or "")
    holding_trade_days = calc_holding_trade_days(entry_date, review_date, trading_dates)
    price = quote.price if quote is not None and quote.price > 0 else (
        recommendation.close if recommendation is not None else cost
    )
    price_source = quote_source_text(quote)
    execution_score, execution_detail = score_trade_execution_facts(
        quote,
        price=price,
        cash_budget=None,
        lot_size=100,
    )
    pnl = (price - cost) * shares
    pnl_pct = price / cost - 1 if cost else 0.0
    if model_pct_rank is None and recommendation is not None:
        model_pct_rank = recommendation.model_pct_rank

    if price <= 0:
        action = "等待行情"
        trigger = "行情缺失，不做主动操作。"
    elif price < stop:
        action = "退出优先"
        trigger = f"低于止损线 {stop:.2f}，已有仓位可按风控减仓/退出。"
    elif price < confirm:
        if is_low_model_rank(model_pct_rank):
            action = "减仓优先"
            trigger = (
                f"跌回确认价 {confirm:.2f} 下方，且道藏模型分低于30%；"
                "若盘中不能快速收回确认价，优先释放仓位，不再只做观察。"
            )
        else:
            action = "买点弱化"
            trigger = f"跌回确认价 {confirm:.2f} 下方，若盘中/收盘收不回，优先减仓。"
    elif price >= target:
        action = "止盈优先"
        trigger = f"到达目标价 {target:.2f} 附近，分批止盈或上移保护线。"
    elif has_time_stop_pressure(holding_trade_days, pnl_pct, price, confirm):
        if is_low_model_rank(model_pct_rank):
            action = "减仓优先"
            trigger = (
                f"{holding_age_text(holding_trade_days)}仍未形成有效盈利，且道藏模型分低于30%；"
                "若10:30前无放量延续，优先减仓或退出。"
            )
        else:
            action = "时间止损优先"
            trigger = (
                f"{holding_age_text(holding_trade_days)}，仍未形成有效盈利/脱离确认区；"
                "若10:30前无放量延续，优先减仓或退出，把资金轮动到更强候选。"
            )
    elif has_low_capital_efficiency(recommendation, pnl_pct, price, confirm, holding_trade_days):
        if is_low_model_rank(model_pct_rank):
            action = "减仓优先"
            trigger = (
                f"{holding_age_text(holding_trade_days)}，短线弹性不足且道藏模型分低于30%；"
                "若10:30前不放量脱离成本/确认区，优先释放资金。"
            )
        else:
            action = "资金效率观察"
            trigger = (
                f"{holding_age_text(holding_trade_days)}，仍在确认价 {confirm:.2f} 上方，"
                "但短线弹性不足且仍在成本附近；若10:30前不放量脱离成本/确认区，"
                "考虑减仓释放资金，轮动到更强候选。"
            )
    else:
        action = "继续持有"
        trigger = f"仍在确认价 {confirm:.2f} 上方，继续观察；不加仓。"

    prediction = prediction_from_recommendation(recommendation)
    release_score, release_reason = score_holding_release(
        action=action,
        price=price,
        cost=cost,
        confirm=confirm,
        stop=stop,
        target=target,
        pnl_pct=pnl_pct,
        holding_trade_days=holding_trade_days,
        model_pct_rank=model_pct_rank,
        execution_score=execution_score,
        prediction=prediction,
        recommendation=recommendation,
    )
    final = decide_holding_action(
        SimpleNamespace(
            action=action,
            pnl_pct=pnl_pct,
            price=price,
            confirm=confirm,
            stop=stop,
            model_pct_rank=model_pct_rank,
        )
    )
    return HoldingPlan(
        code=code,
        name=name,
        shares=shares,
        cost=cost,
        price=price,
        confirm=confirm,
        stop=stop,
        target=target,
        entry_date=entry_date_text,
        holding_trade_days=holding_trade_days,
        pnl=pnl,
        pnl_pct=pnl_pct,
        action=action,
        trigger=trigger,
        release_score=release_score,
        release_reason=release_reason,
        price_source=price_source,
        execution_detail=execution_detail,
        execution_score=execution_score,
        model_pct_rank=model_pct_rank,
        final_action=final.action,
        action_confidence=final.confidence,
        action_reason=final.reason,
        factor_scores=() if recommendation is None else tuple(recommendation.factor_scores),
        **prediction,
    )


def has_low_capital_efficiency(
    recommendation: Recommendation | None,
    pnl_pct: float,
    price: float,
    confirm: float,
    holding_trade_days: int | None,
) -> bool:
    if recommendation is None:
        return False
    if abs(pnl_pct) > 0.015:
        return False
    if "短线弹性" in recommendation.risk:
        return True
    if holding_trade_days is not None and holding_trade_days < 2:
        return False
    if confirm <= 0:
        return False
    confirm_gap = price / confirm - 1
    return price >= confirm and confirm_gap <= 0.006


def score_holding_release(
    *,
    action: str,
    price: float,
    cost: float,
    confirm: float,
    stop: float,
    target: float,
    pnl_pct: float,
    holding_trade_days: int | None,
    model_pct_rank: float | None,
    execution_score: int,
    prediction: dict[str, Any],
    recommendation: Recommendation | None,
) -> tuple[int, str]:
    score = 0
    reasons: list[str] = []

    action_base = {
        "退出优先": 100,
        "减仓优先": 82,
        "时间止损优先": 74,
        "买点弱化": 68,
        "资金效率观察": 58,
        "止盈优先": 52,
        "继续持有": 18,
        "等待行情": 10,
    }.get(action, 25)
    score += action_base
    reasons.append(f"动作基准 {action_base}")

    if price > 0 and stop > 0 and price < stop:
        score += 25
        reasons.append("跌破风控线")
    elif price > 0 and confirm > 0 and price < confirm:
        score += 16
        reasons.append("跌回确认价")
    elif price > 0 and confirm > 0 and price / confirm - 1 <= 0.006:
        score += 8
        reasons.append("贴近确认区，资金效率偏低")

    if target > 0 and price >= target:
        score += 16
        reasons.append("到达目标区，需要保护利润")
    if holding_trade_days is not None and holding_trade_days >= 3 and pnl_pct < 0.01:
        score += 16
        reasons.append("第3日仍未形成有效盈利")
    elif holding_trade_days is not None and holding_trade_days >= 2 and pnl_pct < 0.003:
        score += 8
        reasons.append("第2日收益不足")

    if model_pct_rank is not None:
        if model_pct_rank < 0.30:
            score += 16
            reasons.append("道藏分低于30%")
        elif model_pct_rank < 0.50:
            score += 6
            reasons.append("道藏分不强")
        elif model_pct_rank >= 0.75:
            score -= 8
            reasons.append("道藏分较强，释放分下调")

    avg_return = prediction.get("prediction_avg_return")
    up_prob = prediction.get("prediction_up_prob")
    if avg_return is not None and up_prob is not None:
        if avg_return < -0.003 and up_prob < 0.48:
            score += 14
            reasons.append("历史校准为负期望")
        elif avg_return > 0.006 and up_prob >= 0.55:
            score -= 8
            reasons.append("历史校准仍有正期望")

    if pnl_pct <= -0.015:
        score += 10
        reasons.append("浮亏扩大")
    elif pnl_pct >= 0.025:
        score += 7
        reasons.append("已有浮盈，考虑保护")

    if execution_score <= -12:
        score += 8
        reasons.append("盘中执行因子转弱")
    elif execution_score >= 12:
        score -= 5
        reasons.append("盘中执行因子仍支持持有")

    if recommendation is not None and "短线弹性" in recommendation.risk:
        score += 10
        reasons.append("短线弹性不足")

    bounded = max(0, min(100, int(round(score))))
    label = release_score_label(bounded)
    return bounded, f"{label}: " + "；".join(dict.fromkeys(reasons))


def release_score_label(score: int) -> str:
    if score >= 80:
        return "释放优先"
    if score >= 60:
        return "倾向减仓"
    if score >= 40:
        return "效率观察"
    return "继续持有"


def adjust_holding_release_scores_for_opportunity(
    holding_plans: Iterable[HoldingPlan],
    buy_plans: Iterable[BuyPlan],
) -> tuple[HoldingPlan, ...]:
    buy_tuple = tuple(buy_plans)
    if not buy_tuple:
        return tuple(holding_plans)
    strongest = max(candidate_utility(item, set(), set()) for item in buy_tuple)
    if strongest < 125:
        return tuple(holding_plans)

    adjusted: list[HoldingPlan] = []
    for item in holding_plans:
        if item.release_score < 40:
            adjusted.append(item)
            continue
        bump = 10 if strongest >= 155 else 6
        new_score = min(100, item.release_score + bump)
        new_reason = item.release_reason + f"；更强候选机会成本 +{bump}"
        adjusted.append(replace(item, release_score=new_score, release_reason=new_reason))
    return tuple(adjusted)


def is_low_model_rank(model_pct_rank: float | None) -> bool:
    return model_pct_rank is not None and model_pct_rank < LOW_HOLDING_MODEL_PCT_RANK


def holding_model_rank(
    code: str,
    recommendations_by_code: dict[str, Recommendation],
    model_scores: dict[str, float],
) -> float | None:
    recommendation = recommendations_by_code.get(code)
    return model_scores.get(code, recommendation.model_pct_rank if recommendation is not None else None)


def prediction_from_recommendation(recommendation: Recommendation | None) -> dict[str, Any]:
    if recommendation is None or recommendation.calibration_up_prob is None:
        return {}
    return {
        "prediction_up_prob": recommendation.calibration_up_prob,
        "prediction_avg_return": recommendation.calibration_avg_return,
        "prediction_target_hit_prob": recommendation.calibration_target_hit_prob,
        "prediction_stop_hit_prob": recommendation.calibration_stop_hit_prob,
        "prediction_median_return": recommendation.calibration_median_return,
        "prediction_confidence": recommendation.calibration_confidence,
        "prediction_sample_count": recommendation.calibration_sample_count,
        "prediction_detail": recommendation.calibration_detail,
    }


def assess_trade_risk_posture(
    recommendations: Iterable[Recommendation],
    *,
    model_coverage: ModelScoreCoverage | None = None,
) -> TradeRiskPosture:
    rows = tuple(recommendations)
    total = len(rows)
    executable_count = sum(1 for item in rows if item.status in EXECUTABLE_BUY_STATUSES)
    failed_count = sum(1 for item in rows if item.status in FAILED_BUY_STATUSES)
    weak_count = sum(1 for item in rows if item.status in WEAK_BUY_STATUSES)
    failure_ratio = failed_count / total if total else 0.0
    weak_ratio = weak_count / total if total else 0.0
    budget_scale = 1.0
    max_position_pct = 0.30
    label = "正常"
    block_new_buys = False
    notes: list[str] = []

    if total >= 5 and failure_ratio >= 0.30:
        label = "防守"
        budget_scale = min(budget_scale, 0.40)
        max_position_pct = min(max_position_pct, 0.16)
        notes.append(
            f"候选池失效率 {failure_ratio:.0%}，新增仓位进入防守模式；"
            "先释放弱持仓，未站稳确认价不新开。"
        )
    elif total >= 5 and weak_ratio >= 0.50:
        label = "谨慎"
        budget_scale = min(budget_scale, 0.55)
        max_position_pct = min(max_position_pct, 0.18)
        notes.append(
            f"候选池弱信号占比 {weak_ratio:.0%}，新增预算下调；"
            "优先等盘面恢复再执行。"
        )

    if total >= 5 and executable_count < 2:
        label = "谨慎" if label == "正常" else label
        budget_scale = min(budget_scale, 0.60)
        max_position_pct = min(max_position_pct, 0.18)
        notes.append(
            f"可执行候选仅 {executable_count} 只，不做满仓轮动；"
            "新增买入必须有放量确认。"
        )

    if total >= 5 and executable_count == 0 and weak_ratio >= 0.50:
        label = "暂停新增"
        budget_scale = 0.0
        max_position_pct = 0.0
        block_new_buys = True
        notes.append("候选池无可执行标的且弱信号过半，今日暂停新增买入，只做持仓风控。")

    if model_coverage is not None and model_coverage.stale:
        label = "谨慎" if label == "正常" else label
        budget_scale = min(budget_scale, 0.60)
        max_position_pct = min(max_position_pct, 0.20)
        notes.append("道藏模型分偏旧，新开仓预算下调，低分持仓优先复核。")

    return TradeRiskPosture(
        label=label,
        budget_scale=budget_scale,
        max_position_pct=max_position_pct,
        block_new_buys=block_new_buys,
        failure_ratio=failure_ratio,
        executable_count=executable_count,
        failed_count=failed_count,
        notes=tuple(dict.fromkeys(notes)),
    )


def has_time_stop_pressure(
    holding_trade_days: int | None,
    pnl_pct: float,
    price: float,
    confirm: float,
) -> bool:
    if holding_trade_days is None or holding_trade_days < 3:
        return False
    if confirm <= 0:
        return False
    confirm_gap = price / confirm - 1
    return pnl_pct < 0.01 and confirm_gap <= 0.012


def holding_age_text(holding_trade_days: int | None) -> str:
    if holding_trade_days is None:
        return "未记录入场交易日"
    return f"持仓第{holding_trade_days}个交易日"


def calc_holding_trade_days(
    entry_date: date | None,
    review_date: date | None,
    trading_dates: Iterable[date | None] = (),
) -> int | None:
    if entry_date is None or review_date is None or entry_date > review_date:
        return None
    known_calendar = {
        item
        for item in trading_dates
        if item is not None and entry_date <= item <= review_date and is_weekday(item)
    }
    calendar = set(known_calendar)
    if known_calendar:
        last_known = max(known_calendar)
        current = last_known + timedelta(days=1)
        while current <= review_date:
            if is_weekday(current):
                calendar.add(current)
            current += timedelta(days=1)
    if is_weekday(entry_date):
        calendar.add(entry_date)
    if is_weekday(review_date):
        calendar.add(review_date)
    if calendar:
        return len(calendar)
    return count_weekdays(entry_date, review_date)


def count_weekdays(start: date, end: date) -> int:
    total = 0
    current = start
    while current <= end:
        if is_weekday(current):
            total += 1
        current += timedelta(days=1)
    return total


def is_weekday(value: date) -> bool:
    return value.weekday() < 5


def parse_position_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported position date: {value}")


def quote_source_text(quote: RealtimeQuote | None) -> str:
    if quote is None or quote.price <= 0:
        return "日线"
    return f"实时({quote.source})"


def score_trade_execution_facts(
    quote: RealtimeQuote | None,
    *,
    price: float,
    cash_budget: float | None,
    lot_size: int = 100,
) -> tuple[int, str]:
    if quote is None or quote.price <= 0:
        return 0, "执行因子: 未接入实时行情，使用日线参考价。"

    score = 0
    notes = [f"执行因子: {quote_source_text(quote)}"]
    if quote.quote_time is not None:
        notes.append("时间 " + quote.quote_time.strftime("%H:%M:%S"))
    notes.append(f"涨跌 {quote.change_pct:+.2f}%")

    one_lot_cost = price * lot_size
    notes.append(f"一手约 {one_lot_cost:.0f}元")
    if cash_budget is not None:
        if one_lot_cost > cash_budget:
            score -= 30
            notes.append("现金不足一手")
        elif cash_budget > 0 and one_lot_cost <= cash_budget * 0.35:
            score += 4
            notes.append("资金效率可控")

    if quote.limit_up_price and quote.limit_up_price > 0:
        notes.append(f"涨停 {quote.limit_up_price:.2f}")
        up_gap = quote.limit_up_price / price - 1 if price > 0 else 0
        if up_gap <= 0.003:
            score -= 14
            notes.append("贴近涨停不追")
        elif up_gap <= 0.015:
            score -= 6
            notes.append("离涨停过近")
    if quote.limit_down_price and quote.limit_down_price > 0:
        notes.append(f"跌停 {quote.limit_down_price:.2f}")
        down_gap = price / quote.limit_down_price - 1 if quote.limit_down_price > 0 else 0
        if down_gap <= 0.003:
            score -= 20
            notes.append("贴近跌停风险")
        elif down_gap <= 0.015:
            score -= 8
            notes.append("离跌停过近")

    if quote.turnover_rate is not None:
        notes.append(f"换手 {quote.turnover_rate:.2f}%")
        if quote.turnover_rate < 0.3:
            score -= 4
            notes.append("换手偏低")
        elif quote.turnover_rate <= 8:
            score += 4
        elif quote.turnover_rate > 18:
            score -= 5
            notes.append("换手过热")

    if quote.amount_billion is not None:
        notes.append(f"成交 {quote.amount_billion:.2f}亿")
        if quote.amount_billion >= 20:
            score += 5
        elif quote.amount_billion >= 5:
            score += 3
        elif quote.amount_billion < 1:
            score -= 3
            notes.append("成交偏弱")

    if quote.market_cap_billion is not None:
        notes.append(f"市值 {quote.market_cap_billion * 10:.0f}亿")
        if quote.market_cap_billion >= 300:
            score += 3
        elif quote.market_cap_billion < 30:
            score -= 4
            notes.append("市值承载偏弱")

    if quote.pe_ratio is not None:
        notes.append(f"PE {quote.pe_ratio:.2f}")
    if quote.pb_ratio is not None:
        notes.append(f"PB {quote.pb_ratio:.2f}")
    if quote.warning:
        score -= 8
        notes.append("行情提示: " + quote.warning)
    if quote.stale:
        score -= 8
        notes.append("行情过期")

    return max(min(score, 30), -50), " | ".join(notes)


def build_buy_plan(
    recommendation: Recommendation,
    cash_budget: float,
    lot_size: int,
    model_pct_rank: float | None,
    quote: RealtimeQuote | None = None,
) -> BuyPlan:
    reference_price = quote.price if quote is not None and quote.price > 0 else recommendation.close
    lot_cost = reference_price * lot_size
    max_lots = int(cash_budget // lot_cost) if lot_cost > 0 else 0
    target = recommendation.take_profit_price
    chase_line = recommendation.confirm_price * 1.012
    execution_score, execution_detail = score_trade_execution_facts(
        quote,
        price=reference_price,
        cash_budget=cash_budget,
        lot_size=lot_size,
    )
    trigger = (
        f"仅在放量站上确认价 {recommendation.confirm_price:.2f} 后买入；"
        f"高于 {chase_line:.2f} 不追。"
    )
    risk = (
        f"跌破 {recommendation.invalid_price:.2f} 触发风控；"
        "若不延续或资金效率下降则降仓/退出。"
    )
    final_source = recommendation
    if model_pct_rank is not None and model_pct_rank != recommendation.model_pct_rank:
        final_source = replace(recommendation, model_pct_rank=model_pct_rank)
    final = decide_recommendation_action(final_source)
    prediction = prediction_from_recommendation(recommendation)
    return BuyPlan(
        code=recommendation.code,
        name=recommendation.name,
        status=recommendation.status,
        group=infer_trade_group(recommendation.name),
        close=reference_price,
        confirm=recommendation.confirm_price,
        stop=recommendation.invalid_price,
        target=target,
        candidate_score=recommendation.candidate_score or recommendation.score,
        lot_cost=lot_cost,
        max_lots=max_lots,
        model_pct_rank=model_pct_rank,
        trigger=trigger,
        risk=risk,
        price_source=quote_source_text(quote),
        execution_detail=execution_detail,
        execution_score=execution_score,
        final_action=final.action,
        action_confidence=final.confidence,
        action_reason=final.reason,
        factor_scores=tuple(recommendation.factor_scores),
        **prediction,
    )


def releasable_holding_value(holding_plans: Iterable[HoldingPlan]) -> float:
    releasable_actions = {"资金效率观察", "时间止损优先", "退出优先", "买点弱化", "止盈优先", "减仓优先"}
    return sum(
        max(item.price, 0.0) * item.shares
        for item in holding_plans
        if item.action in releasable_actions or item.release_score >= 70
    )


def choose_buy_plans(
    candidates: list[BuyPlan],
    top_n: int,
    cash_limit: float,
    held_groups: set[str],
    preferred_groups: set[str] | None = None,
) -> list[BuyPlan]:
    if top_n <= 0 or not candidates:
        return []
    best: tuple[float, int, float, tuple[BuyPlan, ...]] | None = None
    max_size = min(top_n, len(candidates))
    preferred_candidates_available = any(
        item.group in (preferred_groups or set())
        and item.lot_cost <= cash_limit
        and not has_unfavorable_prediction(item)
        for item in candidates
    )
    for size in range(1, max_size + 1):
        for combo in itertools.combinations(candidates, size):
            lot_cost = sum(item.lot_cost for item in combo)
            if lot_cost > cash_limit:
                continue
            if preferred_candidates_available and not any(
                item.group in (preferred_groups or set()) and not has_unfavorable_prediction(item)
                for item in combo
            ):
                continue
            utility = score_combo(combo, held_groups, preferred_groups or set())
            raw_score = sum(item.candidate_score for item in combo)
            tie = (utility, raw_score, -lot_cost, combo)
            if best is None or tie[:3] > best[:3]:
                best = tie
    if best is None:
        return []
    selected = list(best[3])
    return sorted(
        selected,
        key=lambda item: candidate_utility(item, held_groups, preferred_groups or set()),
        reverse=True,
    )


def apply_score_weighted_sizing(
    candidates: list[BuyPlan],
    rotation_cash: float,
    lot_size: int = 100,
    max_position_pct: float = 0.30,
    min_score_for_weight: int = 10,
) -> list[BuyPlan]:
    """Apply score-weighted position sizing to selected candidates.

    Higher candidate_score → larger allocation.
    Each position capped at max_position_pct of rotation_cash.
    Candidates with very low scores get minimum 1 lot.

    Args:
        candidates: Selected buy candidates from choose_buy_plans.
        rotation_cash: Total cash available for new positions.
        lot_size: Shares per lot (default 100 for A-shares).
        max_position_pct: Max fraction of rotation_cash per single position.
        min_score_for_weight: Candidates below this score get minimum 1 lot.

    Returns:
        New list of BuyPlan with adjusted max_lots (since BuyPlan is frozen,
        we create new instances with updated lot allocation).
    """
    if not candidates or rotation_cash <= 0:
        return candidates

    # Compute raw scores (shift to positive range for weighting)
    scores = [max(item.candidate_score, min_score_for_weight) for item in candidates]
    total_score = sum(scores)
    if total_score <= 0:
        return candidates

    # Score-weighted budget allocation
    max_single_budget = rotation_cash * max_position_pct
    adjusted: list[BuyPlan] = []
    for item, score in zip(candidates, scores):
        weight = score / total_score
        budget = rotation_cash * weight
        # Cap at max single position
        budget = min(budget, max_single_budget)
        # Ensure at least 1 lot
        budget = max(budget, item.lot_cost)
        lots = int(budget // item.lot_cost) if item.lot_cost > 0 else 0
        lots = max(lots, 1)  # minimum 1 lot

        # Create new BuyPlan with adjusted lots
        adjusted.append(BuyPlan(
            code=item.code,
            name=item.name,
            status=item.status,
            group=item.group,
            close=item.close,
            confirm=item.confirm,
            stop=item.stop,
            target=item.target,
            candidate_score=item.candidate_score,
            lot_cost=item.lot_cost,
            max_lots=lots,
            model_pct_rank=item.model_pct_rank,
            trigger=item.trigger,
            risk=item.risk,
            price_source=item.price_source,
            execution_detail=item.execution_detail,
            execution_score=item.execution_score,
            final_action=item.final_action,
            action_confidence=item.action_confidence,
            action_reason=item.action_reason,
            prediction_up_prob=item.prediction_up_prob,
            prediction_avg_return=item.prediction_avg_return,
            prediction_target_hit_prob=item.prediction_target_hit_prob,
            prediction_stop_hit_prob=item.prediction_stop_hit_prob,
            prediction_median_return=item.prediction_median_return,
            prediction_confidence=item.prediction_confidence,
            prediction_sample_count=item.prediction_sample_count,
            prediction_detail=item.prediction_detail,
            factor_scores=item.factor_scores,
        ))

    return adjusted


def score_combo(combo: tuple[BuyPlan, ...], held_groups: set[str], preferred_groups: set[str]) -> float:
    groups = [item.group for item in combo]
    duplicate_penalty = (len(groups) - len(set(groups))) * 14
    return sum(candidate_utility(item, held_groups, preferred_groups) for item in combo) - duplicate_penalty


def candidate_utility(item: BuyPlan, held_groups: set[str], preferred_groups: set[str] | None = None) -> float:
    status_bonus = {
        "可执行": 18,
        "条件执行": 12,
        "突破": 8,
        "观察": 0,
    }.get(item.status, 0)
    action_bonus = action_priority(item.final_action) * 10
    held_group_penalty = 16 if item.group in held_groups else 0
    model_bonus = (item.model_pct_rank or 0.0) * 18
    preferred_bonus = 24 if item.group in (preferred_groups or set()) else 0
    return (
        item.candidate_score
        + status_bonus
        + action_bonus
        + model_bonus
        + preferred_bonus
        + execution_utility_bonus(item)
        + prediction_utility_bonus(item)
        - held_group_penalty
    )


def execution_utility_bonus(item: BuyPlan) -> float:
    return max(min(item.execution_score, 16), -24) * 0.5


def prediction_utility_bonus(item: BuyPlan) -> float:
    if item.prediction_up_prob is None or item.prediction_avg_return is None:
        return 0.0
    confidence_weight = {"高": 1.0, "中": 0.85, "低": 0.60}.get(item.prediction_confidence, 0.45)
    sample_weight = min(max(item.prediction_sample_count, 0) / 80, 1.0)
    edge = (item.prediction_up_prob - 0.50) * 50
    expected_return = item.prediction_avg_return * 450
    target_stop_edge = ((item.prediction_target_hit_prob or 0.0) - (item.prediction_stop_hit_prob or 0.0)) * 16
    penalty = -14 if has_unfavorable_prediction(item) else 0
    return (edge + expected_return + target_stop_edge) * confidence_weight * sample_weight + penalty


def has_unfavorable_prediction(item: BuyPlan) -> bool:
    if item.prediction_avg_return is None or item.prediction_up_prob is None:
        return False
    return item.prediction_avg_return < -0.003 and item.prediction_up_prob < 0.48


def infer_trade_group(name: str) -> str:
    if any(key in name for key in ("银行",)):
        return "银行"
    if any(key in name for key in ("证券", "财富", "保险", "人保", "平安", "太保", "人寿", "新华")):
        return "非银金融"
    if any(key in name for key in ("电力", "水电", "核电")):
        return "公用事业"
    if any(key in name for key in ("石油", "石化", "海油", "神华", "煤炭")):
        return "能源"
    if any(key in name for key in ("移动", "电信", "联通")):
        return "通信"
    if any(key in name for key in ("海康", "宁德", "中芯", "华创", "寒武", "海光", "澜起", "科技")):
        return "科技制造"
    if any(key in name for key in ("钼", "稀土", "铜", "铝", "黄金")):
        return "资源材料"
    if any(key in name for key in ("医药", "恒瑞", "药明", "百济", "生物", "君实", "迪哲", "博瑞", "亚虹", "泰格")):
        return "医药"
    return "其他"


def load_positions(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(payload.get("positions", []))


def load_model_scores(path: str | Path) -> dict[str, float]:
    score_path = Path(path)
    if not score_path.exists():
        return {}
    result: dict[str, float] = {}
    with score_path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            code = normalize_model_score_code(str(row.get("instrument") or row.get("code") or ""))
            if not code:
                continue
            try:
                result[code] = max(0.0, min(1.0, float(row.get("pct_rank") or 0.0)))
            except ValueError:
                continue
    return result


def inspect_model_score_coverage(
    path: str | Path,
    symbols: Iterable[str],
    *,
    as_of: date | datetime | str | None = None,
    max_stale_days: int = 7,
) -> ModelScoreCoverage:
    score_path = Path(path)
    requested = tuple(dict.fromkeys(normalize_model_score_code(symbol) for symbol in symbols))
    requested = tuple(symbol for symbol in requested if symbol)
    if not score_path.exists():
        return ModelScoreCoverage(
            path=str(score_path),
            exists=False,
            trade_date="",
            rows=0,
            covered=0,
            missing=requested,
            stale=True,
            detail=f"道藏模型分数缺失: {score_path}；本次候选均标记为模型未覆盖。",
        )

    rows = 0
    latest_trade_date = ""
    covered_codes: set[str] = set()
    with score_path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            rows += 1
            trade_date = str(row.get("trade_date") or "").strip()
            if trade_date > latest_trade_date:
                latest_trade_date = trade_date
            code = normalize_model_score_code(str(row.get("instrument") or row.get("code") or ""))
            if code:
                covered_codes.add(code)

    missing = tuple(symbol for symbol in requested if symbol not in covered_codes)
    as_of_date = parse_position_date(as_of)
    score_date = parse_position_date(latest_trade_date)
    stale = True
    if score_date is not None and as_of_date is not None:
        stale = (as_of_date - score_date).days > max_stale_days
    elif score_date is not None:
        stale = False
    covered = len(requested) - len(missing)
    detail = render_model_coverage_detail(
        trade_date=latest_trade_date,
        rows=rows,
        covered=covered,
        total=len(requested),
        missing=missing,
        stale=stale,
    )
    return ModelScoreCoverage(
        path=str(score_path),
        exists=True,
        trade_date=latest_trade_date,
        rows=rows,
        covered=covered,
        missing=missing,
        stale=stale,
        detail=detail,
    )


def render_model_coverage_detail(
    *,
    trade_date: str,
    rows: int,
    covered: int,
    total: int,
    missing: tuple[str, ...],
    stale: bool,
) -> str:
    stale_text = "，需要刷新" if stale else ""
    if total <= 0:
        return f"道藏模型分数: 日期 {trade_date or '-'}，覆盖 {rows} 行{stale_text}。"
    if missing:
        sample = "、".join(missing[:8])
        more = "" if len(missing) <= 8 else f" 等 {len(missing)} 只"
        return (
            f"道藏模型分数: 日期 {trade_date or '-'}，候选覆盖 {covered}/{total}"
            f"{stale_text}；未覆盖 {sample}{more}，相关标的显示为模型未覆盖。"
        )
    return f"道藏模型分数: 日期 {trade_date or '-'}，候选覆盖 {covered}/{total}{stale_text}。"


def normalize_model_score_code(value: str) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        text = text.split(".", 1)[0]
    if len(text) == 8 and text[:2] in {"SH", "SZ", "BJ"} and text[2:].isdigit():
        return text[2:]
    if len(text) == 6 and text.isdigit():
        return text
    digits = "".join(char for char in text if char.isdigit())
    return digits[-6:] if len(digits) >= 6 else ""
