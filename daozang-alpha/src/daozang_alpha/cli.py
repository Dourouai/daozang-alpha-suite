from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import default_config_path, load_config
from .feasibility import render_feasibility, render_roadmap
from .qlib_env import check_environment, format_results
from .smoke import SmokeTestOptions, run_qlib_smoke_test
from .universe import DEFAULT_INDUSTRY_MAP, DEFAULT_LIMIT, DEFAULT_RISK_CALENDAR, DEFAULT_WATCHLISTS


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    parser = argparse.ArgumentParser(description="道藏 Alpha Qlib research assistant")
    parser.add_argument(
        "--config",
        default=str(default_config_path()),
        help="TOML config path",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="check local Qlib package and data directory")
    subparsers.add_parser("feasibility", help="print feasibility analysis")
    subparsers.add_parser("roadmap", help="print project roadmap")
    smoke_parser = subparsers.add_parser("smoke-test-data", help="read a small Qlib data sample")
    smoke_parser.add_argument("--instrument", default="SH600000", help="Qlib instrument code")
    smoke_parser.add_argument("--start", default="2024-01-02", help="start date, YYYY-MM-DD")
    smoke_parser.add_argument("--end", default="2024-01-10", help="end date, YYYY-MM-DD")
    baseline_parser = subparsers.add_parser(
        "run-baseline",
        help="run Alpha158 + LightGBM baseline",
    )
    baseline_parser.add_argument(
        "--quick",
        action="store_true",
        help="use short dates and a small universe",
    )
    baseline_parser.add_argument("--market", default=None, help="Qlib market, e.g. csi300")
    baseline_parser.add_argument(
        "--universe-file",
        default=None,
        help="active universe CSV exported by sync-beichen-universe",
    )
    baseline_parser.add_argument(
        "--max-instruments",
        type=int,
        default=None,
        help="limit instruments for quick runs",
    )
    baseline_parser.add_argument("--top-n", type=int, default=None, help="top score rows to export")
    baseline_parser.add_argument("--train-start", default=None)
    baseline_parser.add_argument("--train-end", default=None)
    baseline_parser.add_argument("--valid-start", default=None)
    baseline_parser.add_argument("--valid-end", default=None)
    baseline_parser.add_argument("--test-start", default=None)
    baseline_parser.add_argument("--test-end", default=None)
    baseline_parser.add_argument("--num-boost-round", type=int, default=None)
    baseline_parser.add_argument("--early-stopping-rounds", type=int, default=None)
    baseline_parser.add_argument("--feature-set", choices=["Alpha158", "Alpha360"], default=None, help="Qlib feature set (default: Alpha158)")
    baseline_parser.add_argument("--objective", choices=["regression", "lambdarank"], default=None, help="training objective (default: regression)")
    baseline_parser.add_argument("--multi-label", action="store_true", help="train on 1d/3d/5d labels simultaneously")
    baseline_parser.add_argument("--ensemble", default="", help="comma-separated additional models: xgb,catboost")
    baseline_parser.add_argument(
        "--extra-features",
        default=None,
        help="optional Beichen daily feature CSV to merge into Qlib features",
    )
    export_parser = subparsers.add_parser(
        "export-scores",
        help="promote and normalize latest alpha score CSV for Beichen",
    )
    export_parser.add_argument("--input", default=None, help="optional source score CSV")
    export_parser.add_argument("--output", default=None, help="optional output score CSV")
    beichen_feature_parser = subparsers.add_parser(
        "export-beichen-features",
        help="convert Beichen decision logs into Daozang daily model features",
    )
    beichen_feature_parser.add_argument("--beichen-root", default="../beichen-alpha")
    beichen_feature_parser.add_argument(
        "--decision-log",
        default="data/decision_logs/recommendations.jsonl",
    )
    beichen_feature_parser.add_argument(
        "--output",
        default="data/features/beichen_daily_features_latest.csv",
    )
    beichen_feature_parser.add_argument("--min-date", default=None)
    beichen_feature_parser.add_argument("--max-date", default=None)
    universe_parser = subparsers.add_parser(
        "sync-beichen-universe",
        help="build Daozang active universe from Beichen positions, watchlists, and cache",
    )
    universe_parser.add_argument("--beichen-root", default="../beichen-alpha")
    universe_parser.add_argument("--out", default="data/universe/active_universe.csv")
    universe_parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    universe_parser.add_argument("--positions", default="data/positions/current_positions.json")
    universe_parser.add_argument(
        "--watchlist",
        action="append",
        default=None,
        help="relative Beichen watchlist path; can be passed multiple times",
    )
    universe_parser.add_argument("--universe-cache", default="data/cache/universe_latest.jsonl")
    universe_parser.add_argument("--qlib-data-dir", default="data/qlib/cn_data")
    universe_parser.add_argument("--industry-map", default=DEFAULT_INDUSTRY_MAP)
    universe_parser.add_argument("--risk-calendar", default=DEFAULT_RISK_CALENDAR)
    industry_parser = subparsers.add_parser(
        "sync-akshare-industry-map",
        help="fetch Eastmoney industry board constituents through AKShare",
    )
    industry_parser.add_argument("--out", default=DEFAULT_INDUSTRY_MAP)
    industry_parser.add_argument("--target-universe", default="data/universe/active_universe.csv")
    industry_parser.add_argument("--board-limit", type=int, default=None)
    risk_parser = subparsers.add_parser(
        "sync-akshare-risk-calendar",
        help="fetch restricted release and earnings disclosure windows through AKShare",
    )
    risk_parser.add_argument("--out", default=DEFAULT_RISK_CALENDAR)
    risk_parser.add_argument("--target-universe", default="data/universe/active_universe.csv")
    risk_parser.add_argument("--as-of", default=None)
    risk_parser.add_argument("--forward-days", type=int, default=60)
    risk_parser.add_argument("--report-period", default=None)
    qlib_bars_parser = subparsers.add_parser(
        "sync-akshare-qlib-bars",
        help="incrementally append AKShare daily bars into local Qlib bin data",
    )
    qlib_bars_parser.add_argument("--qlib-data-dir", default="data/qlib/cn_data")
    qlib_bars_parser.add_argument("--universe-file", default="data/universe/active_universe.csv")
    qlib_bars_parser.add_argument("--benchmark", default="000300")
    qlib_bars_parser.add_argument("--start", default=None)
    qlib_bars_parser.add_argument("--end", default=None)
    qlib_bars_parser.add_argument("--adjust", default="qfq")
    qlib_bars_parser.add_argument("--max-instruments", type=int, default=None)
    qlib_bars_parser.add_argument("--workers", type=int, default=8)
    qlib_bars_parser.add_argument("--request-timeout", type=float, default=4.0)
    qlib_bars_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    command = args.command or "feasibility"

    if command == "feasibility":
        print(render_feasibility())
        return 0

    if command == "roadmap":
        print(render_roadmap())
        return 0

    if command == "doctor":
        config = load_config(Path(args.config))
        print("道藏 Alpha environment check")
        print(f"config: {Path(args.config).expanduser()}")
        print(f"provider_uri: {config.qlib.provider_uri}")
        print(format_results(check_environment(config)))
        return 0

    if command == "smoke-test-data":
        config = load_config(Path(args.config))
        options = SmokeTestOptions(
            instrument=args.instrument,
            start_time=args.start,
            end_time=args.end,
        )
        print("道藏 Alpha Qlib data smoke test")
        print(f"provider_uri: {config.qlib.provider_uri}")
        print(run_qlib_smoke_test(config, options))
        return 0

    if command == "run-baseline":
        from .baseline import BaselineOptions, render_baseline_summary, run_baseline

        config = load_config(Path(args.config))
        max_instruments = args.max_instruments
        if args.quick and max_instruments is None:
            max_instruments = 20
        options = BaselineOptions(
            market=args.market,
            universe_file=args.universe_file,
            max_instruments=max_instruments,
            top_n=args.top_n,
            quick=args.quick,
            train_start=args.train_start,
            train_end=args.train_end,
            valid_start=args.valid_start,
            valid_end=args.valid_end,
            test_start=args.test_start,
            test_end=args.test_end,
            num_boost_round=args.num_boost_round,
            early_stopping_rounds=args.early_stopping_rounds,
            feature_set=args.feature_set or None,
            objective=args.objective or None,
            multi_label=args.multi_label,
            ensemble=args.ensemble or "",
            extra_features=args.extra_features,
        )
        artifacts = run_baseline(config, options)
        print(render_baseline_summary(artifacts))
        return 0

    if command == "export-beichen-features":
        from .beichen_features import (
            ExportBeichenFeaturesOptions,
            export_beichen_features,
            render_export_beichen_features_summary,
        )

        artifacts = export_beichen_features(
            ExportBeichenFeaturesOptions(
                beichen_root=args.beichen_root,
                decision_log=args.decision_log,
                output_path=args.output,
                min_date=args.min_date,
                max_date=args.max_date,
            )
        )
        print(render_export_beichen_features_summary(artifacts))
        return 0

    if command == "export-scores":
        from .export_scores import ExportScoresOptions, export_scores, render_export_scores_summary

        config = load_config(Path(args.config))
        artifacts = export_scores(
            config,
            ExportScoresOptions(input_path=args.input, output_path=args.output),
        )
        print(render_export_scores_summary(artifacts))
        return 0

    if command == "sync-beichen-universe":
        from .universe import SyncUniverseOptions, render_sync_universe_summary, sync_beichen_universe

        artifacts = sync_beichen_universe(
            SyncUniverseOptions(
                beichen_root=args.beichen_root,
                output_path=args.out,
                limit=args.limit,
                positions_path=args.positions,
                watchlists=tuple(args.watchlist) if args.watchlist else DEFAULT_WATCHLISTS,
                universe_cache=args.universe_cache,
                qlib_data_dir=args.qlib_data_dir,
                industry_map=args.industry_map,
                risk_calendar=args.risk_calendar,
            )
        )
        print(render_sync_universe_summary(artifacts))
        return 0

    if command == "sync-akshare-industry-map":
        from .universe import (
            SyncIndustryMapOptions,
            render_sync_industry_map_summary,
            sync_akshare_industry_map,
        )

        artifacts = sync_akshare_industry_map(
            SyncIndustryMapOptions(
                output_path=args.out,
                target_universe=args.target_universe,
                board_limit=args.board_limit,
            )
        )
        print(render_sync_industry_map_summary(artifacts))
        return 0

    if command == "sync-akshare-risk-calendar":
        from .universe import (
            SyncRiskCalendarOptions,
            render_sync_risk_calendar_summary,
            sync_akshare_risk_calendar,
        )

        artifacts = sync_akshare_risk_calendar(
            SyncRiskCalendarOptions(
                output_path=args.out,
                target_universe=args.target_universe,
                as_of=args.as_of,
                forward_days=args.forward_days,
                report_period=args.report_period,
            )
        )
        print(render_sync_risk_calendar_summary(artifacts))
        return 0

    if command == "sync-akshare-qlib-bars":
        from .qlib_incremental import (
            SyncAkshareQlibBarsOptions,
            render_sync_akshare_qlib_bars_summary,
            sync_akshare_qlib_bars,
        )

        artifacts = sync_akshare_qlib_bars(
            SyncAkshareQlibBarsOptions(
                qlib_data_dir=args.qlib_data_dir,
                universe_file=args.universe_file,
                benchmark=args.benchmark,
                start=args.start,
                end=args.end,
                adjust=args.adjust,
                max_instruments=args.max_instruments,
                workers=args.workers,
                request_timeout=args.request_timeout,
                dry_run=args.dry_run,
            )
        )
        print(render_sync_akshare_qlib_bars_summary(artifacts))
        return 0

    parser.print_help()
    return 2
