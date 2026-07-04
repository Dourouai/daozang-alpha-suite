from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import default_config_path, load_config
from .feasibility import render_feasibility, render_roadmap
from .qlib_env import check_environment, format_results
from .smoke import SmokeTestOptions, run_qlib_smoke_test


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
        )
        artifacts = run_baseline(config, options)
        print(render_baseline_summary(artifacts))
        return 0

    parser.print_help()
    return 2
