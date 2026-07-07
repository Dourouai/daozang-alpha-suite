"""Outcome backfill for decision logs.

Reads the Beichen decision log JSONL, queries historical daily bars for each
stock that was recommended or reviewed, calculates forward 1D/3D/5D returns,
checks stop/target hits, and writes enriched records with outcome data.

This closes the feedback loop: measure → learn → improve.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from beichen_alpha.data_sources.baostock_source import BaostockPriceSource


# ---------------------------------------------------------------------------
# Outcome data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyOutcome:
    """Forward return and hit-check for a single horizon."""

    horizon_days: int
    forward_return: float | None
    target_hit: bool
    stop_hit: bool
    max_runup: float | None  # best intra-horizon gain
    max_drawdown: float | None  # worst intra-horizon loss


@dataclass(frozen=True)
class OutcomeResult:
    """Complete outcome for one decision record."""

    code: str
    decision_date: date
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    bars_available: int  # how many future bars were found
    outcomes: tuple[DailyOutcome, ...]
    errors: tuple[str, ...]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def backfill_decision_log(
    log_path: str | Path = "data/decision_logs/recommendations.jsonl",
    output_path: str | Path | None = None,
    *,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    max_bars_lookback: int = 120,
    horizons: tuple[int, ...] = (1, 3, 5, 10),
    quiet: bool = False,
) -> dict[str, Any]:
    """Backfill outcomes for all records in a decision log.

    Args:
        log_path: Path to the decision log JSONL file.
        output_path: Where to write enriched records. Defaults to overwriting
                     the input file with ``.backfilled.jsonl`` extension.
        start_date: Only process records on or after this date.
        end_date: Only process records on or before this date.
        max_bars_lookback: Max historical bars to query before decision_date.
        horizons: Forward horizons in trading days (default: 1, 3, 5, 10).
        quiet: Suppress progress output.

    Returns:
        Summary dict with counts and per-horizon aggregate stats.
    """
    log_path = Path(log_path)
    if not log_path.exists():
        return _empty_summary(f"decision log not found: {log_path}")

    records = _read_records(log_path)
    if not records:
        return _empty_summary("no records in decision log")

    # Filter by date range
    if start_date is not None:
        start = _parse_date(start_date)
        records = [r for r in records if _record_date(r) is not None and _record_date(r) >= start]
    if end_date is not None:
        end = _parse_date(end_date)
        records = [r for r in records if _record_date(r) is not None and _record_date(r) <= end]

    # Group by code for batch price queries
    code_date_map: dict[str, list[date]] = {}
    for rec in records:
        code = str(rec.get("code", "")).strip()
        rec_date = _record_date(rec)
        if not code or rec_date is None:
            continue
        code_date_map.setdefault(code, []).append(rec_date)

    if not code_date_map:
        return _empty_summary("no records with valid code and date")

    # Determine date range for bar queries
    all_dates = sorted({d for dates in code_date_map.values() for d in dates})
    query_start = all_dates[0] - timedelta(days=max_bars_lookback)
    query_end = all_dates[-1] + timedelta(days=max(horizons) + 30)  # buffer for weekends

    # Load price bars in batch
    if not quiet:
        print(
            f"[outcome backfill] loading bars for {len(code_date_map)} stocks "
            f"({query_start.isoformat()} → {query_end.isoformat()}) ...",
            file=sys.stderr,
        )
    price_source = BaostockPriceSource(
        symbols=sorted(code_date_map),
        benchmark="000300",
        start_date=query_start.strftime("%Y%m%d"),
        end_date=query_end.strftime("%Y%m%d"),
    )
    try:
        price_map = price_source.load()
    except Exception as exc:
        return _empty_summary(f"failed to load price bars: {exc}")

    # Remove benchmark from price map
    price_map = {k: v for k, v in price_map.items() if k != "000300"}

    if not price_map:
        return _empty_summary("no price bars loaded")

    # Compute outcomes per record
    enriched_records: list[dict[str, Any]] = []
    outcome_results: list[OutcomeResult] = []
    for rec in records:
        code = str(rec.get("code", "")).strip()
        rec_date = _record_date(rec)
        if not code or rec_date is None or code not in price_map:
            enriched_records.append(rec)
            continue

        result = _compute_outcome(rec, price_map[code], rec_date, horizons=horizons)
        outcome_results.append(result)
        enriched = _enrich_record(rec, result)
        enriched_records.append(enriched)

    # Write enriched records
    out_path = Path(output_path) if output_path else log_path.with_suffix(".backfilled.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in enriched_records:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str) + "\n")

    # Build summary
    summary = _build_summary(outcome_results, horizons, out_path)
    if not quiet:
        _print_summary(summary)
    return summary


def compute_single_outcome(
    code: str,
    decision_date: date,
    price_bars: list[Any],
    *,
    stop_price: float | None = None,
    target_price: float | None = None,
    horizons: tuple[int, ...] = (1, 3, 5, 10),
) -> OutcomeResult:
    """Compute outcome for a single stock/date without reading logs.

    Useful for ad-hoc queries: "what happened after I recommended stock X on date Y?"
    """
    return _compute_outcome(
        {"code": code, "stop": stop_price, "target": target_price},
        price_bars,
        decision_date,
        horizons=horizons,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _record_date(rec: dict[str, Any]) -> date | None:
    """Extract the decision date from a record."""
    for key in ("as_of", "logged_at", "trade_date"):
        val = rec.get(key)
        if val:
            return _parse_date(val)
    return None


def _parse_date(val: Any) -> date | None:
    """Parse a date from various formats."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    text = str(val).strip()[:10]  # take date part only
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _compute_outcome(
    rec: dict[str, Any],
    bars: list[Any],
    decision_date: date,
    horizons: tuple[int, ...],
) -> OutcomeResult:
    """Compute forward returns and hit-checks for one decision record."""
    code = str(rec.get("code", ""))
    errors: list[str] = []

    # Extract prices from record
    prices = rec.get("prices") or {}
    if isinstance(prices, dict):
        entry_price = prices.get("close") or prices.get("current") or prices.get("cost")
        stop_price = prices.get("stop")
        target_price = prices.get("target")
    else:
        entry_price = None
        stop_price = None
        target_price = None

    # Find the bar at or just before decision_date
    # bars are expected to have .date, .close, .high, .low attributes
    decision_idx = _find_bar_index(bars, decision_date)
    if decision_idx is None:
        return OutcomeResult(
            code=code,
            decision_date=decision_date,
            entry_price=None,
            stop_price=None,
            target_price=None,
            bars_available=0,
            outcomes=(),
            errors=("no bar at or before decision_date",),
        )

    decision_bar = bars[decision_idx]
    entry = float(entry_price) if entry_price else float(decision_bar.close)
    stop = float(stop_price) if stop_price else None
    target = float(target_price) if target_price else None

    outcomes: list[DailyOutcome] = []
    for horizon in horizons:
        future_idx = decision_idx + horizon
        if future_idx >= len(bars):
            outcomes.append(
                DailyOutcome(
                    horizon_days=horizon,
                    forward_return=None,
                    target_hit=False,
                    stop_hit=False,
                    max_runup=None,
                    max_drawdown=None,
                )
            )
            errors.append(f"insufficient bars for {horizon}d horizon")
            continue

        future_bars = bars[decision_idx + 1 : future_idx + 1]
        forward_return = float(future_bars[-1].close) / entry - 1

        # Check intra-horizon highs/lows for stop/target hits
        target_hit = False
        stop_hit = False
        max_high = entry
        min_low = entry
        for bar in future_bars:
            high = float(bar.high)
            low = float(bar.low)
            if high > max_high:
                max_high = high
            if low < min_low:
                min_low = low
            if target is not None and high >= target:
                target_hit = True
            if stop is not None and low <= stop:
                stop_hit = True

        outcomes.append(
            DailyOutcome(
                horizon_days=horizon,
                forward_return=forward_return,
                target_hit=target_hit,
                stop_hit=stop_hit,
                max_runup=max_high / entry - 1,
                max_drawdown=min_low / entry - 1,
            )
        )

    return OutcomeResult(
        code=code,
        decision_date=decision_date,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        bars_available=len(bars) - decision_idx - 1,
        outcomes=tuple(outcomes),
        errors=tuple(errors),
    )


def _find_bar_index(bars: list[Any], target_date: date) -> int | None:
    """Find the index of the bar at or just before target_date.

    Returns None if no bar exists on or before the target date.
    """
    best_idx: int | None = None
    for idx, bar in enumerate(bars):
        bar_date = _parse_bar_date(bar)
        if bar_date is None:
            continue
        if bar_date <= target_date:
            best_idx = idx
        else:
            break  # bars assumed sorted by date
    return best_idx


def _parse_bar_date(bar: Any) -> date | None:
    """Extract date from a bar object (Bar dataclass, dict, or object)."""
    if hasattr(bar, "date"):
        return _parse_date(bar.date)
    if isinstance(bar, dict):
        return _parse_date(bar.get("date"))
    return None


def _enrich_record(rec: dict[str, Any], result: OutcomeResult) -> dict[str, Any]:
    """Add outcome data to a decision log record."""
    enriched = dict(rec)
    outcome_data: dict[str, Any] = {
        "backfilled_at": datetime.now().isoformat(timespec="seconds"),
        "decision_date": result.decision_date.isoformat(),
        "entry_price": result.entry_price,
        "stop_price": result.stop_price,
        "target_price": result.target_price,
        "bars_after_decision": result.bars_available,
        "errors": list(result.errors),
    }
    for oc in result.outcomes:
        outcome_data[f"return_{oc.horizon_days}d"] = (
            round(oc.forward_return, 6) if oc.forward_return is not None else None
        )
        outcome_data[f"target_hit_{oc.horizon_days}d"] = oc.target_hit
        outcome_data[f"stop_hit_{oc.horizon_days}d"] = oc.stop_hit
        outcome_data[f"max_runup_{oc.horizon_days}d"] = (
            round(oc.max_runup, 6) if oc.max_runup is not None else None
        )
        outcome_data[f"max_drawdown_{oc.horizon_days}d"] = (
            round(oc.max_drawdown, 6) if oc.max_drawdown is not None else None
        )

    # Merge with existing outcome if any
    existing = enriched.get("outcome") or {}
    if isinstance(existing, dict):
        existing.update(outcome_data)
    else:
        existing = outcome_data
    enriched["outcome"] = existing
    return enriched


def _build_summary(
    results: list[OutcomeResult],
    horizons: tuple[int, ...],
    output_path: Path,
) -> dict[str, Any]:
    """Build aggregate statistics from outcome results."""
    total = len(results)
    if total == 0:
        return _empty_summary("no results", output_path)

    summary: dict[str, Any] = {
        "total_records": total,
        "output_path": str(output_path),
    }

    for horizon in horizons:
        returns = []
        target_hits = 0
        stop_hits = 0
        valid = 0
        for r in results:
            for oc in r.outcomes:
                if oc.horizon_days == horizon and oc.forward_return is not None:
                    returns.append(oc.forward_return)
                    if oc.target_hit:
                        target_hits += 1
                    if oc.stop_hit:
                        stop_hits += 1
                    valid += 1

        if valid == 0:
            summary[f"horizon_{horizon}d"] = {
                "samples": 0,
                "note": "no data",
            }
            continue

        up_count = sum(1 for r in returns if r > 0)
        avg_return = sum(returns) / len(returns)
        sorted_returns = sorted(returns)
        median_return = sorted_returns[len(sorted_returns) // 2]

        summary[f"horizon_{horizon}d"] = {
            "samples": valid,
            "up_probability": round(up_count / valid, 4),
            "avg_return": round(avg_return, 6),
            "median_return": round(median_return, 6),
            "target_hit_rate": round(target_hits / valid, 4) if valid else 0,
            "stop_hit_rate": round(stop_hits / valid, 4) if valid else 0,
            "best_return": round(max(returns), 6),
            "worst_return": round(min(returns), 6),
        }

    return summary


def _empty_summary(message: str = "", output_path: Path | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"total_records": 0, "message": message}
    if output_path:
        result["output_path"] = str(output_path)
    return result


def _print_summary(summary: dict[str, Any]) -> None:
    """Print a human-readable summary to stderr."""
    print("\n=== Outcome Backfill Summary ===", file=sys.stderr)
    total = summary.get("total_records", 0)
    print(f"Records processed: {total}", file=sys.stderr)
    if summary.get("message"):
        print(f"Message: {summary['message']}", file=sys.stderr)

    for key, value in sorted(summary.items()):
        if key.startswith("horizon_") and isinstance(value, dict):
            h = key.replace("horizon_", "").replace("d", "")
            samples = value.get("samples", 0)
            if samples == 0:
                print(f"  {h}日: 无数据", file=sys.stderr)
                continue
            up = value.get("up_probability", 0)
            avg = value.get("avg_return", 0)
            hit = value.get("target_hit_rate", 0)
            stop = value.get("stop_hit_rate", 0)
            print(
                f"  {h}日: 样本{samples} | 上涨{up:.1%} | "
                f"平均收益{avg:+.2%} | 目标触达{hit:.1%} | 止损触碰{stop:.1%}",
                file=sys.stderr,
            )

    print(f"\nEnriched log: {summary.get('output_path', 'N/A')}", file=sys.stderr)
