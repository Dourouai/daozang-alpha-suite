from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from .global_linkage_source import (
    DEFAULT_FRED_SERIES,
    DEFAULT_YAHOO_TICKERS,
    FredSeries,
    YahooTicker,
    fetch_text,
    parse_fred_csv,
)


@dataclass(frozen=True)
class GlobalFeatureDataset:
    rows: list[dict[str, float | str]]
    source_health: tuple[str, ...]
    generated_at: datetime

    @property
    def columns(self) -> tuple[str, ...]:
        keys: set[str] = set()
        for row in self.rows:
            keys.update(row.keys())
        return ("date", *tuple(sorted(key for key in keys if key != "date")))


class GlobalFeatureSource:
    """Build date-indexed global features for model training and inference."""

    def __init__(
        self,
        fred_series: Iterable[FredSeries] = DEFAULT_FRED_SERIES,
        yahoo_tickers: Iterable[YahooTicker] = DEFAULT_YAHOO_TICKERS,
        period: str = "5y",
        start: str | None = None,
        end: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.fred_series = tuple(fred_series)
        self.yahoo_tickers = tuple(yahoo_tickers)
        self.period = period
        self.start = normalize_date_arg(start) or start_date_from_period(period)
        self.end = normalize_date_arg(end)
        self.timeout = timeout

    def load(self) -> GlobalFeatureDataset:
        fred_points: dict[str, list[tuple[str, float]]] = {}
        yahoo_points: dict[str, list[tuple[str, float]]] = {}
        health: list[str] = []

        for series in self.fred_series:
            try:
                fred_points[series.code] = fetch_fred_points(series, self.start, self.end, self.timeout)
                health.append(f"FRED:{series.code} OK({len(fred_points[series.code])})")
            except Exception as exc:
                health.append(f"FRED:{series.code} FAIL({type(exc).__name__})")

        for ticker in self.yahoo_tickers:
            try:
                yahoo_points[ticker.symbol] = fetch_yahoo_close_points(
                    ticker,
                    period=self.period,
                    start=self.start,
                    end=self.end,
                )
                health.append(f"yfinance:{ticker.symbol} OK({len(yahoo_points[ticker.symbol])})")
            except Exception as exc:
                health.append(f"yfinance:{ticker.symbol} FAIL({type(exc).__name__})")

        rows = build_global_feature_rows(fred_points, yahoo_points)
        return GlobalFeatureDataset(rows=rows, source_health=tuple(health), generated_at=datetime.now())


def fetch_fred_points(
    series: FredSeries,
    start: str | None = None,
    end: str | None = None,
    timeout: float = 15.0,
) -> list[tuple[str, float]]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series.code}"
    return filter_points(parse_fred_csv(fetch_text(url, timeout=timeout), series.code), start, end)


def fetch_yahoo_close_points(
    ticker: YahooTicker,
    period: str = "5y",
    start: str | None = None,
    end: str | None = None,
) -> list[tuple[str, float]]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError(
            "yfinance is not installed. Install it with: python3 -m pip install yfinance"
        ) from exc

    history_kwargs: dict[str, str | bool] = {
        "interval": "1d",
        "auto_adjust": False,
    }
    if start or end:
        if start:
            history_kwargs["start"] = start
        if end:
            history_kwargs["end"] = end
    else:
        history_kwargs["period"] = period

    frame = yf.Ticker(ticker.symbol).history(**history_kwargs)
    if frame is None or frame.empty or "Close" not in frame:
        return []
    close = frame["Close"].dropna()
    points: list[tuple[str, float]] = []
    for index, value in close.items():
        date_text = index.strftime("%Y-%m-%d") if hasattr(index, "strftime") else str(index)
        points.append((date_text[:10], float(value)))
    return filter_points(points, start, end)


def build_global_feature_rows(
    fred_points: dict[str, list[tuple[str, float]]],
    yahoo_points: dict[str, list[tuple[str, float]]],
) -> list[dict[str, float | str]]:
    rows_by_date: dict[str, dict[str, float | str]] = {}
    for code, points in fred_points.items():
        add_numeric_features(rows_by_date, f"fred_{feature_name(code)}", points, mode="diff")
    for symbol, points in yahoo_points.items():
        add_numeric_features(rows_by_date, f"yf_{feature_name(symbol)}", points, mode="return")
    return [rows_by_date[date] for date in sorted(rows_by_date)]


def add_numeric_features(
    rows_by_date: dict[str, dict[str, float | str]],
    prefix: str,
    points: list[tuple[str, float]],
    mode: str,
) -> None:
    clean_points = sorted(points, key=lambda item: item[0])
    for index, (date_text, value) in enumerate(clean_points):
        row = rows_by_date.setdefault(date_text, {"date": date_text})
        row[prefix] = round_float(value)
        if index >= 1:
            previous = clean_points[index - 1][1]
            row[f"{prefix}_{mode}_1d"] = round_float(calc_delta(value, previous, mode))
        if index >= 5:
            previous_5d = clean_points[index - 5][1]
            row[f"{prefix}_{mode}_5d"] = round_float(calc_delta(value, previous_5d, mode))


def calc_delta(value: float, previous: float, mode: str) -> float:
    if mode == "return":
        return 0.0 if previous == 0 else value / previous - 1
    return value - previous


def write_global_feature_dataset(
    dataset: GlobalFeatureDataset,
    out_path: str | Path,
    meta_path: str | Path | None = None,
) -> tuple[Path, Path | None]:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(dataset.columns)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in dataset.rows:
            writer.writerow(row)

    meta_saved: Path | None = None
    if meta_path:
        meta_saved = Path(meta_path)
        meta_saved.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "generated_at": dataset.generated_at.isoformat(timespec="seconds"),
            "rows": len(dataset.rows),
            "columns": columns,
            "source_health": list(dataset.source_health),
            "note": "Model feature dataset. Join to A-share samples by as-of date and lag to avoid lookahead.",
        }
        meta_saved.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, meta_saved


def filter_points(
    points: list[tuple[str, float]],
    start: str | None = None,
    end: str | None = None,
) -> list[tuple[str, float]]:
    return [
        (date_text, value)
        for date_text, value in points
        if (start is None or date_text >= start) and (end is None or date_text <= end)
    ]


def normalize_date_arg(value: str | None) -> str | None:
    if not value:
        return None
    clean = str(value).strip()
    if len(clean) == 8 and clean.isdigit():
        return f"{clean[:4]}-{clean[4:6]}-{clean[6:8]}"
    return clean


def start_date_from_period(period: str) -> str | None:
    clean = str(period or "").strip().lower()
    if not clean:
        return None
    try:
        amount = int(clean[:-1])
    except ValueError:
        return None
    unit = clean[-1]
    today = date.today()
    if unit == "y":
        return today.replace(year=today.year - amount).isoformat()
    if unit == "m":
        return (today - timedelta(days=amount * 31)).isoformat()
    if unit == "d":
        return (today - timedelta(days=amount)).isoformat()
    return None


def feature_name(value: str) -> str:
    clean = re.sub(r"[^0-9a-zA-Z]+", "_", value.lower()).strip("_")
    return clean or "unknown"


def round_float(value: float) -> float:
    return round(float(value), 10)
