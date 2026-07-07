from __future__ import annotations

from beichen_alpha.strategy.playbook import classify_buy_plan_strategy, classify_holding_strategy
from beichen_alpha.strategy.trade_plan import ThreeDayTradePlan


def render_three_day_trade_plan(plan: ThreeDayTradePlan) -> str:
    lines = [
        "北辰 Alpha｜交易计划",
        "",
        "账户状态",
        f"- 总资金: {plan.capital:.2f}",
        f"- 已占用成本: {plan.invested_cost:.2f}",
        f"- 可用现金估算: {plan.available_cash:.2f}",
        f"- 调仓轮动预算: {plan.rotation_cash:.2f}",
        (
            f"- 新开仓风控: {plan.risk_posture} | 预算系数 {plan.new_buy_budget_scale:.0%} | "
            f"候选失效率 {plan.candidate_failure_ratio:.0%} | 可执行 {plan.candidate_executable_count} 只"
        ),
        "",
        "已有持仓",
    ]
    if not plan.holding_plans:
        lines.append("- 当前无持仓。")
    for item in plan.holding_plans:
        entry_text = item.entry_date or "-"
        holding_days_text = "-" if item.holding_trade_days is None else f"第{item.holding_trade_days}个交易日"
        model_text = "模型未覆盖" if item.model_pct_rank is None else f"{item.model_pct_rank:.1%}"
        strategy = classify_holding_strategy(item)
        lines.extend(
            [
                (
                    f"- {item.name} {item.code}: {item.final_action or item.action} | "
                    f"置信 {item.action_confidence or '-'} | "
                    f"策略 {strategy['name']} | "
                    f"现价 {item.price:.2f}({item.price_source}) 成本 {item.cost:.2f} "
                    f"盈亏 {item.pnl:+.2f} ({item.pnl_pct:+.2%}) | 道藏 {model_text}"
                ),
                f"  确认 {item.confirm:.2f} | 止损 {item.stop:.2f} | 目标 {item.target:.2f}",
                f"  入场 {entry_text} | 持仓 {holding_days_text}",
                f"  释放分: {item.release_score}/100 | {item.release_reason or '-'}",
                f"  {item.execution_detail}",
                f"  概率预测: {format_prediction(item)}",
                f"  触发: {item.trigger}",
                f"  动作理由: {item.action_reason or '-'}",
            ]
        )

    buy_count = len(plan.buy_plans)
    lines.extend(["", f"新增观察买入候选 {buy_count} 支"])
    if not plan.buy_plans:
        lines.append("- 当前现金/一手约束下无合适新增候选。")
    for index, item in enumerate(plan.buy_plans, 1):
        model_text = "模型未覆盖" if item.model_pct_rank is None else f"{item.model_pct_rank:.1%}"
        target = "-" if item.target is None else f"{item.target:.2f}"
        strategy = classify_buy_plan_strategy(item)
        lines.extend(
            [
                (
                    f"{index}. {item.name} {item.code} | {item.group} | {item.status} | "
                    f"动作 {item.final_action or '-'} | 置信 {item.action_confidence or '-'} | "
                    f"策略 {strategy['name']} | "
                    f"候选分 {item.candidate_score} | 模型评分 {model_text}"
                ),
                (
                    f"   参考价 {item.close:.2f}({item.price_source}) | 确认 {item.confirm:.2f} | "
                    f"止损 {item.stop:.2f} | 目标 {target}"
                ),
                (
                    f"   一手约 {item.lot_cost:.0f} 元 | 单票最多 {item.max_lots} 手 | "
                    f"{item.trigger}"
                ),
                f"   {item.execution_detail}",
                f"   概率预测: {format_prediction(item)}",
                f"   动作理由: {item.action_reason or '-'}",
                f"   风控: {item.risk}",
            ]
        )

    lines.extend(["", "执行纪律"])
    lines.extend(f"- {note}" for note in plan.notes)
    lines.append("- 仅用于个人研究和策略测试，不构成投资建议。")
    return "\n".join(lines)


def format_prediction(item) -> str:
    if getattr(item, "prediction_up_prob", None) is None:
        return "样本不足，暂不展示概率。"
    avg_return = getattr(item, "prediction_avg_return", None)
    target_hit = getattr(item, "prediction_target_hit_prob", None)
    stop_hit = getattr(item, "prediction_stop_hit_prob", None)
    sample_count = getattr(item, "prediction_sample_count", 0)
    confidence = getattr(item, "prediction_confidence", "") or "-"
    return (
        f"未来3日上涨 {item.prediction_up_prob:.0%} | "
        f"期望收益 {format_pct(avg_return, signed=True)} | "
        f"目标触达 {format_pct(target_hit)} | "
        f"止损触碰 {format_pct(stop_hit)} | "
        f"样本 {sample_count}，置信 {confidence}"
    )


def format_pct(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:+.2%}" if signed else f"{value:.0%}"
