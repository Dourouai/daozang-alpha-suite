from __future__ import annotations

import contextlib
import io
from datetime import date, timedelta
from typing import Callable

from beichen_alpha.models import Bar, MarketRegime

from .akshare_source import fetch_index_bars, import_akshare, normalize_symbol
from .universe_source import is_bad_name, is_mainland_stock


DEFAULT_INDEXES = ("000300", "000001", "399001", "399006")


class AkshareMarketRegimeSource:
    def __init__(
        self,
        index_codes: tuple[str, ...] = DEFAULT_INDEXES,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> None:
        self.index_codes = tuple(normalize_symbol(code) for code in index_codes)
        self.start_date = start_date or default_start_date()
        self.end_date = end_date or date.today().strftime("%Y%m%d")

    def load(self) -> MarketRegime | None:
        ak = import_akshare()
        try:
            index_bars = {
                code: fetch_index_bars(ak, code, self.start_date, self.end_date)
                for code in self.index_codes
            }
            spot_rows = fetch_market_spot_rows(ak)
        except Exception:
            return None
        return build_market_regime(index_bars, spot_rows)


def build_market_regime(
    index_bars: dict[str, list[Bar]],
    spot_rows: list[dict],
) -> MarketRegime:
    index_score, index_detail = score_index_trend(index_bars)
    breadth, limit_up_count, limit_down_count, turnover_billion = summarize_spot_rows(spot_rows)

    breadth_score = 0
    if breadth is not None:
        if breadth >= 0.72:
            breadth_score = 14
        elif breadth >= 0.6:
            breadth_score = 9
        elif breadth >= 0.5:
            breadth_score = 4
        elif breadth >= 0.42:
            breadth_score = -3
        elif breadth >= 0.32:
            breadth_score = -10
        else:
            breadth_score = -18

    limit_score = 0
    limit_gap = (limit_up_count or 0) - (limit_down_count or 0)
    if limit_gap >= 70:
        limit_score = 8
    elif limit_gap >= 25:
        limit_score = 4
    elif limit_gap <= -35:
        limit_score = -10
    elif limit_gap <= -10:
        limit_score = -5

    turnover_score = 0
    if turnover_billion is not None:
        if turnover_billion >= 12000:
            turnover_score = 5
        elif turnover_billion >= 8500:
            turnover_score = 2
        elif turnover_billion < 5500:
            turnover_score = -4

    raw_score = index_score + breadth_score + limit_score + turnover_score
    temperature = classify_temperature(raw_score, breadth, limit_up_count)
    score = -8 if temperature == "过热" else raw_score
    detail = (
        f"{index_detail}; 上涨占比 {format_pct(breadth)}, "
        f"涨停 {limit_up_count or 0}, 跌停 {limit_down_count or 0}, "
        f"成交额 {turnover_billion:.0f} 亿" if turnover_billion is not None else
        f"{index_detail}; 上涨占比 {format_pct(breadth)}, 涨停 {limit_up_count or 0}, 跌停 {limit_down_count or 0}"
    )
    return MarketRegime(
        temperature=temperature,
        score=score,
        breadth=breadth,
        limit_up_count=limit_up_count,
        limit_down_count=limit_down_count,
        turnover_billion=turnover_billion,
        index_trend=index_detail,
        detail=detail,
    )


def score_index_trend(index_bars: dict[str, list[Bar]]) -> tuple[int, str]:
    if not index_bars:
        return 0, "指数趋势缺失"

    warm_count = 0
    positive_3d = 0
    details = []
    for code, bars in index_bars.items():
        if len(bars) < 6:
            continue
        closes = [bar.close for bar in bars]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / min(len(closes), 10)
        ret_3d = closes[-1] / closes[-4] - 1 if len(closes) >= 4 and closes[-4] else 0.0
        if closes[-1] > ma5:
            warm_count += 1
        if ret_3d > 0:
            positive_3d += 1
        details.append(f"{code} 3日{ret_3d:.2%}")

    score = warm_count * 4 + positive_3d * 3 - max(len(index_bars) - warm_count, 0) * 3
    return score, "指数" + "、".join(details[:4])


def summarize_spot_rows(rows: list[dict]) -> tuple[float | None, int | None, int | None, float | None]:
    valid_rows = [row for row in rows if row.get("latest", 0.0) > 0]
    changed_rows = [row for row in valid_rows if row.get("pct_change") is not None]
    if not changed_rows:
        return None, None, None, sum(row.get("turnover", 0.0) for row in valid_rows) / 100_000_000 or None

    up_count = sum(1 for row in changed_rows if row.get("pct_change", 0.0) > 0)
    down_count = sum(1 for row in changed_rows if row.get("pct_change", 0.0) < 0)
    breadth = up_count / (up_count + down_count) if up_count + down_count else None
    limit_up_count = sum(1 for row in changed_rows if row.get("pct_change", 0.0) >= 9.5)
    limit_down_count = sum(1 for row in changed_rows if row.get("pct_change", 0.0) <= -9.5)
    turnover_billion = sum(row.get("turnover", 0.0) for row in valid_rows) / 100_000_000
    return breadth, limit_up_count, limit_down_count, turnover_billion if turnover_billion > 0 else None


def classify_temperature(score: int, breadth: float | None, limit_up_count: int | None) -> str:
    if breadth is not None and breadth >= 0.82 and (limit_up_count or 0) >= 140:
        return "过热"
    if score >= 28:
        return "热"
    if score >= 10:
        return "偏暖"
    if score >= -8:
        return "中性"
    if score >= -22:
        return "偏冷"
    return "冷"


def fetch_market_spot_rows(ak) -> list[dict]:
    frame = quiet_call(ak.stock_zh_a_spot)
    rows = []
    for record in frame.to_dict(orient="records"):
        code = normalize_spot_code(record.get("代码"))
        name = str(record.get("名称") or "").strip()
        if not code or not name or not is_mainland_stock(code) or is_bad_name(name):
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "latest": to_float(record.get("最新价")),
                "pct_change": to_optional_float(record.get("涨跌幅")),
                "turnover": to_float(record.get("成交额")),
            }
        )
    return rows


def quiet_call(func: Callable, **kwargs):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return func(**kwargs)


def normalize_spot_code(value) -> str:
    text = str(value or "").strip().lower()
    if text.startswith(("sh", "sz")):
        return normalize_symbol(text)
    return ""


def to_float(value) -> float:
    parsed = to_optional_float(value)
    return 0.0 if parsed is None else parsed


def to_optional_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(str(value).replace("%", ""))
    except (TypeError, ValueError):
        return None


def format_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def default_start_date() -> str:
    return (date.today() - timedelta(days=45)).strftime("%Y%m%d")
