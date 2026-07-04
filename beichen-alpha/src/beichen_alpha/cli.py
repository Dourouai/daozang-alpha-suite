from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .content_sources import ManualTextSource, WechatArticleSource
from .data_sources import (
    AksharePriceSource,
    AkshareMarketRegimeSource,
    AkshareMarketStructureSource,
    AkshareSectorRotationSource,
    AkshareUniverseSource,
    BaostockPriceSource,
    CsvMacroEventSource,
    CsvPriceSource,
    DefaultMarketDataRouter,
    GlobalFeatureSource,
    GlobalLinkageSource,
    MacroRssEventSource,
    PBOCMacroIndicatorSource,
    PolicyPageEventSource,
    StatsMacroEventSource,
    build_sector_signals_from_price_map,
    fetch_universe_rows_and_profiles,
    fetch_tencent_profiles,
    load_profile_csv,
    infer_stock_profile,
    merge_profiles,
    resolve_fred_series,
    resolve_yahoo_tickers,
    save_universe_cache,
    write_global_feature_dataset,
)
from .decision_log import (
    DEFAULT_DECISION_LOG_PATH,
    append_decision_records,
    build_recommendation_decision_records,
    build_trade_plan_decision_records,
)
from .disclosure_sources import CninfoDisclosureSource
from .distill import append_opinion_signal, distill_article
from .models import OpinionSignal
from .models import StrategyPolicy
from .news_sources import AkshareNewsSource, OpinionSignalNewsSource
from .notifiers import render_feishu_recommendations, render_feishu_recommendations_card, send_card, send_text
from .pool_refresh import (
    build_pool_diff,
    find_previous_pool,
    format_watchlist,
    read_watchlist_entries,
    render_pool_refresh_card,
    render_pool_refresh_report,
    write_watchlist,
)
from .reports import render_global_linkage_report, render_table, render_three_day_trade_plan
from .risk_sources import AkshareRiskCalendarSource, disclosure_events_to_risk_calendar, merge_risk_event_maps
from .strategy import build_realtime_checks, build_three_day_trade_plan, load_model_scores, load_positions, rank_recommendations


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "ingest":
        return ingest_main(argv[1:])
    if argv and argv[0] == "sync-universe":
        return sync_universe_main(argv[1:])
    if argv and argv[0] == "daily-refresh-pool":
        return daily_refresh_pool_main(argv[1:])
    if argv and argv[0] == "global-watch":
        return global_watch_main(argv[1:])
    if argv and argv[0] == "sync-global-features":
        return sync_global_features_main(argv[1:])
    if argv and argv[0] == "trade-plan":
        return trade_plan_main(argv[1:])
    if argv and argv[0] == "healthcheck":
        return healthcheck_main(argv[1:])
    if argv and argv[0] == "chat-server":
        return chat_server_main(argv[1:])

    parser = argparse.ArgumentParser(description="Beichen Alpha candidate pool runner")
    parser.add_argument("--source", choices=["akshare", "baostock", "csv"], default="akshare", help="data source")
    parser.add_argument("--symbols", default="", help="comma-separated stock codes, e.g. 600160,300498")
    parser.add_argument("--watchlist", default="", help="text file with one stock code per line")
    parser.add_argument("--universe", choices=["akshare", "profile"], default="akshare", help="symbol universe source")
    parser.add_argument("--universe-limit", type=int, default=60, help="max symbols selected from dynamic universe")
    parser.add_argument("--universe-candidates", type=int, default=0, help="spot candidates checked before filters; 0 means all")
    parser.add_argument("--min-turnover", type=float, default=0.0, help="minimum spot turnover in CNY billion")
    parser.add_argument(
        "--universe-cache",
        default="data/cache/universe_latest.jsonl",
        help="all-stock universe profile cache path",
    )
    parser.add_argument("--refresh-universe-cache", action="store_true", help="ignore and rebuild universe cache")
    parser.add_argument("--benchmark", default="000300", help="benchmark index code")
    parser.add_argument("--profile", default="", help="optional stock profile override CSV path")
    parser.add_argument(
        "--cycle",
        choices=["balanced", "defensive", "recovery", "growth", "inflation"],
        default="balanced",
        help="macro cycle policy",
    )
    parser.add_argument(
        "--horizon",
        choices=["ultra_short_2_3d", "short_3_5d", "position_10_20d"],
        default="ultra_short_2_3d",
        help="holding horizon; default is 2-3 trading days",
    )
    parser.add_argument("--min-market-cap", type=float, default=300.0, help="minimum market cap in CNY billion")
    parser.add_argument("--allow-small-caps", action="store_true", help="disable large-cap-only filter")
    parser.add_argument(
        "--exclude-themes",
        default="消费,品牌消费",
        help="comma-separated themes to hard exclude; default excludes consumer themes",
    )
    parser.add_argument("--include-excluded", action="store_true", help="show excluded candidates")
    parser.add_argument("--limit", type=int, default=0, help="limit displayed rows; 0 means all rows")
    parser.add_argument("--disable-disclosures", action="store_true", help="disable CNINFO disclosure risk factor")
    parser.add_argument("--disclosure-lookback-days", type=int, default=60, help="CNINFO disclosure lookback window")
    parser.add_argument("--disable-risk-calendar", action="store_true", help="disable risk calendar factor")
    parser.add_argument("--risk-forward-days", type=int, default=30, help="future risk calendar window")
    parser.add_argument("--disable-pledge-risk", action="store_true", help="skip pledge risk checks in risk calendar")
    parser.add_argument("--disable-news", action="store_true", help="disable AKShare stock news source")
    parser.add_argument("--news-lookback-days", type=int, default=7, help="AKShare news event lookback window")
    parser.add_argument("--disable-market-regime", action="store_true", help="disable market temperature factor")
    parser.add_argument("--disable-macro-events", action="store_true", help="disable global macro event factor")
    parser.add_argument("--macro-events", default="config/macro_events.csv", help="macro event CSV path")
    parser.add_argument("--macro-lookback-days", type=int, default=7, help="macro event lookback window")
    parser.add_argument("--disable-macro-rss", action="store_true", help="disable macro RSS event source")
    parser.add_argument("--macro-rss-feeds", default="config/macro_rss_feeds.csv", help="macro RSS feed CSV path")
    parser.add_argument("--macro-rss-timeout", type=float, default=8.0, help="timeout for each macro RSS feed")
    parser.add_argument("--disable-policy-pages", action="store_true", help="disable official policy page event source")
    parser.add_argument("--macro-policy-pages", default="config/macro_policy_pages.csv", help="official policy page CSV path")
    parser.add_argument("--macro-policy-timeout", type=float, default=8.0, help="timeout for each policy page")
    parser.add_argument("--disable-pboc-macro", action="store_true", help="disable PBOC liquidity and credit indicator source")
    parser.add_argument("--pboc-macro-lookback-days", type=int, default=45, help="PBOC numeric macro event lookback window")
    parser.add_argument("--pboc-open-market-timeout", type=float, default=8.0, help="timeout for PBOC open-market detail pages")
    parser.add_argument("--disable-stats-macro", action="store_true", help="disable NBS macro surprise source")
    parser.add_argument("--stats-macro-lookback-days", type=int, default=45, help="NBS macro surprise lookback window")
    parser.add_argument("--disable-sector-rotation", action="store_true", help="disable sector rotation factor")
    parser.add_argument("--disable-market-structure", action="store_true", help="disable market structure factor")
    parser.add_argument("--sector-limit", type=int, default=40, help="max industry boards checked for rotation")
    parser.add_argument("--disable-opinions", action="store_true", help="disable personal opinion news source")
    parser.add_argument("--opinion-lookback-days", type=int, default=7, help="personal opinion signal lookback window")
    parser.add_argument(
        "--opinion-signals",
        default="data/opinion_signals.jsonl",
        help="personal opinion signal JSONL path",
    )
    parser.add_argument("--start", default=None, help="start date, YYYYMMDD; default is roughly 240 days ago")
    parser.add_argument("--end", default=None, help="end date, YYYYMMDD; default is today")
    parser.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="stock adjustment mode")
    parser.add_argument("--prices", default="", help="CSV price path, only used with --source csv")
    parser.add_argument("--notify", choices=["none", "feishu"], default="none", help="send recommendation notification")
    parser.add_argument("--notify-title", default="北辰 Alpha 每日候选池", help="notification title")
    parser.add_argument("--notify-style", choices=["card", "text"], default="card", help="Feishu notification style")
    parser.add_argument("--decision-log", default=str(DEFAULT_DECISION_LOG_PATH), help="local JSONL decision log path")
    parser.add_argument("--realtime", action="store_true", help="append realtime quote execution checks")
    parser.add_argument(
        "--realtime-state",
        default="data/runtime/realtime_checks.json",
        help="realtime stability state path; empty string disables standing confirmation state",
    )
    parser.add_argument(
        "--realtime-min-stable-minutes",
        type=float,
        default=5.0,
        help="minimum minutes between two firm confirmation checks",
    )
    parser.add_argument(
        "--realtime-confirm-buffer",
        type=float,
        default=0.002,
        help="normal firm confirmation buffer, e.g. 0.002 means 0.2%% above confirm price",
    )
    parser.add_argument(
        "--realtime-friday-buffer",
        type=float,
        default=0.005,
        help="Friday/T+1 firm confirmation buffer, e.g. 0.005 means 0.5%% above confirm price",
    )
    parser.add_argument("--quiet", action="store_true", help="hide progress messages")
    args = parser.parse_args(argv)

    try:
        log_step(args, "加载股票画像和动态股票池...")
        profile_overrides = load_profiles(args.profile)
        symbols, universe_profiles = resolve_universe(args, profile_overrides)
        log_step(args, f"入围股票 {len(symbols)} 只，开始加载行情...")
        price_map = load_price_map(args, parser, symbols)
        log_step(args, "补充实时市值画像...")
        live_profiles = fetch_tencent_profiles([code for code in price_map if code != args.benchmark])
        profiles = merge_profiles(
            universe_profiles,
            infer_profiles_from_names(live_profiles),
            profile_overrides,
            live_profiles,
        )
        policy = StrategyPolicy(
            cycle=args.cycle,
            large_cap_only=not args.allow_small_caps,
            min_market_cap_billion=args.min_market_cap,
            excluded_themes=tuple(parse_symbols(args.exclude_themes)),
            horizon=args.horizon,
        )
        as_of = parse_as_of(args.end)
        stock_symbols = [code for code in price_map if code != args.benchmark]
        if args.disable_macro_events:
            log_step(args, "已跳过宏观事件源。")
            macro_events = []
        else:
            log_step(args, "加载宏观事件源...")
            csv_macro_events = CsvMacroEventSource(
                path=args.macro_events,
                as_of=as_of,
                lookback_days=args.macro_lookback_days,
            ).load()
            rss_macro_events = [] if args.disable_macro_rss else MacroRssEventSource(
                feeds_path=args.macro_rss_feeds,
                as_of=as_of,
                timeout=args.macro_rss_timeout,
            ).load()
            policy_page_events = [] if args.disable_policy_pages else PolicyPageEventSource(
                pages_path=args.macro_policy_pages,
                as_of=as_of,
                timeout=args.macro_policy_timeout,
            ).load()
            pboc_macro_events = [] if args.disable_pboc_macro else PBOCMacroIndicatorSource(
                as_of=as_of,
                lookback_days=args.pboc_macro_lookback_days,
                open_market_timeout=args.pboc_open_market_timeout,
            ).load()
            stats_macro_events = [] if args.disable_stats_macro else StatsMacroEventSource(
                as_of=as_of,
                lookback_days=args.stats_macro_lookback_days,
            ).load()
            macro_events = csv_macro_events + rss_macro_events + policy_page_events + pboc_macro_events + stats_macro_events
            log_step(
                args,
                (
                    f"宏观事件: CSV {len(csv_macro_events)} 个，RSS {len(rss_macro_events)} 个，"
                    f"政策页 {len(policy_page_events)} 个，央行数值 {len(pboc_macro_events)} 个，"
                    f"统计局 {len(stats_macro_events)} 个"
                ),
            )
        if args.disable_market_regime or args.source == "csv":
            log_step(args, "已跳过市场温度源。")
            market_regime = None
        else:
            log_step(args, "加载市场温度源...")
            market_regime = AkshareMarketRegimeSource(end_date=args.end).load()
            log_step(args, f"市场温度: {market_regime.temperature if market_regime else '不可用'}")
        if args.disable_market_structure or args.source == "csv":
            log_step(args, "已跳过交易结构源。")
            market_structure = None
        else:
            log_step(args, "加载交易结构源...")
            market_structure = AkshareMarketStructureSource(as_of=as_of).load()
            log_step(args, f"交易结构: {market_structure.detail if market_structure else '不可用'}")
        if args.disable_sector_rotation or args.source == "csv":
            log_step(args, "已跳过行业轮动源。")
            sector_signals = {}
        else:
            log_step(args, "加载行业轮动源...")
            sector_signals = AkshareSectorRotationSource(limit=args.sector_limit, end_date=args.end).load()
            if not sector_signals:
                log_step(args, "行业板块源不可用，改用候选池 K 线聚合轮动。")
                sector_signals = build_sector_signals_from_price_map(
                    price_map,
                    profiles,
                    benchmark_code=args.benchmark,
                )
            log_step(args, f"行业轮动信号: {len(sector_signals)} 个")
        if args.disable_disclosures:
            log_step(args, "已跳过巨潮公告源。")
        else:
            log_step(args, "加载巨潮公告风险...")
        disclosure_events = {} if args.disable_disclosures else CninfoDisclosureSource(
            symbols=stock_symbols,
            as_of=as_of,
            lookback_days=args.disclosure_lookback_days,
        ).load()
        if args.disable_risk_calendar or args.source == "csv":
            log_step(args, "已跳过风险日历源。")
            risk_calendar_events = {}
        else:
            log_step(args, "加载风险日历源...")
            calendar_events = AkshareRiskCalendarSource(
                symbols=stock_symbols,
                as_of=as_of,
                forward_days=args.risk_forward_days,
                include_pledge=not args.disable_pledge_risk,
            ).load()
            disclosure_calendar_events = (
                {} if args.disable_disclosures else disclosure_events_to_risk_calendar(disclosure_events)
            )
            risk_calendar_events = merge_risk_event_maps(calendar_events, disclosure_calendar_events)
            risk_count = sum(len(events) for events in risk_calendar_events.values())
            log_step(args, f"风险日历事件: {risk_count} 个")
        if args.disable_news:
            log_step(args, "已跳过普通新闻源。")
        else:
            log_step(args, "加载普通新闻源...")
        news_events = {} if args.disable_news else AkshareNewsSource(
            symbols=stock_symbols,
            as_of=as_of,
            lookback_days=args.news_lookback_days,
        ).load()
        if args.disable_opinions:
            log_step(args, "已跳过个人观点源。")
        else:
            log_step(args, "加载个人观点源...")
        opinion_events = {} if args.disable_opinions else OpinionSignalNewsSource(
            symbols=stock_symbols,
            profiles=profiles,
            path=args.opinion_signals,
            as_of=as_of,
            lookback_days=args.opinion_lookback_days,
        ).load()
        news_events = merge_event_maps(news_events, opinion_events)
        log_step(args, "计算短线候选和T+1处理计划...")
        recommendations = rank_recommendations(
            price_map,
            args.benchmark,
            profiles=profiles,
            policy=policy,
            news_events=news_events,
            disclosure_events=disclosure_events,
            risk_calendar_events=risk_calendar_events,
            macro_events=macro_events,
            market_regime=market_regime,
            market_structure=market_structure,
            sector_signals=sector_signals,
            as_of=as_of,
        )
    except Exception as exc:
        print(f"Error: run failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    if not args.include_excluded:
        recommendations = [item for item in recommendations if item.status != "排除"]
    if args.limit > 0:
        recommendations = recommendations[: args.limit]

    realtime_checks = None
    if args.realtime and recommendations and args.source != "csv":
        log_step(args, "加载实时行情确认...")
        quotes = DefaultMarketDataRouter(item.code for item in recommendations).load()
        realtime_checks = build_realtime_checks(
            recommendations,
            quotes,
            min_confirm_buffer_pct=args.realtime_confirm_buffer,
            friday_buffer_pct=args.realtime_friday_buffer,
            min_stable_minutes=args.realtime_min_stable_minutes,
            state_path=args.realtime_state or None,
            as_of=as_of,
        )

    decision_log_path = append_decision_records(
        build_recommendation_decision_records(
            recommendations,
            as_of=as_of,
            run_kind="candidate_screen",
            context=candidate_screen_context(args, symbols),
            realtime_checks=realtime_checks,
        ),
        args.decision_log,
    )
    log_step(args, f"已写入决策日志: {decision_log_path}")

    print(render_table(recommendations, realtime_checks=realtime_checks))
    if args.notify == "feishu":
        if args.notify_style == "card":
            card = render_feishu_recommendations_card(
                recommendations,
                title=args.notify_title,
                as_of=as_of,
                realtime_checks=realtime_checks,
            )
            send_card(card)
        else:
            text = render_feishu_recommendations(
                recommendations,
                title=args.notify_title,
                as_of=as_of,
                realtime_checks=realtime_checks,
            )
            send_text(text)
        print("Feishu notification sent.")
    return 0


def log_step(args: argparse.Namespace, message: str) -> None:
    if not getattr(args, "quiet", False):
        print(f"[Beichen Alpha] {message}", file=sys.stderr)


def ingest_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Ingest a manually supplied article link or text")
    content_group = parser.add_mutually_exclusive_group(required=True)
    content_group.add_argument("--url", default="", help="article URL")
    content_group.add_argument("--text", default="", help="raw article or video-summary text")
    content_group.add_argument("--text-file", default="", help="plain text file path")
    parser.add_argument("--title", default="", help="manual title, useful with --text or --text-file")
    parser.add_argument("--source-name", default="", help="blogger or account name")
    parser.add_argument("--author", default="", help="content author")
    parser.add_argument("--published-at", default="", help="publish time, e.g. 2026-07-02 or 2026-07-02 14:30:00")
    parser.add_argument("--profile", default="", help="optional stock profile override CSV path")
    parser.add_argument("--out", default="data/opinion_signals.jsonl", help="JSONL output path")
    parser.add_argument("--dry-run", action="store_true", help="print distilled signal without saving")
    args = parser.parse_args(argv)

    try:
        article = load_ingest_article(args)
        signal = distill_article(article, profiles=load_profiles(args.profile))
        saved_path = None if args.dry_run else append_opinion_signal(signal, args.out)
    except Exception as exc:
        print(f"Error: ingest failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    print(render_opinion_signal(signal, saved_path))
    return 0


def sync_universe_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Sync all-stock universe profiles without fetching K-lines")
    parser.add_argument("--profile", default="", help="optional stock profile override CSV path")
    parser.add_argument("--out", default="data/cache/universe_latest.jsonl", help="universe cache JSONL path")
    parser.add_argument("--universe-candidates", type=int, default=0, help="spot candidates checked before filters; 0 means all")
    parser.add_argument("--min-turnover", type=float, default=0.0, help="minimum spot turnover in CNY billion")
    args = parser.parse_args(argv)

    try:
        rows, profiles = fetch_universe_rows_and_profiles(
            candidates=args.universe_candidates,
            min_turnover_billion=args.min_turnover,
            profile_overrides=load_profiles(args.profile),
        )
        saved_path = save_universe_cache(rows, profiles, args.out)
    except Exception as exc:
        print(f"Error: sync-universe failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    profile_count = len(profiles)
    with_market_cap = sum(1 for profile in profiles.values() if profile.market_cap_billion is not None)
    print(f"已同步股票画像: {profile_count} 只")
    print(f"含总市值: {with_market_cap} 只")
    print(f"已保存: {saved_path}")
    return 0


def global_watch_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Observe global market linkage signals")
    parser.add_argument("--fred-series", default="", help="comma-separated FRED series; default uses core rates/dollar/credit")
    parser.add_argument("--yahoo-tickers", default="", help="comma-separated Yahoo tickers; default uses US/HK/risk/commodity set")
    parser.add_argument("--lookback-days", type=int, default=20, help="Yahoo daily history lookback window")
    parser.add_argument("--timeout", type=float, default=10.0, help="FRED HTTP timeout")
    parser.add_argument("--notify", choices=["none", "feishu"], default="none", help="send report notification")
    parser.add_argument("--notify-title", default="北辰 Alpha 全球联动观察", help="notification title")
    args = parser.parse_args(argv)

    try:
        snapshot = GlobalLinkageSource(
            fred_series=resolve_fred_series(args.fred_series),
            yahoo_tickers=resolve_yahoo_tickers(args.yahoo_tickers),
            lookback_days=args.lookback_days,
            timeout=args.timeout,
        ).load()
    except Exception as exc:
        print(f"Error: global-watch failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    report = render_global_linkage_report(snapshot)
    print(report)
    if args.notify == "feishu":
        send_text(f"{args.notify_title}\n\n{report}")
        print("Feishu notification sent.")
    return 0


def sync_global_features_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Sync global market features for model datasets")
    parser.add_argument("--fred-series", default="", help="comma-separated FRED series; default uses core rates/dollar/credit")
    parser.add_argument("--yahoo-tickers", default="", help="comma-separated Yahoo tickers; default uses US/HK/risk/commodity set")
    parser.add_argument("--period", default="5y", help="Yahoo history period when --start is omitted")
    parser.add_argument("--start", default="", help="start date, YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--end", default="", help="end date, YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--timeout", type=float, default=20.0, help="FRED HTTP timeout")
    parser.add_argument("--out", default="data/features/global_linkage_daily.csv", help="output feature CSV path")
    parser.add_argument("--meta-out", default="data/features/global_linkage_meta.json", help="output metadata JSON path")
    args = parser.parse_args(argv)

    try:
        dataset = GlobalFeatureSource(
            fred_series=resolve_fred_series(args.fred_series),
            yahoo_tickers=resolve_yahoo_tickers(args.yahoo_tickers),
            period=args.period,
            start=args.start or None,
            end=args.end or None,
            timeout=args.timeout,
        ).load()
        out_path, meta_path = write_global_feature_dataset(dataset, args.out, args.meta_out)
    except Exception as exc:
        print(f"Error: sync-global-features failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    print(f"global feature rows: {len(dataset.rows)}")
    print(f"columns: {len(dataset.columns)}")
    print(f"saved: {out_path}")
    if meta_path is not None:
        print(f"metadata: {meta_path}")
    failures = [item for item in dataset.source_health if "FAIL" in item]
    if failures:
        print("source warnings: " + "；".join(failures))
    return 0


def healthcheck_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check Beichen Alpha runtime readiness")
    parser.add_argument("--positions", default="data/positions/current_positions.json", help="current positions JSON path")
    parser.add_argument(
        "--min-positions",
        type=int,
        default=int(os.environ.get("BEICHEN_MIN_POSITIONS", "1")),
        help="minimum expected local position count",
    )
    parser.add_argument("--watchlist", default="data/watchlists/broad_target_pool_2026-07-03.txt", help="candidate watchlist path")
    parser.add_argument("--model-scores", default="../daozang-alpha/data/exports/alpha_scores_latest.csv", help="Daozang score CSV")
    parser.add_argument("--decision-log", default=str(DEFAULT_DECISION_LOG_PATH), help="local JSONL decision log path")
    parser.add_argument("--runtime-dir", default="data/runtime", help="runtime state directory")
    parser.add_argument("--log-dir", default="logs", help="script log directory")
    parser.add_argument("--require-feishu", action="store_true", help="fail when FEISHU_WEBHOOK is missing")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)

    checks: list[dict] = []
    add_check(checks, "python", True, f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", "error")
    add_check(checks, "working_directory", Path("pyproject.toml").exists(), str(Path.cwd()), "error")
    positions_path = Path(args.positions)
    add_check(checks, "positions", positions_path.exists(), args.positions, "error")
    if positions_path.exists():
        count, detail = summarize_positions_file(positions_path)
        add_check(checks, "positions_count", count >= args.min_positions, detail, "error")
    add_check(checks, "watchlist", Path(args.watchlist).exists(), args.watchlist, "error")
    add_check(checks, "model_scores", Path(args.model_scores).exists(), args.model_scores, "warning")
    add_writable_dir_check(checks, Path(args.decision_log).parent, "decision_log_dir")
    add_writable_dir_check(checks, Path(args.runtime_dir), "runtime_dir")
    add_writable_dir_check(checks, Path(args.log_dir), "log_dir")

    webhook = os.environ.get("FEISHU_WEBHOOK", "")
    feishu_ok = bool(webhook and "replace-me" not in webhook)
    add_check(
        checks,
        "feishu_webhook",
        feishu_ok or not args.require_feishu,
        "configured" if feishu_ok else "missing",
        "error" if args.require_feishu else "warning",
    )

    payload = {
        "ok": not any(not item["ok"] and item["level"] == "error" for item in checks),
        "checks": checks,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in checks:
            marker = "OK" if item["ok"] else item["level"].upper()
            print(f"[{marker}] {item['name']}: {item['detail']}")
    return 0 if payload["ok"] else 1


def summarize_positions_file(path: Path) -> tuple[int, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        positions = payload.get("positions", [])
    except Exception as exc:
        return 0, f"invalid positions JSON ({type(exc).__name__}: {exc})"
    codes = [str(item.get("code") or "-") for item in positions if isinstance(item, dict)]
    return len(codes), f"{len(codes)} positions: {', '.join(codes) if codes else '-'}"


def chat_server_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the Beichen Alpha Feishu chat adapter server")
    parser.add_argument("--host", default=os.environ.get("FEISHU_CHAT_HOST", "127.0.0.1"), help="bind host")
    parser.add_argument("--port", type=int, default=int(os.environ.get("FEISHU_CHAT_PORT", "8787")), help="bind port")
    args = parser.parse_args(argv)

    from .chat_server import run_chat_server

    run_chat_server(args.host, args.port, project_dir=Path.cwd())
    return 0


def add_check(checks: list[dict], name: str, ok: bool, detail: str, level: str) -> None:
    checks.append({"name": name, "ok": ok, "detail": detail, "level": level})


def add_writable_dir_check(checks: list[dict], path: Path, name: str) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add_check(checks, name, True, str(path), "error")
    except OSError as exc:
        add_check(checks, name, False, f"{path}: {exc}", "error")


def trade_plan_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build a 3-day short-term trading plan")
    parser.add_argument("--positions", default="data/positions/current_positions.json", help="current positions JSON path")
    parser.add_argument("--watchlist", default="data/watchlists/broad_target_pool_2026-07-03.txt", help="candidate watchlist path")
    parser.add_argument("--source", choices=["akshare", "baostock"], default="baostock", help="daily bar source")
    parser.add_argument("--benchmark", default="000300", help="benchmark index code")
    parser.add_argument("--start", default="20260601", help="start date for candidate bars")
    parser.add_argument("--end", default="20260703", help="end date for candidate bars")
    parser.add_argument("--review-date", default="", help="holding review date, YYYYMMDD; default uses --end")
    parser.add_argument("--capital", type=float, default=10000.0, help="account capital for planning")
    parser.add_argument("--top", type=int, default=3, help="number of buy candidates")
    parser.add_argument("--max-trade-pct", type=float, default=0.35, help="single trade budget as capital fraction")
    parser.add_argument("--model-scores", default="../daozang-alpha/data/exports/alpha_scores_latest.csv", help="Daozang latest score CSV")
    parser.add_argument("--notify", choices=["none", "feishu"], default="none", help="send plan to Feishu")
    parser.add_argument("--decision-log", default=str(DEFAULT_DECISION_LOG_PATH), help="local JSONL decision log path")
    args = parser.parse_args(argv)

    try:
        as_of = parse_as_of(args.end)
        review_as_of = parse_as_of(args.review_date) if args.review_date else as_of
        positions = load_positions(args.positions)
        symbols = dedupe([item["code"] for item in positions] + read_watchlist(args.watchlist))
        price_map = (
            BaostockPriceSource(
                symbols=symbols,
                benchmark=args.benchmark,
                start_date=args.start,
                end_date=args.end,
            ).load()
            if args.source == "baostock"
            else AksharePriceSource(
                symbols=symbols,
                benchmark=args.benchmark,
                start_date=args.start,
                end_date=args.end,
            ).load()
        )
        live_profiles = fetch_tencent_profiles([code for code in price_map if code != args.benchmark])
        profiles = merge_profiles(infer_profiles_from_names(live_profiles), live_profiles)
        recommendations = rank_recommendations(
            price_map,
            args.benchmark,
            profiles=profiles,
            policy=StrategyPolicy(
                cycle="balanced",
                large_cap_only=False,
                min_market_cap_billion=0,
                excluded_themes=(),
                horizon="ultra_short_2_3d",
            ),
            macro_events=[],
            market_regime=None,
            sector_signals={},
        )
        plan = build_three_day_trade_plan(
            recommendations,
            positions,
            capital=args.capital,
            top_n=args.top,
            max_trade_pct=args.max_trade_pct,
            model_scores=load_model_scores(args.model_scores),
            review_date=review_as_of,
            trading_dates=[bar.date for bar in price_map.get(args.benchmark, [])],
        )
    except Exception as exc:
        print(f"Error: trade-plan failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    decision_log_path = append_decision_records(
        build_trade_plan_decision_records(
            plan,
            as_of=as_of,
            context=trade_plan_context(args, symbols),
        ),
        args.decision_log,
    )
    report = render_three_day_trade_plan(plan)
    print(report)
    print(f"Decision log saved: {decision_log_path}")
    if args.notify == "feishu":
        send_text(report)
        print("Feishu notification sent.")
    return 0


def daily_refresh_pool_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Refresh dynamic broad target pool and compare with the previous pool")
    parser.add_argument("--pool-size", type=int, default=50, help="final broad pool size")
    parser.add_argument("--scan-limit", type=int, default=120, help="dynamic universe size scored before taking pool-size")
    parser.add_argument("--out-dir", default="data/watchlists", help="watchlist output directory")
    parser.add_argument("--latest-path", default="data/watchlists/broad_target_pool_latest.txt", help="latest broad pool path")
    parser.add_argument("--previous", default="", help="optional previous pool path; default uses latest or newest dated pool")
    parser.add_argument("--profile", default="config/profile_overrides.csv", help="optional stock profile override CSV path")
    parser.add_argument("--cycle", choices=["balanced", "defensive", "recovery", "growth", "inflation"], default="balanced")
    parser.add_argument("--horizon", choices=["ultra_short_2_3d", "short_3_5d", "position_10_20d"], default="ultra_short_2_3d")
    parser.add_argument("--min-market-cap", type=float, default=300.0, help="minimum market cap in CNY billion")
    parser.add_argument("--exclude-themes", default="消费,品牌消费", help="comma-separated hard excluded themes")
    parser.add_argument("--universe-candidates", type=int, default=0, help="spot candidates checked before filters; 0 means all")
    parser.add_argument("--min-turnover", type=float, default=0.0, help="minimum spot turnover in CNY billion")
    parser.add_argument("--universe-cache", default="data/cache/universe_latest.jsonl", help="all-stock universe profile cache path")
    parser.add_argument("--refresh-universe-cache", action="store_true", help="ignore and rebuild universe cache")
    parser.add_argument("--benchmark", default="000300", help="benchmark index code")
    parser.add_argument("--start", default=None, help="start date, YYYYMMDD")
    parser.add_argument("--end", default=None, help="end date, YYYYMMDD")
    parser.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="stock adjustment mode")
    parser.add_argument("--sector-limit", type=int, default=40, help="max industry boards checked for rotation")
    parser.add_argument("--disable-market-structure", action="store_true", help="disable market structure factor")
    parser.add_argument("--risk-forward-days", type=int, default=30, help="future risk calendar window")
    parser.add_argument("--disable-risk-calendar", action="store_true", help="disable risk calendar factor")
    parser.add_argument("--disable-pledge-risk", action="store_true", help="skip pledge risk checks")
    parser.add_argument("--disable-macro-events", action="store_true", help="disable global macro event factor")
    parser.add_argument("--macro-events", default="config/macro_events.csv", help="macro event CSV path")
    parser.add_argument("--macro-lookback-days", type=int, default=7, help="macro event lookback window")
    parser.add_argument("--disable-macro-rss", action="store_true", help="disable macro RSS event source")
    parser.add_argument("--macro-rss-feeds", default="config/macro_rss_feeds.csv", help="macro RSS feed CSV path")
    parser.add_argument("--macro-rss-timeout", type=float, default=8.0, help="timeout for each macro RSS feed")
    parser.add_argument("--disable-policy-pages", action="store_true", help="disable official policy page event source")
    parser.add_argument("--macro-policy-pages", default="config/macro_policy_pages.csv", help="official policy page CSV path")
    parser.add_argument("--macro-policy-timeout", type=float, default=8.0, help="timeout for each policy page")
    parser.add_argument("--disable-pboc-macro", action="store_true", help="disable PBOC liquidity and credit indicator source")
    parser.add_argument("--pboc-macro-lookback-days", type=int, default=45, help="PBOC numeric macro event lookback window")
    parser.add_argument("--pboc-open-market-timeout", type=float, default=8.0, help="timeout for PBOC open-market detail pages")
    parser.add_argument("--disable-stats-macro", action="store_true", help="disable NBS macro surprise source")
    parser.add_argument("--stats-macro-lookback-days", type=int, default=45, help="NBS macro surprise lookback window")
    parser.add_argument("--include-news", action="store_true", help="include ordinary AKShare news; slower")
    parser.add_argument("--include-disclosures", action="store_true", help="include CNINFO disclosures; slower")
    parser.add_argument("--disable-opinions", action="store_true", help="disable personal opinion source")
    parser.add_argument("--opinion-lookback-days", type=int, default=7, help="personal opinion signal lookback window")
    parser.add_argument("--opinion-signals", default="data/opinion_signals.jsonl", help="personal opinion signal JSONL path")
    parser.add_argument("--notify", choices=["none", "feishu"], default="none", help="send refresh report notification")
    parser.add_argument("--notify-title", default="北辰 Alpha 基础池刷新", help="notification title")
    parser.add_argument("--decision-log", default=str(DEFAULT_DECISION_LOG_PATH), help="local JSONL decision log path")
    parser.add_argument("--quiet", action="store_true", help="hide progress messages")
    args = parser.parse_args(argv)

    try:
        as_of = parse_as_of(args.end)
        date_text = as_of.strftime("%Y-%m-%d")
        out_dir = Path(args.out_dir)
        dated_path = out_dir / f"broad_target_pool_{date_text}.txt"
        latest_path = Path(args.latest_path)
        previous_path = Path(args.previous) if args.previous else find_previous_pool(out_dir, dated_path, latest_path)
        previous_entries = read_watchlist_entries(previous_path) if previous_path else {}

        log_step(args, "刷新全A动态候选宇宙...")
        profile_overrides = load_profiles(args.profile)
        universe = AkshareUniverseSource(
            limit=args.scan_limit,
            candidates=args.universe_candidates,
            min_turnover_billion=args.min_turnover,
            min_market_cap_billion=args.min_market_cap,
            exclude_themes=tuple(parse_symbols(args.exclude_themes)),
            cache_path=args.universe_cache,
            refresh_cache=args.refresh_universe_cache,
        ).load(profile_overrides=profile_overrides)
        log_step(args, f"扫描股票 {len(universe.symbols)} 只，加载行情...")
        price_map = AksharePriceSource(
            symbols=universe.symbols,
            benchmark=args.benchmark,
            start_date=args.start,
            end_date=args.end,
            adjust=args.adjust,
        ).load()
        log_step(args, "补充实时市值画像...")
        live_profiles = fetch_tencent_profiles([code for code in price_map if code != args.benchmark])
        profiles = merge_profiles(universe.profiles, profile_overrides, live_profiles)
        policy = StrategyPolicy(
            cycle=args.cycle,
            large_cap_only=True,
            min_market_cap_billion=args.min_market_cap,
            excluded_themes=tuple(parse_symbols(args.exclude_themes)),
            horizon=args.horizon,
        )
        stock_symbols = [code for code in price_map if code != args.benchmark]

        if args.disable_macro_events:
            log_step(args, "已跳过宏观事件源。")
            macro_events = []
        else:
            log_step(args, "加载宏观事件源...")
            csv_macro_events = CsvMacroEventSource(
                path=args.macro_events,
                as_of=as_of,
                lookback_days=args.macro_lookback_days,
            ).load()
            rss_macro_events = [] if args.disable_macro_rss else MacroRssEventSource(
                feeds_path=args.macro_rss_feeds,
                as_of=as_of,
                timeout=args.macro_rss_timeout,
            ).load()
            policy_page_events = [] if args.disable_policy_pages else PolicyPageEventSource(
                pages_path=args.macro_policy_pages,
                as_of=as_of,
                timeout=args.macro_policy_timeout,
            ).load()
            pboc_macro_events = [] if args.disable_pboc_macro else PBOCMacroIndicatorSource(
                as_of=as_of,
                lookback_days=args.pboc_macro_lookback_days,
                open_market_timeout=args.pboc_open_market_timeout,
            ).load()
            stats_macro_events = [] if args.disable_stats_macro else StatsMacroEventSource(
                as_of=as_of,
                lookback_days=args.stats_macro_lookback_days,
            ).load()
            macro_events = csv_macro_events + rss_macro_events + policy_page_events + pboc_macro_events + stats_macro_events
            log_step(
                args,
                (
                    f"宏观事件: CSV {len(csv_macro_events)} 个，RSS {len(rss_macro_events)} 个，"
                    f"政策页 {len(policy_page_events)} 个，央行数值 {len(pboc_macro_events)} 个，"
                    f"统计局 {len(stats_macro_events)} 个"
                ),
            )

        log_step(args, "加载市场温度和行业轮动...")
        market_regime = AkshareMarketRegimeSource(end_date=args.end).load()
        market_structure = None if args.disable_market_structure else AkshareMarketStructureSource(as_of=as_of).load()
        sector_signals = AkshareSectorRotationSource(limit=args.sector_limit, end_date=args.end).load()
        if not sector_signals:
            sector_signals = build_sector_signals_from_price_map(price_map, profiles, benchmark_code=args.benchmark)

        disclosure_events = {}
        if args.include_disclosures:
            log_step(args, "加载巨潮公告风险...")
            disclosure_events = CninfoDisclosureSource(symbols=stock_symbols, as_of=as_of).load()

        risk_calendar_events = {}
        if not args.disable_risk_calendar:
            log_step(args, "加载风险日历...")
            calendar_events = AkshareRiskCalendarSource(
                symbols=stock_symbols,
                as_of=as_of,
                forward_days=args.risk_forward_days,
                include_pledge=not args.disable_pledge_risk,
            ).load()
            disclosure_calendar_events = disclosure_events_to_risk_calendar(disclosure_events) if disclosure_events else {}
            risk_calendar_events = merge_risk_event_maps(calendar_events, disclosure_calendar_events)

        news_events = {}
        if args.include_news:
            log_step(args, "加载普通新闻...")
            news_events = AkshareNewsSource(symbols=stock_symbols, as_of=as_of).load()
        if not args.disable_opinions:
            log_step(args, "加载个人观点源...")
            opinion_events = OpinionSignalNewsSource(
                symbols=stock_symbols,
                profiles=profiles,
                path=args.opinion_signals,
                as_of=as_of,
                lookback_days=args.opinion_lookback_days,
            ).load()
            news_events = merge_event_maps(news_events, opinion_events)

        log_step(args, "计算并刷新基础池...")
        recommendations = rank_recommendations(
            price_map,
            args.benchmark,
            profiles=profiles,
            policy=policy,
            news_events=news_events,
            disclosure_events=disclosure_events,
            risk_calendar_events=risk_calendar_events,
            macro_events=macro_events,
            market_regime=market_regime,
            market_structure=market_structure,
            sector_signals=sector_signals,
            as_of=as_of,
        )
        recommendations = [item for item in recommendations if item.status != "排除"][: args.pool_size]
        content = format_watchlist(
            recommendations,
            created_at=datetime.now(),
            pool_size=args.pool_size,
            scan_limit=args.scan_limit,
            min_market_cap_billion=args.min_market_cap,
            exclude_themes=args.exclude_themes,
        )
        saved_dated = write_watchlist(content, dated_path)
        saved_latest = write_watchlist(content, latest_path)
        diff = build_pool_diff(list(previous_entries.keys()), [item.code for item in recommendations])
    except Exception as exc:
        print(f"Error: daily-refresh-pool failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    report = render_pool_refresh_report(recommendations, diff, previous_entries, saved_dated, saved_latest)
    decision_log_path = append_decision_records(
        build_recommendation_decision_records(
            recommendations,
            as_of=as_of,
            run_kind="daily_pool_refresh",
            context=daily_pool_context(args, universe.symbols, saved_dated, saved_latest),
        ),
        args.decision_log,
    )
    log_step(args, f"已写入决策日志: {decision_log_path}")
    print(report)
    if args.notify == "feishu":
        card = render_pool_refresh_card(recommendations, diff, previous_entries, title=args.notify_title, as_of=as_of)
        send_card(card)
        print("Feishu notification sent.")
    return 0


def load_ingest_article(args: argparse.Namespace):
    if args.url:
        return WechatArticleSource(args.url, source_name=args.source_name).load()

    raw_text = args.text
    if args.text_file:
        raw_text = Path(args.text_file).read_text(encoding="utf-8")

    return ManualTextSource(
        text=raw_text,
        title=args.title,
        source_name=args.source_name,
        author=args.author,
        published_at=parse_optional_datetime(args.published_at),
    ).load()


def render_opinion_signal(signal: OpinionSignal, saved_path: Path | None) -> str:
    lines = [
        f"标题: {signal.title}",
        f"来源: {signal.source_name or '-'} / 作者: {signal.source_author or '-'}",
        f"信号日期: {signal.signal_date.strftime('%Y-%m-%d %H:%M:%S')}",
        f"发布时间: {signal.published_at.strftime('%Y-%m-%d %H:%M:%S') if signal.published_at else '-'}",
        f"投喂日期: {signal.ingested_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"规则版本: {signal.rule_version}",
        f"观点: {signal.stance}",
        f"置信度: {signal.confidence:.2f}",
        f"主题: {', '.join(signal.themes) if signal.themes else '-'}",
        f"映射股票: {', '.join(signal.symbols) if signal.symbols else '-'}",
        f"风险标签: {', '.join(signal.risk_flags) if signal.risk_flags else '-'}",
        f"命中规则: {' | '.join(signal.matched_rules) if signal.matched_rules else '-'}",
        f"摘要: {signal.summary}",
    ]
    if saved_path is not None:
        lines.append(f"已保存: {saved_path}")
    return "\n".join(lines)


def load_price_map(args: argparse.Namespace, parser: argparse.ArgumentParser, symbols: list[str]):
    if args.source == "csv":
        if not args.prices:
            parser.error("--prices is required when --source csv")
        return CsvPriceSource(args.prices).load()

    if not symbols:
        parser.error(f"{args.source} source requires --symbols, --watchlist, or a dynamic universe")

    if args.source == "baostock":
        return BaostockPriceSource(
            symbols=symbols,
            benchmark=args.benchmark,
            start_date=args.start,
            end_date=args.end,
            adjust=args.adjust,
        ).load()

    return AksharePriceSource(
        symbols=symbols,
        benchmark=args.benchmark,
        start_date=args.start,
        end_date=args.end,
        adjust=args.adjust,
    ).load()


def merge_event_maps(*event_maps: dict) -> dict:
    merged = {}
    for event_map in event_maps:
        for code, events in event_map.items():
            merged.setdefault(code, []).extend(events)
    return merged


def candidate_screen_context(args: argparse.Namespace, symbols: list[str]) -> dict:
    return {
        "command": "candidate_screen",
        "source": args.source,
        "benchmark": args.benchmark,
        "symbols": symbols,
        "watchlist": args.watchlist,
        "universe": args.universe,
        "universe_limit": args.universe_limit,
        "cycle": args.cycle,
        "horizon": args.horizon,
        "large_cap_only": not args.allow_small_caps,
        "min_market_cap_billion": args.min_market_cap,
        "exclude_themes": parse_symbols(args.exclude_themes),
        "start": args.start,
        "end": args.end,
        "limit": args.limit,
        "include_excluded": args.include_excluded,
        "enabled_sources": enabled_sources_context(args),
    }


def trade_plan_context(args: argparse.Namespace, symbols: list[str]) -> dict:
    return {
        "command": "trade_plan",
        "source": args.source,
        "benchmark": args.benchmark,
        "positions": args.positions,
        "watchlist": args.watchlist,
        "symbols": symbols,
        "start": args.start,
        "end": args.end,
        "review_date": args.review_date or args.end,
        "capital": args.capital,
        "top": args.top,
        "max_trade_pct": args.max_trade_pct,
        "model_scores": args.model_scores,
    }


def daily_pool_context(args: argparse.Namespace, symbols: list[str], dated_path: Path, latest_path: Path) -> dict:
    return {
        "command": "daily_refresh_pool",
        "benchmark": args.benchmark,
        "symbols": symbols,
        "pool_size": args.pool_size,
        "scan_limit": args.scan_limit,
        "cycle": args.cycle,
        "horizon": args.horizon,
        "min_market_cap_billion": args.min_market_cap,
        "exclude_themes": parse_symbols(args.exclude_themes),
        "start": args.start,
        "end": args.end,
        "dated_path": str(dated_path),
        "latest_path": str(latest_path),
        "enabled_sources": {
            "macro_events": not args.disable_macro_events,
            "macro_rss": not args.disable_macro_rss,
            "policy_pages": not args.disable_policy_pages,
            "market_regime": True,
            "sector_rotation": True,
            "risk_calendar": not args.disable_risk_calendar,
            "pledge_risk": not args.disable_pledge_risk,
            "ordinary_news": args.include_news,
            "disclosures": args.include_disclosures,
            "opinions": not args.disable_opinions,
        },
    }


def enabled_sources_context(args: argparse.Namespace) -> dict:
    return {
        "macro_events": not args.disable_macro_events,
        "macro_rss": not args.disable_macro_rss,
        "policy_pages": not args.disable_policy_pages,
        "market_regime": not args.disable_market_regime and args.source != "csv",
        "sector_rotation": not args.disable_sector_rotation and args.source != "csv",
        "disclosures": not args.disable_disclosures,
        "risk_calendar": not args.disable_risk_calendar and args.source != "csv",
        "pledge_risk": not args.disable_pledge_risk,
        "ordinary_news": not args.disable_news,
        "opinions": not args.disable_opinions,
        "realtime": args.realtime and args.source != "csv",
    }


def infer_profiles_from_names(profiles: dict) -> dict:
    return {
        code: infer_stock_profile(code, profile.name)
        for code, profile in profiles.items()
        if profile.name and profile.name != code
    }


def load_profiles(path: str) -> dict:
    if not path:
        return {}
    profile_path = Path(path)
    if not profile_path.exists() or profile_path.is_dir():
        return {}
    return load_profile_csv(profile_path)


def resolve_universe(args: argparse.Namespace, profile_overrides: dict) -> tuple[list[str], dict]:
    symbols = parse_symbols(args.symbols)
    if args.watchlist:
        symbols.extend(read_watchlist(args.watchlist))
    if args.source == "csv":
        return dedupe(symbols), profile_overrides
    if symbols:
        return dedupe(symbols), profile_overrides

    if args.universe == "akshare":
        result = AkshareUniverseSource(
            limit=args.universe_limit,
            candidates=args.universe_candidates,
            min_turnover_billion=args.min_turnover,
            min_market_cap_billion=args.min_market_cap,
            exclude_themes=tuple(parse_symbols(args.exclude_themes)),
            cache_path=args.universe_cache,
            refresh_cache=args.refresh_universe_cache,
        ).load(profile_overrides=profile_overrides)
        return result.symbols, result.profiles

    return dedupe(list(profile_overrides.keys())), profile_overrides


def parse_symbols(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def read_watchlist(path: str) -> list[str]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        clean = line.split("#", 1)[0].strip()
        if clean:
            rows.append(clean)
    return rows


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def parse_as_of(raw: str | None) -> datetime:
    if not raw:
        return datetime.now()
    return datetime.strptime(raw, "%Y%m%d").replace(hour=23, minute=59, second=59)


def parse_optional_datetime(raw: str) -> datetime | None:
    text = raw.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt in ("%Y-%m-%d", "%Y%m%d"):
                return parsed.replace(hour=23, minute=59, second=59)
            return parsed
        except ValueError:
            pass
    raise RuntimeError(f"unsupported datetime format: {raw}")
