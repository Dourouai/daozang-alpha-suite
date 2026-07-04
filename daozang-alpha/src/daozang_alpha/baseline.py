from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DaozangConfig, SegmentConfig


@dataclass(frozen=True)
class BaselineOptions:
    market: str | None = None
    max_instruments: int | None = None
    top_n: int | None = None
    quick: bool = False
    train_start: str | None = None
    train_end: str | None = None
    valid_start: str | None = None
    valid_end: str | None = None
    test_start: str | None = None
    test_end: str | None = None
    num_boost_round: int | None = None
    early_stopping_rounds: int | None = None


@dataclass(frozen=True)
class BaselineArtifacts:
    report_path: Path
    scores_path: Path
    latest_scores_path: Path
    metrics: dict[str, Any]


def run_baseline(config: DaozangConfig, options: BaselineOptions) -> BaselineArtifacts:
    _prepare_runtime_dirs()

    import lightgbm as lgb
    import qlib
    from qlib.config import REG_CN
    from qlib.contrib.data.handler import Alpha158
    from qlib.data import D
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP

    region: Any = REG_CN if config.qlib.region == "cn" else config.qlib.region
    qlib.init(
        provider_uri=str(config.qlib.provider_path),
        region=region,
        kernels=1,
        joblib_backend="threading",
    )

    segments = _resolve_segments(config, options)
    market = options.market or config.qlib.market
    instruments = _resolve_instruments(D, market, segments, options.max_instruments)
    top_n = options.top_n or config.export.top_n
    num_boost_round = options.num_boost_round or config.model.num_boost_round
    early_stopping_rounds = options.early_stopping_rounds or config.model.early_stopping_rounds

    handler = Alpha158(
        instruments=instruments,
        start_time=segments["train"].start,
        end_time=segments["test"].end,
        fit_start_time=segments["train"].start,
        fit_end_time=segments["train"].end,
        freq=config.dataset.freq,
        label=([config.dataset.label_expression], ["LABEL0"]),
        infer_processors=[
            _make_feature_inf_processor(),
            {"class": "ZScoreNorm", "kwargs": {"fields_group": "feature"}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        learn_processors=[
            {"class": "DropnaLabel"},
        ],
    )
    dataset = DatasetH(
        handler=handler,
        segments={name: (segment.start, segment.end) for name, segment in segments.items()},
    )

    train_frame = _prepare_frame(dataset, "train", DataHandlerLP)
    valid_frame = _prepare_frame(dataset, "valid", DataHandlerLP)
    test_frame = _prepare_frame(dataset, "test", DataHandlerLP)

    model = lgb.train(
        {
            "objective": "regression",
            "metric": "l2",
            "learning_rate": config.model.learning_rate,
            "num_leaves": config.model.num_leaves,
            "max_depth": config.model.max_depth,
            "verbosity": -1,
            "seed": 42,
            "num_threads": max(os.cpu_count() or 1, 1),
        },
        lgb.Dataset(train_frame["x"], label=train_frame["y"]),
        num_boost_round=num_boost_round,
        valid_sets=[
            lgb.Dataset(train_frame["x"], label=train_frame["y"]),
            lgb.Dataset(valid_frame["x"], label=valid_frame["y"]),
        ],
        valid_names=["train", "valid"],
        callbacks=[
            lgb.early_stopping(early_stopping_rounds, verbose=False),
            lgb.log_evaluation(period=25),
        ],
    )

    prediction = pd.Series(model.predict(test_frame["x"]), index=test_frame["index"], name="score")
    label = pd.Series(test_frame["y"], index=test_frame["index"], name="label")
    scored = pd.concat([prediction, label], axis=1).dropna()
    if scored.empty:
        raise RuntimeError("Baseline produced no scored rows on test segment.")

    metrics = _compute_metrics(scored)
    latest_scores = _latest_scores(scored, top_n)
    artifacts = _write_artifacts(
        config=config,
        options=options,
        market=market,
        instruments=instruments,
        segments=segments,
        metrics=metrics,
        latest_scores=latest_scores,
    )
    return artifacts


def render_baseline_summary(artifacts: BaselineArtifacts) -> str:
    metrics = artifacts.metrics
    lines = [
        "道藏 Alpha baseline complete",
        f"report: {artifacts.report_path}",
        f"scores: {artifacts.scores_path}",
        f"latest: {artifacts.latest_scores_path}",
        "",
        "Metrics:",
        f"- rows: {metrics['rows']}",
        f"- dates: {metrics['dates']}",
        f"- mean_ic: {metrics['mean_ic']:.6f}",
        f"- mean_rank_ic: {metrics['mean_rank_ic']:.6f}",
        f"- top_mean_return: {metrics['top_mean_return']:.6f}",
        f"- bottom_mean_return: {metrics['bottom_mean_return']:.6f}",
        f"- long_short_mean_return: {metrics['long_short_mean_return']:.6f}",
    ]
    return "\n".join(lines)


def _prepare_runtime_dirs() -> None:
    Path("reports").mkdir(parents=True, exist_ok=True)
    Path("data/exports").mkdir(parents=True, exist_ok=True)
    Path(".tmp/matplotlib").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(Path(".tmp/matplotlib").resolve()))


def _make_feature_inf_processor() -> Any:
    import numpy as np
    from qlib.data.dataset.processor import Processor, get_group_columns

    class FeatureInfProcessor(Processor):
        def __init__(self, fields_group: str = "feature") -> None:
            self.fields_group = fields_group

        def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
            cols = get_group_columns(df, self.fields_group)
            df.loc[:, cols] = df.loc[:, cols].replace([np.inf, -np.inf], np.nan)
            return df

    return FeatureInfProcessor()


def _resolve_segments(
    config: DaozangConfig,
    options: BaselineOptions,
) -> dict[str, SegmentConfig]:
    if options.quick:
        defaults = {
            "train": SegmentConfig("2023-01-01", "2023-06-30"),
            "valid": SegmentConfig("2023-07-01", "2023-09-30"),
            "test": SegmentConfig("2023-10-01", "2023-12-31"),
        }
    else:
        defaults = {
            "train": config.segments.train,
            "valid": config.segments.valid,
            "test": config.segments.test,
        }
    return {
        "train": SegmentConfig(
            options.train_start or defaults["train"].start,
            options.train_end or defaults["train"].end,
        ),
        "valid": SegmentConfig(
            options.valid_start or defaults["valid"].start,
            options.valid_end or defaults["valid"].end,
        ),
        "test": SegmentConfig(
            options.test_start or defaults["test"].start,
            options.test_end or defaults["test"].end,
        ),
    }


def _resolve_instruments(
    data_api: Any,
    market: str,
    segments: dict[str, SegmentConfig],
    max_instruments: int | None,
) -> list[str] | str:
    if max_instruments is None:
        return market
    instruments = data_api.list_instruments(
        data_api.instruments(market),
        start_time=segments["test"].start,
        end_time=segments["test"].end,
        as_list=True,
    )
    return sorted(instruments)[:max_instruments]


def _prepare_frame(dataset: Any, segment: str, data_handler_module: Any) -> dict[str, Any]:
    frame = dataset.prepare(
        segment,
        col_set=["feature", "label"],
        data_key=data_handler_module.DK_L,
    )
    if frame.empty:
        raise RuntimeError(f"Qlib returned empty {segment} frame.")
    x = frame["feature"].replace([float("inf"), float("-inf")], pd.NA)
    y = frame["label"].iloc[:, 0]
    valid = y.notna()
    return {
        "x": x.loc[valid],
        "y": y.loc[valid],
        "index": y.loc[valid].index,
        "rows": int(valid.sum()),
    }


def _compute_metrics(scored: pd.DataFrame) -> dict[str, Any]:
    by_date = scored.groupby(level="datetime", group_keys=False)
    daily_ic = by_date.apply(
        lambda frame: frame["score"].corr(frame["label"], method="pearson")
    )
    daily_rank_ic = by_date.apply(
        lambda frame: frame["score"].corr(frame["label"], method="spearman")
    )
    group_returns = by_date.apply(_daily_group_returns)

    return {
        "rows": int(len(scored)),
        "dates": int(scored.index.get_level_values("datetime").nunique()),
        "mean_score": _safe_float(scored["score"].mean()),
        "mean_label": _safe_float(scored["label"].mean()),
        "mean_ic": _safe_float(daily_ic.mean()),
        "mean_rank_ic": _safe_float(daily_rank_ic.mean()),
        "ic_positive_ratio": _safe_float((daily_ic > 0).mean()),
        "rank_ic_positive_ratio": _safe_float((daily_rank_ic > 0).mean()),
        "top_mean_return": _safe_float(group_returns["top"].mean()),
        "bottom_mean_return": _safe_float(group_returns["bottom"].mean()),
        "long_short_mean_return": _safe_float(group_returns["long_short"].mean()),
    }


def _daily_group_returns(frame: pd.DataFrame) -> pd.Series:
    if len(frame) < 5:
        return pd.Series({"top": pd.NA, "bottom": pd.NA, "long_short": pd.NA})
    ordered = frame.sort_values("score", ascending=False)
    group_size = max(int(len(ordered) * 0.2), 1)
    top = ordered.head(group_size)["label"].mean()
    bottom = ordered.tail(group_size)["label"].mean()
    return pd.Series({"top": top, "bottom": bottom, "long_short": top - bottom})


def _latest_scores(scored: pd.DataFrame, top_n: int) -> pd.DataFrame:
    latest_date = scored.index.get_level_values("datetime").max()
    latest = scored.xs(latest_date, level="datetime").copy()
    latest = latest.sort_values("score", ascending=False)
    latest["rank"] = range(1, len(latest) + 1)
    latest["pct_rank"] = latest["score"].rank(pct=True)
    latest.insert(0, "trade_date", latest_date.strftime("%Y-%m-%d"))
    latest.insert(1, "instrument", latest.index)
    return latest.head(top_n).reset_index(drop=True)


def _write_artifacts(
    config: DaozangConfig,
    options: BaselineOptions,
    market: str,
    instruments: list[str] | str,
    segments: dict[str, SegmentConfig],
    metrics: dict[str, Any],
    latest_scores: pd.DataFrame,
) -> BaselineArtifacts:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = Path(config.export.path)
    reports_dir = Path(config.export.reports_path)
    export_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    export_scores = latest_scores.copy()
    export_scores["model"] = config.model.name
    export_scores["feature_set"] = config.model.feature_set
    export_scores["horizon_days"] = config.dataset.label_horizon_days
    export_scores["universe"] = market

    scores_path = export_dir / f"alpha_scores_{stamp}.csv"
    latest_scores_path = export_dir / "alpha_scores_latest.csv"
    report_path = reports_dir / f"baseline_{stamp}.json"

    export_scores.to_csv(scores_path, index=False)
    export_scores.to_csv(latest_scores_path, index=False)
    score_date = None if export_scores.empty else str(export_scores["trade_date"].iloc[0])

    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "market": market,
        "instrument_count": len(instruments) if isinstance(instruments, list) else "market",
        "feature_set": config.model.feature_set,
        "model": config.model.name,
        "model_params": {
            "num_boost_round": options.num_boost_round or config.model.num_boost_round,
            "early_stopping_rounds": (
                options.early_stopping_rounds or config.model.early_stopping_rounds
            ),
            "learning_rate": config.model.learning_rate,
            "num_leaves": config.model.num_leaves,
            "max_depth": config.model.max_depth,
        },
        "label_expression": config.dataset.label_expression,
        "label_horizon_days": config.dataset.label_horizon_days,
        "segments": {name: segment.__dict__ for name, segment in segments.items()},
        "quick": options.quick,
        "metrics": metrics,
        "score_date": score_date,
        "top_n": config.export.top_n if options.top_n is None else options.top_n,
        "exported_score_rows": int(len(export_scores)),
        "scores_path": str(scores_path),
        "latest_scores_path": str(latest_scores_path),
    }
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return BaselineArtifacts(
        report_path=report_path,
        scores_path=scores_path,
        latest_scores_path=latest_scores_path,
        metrics=metrics,
    )


def _safe_float(value: Any) -> float:
    if pd.isna(value):
        return 0.0
    return float(value)
