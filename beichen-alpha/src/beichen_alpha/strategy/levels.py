from __future__ import annotations

from beichen_alpha.models import Bar

from .indicators import moving_average


def calc_observation_zone(bars: list[Bar]) -> tuple[float, float]:
    closes = [bar.close for bar in bars]
    latest = bars[-1]
    ma5 = moving_average(closes, 5)
    low = max(ma5, latest.low)
    high = min(latest.close, ma5 * 1.03)
    if high < low:
        low, high = high, low
    return round(low, 2), round(high, 2)


def calc_confirm_price(bars: list[Bar]) -> float:
    recent = bars[-2:] if len(bars) >= 2 else bars
    return round(max(bar.high for bar in recent), 2)


def calc_invalid_price(bars: list[Bar]) -> float:
    closes = [bar.close for bar in bars]
    ma10 = moving_average(closes, 10)
    recent_low = min(bar.low for bar in bars[-3:])
    return round(min(ma10, recent_low), 2)


def calc_take_profit_price(
    bars: list[Bar],
    confirm_price: float,
    invalid_price: float,
    horizon: str = "ultra_short_2_3d",
) -> float:
    latest = bars[-1]
    entry_price = max(confirm_price, latest.close)
    if entry_price <= 0:
        return 0.0

    risk_pct = max((entry_price - invalid_price) / entry_price, 0.015)
    if horizon == "ultra_short_2_3d":
        target_pct = min(max(risk_pct * 1.2, 0.018), 0.04)
    elif horizon == "short_3_5d":
        target_pct = min(max(risk_pct * 1.6, 0.035), 0.075)
    else:
        target_pct = min(max(risk_pct * 2.0, 0.05), 0.12)
    return round(entry_price * (1 + target_pct), 2)


def calc_trailing_stop_price(
    bars: list[Bar],
    invalid_price: float,
    horizon: str = "ultra_short_2_3d",
) -> float:
    closes = [bar.close for bar in bars]
    latest = bars[-1]
    ma5 = moving_average(closes, 5)
    if horizon == "ultra_short_2_3d":
        buffer = 0.99
    elif horizon == "short_3_5d":
        buffer = 0.98
    else:
        buffer = 0.965
    trailing_stop = max(invalid_price, ma5 * buffer)
    if trailing_stop >= latest.close:
        trailing_stop = invalid_price
    return round(trailing_stop, 2)
