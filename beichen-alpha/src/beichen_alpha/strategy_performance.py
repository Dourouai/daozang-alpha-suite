from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


DEFAULT_HORIZONS = (1, 3, 5)
TARGET_FACTOR_GROUPS = ("模型分", "板块生命周期", "预期定价", "个股强弱", "资金博弈")
ACTION_ORDER = ("BUY_WATCH", "PULLBACK_WATCH", "BUY_NOW_SMALL", "HOLD", "REDUCE", "EXIT", "NO_TRADE", "UNKNOWN")
STRATEGY_ORDER = (
    "低吸反转",
    "突破确认",
    "潜伏预期",
    "预期透支",
    "防守轮动",
    "释放资金",
    "资金效率",
    "利润保护",
    "UNKNOWN",
)


@dataclass
class HorizonBucket:
    returns: list[float] = field(default_factory=list)
    max_drawdowns: list[float] = field(default_factory=list)
    target_hits: int = 0
    stop_hits: int = 0

    def add(self, outcome: dict[str, Any], horizon: int) -> None:
        value = optional_float(outcome.get(f"return_{horizon}d"))
        if value is None:
            return
        self.returns.append(value)
        drawdown = optional_float(outcome.get(f"max_drawdown_{horizon}d"))
        if drawdown is not None:
            self.max_drawdowns.append(drawdown)
        if bool(outcome.get(f"target_hit_{horizon}d")):
            self.target_hits += 1
        if bool(outcome.get(f"stop_hit_{horizon}d")):
            self.stop_hits += 1

    def metrics(self) -> dict[str, Any]:
        samples = len(self.returns)
        if samples <= 0:
            return {
                "samples": 0,
                "win_rate": None,
                "avg_return": None,
                "median_return": None,
                "worst_drawdown": None,
                "avg_drawdown": None,
                "target_hit_rate": None,
                "stop_hit_rate": None,
            }
        return {
            "samples": samples,
            "win_rate": sum(1 for item in self.returns if item > 0) / samples,
            "avg_return": mean(self.returns),
            "median_return": median(self.returns),
            "worst_drawdown": min(self.max_drawdowns) if self.max_drawdowns else None,
            "avg_drawdown": mean(self.max_drawdowns) if self.max_drawdowns else None,
            "target_hit_rate": self.target_hits / samples,
            "stop_hit_rate": self.stop_hits / samples,
        }


@dataclass
class DimensionBucket:
    key: str
    label: str
    records: int = 0
    horizons: dict[int, HorizonBucket] = field(default_factory=dict)

    def add(self, outcome: dict[str, Any], horizons: Iterable[int]) -> None:
        self.records += 1
        for horizon in horizons:
            self.horizons.setdefault(horizon, HorizonBucket()).add(outcome, horizon)

    def row(self, horizons: Iterable[int]) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "records": self.records,
            "horizons": {
                str(horizon): self.horizons.get(horizon, HorizonBucket()).metrics()
                for horizon in horizons
            },
        }


def read_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    log_path = Path(path)
    if not log_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def summarize_strategy_performance(
    records: Iterable[dict[str, Any]],
    *,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    min_samples: int = 1,
) -> dict[str, Any]:
    horizon_tuple = tuple(int(item) for item in horizons)
    all_records = list(records)
    outcome_records = [item for item in all_records if has_any_outcome(item, horizon_tuple)]
    dimensions: dict[str, dict[str, DimensionBucket]] = {
        "final_action": {},
        "strategy_profile": {},
        "factor_group": {},
    }

    for record in outcome_records:
        outcome = record.get("outcome") or {}
        action = resolve_final_action(record)
        strategy_id, strategy_label = resolve_strategy_profile(record)
        add_bucket(dimensions["final_action"], action, action, outcome, horizon_tuple)
        add_bucket(dimensions["strategy_profile"], strategy_id, strategy_label, outcome, horizon_tuple)
        for group in resolve_factor_groups(record):
            add_bucket(dimensions["factor_group"], group, group, outcome, horizon_tuple)

    return {
        "total_records": len(all_records),
        "records_with_outcome": len(outcome_records),
        "horizons": horizon_tuple,
        "min_samples": min_samples,
        "dimensions": {
            name: render_dimension_rows(name, buckets, horizon_tuple, min_samples)
            for name, buckets in dimensions.items()
        },
    }


def add_bucket(
    buckets: dict[str, DimensionBucket],
    key: str,
    label: str,
    outcome: dict[str, Any],
    horizons: Iterable[int],
) -> None:
    bucket = buckets.setdefault(key, DimensionBucket(key=key, label=label))
    bucket.add(outcome, horizons)


def render_dimension_rows(
    dimension: str,
    buckets: dict[str, DimensionBucket],
    horizons: tuple[int, ...],
    min_samples: int,
) -> list[dict[str, Any]]:
    rows = [bucket.row(horizons) for bucket in buckets.values()]
    rows = [
        row for row in rows
        if max((row["horizons"][str(horizon)]["samples"] or 0) for horizon in horizons) >= min_samples
    ]
    order = ACTION_ORDER if dimension == "final_action" else STRATEGY_ORDER if dimension == "strategy_profile" else TARGET_FACTOR_GROUPS
    order_map = {name: index for index, name in enumerate(order)}
    return sorted(
        rows,
        key=lambda row: (
            order_map.get(row["label"], order_map.get(row["key"], 999)),
            -max((row["horizons"][str(horizon)]["samples"] or 0) for horizon in horizons),
            row["label"],
        ),
    )


def has_any_outcome(record: dict[str, Any], horizons: Iterable[int]) -> bool:
    outcome = record.get("outcome") or {}
    if not isinstance(outcome, dict):
        return False
    return any(optional_float(outcome.get(f"return_{horizon}d")) is not None for horizon in horizons)


def resolve_final_action(record: dict[str, Any]) -> str:
    final = record.get("final_action")
    if isinstance(final, dict) and final.get("action"):
        return str(final["action"])
    status = str(record.get("status") or "")
    action = str(record.get("action") or "")
    strategy_id, _ = resolve_strategy_profile(record)
    if status in {"继续持有", "持仓正常"} or action == "继续持有":
        return "HOLD"
    if status in {"退出优先", "持仓风险"} or action in {"退出优先", "触及止损区，按T+1纪律处理"}:
        return "EXIT"
    if status in {"减仓优先", "买点弱化", "时间止损优先", "资金效率观察", "止盈优先", "跌回确认", "目标区"}:
        return "REDUCE"
    if status in {"排除", "失效", "偏离", "不追"}:
        return "NO_TRADE"
    if strategy_id == "expectation_priced_in":
        return "NO_TRADE"
    if status in {"可执行", "条件执行", "突破", "观察", "贴线", "贴线站上", "首次站上"}:
        return "PULLBACK_WATCH" if strategy_id == "pullback_reversal" else "BUY_WATCH"
    return "UNKNOWN"


def resolve_strategy_profile(record: dict[str, Any]) -> tuple[str, str]:
    profile = record.get("strategy_profile")
    if isinstance(profile, dict):
        profile_id = str(profile.get("id") or profile.get("name") or "UNKNOWN")
        label = strategy_label(profile_id, str(profile.get("name") or profile_id))
        return profile_id, label

    text = record_search_text(record)
    final = record.get("final_action")
    final_action = str(final.get("action") if isinstance(final, dict) else "")
    status = str(record.get("status") or "")
    group = str(record.get("group") or "")
    name = str(record.get("name") or "")

    if "低吸" in text or final_action == "PULLBACK_WATCH":
        return "pullback_reversal", "低吸反转"
    if "预期透支" in text or "利好兑现" in text or "预期定价-" in text:
        return "expectation_priced_in", "预期透支"
    if "预期潜伏" in text or "预期定价+" in text:
        return "expectation_setup", "潜伏预期"
    if any(key in group + name + text for key in ("银行", "公用事业", "水电", "电力", "高股息", "防守")):
        return "defensive_rotation", "防守轮动"
    if status in {"可执行", "条件执行", "突破", "贴线站上", "首次站上"}:
        return "breakout_watch", "突破确认"
    if status in {"减仓优先", "资金效率观察", "时间止损优先", "买点弱化", "跌回确认"}:
        return "capital_release", "释放资金"
    return "UNKNOWN", "UNKNOWN"


def strategy_label(profile_id: str, fallback: str) -> str:
    labels = {
        "pullback_reversal": "低吸反转",
        "pullback_hold": "低吸反转",
        "breakout_watch": "突破确认",
        "breakout_confirmed": "突破确认",
        "expectation_setup": "潜伏预期",
        "expectation_priced_in": "预期透支",
        "defensive_rotation": "防守轮动",
        "capital_release": "释放资金",
        "capital_efficiency": "资金效率",
        "profit_protection": "利润保护",
    }
    return labels.get(profile_id, fallback or "UNKNOWN")


def resolve_factor_groups(record: dict[str, Any]) -> tuple[str, ...]:
    text = record_search_text(record)
    groups = [group for group in TARGET_FACTOR_GROUPS if group in text]
    return tuple(groups)


def record_search_text(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("status", "action", "group", "industry"):
        parts.append(str(record.get(key) or ""))
    for section_name in ("rationale", "risk", "scores", "portfolio"):
        section = record.get(section_name)
        if isinstance(section, dict):
            parts.extend(str(value) for value in section.values())
    return " ".join(parts)


def render_strategy_performance_report(summary: dict[str, Any]) -> str:
    horizons = tuple(int(item) for item in summary.get("horizons", DEFAULT_HORIZONS))
    lines = [
        "北辰 Alpha｜策略复盘归因报告",
        f"- 日志记录: {summary.get('total_records', 0)} 条",
        f"- 可统计 outcome: {summary.get('records_with_outcome', 0)} 条",
        "",
    ]
    if summary.get("records_with_outcome", 0) <= 0:
        lines.extend(
            [
                "暂无可统计的 outcome。",
                "先运行 backfill-outcomes 生成带未来收益的日志，再运行 strategy-performance。",
            ]
        )
        return "\n".join(lines)

    dimensions = summary.get("dimensions") or {}
    sections = (
        ("final_action", "按 final_action"),
        ("strategy_profile", "按 strategy_profile"),
        ("factor_group", "按因子组"),
    )
    for key, title in sections:
        lines.append(title)
        rows = dimensions.get(key) or []
        if not rows:
            lines.append("- 样本不足。")
            lines.append("")
            continue
        for row in rows:
            lines.append(format_row(row, horizons))
        lines.append("")
    lines.append("仅用于个人研究和策略测试，不构成投资建议。")
    return "\n".join(lines).rstrip()


def format_row(row: dict[str, Any], horizons: tuple[int, ...]) -> str:
    label = row.get("label") or row.get("key") or "-"
    parts = [f"- {label}: 记录 {row.get('records', 0)}"]
    for horizon in horizons:
        metrics = (row.get("horizons") or {}).get(str(horizon), {})
        samples = metrics.get("samples") or 0
        if samples <= 0:
            parts.append(f"{horizon}日 无样本")
            continue
        parts.append(
            (
                f"{horizon}日 n={samples} 胜率{fmt_pct(metrics.get('win_rate'))} "
                f"均收{fmt_pct(metrics.get('avg_return'), signed=True)} "
                f"回撤{fmt_pct(metrics.get('worst_drawdown'), signed=True)} "
                f"止损{fmt_pct(metrics.get('stop_hit_rate'))} "
                f"目标{fmt_pct(metrics.get('target_hit_rate'))}"
            )
        )
    return " | ".join(parts)


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_pct(value: Any, *, signed: bool = False) -> str:
    number = optional_float(value)
    if number is None:
        return "-"
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:.1%}"
