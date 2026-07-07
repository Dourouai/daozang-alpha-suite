from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DaozangConfig, SegmentConfig

MULTI_HORIZON_SPECS: tuple[tuple[str, int, str, str], ...] = (
    ("1d", 1, "LABEL_1D", "Ref($close, -1) / $close - 1"),
    ("3d", 3, "LABEL_3D", "Ref($close, -3) / $close - 1"),
    ("5d", 5, "LABEL_5D", "Ref($close, -5) / $close - 1"),
)
PRIMARY_HORIZON = "3d"
PRIMARY_LABEL_NAME = "LABEL_3D"


@dataclass(frozen=True)
class BaselineOptions:
    market: str | None = None
    universe_file: str | Path | None = None
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
    feature_set: str | None = None        # Alpha158 | Alpha360
    objective: str | None = None          # regression | lambdarank
    multi_label: bool = False
    ensemble: str = ""                     # comma-separated: xgb,catboost
    sub_industry: bool = False             # train per-industry models
    extra_features: str | Path | None = None


@dataclass(frozen=True)
class BaselineArtifacts:
    report_path: Path
    scores_path: Path
    latest_scores_path: Path
    metrics: dict[str, Any]


@dataclass(frozen=True)
class PredictionBundle:
    test: pd.Series | pd.DataFrame
    infer: pd.Series | pd.DataFrame | None = None


def run_baseline(config: DaozangConfig, options: BaselineOptions) -> BaselineArtifacts:
    _prepare_runtime_dirs()

    import qlib
    from qlib.config import REG_CN
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
    market = "active_universe" if options.universe_file else options.market or config.qlib.market
    instruments = _resolve_instruments(
        D,
        market,
        segments,
        options.max_instruments,
        options.universe_file,
    )
    top_n = options.top_n or default_top_n(config, options, instruments)
    num_boost_round = options.num_boost_round or config.model.num_boost_round
    early_stopping_rounds = options.early_stopping_rounds or config.model.early_stopping_rounds

    # Apply CLI overrides to config (P1: feature set, objective, ensemble)
    feature_set = options.feature_set or config.model.feature_set
    objective = options.objective or config.model.objective
    multi_label = options.multi_label or config.model.multi_label
    ensemble_models = (
        tuple(m.strip() for m in options.ensemble.split(",") if m.strip())
        if options.ensemble
        else config.model.ensemble
    )
    sub_industry = options.sub_industry or getattr(config.model, "sub_industry", False)

    # ---- Feature handler: Alpha158 or Alpha360 ----
    if feature_set == "Alpha360":
        from qlib.contrib.data.handler import Alpha360 as AlphaHandler
    else:
        from qlib.contrib.data.handler import Alpha158 as AlphaHandler

    if multi_label:
        labels = [spec[3] for spec in MULTI_HORIZON_SPECS]
        label_names = [spec[2] for spec in MULTI_HORIZON_SPECS]
    else:
        labels = [config.dataset.label_expression]
        label_names = ["LABEL0"]

    extra_features = _load_extra_feature_frame(options.extra_features)

    handler = AlphaHandler(
        instruments=instruments,
        start_time=segments["train"].start,
        end_time=segments["test"].end,
        fit_start_time=segments["train"].start,
        fit_end_time=segments["train"].end,
        freq=config.dataset.freq,
        label=(labels, label_names),
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

    # ---- Sub-industry modeling ----
    if sub_industry:
        predictions = _train_sub_industry_models(
            dataset, instruments, segments, config, options,
            num_boost_round, early_stopping_rounds, ensemble_models,
        )
    else:
        train_frame = _prepare_frame(dataset, "train", DataHandlerLP, extra_features)
        valid_frame = _prepare_frame(dataset, "valid", DataHandlerLP, extra_features)
        test_frame = _prepare_frame(dataset, "test", DataHandlerLP, extra_features)
        infer_frame = _prepare_infer_frame(dataset, "test", DataHandlerLP, extra_features)

        # ---- Model training ----
        predictions = _train_single_model(
            train_frame, valid_frame, test_frame, config,
            num_boost_round, early_stopping_rounds, ensemble_models,
            objective, multi_label,
            infer_frame=infer_frame,
        )

    test_frame = _prepare_frame(dataset, "test", DataHandlerLP)
    prediction_frame = _prediction_frame(predictions.test)
    label_frame = _label_frame(test_frame["y"])
    primary_score = _primary_score_series(prediction_frame)
    primary_label = _primary_label_series(label_frame)
    label = pd.Series(primary_label, index=primary_label.index, name="label")
    prediction = pd.Series(primary_score, index=primary_score.index, name="score")
    scored = pd.concat([prediction, label], axis=1).dropna()
    if scored.empty:
        raise RuntimeError("Baseline produced no scored rows on test segment.")

    metrics = _compute_metrics(scored)
    metrics_by_horizon = _compute_metrics_by_horizon(prediction_frame, label_frame)
    latest_prediction = predictions.infer if predictions.infer is not None else predictions.test
    latest_scores = _latest_scores(_prediction_frame(latest_prediction), top_n)
    artifacts = _write_artifacts(
        config=config,
        options=options,
        market=market,
        instruments=instruments,
        segments=segments,
        metrics=metrics,
        metrics_by_horizon=metrics_by_horizon,
        latest_scores=latest_scores,
        extra_feature_metadata=_extra_feature_metadata(extra_features, options.extra_features),
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
    universe_file: str | Path | None,
) -> list[str] | str:
    if universe_file:
        from .universe import read_universe_instruments

        instruments = read_universe_instruments(universe_file, limit=max_instruments)
        if not instruments:
            raise RuntimeError(f"universe file has no instruments: {universe_file}")
        return instruments
    if max_instruments is None:
        return market
    instruments = data_api.list_instruments(
        data_api.instruments(market),
        start_time=segments["test"].start,
        end_time=segments["test"].end,
        as_list=True,
    )
    return sorted(instruments)[:max_instruments]


def default_top_n(
    config: DaozangConfig,
    options: BaselineOptions,
    instruments: list[str] | str,
) -> int:
    if options.universe_file:
        if isinstance(instruments, list):
            return min(len(instruments), options.max_instruments or len(instruments))
        return options.max_instruments or 800
    return config.export.top_n


def _is_multi_horizon(options: BaselineOptions, config: DaozangConfig) -> bool:
    return bool(options.multi_label or config.model.multi_label)


def _prepare_frame(
    dataset: Any,
    segment: str,
    data_handler_module: Any,
    extra_features: pd.DataFrame | None = None,
) -> dict[str, Any]:
    frame = dataset.prepare(
        segment,
        col_set=["feature", "label"],
        data_key=data_handler_module.DK_L,
    )
    if frame.empty:
        raise RuntimeError(f"Qlib returned empty {segment} frame.")
    x = frame["feature"].replace([float("inf"), float("-inf")], pd.NA)
    x = _join_extra_features(x, extra_features)
    y = _label_frame(frame["label"])
    valid = y.notna().any(axis=1)
    return {
        "x": x.loc[valid],
        "y": y.loc[valid],
        "index": y.loc[valid].index,
        "rows": int(valid.sum()),
    }


def _prepare_infer_frame(
    dataset: Any,
    segment: str,
    data_handler_module: Any,
    extra_features: pd.DataFrame | None = None,
) -> dict[str, Any]:
    frame = dataset.prepare(
        segment,
        col_set=["feature"],
        data_key=data_handler_module.DK_I,
    )
    if frame.empty:
        raise RuntimeError(f"Qlib returned empty infer {segment} frame.")
    x = frame["feature"].replace([float("inf"), float("-inf")], pd.NA)
    x = _join_extra_features(x, extra_features)
    return {
        "x": x,
        "index": x.index,
        "rows": int(len(x)),
    }


def _label_frame(value: pd.Series | pd.DataFrame) -> pd.DataFrame:
    if isinstance(value, pd.Series):
        name = str(value.name or "LABEL0")
        return value.to_frame(name=name)
    frame = value.copy()
    frame.columns = [_flatten_column_name(column) for column in frame.columns]
    return frame


def _prediction_frame(value: pd.Series | pd.DataFrame) -> pd.DataFrame:
    if isinstance(value, pd.Series):
        name = str(value.name or "score")
        return value.to_frame(name=name)
    frame = value.copy()
    frame.columns = [_flatten_column_name(column) for column in frame.columns]
    return frame


def _flatten_column_name(column: Any) -> str:
    if isinstance(column, tuple):
        parts = [str(part) for part in column if str(part)]
        return parts[-1] if parts else ""
    return str(column)


def _load_extra_feature_frame(path: str | Path | None) -> pd.DataFrame | None:
    if not path:
        return None
    feature_path = Path(path)
    if not feature_path.exists():
        raise RuntimeError(f"extra feature file not found: {feature_path}")
    frame = pd.read_csv(feature_path)
    if frame.empty:
        return None
    date_column = _first_existing_column(frame, ("trade_date", "date", "datetime"))
    if date_column is None:
        raise RuntimeError(f"extra feature file missing trade_date/date column: {feature_path}")
    instrument_column = _first_existing_column(frame, ("instrument", "qlib_instrument"))
    if instrument_column is None:
        code_column = _first_existing_column(frame, ("code", "symbol"))
        if code_column is None:
            raise RuntimeError(f"extra feature file missing instrument/code column: {feature_path}")
        frame["instrument"] = frame[code_column].map(_instrument_from_code)
        instrument_column = "instrument"

    id_columns = {
        date_column,
        instrument_column,
        "trade_date",
        "date",
        "datetime",
        "instrument",
        "qlib_instrument",
        "code",
        "symbol",
        "name",
        "source_run_kinds",
        "last_seen_at",
    }
    numeric_columns = [column for column in frame.columns if column not in id_columns]
    if not numeric_columns:
        return None
    feature_values = frame[[date_column, instrument_column, *numeric_columns]].copy()
    for column in numeric_columns:
        feature_values[column] = pd.to_numeric(feature_values[column], errors="coerce")
    feature_values["datetime"] = pd.to_datetime(feature_values[date_column])
    feature_values["instrument"] = feature_values[instrument_column].astype(str)
    feature_values = feature_values.dropna(subset=["datetime", "instrument"])
    feature_values = feature_values.set_index(["datetime", "instrument"])
    feature_values = feature_values[numeric_columns].groupby(level=["datetime", "instrument"]).mean()
    feature_values.columns = [
        column if str(column).startswith("beichen_") else f"beichen_{column}"
        for column in feature_values.columns
    ]
    return feature_values


def _first_existing_column(frame: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in frame.columns:
            return name
    return None


def _instrument_from_code(value: Any) -> str:
    code = str(value).strip()
    if code.startswith(("SH", "SZ")):
        return code
    code = code.zfill(6)
    return f"SH{code}" if code.startswith(("5", "6", "9")) else f"SZ{code}"


def _join_extra_features(
    features: pd.DataFrame,
    extra_features: pd.DataFrame | None,
) -> pd.DataFrame:
    if extra_features is None or extra_features.empty:
        return features
    aligned = extra_features.reindex(features.index).fillna(0.0)
    if aligned.empty:
        return features
    return pd.concat([features, aligned], axis=1)


def _extra_feature_metadata(
    extra_features: pd.DataFrame | None,
    path: str | Path | None,
) -> dict[str, Any]:
    if extra_features is None:
        return {"enabled": False, "path": str(path or "")}
    return {
        "enabled": True,
        "path": str(path or ""),
        "rows": int(len(extra_features)),
        "columns": list(extra_features.columns),
        "date_count": int(extra_features.index.get_level_values("datetime").nunique()),
        "instrument_count": int(extra_features.index.get_level_values("instrument").nunique()),
    }


def _resolve_label_column(frame: pd.DataFrame, preferred: str) -> str | None:
    if preferred in frame.columns:
        return preferred
    for column in frame.columns:
        if str(column).upper().endswith(preferred.upper()):
            return str(column)
    return None


def _primary_score_series(predictions: pd.DataFrame) -> pd.Series:
    if "score_3d" in predictions.columns:
        return predictions["score_3d"].rename("score")
    if "score" in predictions.columns:
        return predictions["score"].rename("score")
    return predictions.iloc[:, 0].rename("score")


def _primary_label_series(labels: pd.DataFrame) -> pd.Series:
    label_column = _resolve_label_column(labels, PRIMARY_LABEL_NAME)
    if label_column is not None:
        return labels[label_column].rename("label")
    label_column = _resolve_label_column(labels, "LABEL0")
    if label_column is not None:
        return labels[label_column].rename("label")
    return labels.iloc[:, 0].rename("label")


def _compute_metrics_by_horizon(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for suffix, _days, label_name, _expression in MULTI_HORIZON_SPECS:
        score_column = f"score_{suffix}"
        label_column = _resolve_label_column(labels, label_name)
        if score_column not in predictions.columns or label_column is None:
            continue
        scored = pd.concat(
            [
                predictions[score_column].rename("score"),
                labels[label_column].rename("label"),
            ],
            axis=1,
        ).dropna()
        if scored.empty:
            continue
        result[suffix] = _compute_metrics(scored)
    return result


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
    primary_score_column = "score_3d" if "score_3d" in latest.columns else "score"
    if primary_score_column not in latest.columns:
        primary_score_column = str(latest.columns[0])
    latest["score"] = latest[primary_score_column]
    for suffix, _days, _label_name, _expression in MULTI_HORIZON_SPECS:
        score_column = f"score_{suffix}"
        if score_column in latest.columns:
            latest[f"pct_rank_{suffix}"] = latest[score_column].rank(pct=True)
    if "pct_rank_3d" in latest.columns:
        latest["pct_rank"] = latest["pct_rank_3d"]
    else:
        latest["pct_rank"] = latest["score"].rank(pct=True)
    latest = latest.sort_values(primary_score_column, ascending=False)
    latest["rank"] = range(1, len(latest) + 1)
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
    metrics_by_horizon: dict[str, dict[str, Any]],
    latest_scores: pd.DataFrame,
    extra_feature_metadata: dict[str, Any] | None = None,
) -> BaselineArtifacts:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = Path(config.export.path)
    reports_dir = Path(config.export.reports_path)
    export_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    export_scores = latest_scores.drop(columns=["label"], errors="ignore").copy()
    export_scores["model"] = config.model.name
    export_scores["feature_set"] = options.feature_set or config.model.feature_set
    export_scores["horizon_days"] = (
        3 if _is_multi_horizon(options, config) else config.dataset.label_horizon_days
    )
    export_scores["universe"] = market
    export_scores = export_scores[_ordered_export_columns(export_scores)]

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
        "feature_set": options.feature_set or config.model.feature_set,
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
        "label_horizons": [days for _suffix, days, _label, _expression in MULTI_HORIZON_SPECS]
        if _is_multi_horizon(options, config)
        else [config.dataset.label_horizon_days],
        "segments": {name: segment.__dict__ for name, segment in segments.items()},
        "quick": options.quick,
        "metrics": metrics,
        "metrics_by_horizon": metrics_by_horizon,
        "extra_features": extra_feature_metadata or {"enabled": False},
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


def _ordered_export_columns(frame: pd.DataFrame) -> list[str]:
    preferred = [
        "trade_date",
        "instrument",
        "score",
        "rank",
        "pct_rank",
        "model",
        "feature_set",
        "horizon_days",
        "universe",
        "score_1d",
        "score_3d",
        "score_5d",
        "pct_rank_1d",
        "pct_rank_3d",
        "pct_rank_5d",
        "expected_return_1d",
        "up_probability_1d",
        "expected_return_3d",
        "up_probability_3d",
        "expected_return_5d",
        "up_probability_5d",
    ]
    return [column for column in preferred if column in frame.columns] + [
        column for column in frame.columns if column not in preferred
    ]


def _safe_float(value: Any) -> float:
    if pd.isna(value):
        return 0.0
    return float(value)


def _build_return_calibrator(valid_score: pd.Series, valid_label: pd.Series, suffix: str = PRIMARY_HORIZON):
    frame = pd.concat(
        [
            valid_score.rename("score"),
            valid_label.rename("label"),
        ],
        axis=1,
    ).dropna()
    if frame.empty:
        return _constant_return_calibrator(0.0, 0.5, suffix=suffix)

    frame = frame.sort_values("score")
    global_up_probability = float((frame["label"] > 0).mean())
    global_expected_return = float(frame["label"].mean())
    if len(frame) < 20:
        return _constant_return_calibrator(global_expected_return, global_up_probability, suffix=suffix)

    bin_count = min(10, max(1, len(frame) // 20))
    ranked = pd.Series(range(len(frame)), index=frame.index)
    frame["bin"] = pd.qcut(ranked, q=bin_count, labels=False, duplicates="drop")
    bins = []
    for _bin_id, group in frame.groupby("bin", sort=True):
        up_count = int((group["label"] > 0).sum())
        sample_count = int(len(group))
        probability = (up_count + global_up_probability * 4) / (sample_count + 4)
        bins.append(
            {
                "min_score": float(group["score"].min()),
                "max_score": float(group["score"].max()),
                "expected_return": float(group["label"].mean()),
                "up_probability": max(0.01, min(0.99, float(probability))),
            }
        )
    if not bins:
        return _constant_return_calibrator(global_expected_return, global_up_probability, suffix=suffix)

    def calibrate(target_score: pd.Series) -> pd.DataFrame:
        expected_values = []
        probabilities = []
        for value in target_score:
            if pd.isna(value):
                expected_values.append(pd.NA)
                probabilities.append(pd.NA)
                continue
            selected = bins[0]
            numeric_value = float(value)
            for item in bins:
                selected = item
                if numeric_value <= item["max_score"]:
                    break
            expected_values.append(selected["expected_return"])
            probabilities.append(selected["up_probability"])
        return pd.DataFrame(
            {
                f"expected_return_{suffix}": expected_values,
                f"up_probability_{suffix}": probabilities,
            },
            index=target_score.index,
        )

    return calibrate


def _constant_return_calibrator(expected_return: float, up_probability: float, suffix: str = PRIMARY_HORIZON):
    def calibrate(target_score: pd.Series) -> pd.DataFrame:
        return pd.DataFrame(
            {
                f"expected_return_{suffix}": [expected_return for _ in range(len(target_score))],
                f"up_probability_{suffix}": [
                    max(0.01, min(0.99, up_probability)) for _ in range(len(target_score))
                ],
            },
            index=target_score.index,
        )

    return calibrate


# ---------------------------------------------------------------------------
# P1: Single model training (supports LambdaRank, XGBoost, CatBoost)
# ---------------------------------------------------------------------------

def _train_single_model(
    train_frame, valid_frame, test_frame, config,
    num_boost_round, early_stopping_rounds, ensemble_models,
    objective, multi_label,
    infer_frame=None,
) -> PredictionBundle:
    """Train one or more horizon models on the full dataset."""
    train_y = _label_frame(train_frame["y"])
    valid_y = _label_frame(valid_frame["y"])
    test_predictions: dict[str, pd.Series] = {}
    infer_predictions: dict[str, pd.Series] = {}
    valid_predictions: dict[str, pd.Series] = {}

    if multi_label:
        for suffix, _days, label_name, _expression in MULTI_HORIZON_SPECS:
            label_column = _resolve_label_column(train_y, label_name)
            if label_column is None:
                continue
            target = _train_target_model(
                train_x=train_frame["x"],
                train_y=train_y[label_column],
                valid_x=valid_frame["x"],
                valid_y=valid_y[label_column],
                test_x=test_frame["x"],
                test_index=test_frame["index"],
                infer_x=None if infer_frame is None else infer_frame["x"],
                infer_index=None if infer_frame is None else infer_frame["index"],
                config=config,
                num_boost_round=num_boost_round,
                early_stopping_rounds=early_stopping_rounds,
                ensemble_models=ensemble_models,
                objective=objective,
            )
            test_predictions[f"score_{suffix}"] = target["test"]
            valid_predictions[f"score_{suffix}"] = target["valid"]
            if "infer" in target:
                infer_predictions[f"score_{suffix}"] = target["infer"]

        if not test_predictions:
            raise RuntimeError("Multi-horizon baseline produced no predictions.")

        test_result = pd.DataFrame(test_predictions)
        infer_result = pd.DataFrame(infer_predictions) if infer_predictions else None
        for suffix, _days, label_name, _expression in MULTI_HORIZON_SPECS:
            score_column = f"score_{suffix}"
            if score_column not in valid_predictions or score_column not in test_result:
                continue
            valid_label_column = _resolve_label_column(valid_y, label_name)
            if valid_label_column is None:
                continue
            calibrator = _build_return_calibrator(
                valid_predictions[score_column],
                valid_y[valid_label_column],
                suffix=suffix,
            )
            test_calibration = calibrator(test_result[score_column])
            test_result = pd.concat([test_result, test_calibration], axis=1)
            if infer_result is not None and score_column in infer_result:
                infer_calibration = calibrator(infer_result[score_column])
                infer_result = pd.concat([infer_result, infer_calibration], axis=1)
        return PredictionBundle(test=test_result, infer=infer_result)

    label_column = _resolve_label_column(train_y, "LABEL0") or train_y.columns[0]
    target = _train_target_model(
        train_x=train_frame["x"],
        train_y=train_y[label_column],
        valid_x=valid_frame["x"],
        valid_y=valid_y[label_column],
        test_x=test_frame["x"],
        test_index=test_frame["index"],
        infer_x=None if infer_frame is None else infer_frame["x"],
        infer_index=None if infer_frame is None else infer_frame["index"],
        config=config,
        num_boost_round=num_boost_round,
        early_stopping_rounds=early_stopping_rounds,
        ensemble_models=ensemble_models,
        objective=objective,
    )
    result = target["test"].rename("score")
    infer_result = target.get("infer")
    if infer_result is not None:
        infer_result = infer_result.rename("score")
    return PredictionBundle(test=result, infer=infer_result)


def _train_target_model(
    *,
    train_x: pd.DataFrame,
    train_y: pd.Series,
    valid_x: pd.DataFrame,
    valid_y: pd.Series,
    test_x: pd.DataFrame,
    test_index: pd.Index,
    infer_x: pd.DataFrame | None,
    infer_index: pd.Index | None,
    config: DaozangConfig,
    num_boost_round: int,
    early_stopping_rounds: int,
    ensemble_models: tuple[str, ...],
    objective: str,
) -> dict[str, pd.Series]:
    import lightgbm as lgb

    train_valid = train_y.notna()
    valid_valid = valid_y.notna()
    if int(train_valid.sum()) == 0 or int(valid_valid.sum()) == 0:
        raise RuntimeError("Target model has no train/valid labels.")

    train_x = train_x.loc[train_valid]
    train_y = train_y.loc[train_valid]
    valid_x = valid_x.loc[valid_valid]
    valid_y = valid_y.loc[valid_valid]

    predictions = []
    valid_predictions = []
    infer_predictions = []
    lgb_model = lgb.train(
        _lightgbm_params(config, objective),
        lgb.Dataset(train_x, label=train_y),
        num_boost_round=num_boost_round,
        valid_sets=[lgb.Dataset(valid_x, label=valid_y)],
        valid_names=["valid"],
        callbacks=[
            lgb.early_stopping(early_stopping_rounds, verbose=False),
            lgb.log_evaluation(period=25),
        ],
    )
    predictions.append(pd.Series(lgb_model.predict(test_x), index=test_index, name="lgb"))
    valid_predictions.append(pd.Series(lgb_model.predict(valid_x), index=valid_y.index, name="lgb"))
    if infer_x is not None and infer_index is not None:
        infer_predictions.append(pd.Series(lgb_model.predict(infer_x), index=infer_index, name="lgb"))

    if "xgb" in ensemble_models:
        try:
            import xgboost as xgb

            xgb_model = xgb.train(
                {
                    "objective": "reg:squarederror",
                    "learning_rate": config.model.learning_rate,
                    "max_depth": min(config.model.max_depth, 6),
                    "verbosity": 0,
                    "seed": 42,
                    "nthread": max(os.cpu_count() or 1, 1),
                },
                xgb.DMatrix(train_x, label=train_y),
                num_boost_round=num_boost_round,
                evals=[(xgb.DMatrix(valid_x, label=valid_y), "valid")],
                early_stopping_rounds=early_stopping_rounds,
                verbose_eval=False,
            )
            predictions.append(
                pd.Series(xgb_model.predict(xgb.DMatrix(test_x)), index=test_index, name="xgb")
            )
            valid_predictions.append(
                pd.Series(xgb_model.predict(xgb.DMatrix(valid_x)), index=valid_y.index, name="xgb")
            )
            if infer_x is not None and infer_index is not None:
                infer_predictions.append(
                    pd.Series(
                        xgb_model.predict(xgb.DMatrix(infer_x)),
                        index=infer_index,
                        name="xgb",
                    )
                )
        except ImportError:
            pass

    if "catboost" in ensemble_models:
        try:
            from catboost import CatBoostRegressor

            cb_model = CatBoostRegressor(
                iterations=num_boost_round,
                learning_rate=config.model.learning_rate,
                depth=min(config.model.max_depth, 6),
                random_seed=42,
                thread_count=max(os.cpu_count() or 1, 1),
                verbose=False,
            )
            cb_model.fit(
                train_x,
                train_y,
                eval_set=(valid_x, valid_y),
                early_stopping_rounds=early_stopping_rounds,
                verbose=False,
            )
            predictions.append(pd.Series(cb_model.predict(test_x), index=test_index, name="cb"))
            valid_predictions.append(pd.Series(cb_model.predict(valid_x), index=valid_y.index, name="cb"))
            if infer_x is not None and infer_index is not None:
                infer_predictions.append(pd.Series(cb_model.predict(infer_x), index=infer_index, name="cb"))
        except ImportError:
            pass

    result = {
        "test": _mean_prediction(predictions),
        "valid": _mean_prediction(valid_predictions),
    }
    if infer_predictions:
        result["infer"] = _mean_prediction(infer_predictions)
    return result


def _lightgbm_params(config: DaozangConfig, objective: str) -> dict[str, Any]:
    if objective == "lambdarank":
        return {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [30],
            "learning_rate": config.model.learning_rate,
            "num_leaves": config.model.num_leaves,
            "max_depth": config.model.max_depth,
            "verbosity": -1,
            "seed": 42,
            "num_threads": max(os.cpu_count() or 1, 1),
        }
    return {
        "objective": "regression",
        "metric": "l2",
        "learning_rate": config.model.learning_rate,
        "num_leaves": config.model.num_leaves,
        "max_depth": config.model.max_depth,
        "verbosity": -1,
        "seed": 42,
        "num_threads": max(os.cpu_count() or 1, 1),
    }


def _mean_prediction(predictions: list[pd.Series]) -> pd.Series:
    if len(predictions) == 1:
        return predictions[0]
    result = pd.concat(predictions, axis=1).mean(axis=1)
    result.name = "score"
    return result


# ---------------------------------------------------------------------------
# P1: Sub-industry modeling
# ---------------------------------------------------------------------------

def _train_sub_industry_models(
    dataset, instruments, segments, config, options,
    num_boost_round, early_stopping_rounds, ensemble_models,
) -> PredictionBundle:
    """Train per-industry LightGBM models, merge predictions.

    Groups instruments by industry label, trains a model per group
    with sufficient samples. Small groups fall back to a global model.
    """
    from qlib.data.dataset.handler import DataHandlerLP

    # Try to load industry labels from active universe CSV
    industry_map = _load_industry_map(instruments)

    # Group instruments by industry
    groups: dict[str, list[str]] = {}
    for inst in instruments:
        ind = industry_map.get(inst, "其他")
        groups.setdefault(ind, []).append(inst)

    # Filter groups with minimum samples
    MIN_SAMPLES = 20
    valid_groups = {k: v for k, v in groups.items() if len(v) >= MIN_SAMPLES}
    small_instruments = [i for k, v in groups.items() if len(v) < MIN_SAMPLES for i in v]

    if len(valid_groups) <= 1:
        # Not enough industry diversity — fall back to single model
        train_frame = _prepare_frame(dataset, "train", DataHandlerLP)
        valid_frame = _prepare_frame(dataset, "valid", DataHandlerLP)
        test_frame = _prepare_frame(dataset, "test", DataHandlerLP)
        return _train_single_model(
            train_frame, valid_frame, test_frame, config,
            num_boost_round, early_stopping_rounds, ensemble_models,
            "regression", False,
        )

    # Train per-industry models
    all_predictions = []
    import lightgbm as lgb

    for industry, group_instruments in valid_groups.items():
        try:
            # Build dataset for this industry group
            handler_kwargs = {
                "instruments": group_instruments,
                "start_time": segments["train"].start,
                "end_time": segments["test"].end,
                "fit_start_time": segments["train"].start,
                "fit_end_time": segments["train"].end,
                "freq": config.dataset.freq,
                "label": ([config.dataset.label_expression], ["LABEL0"]),
                "infer_processors": [
                    _make_feature_inf_processor(),
                    {"class": "ZScoreNorm", "kwargs": {"fields_group": "feature"}},
                    {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
                ],
                "learn_processors": [{"class": "DropnaLabel"}],
            }
            # Use Alpha158 for sub-industry models (simpler, faster)
            from qlib.contrib.data.handler import Alpha158
            from qlib.data.dataset import DatasetH
            h = Alpha158(**handler_kwargs)
            d = DatasetH(handler=h, segments={n: (s.start, s.end) for n, s in segments.items()})

            t_train = _prepare_frame(d, "train", DataHandlerLP)
            t_valid = _prepare_frame(d, "valid", DataHandlerLP)
            t_test = _prepare_frame(d, "test", DataHandlerLP)

            if t_train["rows"] < 50:
                continue

            train_label = _primary_label_series(_label_frame(t_train["y"]))
            valid_label = _primary_label_series(_label_frame(t_valid["y"]))
            model = lgb.train(
                {"objective": "regression", "metric": "l2",
                 "learning_rate": config.model.learning_rate,
                 "num_leaves": min(config.model.num_leaves, 32),
                 "verbosity": -1, "seed": 42,
                 "num_threads": max(os.cpu_count() or 1, 1)},
                lgb.Dataset(t_train["x"], label=train_label),
                num_boost_round=min(num_boost_round, 100),
                valid_sets=[lgb.Dataset(t_valid["x"], label=valid_label)],
                valid_names=["valid"],
                callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
            )
            pred = pd.Series(model.predict(t_test["x"]), index=t_test["index"], name="score")
            all_predictions.append(pred)
        except Exception:
            pass

    # Global model for small groups
    if small_instruments:
        try:
            handler_kwargs["instruments"] = small_instruments
            h = Alpha158(**handler_kwargs)
            d = DatasetH(handler=h, segments={n: (s.start, s.end) for n, s in segments.items()})
            t_train = _prepare_frame(d, "train", DataHandlerLP)
            t_valid = _prepare_frame(d, "valid", DataHandlerLP)
            t_test = _prepare_frame(d, "test", DataHandlerLP)
            if t_train["rows"] >= 50:
                train_label = _primary_label_series(_label_frame(t_train["y"]))
                valid_label = _primary_label_series(_label_frame(t_valid["y"]))
                model = lgb.train(
                    {"objective": "regression", "metric": "l2",
                     "learning_rate": config.model.learning_rate,
                     "num_leaves": min(config.model.num_leaves, 32),
                     "verbosity": -1, "seed": 42,
                     "num_threads": max(os.cpu_count() or 1, 1)},
                    lgb.Dataset(t_train["x"], label=train_label),
                    num_boost_round=min(num_boost_round, 100),
                    valid_sets=[lgb.Dataset(t_valid["x"], label=valid_label)],
                    valid_names=["valid"],
                    callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
                )
                pred = pd.Series(model.predict(t_test["x"]), index=t_test["index"], name="score")
                all_predictions.append(pred)
        except Exception:
            pass

    if not all_predictions:
        # Fallback to single model
        train_frame = _prepare_frame(dataset, "train", DataHandlerLP)
        valid_frame = _prepare_frame(dataset, "valid", DataHandlerLP)
        test_frame = _prepare_frame(dataset, "test", DataHandlerLP)
        return _train_single_model(
            train_frame, valid_frame, test_frame, config,
            num_boost_round, early_stopping_rounds, ensemble_models,
            "regression", False,
        )

    result = pd.concat(all_predictions).sort_index()
    result.name = "score"
    return PredictionBundle(test=result)


def _load_industry_map(instruments: list[str]) -> dict[str, str]:
    """Load industry labels for instruments from active universe CSV."""
    try:
        import csv
        path = Path("data/universe/active_universe.csv")
        if not path.exists():
            return {}
        result = {}
        with path.open("r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row.get("code", "").strip()
                inst = row.get("instrument", "").strip()
                industry = row.get("industry", "").strip()
                if code and industry:
                    result[code] = industry
                if inst and industry:
                    result[inst] = industry
        return result
    except Exception:
        return {}
