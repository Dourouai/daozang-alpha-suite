from __future__ import annotations

import contextlib
import io
import math
import struct
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .universe import read_universe_rows


PRICE_FIELDS = ("open", "high", "low", "close")
DERIVED_FIELDS = ("volume", "amount", "factor", "vwap", "adjclose", "change")
ALL_FIELDS = (*PRICE_FIELDS, *DERIVED_FIELDS)


@dataclass(frozen=True)
class SyncAkshareQlibBarsOptions:
    qlib_data_dir: str | Path = "data/qlib/cn_data"
    universe_file: str | Path = "data/universe/active_universe.csv"
    benchmark: str = "000300"
    start: str | None = None
    end: str | None = None
    adjust: str = "qfq"
    max_instruments: int | None = None
    workers: int = 8
    request_timeout: float = 4.0
    dry_run: bool = False


@dataclass(frozen=True)
class SyncAkshareQlibBarsArtifacts:
    qlib_data_dir: Path
    calendar_start: str
    calendar_end_before: str
    calendar_end_after: str
    target_dates: tuple[str, ...]
    instruments: int
    updated: int
    skipped: int
    failed: int
    dry_run: bool


@dataclass(frozen=True)
class DailyBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume_lot: float


def sync_akshare_qlib_bars(options: SyncAkshareQlibBarsOptions) -> SyncAkshareQlibBarsArtifacts:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("AKShare is required: python3 -m pip install akshare pandas") from exc

    qlib_dir = Path(options.qlib_data_dir)
    calendar_path = qlib_dir / "calendars" / "day.txt"
    calendar = read_calendar(calendar_path)
    if not calendar:
        raise RuntimeError(f"Qlib day calendar is empty or missing: {calendar_path}")

    end = normalize_date_text(options.end) or date.today().strftime("%Y-%m-%d")
    start = normalize_date_text(options.start)
    last_calendar_date = calendar[-1]
    fetch_start = start or last_calendar_date
    benchmark_dates = fetch_benchmark_dates(ak, options.benchmark, fetch_start, end)
    target_dates = tuple(date_text for date_text in benchmark_dates if date_text > last_calendar_date)
    if not target_dates:
        return SyncAkshareQlibBarsArtifacts(
            qlib_data_dir=qlib_dir,
            calendar_start=calendar[0],
            calendar_end_before=last_calendar_date,
            calendar_end_after=last_calendar_date,
            target_dates=(),
            instruments=0,
            updated=0,
            skipped=0,
            failed=0,
            dry_run=options.dry_run,
        )

    instruments = read_target_instruments(Path(options.universe_file), limit=options.max_instruments)
    updated = 0
    skipped = 0
    failed = 0

    candidates = [
        instrument
        for instrument in instruments
        if (qlib_dir / "features" / instrument.lower()).exists()
    ]
    skipped += len(instruments) - len(candidates)
    worker_count = max(int(options.workers or 1), 1)
    if worker_count == 1:
        results = [
            sync_one_instrument(
                ak,
                qlib_dir,
                instrument,
                calendar,
                target_dates,
                last_calendar_date,
                end,
                options.adjust,
                options.request_timeout,
                options.dry_run,
            )
            for instrument in candidates
        ]
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    sync_one_instrument,
                    ak,
                    qlib_dir,
                    instrument,
                    calendar,
                    target_dates,
                    last_calendar_date,
                    end,
                    options.adjust,
                    options.request_timeout,
                    options.dry_run,
                )
                for instrument in candidates
            ]
            results = [future.result() for future in as_completed(futures)]

    for result in results:
        if result == "updated":
            updated += 1
        elif result == "skipped":
            skipped += 1
        else:
            failed += 1

    if not options.dry_run:
        append_calendar_dates(calendar_path, target_dates)

    return SyncAkshareQlibBarsArtifacts(
        qlib_data_dir=qlib_dir,
        calendar_start=calendar[0],
        calendar_end_before=last_calendar_date,
        calendar_end_after=target_dates[-1],
        target_dates=target_dates,
        instruments=len(instruments),
        updated=updated,
        skipped=skipped,
        failed=failed,
        dry_run=options.dry_run,
    )


def sync_one_instrument(
    ak: Any,
    qlib_dir: Path,
    instrument: str,
    calendar: list[str],
    target_dates: tuple[str, ...],
    last_calendar_date: str,
    end: str,
    adjust: str,
    request_timeout: float,
    dry_run: bool,
) -> str:
    feature_dir = qlib_dir / "features" / instrument.lower()
    try:
        bars = fetch_stock_bars(ak, instrument, last_calendar_date, end, adjust, request_timeout)
        if append_instrument_bars(feature_dir, calendar, target_dates, bars, dry_run=dry_run):
            return "updated"
        return "skipped"
    except Exception:
        return "failed"


def render_sync_akshare_qlib_bars_summary(artifacts: SyncAkshareQlibBarsArtifacts) -> str:
    dates = ",".join(artifacts.target_dates) if artifacts.target_dates else "-"
    return "\n".join(
        [
            "道藏 Alpha AKShare Qlib bars synced",
            f"qlib_data_dir: {artifacts.qlib_data_dir}",
            f"calendar: {artifacts.calendar_end_before} -> {artifacts.calendar_end_after}",
            f"target_dates: {dates}",
            f"instruments: {artifacts.instruments}",
            f"updated: {artifacts.updated}",
            f"skipped: {artifacts.skipped}",
            f"failed: {artifacts.failed}",
            f"dry_run: {artifacts.dry_run}",
        ]
    )


def read_calendar(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_calendar_dates(path: Path, dates: Iterable[str]) -> None:
    current = read_calendar(path)
    merged = list(dict.fromkeys([*current, *dates]))
    merged.sort()
    path.write_text("\n".join(merged) + "\n", encoding="utf-8")


def read_target_instruments(path: Path, limit: int | None = None) -> list[str]:
    rows = read_universe_rows(path)
    instruments: list[str] = []
    for row in rows:
        instrument = str(row.get("instrument") or "").strip().upper()
        if not instrument:
            code = str(row.get("code") or "").strip()
            instrument = to_qlib_instrument(code)
        if instrument and instrument not in instruments:
            instruments.append(instrument)
        if limit is not None and len(instruments) >= limit:
            break
    return instruments


def fetch_benchmark_dates(ak: Any, benchmark: str, start: str, end: str) -> list[str]:
    symbol = index_market_symbol(benchmark)
    frame = quiet_call(
        ak.stock_zh_index_daily_tx,
        symbol=symbol,
        start_date=compact_date(start),
        end_date=compact_date(end),
    )
    if frame is None or frame.empty:
        return []
    dates = []
    for record in frame.to_dict(orient="records"):
        date_text = normalize_date_value(record.get("date"))
        if date_text:
            dates.append(date_text)
    return sorted(dict.fromkeys(dates))


def fetch_stock_bars(
    ak: Any,
    instrument: str,
    start: str,
    end: str,
    adjust: str,
    request_timeout: float = 4.0,
) -> dict[str, DailyBar]:
    code = instrument[-6:]
    frame = quiet_call(
        ak.stock_zh_a_hist_tx,
        symbol=stock_market_symbol(code),
        start_date=compact_date(start),
        end_date=compact_date(end),
        adjust=adjust,
        timeout=max(float(request_timeout), 1.0),
    )
    bars: dict[str, DailyBar] = {}
    if frame is None or frame.empty:
        return bars
    for record in frame.to_dict(orient="records"):
        date_text = normalize_date_value(record.get("date"))
        if not date_text:
            continue
        bars[date_text] = DailyBar(
            date=date_text,
            open=to_float(record.get("open")),
            high=to_float(record.get("high")),
            low=to_float(record.get("low")),
            close=to_float(record.get("close")),
            volume_lot=to_float(record.get("amount")),
        )
    return bars


def append_instrument_bars(
    feature_dir: Path,
    calendar: list[str],
    target_dates: tuple[str, ...],
    bars: dict[str, DailyBar],
    dry_run: bool = False,
) -> bool:
    if not bars:
        return False
    close_series = read_bin_series(feature_dir / "close.day.bin")
    if close_series is None:
        return False
    start_index, close_values = close_series
    last_calendar_date = calendar[-1]
    previous_bar = bars.get(last_calendar_date)
    if previous_bar is None or previous_bar.close <= 0:
        return False

    last_close = last_valid(close_values)
    if last_close is None:
        return False
    price_factor = last_close / previous_bar.close
    factor_series = read_bin_series(feature_dir / "factor.day.bin")
    stored_factor = last_valid(factor_series[1]) if factor_series is not None else None
    factor_value = price_factor if price_factor > 0 else stored_factor or 1.0

    adjclose_series = read_bin_series(feature_dir / "adjclose.day.bin")
    previous_adjclose = last_valid(adjclose_series[1]) if adjclose_series is not None else None
    previous_close = previous_bar.close

    field_values: dict[str, list[float]] = {}
    for field in ALL_FIELDS:
        existing = read_bin_series(feature_dir / f"{field}.day.bin")
        if existing is None:
            continue
        _, values = existing
        generated: list[float] = []
        local_previous_close = previous_bar.close
        local_previous_adjclose = previous_adjclose
        for target_date in target_dates:
            bar = bars.get(target_date)
            if bar is None:
                generated.append(math.nan)
                continue
            generated.append(
                calc_field_value(
                    field,
                    bar,
                    factor_value,
                    previous_close=local_previous_close,
                    previous_adjclose=local_previous_adjclose,
                )
            )
            if field == "adjclose" and local_previous_adjclose is not None and bar.close > 0:
                local_previous_adjclose = local_previous_adjclose * (bar.close / local_previous_close)
            if bar.close > 0:
                local_previous_close = bar.close
        field_values[field] = values + generated

    if not field_values:
        return False
    if dry_run:
        return True
    for field, values in field_values.items():
        write_bin_series(feature_dir / f"{field}.day.bin", start_index, values)
    return True


def calc_field_value(
    field: str,
    bar: DailyBar,
    factor: float,
    previous_close: float,
    previous_adjclose: float | None,
) -> float:
    if field in PRICE_FIELDS:
        return getattr(bar, field) * factor
    if field == "factor":
        return factor
    if field == "volume":
        return bar.volume_lot
    if field == "amount":
        return bar.volume_lot * bar.close / 10.0
    if field == "vwap":
        return ((bar.open + bar.high + bar.low + bar.close) / 4.0) * factor
    if field == "adjclose":
        if previous_adjclose is None or previous_close <= 0:
            return bar.close
        return previous_adjclose * (bar.close / previous_close)
    if field == "change":
        if previous_close <= 0:
            return 0.0
        return bar.close / previous_close - 1.0
    return math.nan


def read_bin_series(path: Path) -> tuple[int, list[float]] | None:
    if not path.exists():
        return None
    raw = path.read_bytes()
    if len(raw) < 8 or len(raw) % 4 != 0:
        return None
    values = struct.unpack("<" + "f" * (len(raw) // 4), raw)
    return int(values[0]), list(values[1:])


def write_bin_series(path: Path, start_index: int, values: list[float]) -> None:
    payload = [float(start_index), *[float(value) for value in values]]
    path.write_bytes(struct.pack("<" + "f" * len(payload), *payload))


def last_valid(values: list[float]) -> float | None:
    for value in reversed(values):
        if math.isfinite(value):
            return value
    return None


def quiet_call(func: Any, **kwargs: Any) -> Any:
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        return func(**kwargs)


def to_qlib_instrument(code: str) -> str:
    clean = "".join(ch for ch in str(code) if ch.isdigit())
    if len(clean) != 6:
        return ""
    if clean.startswith(("6", "5", "9")):
        return f"SH{clean}"
    return f"SZ{clean}"


def stock_market_symbol(code: str) -> str:
    clean = "".join(ch for ch in str(code) if ch.isdigit())
    market = "sh" if clean.startswith(("5", "6", "9")) else "sz"
    return f"{market}{clean}"


def index_market_symbol(code: str) -> str:
    clean = "".join(ch for ch in str(code) if ch.isdigit())
    market = "sz" if clean.startswith("399") else "sh"
    return f"{market}{clean}"


def normalize_date_text(value: str | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    clean = str(value).strip()
    if not clean:
        return None
    if len(clean) == 8 and clean.isdigit():
        return f"{clean[:4]}-{clean[4:6]}-{clean[6:8]}"
    return clean[:10]


def normalize_date_value(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return normalize_date_text(str(value)) or ""


def compact_date(value: str) -> str:
    return normalize_date_text(value).replace("-", "")  # type: ignore[union-attr]


def to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
