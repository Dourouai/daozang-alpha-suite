import unittest
from datetime import datetime

from beichen_alpha.models import Bar, MacroEvent, NewsEvent, StockProfile
from beichen_alpha.strategy.engine import build_recommendation
from beichen_alpha.strategy.expectation_factor import score_expectation_pricing


def make_bars(code: str, name: str, closes: list[float], amounts: list[float] | None = None) -> list[Bar]:
    amounts = amounts or [100_000_000 for _ in closes]
    rows = []
    for index, close in enumerate(closes, 1):
        rows.append(
            Bar(
                code=code,
                name=name,
                date=f"2026-07-{index:02d}",
                open=close * 0.995,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                volume=1_000_000,
                amount=amounts[index - 1],
            )
        )
    return rows


class ExpectationFactorTest(unittest.TestCase):
    def test_speculative_good_news_after_runup_is_priced_in(self):
        bars = make_bars(
            "688001",
            "创新药样本",
            [10.0, 10.2, 10.5, 10.8, 11.0, 11.1],
            [100_000_000, 105_000_000, 98_000_000, 102_000_000, 110_000_000, 220_000_000],
        )
        benchmark = make_bars("000300", "沪深300", [100, 100.1, 100, 100.1, 100, 100.1])
        event = NewsEvent(
            code="688001",
            title="创新药有望获批，市场预期升温",
            source="manual",
            published_at=datetime(2026, 7, 6, 9),
            event_type="positive",
            polarity=1,
            importance=1.0,
            confidence=1.0,
        )

        score = score_expectation_pricing(
            bars,
            benchmark,
            news_events=[event],
            as_of=datetime(2026, 7, 6, 10),
        )[0]

        self.assertEqual(score.name, "预期透支")
        self.assertLess(score.score, 0)
        self.assertFalse(score.passed)

    def test_landed_good_news_after_runup_is_sell_the_news_risk(self):
        bars = make_bars(
            "688001",
            "创新药样本",
            [10.0, 10.2, 10.5, 10.8, 11.0, 11.1],
            [100_000_000, 105_000_000, 98_000_000, 102_000_000, 110_000_000, 220_000_000],
        )
        benchmark = make_bars("000300", "沪深300", [100, 100.1, 100, 100.1, 100, 100.1])
        event = NewsEvent(
            code="688001",
            title="创新药获批上市",
            source="manual",
            published_at=datetime(2026, 7, 6, 9),
            event_type="positive",
            polarity=1,
            importance=1.0,
            confidence=1.0,
        )

        score = score_expectation_pricing(
            bars,
            benchmark,
            news_events=[event],
            as_of=datetime(2026, 7, 6, 10),
        )[0]

        self.assertEqual(score.name, "利好兑现")
        self.assertLessEqual(score.score, -30)
        self.assertFalse(score.passed)

    def test_positive_expectation_without_runup_is_setup(self):
        bars = make_bars("600000", "医药样本", [10.0, 10.01, 10.02, 10.03, 10.05, 10.08])
        benchmark = make_bars("000300", "沪深300", [100, 100.1, 100.2, 100.1, 100.2, 100.3])
        profile = StockProfile(code="600000", name="医药样本", industry="医药", themes=("创新药",), market_cap_billion=500)
        event = MacroEvent(
            event_date=datetime(2026, 7, 8, 9),
            title="创新药审评政策或将优化",
            source="manual",
            positive_sectors=("医药", "创新药"),
            base_score=8,
            confidence=1.0,
        )

        score = score_expectation_pricing(
            bars,
            benchmark,
            profile=profile,
            macro_events=[event],
            as_of=datetime(2026, 7, 6, 10),
        )[0]

        self.assertEqual(score.name, "预期潜伏")
        self.assertGreater(score.score, 0)
        self.assertTrue(score.passed)

    def test_priced_in_factor_reaches_recommendation_breakdown(self):
        bars = make_bars(
            "688001",
            "创新药样本",
            [10.0, 10.2, 10.5, 10.8, 11.0, 11.1],
            [100_000_000, 105_000_000, 98_000_000, 102_000_000, 110_000_000, 220_000_000],
        )
        benchmark = make_bars("000300", "沪深300", [100, 100.1, 100, 100.1, 100, 100.1])
        event = NewsEvent(
            code="688001",
            title="创新药获批上市",
            source="manual",
            published_at=datetime(2026, 7, 6, 9),
            event_type="positive",
            polarity=1,
            importance=1.0,
            confidence=1.0,
        )

        row = build_recommendation(
            "688001",
            bars,
            benchmark,
            news_events=[event],
            as_of=datetime(2026, 7, 6, 10),
        )

        self.assertIn("预期定价-", row.candidate_breakdown)
        self.assertIn("利好兑现", row.risk)


if __name__ == "__main__":
    unittest.main()
