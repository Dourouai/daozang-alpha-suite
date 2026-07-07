from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from beichen_alpha.strategy.playbook import (
    classify_buy_plan_strategy,
    classify_holding_strategy,
    classify_recommendation_strategy,
)


BUY_NOW_SMALL = "BUY_NOW_SMALL"
BUY_WATCH = "BUY_WATCH"
PULLBACK_WATCH = "PULLBACK_WATCH"
HOLD = "HOLD"
REDUCE = "REDUCE"
EXIT = "EXIT"
NO_TRADE = "NO_TRADE"
PAUSE_NEW_BUY = "PAUSE_NEW_BUY"


@dataclass(frozen=True)
class FinalAction:
    action: str
    confidence: str
    reason: str
    sizing_hint: str = ""


def decide_recommendation_action(item: Any, realtime_check: Any | None = None) -> FinalAction:
    """Translate recommendation signals into a single executable action label."""

    profile = classify_recommendation_strategy(item, realtime_check)
    status = str(getattr(item, "status", "") or "")
    score = _int(getattr(item, "candidate_score", None) or getattr(item, "score", None))
    model_pct_rank = _float(getattr(item, "model_pct_rank", None))
    realtime_status = "" if realtime_check is None else str(getattr(realtime_check, "status", "") or "")
    execution_score = _int(getattr(realtime_check, "execution_score", None)) if realtime_check is not None else 0

    if status in {"排除", "失效", "偏离"} or profile["expectancy_style"] == "不交易":
        return FinalAction(NO_TRADE, "高", profile["note"], "不占用资金")

    if profile["id"] == "pullback_reversal":
        return FinalAction(
            PULLBACK_WATCH,
            _confidence_from_score(score, model_pct_rank),
            "低吸候选只等承接确认；未站回VWAP/关键价前不提前买。",
            "先小仓验证",
        )

    if realtime_status == "实时可买" and execution_score >= 45 and score >= 90:
        return FinalAction(
            BUY_NOW_SMALL,
            _confidence_from_score(score, model_pct_rank),
            "盘中站稳确认价且执行分达标，可小仓试错。",
            "小仓，不追高",
        )

    if status in {"可执行", "条件执行", "突破", "观察"}:
        return FinalAction(
            BUY_WATCH,
            _confidence_from_score(score, model_pct_rank),
            "候选有效，但仍需盘中站稳确认价、未过追高线后再执行。",
            "等待触发",
        )

    return FinalAction(NO_TRADE, "中", "买点尚未形成，先观察不占资金。", "不占用资金")


def decide_buy_plan_action(item: Any) -> FinalAction:
    profile = classify_buy_plan_strategy(item)
    status = str(getattr(item, "status", "") or "")
    score = _int(getattr(item, "candidate_score", None))
    model_pct_rank = _float(getattr(item, "model_pct_rank", None))

    if profile["expectancy_style"] == "不交易" or status in {"排除", "失效", "偏离"}:
        return FinalAction(NO_TRADE, "高", profile["note"], "不占用资金")
    if profile["id"] == "pullback_reversal":
        return FinalAction(
            PULLBACK_WATCH,
            _confidence_from_score(score, model_pct_rank),
            "回踩未破位，等待承接/VWAP修复；不做跌破后的低吸。",
            "先小仓验证",
        )
    if status in {"可执行", "条件执行", "突破", "观察"}:
        return FinalAction(
            BUY_WATCH,
            _confidence_from_score(score, model_pct_rank),
            "日频候选有效，等待盘中确认后执行。",
            "等待触发",
        )
    return FinalAction(NO_TRADE, "中", "买点尚未形成，先观察不占资金。", "不占用资金")


def decide_holding_action(item: Any) -> FinalAction:
    profile = classify_holding_strategy(item)
    action = str(getattr(item, "action", "") or "")
    pnl_pct = _float(getattr(item, "pnl_pct", None)) or 0.0
    model_pct_rank = _float(getattr(item, "model_pct_rank", None))

    if action == "退出优先":
        return FinalAction(EXIT, "高", "跌破止损线或硬风险触发，优先退出。", "释放资金")
    if action in {"减仓优先", "买点弱化", "时间止损优先"}:
        return FinalAction(
            REDUCE,
            "高" if model_pct_rank is not None and model_pct_rank < 0.30 else "中高",
            "持仓弱化或时间/模型条件不再支持继续占用资金。",
            "减仓或退出",
        )
    if action == "资金效率观察":
        return FinalAction(
            REDUCE if pnl_pct <= 0.003 else HOLD,
            "中",
            "资金效率偏低；若没有更强候选可先持有，有更强候选则释放。",
            "比较机会成本",
        )
    if action == "止盈优先":
        return FinalAction(REDUCE, "中高", "接近目标区，优先保护收益。", "分批止盈")
    if profile["id"] == "trend_hold":
        return FinalAction(HOLD, "中高", "已有浮盈且未跌回确认价，继续持有并上移保护。", "不加仓")
    return FinalAction(HOLD, "中", "未触发退出条件，继续按确认价和资金效率复核。", "不加仓")


def action_priority(action: str) -> int:
    return {
        BUY_NOW_SMALL: 5,
        BUY_WATCH: 4,
        PULLBACK_WATCH: 3,
        HOLD: 2,
        REDUCE: 1,
        EXIT: 0,
        NO_TRADE: -5,
        PAUSE_NEW_BUY: -8,
    }.get(action, 0)


def _confidence_from_score(score: int, model_pct_rank: float | None) -> str:
    model_bonus = 0
    if model_pct_rank is not None:
        if model_pct_rank >= 0.70:
            model_bonus = 12
        elif model_pct_rank < 0.30:
            model_bonus = -16
    adjusted = score + model_bonus
    if adjusted >= 150:
        return "高"
    if adjusted >= 110:
        return "中高"
    if adjusted >= 75:
        return "中"
    return "低"


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
