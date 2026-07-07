from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from beichen_alpha.models import Recommendation, RealtimeCheck, RealtimeQuote


def build_realtime_checks(
    recommendations: list[Recommendation],
    quotes: dict[str, RealtimeQuote],
    chase_limit_pct: float = 0.02,
    min_confirm_buffer_pct: float = 0.002,
    friday_buffer_pct: float = 0.005,
    min_stable_minutes: float = 5.0,
    state_path: str | Path | None = None,
    as_of: datetime | None = None,
    min_sector_confirm_count: int = 2,
    min_sector_confirm_ratio: float = 0.5,
) -> dict[str, RealtimeCheck]:
    trade_time = resolve_trade_time(quotes, as_of)
    is_friday = trade_time.weekday() == 4
    effective_buffer_pct = friday_buffer_pct if is_friday else min_confirm_buffer_pct
    previous_state = load_realtime_state(state_path) if state_path else {}
    sector_confirmations = build_sector_confirmations(
        recommendations,
        quotes,
        firm_buffer_pct=effective_buffer_pct,
        min_count=min_sector_confirm_count,
        min_ratio=min_sector_confirm_ratio,
    )
    checks = {
        item.code: build_realtime_check(
            item,
            quotes.get(item.code),
            chase_limit_pct=chase_limit_pct,
            min_confirm_buffer_pct=effective_buffer_pct,
            base_confirm_buffer_pct=min_confirm_buffer_pct,
            friday_mode=is_friday,
            min_stable_minutes=min_stable_minutes,
            previous_state=previous_state.get(item.code),
            sector_confirmation=sector_confirmations.get(item.code),
        )
        for item in recommendations
    }
    if state_path:
        save_realtime_state(
            state_path,
            recommendations,
            quotes,
            checks,
            firm_buffer_pct=effective_buffer_pct,
            checked_at=trade_time,
        )
    return checks


def build_realtime_check(
    recommendation: Recommendation,
    quote: RealtimeQuote | None,
    chase_limit_pct: float = 0.02,
    min_confirm_buffer_pct: float = 0.002,
    base_confirm_buffer_pct: float = 0.002,
    friday_mode: bool = False,
    min_stable_minutes: float = 5.0,
    previous_state: dict[str, Any] | None = None,
    sector_confirmation: dict[str, Any] | None = None,
) -> RealtimeCheck:
    chase_limit_price = round(recommendation.confirm_price * (1 + chase_limit_pct), 2)
    firm_confirm_price = ceil_price(recommendation.confirm_price * (1 + min_confirm_buffer_pct))
    base_firm_confirm_price = ceil_price(recommendation.confirm_price * (1 + base_confirm_buffer_pct))
    if quote is None or quote.price <= 0:
        execution_score, execution_breakdown = score_realtime_execution(
            "行情缺失",
            recommendation,
            quote,
            chase_limit_price=chase_limit_price,
            friday_mode=friday_mode,
            sector_confirmation=sector_confirmation,
        )
        return RealtimeCheck(
            code=recommendation.code,
            status="行情缺失",
            price=None,
            gap_to_confirm_pct=None,
            chase_limit_price=chase_limit_price,
            execution_score=execution_score,
            execution_breakdown=execution_breakdown,
            detail="未获取到实时行情，不执行。",
        )

    gap = (quote.price / recommendation.confirm_price - 1) * 100 if recommendation.confirm_price > 0 else None
    if quote.price < recommendation.invalid_price:
        status = "盘中失效"
        detail = f"当前价 {quote.price:.2f} 已低于失效线 {recommendation.invalid_price:.2f}，不执行。"
    elif quote.price > chase_limit_price:
        status = "已追高"
        detail = f"当前价 {quote.price:.2f} 高于追高线 {chase_limit_price:.2f}，不追。"
    elif quote.price >= firm_confirm_price:
        stable_minutes = calc_stable_minutes(previous_state, quote.quote_time)
        if stable_minutes is None:
            status = "待站稳"
            detail = (
                f"当前价 {quote.price:.2f} 已站上稳确认价 {firm_confirm_price:.2f}，"
                f"但缺少上一轮站稳记录，等待下一次确认。"
            )
        elif stable_minutes < min_stable_minutes:
            status = "待站稳"
            detail = (
                f"当前价 {quote.price:.2f} 已站上稳确认价 {firm_confirm_price:.2f}，"
                f"连续站稳约 {stable_minutes:.1f} 分钟，未满 {min_stable_minutes:.0f} 分钟。"
            )
        else:
            status = "实时可买"
            detail = (
                f"当前价 {quote.price:.2f} 连续约 {stable_minutes:.1f} 分钟站上稳确认价 {firm_confirm_price:.2f}，"
                f"且未超过追高线 {chase_limit_price:.2f}。"
            )
            if sector_confirmation and not sector_confirmation.get("passed", True):
                status = "板块未共振"
                detail += " " + str(sector_confirmation.get("detail") or "同板块共振不足，暂不执行。")
    elif friday_mode and quote.price >= base_firm_confirm_price:
        status = "周五观察"
        detail = (
            f"当前价 {quote.price:.2f} 已过普通稳确认价 {base_firm_confirm_price:.2f}，"
            f"但周五/T+1 风控要求提高到 {firm_confirm_price:.2f}，暂不执行。"
        )
    elif quote.price >= recommendation.confirm_price:
        status = "贴线观察"
        detail = (
            f"当前价 {quote.price:.2f} 刚站上确认价 {recommendation.confirm_price:.2f}，"
            f"但未达到稳确认价 {firm_confirm_price:.2f}，暂不执行。"
        )
    elif is_near_confirm(quote.price, recommendation.confirm_price):
        status = "接近确认"
        detail = f"当前价 {quote.price:.2f} 距确认价 {recommendation.confirm_price:.2f} 不足 1%，继续等触发。"
    else:
        status = "未触发"
        detail = f"当前价 {quote.price:.2f} 尚未站上确认价 {recommendation.confirm_price:.2f}。"

    execution_score, execution_breakdown = score_realtime_execution(
        status,
        recommendation,
        quote,
        chase_limit_price=chase_limit_price,
        friday_mode=friday_mode,
        sector_confirmation=sector_confirmation,
    )

    return RealtimeCheck(
        code=recommendation.code,
        status=status,
        price=quote.price,
        gap_to_confirm_pct=gap,
        chase_limit_price=chase_limit_price,
        quote_time=quote.quote_time,
        amount_billion=quote.amount_billion,
        sector_confirmation="" if sector_confirmation is None else str(sector_confirmation.get("detail") or ""),
        execution_score=execution_score,
        execution_breakdown=execution_breakdown,
        detail=detail,
    )


def score_realtime_execution(
    status: str,
    recommendation: Recommendation,
    quote: RealtimeQuote | None,
    chase_limit_price: float,
    friday_mode: bool,
    sector_confirmation: dict[str, Any] | None,
) -> tuple[int, str]:
    parts: list[tuple[str, int]] = []

    status_scores = {
        "实时可买": 35,
        "板块未共振": 18,
        "待站稳": 18,
        "周五观察": 10,
        "贴线观察": 8,
        "接近确认": 5,
        "未触发": 0,
        "行情缺失": -40,
        "已追高": -35,
        "盘中失效": -80,
    }
    parts.append(("实时站稳", status_scores.get(status, 0)))

    if quote is None or quote.price <= 0:
        parts.extend([("放量确认", 0), ("VWAP", 0), ("板块同步", 0), ("追高惩罚", 0)])
    else:
        parts.append(("放量确认", score_amount_confirmation(quote.amount_billion)))
        parts.append(("VWAP", score_vwap_confirmation(quote)))
        parts.append(("板块同步", score_sector_sync(sector_confirmation)))
        parts.append(("宏观同步", score_macro_execution(recommendation.macro_event_score)))
        parts.append(("追高惩罚", score_chase_penalty(quote.price, recommendation.confirm_price, chase_limit_price)))
        parts.extend(score_execution_facts(quote))

    parts.append(("周五T+1", -8 if friday_mode else 0))
    total = max(min(sum(score for _, score in parts), 100), -100)
    return total, " ".join(f"{name}{score:+d}" for name, score in parts if score)


def score_amount_confirmation(amount_billion: float | None) -> int:
    if amount_billion is None:
        return 0
    if amount_billion >= 2.0:
        return 10
    if amount_billion >= 1.0:
        return 6
    if amount_billion >= 0.5:
        return 3
    return 0


def score_vwap_confirmation(quote: RealtimeQuote) -> int:
    if quote.vwap_price and quote.vwap_price > 0:
        if quote.price >= quote.vwap_price * 1.001:
            return 8
        if quote.price >= quote.vwap_price:
            return 3
        if quote.price < quote.vwap_price * 0.995:
            return -6
        return 0
    if quote.open > 0:
        return 4 if quote.price >= quote.open else -2
    return 0


def score_sector_sync(sector_confirmation: dict[str, Any] | None) -> int:
    if not sector_confirmation:
        return 0
    total = int(sector_confirmation.get("total_count") or 0)
    if total <= 1:
        return 3
    return 12 if sector_confirmation.get("passed", True) else -15


def score_macro_execution(macro_event_score: int) -> int:
    if macro_event_score >= 12:
        return 5
    if macro_event_score >= 6:
        return 3
    if macro_event_score <= -12:
        return -8
    if macro_event_score <= -6:
        return -4
    return 0


def score_chase_penalty(price: float, confirm_price: float, chase_limit_price: float) -> int:
    if price > chase_limit_price:
        return -30
    if confirm_price > 0 and price / confirm_price - 1 > 0.015:
        return -6
    return 0


def score_execution_facts(quote: RealtimeQuote) -> list[tuple[str, int]]:
    return [
        ("涨跌停距离", score_limit_distance(quote)),
        ("换手活跃", score_turnover_rate(quote.turnover_rate)),
        ("成交流动性", score_quote_amount(quote.amount_billion)),
        ("市值承载", score_market_cap(quote.market_cap_billion)),
    ]


def score_limit_distance(quote: RealtimeQuote) -> int:
    if quote.price <= 0:
        return 0
    if quote.limit_up_price and quote.limit_up_price > 0:
        up_gap = quote.limit_up_price / quote.price - 1
        if up_gap <= 0.003:
            return -12
        if up_gap <= 0.015:
            return -6
    if quote.limit_down_price and quote.limit_down_price > 0:
        down_gap = quote.price / quote.limit_down_price - 1
        if down_gap <= 0.003:
            return -18
        if down_gap <= 0.015:
            return -8
    return 0


def score_turnover_rate(turnover_rate: float | None) -> int:
    if turnover_rate is None:
        return 0
    if turnover_rate < 0.3:
        return -4
    if turnover_rate <= 8:
        return 4
    if turnover_rate <= 18:
        return 0
    return -5


def score_quote_amount(amount_100m: float | None) -> int:
    if amount_100m is None:
        return 0
    if amount_100m >= 20:
        return 5
    if amount_100m >= 5:
        return 3
    if amount_100m >= 1:
        return 1
    return -3


def score_market_cap(market_cap_billion: float | None) -> int:
    if market_cap_billion is None:
        return 0
    if market_cap_billion >= 300:
        return 3
    if market_cap_billion >= 80:
        return 1
    if market_cap_billion < 30:
        return -4
    return 0


def build_sector_confirmations(
    recommendations: list[Recommendation],
    quotes: dict[str, RealtimeQuote],
    firm_buffer_pct: float,
    min_count: int = 2,
    min_ratio: float = 0.5,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Recommendation]] = {}
    for item in recommendations:
        sector = item.industry or item.sector_rotation.split("+", 1)[0] or "未分类"
        groups.setdefault(sector, []).append(item)

    result: dict[str, dict[str, Any]] = {}
    for sector, members in groups.items():
        confirmed: list[str] = []
        valid_members = []
        for member in members:
            quote = quotes.get(member.code)
            if quote is None or quote.price <= 0:
                continue
            valid_members.append(member)
            firm_confirm_price = ceil_price(member.confirm_price * (1 + firm_buffer_pct))
            if quote.price >= firm_confirm_price:
                confirmed.append(member.code)
        total = len(valid_members)
        count = len(confirmed)
        ratio = count / total if total else 0.0
        passed = True if total <= 1 else count >= min_count and ratio >= min_ratio
        detail = (
            f"板块共振: {sector} {count}/{total} 只站上稳确认价"
            if total
            else f"板块共振: {sector} 无实时样本"
        )
        for member in members:
            result[member.code] = {
                "sector": sector,
                "confirmed_count": count,
                "total_count": total,
                "ratio": ratio,
                "passed": passed,
                "detail": detail,
            }
    return result


def is_near_confirm(price: float, confirm_price: float) -> bool:
    if price <= 0 or confirm_price <= 0:
        return False
    return 0 <= confirm_price / price - 1 <= 0.01


def calc_stable_minutes(previous_state: dict[str, Any] | None, current_time: datetime | None) -> float | None:
    if not previous_state or not previous_state.get("firm_above"):
        return None
    previous_time = parse_state_time(str(previous_state.get("quote_time") or ""))
    if previous_time is None or current_time is None:
        return None
    if previous_time.date() != current_time.date():
        return None
    elapsed = (current_time - previous_time).total_seconds() / 60
    if elapsed < 0:
        return None
    return elapsed


def load_realtime_state(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    symbols = payload.get("symbols", {})
    if not isinstance(symbols, dict):
        return {}
    return {str(code): value for code, value in symbols.items() if isinstance(value, dict)}


def save_realtime_state(
    path: str | Path,
    recommendations: list[Recommendation],
    quotes: dict[str, RealtimeQuote],
    checks: dict[str, RealtimeCheck],
    firm_buffer_pct: float,
    checked_at: datetime,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    symbols: dict[str, dict[str, Any]] = {}
    for item in recommendations:
        quote = quotes.get(item.code)
        if quote is None or quote.price <= 0:
            continue
        firm_confirm_price = ceil_price(item.confirm_price * (1 + firm_buffer_pct))
        symbols[item.code] = {
            "code": item.code,
            "name": item.name,
            "price": quote.price,
            "quote_time": (quote.quote_time or checked_at).isoformat(),
            "firm_confirm_price": firm_confirm_price,
            "firm_above": quote.price >= firm_confirm_price,
            "status": checks[item.code].status if item.code in checks else "",
        }
    payload = {
        "checked_at": checked_at.isoformat(),
        "firm_buffer_pct": firm_buffer_pct,
        "symbols": symbols,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_trade_time(quotes: dict[str, RealtimeQuote], as_of: datetime | None) -> datetime:
    quote_times = [quote.quote_time for quote in quotes.values() if quote.quote_time is not None]
    if quote_times:
        return max(quote_times)
    return as_of or datetime.now()


def parse_state_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def ceil_price(value: float) -> float:
    return math.ceil(value * 100 - 1e-9) / 100
