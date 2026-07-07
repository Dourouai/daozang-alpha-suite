from __future__ import annotations

import csv
import math
import json
import struct
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


DEFAULT_LIMIT = 800
DEFAULT_WATCHLISTS = (
    "data/watchlists/broad_target_pool_latest.txt",
    "data/watchlists/innovation_drug_pool.txt",
)
DEFAULT_INDUSTRY_MAP = "data/universe/akshare_industry_map.csv"
DEFAULT_RISK_CALENDAR = "data/universe/akshare_risk_calendar.csv"
METRIC_COLUMNS = (
    "data_start_date",
    "data_end_date",
    "history_days",
    "amount_5d_avg",
    "amount_20d_avg",
    "volatility_20d",
)
RISK_COLUMNS = ("risk_tags", "risk_source", "risk_detail")
ENRICHED_COLUMNS = (*METRIC_COLUMNS, *RISK_COLUMNS)


@dataclass(frozen=True)
class SyncUniverseOptions:
    beichen_root: str | Path = "../beichen-alpha"
    output_path: str | Path = "data/universe/active_universe.csv"
    limit: int = DEFAULT_LIMIT
    positions_path: str = "data/positions/current_positions.json"
    watchlists: tuple[str, ...] = DEFAULT_WATCHLISTS
    universe_cache: str = "data/cache/universe_latest.jsonl"
    qlib_data_dir: str | Path = "data/qlib/cn_data"
    industry_map: str | Path = DEFAULT_INDUSTRY_MAP
    risk_calendar: str | Path = DEFAULT_RISK_CALENDAR


@dataclass(frozen=True)
class SyncIndustryMapOptions:
    output_path: str | Path = DEFAULT_INDUSTRY_MAP
    target_universe: str | Path | None = "data/universe/active_universe.csv"
    board_limit: int | None = None


@dataclass(frozen=True)
class SyncIndustryMapArtifacts:
    output_path: Path
    rows: int
    boards: int
    target_codes: int
    covered_targets: int


@dataclass(frozen=True)
class SyncRiskCalendarOptions:
    output_path: str | Path = DEFAULT_RISK_CALENDAR
    target_universe: str | Path | None = "data/universe/active_universe.csv"
    as_of: str | date | None = None
    forward_days: int = 60
    report_period: str | None = None


@dataclass(frozen=True)
class SyncRiskCalendarArtifacts:
    output_path: Path
    rows: int
    target_codes: int
    release_events: int
    report_events: int
    covered_targets: int


@dataclass(frozen=True)
class SyncUniverseArtifacts:
    output_path: Path
    rows: int
    limit: int
    source_counts: dict[str, int]


def sync_beichen_universe(options: SyncUniverseOptions) -> SyncUniverseArtifacts:
    beichen_root = Path(options.beichen_root)
    output_path = Path(options.output_path)
    industry_map = read_industry_map(Path(options.industry_map))
    risk_calendar_path = Path(options.risk_calendar)
    risk_calendar = read_risk_calendar(risk_calendar_path) if risk_calendar_path.exists() else None
    rows: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}

    for item in read_positions(beichen_root / options.positions_path):
        add_universe_row(rows, item, "positions", is_priority=True, industry_map=industry_map)
        source_counts["positions"] = source_counts.get("positions", 0) + 1

    for watchlist in options.watchlists:
        path = beichen_root / watchlist
        entries = read_watchlist(path)
        if not entries:
            continue
        source_name = Path(watchlist).stem
        is_priority = "innovation" in source_name or "focus" in source_name
        for item in entries:
            add_universe_row(rows, item, source_name, is_priority=is_priority, industry_map=industry_map)
        source_counts[source_name] = len(entries)

    cache_entries = read_universe_cache(beichen_root / options.universe_cache)
    source_counts["universe_cache"] = len(cache_entries)
    for item in cache_entries:
        if len(rows) >= options.limit:
            break
        add_universe_row(rows, item, "universe_cache", is_priority=False, industry_map=industry_map)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    previous_metrics = read_existing_enrichment(output_path)
    ordered = list(rows.values())[: options.limit]
    enrich_universe_rows(
        ordered,
        Path(options.qlib_data_dir),
        previous_metrics=previous_metrics,
        risk_calendar=risk_calendar,
    )
    write_universe_csv(ordered, output_path)
    return SyncUniverseArtifacts(
        output_path=output_path,
        rows=len(ordered),
        limit=options.limit,
        source_counts=source_counts,
    )


def sync_akshare_industry_map(options: SyncIndustryMapOptions) -> SyncIndustryMapArtifacts:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("AKShare is required: python3 -m pip install akshare pandas") from exc

    target_codes = read_target_codes(options.target_universe)
    board_names, industry_source, constituent_fetcher = resolve_akshare_industry_source(ak)
    if options.board_limit is not None:
        board_names = board_names[: max(options.board_limit, 0)]

    rows: dict[str, dict[str, str]] = {}
    for board_name in board_names:
        try:
            constituents = constituent_fetcher(board_name)
        except Exception:
            constituents = []
        for item in constituents:
            code = normalize_code(item.get("code"))
            if not code:
                continue
            if target_codes and code not in target_codes:
                continue
            themes = join_themes(industry_theme_tags(board_name))
            current = rows.get(code)
            if current is None:
                rows[code] = {
                    "code": code,
                    "name": str(item.get("name") or code).strip(),
                    "industry": board_name,
                    "themes": themes,
                    "industry_source": industry_source,
                }
            else:
                industries = merge_tags(current.get("industry", ""), board_name)
                current["industry"] = industries[0]
                current["themes"] = join_themes(merge_tags(current.get("themes", ""), themes))

    output_path = Path(options.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_industry_map(rows.values(), output_path)
    covered = len(set(rows).intersection(target_codes)) if target_codes else 0
    return SyncIndustryMapArtifacts(
        output_path=output_path,
        rows=len(rows),
        boards=len(board_names),
        target_codes=len(target_codes),
        covered_targets=covered,
    )


def render_sync_industry_map_summary(artifacts: SyncIndustryMapArtifacts) -> str:
    coverage = "-"
    if artifacts.target_codes:
        coverage = f"{artifacts.covered_targets}/{artifacts.target_codes}"
    return "\n".join(
        [
            "道藏 Alpha AKShare industry map synced",
            f"output: {artifacts.output_path}",
            f"boards: {artifacts.boards}",
            f"rows: {artifacts.rows}",
            f"target coverage: {coverage}",
        ]
    )


def sync_akshare_risk_calendar(options: SyncRiskCalendarOptions) -> SyncRiskCalendarArtifacts:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("AKShare is required: python3 -m pip install akshare pandas") from exc

    as_of = parse_date(options.as_of) or date.today()
    forward_days = max(int(options.forward_days), 1)
    target_codes = read_target_codes(options.target_universe)
    release_events = fetch_release_risk_events(ak, as_of, forward_days, target_codes)
    report_events = fetch_report_window_events(
        ak,
        as_of,
        min(forward_days, 30),
        target_codes,
        options.report_period or infer_report_period(as_of),
    )
    rows = merge_risk_events(release_events, report_events)

    output_path = Path(options.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_risk_calendar(rows.values(), output_path)
    covered = len(set(rows).intersection(target_codes)) if target_codes else 0
    return SyncRiskCalendarArtifacts(
        output_path=output_path,
        rows=len(rows),
        target_codes=len(target_codes),
        release_events=len(release_events),
        report_events=len(report_events),
        covered_targets=covered,
    )


def render_sync_risk_calendar_summary(artifacts: SyncRiskCalendarArtifacts) -> str:
    coverage = "-"
    if artifacts.target_codes:
        coverage = f"{artifacts.covered_targets}/{artifacts.target_codes}"
    return "\n".join(
        [
            "道藏 Alpha AKShare risk calendar synced",
            f"output: {artifacts.output_path}",
            f"rows: {artifacts.rows}",
            f"release_events: {artifacts.release_events}",
            f"report_events: {artifacts.report_events}",
            f"target coverage: {coverage}",
        ]
    )


def render_sync_universe_summary(artifacts: SyncUniverseArtifacts) -> str:
    counts = "；".join(f"{name}={count}" for name, count in artifacts.source_counts.items())
    return "\n".join(
        [
            "道藏 Alpha active universe synced",
            f"output: {artifacts.output_path}",
            f"rows: {artifacts.rows}/{artifacts.limit}",
            f"sources: {counts or '-'}",
        ]
    )


def read_target_codes(path: str | Path | None) -> set[str]:
    if not path:
        return set()
    target = Path(path)
    if not target.exists() or target.is_dir():
        return set()
    return {row["code"] for row in read_universe_rows(target) if row.get("code")}


def resolve_akshare_industry_source(ak: Any) -> tuple[list[str], str, Any]:
    try:
        board_names = fetch_akshare_industry_board_names(ak)
        if board_names:
            return board_names, "akshare_em_industry", lambda name: fetch_akshare_industry_constituents(ak, name)
    except Exception:
        pass
    board_names = fetch_akshare_industry_board_names_ths(ak)
    return board_names, "akshare_ths_industry", lambda name: fetch_akshare_industry_constituents_ths(ak, name)


def fetch_akshare_industry_board_names(ak: Any) -> list[str]:
    frame = ak.stock_board_industry_name_em()
    names = []
    for record in frame.to_dict(orient="records"):
        name = str(record.get("板块名称") or record.get("名称") or record.get("name") or "").strip()
        if name:
            names.append(name)
    return list(dict.fromkeys(names))


def fetch_akshare_industry_constituents(ak: Any, board_name: str) -> list[dict[str, str]]:
    frame = ak.stock_board_industry_cons_em(symbol=board_name)
    rows = []
    for record in frame.to_dict(orient="records"):
        code = normalize_code(record.get("代码") or record.get("code"))
        name = str(record.get("名称") or record.get("name") or code).strip()
        if code:
            rows.append({"code": code, "name": name})
    return rows


def fetch_akshare_industry_board_names_ths(ak: Any) -> list[str]:
    frame = ak.stock_board_industry_name_ths()
    names = []
    for record in frame.to_dict(orient="records"):
        name = str(record.get("name") or record.get("板块") or record.get("名称") or "").strip()
        if name:
            names.append(name)
    return list(dict.fromkeys(names))


def fetch_akshare_industry_constituents_ths(ak: Any, board_name: str) -> list[dict[str, str]]:
    import requests

    code_map = {}
    for record in ak.stock_board_industry_name_ths().to_dict(orient="records"):
        name = str(record.get("name") or record.get("板块") or record.get("名称") or "").strip()
        board_code = str(record.get("code") or record.get("代码") or "").strip()
        if name and board_code:
            code_map[name] = board_code
    board_code = code_map.get(board_name)
    if not board_code:
        return []

    headers = {"User-Agent": "Mozilla/5.0"}
    rows: list[dict[str, str]] = []
    detail_url = f"https://q.10jqka.com.cn/thshy/detail/code/{board_code}/"
    response = requests.get(detail_url, headers=headers, timeout=10)
    page_count = 1
    if response.status_code == 200:
        rows.extend(extract_ths_stock_rows(response.text))
        page_count = extract_ths_page_count(response.text)
    for page in range(2, min(page_count, 30) + 1):
        page_url = f"http://q.10jqka.com.cn/thshy/detail/code/{board_code}/page/{page}/ajax/1/"
        page_response = requests.get(page_url, headers=headers, timeout=10)
        if page_response.status_code != 200:
            continue
        rows.extend(extract_ths_stock_rows(page_response.text))

    deduped: dict[str, dict[str, str]] = {}
    for row in rows:
        code = normalize_code(row.get("code"))
        if code:
            deduped[code] = {"code": code, "name": str(row.get("name") or code).strip()}
    return list(deduped.values())


def extract_ths_page_count(html: str) -> int:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, features="lxml")
    page_info = soup.select_one(".page_info")
    if page_info:
        text = page_info.get_text(strip=True)
        if "/" in text:
            _, _, total = text.partition("/")
            try:
                return max(int(total), 1)
            except ValueError:
                pass
    pages = []
    for link in soup.select("[page]"):
        try:
            pages.append(int(str(link.get("page") or "").strip()))
        except ValueError:
            continue
    return max(pages) if pages else 1


def extract_ths_stock_rows(html: str) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, features="lxml")
    table = soup.select_one("table.m-pager-table") or soup.find("table")
    if table is None:
        return []
    rows = []
    for tr in table.select("tbody tr"):
        cells = [cell.get_text(strip=True) for cell in tr.find_all("td")]
        if len(cells) < 3:
            continue
        code = normalize_code(cells[1])
        name = cells[2]
        if code:
            rows.append({"code": code, "name": name})
    return rows


def fetch_release_risk_events(
    ak: Any,
    as_of: date,
    forward_days: int,
    target_codes: set[str],
) -> list[dict[str, Any]]:
    end_date = as_of + timedelta(days=forward_days)
    frame = ak.stock_restricted_release_detail_em(
        start_date=as_of.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
    )
    events = []
    for record in frame.to_dict(orient="records"):
        event = release_record_to_risk_event(record, as_of, forward_days)
        if event is None:
            continue
        if target_codes and event["code"] not in target_codes:
            continue
        events.append(event)
    return events


def release_record_to_risk_event(
    record: dict[str, Any],
    as_of: date,
    forward_days: int,
) -> dict[str, Any] | None:
    code = normalize_code(record.get("股票代码"))
    event_date = parse_date(record.get("解禁时间"))
    if not code or event_date is None:
        return None
    days = (event_date - as_of).days
    if days < 0 or days > forward_days:
        return None
    pct_float = normalize_ratio(record.get("占解禁前流通市值比例"))
    amount = parse_float(record.get("实际解禁市值"))
    severity, hard_exclude = score_release_window(days, pct_float)
    if severity <= 0:
        return None
    tags = ["解禁硬风险" if hard_exclude else "解禁窗口"]
    if pct_float is not None and pct_float >= 2:
        tags.append("大额解禁")
    amount_text = "-" if amount is None else f"{amount / 100_000_000:.1f}亿"
    detail = (
        f"{event_date.isoformat()} 解禁，{max(days, 0)}天后，"
        f"流通市值占比 {format_pct(pct_float)}，市值 {amount_text}"
    )
    return {
        "code": code,
        "name": str(record.get("股票简称") or code).strip(),
        "risk_tags": tuple(tags),
        "risk_source": "东方财富限售解禁",
        "risk_detail": detail,
        "event_date": event_date.isoformat(),
        "severity": severity,
        "hard_exclude": hard_exclude,
    }


def fetch_report_window_events(
    ak: Any,
    as_of: date,
    forward_days: int,
    target_codes: set[str],
    period: str,
) -> list[dict[str, Any]]:
    frame = ak.stock_report_disclosure(market="沪深京", period=period)
    events = []
    for record in frame.to_dict(orient="records"):
        event = report_record_to_risk_event(record, as_of, forward_days, period)
        if event is None:
            continue
        if target_codes and event["code"] not in target_codes:
            continue
        events.append(event)
    return events


def report_record_to_risk_event(
    record: dict[str, Any],
    as_of: date,
    forward_days: int,
    period: str,
) -> dict[str, Any] | None:
    code = normalize_code(record.get("股票代码"))
    if not code:
        return None
    actual_date = parse_date(record.get("实际披露"))
    if actual_date is not None and actual_date <= as_of:
        return None
    disclosure_date = latest_nonempty_date(
        record.get("三次变更"),
        record.get("二次变更"),
        record.get("初次变更"),
        record.get("首次预约"),
        actual_date,
    )
    if disclosure_date is None:
        return None
    days = (disclosure_date - as_of).days
    if days < 0 or days > forward_days:
        return None
    changed = any(parse_date(record.get(column)) for column in ("初次变更", "二次变更", "三次变更"))
    tags = ["财报窗口" if days <= 14 else "财报临近"]
    if changed:
        tags.append("财报披露变更")
    severity = 0.7 if days <= 7 else 0.45 if days <= 14 else 0.25
    detail = f"{period} 预约披露 {disclosure_date.isoformat()}，{days}天后"
    if changed:
        detail += "，披露日期有变更记录"
    return {
        "code": code,
        "name": str(record.get("股票简称") or code).strip(),
        "risk_tags": tuple(tags),
        "risk_source": "巨潮财报预约披露",
        "risk_detail": detail,
        "event_date": disclosure_date.isoformat(),
        "severity": severity,
        "hard_exclude": False,
    }


def merge_risk_events(*event_groups: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, Any]] = {}
    for events in event_groups:
        for event in events:
            code = normalize_code(event.get("code"))
            if not code:
                continue
            current = merged.setdefault(
                code,
                {
                    "code": code,
                    "name": str(event.get("name") or code).strip(),
                    "risk_tags": (),
                    "risk_source": (),
                    "risk_detail": (),
                    "event_date": "",
                    "severity": 0.0,
                    "hard_exclude": False,
                },
            )
            current["risk_tags"] = merge_tags(current.get("risk_tags", ()), event.get("risk_tags") or ())
            current["risk_source"] = merge_tags(current.get("risk_source", ()), event.get("risk_source"))
            current["risk_detail"] = merge_tags(current.get("risk_detail", ()), event.get("risk_detail"))
            event_severity = float(event.get("severity") or 0)
            if event_severity >= float(current.get("severity") or 0):
                current["event_date"] = str(event.get("event_date") or "")
                current["severity"] = event_severity
            current["hard_exclude"] = bool(current.get("hard_exclude")) or bool(event.get("hard_exclude"))

    return {
        code: {
            "code": code,
            "name": str(row.get("name") or code),
            "risk_tags": join_themes(row.get("risk_tags") or ()),
            "risk_source": join_themes(row.get("risk_source") or ()),
            "risk_detail": join_themes(row.get("risk_detail") or ()),
            "event_date": str(row.get("event_date") or ""),
            "severity": normalize_number_text(row.get("severity")),
            "hard_exclude": "1" if row.get("hard_exclude") else "0",
        }
        for code, row in merged.items()
    }


def write_risk_calendar(rows: Iterable[dict[str, str]], output_path: Path) -> None:
    fieldnames = (
        "code",
        "name",
        "risk_tags",
        "risk_source",
        "risk_detail",
        "event_date",
        "severity",
        "hard_exclude",
    )
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["code"]))


def read_risk_calendar(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists() or path.is_dir():
        return {}
    result = {}
    with path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            code = normalize_code(row.get("code"))
            if not code:
                continue
            result[code] = {
                "risk_tags": str(row.get("risk_tags") or "").strip(),
                "risk_source": str(row.get("risk_source") or "").strip(),
                "risk_detail": str(row.get("risk_detail") or "").strip(),
                "event_date": str(row.get("event_date") or "").strip(),
                "severity": str(row.get("severity") or "").strip(),
                "hard_exclude": str(row.get("hard_exclude") or "").strip(),
            }
    return result


def score_release_window(days: int, pct_float: float | None) -> tuple[float, bool]:
    pressure = pct_float or 0.0
    if pressure <= 0:
        return 0.0, False
    if days <= 7 and pressure >= 1.0:
        return 1.0, True
    if days <= 30 and pressure >= 5.0:
        return 0.95, True
    if days <= 30 and pressure >= 2.0:
        return 0.75, False
    if days <= 14 and pressure >= 0.5:
        return 0.45, False
    return 0.0, False


def infer_report_period(as_of: date) -> str:
    if 1 <= as_of.month <= 4:
        return f"{as_of.year - 1}年报"
    if 5 <= as_of.month <= 6:
        return f"{as_of.year}一季"
    if 7 <= as_of.month <= 9:
        return f"{as_of.year}半年报"
    return f"{as_of.year}三季"


def latest_nonempty_date(*values: Any) -> date | None:
    for value in values:
        parsed = parse_date(value)
        if parsed is not None:
            return parsed
    return None


def write_industry_map(rows: Iterable[dict[str, str]], output_path: Path) -> None:
    fieldnames = ("code", "name", "industry", "themes", "industry_source")
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["code"]))


def read_industry_map(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists() or path.is_dir():
        return {}
    result = {}
    with path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            code = normalize_code(row.get("code"))
            if not code:
                continue
            result[code] = {
                "industry": str(row.get("industry") or "").strip(),
                "themes": str(row.get("themes") or "").strip(),
                "industry_source": str(row.get("industry_source") or "industry_map").strip(),
            }
    return result


def read_positions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    result = []
    for item in payload.get("positions", []):
        code = normalize_code(item.get("code"))
        if code:
            result.append(
                {
                    "code": code,
                    "name": str(item.get("name") or code).strip(),
                    "industry": "",
                    "themes": (),
                    "latest": "",
                    "turnover": "",
                    "market_cap_billion": "",
                }
            )
    return result


def read_watchlist(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.is_dir():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        code_part, _, comment = text.partition("#")
        code = normalize_code(code_part.strip().split()[0] if code_part.strip() else "")
        if not code:
            continue
        name = comment.strip().split("|", 1)[0].strip()
        result.append(
            {
                "code": code,
                "name": name or code,
                "industry": "",
                "themes": (),
                "latest": "",
                "turnover": "",
                "market_cap_billion": "",
            }
        )
    return result


def read_universe_cache(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.is_dir():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = normalize_code(record.get("code"))
        name = str(record.get("name") or code).strip()
        if not code or is_bad_name(name):
            continue
        result.append(
            {
                "code": code,
                "name": name,
                "industry": str(record.get("industry") or record.get("primary_industry") or "").strip(),
                "themes": tuple(str(item) for item in record.get("themes") or ()),
                "latest": record.get("latest") or "",
                "turnover": record.get("turnover") or "",
                "market_cap_billion": record.get("market_cap_billion") or "",
            }
        )
    return result


def add_universe_row(
    rows: dict[str, dict[str, Any]],
    item: dict[str, Any],
    source_pool: str,
    *,
    is_priority: bool,
    industry_map: dict[str, dict[str, str]] | None = None,
) -> None:
    code = normalize_code(item.get("code"))
    if not code:
        return
    instrument = to_qlib_instrument(code)
    name = str(item.get("name") or code).strip()
    mapped = (industry_map or {}).get(code, {})
    inferred_industry, inferred_themes = infer_profile_labels(name)
    industry = str(mapped.get("industry") or item.get("industry") or inferred_industry).strip()
    themes = merge_tags(mapped.get("themes", ""), item.get("themes") or (), inferred_themes)
    industry_source = (
        mapped.get("industry_source")
        or ("cache" if item.get("industry") else "")
        or ("name_rule" if inferred_industry else "")
    )
    current = rows.get(code)
    if current is None:
        rows[code] = {
            "code": code,
            "instrument": instrument,
            "name": name,
            "source_pool": source_pool,
            "industry": industry,
            "themes": join_themes(themes),
            "industry_source": industry_source,
            "latest": normalize_number_text(item.get("latest")),
            "turnover": normalize_number_text(item.get("turnover")),
            "market_cap_billion": normalize_number_text(item.get("market_cap_billion")),
            "data_start_date": "",
            "data_end_date": "",
            "history_days": "",
            "amount_5d_avg": "",
            "amount_20d_avg": "",
            "volatility_20d": "",
            "risk_tags": "",
            "risk_source": "",
            "risk_detail": "",
            "is_priority": "1" if is_priority else "0",
            "is_excluded": "0",
        }
        return

    sources = set(str(current.get("source_pool") or "").split(";"))
    sources.add(source_pool)
    current["source_pool"] = ";".join(sorted(item for item in sources if item))
    if is_priority:
        current["is_priority"] = "1"
    if not current.get("name") or current["name"] == code:
        current["name"] = name
    if not current.get("industry"):
        current["industry"] = industry
    if not current.get("themes"):
        current["themes"] = join_themes(themes)
    if mapped.get("industry"):
        current["industry"] = industry
        current["industry_source"] = mapped.get("industry_source") or "industry_map"
        current["themes"] = join_themes(merge_tags(current.get("themes", ""), themes))
    elif not current.get("industry_source") and industry_source:
        current["industry_source"] = industry_source
    for key in ("latest", "turnover", "market_cap_billion"):
        if not current.get(key):
            current[key] = normalize_number_text(item.get(key))


def write_universe_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    fieldnames = (
        "code",
        "instrument",
        "name",
        "source_pool",
        "industry",
        "industry_source",
        "themes",
        "latest",
        "turnover",
        "market_cap_billion",
        "data_start_date",
        "data_end_date",
        "history_days",
        "amount_5d_avg",
        "amount_20d_avg",
        "volatility_20d",
        "risk_tags",
        "risk_source",
        "risk_detail",
        "is_priority",
        "is_excluded",
    )
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_existing_enrichment(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists() or path.is_dir():
        return {}
    result = {}
    try:
        existing_rows = read_universe_rows(path)
    except (OSError, csv.Error):
        return {}
    for row in existing_rows:
        code = normalize_code(row.get("code"))
        if not code:
            continue
        result[code] = {column: str(row.get(column) or "") for column in ENRICHED_COLUMNS}
    return result


def enrich_universe_rows(
    rows: list[dict[str, Any]],
    qlib_dir: Path,
    *,
    previous_metrics: dict[str, dict[str, str]] | None = None,
    risk_calendar: dict[str, dict[str, str]] | None = None,
) -> None:
    calendar = read_qlib_calendar(qlib_dir)
    has_fresh_risk_calendar = risk_calendar is not None
    for row in rows:
        stats = read_qlib_daily_stats(qlib_dir, str(row.get("instrument") or ""), calendar)
        if not any(stats.values()) and previous_metrics:
            stats = {
                column: (previous_metrics.get(str(row.get("code") or ""), {}) or {}).get(column, "")
                for column in METRIC_COLUMNS
            }
        row.update(stats)
        calendar_risk = (risk_calendar or {}).get(str(row.get("code") or ""), {})
        risk_tags = join_themes(merge_tags(infer_risk_tags(row), calendar_risk.get("risk_tags", "")))
        if not risk_tags and previous_metrics and not has_fresh_risk_calendar:
            risk_tags = (previous_metrics.get(str(row.get("code") or ""), {}) or {}).get("risk_tags", "")
        row["risk_tags"] = risk_tags
        row["risk_source"] = calendar_risk.get("risk_source", "")
        row["risk_detail"] = calendar_risk.get("risk_detail", "")
        if not row["risk_source"] and previous_metrics and not has_fresh_risk_calendar:
            row["risk_source"] = (previous_metrics.get(str(row.get("code") or ""), {}) or {}).get("risk_source", "")
        if not row["risk_detail"] and previous_metrics and not has_fresh_risk_calendar:
            row["risk_detail"] = (previous_metrics.get(str(row.get("code") or ""), {}) or {}).get("risk_detail", "")


def read_qlib_calendar(qlib_dir: Path) -> list[str]:
    path = qlib_dir / "calendars" / "day.txt"
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_qlib_daily_stats(qlib_dir: Path, instrument: str, calendar: list[str]) -> dict[str, str]:
    empty = {
        "data_start_date": "",
        "data_end_date": "",
        "history_days": "",
        "amount_5d_avg": "",
        "amount_20d_avg": "",
        "volatility_20d": "",
    }
    close = read_qlib_bin_series(qlib_dir, instrument, "close")
    amount = read_qlib_bin_series(qlib_dir, instrument, "amount")
    if close is None:
        return empty

    start_index, close_values = close
    valid_close = [(index, value) for index, value in enumerate(close_values) if is_finite(value)]
    if not valid_close:
        return empty

    first_offset = valid_close[0][0]
    last_offset = valid_close[-1][0]
    data_start = calendar_date(calendar, start_index + first_offset)
    data_end = calendar_date(calendar, start_index + last_offset)
    stats = {
        "data_start_date": data_start,
        "data_end_date": data_end,
        "history_days": str(len(valid_close)),
        "amount_5d_avg": "",
        "amount_20d_avg": "",
        "volatility_20d": normalize_number_text(calc_return_volatility(close_values, 20)),
    }
    if amount is not None:
        _, amount_values = amount
        stats["amount_5d_avg"] = normalize_number_text(avg_last_valid(amount_values, 5))
        stats["amount_20d_avg"] = normalize_number_text(avg_last_valid(amount_values, 20))
    return stats


def read_qlib_bin_series(qlib_dir: Path, instrument: str, field: str) -> tuple[int, list[float]] | None:
    normalized = normalize_instrument(instrument).lower()
    if not normalized:
        return None
    path = qlib_dir / "features" / normalized / f"{field}.day.bin"
    if not path.exists():
        return None
    raw = path.read_bytes()
    if len(raw) < 8 or len(raw) % 4 != 0:
        return None
    values = struct.unpack("<" + "f" * (len(raw) // 4), raw)
    start_index = int(values[0])
    return start_index, list(values[1:])


def calendar_date(calendar: list[str], index: int) -> str:
    if 0 <= index < len(calendar):
        return calendar[index]
    return ""


def avg_last_valid(values: list[float], window: int) -> float | None:
    valid = [value for value in values if is_finite(value)]
    if not valid:
        return None
    selected = valid[-window:]
    return sum(selected) / len(selected)


def calc_return_volatility(values: list[float], window: int) -> float | None:
    returns = []
    previous = None
    for value in values:
        if not is_finite(value) or value == 0:
            continue
        if previous is not None and previous != 0:
            returns.append(value / previous - 1)
        previous = value
    selected = returns[-window:]
    if len(selected) < 2:
        return None
    mean = sum(selected) / len(selected)
    variance = sum((value - mean) ** 2 for value in selected) / (len(selected) - 1)
    return math.sqrt(variance)


def infer_risk_tags(row: dict[str, Any]) -> tuple[str, ...]:
    tags = []
    name = str(row.get("name") or "")
    upper = name.upper()
    if "ST" in upper or "退" in name:
        tags.append("ST/退市风险")
    if not row.get("industry") or not row.get("themes"):
        tags.append("画像缺失")
    history_days = parse_float(row.get("history_days"))
    if history_days is not None and history_days <= 250:
        tags.append("次新")
    volatility = parse_float(row.get("volatility_20d"))
    if volatility is not None and volatility >= 0.06:
        tags.append("高波动")
    turnover = parse_float(row.get("turnover"))
    if turnover is not None and turnover > 0 and turnover < 100_000_000:
        tags.append("低流动性")
    return tuple(dict.fromkeys(tags))


def read_universe_instruments(path: str | Path, limit: int | None = None) -> list[str]:
    rows = read_universe_rows(path, limit=limit)
    return [row["instrument"] for row in rows if row.get("instrument")]


def read_universe_rows(path: str | Path, limit: int | None = None) -> list[dict[str, str]]:
    result = []
    with Path(path).open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            if str(row.get("is_excluded") or "0").strip() in {"1", "true", "TRUE", "yes"}:
                continue
            instrument = normalize_instrument(row.get("instrument") or row.get("code") or "")
            if not instrument:
                continue
            row["instrument"] = instrument
            row["code"] = normalize_code(row.get("code") or instrument)
            result.append(row)
            if limit is not None and len(result) >= limit:
                break
    return result


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        text = text.split(".", 1)[0]
    if len(text) == 8 and text[:2] in {"SH", "SZ", "BJ"} and text[2:].isdigit():
        return text[2:]
    digits = "".join(char for char in text if char.isdigit())
    return digits[-6:] if len(digits) >= 6 else ""


def normalize_instrument(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        code, exchange = text.split(".", 1)
        if exchange in {"SH", "SSE"}:
            return f"SH{normalize_code(code)}"
        if exchange in {"SZ", "SZSE"}:
            return f"SZ{normalize_code(code)}"
        if exchange in {"BJ", "BSE"}:
            return f"BJ{normalize_code(code)}"
    if len(text) == 8 and text[:2] in {"SH", "SZ", "BJ"} and text[2:].isdigit():
        return text
    code = normalize_code(text)
    return to_qlib_instrument(code) if code else ""


def to_qlib_instrument(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"SH{code}"
    if code.startswith(("0", "2", "3")):
        return f"SZ{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    return code


def join_themes(values: Iterable[Any]) -> str:
    return ";".join(str(item).strip() for item in values if str(item).strip())


def merge_tags(*groups: Any) -> tuple[str, ...]:
    result: list[str] = []
    for group in groups:
        if group is None:
            continue
        if isinstance(group, str):
            candidates: Iterable[Any] = group.replace(",", ";").replace("，", ";").split(";")
        else:
            try:
                candidates = iter(group)
            except TypeError:
                candidates = (group,)
        for item in candidates:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
    return tuple(result)


def industry_theme_tags(industry: str) -> tuple[str, ...]:
    text = str(industry or "").strip()
    rules: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
        (("化学制药", "生物制品", "医疗服务", "医疗器械", "中药", "医药"), ("医药", "创新药")),
        (("银行",), ("金融稳定", "高股息", "防御")),
        (("证券", "保险", "多元金融"), ("非银金融", "金融稳定", "顺周期")),
        (("电力", "水务", "燃气", "公用事业", "环保"), ("公用事业", "高股息", "现金流", "政策驱动")),
        (("石油", "煤炭", "燃气", "能源"), ("能源安全", "资源", "高股息")),
        (("贵金属", "有色", "小金属", "钢铁"), ("资源", "工业金属", "顺周期")),
        (("半导体", "电子元件", "消费电子", "光学光电子", "元件"), ("半导体", "AI硬件", "先进制造", "数字经济")),
        (("通信设备", "通信服务", "软件", "互联网", "计算机", "游戏"), ("数字经济", "AI硬件")),
        (("电池", "光伏", "风电", "电源设备", "电网设备", "汽车", "能源金属"), ("新能源", "先进制造", "全球竞争")),
        (("专用设备", "通用设备", "工程机械", "自动化", "仪器仪表"), ("先进制造", "高端制造", "机器人")),
        (("航天", "航空", "船舶", "军工"), ("军工", "高端制造", "先进制造")),
        (("铁路", "公路", "航空机场", "航运港口", "物流", "运输"), ("交通运输", "现金流", "顺周期")),
        (("建筑", "工程建设", "装修建材", "水泥", "玻璃", "房地产"), ("顺周期", "基建")),
        (("食品饮料", "酿酒", "家电", "旅游", "商业百货", "美容护理", "纺织服装"), ("消费", "品牌消费")),
        (("农牧", "饲渔", "农药兽药", "化肥"), ("农业", "消费")),
        (("化学", "塑料", "橡胶", "化纤", "非金属材料"), ("新材料", "先进制造")),
        (("传媒", "文化", "教育"), ("消费", "数字经济")),
    )
    for keywords, tags in rules:
        if any(keyword in text for keyword in keywords):
            return tags
    return (text,) if text else ()


def normalize_number_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
        if not is_finite(number):
            return ""
        return f"{number:.6g}"
    except (TypeError, ValueError):
        return str(value).strip()


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    raw_text = str(value).strip()
    if not raw_text or raw_text in {"-", "NaT", "NaN", "nan", "None"}:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date") and callable(value.date):
        try:
            parsed = value.date()
            if isinstance(parsed, date):
                return parsed
        except (TypeError, ValueError):
            pass
    text = raw_text
    if not text or text in {"-", "NaT", "nan", "None"}:
        return None
    text = text.split(" ", 1)[0].replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if is_finite(number) else None


def normalize_ratio(value: Any) -> float | None:
    number = parse_float(value)
    if number is None:
        return None
    if 0 < number <= 1:
        return number * 100
    return number


def format_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}%"


def is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def infer_profile_labels(name: str) -> tuple[str, tuple[str, ...]]:
    text = normalize_name_for_label(name)
    rules: tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...] = (
        (
            ("茅台", "五粮液", "老窖", "汾酒", "洋河", "啤酒", "伊利", "海天", "中免"),
            "消费",
            ("消费", "品牌消费"),
        ),
        (("银行", "民生银"), "银行", ("金融稳定", "高股息", "防御")),
        (
            ("证券", "中金公司", "华泰", "国泰海通", "东方财富", "中信建投", "中国银河", "同花顺"),
            "非银金融",
            ("非银金融", "金融稳定", "顺周期"),
        ),
        (("保险", "中国平安", "中国人寿", "中国太保", "中国人保", "新华保险"), "保险", ("非银金融", "金融稳定", "防御")),
        (("医药", "生物", "药明", "恒瑞", "迈瑞", "百济", "君实", "迪哲", "博瑞", "亚虹", "泰格", "复星医药", "百利天恒"), "医药", ("创新药", "医药")),
        (("电力", "水电", "核电", "三峡能源", "国投电力", "华电", "华能", "国电", "广核", "发电", "大唐发电"), "公用事业", ("高股息", "防御", "能源安全", "现金流")),
        (("神华", "煤", "兖矿", "中煤"), "煤炭", ("高股息", "能源安全", "资源", "现金流")),
        (("石油", "石化", "海油"), "石油石化", ("高股息", "能源安全", "资源")),
        (("黄金",), "黄金", ("黄金", "避险", "资源")),
        (("钨", "锂", "稀土", "铜", "铝", "钼", "钴", "矿业", "紫金", "盐湖", "宏桥", "铀业"), "工业金属", ("工业金属", "资源", "新材料")),
        (("巨化", "万华", "化学", "化工", "氟", "中化", "三孚"), "化工", ("先进制造", "新材料")),
        (
            ("半导体", "中芯", "寒武纪", "海光", "北方华创", "中微", "韦尔", "华虹", "兆易", "澜起", "长电科技", "复旦微电", "沪硅", "盛合晶微", "摩尔线程", "沐曦", "拓荆", "长川", "江波龙", "佰维", "大普微", "西安奕材", "德明利", "盛美上海", "华海清科", "豪威"),
            "半导体",
            ("半导体", "先进制造", "高端制造", "数字经济"),
        ),
        (("中际旭创", "新易盛", "天孚通信", "光迅科技", "源杰", "光模块", "CPO", "华工科技"), "光模块", ("AI硬件", "先进制造", "数字经济")),
        (("工业富联", "立讯精密", "东山精密", "生益科技", "沪电股份", "胜宏科技", "深南电路", "鹏鼎控股", "蓝思科技", "三环集团", "恒玄科技", "鼎泰高科", "宏和科技", "协创数据", "领益智造", "中科曙光"), "AI硬件", ("AI硬件", "先进制造", "数字经济")),
        (("京东方", "TCL科技", "海康威视", "大华股份", "中科蓝讯", "联讯仪器", "电科蓝天"), "电子", ("先进制造", "数字经济")),
        (("中国移动", "中国电信", "中国联通", "长飞光纤", "中兴通讯", "亨通光电", "盛科通信", "中国卫通"), "通信", ("数字经济", "AI硬件", "高股息")),
        (("宁德", "比亚迪", "阳光电源", "亿纬", "新能源", "厦钨新能"), "新能源", ("新能源", "先进制造", "全球竞争")),
        (("机器人", "工业母机", "精工", "机床", "五洲新春", "博众精工", "汇川技术", "大族数控", "大族激光", "三一重工", "恒立液压", "杰瑞股份"), "机械设备", ("先进制造", "机器人", "高端制造")),
        (("船舶", "中船", "航发", "航天", "中航", "兵器", "军工"), "军工", ("军工", "高端制造", "先进制造")),
        (("高铁", "铁路", "中远海控", "招商轮船", "物流", "机场", "航空", "中国中车", "中国国航"), "交通运输", ("顺周期", "现金流")),
        (("中国巨石", "旗滨", "水泥", "玻璃", "建材", "国际复材", "中材科技"), "建材", ("顺周期", "新材料")),
        (("潍柴", "长城汽车", "赛力斯", "江淮汽车", "汽车", "三花智控"), "汽车", ("先进制造", "全球竞争")),
        (("中国建筑", "中国交建", "中国铁建", "中国中铁", "建筑"), "建筑", ("顺周期", "基建")),
        (("润泽科技", "数据", "云"), "数据中心", ("数字经济", "AI硬件")),
    )
    rules = rules + (
        (
            (
                "中科飞测", "晶合集成", "屹唐", "普冉", "华润微", "北京君正", "燕东微", "通富微电", "雅克科技",
                "华峰测控", "士兰微", "中瓷电子", "睿创微纳", "紫光国微", "华天科技", "立昂微", "国科微",
                "卓胜微", "有研硅", "华大九天", "南大光电", "国盾量子", "思瑞浦", "翱捷", "兴福电子",
                "甬矽", "思特威", "晶晨", "金海通", "炬光", "石英股份", "汇成股份", "全志科技", "杰普特",
                "上海新阳", "神工股份", "新洁能", "华兴源创", "捷捷微电", "京仪装备", "斯达半导", "华特气体",
                "赛微电子", "聚辰", "晶方科技", "铖昌", "乐鑫", "汇顶", "伟测", "安集科技", "江丰电子",
                "盛美上海", "长进光子", "蓝特光学", "臻宝科技", "杰华特", "格科微", "广钢气体",
            ),
            "半导体",
            ("半导体", "先进制造", "高端制造", "数字经济"),
        ),
        (
            (
                "中天科技", "华勤技术", "生益电子", "锐捷网络", "浪潮信息", "广合科技", "英维克", "深科技",
                "华丰科技", "江海股份", "紫光股份", "南亚新材", "中控技术", "金安国纪", "剑桥科技", "风华高科",
                "兴森科技", "歌尔股份", "富创精密", "精测电子", "红板科技", "仕佳光子", "环旭电子", "景旺电子",
                "信科移动", "传音控股", "方正科技", "宏景科技", "太辰光", "影石创新", "安克创新", "水晶光电",
                "顺络电子", "视涯科技", "星宸科技", "兆驰股份", "洁美科技", "斯迪克", "联特科技", "科翔股份",
                "联芸科技", "福晶科技", "火炬电子", "超颖电子", "和辉光电", "海信视像", "中富电路", "杭电股份",
                "唯特偶", "深圳华强", "世运电路", "商络电子", "智微智能", "沃格光电", "腾景科技", "华正新材",
                "海格通信", "德科立", "飞荣达", "光库科技", "欧菲光", "光智科技", "蓝特光学", "旭光电子",
            ),
            "电子",
            ("AI硬件", "先进制造", "数字经济"),
        ),
        (
            (
                "思源电气", "德业股份", "特变电工", "天赐材料", "上海电气", "金风科技", "隆基绿能", "东方电气",
                "麦格米特", "时代电气", "天华新能", "迈为股份", "中国西电", "晶盛机电", "新宙邦", "远东股份",
                "湖南裕能", "微导纳米", "福斯特", "通威股份", "正泰电器", "卧龙电驱", "横店东磁", "国轩高科",
                "海博思创", "欧陆通", "TCL中环", "中伟新材", "联合动力", "大金重工", "南网储能", "阿特斯",
                "法拉电子", "南网科技", "金盘科技", "特锐德", "新天绿能", "锦浪科技", "晶澳科技", "杉杉股份",
                "聚和材料", "爱旭股份", "中恒电气", "科士达", "嘉元科技", "星源材质", "天合光能", "欣旺达",
                "恩捷股份", "科达利", "天岳先进",
            ),
            "电力设备",
            ("新能源", "先进制造", "全球竞争"),
        ),
        (
            (
                "世紀华通", "世纪华通", "金山办公", "科大讯飞", "分众传媒", "三六零", "昆仑万维", "宝信软件",
                "蓝色光标", "巨人网络", "三七互娱", "软通动力", "深信服", "恒生电子", "拓维信息", "岩山科技",
                "恺英网络", "光线传媒", "网宿科技", "用友网络", "润和软件", "中科创达", "财富趋势", "芒果超媒",
                "神州数码", "南网数字", "奥比中光",
            ),
            "软件传媒",
            ("数字经济", "AI应用"),
        ),
        (
            (
                "申万宏源", "电投产融", "中油资本", "沪农商行", "渝农商行", "指南针", "国投资本", "国联民生",
                "越秀资本", "江苏金租", "第一创业", "国网英大",
            ),
            "非银金融",
            ("非银金融", "金融稳定", "顺周期"),
        ),
        (
            (
                "海思科", "联影医疗", "科伦药业", "片仔癀", "凯莱英", "康龙化成", "三生国健", "诺诚健华",
                "百奥赛图", "石药创新", "信立泰", "华润三九", "奕瑞科技", "泽璟制药", "新产业", "甘李药业",
                "九安医疗", "同仁堂", "东阿阿胶", "达仁堂", "上海莱士", "昭衍新药", "贝达药业", "长春高新",
                "惠泰医疗", "爱美客", "英科医疗", "新和成",
            ),
            "医药",
            ("创新药", "医药"),
        ),
        (
            (
                "宝钢股份", "包钢股份", "东阳光", "昊华科技", "国瓷材料", "鼎龙股份", "东方盛虹", "锡业股份",
                "上纬新材", "中信特钢", "博迁新材", "东材科技", "菲利华", "驰宏锌锗", "合盛硅业", "中信金属",
                "桐昆股份", "华鲁恒升", "盛和资源", "三美股份", "兴发集团", "联瑞新材", "圣泉集团", "海亮股份",
                "神火股份", "中矿资源", "东方钽业", "金发科技", "浙江龙盛", "龙佰集团", "君正集团", "亚钾国际",
                "金诚信", "三祥新材", "华锡有色", "白银有色", "铂科新材", "斯瑞新材", "株冶集团", "佛塑科技",
                "蓝晓科技", "振华股份", "永兴材料", "格林美", "中金岭南", "中稀有色", "联泓新科", "飞凯材料",
                "钒钛股份", "鄂尔多斯", "恒坤新材", "平安电工", "宏达股份", "振石股份", "杭氧股份", "南钢股份",
                "华阳股份", "苏能股份", "首钢股份", "科达制造", "四方达", "三环集团", "南亚新材",
            ),
            "材料资源",
            ("新材料", "资源", "顺周期"),
        ),
        (
            (
                "上汽集团", "徐工机械", "拓普集团", "罗博特科", "绿的谐波", "中国动力", "宇通客车", "中联重科",
                "豪迈科技", "三瑞智能", "帝尔激光", "冰轮环境", "四方股份", "先导智能", "春风动力", "应流股份",
                "埃斯顿", "巨星科技", "新锐股份", "新莱应材", "纽威股份", "双环传动", "新泉股份", "千里科技",
                "杭叉集团", "华曙高科", "盈峰环境", "斯菱智驱", "均胜电子", "震裕科技", "科沃斯", "昊志机电",
                "开山股份", "飞龙股份", "星宇股份", "欧科亿", "一汽解放", "北汽蓝谷", "盛龙股份", "广汽集团",
                "万向钱潮", "均胜电子",
            ),
            "机械汽车",
            ("先进制造", "高端制造", "全球竞争"),
        ),
        (
            (
                "中国能建", "上港集团", "中远海能", "中国东航", "四川路桥", "宁波港", "招商公路", "宁沪高速",
                "海航控股", "山东高速", "招商蛇口", "保利发展", "青岛港", "中国通号", "中国中冶", "国货航",
                "招商港口", "中国外运", "万科", "陆家嘴", "辽港股份", "中远海发", "粤高速", "北部湾港",
                "皖通高速", "中国核建", "汇绿生态",
            ),
            "基建交通",
            ("顺周期", "现金流", "基建"),
        ),
        (
            (
                "金龙鱼", "东鹏饮料", "公牛集团", "小商品城", "养元饮品", "古井贡酒", "长裕集团", "华利集团",
                "雅戈尔", "万辰集团", "太阳纸业", "海信家电", "苏泊尔", "安琪酵母", "四川长虹", "今世缘",
                "正邦科技", "永辉超市", "新希望", "松发股份", "中策橡胶",
            ),
            "消费",
            ("消费", "品牌消费"),
        ),
        (
            ("海螺", "东方雨虹", "张江高科", "万通发展"),
            "地产建材",
            ("顺周期", "基建"),
        ),
    )
    for keywords, industry, themes in rules:
        if any(keyword in text for keyword in keywords):
            return industry, themes
    return "", ()


def normalize_name_for_label(name: str) -> str:
    text = str(name or "").strip().upper()
    for prefix in ("XD", "XR", "DR", "N"):
        if text.startswith(prefix) and len(text) > len(prefix) + 1:
            text = text[len(prefix):]
            break
    return "".join(text.split())


def is_bad_name(name: str) -> bool:
    upper = name.upper()
    return "ST" in upper or "退" in name
