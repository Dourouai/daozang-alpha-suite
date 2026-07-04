from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from beichen_alpha.models import Recommendation


@dataclass(frozen=True)
class PoolDiff:
    added: list[str]
    removed: list[str]
    kept: list[str]


def read_watchlist_entries(path: str | Path) -> dict[str, str]:
    target = Path(path)
    if not target.exists() or target.is_dir():
        return {}

    entries: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        code, _, comment = stripped.partition("#")
        clean_code = code.strip()
        if clean_code:
            entries[clean_code] = comment.strip()
    return entries


def build_pool_diff(old_symbols: list[str], new_symbols: list[str]) -> PoolDiff:
    old_set = set(old_symbols)
    new_set = set(new_symbols)
    return PoolDiff(
        added=[symbol for symbol in new_symbols if symbol not in old_set],
        removed=[symbol for symbol in old_symbols if symbol not in new_set],
        kept=[symbol for symbol in new_symbols if symbol in old_set],
    )


def format_watchlist(
    recommendations: list[Recommendation],
    created_at: datetime,
    pool_size: int,
    scan_limit: int,
    min_market_cap_billion: float,
    exclude_themes: str,
) -> str:
    lines = [
        "# Beichen Alpha dynamic broad target pool",
        f"# Created: {created_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "# Source: daily-refresh-pool",
        f"# Size: top {pool_size} from scan limit {scan_limit}",
        f"# Rules: market cap >= {min_market_cap_billion:.0f}B CNY, exclude {exclude_themes}, horizon ultra_short_2_3d",
        "# Columns: code # name | candidate | status | industry | sector | risk",
    ]
    for item in recommendations:
        lines.append(
            f"{item.code} # {item.name} | candidate {item.candidate_score or item.score} | "
            f"{item.status} | {item.industry or '-'} | {item.sector_rotation or '-'} | {item.risk_calendar or '-'}"
        )
    return "\n".join(lines) + "\n"


def write_watchlist(content: str, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def render_pool_refresh_report(
    recommendations: list[Recommendation],
    diff: PoolDiff,
    previous_entries: dict[str, str],
    dated_path: Path,
    latest_path: Path,
) -> str:
    rec_map = {item.code: item for item in recommendations}
    lines = [
        "基础池刷新完成",
        f"已保存: {dated_path}",
        f"最新池: {latest_path}",
        f"保留 {len(diff.kept)} 只 | 新增 {len(diff.added)} 只 | 移除 {len(diff.removed)} 只",
        "",
        "新增:",
        format_symbol_list(diff.added, rec_map, previous_entries),
        "",
        "移除:",
        format_symbol_list(diff.removed, rec_map, previous_entries),
        "",
        "当前前10:",
    ]
    for index, item in enumerate(recommendations[:10], 1):
        lines.append(
            f"{index}. {item.name} {item.code} | 候选 {item.candidate_score or item.score} | "
            f"{item.status} | {item.industry or '-'} | {item.sector_rotation or '-'}"
        )
    return "\n".join(lines)


def render_pool_refresh_card(
    recommendations: list[Recommendation],
    diff: PoolDiff,
    previous_entries: dict[str, str],
    title: str,
    as_of: datetime,
) -> dict:
    rec_map = {item.code: item for item in recommendations}
    elements = [
        div(
            "\n".join(
                [
                    f"**{as_of.strftime('%Y-%m-%d %H:%M')} | 基础池动态刷新**",
                    f"保留 **{len(diff.kept)}** 只 | 新增 **{len(diff.added)}** 只 | 移除 **{len(diff.removed)}** 只",
                    "用途：基础池回答值不值得看；盘中仍需再看执行分，不直接代表买入。",
                ]
            )
        ),
        {"tag": "hr"},
        div("**新增**\n" + format_symbol_list(diff.added[:12], rec_map, previous_entries)),
        {"tag": "hr"},
        div("**移除**\n" + format_symbol_list(diff.removed[:12], rec_map, previous_entries)),
        {"tag": "hr"},
        div("**当前前10**\n" + "\n".join(format_recommendation_line(i, item) for i, item in enumerate(recommendations[:10], 1))),
        div("仅用于个人研究和策略测试，不构成投资建议。"),
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def find_previous_pool(out_dir: str | Path, dated_path: str | Path, latest_path: str | Path) -> Path | None:
    latest = Path(latest_path)
    if latest.exists():
        return latest
    dated = Path(dated_path)
    if dated.exists():
        return dated
    candidates = sorted(Path(out_dir).glob("broad_target_pool_*.txt"), reverse=True)
    return candidates[0] if candidates else None


def format_symbol_list(symbols: list[str], rec_map: dict[str, Recommendation], previous_entries: dict[str, str]) -> str:
    if not symbols:
        return "-"
    return "\n".join(format_symbol(symbol, rec_map, previous_entries) for symbol in symbols)


def format_symbol(symbol: str, rec_map: dict[str, Recommendation], previous_entries: dict[str, str]) -> str:
    item = rec_map.get(symbol)
    if item is not None:
        return f"{item.name} {symbol} | 候选 {item.candidate_score or item.score} | {item.status} | {item.industry or '-'}"
    comment = previous_entries.get(symbol, "")
    name = comment.split("|", 1)[0].strip() if comment else ""
    return f"{name + ' ' if name else ''}{symbol}"


def format_recommendation_line(index: int, item: Recommendation) -> str:
    return (
        f"{index}. {item.name} {item.code} | 候选 {item.candidate_score or item.score} | "
        f"{item.status} | {item.industry or '-'} | {item.sector_rotation or '-'}"
    )


def div(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}
