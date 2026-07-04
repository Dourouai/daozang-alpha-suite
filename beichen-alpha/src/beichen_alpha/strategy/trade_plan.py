from __future__ import annotations

import csv
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beichen_alpha.models import Recommendation


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
    pnl: float
    pnl_pct: float
    action: str
    trigger: str


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


@dataclass(frozen=True)
class ThreeDayTradePlan:
    capital: float
    invested_cost: float
    available_cash: float
    max_trade_cash: float
    holding_plans: tuple[HoldingPlan, ...]
    buy_plans: tuple[BuyPlan, ...]
    notes: tuple[str, ...]


def build_three_day_trade_plan(
    recommendations: list[Recommendation],
    positions: list[dict[str, Any]],
    capital: float = 10000.0,
    top_n: int = 3,
    lot_size: int = 100,
    max_trade_pct: float = 0.35,
    model_scores: dict[str, float] | None = None,
) -> ThreeDayTradePlan:
    by_code = {item.code: item for item in recommendations}
    held_codes = {str(item["code"]) for item in positions}
    invested_cost = sum(float(item["cost"]) * int(item["shares"]) for item in positions)
    available_cash = max(capital - invested_cost, 0.0)
    max_trade_cash = max(capital * max_trade_pct, 0.0)

    holding_plans = tuple(
        build_holding_plan(item, by_code.get(str(item["code"])))
        for item in positions
    )
    held_groups = {infer_trade_group(plan.name) for plan in holding_plans}
    candidates = [
        build_buy_plan(
            item,
            available_cash=available_cash,
            max_trade_cash=max_trade_cash,
            lot_size=lot_size,
            model_pct_rank=(model_scores or {}).get(item.code),
        )
        for item in recommendations
        if item.code not in held_codes and item.status in {"可执行", "条件执行", "突破", "观察"}
    ]
    candidates = [item for item in candidates if item.max_lots >= 1]
    selected = choose_buy_plans(
        candidates,
        top_n=top_n,
        cash_limit=available_cash,
        held_groups=held_groups,
    )
    notes = (
        "3天短线：候选不等于买入，必须盘中站上确认价且不超过追高区。",
        "已有持仓周一可以卖出/减仓；只有周一新买入部分受T+1限制。",
        "若当前持仓跌回确认价下方且收不回，优先释放仓位，再执行新候选。",
        "若持仓短线弹性不足、资金效率低，10:30 前不放量脱离成本区则考虑轮动。",
    )
    return ThreeDayTradePlan(
        capital=capital,
        invested_cost=invested_cost,
        available_cash=available_cash,
        max_trade_cash=max_trade_cash,
        holding_plans=holding_plans,
        buy_plans=tuple(selected),
        notes=notes,
    )


def build_holding_plan(position: dict[str, Any], recommendation: Recommendation | None) -> HoldingPlan:
    code = str(position["code"])
    name = str(position.get("name") or code)
    shares = int(position["shares"])
    cost = float(position["cost"])
    confirm = float(position["confirm"])
    stop = float(position["invalid"])
    target = float(position["target"])
    price = recommendation.close if recommendation is not None else cost
    pnl = (price - cost) * shares
    pnl_pct = price / cost - 1 if cost else 0.0

    if price <= 0:
        action = "等待行情"
        trigger = "行情缺失，不做主动操作。"
    elif price < stop:
        action = "退出优先"
        trigger = f"低于止损线 {stop:.2f}，已有仓位可按风控减仓/退出。"
    elif price < confirm:
        action = "买点弱化"
        trigger = f"跌回确认价 {confirm:.2f} 下方，若盘中/收盘收不回，优先减仓。"
    elif price >= target:
        action = "止盈优先"
        trigger = f"到达目标价 {target:.2f} 附近，分批止盈或上移保护线。"
    elif has_low_capital_efficiency(recommendation, pnl_pct, price, confirm):
        action = "资金效率观察"
        trigger = (
            f"仍在确认价 {confirm:.2f} 上方，但近期短线弹性不足且仍在成本附近；"
            "周一若10:30前不放量脱离成本/确认区，考虑减仓释放资金，轮动到更强候选。"
        )
    else:
        action = "继续持有"
        trigger = f"仍在确认价 {confirm:.2f} 上方，继续观察；不加仓。"

    return HoldingPlan(
        code=code,
        name=name,
        shares=shares,
        cost=cost,
        price=price,
        confirm=confirm,
        stop=stop,
        target=target,
        pnl=pnl,
        pnl_pct=pnl_pct,
        action=action,
        trigger=trigger,
    )


def has_low_capital_efficiency(
    recommendation: Recommendation | None,
    pnl_pct: float,
    price: float,
    confirm: float,
) -> bool:
    if recommendation is None:
        return False
    if abs(pnl_pct) > 0.015:
        return False
    if "短线弹性" in recommendation.risk:
        return True
    if confirm <= 0:
        return False
    confirm_gap = price / confirm - 1
    return price >= confirm and confirm_gap <= 0.006


def build_buy_plan(
    recommendation: Recommendation,
    available_cash: float,
    max_trade_cash: float,
    lot_size: int,
    model_pct_rank: float | None,
) -> BuyPlan:
    lot_cost = recommendation.close * lot_size
    budget = min(available_cash, max_trade_cash)
    max_lots = int(budget // lot_cost) if lot_cost > 0 else 0
    target = recommendation.take_profit_price
    chase_line = recommendation.confirm_price * 1.012
    trigger = (
        f"仅在放量站上确认价 {recommendation.confirm_price:.2f} 后买入；"
        f"高于 {chase_line:.2f} 不追。"
    )
    risk = (
        f"跌破 {recommendation.invalid_price:.2f} 触发风控；"
        "第3个交易日仍不延续则降仓/退出。"
    )
    return BuyPlan(
        code=recommendation.code,
        name=recommendation.name,
        status=recommendation.status,
        group=infer_trade_group(recommendation.name),
        close=recommendation.close,
        confirm=recommendation.confirm_price,
        stop=recommendation.invalid_price,
        target=target,
        candidate_score=recommendation.candidate_score or recommendation.score,
        lot_cost=lot_cost,
        max_lots=max_lots,
        model_pct_rank=model_pct_rank,
        trigger=trigger,
        risk=risk,
    )


def choose_buy_plans(
    candidates: list[BuyPlan],
    top_n: int,
    cash_limit: float,
    held_groups: set[str],
) -> list[BuyPlan]:
    if top_n <= 0 or not candidates:
        return []
    best: tuple[float, int, float, tuple[BuyPlan, ...]] | None = None
    max_size = min(top_n, len(candidates))
    for size in range(1, max_size + 1):
        for combo in itertools.combinations(candidates, size):
            lot_cost = sum(item.lot_cost for item in combo)
            if lot_cost > cash_limit:
                continue
            utility = score_combo(combo, held_groups)
            raw_score = sum(item.candidate_score for item in combo)
            tie = (utility, raw_score, -lot_cost, combo)
            if best is None or tie[:3] > best[:3]:
                best = tie
    if best is None:
        return []
    selected = list(best[3])
    return sorted(selected, key=lambda item: candidate_utility(item, held_groups), reverse=True)


def score_combo(combo: tuple[BuyPlan, ...], held_groups: set[str]) -> float:
    groups = [item.group for item in combo]
    duplicate_penalty = (len(groups) - len(set(groups))) * 14
    return sum(candidate_utility(item, held_groups) for item in combo) - duplicate_penalty


def candidate_utility(item: BuyPlan, held_groups: set[str]) -> float:
    status_bonus = {
        "可执行": 18,
        "条件执行": 12,
        "突破": 8,
        "观察": 0,
    }.get(item.status, 0)
    held_group_penalty = 16 if item.group in held_groups else 0
    model_bonus = (item.model_pct_rank or 0.0) * 18
    return item.candidate_score + status_bonus + model_bonus - held_group_penalty


def infer_trade_group(name: str) -> str:
    if any(key in name for key in ("银行",)):
        return "银行"
    if any(key in name for key in ("证券", "财富", "人保", "平安")):
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
    if any(key in name for key in ("医药", "恒瑞", "药明", "百济")):
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
            instrument = str(row.get("instrument") or "")
            code = instrument[-6:]
            try:
                result[code] = float(row.get("pct_rank") or 0.0)
            except ValueError:
                continue
    return result
