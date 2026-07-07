from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT = "data/features/beichen_daily_features_latest.csv"

POLICY_NAMES = {"政策关键词", "宏观事件"}
FLOW_NAMES = {"资金面", "龙虎榜", "北向资金", "主力资金", "ETF资金", "资金博弈"}
DISCLOSURE_NAMES = {"公告风险", "公告事件"}
SECTOR_LIFECYCLE_NAMES = {"板块生命周期", "板块启动", "板块发酵", "板块高潮", "板块退潮"}
EXPECTATION_NAMES = {"预期定价", "预期潜伏", "预期发酵", "预期透支", "利好兑现"}

SECTOR_STATE = {
    "板块启动": 1.0,
    "板块发酵": 0.5,
    "板块生命周期": 0.0,
    "板块高潮": -0.5,
    "板块退潮": -1.0,
}

EXPECTATION_STATE = {
    "预期潜伏": 1.0,
    "预期发酵": 0.5,
    "预期定价": 0.0,
    "预期透支": -1.0,
    "利好兑现": -1.0,
}

ACTION_SCORE = {
    "BUY_NOW_SMALL": 1.0,
    "BUY_WATCH": 0.6,
    "PULLBACK_WATCH": 0.4,
    "HOLD": 0.2,
    "REDUCE": -0.5,
    "EXIT": -1.0,
    "NO_TRADE": -0.8,
    "PAUSE_NEW_BUY": -0.8,
}

BREAKDOWN_NAME_TO_FEATURE = {
    "宏观事件": "beichen_policy_score",
    "政策因子": "beichen_policy_score",
    "资金博弈": "beichen_flow_score",
    "板块生命周期": "beichen_sector_lifecycle_score",
    "预期定价": "beichen_expectation_score",
}

FEATURE_COLUMNS = [
    "beichen_policy_score",
    "beichen_policy_keyword_score",
    "beichen_policy_event_count",
    "beichen_flow_score",
    "beichen_lhb_score",
    "beichen_northbound_score",
    "beichen_main_flow_score",
    "beichen_flow_event_count",
    "beichen_disclosure_score",
    "beichen_disclosure_hard_risk",
    "beichen_sector_lifecycle_score",
    "beichen_sector_lifecycle_state",
    "beichen_expectation_score",
    "beichen_expectation_state",
    "beichen_candidate_score",
    "beichen_model_pct_rank",
    "beichen_execution_score",
    "beichen_final_action_score",
]


@dataclass(frozen=True)
class ExportBeichenFeaturesOptions:
    beichen_root: str | Path = "../beichen-alpha"
    decision_log: str | Path = "data/decision_logs/recommendations.jsonl"
    output_path: str | Path = DEFAULT_OUTPUT
    min_date: str | None = None
    max_date: str | None = None


@dataclass(frozen=True)
class ExportBeichenFeaturesArtifacts:
    output_path: Path
    rows: int
    instruments: int
    dates: int
    source_records: int


def export_beichen_features(
    options: ExportBeichenFeaturesOptions,
) -> ExportBeichenFeaturesArtifacts:
    beichen_root = Path(options.beichen_root)
    decision_log = Path(options.decision_log)
    if not decision_log.is_absolute():
        decision_log = beichen_root / decision_log
    output_path = Path(options.output_path)

    records = list(read_decision_records(decision_log))
    features = [
        feature_row_from_record(record)
        for record in records
        if record_in_date_window(record, options.min_date, options.max_date)
    ]
    features = [row for row in features if row is not None]
    rows = aggregate_feature_rows(features)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "trade_date",
        "instrument",
        "code",
        "name",
        "record_count",
        "source_run_kinds",
        "last_seen_at",
        *FEATURE_COLUMNS,
    ]
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    return ExportBeichenFeaturesArtifacts(
        output_path=output_path,
        rows=len(rows),
        instruments=len({row["instrument"] for row in rows}),
        dates=len({row["trade_date"] for row in rows}),
        source_records=len(features),
    )


def render_export_beichen_features_summary(artifacts: ExportBeichenFeaturesArtifacts) -> str:
    return "\n".join(
        [
            "道藏 Alpha Beichen feature export complete",
            f"output: {artifacts.output_path}",
            f"rows: {artifacts.rows}",
            f"instruments: {artifacts.instruments}",
            f"dates: {artifacts.dates}",
            f"source_records: {artifacts.source_records}",
        ]
    )


def read_decision_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def record_in_date_window(
    record: dict[str, Any],
    min_date: str | None,
    max_date: str | None,
) -> bool:
    trade_date = trade_date_from_record(record)
    if not trade_date:
        return False
    if min_date and trade_date < min_date:
        return False
    return not (max_date and trade_date > max_date)


def feature_row_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    raw_code = str(record.get("code") or "").strip()
    if not re.fullmatch(r"\d{1,6}", raw_code):
        return None
    code = raw_code.zfill(6)
    trade_date = trade_date_from_record(record)
    if not trade_date:
        return None

    row: dict[str, Any] = {
        "trade_date": trade_date,
        "instrument": instrument_from_code(code),
        "code": code,
        "name": record.get("name", ""),
        "source_run_kinds": str(record.get("run_kind") or ""),
        "last_seen_at": str(record.get("logged_at") or record.get("as_of") or ""),
    }
    for column in FEATURE_COLUMNS:
        row[column] = 0.0

    apply_factor_scores(row, record.get("factor_scores") or [])
    apply_breakdown_fallback(row, ((record.get("rationale") or {}).get("candidate_breakdown") or ""))
    apply_record_fields(row, record)
    return row


def trade_date_from_record(record: dict[str, Any]) -> str:
    raw = str(record.get("as_of") or record.get("logged_at") or "")
    if not raw:
        return ""
    return raw[:10]


def instrument_from_code(code: str) -> str:
    prefix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
    return f"{prefix}{code}"


def apply_factor_scores(row: dict[str, Any], factor_scores: list[dict[str, Any]]) -> None:
    for factor in factor_scores:
        name = str(factor.get("name") or "")
        score = safe_float(factor.get("score"))
        passed = bool(factor.get("passed", True))

        if name in POLICY_NAMES:
            row["beichen_policy_score"] += score
            row["beichen_policy_event_count"] += 1
            if name == "政策关键词":
                row["beichen_policy_keyword_score"] += score
        if name in FLOW_NAMES:
            row["beichen_flow_score"] += score
            row["beichen_flow_event_count"] += 1
            if name == "龙虎榜":
                row["beichen_lhb_score"] += score
            elif name == "北向资金":
                row["beichen_northbound_score"] += score
            elif name == "主力资金":
                row["beichen_main_flow_score"] += score
        if name in DISCLOSURE_NAMES:
            row["beichen_disclosure_score"] += score
            if name == "公告风险" and not passed and score < 0:
                row["beichen_disclosure_hard_risk"] = 1.0
        if name in SECTOR_LIFECYCLE_NAMES:
            row["beichen_sector_lifecycle_score"] += score
            row["beichen_sector_lifecycle_state"] = merge_state(
                row["beichen_sector_lifecycle_state"],
                SECTOR_STATE.get(name, 0.0),
            )
        if name in EXPECTATION_NAMES:
            row["beichen_expectation_score"] += score
            row["beichen_expectation_state"] = merge_state(
                row["beichen_expectation_state"],
                EXPECTATION_STATE.get(name, 0.0),
            )


def apply_breakdown_fallback(row: dict[str, Any], text: str) -> None:
    for name, raw_score in re.findall(r"([\u4e00-\u9fffA-Za-z0-9/-]+)([+-]\d+)", text):
        target = BREAKDOWN_NAME_TO_FEATURE.get(name)
        if target and row.get(target, 0.0) == 0:
            row[target] = safe_float(raw_score)


def apply_record_fields(row: dict[str, Any], record: dict[str, Any]) -> None:
    scores = record.get("scores") or {}
    execution = record.get("execution") or {}
    final_action = record.get("final_action") or {}

    row["beichen_candidate_score"] = safe_float(scores.get("candidate_score") or scores.get("score"))
    row["beichen_model_pct_rank"] = safe_float(scores.get("model_pct_rank"))
    row["beichen_execution_score"] = safe_float(
        scores.get("execution_score") or execution.get("execution_score")
    )
    row["beichen_final_action_score"] = ACTION_SCORE.get(str(final_action.get("action") or ""), 0.0)


def aggregate_feature_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["trade_date"], row["instrument"]), []).append(row)

    result = []
    for (trade_date, instrument), group in sorted(grouped.items()):
        out: dict[str, Any] = {
            "trade_date": trade_date,
            "instrument": instrument,
            "code": group[-1].get("code", ""),
            "name": group[-1].get("name", ""),
            "record_count": len(group),
            "source_run_kinds": ",".join(sorted({str(item.get("source_run_kinds", "")) for item in group if item.get("source_run_kinds")})),
            "last_seen_at": max(str(item.get("last_seen_at", "")) for item in group),
        }
        for column in FEATURE_COLUMNS:
            values = [safe_float(item.get(column)) for item in group]
            if column.endswith("_hard_risk"):
                out[column] = max(values)
            else:
                out[column] = round(sum(values) / len(values), 8) if values else 0.0
        result.append(out)
    return result


def safe_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def merge_state(current: float, value: float) -> float:
    if value < 0:
        return min(current, value)
    if current < 0:
        return current
    return max(current, value)
