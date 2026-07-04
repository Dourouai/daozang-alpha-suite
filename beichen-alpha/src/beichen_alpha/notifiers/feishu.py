from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request
from collections import Counter
from datetime import datetime

from beichen_alpha.models import Recommendation, RealtimeCheck


def render_feishu_recommendations(
    rows: list[Recommendation],
    title: str = "北辰 Alpha 每日候选池",
    as_of: datetime | None = None,
    realtime_checks: dict[str, RealtimeCheck] | None = None,
) -> str:
    date_text = (as_of or datetime.now()).strftime("%Y-%m-%d %H:%M")
    lines = [f"{title}", f"时间: {date_text}", ""]
    if not rows:
        lines.append("今日无候选。")
    for index, item in enumerate(rows, 1):
        market_cap = "-" if item.market_cap_billion is None else f"{item.market_cap_billion:.0f}亿"
        take_profit = "-" if item.take_profit_price is None else f"{item.take_profit_price:.2f}"
        trailing_stop = "-" if item.trailing_stop_price is None else f"{item.trailing_stop_price:.2f}"
        lines.append(
            (
                f"{index}. {item.name} {item.code}｜{item.status}｜候选 {item.candidate_score or item.score}｜"
                f"收盘 {item.close:.2f}｜确认 {item.confirm_price:.2f}｜止盈 {take_profit}｜失效 {item.invalid_price:.2f}"
            )
        )
        realtime = (realtime_checks or {}).get(item.code)
        if realtime is not None:
            price = "-" if realtime.price is None else f"{realtime.price:.2f}"
            gap = "-" if realtime.gap_to_confirm_pct is None else f"{realtime.gap_to_confirm_pct:+.2f}%"
            quote_time = "-" if realtime.quote_time is None else realtime.quote_time.strftime("%Y-%m-%d %H:%M:%S")
            amount = "-" if realtime.amount_billion is None else f"{realtime.amount_billion:.1f}亿"
            lines.append(
                f"   实时: {realtime.status}｜执行 {realtime.execution_score}｜现价 {price}｜距确认 {gap}｜追高线 {realtime.chase_limit_price:.2f}｜成交额 {amount}"
            )
            lines.append(f"   行情时间: {quote_time}｜{realtime.execution_breakdown or realtime.detail}")
        lines.append(
            f"   行业: {item.industry or '-'}｜市值: {market_cap}｜"
            f"市场: {item.market_temperature or '-'}｜轮动: {item.sector_rotation or '-'}｜"
            f"风控: {item.risk_calendar or '-'}"
        )
        if item.macro_events:
            lines.append(f"   宏观: {item.macro_events}")
        lines.append(f"   观察区: {item.observation_zone}｜持有: {item.holding_period}｜移动止损: {trailing_stop}")
        if item.sell_plan:
            lines.append(f"   卖出计划: {item.sell_plan}")
        lines.append(f"   {item.candidate_breakdown or item.reason}")
    lines.append("")
    lines.append("仅用于个人研究，不构成投资建议。")
    return "\n".join(lines)


def render_feishu_recommendations_card(
    rows: list[Recommendation],
    title: str = "北辰 Alpha 每日候选池",
    as_of: datetime | None = None,
    realtime_checks: dict[str, RealtimeCheck] | None = None,
) -> dict:
    now = as_of or datetime.now()
    realtime_values = [check for check in (realtime_checks or {}).values()]
    counts = Counter(check.status for check in realtime_values)
    buyable_count = counts.get("实时可买", 0)
    quote_time = latest_quote_time(realtime_values)
    template = "green" if buyable_count else "blue"
    subtitle = f"{now.strftime('%Y-%m-%d %H:%M')}"
    if quote_time:
        subtitle += f" | 行情 {quote_time.strftime('%H:%M:%S')}"

    elements = [
        div(
            "\n".join(
                [
                    f"**{subtitle}**",
                    f"实时可买 **{buyable_count}** 只 | 待站稳 **{counts.get('待站稳', 0)}** 只 | 板块未共振 **{counts.get('板块未共振', 0)}** 只",
                    f"周五观察 **{counts.get('周五观察', 0)}** 只",
                    f"贴线观察 **{counts.get('贴线观察', 0)}** 只 | 接近确认 **{counts.get('接近确认', 0)}** 只 | 候选 **{len(rows)}** 只",
                    "执行窗口：10:00-10:30；只看连续站稳确认价且未超过追高线的标的。",
                    "测试纪律：总仓先不超过 3000 元；高于追高线不追；买入后按A股T+1执行风控。",
                ]
            )
        ),
        {"tag": "hr"},
    ]

    if not rows:
        elements.append(div("今日无候选。"))
    for index, item in enumerate(rows, 1):
        check = (realtime_checks or {}).get(item.code)
        elements.append(div(render_card_row(index, item, check)))
        if index < len(rows):
            elements.append({"tag": "hr"})

    elements.append(
        div("提示：A股普通股票T+1，买入当日不可卖出；盘中失效只记录为次交易日处理计划。")
    )
    elements.append(div("仅用于个人研究和策略测试，不构成投资建议。"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def render_card_row(index: int, item: Recommendation, check: RealtimeCheck | None) -> str:
    realtime_status = check.status if check else "无实时"
    price = "-" if check is None or check.price is None else f"{check.price:.2f}"
    gap = "-" if check is None or check.gap_to_confirm_pct is None else f"{check.gap_to_confirm_pct:+.2f}%"
    chase = "-" if check is None else f"{check.chase_limit_price:.2f}"
    take_profit = "-" if item.take_profit_price is None else f"{item.take_profit_price:.2f}"
    market_cap = "-" if item.market_cap_billion is None else f"{item.market_cap_billion:.0f}亿"
    candidate_score = item.candidate_score or item.score
    execution_score = "-" if check is None else str(check.execution_score)
    return "\n".join(
        [
            f"**{index}. {item.name} {item.code}** | **{realtime_status}** | 候选 {candidate_score} | 执行 {execution_score}",
            f"现价 {price} | 确认 {item.confirm_price:.2f} | 距确认 {gap} | 追高线 {chase}",
            f"失效 {item.invalid_price:.2f} | 目标 {take_profit} | 行业 {item.industry or '-'} | 市值 {market_cap}",
            f"宏观: {item.macro_events}" if item.macro_events else "",
            item.candidate_breakdown,
            f"执行拆分: {check.execution_breakdown}" if check and check.execution_breakdown else "",
            f"{check.sector_confirmation}" if check and check.sector_confirmation else "",
        ]
    ).strip()


def div(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def latest_quote_time(checks: list[RealtimeCheck]) -> datetime | None:
    times = [check.quote_time for check in checks if check.quote_time is not None]
    if not times:
        return None
    return max(times)


def build_text_payload(text: str, secret: str = "", timestamp: int | None = None) -> dict:
    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }
    if secret:
        stamp = int(timestamp if timestamp is not None else time.time())
        payload["timestamp"] = str(stamp)
        payload["sign"] = make_sign(secret, stamp)
    return payload


def build_card_payload(card: dict, secret: str = "", timestamp: int | None = None) -> dict:
    payload = {
        "msg_type": "interactive",
        "card": card,
    }
    if secret:
        stamp = int(timestamp if timestamp is not None else time.time())
        payload["timestamp"] = str(stamp)
        payload["sign"] = make_sign(secret, stamp)
    return payload


def make_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_text(text: str, webhook: str | None = None, secret: str | None = None) -> dict:
    payload = build_text_payload(text, secret=secret or os.environ.get("FEISHU_SECRET", ""))
    return send_payload(payload, webhook=webhook)


def send_card(card: dict, webhook: str | None = None, secret: str | None = None) -> dict:
    payload = build_card_payload(card, secret=secret or os.environ.get("FEISHU_SECRET", ""))
    return send_payload(payload, webhook=webhook)


def send_payload(payload: dict, webhook: str | None = None, max_attempts: int = 2) -> dict:
    target = webhook or os.environ.get("FEISHU_WEBHOOK", "")
    if not target:
        raise RuntimeError("FEISHU_WEBHOOK is required")

    last_result: dict = {}
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            target,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8", errors="replace")
        try:
            last_result = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        if last_result.get("code") != 11232 or attempt >= max_attempts:
            return last_result
        time.sleep(30)
    return last_result
