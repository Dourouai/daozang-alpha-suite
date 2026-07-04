from __future__ import annotations

import importlib.metadata
import importlib.util
from dataclasses import dataclass
from pathlib import Path

from .config import DaozangConfig


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str

    @property
    def mark(self) -> str:
        return "OK" if self.ok else "WARN"


def check_environment(config: DaozangConfig) -> list[CheckResult]:
    results = [
        _check_qlib_package(),
        _check_region(config.qlib.region),
        _check_provider_path(config.qlib.provider_path),
        _check_provider_shape(config.qlib.provider_path),
        _check_dataset_label(config.dataset.label_horizon_days),
    ]
    return results


def format_results(results: list[CheckResult]) -> str:
    width = max(len(item.name) for item in results) if results else 0
    lines = []
    for item in results:
        lines.append(f"[{item.mark}] {item.name:<{width}}  {item.detail}")
    return "\n".join(lines)


def _check_qlib_package() -> CheckResult:
    if importlib.util.find_spec("qlib") is None:
        return CheckResult(
            name="pyqlib",
            ok=False,
            detail='not installed; run: python -m pip install -e ".[research]"',
        )
    try:
        version = importlib.metadata.version("pyqlib")
    except importlib.metadata.PackageNotFoundError:
        version = "installed"
    return CheckResult(name="pyqlib", ok=True, detail=str(version))


def _check_region(region: str) -> CheckResult:
    ok = region in {"cn", "us"}
    detail = region if ok else f"{region!r}; expected 'cn' or 'us'"
    return CheckResult(name="qlib region", ok=ok, detail=detail)


def _check_provider_path(path: Path) -> CheckResult:
    if path.exists():
        return CheckResult(name="provider path", ok=True, detail=str(path))
    return CheckResult(
        name="provider path",
        ok=False,
        detail=f"{path} does not exist; set DAOZANG_QLIB_PROVIDER_URI or prepare Qlib CN data",
    )


def _check_provider_shape(path: Path) -> CheckResult:
    expected = ["calendars", "features", "instruments"]
    if not path.exists():
        return CheckResult(name="provider shape", ok=False, detail="skipped because path is missing")
    missing = [name for name in expected if not (path / name).exists()]
    if missing:
        return CheckResult(name="provider shape", ok=False, detail=f"missing: {', '.join(missing)}")
    return CheckResult(name="provider shape", ok=True, detail="calendars/features/instruments found")


def _check_dataset_label(horizon_days: int) -> CheckResult:
    ok = horizon_days > 0
    detail = f"{horizon_days} trading days" if ok else "must be positive"
    return CheckResult(name="label horizon", ok=ok, detail=detail)
