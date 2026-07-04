from __future__ import annotations

from beichen_alpha.strategy.trade_plan import ThreeDayTradePlan


def render_three_day_trade_plan(plan: ThreeDayTradePlan) -> str:
    lines = [
        "北辰 Alpha｜3日短线交易计划",
        "",
        "账户状态",
        f"- 总资金: {plan.capital:.2f}",
        f"- 已占用成本: {plan.invested_cost:.2f}",
        f"- 可用现金估算: {plan.available_cash:.2f}",
        f"- 单笔预算上限: {plan.max_trade_cash:.2f}",
        "",
        "已有持仓",
    ]
    if not plan.holding_plans:
        lines.append("- 当前无持仓。")
    for item in plan.holding_plans:
        lines.extend(
            [
                (
                    f"- {item.name} {item.code}: {item.action} | "
                    f"现价 {item.price:.2f} 成本 {item.cost:.2f} "
                    f"盈亏 {item.pnl:+.2f} ({item.pnl_pct:+.2%})"
                ),
                f"  确认 {item.confirm:.2f} | 止损 {item.stop:.2f} | 目标 {item.target:.2f}",
                f"  触发: {item.trigger}",
            ]
        )

    lines.extend(["", "下周3支观察买入候选"])
    if not plan.buy_plans:
        lines.append("- 当前现金/一手约束下无合适新增候选。")
    for index, item in enumerate(plan.buy_plans, 1):
        model_text = "-" if item.model_pct_rank is None else f"{item.model_pct_rank:.1%}"
        target = "-" if item.target is None else f"{item.target:.2f}"
        lines.extend(
            [
                (
                    f"{index}. {item.name} {item.code} | {item.group} | {item.status} | "
                    f"候选分 {item.candidate_score} | 道藏分位 {model_text}"
                ),
                (
                    f"   收盘 {item.close:.2f} | 确认 {item.confirm:.2f} | "
                    f"止损 {item.stop:.2f} | 目标 {target}"
                ),
                (
                    f"   一手约 {item.lot_cost:.0f} 元 | 单票最多 {item.max_lots} 手 | "
                    f"{item.trigger}"
                ),
                f"   风控: {item.risk}",
            ]
        )

    lines.extend(["", "执行纪律"])
    lines.extend(f"- {note}" for note in plan.notes)
    lines.append("- 仅用于个人研究和策略测试，不构成投资建议。")
    return "\n".join(lines)
