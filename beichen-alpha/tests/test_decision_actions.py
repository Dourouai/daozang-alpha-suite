import unittest
from datetime import datetime

from beichen_alpha.decision_log import build_trade_plan_decision_records
from beichen_alpha.models import FactorScore, Recommendation, RealtimeCheck, SectorSignal, StockProfile
from beichen_alpha.strategy.final_action import BUY_NOW_SMALL, NO_TRADE, PULLBACK_WATCH, decide_recommendation_action
from beichen_alpha.strategy.sector_lifecycle_factor import score_sector_lifecycle
from beichen_alpha.strategy.trade_plan import build_three_day_trade_plan


def make_recommendation(**overrides):
    base = dict(
        code="000963",
        name="华东医药",
        score=120,
        status="条件执行",
        close=30.0,
        observation_zone="29.50-30.20",
        confirm_price=30.2,
        invalid_price=28.9,
        reason="通过: 趋势",
        risk="核心因子均通过",
        industry="医药",
        themes=("创新药",),
        candidate_score=120,
        take_profit_price=32.0,
        model_pct_rank=0.62,
    )
    base.update(overrides)
    return Recommendation(**base)


class FinalActionTest(unittest.TestCase):
    def test_pullback_reversal_gets_dedicated_action(self):
        item = make_recommendation(close=29.35, confirm_price=30.2, invalid_price=28.5)

        final = decide_recommendation_action(item)

        self.assertEqual(final.action, PULLBACK_WATCH)
        self.assertIn("低吸候选", final.reason)

    def test_realtime_confirmed_candidate_can_buy_small(self):
        item = make_recommendation(close=30.22, confirm_price=30.0, invalid_price=28.9)
        check = RealtimeCheck(
            code=item.code,
            status="实时可买",
            price=30.22,
            gap_to_confirm_pct=0.007,
            chase_limit_price=30.36,
            execution_score=55,
        )

        final = decide_recommendation_action(item, check)

        self.assertEqual(final.action, BUY_NOW_SMALL)
        self.assertIn("小仓", final.sizing_hint)

    def test_priced_in_candidate_is_no_trade(self):
        item = make_recommendation(
            candidate_breakdown="预期定价-30",
            risk="注意: 利好兑现，短线可能兑现",
        )

        final = decide_recommendation_action(item)

        self.assertEqual(final.action, NO_TRADE)
        self.assertIn("提前反映", final.reason)


class SectorLifecycleFactorTest(unittest.TestCase):
    def test_sector_startup_scores_positive(self):
        profile = StockProfile(code="000963", name="华东医药", industry="医药", themes=("创新药",))
        signals = {"医药": SectorSignal("医药", 10, return_3d=0.03, return_5d=0.05, amount_ratio=1.18)}

        score = score_sector_lifecycle(profile, signals)[0]

        self.assertEqual(score.name, "板块启动")
        self.assertGreater(score.score, 0)

    def test_sector_climax_scores_negative(self):
        profile = StockProfile(code="000963", name="华东医药", industry="医药", themes=("创新药",))
        signals = {"医药": SectorSignal("医药", 25, return_3d=0.07, return_5d=0.12, amount_ratio=1.55)}

        score = score_sector_lifecycle(profile, signals)[0]

        self.assertEqual(score.name, "板块高潮")
        self.assertLess(score.score, 0)

    def test_sector_retreat_scores_negative(self):
        profile = StockProfile(code="000963", name="华东医药", industry="医药", themes=("创新药",))
        signals = {"医药": SectorSignal("医药", -15, return_3d=-0.04, return_5d=-0.03, amount_ratio=0.72)}

        score = score_sector_lifecycle(profile, signals)[0]

        self.assertEqual(score.name, "板块退潮")
        self.assertLess(score.score, 0)


class TradePlanActionTest(unittest.TestCase):
    def test_trade_plan_filters_no_trade_candidates_and_logs_final_action(self):
        priced_in = make_recommendation(
            code="688001",
            name="创新药样本",
            close=21.0,
            confirm_price=21.3,
            invalid_price=20.2,
            candidate_score=180,
            candidate_breakdown="预期定价-30",
            risk="注意: 利好兑现，短线可能兑现",
        )
        pullback = make_recommendation(
            code="300059",
            name="东方财富",
            industry="非银金融",
            themes=("非银金融",),
            close=20.8,
            confirm_price=21.4,
            invalid_price=20.2,
            candidate_score=130,
            factor_scores=(FactorScore("政策关键词", 8, True, "政策支持资本市场"),),
        )

        plan = build_three_day_trade_plan([priced_in, pullback], [], capital=10000, top_n=2)

        self.assertEqual([item.code for item in plan.buy_plans], ["300059"])
        self.assertEqual(plan.buy_plans[0].final_action, PULLBACK_WATCH)

        records = build_trade_plan_decision_records(
            plan,
            as_of=datetime(2026, 7, 7, 9, 30),
            context={"command": "trade_plan"},
            logged_at=datetime(2026, 7, 7, 9, 35),
        )
        buy = next(item for item in records if item["decision_kind"] == "trade_plan_buy")

        self.assertEqual(buy["final_action"]["action"], PULLBACK_WATCH)
        self.assertIn("reason", buy["final_action"])
        self.assertEqual(buy["factor_scores"][0]["name"], "政策关键词")
        self.assertEqual(buy["factor_scores"][0]["group"], "政策因子")


if __name__ == "__main__":
    unittest.main()
