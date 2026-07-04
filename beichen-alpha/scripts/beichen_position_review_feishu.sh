#!/bin/zsh
set -euo pipefail

cd /Users/yancy/Documents/vibe-project/daozang-alpha-suite/beichen-alpha

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p logs

if [ -f "config/local.env" ]; then
  set -a
  source "config/local.env"
  set +a
fi

if [ -z "${FEISHU_WEBHOOK:-}" ]; then
  echo "FEISHU_WEBHOOK is not configured. Edit config/local.env first." >&2
  exit 2
fi

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 - <<'PY'
import json
import os
from datetime import datetime
from pathlib import Path

from beichen_alpha.data_sources import DefaultMarketDataRouter, QlibBinPriceSource
from beichen_alpha.notifiers import send_card
from beichen_alpha.strategy.return_calibration import (
    calibrate_position_return,
    format_return_calibration,
)


def div(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def classify(price: float, confirm: float, invalid: float, target: float) -> tuple[str, str]:
    if price <= 0:
        return "行情缺失", "grey"
    if price < invalid:
        return "T+1硬风控", "red"
    if price < confirm:
        return "买点弱化", "orange"
    if price >= target:
        return "触及目标", "green"
    return "持仓有效", "green"


def action_plan(price: float, confirm: float, invalid: float, target: float) -> str:
    if price <= 0:
        return "行情缺失，暂停判断，等待下一次行情刷新。"
    if price < invalid:
        return "跌破止损线，按A股T+1制定次交易日优先减仓/退出计划。"
    if price < confirm:
        return "跌回确认价下方，买点弱化；今天不加仓，收盘仍弱则次交易日处理。"
    if price >= target:
        return "触及目标价，按计划分批止盈或上移保护线。"
    return "站在确认价上方，继续持有观察；不追高加仓，等待放量或趋势延续。"


def evaluated_action(price: float, cost: float, confirm: float, invalid: float, target: float) -> tuple[str, float, str]:
    if price <= 0:
        return "暂停判断，等待行情刷新", 0, "行情缺失"
    if price < invalid:
        return "次交易日优先减仓/退出", 25, "已跌破止损线"
    if price < confirm:
        return "买点弱化，不加仓，等待收复确认价", 42, "跌回确认价下方"
    if price >= target:
        return "触及目标，分批止盈或上移保护线", 62, "已到达目标区"

    risk = max(price - invalid, 0.01)
    reward = max(target - price, 0.0)
    reward_risk = reward / risk
    confirm_buffer = (price - confirm) / max(confirm - invalid, 0.01)

    pnl_pct = (price / cost - 1) * 100 if cost > 0 else 0.0
    probability = 50.0
    probability += max(-6.0, min(8.0, confirm_buffer * 25))
    probability += max(-8.0, min(8.0, (reward_risk - 1) * 8))
    probability += max(-5.0, min(5.0, pnl_pct * 1.5))
    probability = max(35.0, min(65.0, probability))
    if confirm_buffer < 0.10:
        action = "继续持有，但属于弱确认，不加仓"
        reason = "仅略高于确认价"
    else:
        action = "继续持有，等待趋势延续"
        reason = "站在确认价上方"
    return action, probability, reason


def reward_risk_text(price: float, invalid: float, target: float) -> str:
    if price <= 0:
        return "-"
    risk = price - invalid
    reward = target - price
    if risk <= 0:
        return "止损线已破"
    return f"{reward / risk:.2f}"


def quote_health_text(quote) -> str:
    if quote is None:
        return "行情源：-"
    latency = f"{quote.latency_ms:.0f}ms" if quote.latency_ms is not None else "-"
    return f"行情源：{quote.source} | 延迟 {latency}"


def money(value: float) -> str:
    return f"{value:+.2f}"


payload = json.loads(Path("data/positions/current_positions.json").read_text(encoding="utf-8"))
positions = payload.get("positions", [])
router = DefaultMarketDataRouter(item["code"] for item in positions)
quotes = router.load()
qlib_provider_uri = Path(os.getenv("BEICHEN_QLIB_PROVIDER_URI", "../daozang-alpha/data/qlib/cn_data"))
historical_prices = (
    QlibBinPriceSource(qlib_provider_uri, (item["code"] for item in positions)).load()
    if qlib_provider_uri.exists()
    else {}
)
now = datetime.now()

rows = []
statuses = []
total_cost = 0.0
total_value = 0.0
total_target_value = 0.0
total_invalid_value = 0.0
weighted_probability = 0.0
weighted_calibrated_up_probability = 0.0
weighted_calibrated_avg_return = 0.0
calibration_weight = 0.0
quote_times = []

for item in positions:
    code = item["code"]
    quote = quotes.get(code)
    price = quote.price if quote else 0.0
    shares = int(item["shares"])
    cost = float(item["cost"])
    confirm = float(item["confirm"])
    invalid = float(item["invalid"])
    target = float(item["target"])
    status, color = classify(price, confirm, invalid, target)
    statuses.append(status)
    if quote and quote.quote_time:
        quote_times.append(quote.quote_time)
    position_cost = cost * shares
    position_value = price * shares
    pnl = position_value - position_cost
    pnl_pct = pnl / position_cost * 100 if position_cost else 0.0
    total_cost += position_cost
    total_value += position_value
    total_target_value += target * shares
    total_invalid_value += invalid * shares
    target_pnl = (target - cost) * shares
    target_pnl_pct = target_pnl / position_cost * 100 if position_cost else 0.0
    invalid_pnl = (invalid - cost) * shares
    invalid_pnl_pct = invalid_pnl / position_cost * 100 if position_cost else 0.0
    current_to_target = (target - price) * shares if price > 0 else 0.0
    current_to_invalid = (invalid - price) * shares if price > 0 else 0.0
    conclusion, probability, reason = evaluated_action(price, cost, confirm, invalid, target)
    if probability > 0:
        weighted_probability += probability * position_cost
    calibration = calibrate_position_return(
        historical_prices.get(code, []),
        price,
        cost,
        confirm,
        invalid,
        target,
        horizon_days=5,
    )
    if calibration is not None:
        weighted_calibrated_up_probability += calibration.up_probability * position_cost
        weighted_calibrated_avg_return += calibration.avg_return * position_cost
        calibration_weight += position_cost
    rows.append(
        "\n".join(
            [
                f"**{item['name']} {code}** | **评估结论：{conclusion}**",
                (
                    f"有利情景评分：{probability:.1f}/100（规则估计，非胜率） | "
                    f"盈亏比 {reward_risk_text(price, invalid, target)} | {reason}"
                ),
                format_return_calibration(calibration),
                f"现价 {price:.2f} | 成本 {cost:.2f} | 浮盈亏 {pnl:+.2f} ({pnl_pct:+.2f}%)",
                f"确认 {confirm:.2f} | 止损线 {invalid:.2f} | 目标 {target:.2f} | 持仓 {shares}股",
                quote_health_text(quote),
                f"行情提示：{quote.warning}" if quote and quote.warning else "行情提示：正常",
                f"操作：{action_plan(price, confirm, invalid, target)}",
                (
                    f"计划盈亏：到目标 {money(target_pnl)} ({target_pnl_pct:+.2f}%)；"
                    f"跌到止损 {money(invalid_pnl)} ({invalid_pnl_pct:+.2f}%)"
                ),
                (
                    f"从现价测算：距目标 {money(current_to_target)}；"
                    f"到止损线还可承受 {money(current_to_invalid)}"
                ),
            ]
        )
    )

total_pnl = total_value - total_cost
total_pnl_pct = total_pnl / total_cost * 100 if total_cost else 0.0
total_target_pnl = total_target_value - total_cost
total_target_pnl_pct = total_target_pnl / total_cost * 100 if total_cost else 0.0
total_invalid_pnl = total_invalid_value - total_cost
total_invalid_pnl_pct = total_invalid_pnl / total_cost * 100 if total_cost else 0.0
portfolio_probability = weighted_probability / total_cost if total_cost else 0.0
portfolio_calibration_text = "组合历史校准：样本不足，暂不展示上涨概率。"
if calibration_weight:
    portfolio_calibration_text = (
        "组合历史校准："
        f"未来5日上涨 {weighted_calibrated_up_probability / calibration_weight:.0%}，"
        f"平均收益 {weighted_calibrated_avg_return / calibration_weight:+.2%}"
    )
portfolio_action = (
    "继续持有，弱确认"
    if portfolio_probability >= 50 and "T+1硬风控" not in statuses and "买点弱化" not in statuses
    else "降低预期，按规则处理弱项"
)
quote_time = max(quote_times).strftime("%H:%M:%S") if quote_times else "-"
template = "red" if "T+1硬风控" in statuses else "orange" if "买点弱化" in statuses else "green"
source_summary = "；".join(
    f"{item.source}:{'OK' if item.ok else 'FAIL'}({item.count})"
    for item in router.health
)

elements = [
    div(
        "\n".join(
            [
                f"**{payload.get('account', '测试账户')} | 10:30 持仓复核**",
                f"时间 {now.strftime('%Y-%m-%d %H:%M:%S')} | 行情 {quote_time}",
                f"行情路由：{source_summary or '-'}",
                (
                    f"组合评估：**{portfolio_action}** | "
                    f"有利情景评分 {portfolio_probability:.1f}/100（规则估计，非胜率）"
                ),
                portfolio_calibration_text,
                f"持仓市值 {total_value:.2f} | 持仓盈亏 {total_pnl:+.2f} ({total_pnl_pct:+.2f}%)",
                (
                    f"计划空间：目标合计 {money(total_target_pnl)} ({total_target_pnl_pct:+.2f}%)；"
                    f"跌到止损合计 {money(total_invalid_pnl)} ({total_invalid_pnl_pct:+.2f}%)"
                ),
                "纪律：A股T+1，今天买入今天不可卖；今天不加仓，只记录强弱和次交易日处理计划。",
                "若跌回确认价下方且站不回，记为买点弱化；若跌破止损线，记为次交易日优先减仓/退出。",
                "说明：计划盈亏按持仓成本和预设目标/止损线测算，不代表确定收益。",
            ]
        )
    ),
    {"tag": "hr"},
]
for index, row in enumerate(rows):
    elements.append(div(row))
    if index < len(rows) - 1:
        elements.append({"tag": "hr"})
elements.append(div("仅用于个人研究和策略测试，不构成投资建议。"))

card = {
    "config": {"wide_screen_mode": True},
    "header": {
        "template": template,
        "title": {"tag": "plain_text", "content": "北辰 Alpha 持仓复核"},
    },
    "elements": elements,
}

response = send_card(card)
if isinstance(response, dict) and response.get("code") not in (None, 0):
    raise SystemExit(f"Feishu returned an error: {response}")
print("Position review sent.")
PY
