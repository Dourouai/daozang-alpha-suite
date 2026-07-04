from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import DaozangConfig


@dataclass(frozen=True)
class SmokeTestOptions:
    instrument: str = "SH600000"
    start_time: str = "2024-01-02"
    end_time: str = "2024-01-10"


def run_qlib_smoke_test(config: DaozangConfig, options: SmokeTestOptions) -> str:
    try:
        import qlib
        from qlib.config import REG_CN
        from qlib.data import D
    except ImportError as exc:
        raise RuntimeError('pyqlib is not installed; run: python -m pip install -e ".[research]"') from exc

    region: Any = REG_CN if config.qlib.region == "cn" else config.qlib.region
    qlib.init(provider_uri=str(config.qlib.provider_path), region=region)
    frame = D.features(
        [options.instrument],
        ["$close", "$volume"],
        start_time=options.start_time,
        end_time=options.end_time,
        freq=config.dataset.freq,
    )
    if frame.empty:
        raise RuntimeError(
            f"Qlib returned no rows for {options.instrument} "
            f"from {options.start_time} to {options.end_time}"
        )
    return frame.tail().to_string()
