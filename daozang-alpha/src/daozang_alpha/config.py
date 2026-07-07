from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_PROVIDER_URI = "~/.qlib/qlib_data/cn_data"
ENV_PROVIDER_URI = "DAOZANG_QLIB_PROVIDER_URI"


@dataclass(frozen=True)
class QlibConfig:
    provider_uri: str = DEFAULT_PROVIDER_URI
    region: str = "cn"
    market: str = "csi300"
    benchmark: str = "SH000300"

    @property
    def provider_path(self) -> Path:
        return Path(self.provider_uri).expanduser()


@dataclass(frozen=True)
class DatasetConfig:
    freq: str = "day"
    label_horizon_days: int = 5
    label_expression: str = "Ref($close, -5) / $close - 1"


@dataclass(frozen=True)
class SegmentConfig:
    start: str
    end: str


@dataclass(frozen=True)
class SegmentsConfig:
    train: SegmentConfig
    valid: SegmentConfig
    test: SegmentConfig


@dataclass(frozen=True)
class ModelConfig:
    name: str = "lightgbm"
    feature_set: str = "Alpha158"       # Alpha158 | Alpha360
    objective: str = "regression"       # regression | lambdarank
    num_boost_round: int = 200
    early_stopping_rounds: int = 20
    learning_rate: float = 0.05
    num_leaves: int = 64
    max_depth: int = 8
    multi_label: bool = True           # Train 1d/3d/5d labels simultaneously
    ensemble: tuple[str, ...] = ()      # e.g. ("xgb", "catboost") for multi-model


@dataclass(frozen=True)
class ExportConfig:
    top_n: int = 50
    path: str = "data/exports"
    reports_path: str = "reports"


@dataclass(frozen=True)
class DaozangConfig:
    qlib: QlibConfig
    dataset: DatasetConfig
    segments: SegmentsConfig = field(
        default_factory=lambda: SegmentsConfig(
            train=SegmentConfig("2016-01-01", "2022-12-31"),
            valid=SegmentConfig("2023-01-01", "2023-12-31"),
            test=SegmentConfig("2024-01-01", "2026-06-30"),
        )
    )
    model: ModelConfig = field(default_factory=ModelConfig)
    export: ExportConfig = field(default_factory=ExportConfig)


def default_config_path(base_dir: Path | None = None) -> Path:
    root = Path.cwd() if base_dir is None else base_dir
    return root / "config" / "daozang.example.toml"


def load_config(path: str | Path | None = None) -> DaozangConfig:
    config_path = Path(path).expanduser() if path else default_config_path()
    raw: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)

    qlib_raw = raw.get("qlib", {})
    dataset_raw = raw.get("dataset", {})
    segments_raw = raw.get("segments", {})
    model_raw = raw.get("model", {})
    export_raw = raw.get("export", {})

    provider_uri = os.environ.get(
        ENV_PROVIDER_URI,
        str(qlib_raw.get("provider_uri", DEFAULT_PROVIDER_URI)),
    )

    qlib_config = QlibConfig(
        provider_uri=provider_uri,
        region=str(qlib_raw.get("region", "cn")),
        market=str(qlib_raw.get("market", "csi300")),
        benchmark=str(qlib_raw.get("benchmark", "SH000300")),
    )
    dataset_config = DatasetConfig(
        freq=str(dataset_raw.get("freq", "day")),
        label_horizon_days=int(dataset_raw.get("label_horizon_days", 5)),
        label_expression=str(
            dataset_raw.get("label_expression", "Ref($close, -5) / $close - 1")
        ),
    )
    segments_config = SegmentsConfig(
        train=_load_segment(segments_raw, "train", "2016-01-01", "2022-12-31"),
        valid=_load_segment(segments_raw, "valid", "2023-01-01", "2023-12-31"),
        test=_load_segment(segments_raw, "test", "2024-01-01", "2026-06-30"),
    )
    model_config = ModelConfig(
        name=str(model_raw.get("name", "lightgbm")),
        feature_set=str(model_raw.get("feature_set", "Alpha158")),
        objective=str(model_raw.get("objective", "regression")),
        num_boost_round=int(model_raw.get("num_boost_round", 200)),
        early_stopping_rounds=int(model_raw.get("early_stopping_rounds", 20)),
        learning_rate=float(model_raw.get("learning_rate", 0.05)),
        num_leaves=int(model_raw.get("num_leaves", 64)),
        max_depth=int(model_raw.get("max_depth", 8)),
        multi_label=_load_bool(model_raw.get("multi_label", True)),
        ensemble=_load_string_tuple(model_raw.get("ensemble", ())),
    )
    export_config = ExportConfig(
        top_n=int(export_raw.get("top_n", 50)),
        path=str(export_raw.get("path", "data/exports")),
        reports_path=str(export_raw.get("reports_path", "reports")),
    )
    return DaozangConfig(
        qlib=qlib_config,
        dataset=dataset_config,
        segments=segments_config,
        model=model_config,
        export=export_config,
    )


def _load_segment(
    segments_raw: dict[str, Any],
    name: str,
    default_start: str,
    default_end: str,
) -> SegmentConfig:
    raw = segments_raw.get(name, {})
    return SegmentConfig(
        start=str(raw.get("start", default_start)),
        end=str(raw.get("end", default_end)),
    )


def _load_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _load_string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_items = value.split(",")
    else:
        raw_items = value
    return tuple(str(item).strip() for item in raw_items if str(item).strip())
