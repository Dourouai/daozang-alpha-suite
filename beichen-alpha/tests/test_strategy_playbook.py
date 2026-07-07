from beichen_alpha.models import Recommendation
from beichen_alpha.strategy.playbook import (
    classify_buy_plan_strategy,
    classify_recommendation_strategy,
)
from beichen_alpha.strategy.trade_plan import BuyPlan


def make_recommendation(**overrides):
    base = dict(
        code="600036",
        name="招商银行",
        score=120,
        status="条件执行",
        close=37.0,
        observation_zone="36.50-37.20",
        confirm_price=36.9,
        invalid_price=35.8,
        reason="通过: 趋势",
        risk="核心因子均通过",
        industry="银行",
        candidate_score=120,
        macro_event_score=0,
        model_pct_rank=0.55,
    )
    base.update(overrides)
    return Recommendation(**base)


def test_recommendation_strategy_marks_defensive_rotation():
    item = make_recommendation(industry="银行", name="工商银行")
    profile = classify_recommendation_strategy(item)
    assert profile["id"] == "defensive_rotation"
    assert profile["name"] == "防守轮动"


def test_recommendation_strategy_blocks_chase():
    item = make_recommendation(close=38.0, confirm_price=36.9)
    profile = classify_recommendation_strategy(item)
    assert profile["id"] == "momentum_chase_risk"


def test_buy_plan_strategy_can_mark_pullback_reversal():
    item = BuyPlan(
        code="300059",
        name="东方财富",
        status="观察",
        group="非银金融",
        close=20.8,
        confirm=21.4,
        stop=20.2,
        target=22.2,
        candidate_score=130,
        lot_cost=2080,
        max_lots=1,
        model_pct_rank=0.62,
        trigger="等待承接",
        risk="跌破20.2风控",
    )
    profile = classify_buy_plan_strategy(item)
    assert profile["id"] == "pullback_reversal"
