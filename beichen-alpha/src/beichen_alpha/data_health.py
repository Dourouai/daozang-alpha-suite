"""Pre-trade data health validation.

Checks model score freshness, source data availability, and risk calendar
coverage before generating trade plans. Produces a structured health report
suitable for Feishu cards or CLI output.

Rationale: A trade plan built on stale model scores or broken data sources
is worse than no plan at all. This module makes data quality visible.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SourceCheck:
    name: str
    status: str  # "ok", "stale", "missing", "error", "optional"
    detail: str
    score: int  # +2 ok, 0 stale, -3 missing, -5 error


@dataclass
class DataHealthReport:
    as_of: datetime
    overall_status: str  # "healthy", "degraded", "unhealthy"
    overall_score: int  # sum of all source scores
    checks: list[SourceCheck] = field(default_factory=list)
    model_score_trade_date: str = ""
    model_score_rows: int = 0
    model_score_covered: int = 0
    active_universe_rows: int = 0
    positions_count: int = 0
    decision_log_entries: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return self.overall_status == "healthy"

    @property
    def warning_sources(self) -> list[str]:
        return [c.name for c in self.checks if c.status in ("stale", "missing", "error")]

    @property
    def ok_sources(self) -> list[str]:
        return [c.name for c in self.checks if c.status == "ok"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_data_health(
    *,
    beichen_root: str | Path = ".",
    daozang_root: str | Path = "../daozang-alpha",
    positions_path: str = "data/positions/current_positions.json",
    decision_log_path: str = "data/decision_logs/recommendations.jsonl",
    model_scores_path: str = "../daozang-alpha/data/exports/alpha_scores_latest.csv",
    active_universe_path: str = "../daozang-alpha/data/universe/active_universe.csv",
    risk_calendar_path: str = "../daozang-alpha/data/universe/akshare_risk_calendar.csv",
    industry_map_path: str = "../daozang-alpha/data/universe/akshare_industry_map.csv",
    max_model_score_age_days: int = 2,
    max_universe_age_days: int = 3,
    max_risk_calendar_age_days: int = 5,
    min_model_score_rows: int = 500,
    min_active_universe_rows: int = 500,
    config_env_path: str = "config/local.env",
    as_of: datetime | None = None,
) -> DataHealthReport:
    """Run a comprehensive data health check.

    Args:
        beichen_root: Path to beichen-alpha project root.
        daozang_root: Path to daozang-alpha project root.
        positions_path: Relative path to current positions JSON.
        decision_log_path: Relative path to decision log JSONL.
        model_scores_path: Relative path to Daozang model score CSV.
        active_universe_path: Relative path to active universe CSV.
        risk_calendar_path: Relative path to risk calendar CSV.
        industry_map_path: Relative path to industry map CSV.
        max_model_score_age_days: Max acceptable age of model scores.
        max_universe_age_days: Max acceptable age of active universe.
        max_risk_calendar_age_days: Max acceptable age of risk calendar.
        min_model_score_rows: Minimum expected rows in model score CSV.
        min_active_universe_rows: Minimum expected rows in active universe.
        config_env_path: Path to config/local.env for env var checks.
        as_of: Reference timestamp (default: now).

    Returns:
        DataHealthReport with consolidated status.
    """
    ref_time = as_of or datetime.now()
    beichen = Path(beichen_root)
    daozang = Path(daozang_root)

    checks: list[SourceCheck] = []

    # --- Model scores ---
    model_path = beichen / model_scores_path
    model_trade_date = ""
    model_rows = 0
    if model_path.exists():
        try:
            import csv
            with model_path.open("r", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
            model_rows = len(rows)
            # Extract trade_date from first row
            if rows:
                model_trade_date = rows[0].get("trade_date", "")
            # Check freshness
            if model_trade_date:
                try:
                    score_date = datetime.strptime(model_trade_date[:10], "%Y-%m-%d").date()
                    age = (ref_time.date() - score_date).days
                    if age <= max_model_score_age_days:
                        checks.append(SourceCheck(
                            "道藏模型分数",
                            "ok",
                            f"{model_rows}行, 日期 {model_trade_date[:10]}",
                            2,
                        ))
                    else:
                        checks.append(SourceCheck(
                            "道藏模型分数",
                            "stale",
                            f"{model_rows}行, 日期 {model_trade_date[:10]} (已{age}天)",
                            0,
                        ))
                except ValueError:
                    checks.append(SourceCheck(
                        "道藏模型分数",
                        "stale",
                        f"{model_rows}行, 无法解析日期",
                        0,
                    ))
            else:
                checks.append(SourceCheck(
                    "道藏模型分数",
                    "stale",
                    f"{model_rows}行, 无trade_date",
                    0,
                ))
            if model_rows < min_model_score_rows:
                checks[-1].status = "stale"
                checks[-1].score = 0
                checks[-1].detail += f" (行数{model_rows} < 期望{min_model_score_rows})"
        except Exception as exc:
            checks.append(SourceCheck(
                "道藏模型分数",
                "error",
                f"读取失败: {exc}",
                -5,
            ))
    else:
        checks.append(SourceCheck(
            "道藏模型分数",
            "missing",
            f"文件不存在: {model_path}",
            -3,
        ))

    # --- Active universe ---
    universe_path = beichen / active_universe_path
    universe_rows = 0
    if universe_path.exists():
        try:
            import csv
            with universe_path.open("r", encoding="utf-8") as fh:
                universe_rows = sum(1 for _ in csv.reader(fh)) - 1  # exclude header
            mtime = datetime.fromtimestamp(universe_path.stat().st_mtime)
            age = (ref_time - mtime).days
            if universe_rows >= min_active_universe_rows and age <= max_universe_age_days:
                checks.append(SourceCheck(
                    "活跃股票池",
                    "ok",
                    f"{universe_rows}只, 更新于{mtime.strftime('%Y-%m-%d %H:%M')}",
                    2,
                ))
            else:
                issues = []
                if universe_rows < min_active_universe_rows:
                    issues.append(f"仅{universe_rows}只")
                if age > max_universe_age_days:
                    issues.append(f"已{age}天未更新")
                checks.append(SourceCheck(
                    "活跃股票池",
                    "stale",
                    ", ".join(issues) if issues else f"{universe_rows}只",
                    0,
                ))
        except Exception as exc:
            checks.append(SourceCheck(
                "活跃股票池",
                "error",
                f"读取失败: {exc}",
                -5,
            ))
    else:
        checks.append(SourceCheck(
            "活跃股票池",
            "missing",
            f"文件不存在: {universe_path}",
            -3,
        ))

    # --- Risk calendar ---
    risk_path = beichen / risk_calendar_path
    if risk_path.exists():
        mtime = datetime.fromtimestamp(risk_path.stat().st_mtime)
        age = (ref_time - mtime).days
        if age <= max_risk_calendar_age_days:
            checks.append(SourceCheck(
                "风险日历",
                "ok",
                f"更新于{mtime.strftime('%Y-%m-%d %H:%M')}",
                2,
            ))
        else:
            checks.append(SourceCheck(
                "风险日历",
                "stale",
                f"{age}天未更新",
                0,
            ))
    else:
        checks.append(SourceCheck(
            "风险日历",
            "missing",
            f"文件不存在: {risk_path}",
            -3,
        ))

    # --- Industry map ---
    industry_path = beichen / industry_map_path
    industry_rows = 0
    if industry_path.exists():
        mtime = datetime.fromtimestamp(industry_path.stat().st_mtime)
        try:
            import csv
            with industry_path.open("r", encoding="utf-8") as fh:
                industry_rows = max(sum(1 for _ in csv.reader(fh)) - 1, 0)
        except Exception:
            industry_rows = 0
        checks.append(SourceCheck(
            "行业分类",
            "ok",
            f"{industry_rows}行, 更新于{mtime.strftime('%Y-%m-%d')}",
            1,
        ))
    else:
        checks.append(SourceCheck(
            "行业分类",
            "missing",
            f"文件不存在: {industry_path}",
            -2,
        ))

    if universe_rows > 0 or industry_rows > 0:
        checks.append(SourceCheck(
            "北辰-道藏画像接入",
            "ok",
            "交易计划默认读取 active_universe.csv 与 akshare_industry_map.csv",
            1,
        ))

    # --- Positions ---
    pos_path = beichen / positions_path
    positions_count = 0
    if pos_path.exists():
        try:
            data = json.loads(pos_path.read_text(encoding="utf-8"))
            positions_count = len(data.get("positions", []))
            checks.append(SourceCheck(
                "当前持仓",
                "ok",
                f"{positions_count}只持仓",
                2,
            ))
        except Exception as exc:
            checks.append(SourceCheck(
                "当前持仓",
                "error",
                f"读取失败: {exc}",
                -5,
            ))
    else:
        checks.append(SourceCheck(
            "当前持仓",
            "missing",
            "无持仓文件（可能为空仓）",
            0,
        ))

    # --- Decision log ---
    log_path = beichen / decision_log_path
    log_entries = 0
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8") as fh:
                log_entries = sum(1 for line in fh if line.strip())
            checks.append(SourceCheck(
                "决策日志",
                "ok",
                f"{log_entries}条记录",
                1,
            ))
        except Exception as exc:
            checks.append(SourceCheck(
                "决策日志",
                "error",
                f"读取失败: {exc}",
                -3,
            ))
    else:
        checks.append(SourceCheck(
            "决策日志",
            "missing",
            "无决策日志（新项目或首次运行）",
            0,
        ))

    # --- Config / env ---
    env_path = beichen / config_env_path
    env_vars = {}
    if env_path.exists():
        env_vars = _parse_env_file(env_path)
        webhook = env_vars.get("FEISHU_WEBHOOK", "")
        if webhook and "replace-me" not in webhook:
            checks.append(SourceCheck(
                "飞书Webhook",
                "ok",
                "已配置",
                1,
            ))
        else:
            checks.append(SourceCheck(
                "飞书Webhook",
                "missing",
                "未配置或使用占位值",
                0,
            ))
    else:
        checks.append(SourceCheck(
            "环境配置",
            "missing",
            f"文件不存在: {env_path}",
            -1,
        ))

    tushare_token = os.environ.get("TUSHARE_TOKEN") or env_vars.get("TUSHARE_TOKEN", "")
    if tushare_token:
        checks.append(SourceCheck(
            "Tushare Pro",
            "ok",
            "已配置 token；作为 P1 可选增强源",
            1,
        ))
    else:
        checks.append(SourceCheck(
            "Tushare Pro",
            "optional",
            "未配置 token；不影响 AKShare/东方财富/道藏本地免费源",
            0,
        ))

    # --- Overall assessment ---
    overall_score = sum(c.score for c in checks)
    error_count = sum(1 for c in checks if c.status == "error")
    missing_count = sum(1 for c in checks if c.status == "missing")
    stale_count = sum(1 for c in checks if c.status == "stale")

    if error_count > 0 or missing_count >= 3:
        overall_status = "unhealthy"
    elif stale_count >= 3 or missing_count >= 1:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    # Build notes
    notes: list[str] = []
    if model_trade_date and model_rows > 0:
        notes.append(f"模型分数日期: {model_trade_date[:10]}, {model_rows}行")
    if overall_status != "healthy":
        notes.append(
            f"数据健康状态: {overall_status} | "
            f"错误{error_count} 缺失{missing_count} 陈旧{stale_count}"
        )
    else:
        notes.append("数据健康状态: 正常，可以生成交易计划。")

    return DataHealthReport(
        as_of=ref_time,
        overall_status=overall_status,
        overall_score=overall_score,
        checks=checks,
        model_score_trade_date=model_trade_date,
        model_score_rows=model_rows,
        model_score_covered=model_rows,
        active_universe_rows=universe_rows,
        positions_count=positions_count,
        decision_log_entries=log_entries,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_data_health_text(report: DataHealthReport) -> str:
    """Render a data health report as plain text (suitable for Feishu text messages)."""
    lines = [
        f"📊 数据健康检查 — {report.as_of.strftime('%Y-%m-%d %H:%M')}",
        f"整体状态: {'✅ 健康' if report.is_healthy else '⚠️ 降级' if report.overall_status == 'degraded' else '❌ 异常'}",
        "",
    ]
    for check in report.checks:
        icon = {"ok": "✅", "stale": "⚠️", "missing": "❌", "error": "🚫", "optional": "ℹ️"}.get(check.status, "❓")
        lines.append(f"  {icon} {check.name}: {check.detail}")

    if report.notes:
        lines.append("")
        for note in report.notes:
            lines.append(f"📝 {note}")

    if not report.is_healthy:
        lines.append("")
        lines.append("⚠️ 数据健康降级，建议先修复数据源再运行交易计划。")

    return "\n".join(lines)


def format_data_health_card(report: DataHealthReport, title: str = "北辰 Alpha 数据健康") -> dict[str, Any]:
    """Render a data health report as a Feishu card.

    The notifier wraps this card with ``msg_type=interactive``. This function
    must return only the card body, not a full webhook payload.
    """
    status_color = {
        "healthy": "green",
        "degraded": "orange",
        "unhealthy": "red",
    }.get(report.overall_status, "red")

    status_text = {
        "healthy": "✅ 数据健康",
        "degraded": "⚠️ 数据降级",
        "unhealthy": "❌ 数据异常",
    }.get(report.overall_status, "未知")

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{title}** | {status_text}\n{report.as_of.strftime('%Y-%m-%d %H:%M')}",
            },
        },
        {"tag": "hr"},
    ]

    for check in report.checks[:8]:
        icon = {"ok": "OK", "stale": "WARN", "missing": "MISS", "error": "ERR", "optional": "OPT"}.get(check.status, "INFO")
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{icon} {check.name}**\n{check.detail}",
                },
            }
        )

    if report.notes:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "\n".join(f"- {n}" for n in report.notes),
            },
        })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": status_color,
        },
        "elements": elements + [
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": f"得分: {report.overall_score} | 仅用于个人研究和策略测试，不构成投资建议。"}
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a shell-style env file (supports both KEY=VALUE and export KEY=VALUE)."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip "export " prefix (shell-style env files)
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip().strip('"').strip("'")
    # Also merge in OS environment (so exported/sourced vars are visible)
    for key in ("FEISHU_WEBHOOK", "FEISHU_SECRET", "FEISHU_APP_ID", "FEISHU_APP_SECRET", "TUSHARE_TOKEN"):
        os_val = os.environ.get(key, "")
        if os_val and key not in result:
            result[key] = os_val
        elif os_val and result.get(key, "") in ("", "replace-me"):
            result[key] = os_val
    return result
