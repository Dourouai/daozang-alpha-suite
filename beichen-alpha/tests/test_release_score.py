import unittest
from datetime import datetime

from beichen_alpha.decision_log import build_trade_plan_decision_records
from beichen_alpha.models import Recommendation
from beichen_alpha.reports.trade_plan import render_three_day_trade_plan
from beichen_alpha.strategy.trade_plan import build_three_day_trade_plan


def make_position() -> dict:
    return {
        "code": "600025",
        "name": "华能水电",
        "shares": 100,
        "entry_date": "2026-07-03",
        "cost": 9.24,
        "confirm": 9.17,
        "invalid": 8.79,
        "target": 9.78,
    }


def make_recommendation() -> Recommendation:
    return Recommendation(
        code="600025",
        name="华能水电",
        score=98,
        status="条件执行",
        close=9.19,
        observation_zone="9.10-9.25",
        confirm_price=9.17,
        invalid_price=8.79,
        reason="测试",
        risk="-",
        candidate_score=98,
        take_profit_price=9.78,
    )


class ReleaseScoreTest(unittest.TestCase):
    def test_holding_plan_renders_and_logs_release_score(self):
        plan = build_three_day_trade_plan(
            [make_recommendation()],
            [make_position()],
            capital=10000,
            top_n=0,
            review_date=datetime(2026, 7, 6),
            trading_dates=["2026-07-03", "2026-07-06"],
        )
        holding = plan.holding_plans[0]
        report = render_three_day_trade_plan(plan)
        records = build_trade_plan_decision_records(
            plan,
            as_of=datetime(2026, 7, 6, 10, 0),
            context={"command": "trade_plan"},
        )
        holding_record = next(item for item in records if item["decision_kind"] == "holding_review")

        self.assertGreaterEqual(holding.release_score, 40)
        self.assertIn("释放分", report)
        self.assertEqual(holding_record["scores"]["release_score"], holding.release_score)
        self.assertEqual(holding_record["rationale"]["release_reason"], holding.release_reason)


if __name__ == "__main__":
    unittest.main()
