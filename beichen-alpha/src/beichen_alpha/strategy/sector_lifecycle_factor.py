from __future__ import annotations

from beichen_alpha.models import FactorScore, SectorSignal, StockProfile

from .market_factor import match_sector_signal


def score_sector_lifecycle(
    profile: StockProfile | None,
    sector_signals: dict[str, SectorSignal] | None,
) -> list[FactorScore]:
    signal = match_sector_signal(profile, sector_signals or {})
    if profile is None:
        return [FactorScore("板块生命周期", 0, True, "缺少股票画像，板块生命周期按中性处理")]
    if signal is None:
        return [FactorScore("板块生命周期", 0, True, "暂无板块生命周期数据")]

    ret_3d = signal.return_3d
    ret_5d = signal.return_5d
    amount_ratio = signal.amount_ratio
    if ret_3d is None or ret_5d is None:
        return [FactorScore("板块生命周期", 0, True, f"{signal.name} 缺少涨幅数据")]

    hot_volume = amount_ratio is not None and amount_ratio >= 1.35
    cooling_volume = amount_ratio is not None and amount_ratio < 0.90

    if ret_5d >= 0.09 and hot_volume:
        return [
            FactorScore(
                "板块高潮",
                -18,
                False,
                f"{signal.name} 5日{ret_5d:+.2%}且量能{amount_ratio:.2f}x，警惕高潮/兑现",
            )
        ]
    if ret_3d < -0.025 and ret_5d < 0 and cooling_volume:
        return [
            FactorScore(
                "板块退潮",
                -22,
                False,
                f"{signal.name} 3日{ret_3d:+.2%}、5日{ret_5d:+.2%}且量能转弱",
            )
        ]
    if 0.012 <= ret_3d <= 0.055 and ret_5d <= 0.08:
        score = 12 if amount_ratio is None or amount_ratio >= 1.05 else 7
        volume_text = "-" if amount_ratio is None else f"{amount_ratio:.2f}x"
        return [
            FactorScore(
                "板块启动",
                score,
                True,
                f"{signal.name} 3日{ret_3d:+.2%}、5日{ret_5d:+.2%}，量能{volume_text}",
            )
        ]
    if ret_3d > 0 and ret_5d > 0:
        return [
            FactorScore(
                "板块发酵",
                5,
                True,
                f"{signal.name} 已有正收益但未进入高潮区，继续观察确认",
            )
        ]
    return [FactorScore("板块生命周期", 0, True, f"{signal.name} 生命周期中性")]
