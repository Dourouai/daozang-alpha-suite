from __future__ import annotations

from beichen_alpha.models import GlobalIndicator, GlobalLinkageSnapshot

from .console import display_width, format_row


def render_global_linkage_report(snapshot: GlobalLinkageSnapshot) -> str:
    lines = [
        "北辰 Alpha 全球联动观察",
        f"时间: {snapshot.as_of.strftime('%Y-%m-%d %H:%M:%S')}",
        f"外部状态: {snapshot.posture} ({snapshot.score:+d})",
        "信号: " + "；".join(snapshot.signals),
    ]
    if snapshot.source_health:
        lines.append("数据源提示: " + "；".join(snapshot.source_health))
    lines.append("")
    lines.append(render_global_indicator_table(list(snapshot.indicators)))
    lines.append("")
    lines.append("说明: 该报告只做外部环境观察，不直接构成买卖建议。")
    return "\n".join(lines)


def render_global_indicator_table(indicators: list[GlobalIndicator]) -> str:
    if not indicators:
        return "暂无全球联动数据。"

    headers = ["类别", "名称", "最新", "变化", "日期", "来源"]
    rows = [
        [
            item.category,
            item.name,
            format_latest(item),
            format_delta(item),
            item.latest_date,
            item.source,
        ]
        for item in indicators
    ]
    widths = [display_width(value) for value in headers]
    for row in rows:
        widths = [max(width, display_width(value)) for width, value in zip(widths, row)]

    lines = [format_row(headers, widths)]
    lines.append(format_row(["-" * width for width in widths], widths))
    lines.extend(format_row(row, widths) for row in rows)
    return "\n".join(lines)


def format_latest(item: GlobalIndicator) -> str:
    if item.unit and item.unit != "index":
        return f"{item.latest:.2f}{item.unit}"
    return f"{item.latest:.2f}"


def format_delta(item: GlobalIndicator) -> str:
    if item.change is None:
        return "-"
    if item.change_pct is None or item.unit == "%":
        return f"{item.change:+.2f}"
    return f"{item.change:+.2f} / {item.change_pct:+.2%}"
