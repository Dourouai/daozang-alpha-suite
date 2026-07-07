from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from beichen_alpha.models import Recommendation, RealtimeCheck
from beichen_alpha.strategy.final_action import decide_recommendation_action
from beichen_alpha.strategy.playbook import (
    classify_buy_plan_strategy,
    classify_holding_strategy,
    classify_recommendation_strategy,
)
from beichen_alpha.strategy.trade_plan import BuyPlan, HoldingPlan, ThreeDayTradePlan


DEFAULT_DECISION_LOG_PATH = Path("data/decision_logs/recommendations.jsonl")
SCHEMA_VERSION = "decision-log-v1"


def append_decision_records(
    records: list[dict[str, Any]],
    path: str | Path = DEFAULT_DECISION_LOG_PATH,
) -> Path:
    log_path = Path(path)
    if not records:
        return log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return log_path


def read_decision_records(path: str | Path = DEFAULT_DECISION_LOG_PATH) -> list[dict[str, Any]]:
    log_path = Path(path)
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def make_run_id(kind: str, as_of: datetime, context: dict[str, Any] | None = None) -> str:
    payload = json.dumps(context or {}, ensure_ascii=False, sort_keys=True, default=str)
    suffix = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]
    return f"{kind}-{as_of.strftime('%Y%m%d%H%M%S')}-{suffix}"


def build_recommendation_decision_records(
    recommendations: list[Recommendation],
    *,
    as_of: datetime,
    run_kind: str,
    context: dict[str, Any] | None = None,
    realtime_checks: dict[str, RealtimeCheck] | None = None,
    logged_at: datetime | None = None,
) -> list[dict[str, Any]]:
    run_id = make_run_id(run_kind, as_of, context)
    stamp = (logged_at or datetime.now()).isoformat(timespec="seconds")
    return [
        recommendation_to_record(
            item,
            rank=index,
            run_id=run_id,
            as_of=as_of,
            run_kind=run_kind,
            context=context or {},
            realtime_check=(realtime_checks or {}).get(item.code),
            logged_at=stamp,
        )
        for index, item in enumerate(recommendations, 1)
    ]


def build_trade_plan_decision_records(
    plan: ThreeDayTradePlan,
    *,
    as_of: datetime,
    context: dict[str, Any] | None = None,
    logged_at: datetime | None = None,
) -> list[dict[str, Any]]:
    run_kind = "three_day_trade_plan"
    run_id = make_run_id(run_kind, as_of, context)
    stamp = (logged_at or datetime.now()).isoformat(timespec="seconds")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(plan.holding_plans, 1):
        records.append(
            holding_plan_to_record(
                item,
                rank=index,
                run_id=run_id,
                as_of=as_of,
                context=context or {},
                logged_at=stamp,
                plan=plan,
            )
        )
    for index, item in enumerate(plan.buy_plans, 1):
        records.append(
            buy_plan_to_record(
                item,
                rank=index,
                run_id=run_id,
                as_of=as_of,
                context=context or {},
                logged_at=stamp,
                plan=plan,
            )
        )
    return records


def recommendation_to_record(
    item: Recommendation,
    *,
    rank: int,
    run_id: str,
    as_of: datetime,
    run_kind: str,
    context: dict[str, Any],
    realtime_check: RealtimeCheck | None,
    logged_at: str,
) -> dict[str, Any]:
    final = decide_recommendation_action(item, realtime_check)
    record = base_record(
        run_id=run_id,
        run_kind=run_kind,
        decision_kind="candidate_recommendation",
        as_of=as_of,
        logged_at=logged_at,
        rank=rank,
        code=item.code,
        name=item.name,
        action=recommendation_action(item.status),
        status=item.status,
        context=context,
    )
    record.update(
        {
            "industry": item.industry,
            "themes": list(item.themes),
            "market_cap_billion": item.market_cap_billion,
            "horizon": item.holding_period,
            "prices": {
                "close": item.close,
                "observation_zone": item.observation_zone,
                "confirm": item.confirm_price,
                "stop": item.invalid_price,
                "target": item.take_profit_price,
                "trailing_stop": item.trailing_stop_price,
            },
            "scores": {
                "score": item.score,
                "candidate_score": item.candidate_score or item.score,
                "macro_event_score": item.macro_event_score,
                "model_pct_rank": item.model_pct_rank,
                "prediction": recommendation_prediction_to_dict(item),
            },
            "rationale": {
                "reason": item.reason,
                "candidate_breakdown": item.candidate_breakdown,
                "macro_events": item.macro_events,
                "market_temperature": item.market_temperature,
                "sector_rotation": item.sector_rotation,
                "risk_calendar": item.risk_calendar,
                "sell_plan": item.sell_plan,
            },
            "factor_scores": [factor_score_to_dict(score) for score in item.factor_scores],
            "strategy_profile": classify_recommendation_strategy(item, realtime_check),
            "final_action": {
                "action": final.action,
                "confidence": final.confidence,
                "reason": final.reason,
            },
            "risk": {
                "risk_text": item.risk,
                "stop": item.invalid_price,
                "risk_calendar": item.risk_calendar,
            },
            "outcome": {},
        }
    )
    if realtime_check is not None:
        record["execution"] = realtime_check_to_dict(realtime_check)
    return record


def factor_score_to_dict(item: Any) -> dict[str, Any]:
    return {
        "name": getattr(item, "name", ""),
        "score": getattr(item, "score", 0),
        "passed": bool(getattr(item, "passed", True)),
        "detail": getattr(item, "detail", ""),
        "group": factor_group_name(getattr(item, "name", "")),
    }


def factor_group_name(name: str) -> str:
    try:
        from beichen_alpha.strategy.engine import CANDIDATE_FACTOR_GROUPS

        return CANDIDATE_FACTOR_GROUPS.get(name, "")
    except Exception:
        return ""


def holding_plan_to_record(
    item: HoldingPlan,
    *,
    rank: int,
    run_id: str,
    as_of: datetime,
    context: dict[str, Any],
    logged_at: str,
    plan: ThreeDayTradePlan,
) -> dict[str, Any]:
    record = base_record(
        run_id=run_id,
        run_kind="three_day_trade_plan",
        decision_kind="holding_review",
        as_of=as_of,
        logged_at=logged_at,
        rank=rank,
        code=item.code,
        name=item.name,
        action=item.action,
        status=item.action,
        context=context,
    )
    record.update(
        {
            "prices": {
                "current": item.price,
                "source": item.price_source,
                "cost": item.cost,
                "confirm": item.confirm,
                "stop": item.stop,
                "target": item.target,
            },
            "sizing": {
                "shares": item.shares,
                "cost_basis": item.cost * item.shares,
                "pnl": item.pnl,
                "pnl_pct": item.pnl_pct,
                "entry_date": item.entry_date,
                "holding_trade_days": item.holding_trade_days,
            },
            "scores": {
                "model_pct_rank": item.model_pct_rank,
                "prediction": plan_prediction_to_dict(item),
                "execution_score": item.execution_score,
                "release_score": item.release_score,
            },
            "portfolio": portfolio_context(plan),
            "rationale": {
                "trigger": item.trigger,
                "execution_detail": item.execution_detail,
                "release_reason": item.release_reason,
            },
            "factor_scores": [factor_score_to_dict(score) for score in item.factor_scores],
            "execution": plan_execution_to_dict(item),
            "strategy_profile": classify_holding_strategy(item),
            "final_action": {
                "action": item.final_action,
                "confidence": item.action_confidence,
                "reason": item.action_reason,
            },
            "risk": {
                "stop": item.stop,
                "target": item.target,
            },
            "outcome": {},
        }
    )
    return record


def buy_plan_to_record(
    item: BuyPlan,
    *,
    rank: int,
    run_id: str,
    as_of: datetime,
    context: dict[str, Any],
    logged_at: str,
    plan: ThreeDayTradePlan,
) -> dict[str, Any]:
    record = base_record(
        run_id=run_id,
        run_kind="three_day_trade_plan",
        decision_kind="trade_plan_buy",
        as_of=as_of,
        logged_at=logged_at,
        rank=rank,
        code=item.code,
        name=item.name,
        action="conditional_buy",
        status=item.status,
        context=context,
    )
    record.update(
        {
            "group": item.group,
            "prices": {
                "close": item.close,
                "source": item.price_source,
                "confirm": item.confirm,
                "stop": item.stop,
                "target": item.target,
                "chase_limit": round(item.confirm * 1.012, 2),
            },
            "scores": {
                "candidate_score": item.candidate_score,
                "model_pct_rank": item.model_pct_rank,
                "prediction": plan_prediction_to_dict(item),
                "execution_score": item.execution_score,
            },
            "sizing": {
                "lot_cost": item.lot_cost,
                "max_lots": item.max_lots,
            },
            "portfolio": portfolio_context(plan),
            "rationale": {
                "trigger": item.trigger,
                "execution_detail": item.execution_detail,
            },
            "factor_scores": [factor_score_to_dict(score) for score in item.factor_scores],
            "execution": plan_execution_to_dict(item),
            "strategy_profile": classify_buy_plan_strategy(item),
            "final_action": {
                "action": item.final_action,
                "confidence": item.action_confidence,
                "reason": item.action_reason,
            },
            "risk": {
                "stop": item.stop,
                "risk_text": item.risk,
            },
            "outcome": {},
        }
    )
    return record


def recommendation_prediction_to_dict(item: Recommendation) -> dict[str, Any]:
    if item.calibration_up_prob is None:
        return {"available": False, "reason": "样本不足"}
    return {
        "available": True,
        "horizon_days": 3,
        "up_probability": item.calibration_up_prob,
        "avg_return": item.calibration_avg_return,
        "median_return": item.calibration_median_return,
        "target_hit_probability": item.calibration_target_hit_prob,
        "stop_hit_probability": item.calibration_stop_hit_prob,
        "confidence": item.calibration_confidence,
        "sample_count": item.calibration_sample_count,
        "source": "same_stock_historical_shape",
        "detail": item.calibration_detail,
    }


def plan_prediction_to_dict(item: HoldingPlan | BuyPlan) -> dict[str, Any]:
    if item.prediction_up_prob is None:
        return {"available": False, "reason": "样本不足"}
    return {
        "available": True,
        "horizon_days": 3,
        "up_probability": item.prediction_up_prob,
        "avg_return": item.prediction_avg_return,
        "median_return": item.prediction_median_return,
        "target_hit_probability": item.prediction_target_hit_prob,
        "stop_hit_probability": item.prediction_stop_hit_prob,
        "confidence": item.prediction_confidence,
        "sample_count": item.prediction_sample_count,
        "source": "same_stock_historical_shape",
        "detail": item.prediction_detail,
    }


def plan_execution_to_dict(item: HoldingPlan | BuyPlan) -> dict[str, Any]:
    return {
        "price_source": item.price_source,
        "execution_score": item.execution_score,
        "detail": item.execution_detail,
    }


def base_record(
    *,
    run_id: str,
    run_kind: str,
    decision_kind: str,
    as_of: datetime,
    logged_at: str,
    rank: int,
    code: str,
    name: str,
    action: str,
    status: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "run_kind": run_kind,
        "decision_kind": decision_kind,
        "logged_at": logged_at,
        "as_of": as_of.isoformat(timespec="seconds"),
        "rank": rank,
        "code": code,
        "name": name,
        "action": action,
        "status": status,
        "context": context,
    }


def recommendation_action(status: str) -> str:
    if status in {"可执行", "条件执行", "突破", "观察"}:
        return "watch_buy"
    if status == "偏离":
        return "wait_pullback"
    if status == "排除":
        return "exclude"
    return "review"


def realtime_check_to_dict(check: RealtimeCheck) -> dict[str, Any]:
    return {
        "status": check.status,
        "price": check.price,
        "gap_to_confirm_pct": check.gap_to_confirm_pct,
        "chase_limit_price": check.chase_limit_price,
        "quote_time": check.quote_time.isoformat(timespec="seconds") if check.quote_time else None,
        "amount_billion": check.amount_billion,
        "execution_score": check.execution_score,
        "execution_breakdown": check.execution_breakdown,
        "sector_confirmation": check.sector_confirmation,
        "detail": check.detail,
    }


def portfolio_context(plan: ThreeDayTradePlan) -> dict[str, Any]:
    return {
        "capital": plan.capital,
        "invested_cost": plan.invested_cost,
        "available_cash": plan.available_cash,
        "rotation_cash": plan.rotation_cash,
        "risk_posture": {
            "label": plan.risk_posture,
            "new_buy_budget_scale": plan.new_buy_budget_scale,
            "candidate_failure_ratio": plan.candidate_failure_ratio,
            "candidate_executable_count": plan.candidate_executable_count,
            "candidate_failed_count": plan.candidate_failed_count,
        },
        "model_scores": {
            "trade_date": plan.model_score_trade_date,
            "rows": plan.model_score_rows,
            "covered": plan.model_score_covered,
            "missing": list(plan.model_score_missing),
            "stale": plan.model_score_stale,
            "detail": plan.model_score_note,
        },
        "notes": list(plan.notes),
    }
