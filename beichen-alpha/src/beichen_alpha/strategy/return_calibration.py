from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median

from beichen_alpha.models import Bar

from .levels import calc_confirm_price, calc_invalid_price, calc_take_profit_price


@dataclass(frozen=True)
class PositionShape:
    confirm_buffer: float
    reward_risk: float
    pnl_pct: float
    state: str


@dataclass(frozen=True)
class HistoricalSample:
    shape: PositionShape
    future_return: float
    target_hit: bool
    stop_hit: bool
    distance: float


@dataclass(frozen=True)
class ReturnCalibration:
    horizon_days: int
    sample_count: int
    up_probability: float
    avg_return: float
    median_return: float
    target_hit_probability: float
    stop_hit_probability: float
    confidence: str
    scope: str
    median_distance: float


def position_shape(
    price: float,
    cost: float,
    confirm: float,
    invalid: float,
    target: float,
) -> PositionShape | None:
    if price <= 0 or cost <= 0 or confirm <= 0 or invalid <= 0 or target <= 0:
        return None
    risk = max(price - invalid, 0.01)
    reward = max(target - price, 0.0)
    confirm_span = max(confirm - invalid, 0.01)
    if price < invalid:
        state = "below_stop"
    elif price < confirm:
        state = "below_confirm"
    elif price >= target:
        state = "target_zone"
    else:
        state = "active_hold"
    return PositionShape(
        confirm_buffer=(price - confirm) / confirm_span,
        reward_risk=reward / risk,
        pnl_pct=price / cost - 1,
        state=state,
    )


def calibrate_position_return(
    bars: list[Bar],
    price: float,
    cost: float,
    confirm: float,
    invalid: float,
    target: float,
    horizon_days: int = 5,
    min_samples: int = 40,
    max_samples: int = 180,
) -> ReturnCalibration | None:
    current_shape = position_shape(price, cost, confirm, invalid, target)
    if current_shape is None or len(bars) < horizon_days + 20:
        return None

    samples = build_historical_samples(bars, current_shape, horizon_days=horizon_days)
    if not samples:
        return None

    nearest = sorted(samples, key=lambda item: item.distance)[:max_samples]
    close_matches = [item for item in nearest if item.distance <= 3.0]
    selected = close_matches if len(close_matches) >= min_samples else nearest[:min_samples]
    if not selected:
        return None

    returns = [item.future_return for item in selected]
    up_count = sum(1 for item in selected if item.future_return > 0)
    target_hits = sum(1 for item in selected if item.target_hit)
    stop_hits = sum(1 for item in selected if item.stop_hit)
    count = len(selected)
    median_distance = median(item.distance for item in selected)
    confidence = confidence_label(count, median_distance)
    return ReturnCalibration(
        horizon_days=horizon_days,
        sample_count=count,
        up_probability=smoothed_probability(up_count, count),
        avg_return=mean(returns),
        median_return=median(returns),
        target_hit_probability=smoothed_probability(target_hits, count),
        stop_hit_probability=smoothed_probability(stop_hits, count),
        confidence=confidence,
        scope="同股历史相似样本",
        median_distance=median_distance,
    )


def build_historical_samples(
    bars: list[Bar],
    target_shape: PositionShape,
    horizon_days: int = 5,
) -> list[HistoricalSample]:
    samples: list[HistoricalSample] = []
    start_index = 12
    end_index = len(bars) - horizon_days
    for index in range(start_index, end_index):
        window = bars[:index]
        latest = bars[index]
        previous = bars[index - 1]
        confirm = calc_confirm_price(window)
        invalid = calc_invalid_price(window)
        target = calc_take_profit_price(
            window,
            confirm,
            invalid,
            horizon="short_3_5d" if horizon_days >= 5 else "ultra_short_2_3d",
        )
        shape = position_shape(
            latest.close,
            previous.close,
            confirm,
            invalid,
            target,
        )
        if shape is None or shape.state != target_shape.state:
            continue
        future_bars = bars[index + 1 : index + horizon_days + 1]
        if len(future_bars) < horizon_days:
            continue
        future_return = future_bars[-1].close / latest.close - 1
        samples.append(
            HistoricalSample(
                shape=shape,
                future_return=future_return,
                target_hit=any(bar.high >= target for bar in future_bars),
                stop_hit=any(bar.low <= invalid for bar in future_bars),
                distance=shape_distance(target_shape, shape),
            )
        )
    return samples


def shape_distance(left: PositionShape, right: PositionShape) -> float:
    reward_left = min(max(left.reward_risk, 0.0), 5.0)
    reward_right = min(max(right.reward_risk, 0.0), 5.0)
    return (
        abs(left.confirm_buffer - right.confirm_buffer) / 0.12
        + abs(reward_left - reward_right) / 0.35
        + abs(left.pnl_pct - right.pnl_pct) / 0.025
    )


def smoothed_probability(success_count: int, sample_count: int) -> float:
    if sample_count <= 0:
        return 0.0
    return (success_count + 1) / (sample_count + 2)


def confidence_label(sample_count: int, median_distance: float) -> str:
    if sample_count >= 120 and median_distance <= 2.0:
        return "高"
    if sample_count >= 60 and median_distance <= 3.0:
        return "中"
    return "低"


def format_return_calibration(calibration: ReturnCalibration | None) -> str:
    if calibration is None:
        return "历史校准：样本不足，暂不展示上涨概率。"
    return (
        f"历史校准：未来{calibration.horizon_days}日上涨 {calibration.up_probability:.0%}，"
        f"平均收益 {calibration.avg_return:+.2%}，"
        f"目标触达 {calibration.target_hit_probability:.0%}，"
        f"止损触碰 {calibration.stop_hit_probability:.0%} | "
        f"样本 {calibration.sample_count}（{calibration.confidence}置信，{calibration.scope}）"
    )
