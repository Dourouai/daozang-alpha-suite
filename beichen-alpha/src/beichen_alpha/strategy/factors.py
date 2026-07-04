from __future__ import annotations

from beichen_alpha.models import Bar, FactorScore, StrategyPolicy

from .indicators import moving_average, pct_return
from .levels import calc_invalid_price


def score_bars(
    bars: list[Bar],
    benchmark_bars: list[Bar],
    policy: StrategyPolicy | None = None,
) -> list[FactorScore]:
    if len(bars) < 6:
        return [FactorScore("样本", 0, False, "历史数据少于 6 天")]

    active_policy = policy or StrategyPolicy()
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    amounts = [bar.amount for bar in bars]
    benchmark_closes = [bar.close for bar in benchmark_bars]

    latest = bars[-1]
    ma5 = moving_average(closes, 5)
    ma10 = moving_average(closes, 10)
    volume_ma5 = moving_average([float(value) for value in volumes], 5)
    amount_ma5 = moving_average(amounts, 5)
    stock_return_3d = pct_return(closes, 3)
    benchmark_return_3d = pct_return(benchmark_closes, 3)
    stock_return_5d = pct_return(closes, 5)
    benchmark_return_5d = pct_return(benchmark_closes, 5)
    invalid_price = calc_invalid_price(bars)
    risk_distance = latest.close / invalid_price - 1 if invalid_price > 0 else 9.99
    distance_to_ma5 = latest.close / ma5 - 1 if ma5 > 0 else 9.99

    scores = [
        FactorScore(
            "流动性",
            15 if amount_ma5 >= 100_000_000 else 5,
            amount_ma5 >= 100_000_000,
            f"5日均成交额 {amount_ma5 / 100_000_000:.2f} 亿",
        ),
        FactorScore(
            "趋势",
            20 if latest.close > ma5 > ma10 else 8,
            latest.close > ma5 > ma10,
            f"收盘 {latest.close:.2f}, MA5 {ma5:.2f}, MA10 {ma10:.2f}",
        ),
        FactorScore(
            "相对强弱",
            20 if stock_return_5d > benchmark_return_5d else 6,
            stock_return_5d > benchmark_return_5d,
            f"个股5日 {stock_return_5d:.2%}, 基准5日 {benchmark_return_5d:.2%}",
        ),
        FactorScore(
            "回踩承接",
            20 if -0.02 <= distance_to_ma5 <= 0.03 else 8,
            -0.02 <= distance_to_ma5 <= 0.03,
            f"距MA5 {distance_to_ma5:.2%}",
        ),
        FactorScore(
            "量能",
            15 if latest.volume >= volume_ma5 else 6,
            latest.volume >= volume_ma5,
            f"量比5日均量 {latest.volume / volume_ma5:.2f}",
        ),
        FactorScore(
            "风险距离",
            10 if risk_distance <= 0.05 else 3,
            risk_distance <= 0.05,
            f"距失效线 {risk_distance:.2%}",
        ),
    ]

    if active_policy.horizon in {"ultra_short_2_3d", "short_3_5d"}:
        short_momentum_passed = stock_return_3d > benchmark_return_3d and stock_return_3d > 0
        if active_policy.horizon == "ultra_short_2_3d":
            not_overheated = stock_return_5d <= 0.08 and distance_to_ma5 <= 0.04
            short_risk_passed = risk_distance <= 0.035
            odds_name = "2-3日赔率"
        else:
            not_overheated = stock_return_5d <= 0.12 and distance_to_ma5 <= 0.06
            short_risk_passed = risk_distance <= 0.045
            odds_name = "3-5日赔率"
        scores.extend(
            [
                FactorScore(
                    "短线动量",
                    16 if short_momentum_passed else 4,
                    short_momentum_passed,
                    f"个股3日 {stock_return_3d:.2%}, 基准3日 {benchmark_return_3d:.2%}",
                ),
                FactorScore(
                    "短线过热",
                    10 if not_overheated else -18,
                    not_overheated,
                    f"5日涨跌 {stock_return_5d:.2%}, 距MA5 {distance_to_ma5:.2%}",
                ),
                FactorScore(
                    odds_name,
                    12 if short_risk_passed else (-5 if risk_distance > 0.08 else 3),
                    short_risk_passed,
                    f"买点到失效线距离 {risk_distance:.2%}",
                ),
            ]
        )

    return scores
