from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DaozangConfig


CANONICAL_COLUMNS = (
    "trade_date",
    "instrument",
    "score",
    "rank",
    "pct_rank",
    "model",
    "feature_set",
    "horizon_days",
    "universe",
)
OPTIONAL_COLUMNS = (
    "score_1d",
    "score_3d",
    "score_5d",
    "pct_rank_1d",
    "pct_rank_3d",
    "pct_rank_5d",
    "expected_return_1d",
    "up_probability_1d",
    "expected_return_3d",
    "up_probability_3d",
    "expected_return_5d",
    "up_probability_5d",
)


@dataclass(frozen=True)
class ExportScoresOptions:
    input_path: str | Path | None = None
    output_path: str | Path | None = None


@dataclass(frozen=True)
class ExportScoresArtifacts:
    source_path: Path
    output_path: Path
    rows: int
    trade_date: str


def export_scores(config: DaozangConfig, options: ExportScoresOptions) -> ExportScoresArtifacts:
    export_dir = Path(config.export.path)
    source_path = resolve_source_path(export_dir, options.input_path)
    output_path = Path(options.output_path) if options.output_path else export_dir / "alpha_scores_latest.csv"
    rows = read_score_rows(source_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_canonical_scores(rows, output_path)
    return ExportScoresArtifacts(
        source_path=source_path,
        output_path=output_path,
        rows=len(rows),
        trade_date=str(rows[0]["trade_date"]),
    )


def render_export_scores_summary(artifacts: ExportScoresArtifacts) -> str:
    return "\n".join(
        [
            "道藏 Alpha score export complete",
            f"source: {artifacts.source_path}",
            f"latest: {artifacts.output_path}",
            f"trade_date: {artifacts.trade_date}",
            f"rows: {artifacts.rows}",
        ]
    )


def resolve_source_path(export_dir: Path, input_path: str | Path | None) -> Path:
    if input_path:
        path = Path(input_path)
        if not path.exists():
            raise FileNotFoundError(f"score input not found: {path}")
        return path

    candidates = [
        path
        for path in export_dir.glob("alpha_scores_*.csv")
        if path.name != "alpha_scores_latest.csv"
    ]
    if candidates:
        return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))

    latest_path = export_dir / "alpha_scores_latest.csv"
    if latest_path.exists():
        return latest_path

    raise FileNotFoundError(f"no alpha score CSV found in {export_dir}")


def read_score_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ValueError(f"empty score CSV: {path}")
        missing = [column for column in CANONICAL_COLUMNS if column not in reader.fieldnames]
        if missing:
            raise ValueError(f"score CSV missing columns: {', '.join(missing)}")
        rows = [normalize_score_row(row) for row in reader]
    if not rows:
        raise ValueError(f"score CSV has no rows: {path}")
    return rows


def normalize_score_row(row: dict[str, str]) -> dict[str, Any]:
    normalized = {
        "trade_date": str(row["trade_date"]).strip(),
        "instrument": normalize_instrument(row["instrument"]),
        "score": float(row["score"]),
        "rank": int(float(row["rank"])),
        "pct_rank": clamp_float(float(row["pct_rank"]), 0.0, 1.0),
        "model": str(row["model"]).strip(),
        "feature_set": str(row["feature_set"]).strip(),
        "horizon_days": int(float(row["horizon_days"])),
        "universe": str(row["universe"]).strip(),
    }
    for column in OPTIONAL_COLUMNS:
        if column in row:
            normalized[column] = normalize_optional_float(row.get(column))
    if not normalized["trade_date"]:
        raise ValueError("score row has empty trade_date")
    if not normalized["instrument"]:
        raise ValueError("score row has empty instrument")
    return normalized


def write_canonical_scores(rows: list[dict[str, Any]], output_path: Path) -> None:
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    columns = tuple(CANONICAL_COLUMNS) + tuple(
        column for column in OPTIONAL_COLUMNS if any(column in row for row in rows)
    )
    with temp_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    shutil.move(str(temp_path), str(output_path))


def normalize_instrument(value: str) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        code, exchange = text.split(".", 1)
        code = code.strip()
        exchange = exchange.strip()
        if exchange in {"SH", "SSE"}:
            return f"SH{code}"
        if exchange in {"SZ", "SZSE"}:
            return f"SZ{code}"
        if exchange in {"BJ", "BSE"}:
            return f"BJ{code}"
    if len(text) == 8 and text[:2] in {"SH", "SZ", "BJ"} and text[2:].isdigit():
        return text
    if len(text) == 6 and text.isdigit():
        if text.startswith(("6", "9")):
            return f"SH{text}"
        if text.startswith(("0", "2", "3")):
            return f"SZ{text}"
        if text.startswith(("4", "8")):
            return f"BJ{text}"
    return text


def clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_optional_float(value: Any) -> float | str:
    text = str(value or "").strip()
    if not text:
        return ""
    return float(text)
