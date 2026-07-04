from __future__ import annotations

from beichen_alpha.models import Recommendation, RealtimeCheck


def render_table(rows: list[Recommendation], realtime_checks: dict[str, RealtimeCheck] | None = None) -> str:
    if not rows:
        return (
            "无候选。可能是过滤条件过严，或候选都被公告/新闻风险排除；"
            "可加 --include-excluded 查看被排除原因，或先用 --disable-news --disable-disclosures 快速试跑。"
        )

    headers = [
        "排名",
        "代码",
        "名称",
        "行业",
        "市值",
        "候选分",
        "状态",
        *([] if realtime_checks is None else ["执行分", "实时", "实时价", "距确认", "共振", "行情时间"]),
        "市场",
        "宏观",
        "轮动",
        "风控",
        "收盘",
        "观察区",
        "确认价",
        "失效线",
        "止盈",
        "持有",
        "T+1处理计划",
        "理由",
    ]
    data = []
    for index, item in enumerate(rows, 1):
        realtime = (realtime_checks or {}).get(item.code)
        row = [
            str(index),
            item.code,
            item.name,
            item.industry or "-",
            "-" if item.market_cap_billion is None else f"{item.market_cap_billion:.0f}亿",
            str(item.candidate_score or item.score),
            item.status,
        ]
        if realtime_checks is not None:
            row.extend(
                [
                    "-" if realtime is None else str(realtime.execution_score),
                    realtime.status if realtime else "行情缺失",
                    "-" if realtime is None or realtime.price is None else f"{realtime.price:.2f}",
                    "-" if realtime is None or realtime.gap_to_confirm_pct is None else f"{realtime.gap_to_confirm_pct:+.2f}%",
                    "-" if realtime is None else realtime.sector_confirmation or "-",
                    format_quote_time(realtime) if realtime else "-",
                ]
            )
        row.extend(
            [
                item.market_temperature or "-",
                item.macro_events or "-",
                item.sector_rotation or "-",
                item.risk_calendar or "-",
                f"{item.close:.2f}",
                item.observation_zone,
                f"{item.confirm_price:.2f}",
                f"{item.invalid_price:.2f}",
                "-" if item.take_profit_price is None else f"{item.take_profit_price:.2f}",
                item.holding_period,
                item.sell_plan or "-",
                item.candidate_breakdown or item.reason,
            ]
        )
        data.append(row)
    widths = [display_width(value) for value in headers]
    for row in data:
        widths = [max(width, display_width(value)) for width, value in zip(widths, row)]

    lines = [format_row(headers, widths)]
    lines.append(format_row(["-" * width for width in widths], widths))
    lines.extend(format_row(row, widths) for row in data)
    return "\n".join(lines)


def format_row(row: list[str], widths: list[int]) -> str:
    return "  ".join(value + " " * (width - display_width(value)) for value, width in zip(row, widths))


def display_width(value: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in value)


def format_quote_time(check: RealtimeCheck) -> str:
    if check.quote_time is None:
        return "-"
    return check.quote_time.strftime("%H:%M:%S")
