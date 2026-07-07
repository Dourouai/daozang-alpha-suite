from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .content_sources import ManualTextSource, WechatArticleSource
from .data_health import check_data_health, format_data_health_card, format_data_health_text
from .data_prewarm import (
    combine_factor_rows,
    flow_daily_rows,
    global_daily_row,
    load_market_structure,
    sentiment_daily_rows,
    snapshot_payload,
    upsert_csv_rows,
    write_snapshot_json,
)
from .data_sources import (
    AksharePriceSource,
    AkshareMarketRegimeSource,
    AkshareMarketStructureSource,
    AkshareSectorRotationSource,
    AkshareUniverseSource,
    BaostockPriceSource,
    CsvMacroEventSource,
    CsvPriceSource,
    QlibBinPriceSource,
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
    load_daozang_profiles,
    load_profile_csv,
    infer_stock_profile,
    merge_profiles,
    resolve_fred_series,
    resolve_yahoo_tickers,
    save_universe_cache,
    write_global_feature_dataset,
)
from .data_sources.flow_source import AkshareFlowSource
from .data_sources.sentiment_source import AkshareSentimentSource
from .data_sources.advanced_source import AkshareAdvancedSource
from .data_sources.heat_source import AkshareHeatSource
from .strategy.bond_factor import load_bond_map, load_etf_scale_map
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
from .outcome_backfill import backfill_decision_log
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
from .risk_sources import (
    AkshareRiskCalendarSource,
    disclosure_events_to_risk_calendar,
    load_static_risk_calendar,
    merge_risk_event_maps,
)
from .strategy_performance import (
    read_jsonl_records,
    render_strategy_performance_report,
    summarize_strategy_performance,
)
from .strategy import (
    build_realtime_checks,
    build_three_day_trade_plan,
    inspect_model_score_coverage,
    load_model_scores,
    load_positions,
    rank_recommendations,
)


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
    if argv and argv[0] == "prewarm-factors":
        return prewarm_factors_main(argv[1:])
    if argv and argv[0] == "trade-plan":
        return trade_plan_main(argv[1:])
    if argv and argv[0] == "healthcheck":
        return healthcheck_main(argv[1:])
    if argv and argv[0] == "chat-server":
        return chat_server_main(argv[1:])
    if argv and argv[0] == "backfill-outcomes":
        return backfill_outcomes_main(argv[1:])
    if argv and argv[0] == "strategy-performance":
        return strategy_performance_main(argv[1:])
    if argv and argv[0] == "data-health":
        return data_health_main(argv[1:])

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
        "--daozang-active-universe",
        default="../daozang-alpha/data/universe/active_universe.csv",
        help="Daozang active universe profile CSV",
    )
    parser.add_argument(
        "--daozang-industry-map",
        default="../daozang-alpha/data/universe/akshare_industry_map.csv",
        help="Daozang AKShare industry map CSV",
    )
    parser.add_argument("--disable-daozang-profiles", action="store_true", help="skip Daozang profile CSVs")
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
    parser.add_argument(
        "--static-risk-calendar",
        default="../daozang-alpha/data/universe/akshare_risk_calendar.csv",
        help="prebuilt Daozang risk calendar CSV",
    )
    parser.add_argument("--disable-static-risk-calendar", action="store_true", help="skip prebuilt Daozang risk calendar CSV")
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
    parser.add_argument("--disable-flow-factor", action="store_true", help="disable flow-based factors (龙虎榜/北向/主力资金)")
    parser.add_argument("--flow-lhb-lookback", type=int, default=5, help="龙虎榜 lookback days")
    parser.add_argument("--flow-northbound-lookback", type=int, default=5, help="北向资金 lookback days")
    parser.add_argument("--flow-fund-lookback", type=int, default=3, help="主力资金 lookback days")
    parser.add_argument("--disable-global-linkage", action="store_true", help="disable global linkage factor (美股映射)")
    parser.add_argument("--disable-sentiment", action="store_true", help="disable sentiment/leverage factors (涨停板/融资融券/期货)")
    parser.add_argument("--disable-advanced", action="store_true", help="disable advanced factors (股东增减持)")
    parser.add_argument("--sector-limit", type=int, default=40, help="max industry boards checked for rotation")
    parser.add_argument("--disable-opinions", action="store_true", help="disable personal opinion news source")
    parser.add_argument("--opinion-lookback-days", type=int, default=7, help="personal opinion signal lookback window")
    parser.add_argument("--opinion-as-of", default="", help="personal opinion signal cutoff time; default is now")
    parser.add_argument(
        "--opinion-signals",
        default="data/opinion_signals.jsonl",
        help="personal opinion signal JSONL path",
    )
    parser.add_argument("--disable-model-scores", action="store_true", help="disable Daozang model score factor")
    parser.add_argument(
        "--model-scores",
        default="../daozang-alpha/data/exports/alpha_scores_latest.csv",
        help="Daozang latest score CSV",
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
        daozang_profiles = load_cli_daozang_profiles(args)
        manual_profiles = load_profiles(args.profile)
        profile_overrides = merge_profiles(daozang_profiles, manual_profiles)
        if daozang_profiles:
            log_step(args, f"道藏画像: {len(daozang_profiles)} 条")
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
        opinion_as_of = resolve_opinion_as_of(args.opinion_as_of, as_of)
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
            static_calendar_events = (
                {}
                if args.disable_static_risk_calendar
                else load_static_risk_calendar(
                    args.static_risk_calendar,
                    symbols=stock_symbols,
                    as_of=as_of,
                    forward_days=args.risk_forward_days,
                )
            )
            calendar_events = AkshareRiskCalendarSource(
                symbols=stock_symbols,
                as_of=as_of,
                forward_days=args.risk_forward_days,
                include_pledge=not args.disable_pledge_risk,
            ).load()
            disclosure_calendar_events = (
                {} if args.disable_disclosures else disclosure_events_to_risk_calendar(disclosure_events)
            )
            risk_calendar_events = merge_risk_event_maps(static_calendar_events, calendar_events, disclosure_calendar_events)
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
            as_of=opinion_as_of,
            lookback_days=args.opinion_lookback_days,
        ).load()
        news_events = merge_event_maps(news_events, opinion_events)
        if args.disable_model_scores:
            log_step(args, "已跳过道藏模型分数。")
            model_scores = {}
        else:
            model_scores = load_model_scores(args.model_scores)
            log_step(args, f"道藏模型分数: {len(model_scores)} 条")
        if args.disable_flow_factor:
            log_step(args, "已跳过资金面因子。")
            flow_snapshot = None
        else:
            log_step(args, "加载资金面数据（龙虎榜/北向/主力资金）...")
            flow_snapshot = AkshareFlowSource(
                symbols=stock_symbols,
                as_of=as_of,
                lhb_lookback_days=args.flow_lhb_lookback,
                northbound_lookback_days=args.flow_northbound_lookback,
                fund_flow_lookback_days=args.flow_fund_lookback,
            ).load()
            log_step(args, f"资金面: {', '.join(flow_snapshot.source_health) if flow_snapshot.source_health else '无数据'}")
        if args.disable_global_linkage:
            log_step(args, "已跳过全球联动因子。")
            global_linkage_snapshot = None
        else:
            log_step(args, "加载全球联动数据（美股/汇率/商品）...")
            global_linkage_snapshot = GlobalLinkageSource().load()
            log_step(args, f"全球联动: 姿态{global_linkage_snapshot.posture}, 得分{global_linkage_snapshot.score}")
        if args.disable_sentiment:
            sentiment_snapshot = None
        else:
            log_step(args, "加载情绪/杠杆数据...")
            sentiment_snapshot = AkshareSentimentSource(symbols=stock_symbols, as_of=as_of).load()
            log_step(args, f"情绪杠杆: {', '.join(sentiment_snapshot.source_health)}")
        if args.disable_advanced:
            advanced_snapshot = None
        else:
            log_step(args, "加载高级数据...")
            advanced_snapshot = AkshareAdvancedSource(symbols=stock_symbols, as_of=as_of).load()
            log_step(args, f"高级数据: {', '.join(advanced_snapshot.source_health)}")
        if args.disable_advanced:
            bond_map = {}
            etf_scale_map = {}
            heat_snapshot = None
        else:
            # Load bond & ETF data with advanced/heat sources; these endpoints can be slow.
            bond_map = load_bond_map()
            etf_scale_map = load_etf_scale_map()
            log_step(args, "加载板块热度数据（ETF资金流/概念/大宗）...")
            heat_snapshot = AkshareHeatSource(symbols=stock_symbols, as_of=as_of).load()
            log_step(args, f"板块热度: {', '.join(heat_snapshot.source_health)}")
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
            model_scores=model_scores,
            flow_snapshot=flow_snapshot,
            global_linkage_snapshot=global_linkage_snapshot,
            sentiment_snapshot=sentiment_snapshot,
            advanced_snapshot=advanced_snapshot,
            bond_map=bond_map,
            etf_scale_map=etf_scale_map,
            heat_snapshot=heat_snapshot,
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


def prewarm_factors_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Prewarm flow/global/sentiment factor snapshots")
    parser.add_argument("--symbols", default="", help="comma-separated stock codes")
    parser.add_argument(
        "--watchlist",
        default=os.environ.get("BEICHEN_TRADE_WATCHLIST", "data/watchlists/trade_target_pool_latest.txt"),
        help="candidate watchlist used for factor prewarm",
    )
    parser.add_argument(
        "--positions",
        default=os.environ.get("BEICHEN_POSITIONS", "data/positions/current_positions.json"),
        help="current positions JSON path; holdings are always included",
    )
    parser.add_argument("--limit", type=int, default=int(os.environ.get("BEICHEN_PREWARM_FACTOR_LIMIT", "80")), help="max non-position symbols")
    parser.add_argument("--as-of", default="", help="reference date/time, YYYYMMDD or ISO; default now")
    parser.add_argument("--disable-flow", action="store_true", help="skip flow snapshot")
    parser.add_argument("--disable-global", action="store_true", help="skip global linkage snapshot")
    parser.add_argument("--disable-sentiment", action="store_true", help="skip sentiment/leverage snapshot")
    parser.add_argument("--disable-market-structure", action="store_true", help="skip broad market structure columns")
    parser.add_argument("--fred-series", default=os.environ.get("BEICHEN_GLOBAL_FRED_SERIES", ""), help="comma-separated FRED series")
    parser.add_argument("--yahoo-tickers", default=os.environ.get("BEICHEN_GLOBAL_YAHOO_TICKERS", ""), help="comma-separated Yahoo tickers")
    parser.add_argument("--global-lookback-days", type=int, default=20, help="Yahoo daily history lookback for snapshot")
    parser.add_argument("--global-timeout", type=float, default=10.0, help="FRED HTTP timeout")
    parser.add_argument("--flow-json", default=os.environ.get("BEICHEN_PREWARM_FLOW_JSON", "data/runtime/latest_flow_snapshot.json"))
    parser.add_argument("--global-json", default=os.environ.get("BEICHEN_PREWARM_GLOBAL_JSON", "data/runtime/latest_global_linkage.json"))
    parser.add_argument("--sentiment-json", default=os.environ.get("BEICHEN_PREWARM_SENTIMENT_JSON", "data/runtime/latest_sentiment_snapshot.json"))
    parser.add_argument("--status-json", default=os.environ.get("BEICHEN_PREWARM_FACTOR_STATUS_JSON", "data/runtime/latest_factor_prewarm.json"))
    parser.add_argument("--flow-daily", default=os.environ.get("BEICHEN_PREWARM_FLOW_DAILY", "data/features/flow_daily.csv"))
    parser.add_argument("--global-daily", default=os.environ.get("BEICHEN_PREWARM_GLOBAL_DAILY", "data/features/global_linkage_snapshot_daily.csv"))
    parser.add_argument("--sentiment-daily", default=os.environ.get("BEICHEN_PREWARM_SENTIMENT_DAILY", "data/features/sentiment_daily.csv"))
    parser.add_argument("--combined-daily", default=os.environ.get("BEICHEN_PREWARM_COMBINED_DAILY", "data/features/beichen_factor_daily.csv"))
    args = parser.parse_args(argv)

    as_of = parse_as_of(args.as_of)
    positions = load_positions(args.positions)
    position_symbols = [str(item["code"]) for item in positions]
    candidate_symbols = parse_symbols(args.symbols) if args.symbols else read_optional_watchlist(args.watchlist)
    held = set(position_symbols)
    if args.limit > 0:
        limited_candidates = []
        for symbol in candidate_symbols:
            if symbol in held or len(limited_candidates) < args.limit:
                limited_candidates.append(symbol)
        candidate_symbols = limited_candidates
    symbols = dedupe(position_symbols + candidate_symbols)
    if not symbols:
        print("Error: no symbols to prewarm", file=sys.stderr)
        return 2

    started_at = datetime.now()
    statuses: dict[str, dict[str, object]] = {}
    flow_rows: list[dict[str, object]] = []
    sentiment_rows: list[dict[str, object]] = []
    global_row: dict[str, object] | None = None

    if args.disable_flow:
        statuses["flow"] = {"status": "skipped"}
    else:
        try:
            flow_snapshot = AkshareFlowSource(symbols=symbols, as_of=as_of).load()
            write_snapshot_json(args.flow_json, snapshot_payload("flow", as_of, symbols, flow_snapshot))
            flow_rows = flow_daily_rows(flow_snapshot, symbols, as_of)
            upsert_csv_rows(args.flow_daily, flow_rows, key_fields=("date", "code"))
            statuses["flow"] = {
                "status": "ok",
                "rows": len(flow_rows),
                "health": list(flow_snapshot.source_health),
                "json": args.flow_json,
                "daily": args.flow_daily,
            }
        except Exception as exc:
            write_snapshot_json(args.flow_json, snapshot_payload("flow", as_of, symbols, None, error=f"{type(exc).__name__}: {exc}"))
            statuses["flow"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    if args.disable_global:
        statuses["global"] = {"status": "skipped"}
    else:
        try:
            global_snapshot = GlobalLinkageSource(
                fred_series=resolve_fred_series(args.fred_series),
                yahoo_tickers=resolve_yahoo_tickers(args.yahoo_tickers),
                lookback_days=args.global_lookback_days,
                timeout=args.global_timeout,
            ).load()
            write_snapshot_json(args.global_json, snapshot_payload("global_linkage", as_of, symbols, global_snapshot))
            global_row = global_daily_row(global_snapshot, as_of)
            upsert_csv_rows(args.global_daily, [global_row], key_fields=("date",))
            statuses["global"] = {
                "status": "ok",
                "posture": global_snapshot.posture,
                "score": global_snapshot.score,
                "signals": list(global_snapshot.signals),
                "json": args.global_json,
                "daily": args.global_daily,
            }
        except Exception as exc:
            write_snapshot_json(args.global_json, snapshot_payload("global_linkage", as_of, symbols, None, error=f"{type(exc).__name__}: {exc}"))
            statuses["global"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    if args.disable_sentiment:
        statuses["sentiment"] = {"status": "skipped"}
    else:
        try:
            sentiment_snapshot = AkshareSentimentSource(symbols=symbols, as_of=as_of).load()
            market_structure = None if args.disable_market_structure else load_market_structure(as_of)
            write_snapshot_json(
                args.sentiment_json,
                {
                    **snapshot_payload("sentiment", as_of, symbols, sentiment_snapshot),
                    "market_structure": market_structure,
                },
            )
            sentiment_rows = sentiment_daily_rows(sentiment_snapshot, symbols, as_of, market_structure)
            upsert_csv_rows(args.sentiment_daily, sentiment_rows, key_fields=("date", "code"))
            statuses["sentiment"] = {
                "status": "ok",
                "rows": len(sentiment_rows),
                "health": list(sentiment_snapshot.source_health),
                "market_structure": "" if market_structure is None else market_structure.detail,
                "json": args.sentiment_json,
                "daily": args.sentiment_daily,
            }
        except Exception as exc:
            write_snapshot_json(args.sentiment_json, snapshot_payload("sentiment", as_of, symbols, None, error=f"{type(exc).__name__}: {exc}"))
            statuses["sentiment"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    combined_rows = combine_factor_rows(flow_rows, sentiment_rows, global_row)
    if combined_rows:
        upsert_csv_rows(args.combined_daily, combined_rows, key_fields=("date", "code"))
    status_payload = {
        "status": "ok" if not any(item.get("status") == "failed" for item in statuses.values()) else "partial",
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(timespec="seconds"),
        "symbols": len(symbols),
        "positions": len(position_symbols),
        "candidate_limit": args.limit,
        "combined_rows": len(combined_rows),
        "combined_daily": args.combined_daily,
        "sources": statuses,
    }
    write_snapshot_json(args.status_json, status_payload)

    print(f"factor prewarm status: {status_payload['status']}")
    print(f"symbols: {len(symbols)}")
    print(f"combined rows: {len(combined_rows)}")
    print(f"status: {args.status_json}")
    if combined_rows:
        print(f"combined daily: {args.combined_daily}")
    return 0 if status_payload["status"] in {"ok", "partial"} else 2


def healthcheck_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check Beichen Alpha runtime readiness")
    parser.add_argument("--positions", default="data/positions/current_positions.json", help="current positions JSON path")
    parser.add_argument(
        "--min-positions",
        type=int,
        default=int(os.environ.get("BEICHEN_MIN_POSITIONS", "1")),
        help="minimum expected local position count",
    )
    parser.add_argument(
        "--watchlist",
        default=os.environ.get("BEICHEN_BROAD_WATCHLIST", "data/watchlists/broad_target_pool_latest.txt"),
        help="candidate watchlist path",
    )
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
    watchlist_path = Path(args.watchlist)
    add_check(checks, "watchlist", watchlist_path.exists(), args.watchlist, "error")
    add_check(checks, "model_scores", Path(args.model_scores).exists(), args.model_scores, "warning")
    if watchlist_path.exists() and Path(args.model_scores).exists():
        coverage = inspect_model_score_coverage(args.model_scores, read_watchlist(args.watchlist))
        add_check(
            checks,
            "model_score_coverage",
            bool(not coverage.missing and not coverage.stale),
            coverage.detail,
            "warning",
        )
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


def backfill_outcomes_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Backfill forward-return outcomes for decision log records")
    parser.add_argument("--log", default=str(DEFAULT_DECISION_LOG_PATH), help="decision log JSONL path")
    parser.add_argument("--out", default="", help="output path; default appends .backfilled.jsonl suffix")
    parser.add_argument("--start-date", default="", help="only process records on/after YYYY-MM-DD")
    parser.add_argument("--end-date", default="", help="only process records on/before YYYY-MM-DD")
    parser.add_argument("--horizons", default="1,3,5,10", help="comma-separated forward horizons in trading days")
    parser.add_argument("--quiet", action="store_true", help="suppress progress messages")
    args = parser.parse_args(argv)

    try:
        horizons = tuple(int(h.strip()) for h in args.horizons.split(",") if h.strip())
        summary = backfill_decision_log(
            log_path=args.log,
            output_path=args.out or None,
            start_date=args.start_date or None,
            end_date=args.end_date or None,
            horizons=horizons,
            quiet=args.quiet,
        )
    except Exception as exc:
        print(f"Error: backfill failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    import json as _json
    print(_json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if summary.get("total_records", 0) > 0 else 1


def strategy_performance_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Summarize strategy attribution from backfilled decision logs")
    parser.add_argument("--log", default="", help="decision log JSONL path; defaults to backfilled log when present")
    parser.add_argument("--horizons", default="1,3,5", help="comma-separated forward horizons in trading days")
    parser.add_argument("--min-samples", type=int, default=1, help="minimum samples for a bucket to be displayed")
    parser.add_argument("--out", default="", help="optional report text output path")
    parser.add_argument("--notify", choices=["none", "feishu"], default="none", help="send report to Feishu")
    parser.add_argument("--notify-title", default="北辰 Alpha 策略复盘归因报告", help="Feishu text title")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        horizons = tuple(int(item.strip()) for item in args.horizons.split(",") if item.strip())
        log_path = resolve_strategy_performance_log(args.log)
        records = read_jsonl_records(log_path)
        summary = summarize_strategy_performance(
            records,
            horizons=horizons,
            min_samples=args.min_samples,
        )
        summary["log_path"] = str(log_path)
        report_text = render_strategy_performance_report(summary)
    except Exception as exc:
        print(f"Error: strategy-performance failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    if args.json:
        import json as _json
        print(_json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        print(report_text)
        print(f"Log path: {log_path}")
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report_text + f"\nLog path: {log_path}\n", encoding="utf-8")
    if args.notify == "feishu":
        from .notifiers import send_text

        send_text(args.notify_title + "\n" + truncate_text_for_feishu(report_text))
    return 0


def truncate_text_for_feishu(value: str, limit: int = 3500) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 40].rstrip() + "\n...（报告过长，已截断）"


def resolve_strategy_performance_log(value: str) -> Path:
    if value:
        return Path(value)
    backfilled = DEFAULT_DECISION_LOG_PATH.with_suffix(".backfilled.jsonl")
    if backfilled.exists():
        return backfilled
    return DEFAULT_DECISION_LOG_PATH


def data_health_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check data health before generating trade plans")
    parser.add_argument("--positions", default="data/positions/current_positions.json", help="current positions JSON path")
    parser.add_argument("--decision-log", default=str(DEFAULT_DECISION_LOG_PATH), help="local JSONL decision log path")
    parser.add_argument("--model-scores", default="../daozang-alpha/data/exports/alpha_scores_latest.csv", help="Daozang score CSV")
    parser.add_argument("--active-universe", default="../daozang-alpha/data/universe/active_universe.csv", help="active universe CSV")
    parser.add_argument("--risk-calendar", default="../daozang-alpha/data/universe/akshare_risk_calendar.csv", help="risk calendar CSV")
    parser.add_argument("--industry-map", default="../daozang-alpha/data/universe/akshare_industry_map.csv", help="industry map CSV")
    parser.add_argument("--max-model-score-age", type=int, default=2, help="max acceptable model score age in days")
    parser.add_argument("--notify", choices=["none", "feishu"], default="none", help="send health report to Feishu")
    parser.add_argument("--notify-title", default="北辰 Alpha 数据健康", help="Feishu notification title")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        report = check_data_health(
            positions_path=args.positions,
            decision_log_path=args.decision_log,
            model_scores_path=args.model_scores,
            active_universe_path=args.active_universe,
            risk_calendar_path=args.risk_calendar,
            industry_map_path=args.industry_map,
            max_model_score_age_days=args.max_model_score_age,
        )
    except Exception as exc:
        print(f"Error: data-health check failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    if args.json:
        import dataclasses as _dc
        import json as _json
        report_dict = _dc.asdict(report)
        report_dict["as_of"] = report.as_of.isoformat()
        print(_json.dumps(report_dict, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_data_health_text(report))

    if args.notify == "feishu":
        card = format_data_health_card(report, title=args.notify_title)
        send_card(card)
        print("Feishu notification sent.")

    return 0 if report.is_healthy else 1


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
    parser.add_argument(
        "--watchlist",
        default=os.environ.get("BEICHEN_BROAD_WATCHLIST", "data/watchlists/broad_target_pool_latest.txt"),
        help="candidate watchlist path",
    )
    parser.add_argument(
        "--priority-watchlist",
        default=os.environ.get("BEICHEN_PRIORITY_WATCHLIST", "data/watchlists/innovation_drug_pool.txt"),
        help="extra priority candidate watchlist, merged into trade planning",
    )
    parser.add_argument("--source", choices=["akshare", "baostock", "qlib"], default="baostock", help="daily bar source")
    parser.add_argument("--qlib-provider-uri", default=os.environ.get("QLIB_PROVIDER_URI", "../daozang-alpha/data/qlib/cn_data"), help="Qlib binary provider directory")
    parser.add_argument("--benchmark", default="000300", help="benchmark index code")
    parser.add_argument("--profile", default="config/profile_overrides.csv", help="optional stock profile override CSV path")
    parser.add_argument(
        "--daozang-active-universe",
        default="../daozang-alpha/data/universe/active_universe.csv",
        help="Daozang active universe profile CSV",
    )
    parser.add_argument(
        "--daozang-industry-map",
        default="../daozang-alpha/data/universe/akshare_industry_map.csv",
        help="Daozang AKShare industry map CSV",
    )
    parser.add_argument("--disable-daozang-profiles", action="store_true", help="skip Daozang profile CSVs")
    parser.add_argument("--start", default=None, help="start date for candidate bars; default is source-specific recent history")
    parser.add_argument("--end", default=None, help="end date for candidate bars; default is today")
    parser.add_argument("--review-date", default="", help="holding review date, YYYYMMDD; default uses --end")
    parser.add_argument("--capital", type=float, default=10000.0, help="account capital for planning")
    parser.add_argument("--top", type=int, default=3, help="number of buy candidates")
    parser.add_argument("--max-trade-pct", type=float, default=None, help="optional single trade budget cap as capital fraction")
    parser.add_argument("--model-scores", default="../daozang-alpha/data/exports/alpha_scores_latest.csv", help="Daozang latest score CSV")
    parser.add_argument("--disable-opinions", action="store_true", help="disable personal opinion source")
    parser.add_argument("--disable-flow-factor", action="store_true", help="disable flow-based factors (龙虎榜/北向/主力资金)")
    parser.add_argument("--disable-global-linkage", action="store_true", help="disable global linkage factor (美股映射)")
    parser.add_argument("--disable-sentiment", action="store_true", help="disable sentiment/leverage factors")
    parser.add_argument("--disable-advanced", action="store_true", help="disable advanced factors (股东增减持)")
    parser.add_argument("--disable-risk-calendar", action="store_true", help="disable prebuilt risk calendar factor")
    parser.add_argument("--risk-forward-days", type=int, default=30, help="future risk calendar window")
    parser.add_argument(
        "--static-risk-calendar",
        default="../daozang-alpha/data/universe/akshare_risk_calendar.csv",
        help="prebuilt Daozang risk calendar CSV",
    )
    parser.add_argument("--disable-static-risk-calendar", action="store_true", help="skip prebuilt Daozang risk calendar CSV")
    parser.add_argument("--opinion-lookback-days", type=int, default=7, help="personal opinion signal lookback window")
    parser.add_argument("--opinion-as-of", default="", help="personal opinion signal cutoff time; default is now")
    parser.add_argument("--opinion-signals", default="data/opinion_signals.jsonl", help="personal opinion signal JSONL path")
    parser.add_argument(
        "--disable-realtime-execution",
        action="store_true",
        help="skip realtime quote execution facts in trade plan",
    )
    parser.add_argument(
        "--exclude-trade-groups",
        default=os.environ.get("BEICHEN_EXCLUDE_TRADE_GROUPS", "能源"),
        help="comma-separated trade groups excluded from new positions",
    )
    parser.add_argument(
        "--prefer-trade-groups",
        default=os.environ.get("BEICHEN_PREFER_TRADE_GROUPS", "医药"),
        help="comma-separated trade groups preferred for new positions",
    )
    parser.add_argument("--notify", choices=["none", "feishu"], default="none", help="send plan to Feishu")
    parser.add_argument("--decision-log", default=str(DEFAULT_DECISION_LOG_PATH), help="local JSONL decision log path")
    parser.add_argument("--min-model-pct-rank", type=float, default=float(os.environ.get("BEICHEN_MIN_MODEL_PCT_RANK", "0.0")), help="minimum Daozang model pct_rank (0.0-1.0) for candidates")
    args = parser.parse_args(argv)

    try:
        as_of = parse_as_of(args.end)
        review_as_of = parse_as_of(args.review_date) if args.review_date else as_of
        opinion_as_of = resolve_opinion_as_of(args.opinion_as_of, review_as_of)
        positions = load_positions(args.positions)
        symbols = dedupe(
            [item["code"] for item in positions]
            + read_watchlist(args.watchlist)
            + read_optional_watchlist(args.priority_watchlist)
        )
        # Load model scores early for candidate-level filtering
        raw_model_scores = load_model_scores(args.model_scores)
        ranking_model_scores = raw_model_scores
        if args.min_model_pct_rank > 0:
            before = len(symbols)
            # Keep positions always (don't filter holdings)
            held_codes = {item["code"] for item in positions}
            symbols = [
                s for s in symbols
                if s in held_codes
                or raw_model_scores.get(s, 1.0) >= args.min_model_pct_rank  # keep if not in model (no data)
            ]
            filtered_out = before - len(symbols)
            log_step(args, f"道藏候选过滤 ≥{args.min_model_pct_rank:.0%}: {before}→{len(symbols)}只 (排除{filtered_out}只)")
        # Filter only the ranking input. Trade-plan holding review still needs raw scores.
        if args.min_model_pct_rank > 0:
            ranking_model_scores = {k: v for k, v in raw_model_scores.items() if v >= args.min_model_pct_rank}
        if args.source == "qlib":
            price_map = QlibBinPriceSource(
                provider_uri=args.qlib_provider_uri,
                codes=symbols,
            ).load()
            # Also load benchmark from qlib if available
            bench_bars = QlibBinPriceSource(
                provider_uri=args.qlib_provider_uri,
                codes=[args.benchmark],
            ).load()
            if bench_bars:
                price_map[args.benchmark] = bench_bars.get(args.benchmark, [])
        elif args.source == "baostock":
            price_map = BaostockPriceSource(
                symbols=symbols,
                benchmark=args.benchmark,
                start_date=args.start,
                end_date=args.end,
            ).load()
        else:
            price_map = AksharePriceSource(
                symbols=symbols,
                benchmark=args.benchmark,
                start_date=args.start,
                end_date=args.end,
            ).load()
        stock_symbols = [code for code in price_map if code != args.benchmark]
        daozang_profiles = load_cli_daozang_profiles(args)
        manual_profiles = load_profiles(args.profile)
        live_profiles = fetch_tencent_profiles(stock_symbols)
        profiles = merge_profiles(
            daozang_profiles,
            infer_profiles_from_names(live_profiles),
            manual_profiles,
            live_profiles,
        )
        if daozang_profiles:
            log_step(args, f"道藏画像: {len(daozang_profiles)} 条")
        model_coverage = inspect_model_score_coverage(
            args.model_scores,
            stock_symbols,
            as_of=review_as_of,
        )
        if args.disable_realtime_execution:
            realtime_quotes = {}
        else:
            quote_symbols = dedupe(stock_symbols + [str(item["code"]) for item in positions])
            try:
                log_step(args, "加载腾讯实时行情/交易执行因子...")
                realtime_quotes = DefaultMarketDataRouter(quote_symbols).load()
                log_step(args, f"实时行情: {len(realtime_quotes)}/{len(quote_symbols)} 只")
            except Exception as exc:
                realtime_quotes = {}
                log_step(args, f"实时行情不可用，回退日线参考价: {type(exc).__name__}: {exc}")
        if args.disable_risk_calendar or args.disable_static_risk_calendar:
            risk_calendar_events = {}
        else:
            risk_calendar_events = load_static_risk_calendar(
                args.static_risk_calendar,
                symbols=stock_symbols,
                as_of=review_as_of,
                forward_days=args.risk_forward_days,
            )
            risk_count = sum(len(events) for events in risk_calendar_events.values())
            log_step(args, f"道藏风险日历: {risk_count} 个")
        opinion_events = {} if args.disable_opinions else OpinionSignalNewsSource(
            symbols=stock_symbols,
            profiles=profiles,
            path=args.opinion_signals,
            as_of=opinion_as_of,
            lookback_days=args.opinion_lookback_days,
        ).load()
        if args.disable_flow_factor:
            flow_snapshot = None
        else:
            log_step(args, "加载资金面数据...")
            flow_snapshot = AkshareFlowSource(
                symbols=stock_symbols,
                as_of=review_as_of,
            ).load()
            log_step(args, f"资金面: {', '.join(flow_snapshot.source_health) if flow_snapshot.source_health else '无数据'}")
        if args.disable_global_linkage:
            global_linkage_snapshot = None
        else:
            log_step(args, "加载全球联动...")
            global_linkage_snapshot = GlobalLinkageSource().load()
        if args.disable_sentiment:
            sentiment_snapshot = None
        else:
            log_step(args, "加载情绪/杠杆数据...")
            sentiment_snapshot = AkshareSentimentSource(symbols=stock_symbols, as_of=review_as_of).load()
        if args.disable_advanced:
            advanced_snapshot = None
        else:
            advanced_snapshot = AkshareAdvancedSource(symbols=stock_symbols, as_of=review_as_of).load()
        bond_map = load_bond_map()
        etf_scale_map = load_etf_scale_map()
        if args.disable_advanced:
            heat_snapshot = None
        else:
            heat_snapshot = AkshareHeatSource(symbols=stock_symbols, as_of=review_as_of).load()
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
            news_events=opinion_events,
            risk_calendar_events=risk_calendar_events,
            model_scores=ranking_model_scores,
            flow_snapshot=flow_snapshot,
            global_linkage_snapshot=global_linkage_snapshot,
            sentiment_snapshot=sentiment_snapshot,
            advanced_snapshot=advanced_snapshot,
            bond_map=bond_map,
            etf_scale_map=etf_scale_map,
            heat_snapshot=heat_snapshot,
            as_of=opinion_as_of,
        )
        plan = build_three_day_trade_plan(
            recommendations,
            positions,
            capital=args.capital,
            top_n=args.top,
            max_trade_pct=args.max_trade_pct,
            model_scores=raw_model_scores,
            excluded_groups=parse_symbols(args.exclude_trade_groups),
            preferred_groups=parse_symbols(args.prefer_trade_groups),
            review_date=review_as_of,
            trading_dates=[bar.date for bar in price_map.get(args.benchmark, [])],
            model_coverage=model_coverage,
            realtime_quotes=realtime_quotes,
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
    parser.add_argument(
        "--daozang-active-universe",
        default="../daozang-alpha/data/universe/active_universe.csv",
        help="Daozang active universe profile CSV",
    )
    parser.add_argument(
        "--daozang-industry-map",
        default="../daozang-alpha/data/universe/akshare_industry_map.csv",
        help="Daozang AKShare industry map CSV",
    )
    parser.add_argument("--disable-daozang-profiles", action="store_true", help="skip Daozang profile CSVs")
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
    parser.add_argument(
        "--static-risk-calendar",
        default="../daozang-alpha/data/universe/akshare_risk_calendar.csv",
        help="prebuilt Daozang risk calendar CSV",
    )
    parser.add_argument("--disable-static-risk-calendar", action="store_true", help="skip prebuilt Daozang risk calendar CSV")
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
    parser.add_argument("--opinion-as-of", default="", help="personal opinion signal cutoff time; default is now")
    parser.add_argument("--opinion-signals", default="data/opinion_signals.jsonl", help="personal opinion signal JSONL path")
    parser.add_argument("--disable-model-scores", action="store_true", help="disable Daozang model score factor")
    parser.add_argument(
        "--model-scores",
        default="../daozang-alpha/data/exports/alpha_scores_latest.csv",
        help="Daozang latest score CSV",
    )
    parser.add_argument("--notify", choices=["none", "feishu"], default="none", help="send refresh report notification")
    parser.add_argument("--notify-title", default="北辰 Alpha 基础池刷新", help="notification title")
    parser.add_argument("--decision-log", default=str(DEFAULT_DECISION_LOG_PATH), help="local JSONL decision log path")
    parser.add_argument("--quiet", action="store_true", help="hide progress messages")
    args = parser.parse_args(argv)

    try:
        as_of = parse_as_of(args.end)
        opinion_as_of = resolve_opinion_as_of(args.opinion_as_of, as_of)
        date_text = as_of.strftime("%Y-%m-%d")
        out_dir = Path(args.out_dir)
        dated_path = out_dir / f"broad_target_pool_{date_text}.txt"
        latest_path = Path(args.latest_path)
        previous_path = Path(args.previous) if args.previous else find_previous_pool(out_dir, dated_path, latest_path)
        previous_entries = read_watchlist_entries(previous_path) if previous_path else {}

        log_step(args, "刷新全A动态候选宇宙...")
        daozang_profiles = load_cli_daozang_profiles(args)
        manual_profiles = load_profiles(args.profile)
        profile_overrides = merge_profiles(daozang_profiles, manual_profiles)
        if daozang_profiles:
            log_step(args, f"道藏画像: {len(daozang_profiles)} 条")
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
            static_calendar_events = (
                {}
                if args.disable_static_risk_calendar
                else load_static_risk_calendar(
                    args.static_risk_calendar,
                    symbols=stock_symbols,
                    as_of=as_of,
                    forward_days=args.risk_forward_days,
                )
            )
            calendar_events = AkshareRiskCalendarSource(
                symbols=stock_symbols,
                as_of=as_of,
                forward_days=args.risk_forward_days,
                include_pledge=not args.disable_pledge_risk,
            ).load()
            disclosure_calendar_events = disclosure_events_to_risk_calendar(disclosure_events) if disclosure_events else {}
            risk_calendar_events = merge_risk_event_maps(static_calendar_events, calendar_events, disclosure_calendar_events)

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
                as_of=opinion_as_of,
                lookback_days=args.opinion_lookback_days,
            ).load()
            news_events = merge_event_maps(news_events, opinion_events)

        if args.disable_model_scores:
            log_step(args, "已跳过道藏模型分数。")
            model_scores = {}
        else:
            model_scores = load_model_scores(args.model_scores)
            log_step(args, f"道藏模型分数: {len(model_scores)} 条")

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
            model_scores=model_scores,
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
        "model_scores": args.model_scores,
        "daozang_profiles": not args.disable_daozang_profiles,
        "static_risk_calendar": not args.disable_static_risk_calendar,
    }


def trade_plan_context(args: argparse.Namespace, symbols: list[str]) -> dict:
    return {
        "command": "trade_plan",
        "source": args.source,
        "benchmark": args.benchmark,
        "positions": args.positions,
        "watchlist": args.watchlist,
        "priority_watchlist": args.priority_watchlist,
        "symbols": symbols,
        "start": args.start,
        "end": args.end,
        "review_date": args.review_date or args.end,
        "capital": args.capital,
        "top": args.top,
        "max_trade_pct": args.max_trade_pct,
        "model_scores": args.model_scores,
        "daozang_profiles": not args.disable_daozang_profiles,
        "static_risk_calendar": not args.disable_static_risk_calendar,
        "exclude_trade_groups": parse_symbols(args.exclude_trade_groups),
        "prefer_trade_groups": parse_symbols(args.prefer_trade_groups),
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
        "model_scores": args.model_scores,
        "enabled_sources": {
            "macro_events": not args.disable_macro_events,
            "macro_rss": not args.disable_macro_rss,
            "policy_pages": not args.disable_policy_pages,
            "market_regime": True,
            "sector_rotation": True,
            "risk_calendar": not args.disable_risk_calendar,
            "static_risk_calendar": not args.disable_static_risk_calendar,
            "daozang_profiles": not args.disable_daozang_profiles,
            "pledge_risk": not args.disable_pledge_risk,
            "ordinary_news": args.include_news,
            "disclosures": args.include_disclosures,
            "opinions": not args.disable_opinions,
            "model_scores": not args.disable_model_scores,
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
        "static_risk_calendar": not args.disable_static_risk_calendar and args.source != "csv",
        "daozang_profiles": not args.disable_daozang_profiles,
        "pledge_risk": not args.disable_pledge_risk,
        "ordinary_news": not args.disable_news,
        "opinions": not args.disable_opinions,
        "model_scores": not args.disable_model_scores,
        "realtime": args.realtime and args.source != "csv",
    }


def infer_profiles_from_names(profiles: dict) -> dict:
    return {
        code: infer_stock_profile(code, profile.name)
        for code, profile in profiles.items()
        if profile.name and profile.name != code
    }


def load_cli_daozang_profiles(args: argparse.Namespace) -> dict:
    if getattr(args, "disable_daozang_profiles", False):
        return {}
    return load_daozang_profiles(
        getattr(args, "daozang_active_universe", ""),
        getattr(args, "daozang_industry_map", ""),
    )


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


def read_optional_watchlist(path: str) -> list[str]:
    if not path:
        return []
    watchlist_path = Path(path)
    if not watchlist_path.exists() or watchlist_path.is_dir():
        return []
    return read_watchlist(path)


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def resolve_opinion_as_of(raw: str, fallback: datetime) -> datetime:
    if raw.strip():
        parsed = parse_optional_datetime(raw)
        if parsed is not None:
            return parsed
    now = datetime.now()
    return now if now > fallback else fallback


def parse_as_of(raw: str | None) -> datetime:
    if not raw:
        return datetime.now()
    parsed = parse_optional_datetime(raw)
    if parsed is None:
        return datetime.now()
    return parsed


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
