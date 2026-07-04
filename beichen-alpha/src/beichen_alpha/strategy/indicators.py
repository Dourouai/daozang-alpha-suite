from __future__ import annotations

from statistics import mean


def moving_average(values: list[float], window: int) -> float:
    if len(values) < window:
        return mean(values)
    return mean(values[-window:])


def pct_return(values: list[float], window: int) -> float:
    if len(values) <= window:
        return 0.0
    start = values[-window - 1]
    end = values[-1]
    if start == 0:
        return 0.0
    return end / start - 1
