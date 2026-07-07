from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from beichen_alpha.data_sources.flow_source import FlowSnapshot
from beichen_alpha.data_sources.market_structure_source import AkshareMarketStructureSource
from beichen_alpha.data_sources.sentiment_source import SentimentSnapshot
from beichen_alpha.models import GlobalLinkageSnapshot, MarketStructureSnapshot


def write_snapshot_json(path: str | Path, payload: dict[str, Any]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(normalize_json(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def normalize_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return normalize_json(asdict(value))
    if isinstance(value, dict):
        return {str(key): normalize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_json(item) for item in value]
    return value


def snapshot_payload(kind: str, as_of: datetime, symbols: list[str], snapshot: Any, error: str = "") -> dict[str, Any]:
    source_health = list(getattr(snapshot, "source_health", ()) or ()) if snapshot is not None else []
    return {
        "kind": kind,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(timespec="seconds"),
        "symbols": symbols,
        "source_health": source_health,
        "error": error,
        "snapshot": snapshot,
    }


def flow_daily_rows(snapshot: FlowSnapshot, symbols: list[str], as_of: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    row_date = as_of.date().isoformat()
    for code in symbols:
        lhb_records = sorted(
            snapshot.lhb_records.get(code, []),
            key=lambda item: item.trade_date or date.min,
            reverse=True,
        )
        northbound_records = sorted(
            snapshot.northbound_records.get(code, []),
            key=lambda item: item.trade_date or date.min,
            reverse=True,
        )
        fund_records = sorted(
            snapshot.fund_flow_records.get(code, []),
            key=lambda item: item.trade_date or date.min,
            reverse=True,
        )
        latest_northbound = northbound_records[0] if northbound_records else None
        latest_fund = fund_records[0] if fund_records else None
        lhb_net_amount = sum(float(item.net_amount or 0.0) for item in lhb_records)
        rows.append(
            {
                "date": row_date,
                "code": code,
                "flow_lhb_count": len(lhb_records),
                "flow_lhb_net_amount": round_float(lhb_net_amount),
                "flow_northbound_net_buy_10k": round_float(
                    latest_northbound.net_buy_10k if latest_northbound else 0.0
                ),
                "flow_northbound_hold_pct": round_float(
                    latest_northbound.hold_pct if latest_northbound else 0.0
                ),
                "flow_northbound_consecutive_days": (
                    latest_northbound.consecutive_days if latest_northbound else 0
                ),
                "flow_main_net_inflow_10k": round_float(
                    latest_fund.main_net_inflow if latest_fund else 0.0
                ),
            }
        )
    return rows


def global_daily_row(snapshot: GlobalLinkageSnapshot, as_of: datetime) -> dict[str, Any]:
    row: dict[str, Any] = {
        "date": as_of.date().isoformat(),
        "global_posture": snapshot.posture,
        "global_score": snapshot.score,
        "global_signal_count": len(snapshot.signals),
        "global_signals": "；".join(snapshot.signals[:5]),
    }
    for indicator in snapshot.indicators:
        prefix = "global_" + feature_name(indicator.code)
        row[f"{prefix}_latest"] = round_float(indicator.latest)
        row[f"{prefix}_change"] = "" if indicator.change is None else round_float(indicator.change)
        row[f"{prefix}_change_pct"] = (
            "" if indicator.change_pct is None else round_float(indicator.change_pct)
        )
    return row


def sentiment_daily_rows(
    snapshot: SentimentSnapshot,
    symbols: list[str],
    as_of: datetime,
    market_structure: MarketStructureSnapshot | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    row_date = as_of.date().isoformat()
    futures = {item.contract: item for item in snapshot.futures_basis}
    basis_values = [float(item.basis_pct or 0.0) for item in snapshot.futures_basis]
    avg_basis = sum(basis_values) / len(basis_values) if basis_values else 0.0
    market_columns = market_structure_columns(market_structure)
    for code in symbols:
        zt_records = sorted(
            snapshot.zt_records.get(code, []),
            key=lambda item: item.trade_date or date.min,
            reverse=True,
        )
        margin_records = sorted(
            snapshot.margin_records.get(code, []),
            key=lambda item: item.trade_date or date.min,
            reverse=True,
        )
        latest_zt = zt_records[0] if zt_records else None
        latest_margin = margin_records[0] if margin_records else None
        row = {
            "date": row_date,
            "code": code,
            "sentiment_zt_count": len(zt_records),
            "sentiment_zt_change_pct": round_float(latest_zt.change_pct if latest_zt else 0.0),
            "sentiment_zt_turnover_rate": round_float(
                latest_zt.turnover_rate if latest_zt else 0.0
            ),
            "sentiment_zt_seal_amount_wan": round_float(
                latest_zt.seal_amount_wan if latest_zt else 0.0
            ),
            "sentiment_margin_balance": round_float(
                latest_margin.margin_balance if latest_margin else 0.0
            ),
            "sentiment_margin_net_flow": round_float(
                latest_margin.net_margin_flow if latest_margin else 0.0
            ),
            "sentiment_futures_avg_basis_pct": round_float(avg_basis),
            "sentiment_futures_if_basis_pct": round_float(get_basis(futures, "IF")),
            "sentiment_futures_ic_basis_pct": round_float(get_basis(futures, "IC")),
            "sentiment_futures_im_basis_pct": round_float(get_basis(futures, "IM")),
            "sentiment_futures_ih_basis_pct": round_float(get_basis(futures, "IH")),
        }
        row.update(market_columns)
        rows.append(row)
    return rows


def market_structure_columns(snapshot: MarketStructureSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {
            "market_breadth": "",
            "market_limit_up_count": "",
            "market_limit_down_count": "",
            "market_turnover_100m": "",
            "market_margin_balance_100m": "",
            "market_margin_buy_turnover_ratio": "",
            "market_northbound_net_buy_100m": "",
            "market_northbound_5d_net_buy_100m": "",
        }
    return {
        "market_breadth": none_or_round(snapshot.breadth),
        "market_limit_up_count": snapshot.limit_up_count if snapshot.limit_up_count is not None else "",
        "market_limit_down_count": snapshot.limit_down_count if snapshot.limit_down_count is not None else "",
        "market_turnover_100m": none_or_round(snapshot.turnover_100m),
        "market_margin_balance_100m": none_or_round(snapshot.margin_balance_100m),
        "market_margin_buy_turnover_ratio": none_or_round(snapshot.margin_buy_turnover_ratio),
        "market_northbound_net_buy_100m": none_or_round(snapshot.northbound_net_buy_100m),
        "market_northbound_5d_net_buy_100m": none_or_round(snapshot.northbound_5d_net_buy_100m),
    }


def load_market_structure(as_of: datetime) -> MarketStructureSnapshot | None:
    return AkshareMarketStructureSource(as_of=as_of).load()


def combine_factor_rows(
    flow_rows: list[dict[str, Any]],
    sentiment_rows: list[dict[str, Any]],
    global_row: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in flow_rows + sentiment_rows:
        key = (str(row.get("date") or ""), str(row.get("code") or ""))
        if not key[0] or not key[1]:
            continue
        by_key.setdefault(key, {"date": key[0], "code": key[1]}).update(row)
    if global_row:
        for row in by_key.values():
            row.update({key: value for key, value in global_row.items() if key != "date"})
    return [by_key[key] for key in sorted(by_key)]


def upsert_csv_rows(path: str | Path, rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    merged_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    key_order: list[tuple[str, ...]] = []
    if output.exists():
        with output.open("r", newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                key = row_key(row, key_fields)
                if key not in merged_by_key:
                    key_order.append(key)
                merged_by_key[key] = row
    for row in rows:
        key = row_key(row, key_fields)
        if key not in merged_by_key:
            key_order.append(key)
            merged_by_key[key] = {}
        merged_by_key[key] = {**merged_by_key[key], **row}
    merged = [merged_by_key[key] for key in key_order]
    columns = sorted({key for row in merged for key in row.keys()})
    columns = ordered_columns(columns)
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in sorted(merged, key=lambda item: tuple(str(item.get(field, "")) for field in columns[:2])):
            writer.writerow(row)
    return output


def row_key(row: dict[str, Any], key_fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(row.get(field, "")) for field in key_fields)


def ordered_columns(columns: list[str]) -> list[str]:
    preferred = ["date", "code"]
    return [item for item in preferred if item in columns] + [
        item for item in columns if item not in preferred
    ]


def get_basis(values: dict[str, Any], contract: str) -> float:
    item = values.get(contract)
    return float(item.basis_pct or 0.0) if item is not None else 0.0


def none_or_round(value: float | None) -> float | str:
    return "" if value is None else round_float(value)


def round_float(value: float) -> float:
    return round(float(value or 0.0), 10)


def feature_name(value: str) -> str:
    clean = re.sub(r"[^0-9a-zA-Z]+", "_", str(value).lower()).strip("_")
    return clean or "unknown"
