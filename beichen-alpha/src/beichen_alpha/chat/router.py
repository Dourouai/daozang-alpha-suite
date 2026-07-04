from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from beichen_alpha.decision_log import read_decision_records


@dataclass(frozen=True)
class ChatMessage:
    text: str
    user_id: str = ""
    chat_id: str = ""
    message_id: str = ""


@dataclass(frozen=True)
class ChatResponse:
    text: str
    intent: str


def handle_chat_message(message: ChatMessage, project_dir: str | Path = ".") -> ChatResponse:
    root = Path(project_dir)
    text = normalize_text(message.text)
    if not text or is_help(text):
        return ChatResponse(render_help(), "help")
    if has_any(text, ("状态", "健康", "health", "status")):
        return ChatResponse(render_status(root), "status")
    if has_any(text, ("持仓", "仓位", "position")):
        return ChatResponse(render_positions(root), "positions")
    if has_any(text, ("计划", "候选", "买入", "trade", "plan")):
        return ChatResponse(render_latest_trade_plan(root), "trade_plan")
    if has_any(text, ("日志", "复盘", "记录", "decision", "log")):
        return ChatResponse(render_decision_log_summary(root), "decision_log")
    return ChatResponse(
        "我现在能看：状态、持仓、最新计划、决策日志。\n发送「帮助」可以看完整命令。",
        "fallback",
    )


def normalize_text(text: str) -> str:
    clean = re.sub(r"<at[^>]*>.*?</at>", "", text or "")
    clean = clean.replace("@北辰", "").replace("@道藏", "")
    clean = re.sub(r"(?i)@?daocang", "", clean)
    return clean.strip()


def is_help(text: str) -> bool:
    return text.lower() in {"help", "/help", "帮助", "菜单", "？", "?"}


def has_any(text: str, keywords: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def render_help() -> str:
    return "\n".join(
        [
            "daocang 飞书助手",
            "",
            "可用命令：",
            "- 状态：检查运行目录、持仓、候选池、决策日志",
            "- 持仓：查看本地持仓摘要",
            "- 计划：查看最近一次 3 日交易计划",
            "- 日志：查看决策日志数量和最近记录",
            "",
            "说明：当前只做研究提醒，不自动下单，不构成投资建议。",
        ]
    )


def render_status(root: Path) -> str:
    checks = [
        ("持仓文件", root / "data/positions/current_positions.json"),
        ("基础候选池", root / "data/watchlists/broad_target_pool_2026-07-03.txt"),
        ("创新药主题池", root / "data/watchlists/innovation_drug_pool.txt"),
        ("决策日志目录", root / "data/decision_logs"),
        ("运行状态目录", root / "data/runtime"),
        ("日志目录", root / "logs"),
        ("道藏模型分数", root / "../daozang-alpha/data/exports/alpha_scores_latest.csv"),
    ]
    lines = ["daocang / 北辰 Alpha 状态"]
    for label, path in checks:
        marker = "OK" if path.exists() else "缺失"
        lines.append(f"- {label}: {marker}")
    return "\n".join(lines)


def render_positions(root: Path) -> str:
    path = root / "data/positions/current_positions.json"
    if not path.exists():
        return "未找到本地持仓文件：data/positions/current_positions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    positions = payload.get("positions", [])
    if not positions:
        return "当前持仓文件为空。"
    lines = ["当前持仓"]
    for item in positions:
        lines.append(
            (
                f"- {item.get('name') or item.get('code')} {item.get('code')}: "
                f"{item.get('shares')} 股 | 成本 {float(item.get('cost', 0)):.2f} | "
                f"确认 {float(item.get('confirm', 0)):.2f} | "
                f"止损 {float(item.get('invalid', 0)):.2f} | "
                f"目标 {float(item.get('target', 0)):.2f}"
            )
        )
    return "\n".join(lines)


def render_latest_trade_plan(root: Path) -> str:
    records = read_decision_records(root / "data/decision_logs/recommendations.jsonl")
    plan_records = [item for item in records if item.get("run_kind") == "three_day_trade_plan"]
    if not plan_records:
        return "还没有 3 日交易计划日志。可以先运行 scripts/server_daily_run.sh 或 trade-plan。"
    latest_run_id = max(plan_records, key=lambda item: item.get("logged_at", "")).get("run_id")
    latest = [item for item in plan_records if item.get("run_id") == latest_run_id]
    buys = sorted(
        [item for item in latest if item.get("decision_kind") == "trade_plan_buy"],
        key=lambda item: item.get("rank", 0),
    )
    holdings = [item for item in latest if item.get("decision_kind") == "holding_review"]
    lines = [
        "最近 3 日交易计划",
        f"- 时间: {latest[0].get('as_of', '-') if latest else '-'}",
        f"- 持仓复核: {len(holdings)} 条",
    ]
    if not buys:
        lines.append("- 当前没有新增买入候选。")
    for item in buys:
        prices = item.get("prices", {})
        scores = item.get("scores", {})
        lines.append(
            (
                f"{item.get('rank')}. {item.get('name')} {item.get('code')} | {item.get('status')} | "
                f"分 {scores.get('candidate_score', '-')} | 确认 {fmt(prices.get('confirm'))} | "
                f"止损 {fmt(prices.get('stop'))} | 目标 {fmt(prices.get('target'))}"
            )
        )
    return "\n".join(lines)


def render_decision_log_summary(root: Path) -> str:
    path = root / "data/decision_logs/recommendations.jsonl"
    records = read_decision_records(path)
    if not records:
        return "决策日志为空。"
    counts = Counter(item.get("decision_kind", "unknown") for item in records)
    latest = max(records, key=lambda item: item.get("logged_at", ""))
    lines = [
        "决策日志摘要",
        f"- 总记录: {len(records)}",
        f"- 最近记录: {latest.get('logged_at', '-')} {latest.get('name', '-')} {latest.get('code', '-')}",
    ]
    for name, count in sorted(counts.items()):
        lines.append(f"- {name}: {count}")
    return "\n".join(lines)


def fmt(value) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)
