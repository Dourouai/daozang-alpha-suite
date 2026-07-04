from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from beichen_alpha.decision_log import (
    SCHEMA_VERSION,
    append_decision_records,
    make_run_id,
    read_decision_records,
    realtime_check_to_dict,
)
from beichen_alpha.models import Recommendation, RealtimeQuote
from beichen_alpha.strategy.realtime import build_realtime_checks


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


QuoteLoader = Callable[[list[str]], dict[str, RealtimeQuote]]


def handle_chat_message(
    message: ChatMessage,
    project_dir: str | Path = ".",
    quote_loader: QuoteLoader | None = None,
) -> ChatResponse:
    root = Path(project_dir)
    text = normalize_text(message.text)
    if not text or is_help(text):
        return ChatResponse(render_help(), "help")
    if has_any(text, ("状态", "健康", "health", "status")):
        return ChatResponse(render_status(root), "status")
    if has_any(text, ("持仓", "仓位", "position")):
        return ChatResponse(render_positions(root, quote_loader=quote_loader), "positions")
    if is_recommendation_request(text):
        return ChatResponse(render_recommendation_request(text, root, quote_loader=quote_loader), "recommendation")
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
            "- 持仓：查看本地持仓，并现场拉取行情",
            "- 推荐 医疗行业的3支股票：从可追溯候选中筛选，并现场拉取行情",
            "- 计划：查看最近一次 3 日交易计划",
            "- 日志：查看决策日志数量和最近记录",
            "",
            "说明：当前只做研究提醒，不自动下单，不构成投资建议；持仓股数/成本来自本地文件，不等于券商实时账户。",
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


def render_positions(root: Path, quote_loader: QuoteLoader | None = None) -> str:
    path = root / "data/positions/current_positions.json"
    if not path.exists():
        return "未找到本地持仓文件：data/positions/current_positions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    positions = payload.get("positions", [])
    if not positions:
        return "当前持仓文件为空。"
    symbols = [str(item.get("code") or "") for item in positions if item.get("code")]
    quotes, quote_error = safe_load_quotes(symbols, quote_loader)
    lines = [
        "当前持仓",
        "持仓来源: 本地 current_positions.json；股数/成本未接券商账户，可能与真实账户不同。",
    ]
    if quote_error:
        lines.append(f"实时行情: 获取失败，不能给实时执行结论。{quote_error}")
    for item in positions:
        code = str(item.get("code") or "")
        quote = quotes.get(code)
        current = quote.price if quote else None
        cost = float(item.get("cost", 0))
        shares = float(item.get("shares", 0))
        pnl_text = "-"
        if current is not None and cost > 0:
            pnl = (current - cost) * shares
            pnl_pct = current / cost - 1
            pnl_text = f"{pnl:+.2f} ({pnl_pct:+.2%})"
        quote_text = format_quote(quote)
        lines.append(
            (
                f"- {item.get('name') or item.get('code')} {item.get('code')}: "
                f"{item.get('shares')} 股 | 成本 {float(item.get('cost', 0)):.2f} | "
                f"现价 {fmt(current)} | 浮盈亏 {pnl_text} | "
                f"确认 {float(item.get('confirm', 0)):.2f} | "
                f"止损 {float(item.get('invalid', 0)):.2f} | "
                f"目标 {float(item.get('target', 0)):.2f} | {quote_text}"
            )
        )
    return "\n".join(lines)


def is_recommendation_request(text: str) -> bool:
    return "推荐" in text and has_any(text, ("股票", "支", "只", "买", "候选", "行业", "板块"))


def render_recommendation_request(text: str, root: Path, quote_loader: QuoteLoader | None = None) -> str:
    count = parse_requested_count(text, default=3)
    sector_label, keywords = parse_sector_keywords(text)
    records = select_candidate_records(root, keywords, count=count)
    if not records:
        return f"没有找到「{sector_label}」可追溯候选。先运行一次对应行业的筛选，再做实时推荐。"

    recommendations = [record_to_recommendation(record) for record in records]
    quotes, quote_error = safe_load_quotes([item.code for item in recommendations], quote_loader)
    if quote_error:
        return f"实时行情获取失败，不能给「{sector_label}」执行结论：{quote_error}"
    checks = build_realtime_checks(
        recommendations,
        quotes,
        chase_limit_pct=0.012,
        state_path=root / "data/runtime/chat_realtime_state.json",
    )
    log_chat_recommendations(text, root, records, recommendations, checks, sector_label)

    quote_times = [check.quote_time for check in checks.values() if check.quote_time is not None]
    quote_time_text = max(quote_times).strftime("%Y-%m-%d %H:%M:%S") if quote_times else "未知"
    lines = [
        f"{sector_label} 3日短线候选（实时复核）",
        f"行情时间: {quote_time_text}",
        "规则: 候选不等于买入，只在站上确认价且未超过追高线时考虑；不构成投资建议。",
    ]
    for index, item in enumerate(recommendations, 1):
        check = checks.get(item.code)
        record = records[index - 1]
        prices = record.get("prices", {})
        score = item.candidate_score or item.score
        if check is None:
            lines.append(f"{index}. {item.name} {item.code} | 行情缺失 | 候选 {score}")
            continue
        gap = "-" if check.gap_to_confirm_pct is None else f"{check.gap_to_confirm_pct:+.2f}%"
        lines.append(
            (
                f"{index}. {item.name} {item.code} | {check.status} | 候选 {score} | 执行 {check.execution_score}\n"
                f"   现价 {fmt(check.price)} | 距确认 {gap} | 确认 {fmt(item.confirm_price)} | "
                f"追高线 {fmt(check.chase_limit_price)} | 止损 {fmt(item.invalid_price)} | 目标 {fmt(item.take_profit_price)}\n"
                f"   依据: {summarize_record_reason(record)}\n"
                f"   操作: {check.detail}"
            )
        )
        if prices.get("close") is not None and check.price is not None:
            lines.append(f"   参考: 上次候选收盘 {fmt(prices.get('close'))}，本次现场价 {fmt(check.price)}")
    return "\n".join(lines)


def parse_requested_count(text: str, default: int = 3) -> int:
    match = re.search(r"(\d+)\s*[支只个]?", text)
    if not match:
        return default
    return max(1, min(int(match.group(1)), 5))


def parse_sector_keywords(text: str) -> tuple[str, tuple[str, ...]]:
    if has_any(text, ("医疗", "医药", "创新药", "药")):
        return "医疗/医药", ("医疗", "医药", "创新药")
    if has_any(text, ("银行", "金融")):
        return "金融", ("银行", "金融", "非银金融")
    if has_any(text, ("能源", "石油", "煤炭", "电力")):
        return "能源", ("能源", "石油", "煤炭", "电力")
    return "全市场", ()


def select_candidate_records(root: Path, keywords: tuple[str, ...], count: int) -> list[dict]:
    records = read_decision_records(root / "data/decision_logs/recommendations.jsonl")
    candidates = [
        record
        for record in records
        if record.get("decision_kind") in {"candidate_recommendation", "trade_plan_buy"}
        and record_has_prices(record)
        and (not keywords or record_matches_keywords(record, keywords))
    ]
    by_code: dict[str, dict] = {}
    for record in candidates:
        code = str(record.get("code") or "")
        current = by_code.get(code)
        if current is None or candidate_sort_key(record) > candidate_sort_key(current):
            by_code[code] = record
    return sorted(by_code.values(), key=candidate_sort_key, reverse=True)[:count]


def record_matches_keywords(record: dict, keywords: tuple[str, ...]) -> bool:
    fields = [
        record.get("industry", ""),
        record.get("group", ""),
        " ".join(str(item) for item in record.get("themes", [])),
        json.dumps(record.get("context", {}), ensure_ascii=False),
    ]
    haystack = " ".join(str(item) for item in fields)
    return any(keyword in haystack for keyword in keywords)


def record_has_prices(record: dict) -> bool:
    prices = record.get("prices") or {}
    return bool(prices.get("confirm") and (prices.get("stop") or prices.get("invalid")))


def candidate_sort_key(record: dict) -> tuple[int, int, str]:
    score = record.get("scores", {}).get("candidate_score")
    if score is None:
        score = record.get("scores", {}).get("score", 0)
    status_rank = {"条件执行": 4, "可执行": 4, "观察": 3, "贴线观察": 2}
    return (status_rank.get(str(record.get("status") or ""), 1), int(score or 0), str(record.get("logged_at") or ""))


def record_to_recommendation(record: dict) -> Recommendation:
    prices = record.get("prices") or {}
    scores = record.get("scores") or {}
    rationale = record.get("rationale") or {}
    risk = record.get("risk") or {}
    score = int(scores.get("score") or scores.get("candidate_score") or 0)
    stop = prices.get("stop") if prices.get("stop") is not None else prices.get("invalid")
    return Recommendation(
        code=str(record.get("code") or ""),
        name=str(record.get("name") or record.get("code") or ""),
        score=score,
        status=str(record.get("status") or "观察"),
        close=float(prices.get("close") or prices.get("current") or 0),
        observation_zone=str(prices.get("observation_zone") or "-"),
        confirm_price=float(prices.get("confirm") or 0),
        invalid_price=float(stop or 0),
        reason=str(rationale.get("reason") or rationale.get("trigger") or ""),
        risk=str(risk.get("risk_text") or ""),
        industry=str(record.get("industry") or record.get("group") or ""),
        themes=tuple(str(item) for item in record.get("themes", [])),
        take_profit_price=float(prices.get("target") or 0),
        trailing_stop_price=float(prices.get("trailing_stop") or stop or 0),
        sell_plan=str(rationale.get("sell_plan") or rationale.get("trigger") or ""),
        candidate_score=int(scores.get("candidate_score") or score),
        candidate_breakdown=str(rationale.get("candidate_breakdown") or ""),
        macro_event_score=int(scores.get("macro_event_score") or 0),
        macro_events=str(rationale.get("macro_events") or ""),
    )


def summarize_record_reason(record: dict) -> str:
    rationale = record.get("rationale") or {}
    for key in ("candidate_breakdown", "reason", "trigger"):
        value = str(rationale.get(key) or "").strip()
        if value:
            return value
    return "来自最近一次可追溯候选记录。"


def log_chat_recommendations(
    text: str,
    root: Path,
    source_records: list[dict],
    recommendations: list[Recommendation],
    checks: dict[str, Any],
    sector_label: str,
) -> None:
    now = datetime.now()
    run_id = make_run_id("chat_recommendation", now, {"query": text, "sector": sector_label})
    records = []
    for index, item in enumerate(recommendations, 1):
        check = checks.get(item.code)
        source = source_records[index - 1]
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "run_kind": "chat_recommendation",
                "decision_kind": "chat_recommendation",
                "logged_at": now.isoformat(timespec="seconds"),
                "as_of": (check.quote_time if check and check.quote_time else now).isoformat(timespec="seconds"),
                "rank": index,
                "code": item.code,
                "name": item.name,
                "action": "conditional_buy" if check and check.status in {"实时可买", "贴线观察", "接近确认"} else "watch_only",
                "status": check.status if check else "行情缺失",
                "context": {
                    "command": "feishu_chat_recommend",
                    "query": text,
                    "sector": sector_label,
                    "source_run_id": source.get("run_id"),
                    "source_decision_kind": source.get("decision_kind"),
                    "source_logged_at": source.get("logged_at"),
                },
                "prices": {
                    "current": None if check is None else check.price,
                    "confirm": item.confirm_price,
                    "stop": item.invalid_price,
                    "target": item.take_profit_price,
                    "chase_limit": None if check is None else check.chase_limit_price,
                },
                "scores": {
                    "candidate_score": item.candidate_score or item.score,
                    "execution_score": None if check is None else check.execution_score,
                },
                "rationale": {
                    "source_reason": summarize_record_reason(source),
                    "execution_detail": "" if check is None else check.detail,
                },
                "risk": {
                    "stop": item.invalid_price,
                    "risk_text": item.risk,
                },
                "execution": {} if check is None else realtime_check_to_dict(check),
                "outcome": {},
            }
        )
    append_decision_records(records, root / "data/decision_logs/recommendations.jsonl")


def safe_load_quotes(symbols: list[str], quote_loader: QuoteLoader | None = None) -> tuple[dict[str, RealtimeQuote], str]:
    try:
        loader = quote_loader or load_realtime_quotes
        return loader(symbols), ""
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"


def load_realtime_quotes(symbols: list[str]) -> dict[str, RealtimeQuote]:
    from beichen_alpha.data_sources.realtime_quote_source import TencentRealtimeQuoteSource

    return TencentRealtimeQuoteSource(symbols).load()


def format_quote(quote: RealtimeQuote | None) -> str:
    if quote is None:
        return "行情缺失"
    parts = [f"行情 {quote.source}"]
    if quote.quote_time:
        parts.append(quote.quote_time.strftime("%Y-%m-%d %H:%M:%S"))
    if quote.warning:
        parts.append(quote.warning)
    return " | ".join(parts)


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
