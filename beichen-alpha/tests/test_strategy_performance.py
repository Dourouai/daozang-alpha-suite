import unittest

from beichen_alpha.strategy_performance import (
    render_strategy_performance_report,
    resolve_final_action,
    resolve_strategy_profile,
    summarize_strategy_performance,
)


def make_record(
    *,
    code: str,
    status: str,
    final_action: str | None,
    strategy_id: str | None,
    strategy_name: str | None,
    breakdown: str,
    returns: tuple[float, float, float],
    drawdowns: tuple[float, float, float],
    target_hits: tuple[bool, bool, bool] = (False, False, False),
    stop_hits: tuple[bool, bool, bool] = (False, False, False),
) -> dict:
    horizons = (1, 3, 5)
    outcome = {}
    for index, horizon in enumerate(horizons):
        outcome[f"return_{horizon}d"] = returns[index]
        outcome[f"max_drawdown_{horizon}d"] = drawdowns[index]
        outcome[f"target_hit_{horizon}d"] = target_hits[index]
        outcome[f"stop_hit_{horizon}d"] = stop_hits[index]
    record = {
        "code": code,
        "name": code,
        "status": status,
        "rationale": {"candidate_breakdown": breakdown},
        "outcome": outcome,
    }
    if final_action is not None:
        record["final_action"] = {"action": final_action, "confidence": "中"}
    if strategy_id is not None:
        record["strategy_profile"] = {"id": strategy_id, "name": strategy_name or strategy_id}
    return record


class StrategyPerformanceTest(unittest.TestCase):
    def test_summarizes_final_action_strategy_and_factor_groups(self):
        records = [
            make_record(
                code="600001",
                status="条件执行",
                final_action="BUY_WATCH",
                strategy_id="breakout_watch",
                strategy_name="突破观察",
                breakdown="模型分+14 个股强弱+40 资金博弈+8",
                returns=(0.02, 0.035, 0.04),
                drawdowns=(-0.006, -0.012, -0.018),
                target_hits=(False, True, True),
            ),
            make_record(
                code="300001",
                status="观察",
                final_action="PULLBACK_WATCH",
                strategy_id="pullback_reversal",
                strategy_name="低吸反转",
                breakdown="板块生命周期+12 预期定价+8 个股强弱+18",
                returns=(-0.01, -0.03, 0.005),
                drawdowns=(-0.015, -0.05, -0.052),
                stop_hits=(False, True, True),
            ),
        ]

        summary = summarize_strategy_performance(records, horizons=(1, 3, 5))

        action_rows = {row["label"]: row for row in summary["dimensions"]["final_action"]}
        self.assertEqual(action_rows["BUY_WATCH"]["horizons"]["1"]["samples"], 1)
        self.assertEqual(action_rows["BUY_WATCH"]["horizons"]["1"]["win_rate"], 1.0)
        self.assertEqual(action_rows["PULLBACK_WATCH"]["horizons"]["3"]["stop_hit_rate"], 1.0)

        strategy_rows = {row["label"]: row for row in summary["dimensions"]["strategy_profile"]}
        self.assertIn("突破确认", strategy_rows)
        self.assertIn("低吸反转", strategy_rows)

        factor_rows = {row["label"]: row for row in summary["dimensions"]["factor_group"]}
        self.assertEqual(factor_rows["个股强弱"]["horizons"]["1"]["samples"], 2)
        self.assertEqual(factor_rows["模型分"]["horizons"]["3"]["target_hit_rate"], 1.0)

    def test_falls_back_for_legacy_priced_in_records(self):
        record = make_record(
            code="688001",
            status="条件执行",
            final_action=None,
            strategy_id=None,
            strategy_name=None,
            breakdown="预期定价-30 预期透支-26 个股强弱+20",
            returns=(-0.02, -0.04, -0.05),
            drawdowns=(-0.03, -0.06, -0.08),
        )

        self.assertEqual(resolve_final_action(record), "NO_TRADE")
        self.assertEqual(resolve_strategy_profile(record), ("expectation_priced_in", "预期透支"))

    def test_render_report_names_required_sections(self):
        records = [
            make_record(
                code="600001",
                status="条件执行",
                final_action="BUY_WATCH",
                strategy_id="breakout_watch",
                strategy_name="突破观察",
                breakdown="模型分+14 个股强弱+40",
                returns=(0.02, 0.03, 0.01),
                drawdowns=(-0.01, -0.02, -0.02),
            )
        ]

        report = render_strategy_performance_report(summarize_strategy_performance(records))

        self.assertIn("策略复盘归因报告", report)
        self.assertIn("按 final_action", report)
        self.assertIn("BUY_WATCH", report)
        self.assertIn("按因子组", report)
        self.assertIn("模型分", report)


if __name__ == "__main__":
    unittest.main()
