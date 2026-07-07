import json
import struct
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from beichen_alpha.data import load_price_csv
from beichen_alpha.chat import ChatMessage, FeishuEventAdapter, handle_chat_message
from beichen_alpha.chat.feishu import parse_decrypted_feishu_json
from beichen_alpha.cli import summarize_positions_file
from beichen_alpha.content_sources.manual_text import ManualTextSource
from beichen_alpha.content_sources.wechat_article import parse_wechat_html
from beichen_alpha.decision_log import (
    append_decision_records,
    build_recommendation_decision_records,
    build_trade_plan_decision_records,
    read_decision_records,
)
from beichen_alpha.data_sources.universe_source import (
    infer_stock_profile,
    load_universe_cache,
    passes_profile_filter,
    save_universe_cache,
    select_spot_candidates,
)
from beichen_alpha.data_sources.profile_source import load_profile_csv
from beichen_alpha.data_sources.daozang_profile_source import load_daozang_profiles
from beichen_alpha.data_sources.market_regime_source import build_market_regime
from beichen_alpha.data_sources.market_structure_source import build_margin_summary, build_northbound_summary
from beichen_alpha.data_sources.macro_event_source import CsvMacroEventSource
from beichen_alpha.data_sources.macro_rss_source import MacroRssFeed, parse_rss_events
from beichen_alpha.data_sources.policy_page_source import PolicyPage, parse_policy_page_events, parse_policy_page_items
from beichen_alpha.data_sources.pboc_macro_source import (
    build_credit_growth_events,
    build_lpr_events,
    build_money_supply_events,
    build_reserve_requirement_events,
    build_social_financing_events,
    parse_open_market_detail,
)
from beichen_alpha.data_sources.stats_macro_source import (
    build_consensus_surprise_events,
    build_indicator_acceleration_events,
    build_pmi_events,
)
from beichen_alpha.data_sources.realtime_quote_source import parse_tencent_quote
from beichen_alpha.data_sources.market_data_router import MarketDataRouter
from beichen_alpha.data_sources.qlib_bin_source import QlibBinPriceSource, normalize_qlib_symbol
from beichen_alpha.data_sources.baostock_source import (
    baostock_adjustflag,
    baostock_date,
    baostock_symbol,
    normalize_baostock_rows,
)
from beichen_alpha.data_sources.global_linkage_source import (
    build_global_linkage_snapshot,
    parse_fred_csv,
    resolve_fred_series,
    resolve_yahoo_tickers,
)
from beichen_alpha.data_sources.global_feature_source import (
    GlobalFeatureDataset,
    build_global_feature_rows,
    start_date_from_period,
    write_global_feature_dataset,
)
from beichen_alpha.data_sources.sector_rotation_source import build_sector_signals_from_price_map, score_sector_history
from beichen_alpha.data_sources.sector_rotation_source import normalize_sector_name
from beichen_alpha.distill import distill_article, opinion_signal_to_dict
from beichen_alpha.events import classify_disclosure, classify_news
from beichen_alpha.models import ArticleContent, Bar, FactorScore, GlobalIndicator, MacroEvent, MarketStructureSnapshot, NewsEvent, OpinionSignal, RealtimeQuote, Recommendation, RiskCalendarEvent, SectorSignal, StockProfile, StrategyPolicy
from beichen_alpha.data_health import DataHealthReport, SourceCheck, format_data_health_card
from beichen_alpha.news_sources.opinion_signal_news import OpinionSignalNewsSource, signal_to_news_event
from beichen_alpha.notifiers import render_feishu_recommendations_card
from beichen_alpha.reports import render_global_linkage_report, render_three_day_trade_plan
from beichen_alpha.pool_refresh import build_pool_diff, format_watchlist, read_watchlist_entries
from beichen_alpha.profile_tags import (
    profile_all_tags,
    profile_concept_tags,
    profile_industry_candidates,
    profile_primary_industry,
    profile_secondary_industries,
    profile_style_tags,
)
from beichen_alpha.risk_sources.risk_calendar import (
    disclosure_events_to_risk_calendar,
    score_pledge_risk,
    score_release_risk,
)
from beichen_alpha.risk_sources.static_risk_calendar import load_static_risk_calendar
from beichen_alpha.strategy.news_factor import score_news_events
from beichen_alpha.strategy.realtime import build_realtime_check
from beichen_alpha.strategy.return_calibration import calibrate_position_return
from beichen_alpha.strategy.disclosure_factor import score_disclosure_events
from beichen_alpha.strategy.levels import calc_confirm_price as calc_level_confirm_price
from beichen_alpha.strategy.levels import calc_invalid_price as calc_level_invalid_price
from beichen_alpha.strategy.levels import calc_take_profit_price as calc_level_take_profit_price
from beichen_alpha.strategy.macro_event_factor import score_macro_events
from beichen_alpha.strategy.market_factor import score_chain_rotation, score_market_regime, score_sector_rotation
from beichen_alpha.strategy.market_structure_factor import score_market_structure
from beichen_alpha.strategy.policy import score_policy
from beichen_alpha.strategy.risk_calendar_factor import score_risk_calendar_events
from beichen_alpha.strategy.factors import score_bars
from beichen_alpha.strategy.trade_plan import (
    ModelScoreCoverage,
    build_three_day_trade_plan,
    infer_trade_group,
    inspect_model_score_coverage,
    load_model_scores,
)
from beichen_alpha.data_sources.akshare_source import normalize_symbol
from beichen_alpha.engine import rank_recommendations
from beichen_alpha.strategy.engine import build_candidate_score, build_recommendation
from beichen_alpha.factors import calc_invalid_price, calc_observation_zone


CSV_LINES = [
    "code,name,date,open,high,low,close,volume,amount",
    "000300,沪深300,2026-06-24,4945,4970,4910,4935,112000000,218000000000",
    "000300,沪深300,2026-06-25,4930,4960,4895,4920,118000000,230000000000",
    "000300,沪深300,2026-06-26,4925,4950,4880,4905,119000000,235000000000",
    "000300,沪深300,2026-06-29,4900,4920,4855,4870,121000000,240000000000",
    "000300,沪深300,2026-06-30,4875,4890,4820,4840,125000000,245000000000",
    "000300,沪深300,2026-07-01,4845,4860,4795,4828,127000000,248000000000",
    "000300,沪深300,2026-07-02,4865.17,4896.99,4800.48,4812.30,343243758,1027796834233",
    "600160,巨化股份,2026-06-24,50.90,52.20,50.20,51.70,720000,3800000000",
    "600160,巨化股份,2026-06-25,51.80,53.00,51.20,52.60,800000,4250000000",
    "600160,巨化股份,2026-06-26,52.80,54.10,52.30,53.80,850000,4600000000",
    "600160,巨化股份,2026-06-29,54.20,55.90,53.50,55.10,900000,5000000000",
    "600160,巨化股份,2026-06-30,55.40,56.80,54.90,56.20,960000,5400000000",
    "600160,巨化股份,2026-07-01,56.30,57.30,54.90,55.85,980000,5500000000",
    "600160,巨化股份,2026-07-02,53.70,58.00,51.07,54.83,1269606,7027193021",
    "300498,温氏股份,2026-06-24,12.35,12.55,12.25,12.48,610000,760000000",
    "300498,温氏股份,2026-06-25,12.50,12.70,12.40,12.62,640000,810000000",
    "300498,温氏股份,2026-06-26,12.63,12.85,12.50,12.78,680000,870000000",
    "300498,温氏股份,2026-06-29,12.80,13.00,12.67,12.95,720000,940000000",
    "300498,温氏股份,2026-06-30,12.98,13.18,12.82,13.05,760000,1000000000",
    "300498,温氏股份,2026-07-01,13.14,13.28,13.00,13.13,790000,1040000000",
    "300498,温氏股份,2026-07-02,13.14,13.34,12.90,13.18,850564,1118501404",
]


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.tmpdir.name) / "prices.csv"
        self.csv_path.write_text("\n".join(CSV_LINES), encoding="utf-8")
        self.price_map = load_price_csv(self.csv_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_recommendations_rank(self):
        rows = rank_recommendations(self.price_map, "000300")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].code, "300498")
        self.assertGreaterEqual(rows[0].score, 80)
        self.assertEqual(rows[0].candidate_score, rows[0].score)
        self.assertIn("个股强弱", rows[0].candidate_breakdown)

    def test_zone_is_ordered(self):
        bars = self.price_map["600160"]
        low, high = calc_observation_zone(bars)
        self.assertLessEqual(low, high)

    def test_invalid_price_below_latest_close(self):
        bars = self.price_map["300498"]
        invalid = calc_invalid_price(bars)
        self.assertLess(invalid, bars[-1].close)

    def test_normalize_symbol(self):
        self.assertEqual(normalize_symbol("sh600160"), "600160")
        self.assertEqual(normalize_symbol("300498.SZ"), "300498")

    def test_short_horizon_outputs_exit_plan(self):
        rows = rank_recommendations(
            self.price_map,
            "000300",
            policy=StrategyPolicy(horizon="short_3_5d"),
        )
        row = rows[0]
        self.assertEqual(row.holding_period, "3-5交易日")
        self.assertIsNotNone(row.take_profit_price)
        self.assertGreater(row.take_profit_price, row.confirm_price)
        self.assertIn("第3个交易日", row.sell_plan)

    def test_near_confirm_is_conditional_execution(self):
        benchmark = make_bars("000300", "沪深300", [100, 101, 102, 103, 104, 105, 106])
        bars = [
            Bar("600000", "测试银行", "2026-07-01", 9.8, 9.9, 9.7, 9.85, 1000000, 100000000),
            Bar("600000", "测试银行", "2026-07-02", 9.9, 10.06, 9.95, 10.0, 1200000, 120000000),
            Bar("600000", "测试银行", "2026-07-03", 10.0, 10.07, 9.98, 10.03, 1300000, 130000000),
            Bar("600000", "测试银行", "2026-07-04", 10.02, 10.08, 10.0, 10.04, 1400000, 140000000),
            Bar("600000", "测试银行", "2026-07-05", 10.03, 10.09, 10.01, 10.05, 1500000, 150000000),
            Bar("600000", "测试银行", "2026-07-06", 10.04, 10.10, 10.02, 10.06, 1600000, 160000000),
        ]
        row = build_recommendation("600000", bars, benchmark)
        self.assertEqual(row.status, "条件执行")
        self.assertIn("次日", row.sell_plan)

    def test_low_short_elasticity_is_penalized(self):
        benchmark = make_bars("000300", "沪深300", [100, 101, 102, 103, 104, 105, 106])
        bars = [
            Bar("600000", "测试银行", "2026-07-01", 10.00, 10.03, 9.98, 10.00, 1000000, 100000000),
            Bar("600000", "测试银行", "2026-07-02", 10.00, 10.03, 9.98, 10.01, 1000000, 100000000),
            Bar("600000", "测试银行", "2026-07-03", 10.01, 10.04, 9.99, 10.01, 1000000, 100000000),
            Bar("600000", "测试银行", "2026-07-04", 10.01, 10.04, 9.99, 10.02, 1000000, 100000000),
            Bar("600000", "测试银行", "2026-07-05", 10.02, 10.05, 10.00, 10.02, 1000000, 100000000),
            Bar("600000", "测试银行", "2026-07-06", 10.02, 10.05, 10.00, 10.03, 1000000, 100000000),
        ]

        scores = score_bars(bars, benchmark, StrategyPolicy(horizon="ultra_short_2_3d"))
        elasticity = next(item for item in scores if item.name == "短线弹性")

        self.assertFalse(elasticity.passed)
        self.assertLess(elasticity.score, 0)
        self.assertIn("近3日振幅", elasticity.detail)

    def test_chain_rotation_contributes_to_candidate_score(self):
        benchmark = make_bars("000300", "沪深300", [100, 101, 102, 103, 104, 105, 106])
        bars = make_bars("600160", "巨化股份", [50, 50.2, 50.6, 51.0, 51.3, 51.5, 51.8])
        profile = StockProfile(code="600160", name="巨化股份", industry="氟化工", themes=("新材料", "先进制造"), market_cap_billion=500)
        signals = {
            "AI硬件": SectorSignal(name="AI硬件", score=24, return_3d=0.05, return_5d=0.08, amount_ratio=1.3),
            "半导体": SectorSignal(name="半导体", score=18, return_3d=0.03, return_5d=0.05, amount_ratio=1.2),
            "材料": SectorSignal(name="材料", score=10, return_3d=0.012, return_5d=0.02, amount_ratio=1.25),
        }
        row = build_recommendation("600160", bars, benchmark, profile=profile, sector_signals=signals)
        self.assertIn("行业共振", row.candidate_breakdown)
        self.assertGreater(row.candidate_score, 0)

    def test_daozang_model_score_contributes_to_candidate_score(self):
        row = build_recommendation(
            "600160",
            self.price_map["600160"],
            self.price_map["000300"],
            model_pct_rank=0.96,
        )

        self.assertEqual(row.model_pct_rank, 0.96)
        self.assertIn("模型分", row.candidate_breakdown)
        self.assertIn("道藏模型", row.reason)

    def test_low_daozang_model_score_penalizes_without_excluding(self):
        row = build_recommendation(
            "600160",
            self.price_map["600160"],
            self.price_map["000300"],
            model_pct_rank=0.12,
        )

        self.assertIn("模型分-", row.candidate_breakdown)
        self.assertIn("道藏模型", row.risk)
        self.assertNotEqual(row.status, "排除")

    def test_candidate_score_caps_factor_groups(self):
        score, breakdown = build_candidate_score(
            [
                FactorScore("龙虎榜", 40, True, "机构净买"),
                FactorScore("北向资金", 36, True, "连续买入"),
                FactorScore("主力资金", 30, True, "主力流入"),
            ]
        )

        self.assertEqual(score, 45)
        self.assertIn("资金博弈+45(原+106)", breakdown)
        self.assertIn("资金博弈封顶", breakdown)

    def test_etf_money_factor_counts_as_flow_group(self):
        score, breakdown = build_candidate_score([FactorScore("ETF资金", 12, True, "ETF流入")])

        self.assertEqual(score, 12)
        self.assertIn("资金博弈+12", breakdown)
        self.assertNotIn("板块热度+12", breakdown)

    def test_low_model_score_caps_total_score(self):
        score, breakdown = build_candidate_score(
            [
                FactorScore("道藏模型", -14, False, "模型分位低"),
                FactorScore("龙虎榜", 40, True, "机构净买"),
                FactorScore("北向资金", 36, True, "连续买入"),
                FactorScore("行业轮动", 48, True, "板块强"),
                FactorScore("趋势", 55, True, "趋势强"),
                FactorScore("流动性", 35, True, "流动性好"),
            ]
        )

        self.assertEqual(score, 100)
        self.assertIn("模型低分限顶100", breakdown)

    def test_data_health_card_returns_card_body_only(self):
        report = DataHealthReport(
            as_of=datetime(2026, 7, 6, 9, 30),
            overall_status="degraded",
            overall_score=1,
            checks=[SourceCheck("道藏模型分数", "stale", "日期 2026-07-03", 0)],
            notes=["模型分数需要刷新"],
        )

        card = format_data_health_card(report)

        self.assertNotIn("msg_type", card)
        self.assertNotIn("card", card)
        self.assertIn("header", card)
        self.assertIn("elements", card)
        self.assertIn("道藏模型分数", json.dumps(card, ensure_ascii=False))


class PoolRefreshTest(unittest.TestCase):
    def test_pool_diff_preserves_new_order(self):
        diff = build_pool_diff(["600900", "600036", "600025"], ["600900", "601166", "600036"])
        self.assertEqual(diff.added, ["601166"])
        self.assertEqual(diff.removed, ["600025"])
        self.assertEqual(diff.kept, ["600900", "600036"])

    def test_format_watchlist_can_be_read_back(self):
        recommendation = Recommendation(
            code="600900",
            name="长江电力",
            score=207,
            status="条件执行",
            close=26.95,
            observation_zone="26.71-26.95",
            confirm_price=27.09,
            invalid_price=26.36,
            reason="测试",
            risk="-",
            industry="公用事业",
            market_cap_billion=6619,
            sector_rotation="公用事业+10",
            risk_calendar="-",
            candidate_score=207,
        )
        content = format_watchlist(
            [recommendation],
            created_at=datetime(2026, 7, 3, 15, 30),
            pool_size=50,
            scan_limit=120,
            min_market_cap_billion=300,
            exclude_themes="消费,品牌消费",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pool.txt"
            path.write_text(content, encoding="utf-8")
            entries = read_watchlist_entries(path)
        self.assertIn("600900", entries)
        self.assertIn("长江电力", entries["600900"])
        self.assertIn("candidate 207", entries["600900"])


class NewsFactorTest(unittest.TestCase):
    def test_positive_news_scores_positive(self):
        event = classify_news(
            code="600160",
            title="公司中标重大订单，行业需求改善",
            source="test",
            published_at=datetime(2026, 7, 2),
        )
        score = score_news_events([event], as_of=datetime(2026, 7, 2))[0]
        self.assertGreater(score.score, 0)
        self.assertTrue(score.passed)

    def test_hard_negative_news_excludes(self):
        event = classify_news(
            code="600160",
            title="公司被立案调查并收到监管处罚",
            source="test",
            published_at=datetime(2026, 7, 2),
        )
        score = score_news_events([event], as_of=datetime(2026, 7, 2))[0]
        self.assertEqual(score.name, "新闻风险")
        self.assertLess(score.score, 0)
        self.assertFalse(score.passed)

    def test_opinion_sector_bias_maps_to_industry(self):
        signal = OpinionSignal(
            source_name="测试博主",
            source_author="测试博主",
            title="资源股偏谨慎",
            url="",
            signal_date=datetime(2026, 7, 3),
            ingested_at=datetime(2026, 7, 3),
            published_at=datetime(2026, 7, 3),
            rule_version="test",
            summary="贵金属、煤炭石化等资源方向存在外围冲击后的回调压力。",
            stance="资源股偏谨慎；非银金融偏积极观察",
            confidence=0.8,
            themes=("资源", "贵金属"),
            symbols=(),
            risk_flags=("资源股外围回调风险",),
            key_points=("资源方向偏谨慎。",),
            matched_rules=(),
        )
        profile = StockProfile(code="601088", name="中国神华", industry="煤炭", themes=("资源",))
        event = signal_to_news_event(signal, "601088", profile)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "opinion_sector_risk")
        self.assertLess(event.polarity, 0)

    def test_opinion_coal_petchem_theme_maps_to_resource_profiles(self):
        signal = OpinionSignal(
            source_name="测试博主",
            source_author="测试博主",
            title="煤炭石化偏谨慎",
            url="",
            signal_date=datetime(2026, 7, 3),
            ingested_at=datetime(2026, 7, 3),
            published_at=datetime(2026, 7, 3),
            rule_version="test",
            summary="煤炭石化等资源方向存在外围冲击后的回调压力。",
            stance="资源股偏谨慎",
            confidence=0.8,
            themes=("煤炭石化",),
            symbols=(),
            risk_flags=("资源股外围回调风险",),
            key_points=("煤炭石化方向偏谨慎。",),
            matched_rules=(),
        )
        profile = StockProfile(code="601857", name="中国石油", industry="石油石化", themes=("资源",))
        event = signal_to_news_event(signal, "601857", profile)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "opinion_sector_risk")

    def test_opinion_factor_decays_by_freshness(self):
        as_of = datetime(2026, 7, 3, 10, 0)
        fresh = NewsEvent(
            code="600030",
            title="新近观点：非银金融偏积极",
            source="个人观点源:测试",
            published_at=as_of - timedelta(hours=6),
            event_type="opinion_sector_positive",
            polarity=1,
            importance=0.55,
            confidence=0.8,
        )
        stale = NewsEvent(
            code="600030",
            title="过期观点：非银金融偏积极",
            source="个人观点源:测试",
            published_at=as_of - timedelta(days=6),
            event_type="opinion_sector_positive",
            polarity=1,
            importance=0.55,
            confidence=0.8,
        )

        fresh_score = score_news_events([fresh], as_of=as_of)[0]
        stale_score = score_news_events([stale], as_of=as_of)[0]

        self.assertGreater(fresh_score.score, 0)
        self.assertEqual(stale_score.score, 0)
        self.assertTrue(stale_score.passed)


class MarketDataSourceTest(unittest.TestCase):
    def test_market_regime_scores_warm_breadth(self):
        index_bars = {
            "000300": make_bars("000300", "沪深300", [100, 101, 102, 103, 104, 106, 108]),
            "399006": make_bars("399006", "创业板指", [100, 99, 100, 101, 102, 104, 105]),
        }
        spot_rows = [
            {"latest": 10.0, "pct_change": 1.0, "turnover": 200_000_000}
            for _ in range(70)
        ] + [
            {"latest": 10.0, "pct_change": -1.0, "turnover": 100_000_000}
            for _ in range(30)
        ] + [
            {"latest": 10.0, "pct_change": 10.0, "turnover": 100_000_000}
            for _ in range(10)
        ]
        regime = build_market_regime(index_bars, spot_rows)
        self.assertIn(regime.temperature, {"偏暖", "热"})
        score = score_market_regime(regime)[0]
        self.assertGreater(score.score, 0)
        self.assertTrue(score.passed)

    def test_market_structure_scores_funding_inflow(self):
        snapshot = MarketStructureSnapshot(
            as_of=datetime(2026, 7, 3),
            breadth=0.7,
            limit_up_count=90,
            limit_down_count=10,
            turnover_100m=11000,
            margin_balance_100m=18000,
            margin_balance_change_pct=0.02,
            margin_buy_100m=1200,
            margin_buy_turnover_ratio=0.11,
            northbound_net_buy_100m=55,
            northbound_5d_net_buy_100m=140,
            detail="测试交易结构",
        )
        score = score_market_structure(snapshot)[0]

        self.assertEqual(score.name, "交易结构")
        self.assertGreater(score.score, 0)
        self.assertTrue(score.passed)
        self.assertIn("北向明显净买", score.detail)

    def test_market_structure_penalizes_weak_breadth_and_outflow(self):
        snapshot = MarketStructureSnapshot(
            as_of=datetime(2026, 7, 3),
            breadth=0.3,
            limit_up_count=8,
            limit_down_count=60,
            turnover_100m=8000,
            margin_balance_change_pct=-0.02,
            northbound_net_buy_100m=-60,
            northbound_5d_net_buy_100m=-160,
            detail="测试交易结构",
        )
        score = score_market_structure(snapshot)[0]

        self.assertLess(score.score, 0)
        self.assertFalse(score.passed)

    def test_market_structure_source_combines_margin_and_northbound(self):
        margin = build_margin_summary(
            [
                {"日期": "2026-07-02", "融资融券余额": 10000, "融资买入额": 500},
                {"日期": "2026-07-03", "融资融券余额": 10200, "融资买入额": 650},
            ],
            [
                {"日期": "2026-07-02", "融资融券余额": 8000, "融资买入额": 300},
                {"日期": "2026-07-03", "融资融券余额": 8100, "融资买入额": 420},
            ],
        )
        northbound = build_northbound_summary(
            [
                {"日期": "2026-06-29", "当日成交净买额": 10},
                {"日期": "2026-06-30", "当日成交净买额": 20},
                {"日期": "2026-07-01", "当日成交净买额": -5},
                {"日期": "2026-07-02", "当日成交净买额": 30},
                {"日期": "2026-07-03", "当日成交净买额": 40},
            ],
            as_of=datetime(2026, 7, 3, 15),
        )

        self.assertEqual(margin["balance_100m"], 18300)
        self.assertGreater(margin["balance_change_pct"], 0)
        self.assertEqual(margin["buy_100m"], 1070)
        self.assertEqual(northbound["net_buy_100m"], 40)
        self.assertEqual(northbound["net_buy_5d_100m"], 95)

    def test_market_structure_source_normalizes_large_margin_units(self):
        margin = build_margin_summary(
            [
                {"日期": "2026-07-02", "融资融券余额": 1_000_000_000_000, "融资买入额": 50_000_000_000},
                {"日期": "2026-07-03", "融资融券余额": 1_020_000_000_000, "融资买入额": 65_000_000_000},
            ],
        )
        northbound = build_northbound_summary(
            [
                {"日期": "2026-07-02", "当日成交净买额": "nan"},
                {"日期": "2026-07-03", "当日成交净买额": 40},
            ],
            as_of=datetime(2026, 7, 3, 15),
        )

        self.assertEqual(margin["balance_100m"], 10200)
        self.assertEqual(margin["buy_100m"], 650)
        self.assertEqual(northbound["net_buy_100m"], 40)

    def test_sector_rotation_scores_matching_profile(self):
        history_rows = [
            {"close": 100, "amount": 100},
            {"close": 101, "amount": 110},
            {"close": 102, "amount": 120},
            {"close": 103, "amount": 150},
            {"close": 106, "amount": 220},
            {"close": 109, "amount": 260},
            {"close": 111, "amount": 300},
        ]
        signal = score_sector_history("证券", history_rows, rank=3)
        self.assertEqual(signal.name, "非银金融")
        self.assertGreater(signal.score, 0)

        profile = StockProfile(code="600030", name="中信证券", industry="非银金融", themes=("非银金融",))
        score = score_sector_rotation(profile, {"非银金融": signal})[0]
        self.assertEqual(score.name, "行业轮动")
        self.assertGreater(score.score, 0)
        self.assertTrue(score.passed)

    def test_chain_rotation_scores_ai_to_material_transfer(self):
        profile = StockProfile(code="600160", name="巨化股份", industry="氟化工", themes=("新材料", "先进制造"))
        signals = {
            "AI硬件": SectorSignal(name="AI硬件", score=24, return_3d=0.05, return_5d=0.08, amount_ratio=1.3),
            "半导体": SectorSignal(name="半导体", score=18, return_3d=0.03, return_5d=0.05, amount_ratio=1.2),
            "材料": SectorSignal(name="材料", score=10, return_3d=0.012, return_5d=0.02, amount_ratio=1.25),
        }
        score = score_chain_rotation(profile, signals)[0]
        self.assertEqual(score.name, "产业链传导")
        self.assertGreater(score.score, 0)
        self.assertTrue(score.passed)
        self.assertIn("AI算力链", score.detail)
        self.assertIn("接力", score.detail)

    def test_chain_rotation_penalizes_late_catchup_after_upstream_fades(self):
        profile = StockProfile(code="600160", name="巨化股份", industry="氟化工", themes=("新材料",))
        signals = {
            "AI硬件": SectorSignal(name="AI硬件", score=-14, return_3d=-0.05, return_5d=-0.04, amount_ratio=0.8),
            "半导体": SectorSignal(name="半导体", score=-12, return_3d=-0.04, return_5d=-0.03, amount_ratio=0.9),
            "材料": SectorSignal(name="材料", score=12, return_3d=0.02, return_5d=0.03, amount_ratio=1.1),
        }
        score = score_chain_rotation(profile, signals)[0]
        self.assertLess(score.score, 0)
        self.assertFalse(score.passed)
        self.assertIn("退潮", score.detail)

    def test_sector_rotation_fallback_uses_candidate_bars(self):
        price_map = {
            "000300": make_bars("000300", "沪深300", [100, 100, 100, 100, 100, 100, 100]),
            "600030": make_bars("600030", "中信证券", [10, 10.1, 10.2, 10.4, 10.8, 11.0, 11.3]),
            "300059": make_bars("300059", "东方财富", [20, 20.1, 20.2, 20.5, 21.0, 21.3, 21.8]),
        }
        profiles = {
            "600030": StockProfile(code="600030", name="中信证券", industry="非银金融", themes=("非银金融",)),
            "300059": StockProfile(code="300059", name="东方财富", industry="非银金融", themes=("非银金融",)),
        }
        signals = build_sector_signals_from_price_map(price_map, profiles)
        self.assertIn("非银金融", signals)
        self.assertGreater(signals["非银金融"].score, 0)


class PolicyFactorTest(unittest.TestCase):
    def test_balanced_cycle_is_not_overweight_defensive_energy(self):
        profile = StockProfile(
            code="600900",
            name="长江电力",
            industry="公用事业",
            themes=("高股息", "防御", "能源安全", "现金流"),
            market_cap_billion=6600,
        )
        scores = score_policy(profile, StrategyPolicy(cycle="balanced"))
        cycle_score = next(item for item in scores if item.name == "周期产业")

        self.assertLessEqual(cycle_score.score, 8)

    def test_cycle_policy_is_reported_as_style_bias(self):
        row = build_recommendation(
            "600900",
            make_bars("600900", "长江电力", [26.2, 26.3, 26.4, 26.5, 26.7, 26.9, 27.0]),
            make_bars("000300", "沪深300", [100, 101, 102, 103, 104, 105, 106]),
            profile=StockProfile(
                code="600900",
                name="长江电力",
                industry="公用事业",
                themes=("高股息", "防御", "能源安全"),
                market_cap_billion=6600,
            ),
            policy=StrategyPolicy(cycle="balanced"),
        )

        self.assertIn("风格偏向", row.candidate_breakdown)


class MacroEventFactorTest(unittest.TestCase):
    def test_macro_event_source_reads_active_enabled_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "macro.csv"
            path.write_text(
                "\n".join(
                    [
                        "enabled,date,title,source,event_type,stance,positive_sectors,negative_sectors,base_score,decay_days,confidence,detail,url",
                        "true,2026-07-02,美国就业弱于预期,manual,us_jobs,dovish,黄金/有色/半导体,银行,8,2,0.8,降息预期升温,",
                        "false,2026-07-02,禁用事件,manual,test,neutral,银行,,8,2,1.0,,",
                    ]
                ),
                encoding="utf-8",
            )
            events = CsvMacroEventSource(path=path, as_of=datetime(2026, 7, 3, 9), lookback_days=7).load()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "美国就业弱于预期")
        self.assertIn("黄金", events[0].positive_sectors)
        self.assertIn("银行", events[0].negative_sectors)

    def test_macro_event_scores_matching_profile_with_decay(self):
        event = MacroEvent(
            event_date=datetime(2026, 7, 2, 9),
            title="美国就业弱于预期",
            source="manual",
            stance="dovish",
            positive_sectors=("黄金", "资源", "半导体"),
            negative_sectors=("银行",),
            base_score=8,
            decay_days=2,
            confidence=1.0,
        )
        gold = StockProfile(code="600489", name="中金黄金", industry="黄金", themes=("资源",))
        bank = StockProfile(code="600036", name="招商银行", industry="银行", themes=("金融稳定",))

        gold_score = score_macro_events(gold, [event], as_of=datetime(2026, 7, 2, 12))[0]
        bank_score = score_macro_events(bank, [event], as_of=datetime(2026, 7, 2, 12))[0]
        stale_score = score_macro_events(gold, [event], as_of=datetime(2026, 7, 5, 12))[0]

        self.assertGreater(gold_score.score, 0)
        self.assertLess(bank_score.score, 0)
        self.assertEqual(stale_score.score, 0)

    def test_macro_event_maps_nonferrous_to_industrial_metals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "macro.csv"
            path.write_text(
                "\n".join(
                    [
                        "enabled,date,title,source,event_type,stance,positive_sectors,negative_sectors,base_score,decay_days,confidence,detail,url",
                        "true,2026-07-03,美元指数走弱,manual,usd,weak_usd,有色/铜/铝,银行,6,2,1.0,,",
                    ]
                ),
                encoding="utf-8",
            )
            events = CsvMacroEventSource(path=path, as_of=datetime(2026, 7, 3, 10), lookback_days=7).load()

        profile = StockProfile(
            code="603993",
            name="洛阳钼业",
            industry="工业金属",
            themes=("工业金属", "资源"),
            market_cap_billion=3900,
        )
        score = score_macro_events(profile, events, as_of=datetime(2026, 7, 3, 10))[0]

        self.assertGreater(score.score, 0)
        self.assertIn("工业金属", events[0].positive_sectors)

    def test_macro_event_contributes_to_recommendation_breakdown(self):
        benchmark = make_bars("000300", "沪深300", [100, 101, 102, 103, 104, 105, 106])
        bars = make_bars("600489", "中金黄金", [14, 14.1, 14.2, 14.4, 14.6, 14.7, 14.8])
        profile = StockProfile(code="600489", name="中金黄金", industry="黄金", themes=("资源",), market_cap_billion=900)
        event = MacroEvent(
            event_date=datetime(2026, 7, 3, 8),
            title="美元利率走弱",
            source="manual",
            stance="dovish",
            positive_sectors=("黄金", "资源"),
            base_score=8,
            decay_days=2,
            confidence=1.0,
        )
        row = build_recommendation(
            "600489",
            bars,
            benchmark,
            profile=profile,
            macro_events=[event],
            as_of=datetime(2026, 7, 3, 10),
        )

        self.assertIn("宏观事件", row.candidate_breakdown)
        self.assertGreater(row.macro_event_score, 0)
        self.assertIn("美元利率走弱", row.macro_events)

    def test_macro_event_resolves_mixed_theme_by_primary_sector(self):
        event = MacroEvent(
            event_date=datetime(2026, 7, 3, 8),
            title="芯片出口管制升级",
            source="manual",
            stance="trade_restriction",
            positive_sectors=("国产替代", "半导体", "军工"),
            negative_sectors=("出口链", "电子", "AI硬件"),
            base_score=8,
            decay_days=2,
            confidence=1.0,
        )
        profile = StockProfile(
            code="688981",
            name="中芯国际",
            industry="半导体",
            themes=("国产替代", "AI硬件"),
            market_cap_billion=6500,
        )
        score = score_macro_events(profile, [event], as_of=datetime(2026, 7, 3, 10))[0]

        self.assertGreater(score.score, 0)
        self.assertIn("半导体", score.detail)

    def test_macro_rss_classifies_weak_jobs_release(self):
        xml = """
        <rss><channel>
          <item>
            <title>Employment Situation Summary</title>
            <description>Both payroll employment (+57,000) and unemployment rate changed little in June.</description>
            <link>https://www.bls.gov/news.release/empsit.nr0.htm</link>
            <pubDate>Thu, 02 Jul 2026 08:30:00 GMT</pubDate>
          </item>
        </channel></rss>
        """
        events = parse_rss_events(
            xml,
            MacroRssFeed("BLS Employment", "https://example.com/rss", "employment", lookback_days=3),
            as_of=datetime(2026, 7, 3, 10),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "us_jobs")
        self.assertEqual(events[0].stance, "dovish")
        self.assertIn("黄金", events[0].positive_sectors)

    def test_macro_rss_classifies_hawkish_fed_speech(self):
        xml = """
        <rss><channel>
          <item>
            <title>Monetary policy and inflation risks</title>
            <description>Inflation risks remain too high and policy may need higher rates for longer.</description>
            <link>https://www.federalreserve.gov/newsevents/speech/test.htm</link>
            <pubDate>Thu, 02 Jul 2026 12:00:00 GMT</pubDate>
          </item>
        </channel></rss>
        """
        events = parse_rss_events(
            xml,
            MacroRssFeed("Fed Speeches", "https://example.com/rss", "fed_speech", lookback_days=3),
            as_of=datetime(2026, 7, 3, 10),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "hawkish")
        self.assertIn("AI硬件", events[0].negative_sectors)

    def test_macro_rss_classifies_central_bank_rate_hike(self):
        xml = """
        <rss><channel>
          <item>
            <title>Central bank raises rates</title>
            <description>The central bank raised rates as inflation pressure remained high.</description>
            <link>https://example.com/central-bank</link>
            <pubDate>Thu, 02 Jul 2026 12:00:00 GMT</pubDate>
          </item>
        </channel></rss>
        """
        events = parse_rss_events(
            xml,
            MacroRssFeed("Global Macro", "https://example.com/rss", "general_macro", lookback_days=3),
            as_of=datetime(2026, 7, 3, 10),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "central_bank")
        self.assertEqual(events[0].stance, "hawkish_global")
        self.assertIn("银行", events[0].positive_sectors)

    def test_macro_rss_classifies_policy_tax_tightening(self):
        xml = """
        <rss><channel>
          <item>
            <title>关于规范税收征管有关事项的通知</title>
            <description>提高税率并严查偷逃税，推动平台监管和税收征管趋严。</description>
            <link>https://example.com/policy-tax</link>
            <pubDate>Thu, 02 Jul 2026 12:00:00 GMT</pubDate>
          </item>
        </channel></rss>
        """
        events = parse_rss_events(
            xml,
            MacroRssFeed("Official Policy", "https://example.com/rss", "policy", lookback_days=3),
            as_of=datetime(2026, 7, 3, 10),
        )
        consumer = StockProfile(code="000333", name="美的集团", industry="家电", themes=("消费",))
        utility = StockProfile(code="600900", name="长江电力", industry="电力", themes=("高股息",))

        consumer_score = score_macro_events(consumer, events, as_of=datetime(2026, 7, 3, 10))[0]
        utility_score = score_macro_events(utility, events, as_of=datetime(2026, 7, 3, 10))[0]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "policy_event")
        self.assertEqual(events[0].stance, "tax_tightening")
        self.assertIn("消费", events[0].negative_sectors)
        self.assertLess(consumer_score.score, 0)
        self.assertGreater(utility_score.score, 0)

    def test_macro_rss_classifies_industrial_policy_support(self):
        xml = """
        <rss><channel>
          <item>
            <title>关于促进人工智能和数据要素产业发展的意见</title>
            <description>支持人工智能、算力、半导体、先进制造和机器人产业创新。</description>
            <link>https://example.com/policy-ai</link>
            <pubDate>Thu, 02 Jul 2026 12:00:00 GMT</pubDate>
          </item>
        </channel></rss>
        """
        events = parse_rss_events(
            xml,
            MacroRssFeed("Official Policy", "https://example.com/rss", "policy", lookback_days=3),
            as_of=datetime(2026, 7, 3, 10),
        )
        ai_profile = StockProfile(code="688041", name="海光信息", industry="半导体", themes=("AI硬件",))
        score = score_macro_events(ai_profile, events, as_of=datetime(2026, 7, 3, 10))[0]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "policy_event")
        self.assertEqual(events[0].stance, "industrial_policy_support")
        self.assertIn("半导体", events[0].positive_sectors)
        self.assertGreater(score.score, 0)

    def test_policy_page_source_extracts_and_classifies_titles(self):
        html = """
        <html><body>
          <ul>
            <li><a href="/policy/ai.html">关于促进人工智能和数据要素产业发展的意见</a><span>2026-07-02</span></li>
            <li><a href="/older.html">关于其他事项的通知</a><span>2026-06-01</span></li>
          </ul>
        </body></html>
        """
        items = parse_policy_page_items(html, "https://example.com/list/", as_of=datetime(2026, 7, 3, 10))
        events = parse_policy_page_events(
            html,
            PolicyPage("官方政策页", "https://example.com/list/", "policy", lookback_days=7),
            as_of=datetime(2026, 7, 3, 10),
        )

        self.assertEqual(items[0]["link"], "https://example.com/policy/ai.html")
        self.assertEqual(items[0]["published_at"], datetime(2026, 7, 2))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "industrial_policy_support")

    def test_policy_page_source_classifies_capital_market_support(self):
        xml = """
        <rss><channel>
          <item>
            <title>证监会发布资本市场支持长期资金入市和并购重组政策</title>
            <description>推动回购增持和提高上市公司质量。</description>
            <link>https://example.com/csrc</link>
            <pubDate>Thu, 02 Jul 2026 12:00:00 GMT</pubDate>
          </item>
        </channel></rss>
        """
        events = parse_rss_events(
            xml,
            MacroRssFeed("证监会要闻", "https://example.com/rss", "policy", lookback_days=3),
            as_of=datetime(2026, 7, 3, 10),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "capital_market_support")
        self.assertIn("非银金融", events[0].positive_sectors)

    def test_policy_page_source_classifies_exchange_regulatory_tightening(self):
        xml = """
        <rss><channel>
          <item>
            <title>深交所进一步规范程序化交易和退市监管安排</title>
            <description>从严监管违规交易行为。</description>
            <link>https://example.com/szse</link>
            <pubDate>Thu, 02 Jul 2026 12:00:00 GMT</pubDate>
          </item>
        </channel></rss>
        """
        events = parse_rss_events(
            xml,
            MacroRssFeed("深交所通知公告", "https://example.com/rss", "policy", lookback_days=3),
            as_of=datetime(2026, 7, 3, 10),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "regulatory_tightening")
        self.assertIn("公用事业", events[0].positive_sectors)

    def test_policy_page_source_classifies_m2_slowdown_as_tightening(self):
        xml = """
        <rss><channel>
          <item>
            <title>人民银行发布6月货币供应量：M2同比增速下降</title>
            <description>社融和新增人民币贷款同比少增。</description>
            <link>https://example.com/pboc-m2</link>
            <pubDate>Thu, 02 Jul 2026 12:00:00 GMT</pubDate>
          </item>
        </channel></rss>
        """
        events = parse_rss_events(
            xml,
            MacroRssFeed("人民银行新闻", "https://example.com/rss", "policy", lookback_days=3),
            as_of=datetime(2026, 7, 3, 10),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "china_liquidity_tightening")
        self.assertIn("非银金融", events[0].negative_sectors)

    def test_pboc_open_market_detail_classifies_net_injection(self):
        event = parse_open_market_detail(
            title="公开市场业务交易公告 [2026]第127号",
            raw_html="""
            <html><body>
            为维护银行体系流动性合理充裕，2026年7月3日人民银行以固定利率、数量招标方式
            开展了1000亿元7天期逆回购操作，中标利率1.40%。
            今日有300亿元逆回购到期，实现净投放700亿元。
            </body></html>
            """,
            url="https://www.pbc.gov.cn/test.html",
            published_at=datetime(2026, 7, 3),
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.stance, "pboc_net_injection")
        self.assertIn("净投放 700亿元", event.detail)

    def test_pboc_open_market_detail_classifies_mlf_rate_cut(self):
        event = parse_open_market_detail(
            title="中期借贷便利（MLF）操作公告",
            raw_html="""
            <html><body>
            人民银行开展3000亿元中期借贷便利（MLF）操作，操作利率较上次下降10个基点。
            </body></html>
            """,
            url="https://www.pbc.gov.cn/mlf.html",
            published_at=datetime(2026, 7, 3),
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "pboc_rate")
        self.assertEqual(event.stance, "pboc_policy_rate_cut")

    def test_pboc_lpr_events_classify_cut(self):
        events = build_lpr_events(
            [
                {"TRADE_DATE": "2026-06-20", "LPR1Y": 3.45, "LPR5Y": 3.95},
                {"TRADE_DATE": "2026-07-20", "LPR1Y": 3.35, "LPR5Y": 3.85},
            ],
            as_of=datetime(2026, 7, 21, 10),
            lookback_days=45,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "pboc_lpr_cut")
        self.assertIn("银行", events[0].negative_sectors)

    def test_pboc_reserve_requirement_events_classify_cut(self):
        events = build_reserve_requirement_events(
            [
                {
                    "公布时间": "2026-07-01",
                    "大型金融机构-调整幅度": -0.5,
                    "中小金融机构-调整幅度": -0.5,
                }
            ],
            as_of=datetime(2026, 7, 3, 10),
            lookback_days=45,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "pboc_rrr_cut")
        self.assertIn("非银金融", events[0].positive_sectors)

    def test_pboc_money_supply_events_classify_m2_reacceleration(self):
        events = build_money_supply_events(
            [
                {"月份": "2026-05", "货币和准货币(M2)-同比增长": 7.2},
                {"月份": "2026-06", "货币和准货币(M2)-同比增长": 7.8},
            ],
            as_of=datetime(2026, 7, 3, 10),
            lookback_days=45,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "m2_growth_reaccelerates")

    def test_pboc_credit_growth_events_classify_loan_contraction(self):
        events = build_credit_growth_events(
            [
                {"月份": "2026-05", "当月": 9000, "当月-同比增长": -8.0},
                {"月份": "2026-06", "当月": 7000, "当月-同比增长": -18.0},
            ],
            as_of=datetime(2026, 7, 3, 10),
            lookback_days=45,
            title_prefix="新增人民币贷款",
            value_field="当月",
            yoy_field="当月-同比增长",
            source="test",
            url="https://example.com",
            confidence=0.6,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "credit_growth_contracts")
        self.assertIn("非银金融", events[0].negative_sectors)

    def test_pboc_social_financing_events_classify_expansion(self):
        events = build_social_financing_events(
            [
                {"月份": "2026-05", "社会融资规模增量": 10000, "其中-人民币贷款": 7000},
                {"月份": "2026-06", "社会融资规模增量": 16000, "其中-人民币贷款": 11000},
            ],
            as_of=datetime(2026, 7, 3, 10),
            lookback_days=45,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "social_financing_expands")

    def test_stats_macro_pmi_events_classify_improvement(self):
        events = build_pmi_events(
            [
                {"月份": "2026-05", "制造业-指数": 49.8, "非制造业-指数": 50.2},
                {"月份": "2026-06", "制造业-指数": 50.3, "非制造业-指数": 50.8},
            ],
            as_of=datetime(2026, 7, 3, 10),
            lookback_days=45,
        )

        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "china_macro_surprise")
        self.assertIn(events[0].stance, {"china_pmi_improves", "china_pmi_weakens"})

    def test_stats_macro_consensus_surprise_classifies_upside(self):
        events = build_consensus_surprise_events(
            [
                {"日期": "2026-06-16", "今值": 6.8, "预测值": 5.5, "前值": 5.2},
            ],
            as_of=datetime(2026, 7, 3, 10),
            lookback_days=45,
            title="规模以上工业增加值同比",
            threshold=0.8,
            source="test",
            url="https://example.com",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "china_growth_upside_surprise")
        self.assertIn("非银金融", events[0].positive_sectors)

    def test_stats_macro_indicator_acceleration_classifies_slowdown(self):
        events = build_indicator_acceleration_events(
            [
                {"月份": "2026-05", "同比增长": 4.2},
                {"月份": "2026-06", "同比增长": 2.9},
            ],
            as_of=datetime(2026, 7, 3, 10),
            lookback_days=45,
            title="社零同比增速",
            value_field="同比增长",
            threshold=0.8,
            source="test",
            url="https://example.com",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].stance, "china_growth_slows")
        self.assertIn("非银金融", events[0].negative_sectors)


class DisclosureFactorTest(unittest.TestCase):
    def test_shareholder_reduce_disclosure_excludes(self):
        event = classify_disclosure(
            code="600160",
            title="关于控股股东拟减持股份的预披露公告",
            published_at=datetime(2026, 7, 2),
        )
        score = score_disclosure_events([event], as_of=datetime(2026, 7, 2))[0]
        self.assertEqual(score.name, "公告风险")
        self.assertLess(score.score, 0)
        self.assertFalse(score.passed)

    def test_positive_earnings_disclosure_scores_positive(self):
        event = classify_disclosure(
            code="600160",
            title="2026年半年度业绩预告：预计净利润同比增长",
            published_at=datetime(2026, 7, 2),
        )
        score = score_disclosure_events([event], as_of=datetime(2026, 7, 2))[0]
        self.assertEqual(score.name, "公告事件")
        self.assertGreater(score.score, 0)
        self.assertTrue(score.passed)

    def test_buyback_disclosure_scores_positive(self):
        event = classify_disclosure(
            code="600160",
            title="关于以集中竞价交易方式回购公司股份方案的公告",
            published_at=datetime(2026, 7, 2),
        )
        score = score_disclosure_events([event], as_of=datetime(2026, 7, 2))[0]

        self.assertEqual(event.event_type, "share_buyback")
        self.assertGreater(score.score, 0)
        self.assertIn("利好公告", score.detail)

    def test_soft_reduce_disclosure_penalizes_without_excluding(self):
        event = classify_disclosure(
            code="600160",
            title="关于持股5%以上股东减持计划的预披露公告",
            published_at=datetime(2026, 7, 2),
        )
        score = score_disclosure_events([event], as_of=datetime(2026, 7, 2))[0]

        self.assertEqual(event.event_type, "shareholder_reduce")
        self.assertFalse(event.hard_exclude)
        self.assertEqual(score.name, "公告事件")
        self.assertLess(score.score, 0)
        self.assertTrue(score.passed)

    def test_penalty_disclosure_remains_hard_exclude(self):
        event = classify_disclosure(
            code="600160",
            title="关于收到行政处罚事先告知书的公告",
            published_at=datetime(2026, 7, 2),
        )
        score = score_disclosure_events([event], as_of=datetime(2026, 7, 2))[0]

        self.assertTrue(event.hard_exclude)
        self.assertEqual(score.name, "公告风险")
        self.assertFalse(score.passed)

    def test_disclosure_risk_excludes_recommendation(self):
        event = classify_disclosure(
            code="300498",
            title="关于公司涉及重大诉讼的公告",
            published_at=datetime(2026, 7, 2),
        )
        rows = rank_recommendations(
            self_price_map(),
            "000300",
            disclosure_events={"300498": [event]},
            as_of=datetime(2026, 7, 2),
        )
        row = next(item for item in rows if item.code == "300498")
        self.assertEqual(row.status, "排除")


class RiskCalendarFactorTest(unittest.TestCase):
    def test_near_large_release_is_hard_risk(self):
        severity, hard_exclude = score_release_risk(days=5, pct_total=1.5, pct_float=2.0)
        self.assertGreaterEqual(severity, 0.9)
        self.assertTrue(hard_exclude)

    def test_high_pledge_is_hard_risk(self):
        severity, hard_exclude = score_pledge_risk(total_ratio=22.0, near_close_line=False)
        self.assertGreaterEqual(severity, 0.9)
        self.assertTrue(hard_exclude)

    def test_risk_calendar_excludes_recommendation(self):
        event = RiskCalendarEvent(
            code="300498",
            title="限售解禁",
            source="test",
            event_date=datetime(2026, 7, 5),
            event_type="restricted_release",
            severity=1.0,
            hard_exclude=True,
            detail="3天后大比例解禁",
        )
        rows = rank_recommendations(
            self_price_map(),
            "000300",
            risk_calendar_events={"300498": [event]},
            as_of=datetime(2026, 7, 2),
        )
        row = next(item for item in rows if item.code == "300498")
        self.assertEqual(row.status, "排除")
        self.assertTrue(row.risk_calendar.startswith("硬:"))

    def test_disclosure_events_map_to_risk_calendar(self):
        event = classify_disclosure(
            code="600160",
            title="关于控股股东拟减持股份的预披露公告",
            published_at=datetime(2026, 7, 2),
        )
        mapped = disclosure_events_to_risk_calendar({"600160": [event]})
        self.assertEqual(mapped["600160"][0].event_type, "shareholder_reduce")

    def test_risk_calendar_warning_penalizes_without_hard_exclude(self):
        event = RiskCalendarEvent(
            code="600160",
            title="质押比例较高",
            source="test",
            event_type="pledge_risk",
            severity=0.7,
            hard_exclude=False,
        )
        score = score_risk_calendar_events([event], as_of=datetime(2026, 7, 2))[0]
        self.assertEqual(score.name, "风险日历")
        self.assertLess(score.score, 0)
        self.assertFalse(score.passed)

    def test_static_risk_calendar_loads_daozang_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "akshare_risk_calendar.csv"
            path.write_text(
                "\n".join(
                    [
                        "code,name,risk_tags,risk_source,risk_detail,event_date,severity,hard_exclude",
                        "000100,TCL科技,解禁硬风险;大额解禁,东方财富限售解禁,2026-07-10 解禁;3天后,2026-07-10,1,1",
                        "600000,浦发银行,财报临近,巨潮财报预约披露,2026半年报 预约披露,2026-08-30,0.25,0",
                    ]
                ),
                encoding="utf-8",
            )

            events = load_static_risk_calendar(
                path,
                symbols=["000100", "600000"],
                as_of=datetime(2026, 7, 7),
                forward_days=30,
            )

        self.assertEqual(events["000100"][0].event_type, "restricted_release")
        self.assertTrue(events["000100"][0].hard_exclude)
        self.assertNotIn("600000", events)


class ContentIngestTest(unittest.TestCase):
    def test_parse_wechat_html_extracts_main_content(self):
        raw_html = """
        <html><head><meta property="og:title" content="测试标题"></head>
        <body>
        <h1 id="activity-name">测试标题</h1>
        <div id="js_content"><p>AI硬件出现获利盘。</p><script>var noisy = true;</script></div>
        <script>var author = "测试作者"; var ct = "1782921281";</script>
        </body></html>
        """
        title, author, published_at, text = parse_wechat_html(raw_html)
        self.assertEqual(title, "测试标题")
        self.assertEqual(author, "测试作者")
        self.assertIsNotNone(published_at)
        self.assertIn("AI硬件", text)
        self.assertNotIn("noisy", text)

    def test_manual_text_source_builds_article(self):
        article = ManualTextSource(
            text="7月2日市场总结，非银金融走强。",
            title="7月2日市场总结",
            source_name="许戈",
            author="许戈",
            published_at=datetime(2026, 7, 2, 23, 59, 59),
        ).load()
        self.assertEqual(article.title, "7月2日市场总结")
        self.assertEqual(article.source_name, "许戈")
        self.assertEqual(article.published_at, datetime(2026, 7, 2, 23, 59, 59))

    def test_distill_article_detects_ai_hardware_risk(self):
        article = ArticleContent(
            title="测试",
            author="作者",
            source_name="来源",
            url="https://example.com",
            text="AI硬件存储突然大跌，算力链出现获利盘，资本开支见顶担忧升温。券商资金流入。",
        )
        signal = distill_article(article)
        self.assertIn("AI硬件", signal.themes)
        self.assertIn("非银金融", signal.themes)
        self.assertTrue(signal.risk_flags)
        self.assertIn("偏谨慎", signal.stance)

    def test_distill_article_uses_title_for_risk(self):
        article = ArticleContent(
            title="AI半导体随时崩盘",
            author="作者",
            source_name="来源",
            url="https://example.com",
            text="注意一个信号。券商资金流入。",
        )
        signal = distill_article(article)
        self.assertIn("AI硬件拥挤交易风险", signal.risk_flags)
        self.assertIn("AI硬件/存储链偏谨慎", signal.stance)

    def test_distill_signal_keeps_dates_and_rules(self):
        article = ArticleContent(
            title="AI半导体随时崩盘",
            author="作者",
            source_name="来源",
            url="https://example.com",
            text="注意一个信号。",
            published_at=datetime(2026, 6, 29, 20, 28, 38),
        )
        signal = distill_article(article, ingested_at=datetime(2026, 7, 2, 9, 0, 0))
        payload = opinion_signal_to_dict(signal)
        self.assertEqual(payload["signal_date"], "2026-06-29T20:28:38")
        self.assertEqual(payload["ingested_at"], "2026-07-02T09:00:00")
        self.assertTrue(payload["rule_version"])
        self.assertTrue(payload["matched_rules"])

    def test_distill_skips_consumer_profile_mapping(self):
        article = ArticleContent(
            title="测试",
            author="作者",
            source_name="来源",
            url="https://example.com",
            text="畜牧方向被提及为观察方向。",
        )
        profiles = {
            "300498": StockProfile(
                code="300498",
                name="温氏股份",
                industry="养殖",
                themes=("农业", "防御", "消费"),
            )
        }
        signal = distill_article(article, profiles=profiles)
        self.assertEqual(signal.symbols, ())

    def test_distill_does_not_map_by_industry_word_only(self):
        article = ArticleContent(
            title="测试",
            author="作者",
            source_name="来源",
            url="https://example.com",
            text="今天黄金方向被提及，但没有点名具体公司。",
        )
        profiles = {
            "600489": StockProfile(
                code="600489",
                name="中金黄金",
                industry="黄金",
                themes=("黄金", "避险", "资源"),
            )
        }
        signal = distill_article(article, profiles=profiles)
        self.assertEqual(signal.symbols, ())

    def test_distill_medical_keyword_uses_innovative_drug_rule(self):
        article = ArticleContent(
            title="测试",
            author="作者",
            source_name="来源",
            url="https://example.com",
            text="医药方向被提及为观察方向。",
        )
        signal = distill_article(article)
        self.assertIn("创新药观察", signal.stance)
        self.assertTrue(any(rule.startswith("WATCH_INNOVATIVE_DRUG") for rule in signal.matched_rules))

    def test_distill_market_summary_rules(self):
        article = ArticleContent(
            title="7月2日市场总结",
            author="许戈",
            source_name="许戈",
            url="",
            published_at=datetime(2026, 7, 2, 23, 59, 59),
            text=(
                "费城半导体指数跌超6%，Meta卖闲置算力引发AI算力过剩担忧，"
                "加息预期上来了。A股板块里非银金融、农林牧渔走强，"
                "CPO、光模块和贵金属、煤炭石化受外围影响回调。"
                "稳健选手可以搞宽基指数，激进点选科创ETF，少单押个股，分散布局。"
            ),
        )
        signal = distill_article(article)
        self.assertIn("AI硬件/存储链偏谨慎", signal.stance)
        self.assertIn("非银金融偏积极观察", signal.stance)
        self.assertIn("宽基指数", signal.themes)
        self.assertIn("资源股外围回调风险", signal.risk_flags)
        self.assertTrue(any(rule.startswith("ALLOC_ETF_DIVERSIFY") for rule in signal.matched_rules))

    def test_distill_article_detects_a_share_policy_factors(self):
        article = ArticleContent(
            title="中美股市体系差异与A股长期改革方向",
            author="何毅财经课堂",
            source_name="何毅财经课堂",
            url="",
            text=(
                "A股是外部约束型，上涨需政策确认，承载资源配置等多重职能，"
                "会调控降温防暴涨暴跌。未来A股改革方向是包容健康上涨，"
                "减少临时干预，引入长期资金，转向基本面定价，对长期发展有信心。"
            ),
        )

        signal = distill_article(article)

        self.assertIn("A股政策确认", signal.themes)
        self.assertIn("长期资金/基本面定价", signal.themes)
        self.assertIn("市场干预风险", signal.themes)
        self.assertIn("政策未确认风险", signal.risk_flags)
        self.assertIn("追高降温风险", signal.risk_flags)
        self.assertIn("A股政策确认观察", signal.stance)
        self.assertIn("长期资金/基本面定价观察", signal.stance)
        self.assertIn("追高降温风险", signal.stance)
        self.assertTrue(any(rule.startswith("WATCH_A_SHARE_POLICY_CONFIRMATION") for rule in signal.matched_rules))

    def test_opinion_market_wide_risk_maps_to_candidate_event(self):
        article = ArticleContent(
            title="中美股市体系差异与A股长期改革方向",
            author="何毅财经课堂",
            source_name="何毅财经课堂",
            url="",
            text=(
                "A股是外部约束型，上涨需政策确认。市场会调控降温防暴涨暴跌，"
                "也要引入长期资金，转向基本面定价。"
            ),
            published_at=datetime(2026, 7, 5, 20, 0, 0),
        )
        signal = distill_article(article)
        profile = StockProfile(code="000963", name="华东医药", industry="医药", themes=("创新药",))

        event = signal_to_news_event(signal, "000963", profile)

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "opinion_market_intervention_risk")
        self.assertLess(event.polarity, 0)

    def test_opinion_signal_news_source_converts_theme_risk_to_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            article = ArticleContent(
                title="AI半导体随时崩盘",
                author="作者",
                source_name="来源",
                url="https://example.com",
                text="注意一个信号。",
                published_at=datetime(2026, 6, 29, 20, 28, 38),
            )
            signal = distill_article(article, ingested_at=datetime(2026, 7, 2, 9, 0, 0))
            path = Path(tmpdir) / "opinion.jsonl"
            path.write_text(json.dumps(opinion_signal_to_dict(signal), ensure_ascii=False) + "\n", encoding="utf-8")
            profiles = {
                "688001": StockProfile(
                    code="688001",
                    name="测试半导体",
                    industry="半导体",
                    themes=("AI硬件", "半导体"),
                    market_cap_billion=1000,
                )
            }
            events = OpinionSignalNewsSource(
                symbols=["688001"],
                profiles=profiles,
                path=path,
                as_of=datetime(2026, 7, 2),
                lookback_days=7,
            ).load()
            self.assertEqual(len(events["688001"]), 1)
            self.assertEqual(events["688001"][0].polarity, -1)
            self.assertTrue(events["688001"][0].source.startswith("个人观点源"))


class UniverseSourceTest(unittest.TestCase):
    def test_select_spot_candidates_filters_st_and_low_turnover(self):
        rows = [
            {"code": "600000", "name": "浦发银行", "latest": 10.0, "turnover": 2_000_000_000},
            {"code": "600001", "name": "ST测试", "latest": 10.0, "turnover": 2_000_000_000},
            {"code": "600002", "name": "低成交", "latest": 10.0, "turnover": 10_000_000},
            {"code": "920000", "name": "北交所", "latest": 10.0, "turnover": 2_000_000_000},
            {"code": "001399", "name": "C惠科股份", "latest": 10.0, "turnover": 2_000_000_000},
        ]
        selected = select_spot_candidates(rows, candidates=10, min_turnover_billion=1.0)
        self.assertEqual([row["code"] for row in selected], ["600000"])

    def test_infer_non_bank_profile(self):
        profile = infer_stock_profile("600030", "中信证券")
        self.assertEqual(profile.industry, "非银金融")
        self.assertIn("非银金融", profile.themes)

    def test_infer_industrial_metal_profile(self):
        profile = infer_stock_profile("603993", "洛阳钼业")
        self.assertEqual(profile.industry, "工业金属")
        self.assertIn("工业金属", profile.themes)
        self.assertEqual(normalize_sector_name("工业金属"), "工业金属")

    def test_legacy_profile_tags_are_split_into_groups(self):
        profile = StockProfile(
            code="600900",
            name="长江电力",
            industry="公用事业",
            themes=("高股息", "防御", "能源安全", "现金流"),
        )

        self.assertEqual(profile_primary_industry(profile), "公用事业")
        self.assertIn("高股息", profile_style_tags(profile))
        self.assertIn("能源安全", profile_style_tags(profile))
        self.assertNotIn("防御", profile_industry_candidates(profile))

    def test_profile_csv_can_load_structured_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "profiles.csv"
            path.write_text(
                "\n".join(
                    [
                        "code,name,industry,themes,market_cap_billion,primary_industry,secondary_industries,style_tags,concept_tags",
                        "603993,洛阳钼业,工业金属,工业金属;资源;新材料,3962,工业金属,钼;铜,全球竞争,新材料;全球竞争",
                    ]
                ),
                encoding="utf-8",
            )
            profile = load_profile_csv(path)["603993"]

        self.assertEqual(profile_primary_industry(profile), "工业金属")
        self.assertEqual(profile_secondary_industries(profile), ("钼", "铜"))
        self.assertEqual(profile_style_tags(profile), ("全球竞争",))
        self.assertIn("新材料", profile_concept_tags(profile))
        self.assertIn("工业金属", profile_all_tags(profile))

    def test_daozang_profiles_merge_active_universe_and_industry_map(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            active_path = Path(tmpdir) / "active_universe.csv"
            industry_path = Path(tmpdir) / "akshare_industry_map.csv"
            active_path.write_text(
                "\n".join(
                    [
                        "code,instrument,name,source_pool,industry,industry_source,themes,latest,turnover,market_cap_billion",
                        "600036,SH600036,招商银行,positions,银行,akshare_ths_industry,金融稳定;高股息,36.6,3442080000,9230.46",
                    ]
                ),
                encoding="utf-8",
            )
            industry_path.write_text(
                "\n".join(
                    [
                        "code,name,industry,themes,industry_source",
                        "600036,招商银行,银行,防御;金融稳定,akshare_ths_industry",
                    ]
                ),
                encoding="utf-8",
            )

            profile = load_daozang_profiles(active_path, industry_path)["600036"]

        self.assertEqual(profile.industry, "银行")
        self.assertEqual(profile.market_cap_billion, 9230.46)
        self.assertIn("高股息", profile_all_tags(profile))
        self.assertIn("防御", profile_all_tags(profile))

    def test_infer_consumer_appliance_profile(self):
        profile = infer_stock_profile("000333", "美的集团")
        self.assertEqual(profile.industry, "消费")
        self.assertIn("消费", profile.themes)

    def test_profile_filter_excludes_consumer_and_small_caps(self):
        consumer = StockProfile(
            code="600519",
            name="贵州茅台",
            themes=("消费", "品牌消费"),
            market_cap_billion=15000,
        )
        small = StockProfile(code="688001", name="测试", themes=("半导体",), market_cap_billion=300)
        large = StockProfile(code="600030", name="中信证券", themes=("非银金融",), market_cap_billion=2000)
        self.assertFalse(passes_profile_filter(consumer, 500, ("消费", "品牌消费")))
        self.assertFalse(passes_profile_filter(small, 500, ("消费", "品牌消费")))
        self.assertTrue(passes_profile_filter(large, 500, ("消费", "品牌消费")))

    def test_universe_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "universe.jsonl"
            rows = [{"code": "600030", "name": "中信证券", "latest": 28.0, "turnover": 1000000000}]
            profiles = {
                "600030": StockProfile(
                    code="600030",
                    name="中信证券",
                    industry="非银金融",
                    themes=("非银金融", "金融稳定"),
                    market_cap_billion=4200,
                )
            }
            save_universe_cache(rows, profiles, path)
            loaded_rows, loaded_profiles = load_universe_cache(path)
            self.assertEqual(loaded_rows[0]["code"], "600030")
            self.assertEqual(loaded_profiles["600030"].industry, "非银金融")
            self.assertEqual(loaded_profiles["600030"].market_cap_billion, 4200)


class RealtimeQuoteTest(unittest.TestCase):
    def test_market_data_router_falls_back_when_primary_fails(self):
        class FailingProvider:
            name = "primary"

            def load(self):
                raise RuntimeError("offline")

        class BackupProvider:
            name = "backup"

            def load(self):
                return {
                    "600000": RealtimeQuote(
                        "600000",
                        "测试银行",
                        10.0,
                        9.9,
                        10.1,
                        9.8,
                        9.9,
                        1.0,
                    )
                }

        router = MarketDataRouter([FailingProvider(), BackupProvider()])
        quotes = router.load()

        self.assertEqual(quotes["600000"].source, "backup")
        self.assertTrue(any(not item.ok and item.source == "primary" for item in router.health))
        self.assertTrue(any(item.ok and item.source == "backup" for item in router.health))

    def test_market_data_router_marks_large_source_diff(self):
        class PrimaryProvider:
            name = "primary"

            def load(self):
                return {
                    "600000": RealtimeQuote(
                        "600000",
                        "测试银行",
                        10.00,
                        9.9,
                        10.1,
                        9.8,
                        9.9,
                        1.0,
                    )
                }

        class BackupProvider:
            name = "backup"

            def load(self):
                return {
                    "600000": RealtimeQuote(
                        "600000",
                        "测试银行",
                        10.10,
                        9.9,
                        10.1,
                        9.8,
                        9.9,
                        1.0,
                    )
                }

        router = MarketDataRouter(
            [PrimaryProvider(), BackupProvider()],
            max_source_diff_pct=0.30,
        )
        quote = router.load()["600000"]

        self.assertEqual(quote.source, "primary")
        self.assertGreater(quote.source_diff_pct, 0.30)
        self.assertIn("多源价格差", quote.warning)

    def test_parse_tencent_quote(self):
        record = (
            'v_sh600900="1~长江电力~600900~27.10~26.64~26.79~1400553~790799~609754~'
            '26.94~1607~26.93~2402~26.92~4078~26.91~2401~26.90~3291~27.10~781~'
            '27.11~694~27.12~516~27.13~391~27.14~607~~20260703100001~0.46~1.73~'
            '27.20~26.55~27.10/1400553/3761292696~1400553~376129~0.57~18.28~~'
            '27.20~26.55~2.03~6594.18~6594.18~2.89~29.30~23.98~1.21~10790~26.86~'
            '24.38~19.11~~~0.10~376129.2696~0.0000~0~ ~GP-A";'
        )
        quote = parse_tencent_quote(record)
        self.assertIsNotNone(quote)
        self.assertEqual(quote.code, "600900")
        self.assertEqual(quote.name, "长江电力")
        self.assertEqual(quote.price, 27.10)
        self.assertAlmostEqual(quote.amount_billion, 37.61292696)
        self.assertEqual(quote.volume_hand, 1400553)
        self.assertEqual(quote.vwap_price, 26.86)
        self.assertEqual(quote.turnover_rate, 0.57)
        self.assertEqual(quote.pe_ratio, 18.28)
        self.assertEqual(quote.pb_ratio, 2.89)
        self.assertEqual(quote.limit_up_price, 29.30)
        self.assertEqual(quote.limit_down_price, 23.98)
        self.assertAlmostEqual(quote.float_market_cap_billion, 659.418)
        self.assertAlmostEqual(quote.market_cap_billion, 659.418)
        self.assertEqual(quote.quote_time.strftime("%Y-%m-%d %H:%M:%S"), "2026-07-03 10:00:01")

    def test_realtime_check_marks_buyable_and_chasing(self):
        recommendation = build_recommendation(
            "600000",
            make_bars("600000", "测试银行", [9.9, 10.0, 10.03, 10.04, 10.05, 10.06]),
            make_bars("000300", "沪深300", [100, 101, 102, 103, 104, 105]),
        )
        borderline = build_realtime_check(
            recommendation,
            RealtimeQuote("600000", "测试银行", recommendation.confirm_price, 10, 10, 10, 9.9, 1.0),
        )
        self.assertEqual(borderline.status, "贴线观察")

        now = datetime(2026, 7, 3, 10, 5)
        waiting = build_realtime_check(
            recommendation,
            RealtimeQuote(
                "600000",
                "测试银行",
                recommendation.confirm_price * 1.003,
                10,
                10,
                10,
                9.9,
                1.0,
                quote_time=now,
            ),
        )
        self.assertEqual(waiting.status, "待站稳")
        self.assertIn("实时站稳", waiting.execution_breakdown)

        buyable = build_realtime_check(
            recommendation,
            RealtimeQuote(
                "600000",
                "测试银行",
                recommendation.confirm_price * 1.003,
                10,
                10,
                10,
                9.9,
                1.0,
                quote_time=now,
            ),
            previous_state={"firm_above": True, "quote_time": (now - timedelta(minutes=6)).isoformat()},
        )
        self.assertEqual(buyable.status, "实时可买")
        self.assertGreater(buyable.execution_score, waiting.execution_score)

        friday_watch = build_realtime_check(
            recommendation,
            RealtimeQuote(
                "600000",
                "测试银行",
                recommendation.confirm_price * 1.003,
                10,
                10,
                10,
                9.9,
                1.0,
                quote_time=now,
            ),
            min_confirm_buffer_pct=0.005,
            friday_mode=True,
        )
        self.assertEqual(friday_watch.status, "周五观察")

        no_sector_confirmation = build_realtime_check(
            recommendation,
            RealtimeQuote(
                "600000",
                "测试银行",
                recommendation.confirm_price * 1.003,
                10,
                10,
                10,
                9.9,
                1.0,
                quote_time=now,
            ),
            previous_state={"firm_above": True, "quote_time": (now - timedelta(minutes=6)).isoformat()},
            sector_confirmation={"passed": False, "detail": "板块共振: 银行 1/4 只站上稳确认价"},
        )
        self.assertEqual(no_sector_confirmation.status, "板块未共振")
        self.assertIn("1/4", no_sector_confirmation.sector_confirmation)
        self.assertLess(no_sector_confirmation.execution_score, buyable.execution_score)

        chasing = build_realtime_check(
            recommendation,
            RealtimeQuote("600000", "测试银行", recommendation.confirm_price * 1.03, 10, 10, 10, 9.9, 3.0),
        )
        self.assertEqual(chasing.status, "已追高")

        failed = build_realtime_check(
            recommendation,
            RealtimeQuote("600000", "测试银行", recommendation.invalid_price - 0.01, 10, 10, 10, 9.9, -2.0),
        )
        self.assertEqual(failed.status, "盘中失效")

    def test_feishu_card_contains_compact_realtime_summary(self):
        recommendation = build_recommendation(
            "600000",
            make_bars("600000", "测试银行", [9.9, 10.0, 10.03, 10.04, 10.05, 10.06]),
            make_bars("000300", "沪深300", [100, 101, 102, 103, 104, 105]),
        )
        check = build_realtime_check(
            recommendation,
            RealtimeQuote(
                "600000",
                "测试银行",
                recommendation.confirm_price * 1.003,
                10,
                10,
                10,
                9.9,
                1.0,
                quote_time=datetime(2026, 7, 3, 10, 6),
            ),
            previous_state={"firm_above": True, "quote_time": datetime(2026, 7, 3, 10, 0).isoformat()},
        )
        card = render_feishu_recommendations_card(
            [recommendation],
            title="北辰 Alpha 样式测试",
            as_of=datetime(2026, 7, 3, 10, 0),
            realtime_checks={"600000": check},
        )
        self.assertEqual(card["header"]["title"]["content"], "北辰 Alpha 样式测试")
        self.assertIn("实时可买", card["elements"][0]["text"]["content"])
        self.assertIn("确认", card["elements"][2]["text"]["content"])
        self.assertIn("候选", card["elements"][2]["text"]["content"])
        self.assertIn("执行", card["elements"][2]["text"]["content"])


class ReturnCalibrationTest(unittest.TestCase):
    def test_calibrates_future_return_from_similar_historical_shapes(self):
        closes = [
            10 + index * 0.03 + (0.08 if index % 6 in {3, 4} else 0.0)
            for index in range(180)
        ]
        bars = make_bars("600000", "测试银行", closes)
        level_window = bars[:-5]
        latest = bars[-5]
        previous = bars[-6]
        confirm = calc_level_confirm_price(level_window)
        invalid = calc_level_invalid_price(level_window)
        target = calc_level_take_profit_price(
            level_window,
            confirm,
            invalid,
            horizon="short_3_5d",
        )

        calibration = calibrate_position_return(
            bars,
            price=latest.close,
            cost=previous.close,
            confirm=confirm,
            invalid=invalid,
            target=target,
            horizon_days=5,
        )

        self.assertIsNotNone(calibration)
        self.assertGreater(calibration.sample_count, 0)
        self.assertGreaterEqual(calibration.up_probability, 0)
        self.assertLessEqual(calibration.up_probability, 1)
        self.assertEqual(calibration.horizon_days, 5)

    def test_qlib_bin_price_source_reads_local_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "calendars").mkdir()
            (root / "features" / "sh600000").mkdir(parents=True)
            (root / "calendars" / "day.txt").write_text(
                "2026-01-01\n2026-01-02\n2026-01-05\n",
                encoding="utf-8",
            )
            for field, values in {
                "open": [10.0, 10.1, 10.2],
                "high": [10.2, 10.3, 10.4],
                "low": [9.9, 10.0, 10.1],
                "close": [10.1, 10.2, 10.3],
                "volume": [1000.0, 1200.0, 1300.0],
                "amount": [10100.0, 12240.0, 13390.0],
            }.items():
                write_qlib_feature(root / "features" / "sh600000" / f"{field}.day.bin", 0, values)

            source = QlibBinPriceSource(root, ["600000"])
            bars = source.load()["600000"]

        self.assertEqual(normalize_qlib_symbol("600000"), "sh600000")
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[-1].date, "2026-01-05")
        self.assertAlmostEqual(bars[-1].close, 10.3, places=5)
        self.assertEqual(bars[-1].volume, 1300)


class BaostockSourceTest(unittest.TestCase):
    def test_normalizes_baostock_rows(self):
        query = FakeBaostockQuery(
            [
                ["2026-07-01", "sh.600000", "10.00", "10.30", "9.90", "10.20", "1200", "12240.5"],
                ["2026-07-02", "sh.600000", "10.20", "10.40", "10.10", "10.30", "1300", "13390.0"],
            ]
        )

        bars = normalize_baostock_rows(query, "600000", "测试银行")

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1].code, "600000")
        self.assertEqual(bars[-1].name, "测试银行")
        self.assertEqual(bars[-1].date, "2026-07-02")
        self.assertAlmostEqual(bars[-1].close, 10.30)
        self.assertEqual(bars[-1].volume, 1300)

    def test_baostock_symbol_and_adjustflag_helpers(self):
        self.assertEqual(baostock_symbol("600036"), "sh.600036")
        self.assertEqual(baostock_symbol("000001"), "sz.000001")
        self.assertEqual(baostock_symbol("000300", is_index=True), "sh.000300")
        self.assertEqual(baostock_adjustflag("qfq"), "2")
        self.assertEqual(baostock_adjustflag("hfq"), "1")
        self.assertEqual(baostock_adjustflag(""), "3")
        self.assertEqual(baostock_date("20260703"), "2026-07-03")


class GlobalLinkageSourceTest(unittest.TestCase):
    def test_parse_fred_csv_skips_missing_values(self):
        rows = parse_fred_csv(
            "observation_date,DGS10\n2026-07-01,4.20\n2026-07-02,.\n2026-07-03,4.31\n",
            "DGS10",
        )

        self.assertEqual(rows, [("2026-07-01", 4.20), ("2026-07-03", 4.31)])

    def test_global_linkage_snapshot_scores_external_pressure(self):
        snapshot = build_global_linkage_snapshot(
            [
                GlobalIndicator(
                    code="DGS10",
                    name="美国10年期国债收益率",
                    category="利率",
                    source="FRED",
                    latest_date="2026-07-03",
                    latest=4.30,
                    previous=4.20,
                    change=0.10,
                    unit="%",
                ),
                GlobalIndicator(
                    code="^VIX",
                    name="VIX波动率",
                    category="风险偏好",
                    source="yfinance",
                    latest_date="2026-07-03",
                    latest=26.0,
                    previous=23.0,
                    change=3.0,
                    change_pct=0.13,
                ),
                GlobalIndicator(
                    code="USDCNH=X",
                    name="美元/离岸人民币",
                    category="汇率",
                    source="yfinance",
                    latest_date="2026-07-03",
                    latest=7.35,
                    previous=7.30,
                    change=0.05,
                    change_pct=0.0068,
                ),
            ],
            as_of=datetime(2026, 7, 4, 9, 0),
        )

        self.assertEqual(snapshot.posture, "外部风险偏高")
        self.assertLess(snapshot.score, 0)
        self.assertTrue(any("VIX" in item for item in snapshot.signals))

    def test_render_global_linkage_report(self):
        snapshot = build_global_linkage_snapshot(
            [
                GlobalIndicator(
                    code="^IXIC",
                    name="纳斯达克",
                    category="美股",
                    source="yfinance",
                    latest_date="2026-07-03",
                    latest=18000,
                    previous=17800,
                    change=200,
                    change_pct=0.0112,
                )
            ],
            as_of=datetime(2026, 7, 4, 9, 0),
        )

        report = render_global_linkage_report(snapshot)

        self.assertIn("全球联动观察", report)
        self.assertIn("纳斯达克", report)
        self.assertIn("不直接构成买卖建议", report)

    def test_resolve_global_source_defaults_and_overrides(self):
        self.assertTrue(resolve_fred_series(""))
        self.assertTrue(resolve_yahoo_tickers(""))
        self.assertEqual(resolve_fred_series("DGS10")[0].name, "美国10年期国债收益率")
        self.assertEqual(resolve_yahoo_tickers("^GSPC")[0].name, "标普500")


class GlobalFeatureSourceTest(unittest.TestCase):
    def test_builds_model_feature_rows(self):
        rows = build_global_feature_rows(
            fred_points={
                "DGS10": [
                    ("2026-07-01", 4.20),
                    ("2026-07-02", 4.25),
                    ("2026-07-03", 4.30),
                ]
            },
            yahoo_points={
                "^IXIC": [
                    ("2026-07-01", 100.0),
                    ("2026-07-02", 102.0),
                    ("2026-07-03", 101.0),
                ]
            },
        )

        latest = rows[-1]

        self.assertEqual(latest["date"], "2026-07-03")
        self.assertEqual(latest["fred_dgs10"], 4.30)
        self.assertAlmostEqual(latest["fred_dgs10_diff_1d"], 0.05)
        self.assertEqual(latest["yf_ixic"], 101.0)
        self.assertAlmostEqual(latest["yf_ixic_return_1d"], 101.0 / 102.0 - 1)

    def test_write_global_feature_dataset_outputs_csv_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "global.csv"
            meta_path = Path(tmpdir) / "global.json"
            dataset = GlobalFeatureDataset(
                rows=[
                    {"date": "2026-07-02", "fred_dgs10": 4.25},
                    {"date": "2026-07-03", "fred_dgs10": 4.30, "fred_dgs10_diff_1d": 0.05},
                ],
                source_health=("FRED:DGS10 OK(2)",),
                generated_at=datetime(2026, 7, 4, 9, 0),
            )

            saved_csv, saved_meta = write_global_feature_dataset(dataset, out_path, meta_path)
            csv_text = saved_csv.read_text(encoding="utf-8")
            meta = json.loads(saved_meta.read_text(encoding="utf-8"))

        self.assertIn("date", csv_text)
        self.assertIn("fred_dgs10_diff_1d", csv_text)
        self.assertEqual(meta["rows"], 2)
        self.assertIn("avoid lookahead", meta["note"])

    def test_period_start_date_limits_default_feature_window(self):
        start = start_date_from_period("5y")

        self.assertIsNotNone(start)
        self.assertRegex(start, r"^\d{4}-\d{2}-\d{2}$")


class ThreeDayTradePlanTest(unittest.TestCase):
    def test_load_model_scores_normalizes_instrument_codes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "alpha_scores_latest.csv"
            path.write_text(
                "\n".join(
                    [
                        "trade_date,instrument,score,rank,pct_rank,model,feature_set,horizon_days,universe",
                        "2026-07-03,SH600938,0.1,1,0.93,lightgbm,Alpha158,5,csi300",
                        "2026-07-03,000001.SZ,0.1,2,1.20,lightgbm,Alpha158,5,csi300",
                        "2026-07-03,BJ430047,0.1,3,-0.10,lightgbm,Alpha158,5,csi300",
                    ]
                ),
                encoding="utf-8",
            )

            scores = load_model_scores(path)

        self.assertEqual(scores["600938"], 0.93)
        self.assertEqual(scores["000001"], 1.0)
        self.assertEqual(scores["430047"], 0.0)

    def test_model_score_coverage_reports_missing_and_stale_scores(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "alpha_scores_latest.csv"
            path.write_text(
                "\n".join(
                    [
                        "trade_date,instrument,score,rank,pct_rank,model,feature_set,horizon_days,universe",
                        "2026-06-26,SH600900,0.1,1,0.90,lightgbm,Alpha158,5,active_universe",
                    ]
                ),
                encoding="utf-8",
            )

            coverage = inspect_model_score_coverage(
                path,
                ["600900", "000963"],
                as_of=datetime(2026, 7, 6),
                max_stale_days=5,
            )

        self.assertEqual(coverage.trade_date, "2026-06-26")
        self.assertEqual(coverage.covered, 1)
        self.assertEqual(coverage.missing, ("000963",))
        self.assertTrue(coverage.stale)
        self.assertIn("未覆盖 000963", coverage.detail)

    def test_builds_three_day_plan_with_cash_and_lot_constraints(self):
        positions = [
            {
                "code": "600036",
                "name": "招商银行",
                "shares": 100,
                "cost": 36.89,
                "confirm": 36.80,
                "invalid": 35.28,
                "target": 39.23,
            },
            {
                "code": "600025",
                "name": "华能水电",
                "shares": 100,
                "entry_date": "2026-07-03",
                "cost": 9.24,
                "confirm": 9.17,
                "invalid": 8.79,
                "target": 9.78,
            },
        ]
        recommendations = [
            make_recommendation("600036", "招商银行", 36.83, 113, "条件执行"),
            make_recommendation("600030", "中信证券", 28.95, 134, "观察"),
            make_recommendation("600938", "中国海油", 27.69, 122, "条件执行", model_rank=0.93),
            make_recommendation("600028", "中国石化", 4.70, 115, "条件执行"),
            make_recommendation("601728", "中国电信", 5.43, 106, "观察"),
            make_recommendation("300308", "中际旭创", 1116.00, 93, "观察"),
        ]

        plan = build_three_day_trade_plan(
            recommendations,
            positions,
            capital=10000,
            top_n=3,
            model_scores={"600938": 0.93},
        )

        buy_codes = [item.code for item in plan.buy_plans]
        self.assertNotIn("600036", buy_codes)
        self.assertNotIn("300308", buy_codes)
        self.assertGreater(plan.rotation_cash, plan.available_cash)
        self.assertLessEqual(sum(item.lot_cost for item in plan.buy_plans), plan.rotation_cash)
        self.assertEqual(len(plan.buy_plans), 3)
        self.assertTrue(any(item.code == "600938" and item.model_pct_rank == 0.93 for item in plan.buy_plans))

    def test_trade_plan_uses_model_rank_from_recommendation_when_scores_not_passed(self):
        recommendation = make_recommendation(
            "600938",
            "中国海油",
            27.69,
            122,
            "条件执行",
            model_rank=0.88,
        )

        plan = build_three_day_trade_plan([recommendation], [], capital=10000, top_n=1)

        self.assertEqual(plan.buy_plans[0].model_pct_rank, 0.88)

    def test_trade_plan_uses_realtime_quote_for_lot_cost_and_execution_detail(self):
        recommendation = make_recommendation("600028", "中国石化", 30.00, 120, "条件执行")
        quote = RealtimeQuote(
            "600028",
            "中国石化",
            25.00,
            24.80,
            25.20,
            24.50,
            24.70,
            1.21,
            amount_billion=8.5,
            turnover_rate=1.8,
            pe_ratio=9.2,
            pb_ratio=0.9,
            market_cap_billion=500,
            limit_up_price=27.17,
            limit_down_price=22.23,
            quote_time=datetime(2026, 7, 7, 10, 0),
        )

        plan = build_three_day_trade_plan(
            [recommendation],
            [],
            capital=10000,
            top_n=1,
            realtime_quotes={"600028": quote},
        )
        report = render_three_day_trade_plan(plan)

        self.assertEqual(plan.buy_plans[0].close, 25.00)
        self.assertEqual(plan.buy_plans[0].lot_cost, 2500)
        self.assertEqual(plan.buy_plans[0].price_source, "实时(tencent)")
        self.assertIn("换手 1.80%", plan.buy_plans[0].execution_detail)
        self.assertIn("参考价 25.00(实时", report)

    def test_trade_plan_filters_candidate_when_realtime_one_lot_is_unaffordable(self):
        recommendation = make_recommendation("300548", "长芯博创", 20.00, 120, "条件执行")
        quote = RealtimeQuote("300548", "长芯博创", 120.00, 118, 122, 116, 118, 1.7)

        plan = build_three_day_trade_plan(
            [recommendation],
            [],
            capital=10000,
            top_n=1,
            realtime_quotes={"300548": quote},
        )

        self.assertEqual(plan.buy_plans, ())

    def test_probability_prediction_is_rendered_and_affects_tie_break(self):
        recommendations = [
            make_recommendation("600030", "中信证券", 28.95, 120, "条件执行", up_prob=0.47, avg_return=-0.004),
            make_recommendation("000963", "华东医药", 30.05, 120, "条件执行", up_prob=0.62, avg_return=0.018),
        ]

        plan = build_three_day_trade_plan(recommendations, [], capital=10000, top_n=1)
        report = render_three_day_trade_plan(plan)

        self.assertEqual(plan.buy_plans[0].code, "000963")
        self.assertEqual(plan.buy_plans[0].prediction_up_prob, 0.62)
        self.assertIn("概率预测", report)
        self.assertIn("未来3日上涨 62%", report)
        self.assertIn("期望收益 +1.80%", report)

    def test_trade_plan_can_exclude_energy_from_new_positions(self):
        recommendations = [
            make_recommendation("600938", "中国海油", 27.69, 180, "条件执行", model_rank=0.93),
            make_recommendation("600028", "中国石化", 4.70, 170, "条件执行"),
            make_recommendation("000963", "华东医药", 30.05, 118, "条件执行"),
            make_recommendation("600030", "中信证券", 28.95, 120, "观察"),
        ]

        plan = build_three_day_trade_plan(
            recommendations,
            [],
            capital=10000,
            top_n=3,
            excluded_groups=("能源",),
        )

        buy_codes = [item.code for item in plan.buy_plans]
        self.assertNotIn("600938", buy_codes)
        self.assertNotIn("600028", buy_codes)
        self.assertIn("000963", buy_codes)
        self.assertTrue(any("新增持仓暂不纳入: 能源" in note for note in plan.notes))

    def test_trade_plan_prefers_medical_candidates_when_scores_are_close(self):
        recommendations = [
            make_recommendation("000963", "华东医药", 30.05, 118, "条件执行"),
            make_recommendation("600030", "中信证券", 28.95, 125, "条件执行"),
        ]

        plan = build_three_day_trade_plan(
            recommendations,
            [],
            capital=10000,
            top_n=1,
            preferred_groups=("医药",),
        )

        self.assertEqual(plan.buy_plans[0].code, "000963")
        self.assertTrue(any("新增持仓优先观察: 医药" in note for note in plan.notes))

    def test_preferred_group_does_not_force_negative_expectancy_candidate(self):
        recommendations = [
            make_recommendation("000963", "华东医药", 30.05, 118, "条件执行", up_prob=0.40, avg_return=-0.012),
            make_recommendation("600030", "中信证券", 28.95, 125, "条件执行", up_prob=0.58, avg_return=0.010),
        ]

        plan = build_three_day_trade_plan(
            recommendations,
            [],
            capital=10000,
            top_n=1,
            preferred_groups=("医药",),
        )

        self.assertEqual(plan.buy_plans[0].code, "600030")

    def test_trade_plan_reserves_one_slot_for_preferred_group_when_available(self):
        recommendations = [
            make_recommendation("600900", "长江电力", 27.05, 162, "条件执行"),
            make_recommendation("600030", "中信证券", 28.95, 152, "观察"),
            make_recommendation("002415", "海康威视", 34.00, 142, "观察"),
            make_recommendation("000963", "华东医药", 30.05, 91, "条件执行"),
        ]

        plan = build_three_day_trade_plan(
            recommendations,
            [],
            capital=10000,
            top_n=3,
            preferred_groups=("医药",),
        )

        self.assertIn("000963", [item.code for item in plan.buy_plans])

    def test_infer_trade_group(self):
        self.assertEqual(infer_trade_group("招商银行"), "银行")
        self.assertEqual(infer_trade_group("华能水电"), "公用事业")
        self.assertEqual(infer_trade_group("中国海油"), "能源")
        self.assertEqual(infer_trade_group("中信证券"), "非银金融")
        self.assertEqual(infer_trade_group("中国太保"), "非银金融")
        self.assertEqual(infer_trade_group("君实生物-U"), "医药")

    def test_holding_with_low_elasticity_releases_capital(self):
        positions = [
            {
                "code": "600036",
                "name": "招商银行",
                "shares": 100,
                "cost": 36.89,
                "confirm": 36.80,
                "invalid": 35.28,
                "target": 39.23,
            }
        ]
        recommendation = Recommendation(
            code="600036",
            name="招商银行",
            score=90,
            status="条件执行",
            close=36.83,
            observation_zone="36.50-36.90",
            confirm_price=36.80,
            invalid_price=35.28,
            reason="测试",
            risk="注意: 短线弹性",
            candidate_score=90,
            take_profit_price=39.23,
        )

        plan = build_three_day_trade_plan([recommendation], positions, capital=10000, top_n=0)

        self.assertEqual(plan.holding_plans[0].action, "资金效率观察")
        self.assertIn("释放资金", plan.holding_plans[0].trigger)

    def test_holding_near_cost_and_confirm_uses_capital_efficiency_watch(self):
        positions = [
            {
                "code": "600025",
                "name": "华能水电",
                "shares": 100,
                "entry_date": "2026-07-03",
                "cost": 9.24,
                "confirm": 9.17,
                "invalid": 8.79,
                "target": 9.78,
            }
        ]
        recommendation = Recommendation(
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

        plan = build_three_day_trade_plan(
            [recommendation],
            positions,
            capital=10000,
            top_n=0,
            review_date=datetime(2026, 7, 6),
            trading_dates=["2026-07-03", "2026-07-06"],
        )

        self.assertEqual(plan.holding_plans[0].action, "资金效率观察")
        self.assertEqual(plan.holding_plans[0].entry_date, "2026-07-03")
        self.assertEqual(plan.holding_plans[0].holding_trade_days, 2)
        self.assertIn("轮动到更强候选", plan.holding_plans[0].trigger)

    def test_holding_day_three_without_progress_uses_time_stop(self):
        positions = [
            {
                "code": "600025",
                "name": "华能水电",
                "shares": 100,
                "entry_date": "2026-07-03",
                "cost": 9.24,
                "confirm": 9.17,
                "invalid": 8.79,
                "target": 9.78,
            }
        ]
        recommendation = Recommendation(
            code="600025",
            name="华能水电",
            score=98,
            status="条件执行",
            close=9.22,
            observation_zone="9.10-9.25",
            confirm_price=9.17,
            invalid_price=8.79,
            reason="测试",
            risk="-",
            candidate_score=98,
            take_profit_price=9.78,
        )

        plan = build_three_day_trade_plan(
            [recommendation],
            positions,
            capital=10000,
            top_n=0,
            review_date=datetime(2026, 7, 7),
            trading_dates=["2026-07-03"],
        )

        self.assertEqual(plan.holding_plans[0].action, "时间止损优先")
        self.assertEqual(plan.holding_plans[0].holding_trade_days, 3)
        self.assertIn("优先减仓或退出", plan.holding_plans[0].trigger)

    def test_low_model_holding_below_confirm_escalates_to_reduce(self):
        positions = [
            {
                "code": "600036",
                "name": "招商银行",
                "shares": 100,
                "entry_date": "2026-07-03",
                "cost": 36.89,
                "confirm": 36.80,
                "invalid": 35.28,
                "target": 39.23,
            }
        ]
        recommendation = Recommendation(
            code="600036",
            name="招商银行",
            score=90,
            status="条件执行",
            close=36.60,
            observation_zone="36.50-36.90",
            confirm_price=36.80,
            invalid_price=35.28,
            reason="测试",
            risk="-",
            candidate_score=90,
            take_profit_price=39.23,
            model_pct_rank=0.12,
        )

        plan = build_three_day_trade_plan(
            [recommendation],
            positions,
            capital=10000,
            top_n=0,
            model_scores={"600036": 0.12},
        )

        self.assertEqual(plan.holding_plans[0].action, "减仓优先")
        self.assertEqual(plan.holding_plans[0].model_pct_rank, 0.12)
        self.assertIn("模型分低于30%", plan.holding_plans[0].trigger)
        self.assertGreater(plan.rotation_cash, plan.available_cash)

    def test_weak_candidate_pool_pauses_new_buys(self):
        recommendations = [
            make_recommendation("600030", "中信证券", 28.95, 152, "失效"),
            make_recommendation("600900", "长江电力", 27.05, 148, "失效"),
            make_recommendation("601857", "中国石油", 9.05, 132, "排除"),
            make_recommendation("600025", "华能水电", 9.20, 118, "等待"),
            make_recommendation("002415", "海康威视", 34.00, 110, "观察"),
            make_recommendation("000963", "华东医药", 30.05, 108, "观察"),
        ]

        plan = build_three_day_trade_plan(recommendations, [], capital=10000, top_n=3)

        self.assertEqual(plan.risk_posture, "暂停新增")
        self.assertEqual(plan.rotation_cash, 0)
        self.assertEqual(plan.buy_plans, ())
        self.assertAlmostEqual(plan.candidate_failure_ratio, 0.5)
        self.assertTrue(any("暂停新增买入" in note for note in plan.notes))

    def test_stale_model_scores_reduce_new_buy_budget(self):
        coverage = ModelScoreCoverage(
            path="alpha_scores_latest.csv",
            exists=True,
            trade_date="2026-07-03",
            rows=800,
            covered=1,
            missing=(),
            stale=True,
            detail="道藏模型分数: 日期 2026-07-03，需要刷新。",
        )

        plan = build_three_day_trade_plan(
            [make_recommendation("000963", "华东医药", 30.05, 118, "条件执行")],
            [],
            capital=10000,
            top_n=1,
            model_coverage=coverage,
        )

        self.assertEqual(plan.risk_posture, "谨慎")
        self.assertAlmostEqual(plan.rotation_cash, 6000)
        self.assertAlmostEqual(plan.new_buy_budget_scale, 0.6)
        self.assertTrue(any("模型分偏旧" in note for note in plan.notes))


class DecisionLogTest(unittest.TestCase):
    def test_recommendation_decision_records_roundtrip(self):
        recommendation = make_recommendation("000963", "华东医药", 30.05, 92, "条件执行")
        records = build_recommendation_decision_records(
            [recommendation],
            as_of=datetime(2026, 7, 3, 23, 59, 59),
            run_kind="theme_screen",
            context={"theme": "创新药"},
            logged_at=datetime(2026, 7, 4, 18, 0, 0),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "decisions.jsonl"
            append_decision_records(records, path)
            loaded = read_decision_records(path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["schema_version"], "decision-log-v1")
        self.assertEqual(loaded[0]["code"], "000963")
        self.assertEqual(loaded[0]["action"], "watch_buy")
        self.assertEqual(loaded[0]["context"]["theme"], "创新药")
        self.assertEqual(loaded[0]["prices"]["confirm"], recommendation.confirm_price)
        self.assertIn("run_id", loaded[0])

    def test_trade_plan_logs_holdings_and_buy_candidates(self):
        positions = [
            {
                "code": "600036",
                "name": "招商银行",
                "shares": 100,
                "entry_date": "2026-07-03",
                "cost": 36.89,
                "confirm": 36.80,
                "invalid": 35.28,
                "target": 39.23,
            }
        ]
        recommendations = [
            make_recommendation("600036", "招商银行", 36.83, 113, "条件执行"),
            make_recommendation("600028", "中国石化", 4.70, 115, "条件执行", up_prob=0.58, avg_return=0.012),
        ]
        plan = build_three_day_trade_plan(
            recommendations,
            positions,
            capital=10000,
            top_n=1,
            model_scores={"600028": 0.61},
        )
        records = build_trade_plan_decision_records(
            plan,
            as_of=datetime(2026, 7, 3, 23, 59, 59),
            context={"command": "trade_plan"},
            logged_at=datetime(2026, 7, 4, 18, 0, 0),
        )

        kinds = {record["decision_kind"] for record in records}
        buy = next(record for record in records if record["decision_kind"] == "trade_plan_buy")

        self.assertEqual(kinds, {"holding_review", "trade_plan_buy"})
        self.assertEqual(buy["code"], "600028")
        self.assertEqual(buy["scores"]["model_pct_rank"], 0.61)
        self.assertTrue(buy["scores"]["prediction"]["available"])
        self.assertEqual(buy["scores"]["prediction"]["up_probability"], 0.58)
        self.assertIn("execution", buy)
        self.assertIn("execution_score", buy["execution"])
        self.assertEqual(buy["portfolio"]["available_cash"], plan.available_cash)
        holding = next(record for record in records if record["decision_kind"] == "holding_review")
        self.assertEqual(holding["sizing"]["entry_date"], "2026-07-03")


class ChatAdapterTest(unittest.TestCase):
    def test_chat_router_reads_positions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            positions_dir = root / "data/positions"
            positions_dir.mkdir(parents=True)
            (positions_dir / "current_positions.json").write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "code": "600036",
                                "name": "招商银行",
                                "shares": 100,
                                "cost": 36.89,
                                "confirm": 36.80,
                                "invalid": 35.28,
                                "target": 39.23,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            response = handle_chat_message(
                ChatMessage("持仓"),
                project_dir=root,
                quote_loader=lambda symbols: {
                    "600036": RealtimeQuote(
                        "600036",
                        "招商银行",
                        37.10,
                        36.80,
                        37.20,
                        36.60,
                        36.70,
                        1.09,
                        quote_time=datetime(2026, 7, 3, 10, 30),
                    )
                },
            )

        self.assertEqual(response.intent, "positions")
        self.assertIn("招商银行", response.text)
        self.assertIn("现价 37.10", response.text)
        self.assertIn("止损 35.28", response.text)

    def test_chat_router_recommendation_starts_fresh_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            launched = []

            response = handle_chat_message(
                ChatMessage("推荐 医疗行业的3支股票"),
                project_dir=root,
                recommendation_launcher=launched.append,
            )

            job_log = root / "data/runtime/chat_recommendation_jobs.jsonl"
            job_log_exists = job_log.exists()

        self.assertEqual(response.intent, "recommendation_job")
        self.assertIn("已启动最新推荐任务", response.text)
        self.assertEqual(len(launched), 1)
        self.assertIn("data/watchlists/innovation_drug_pool.txt", launched[0].command)
        self.assertIn("--notify", launched[0].command)
        self.assertIn("--realtime", launched[0].command)
        self.assertTrue(job_log_exists)

    def test_healthcheck_summarizes_position_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "positions.json"
            path.write_text(
                json.dumps(
                    {
                        "positions": [
                            {"code": "600036"},
                            {"code": "600025"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            count, detail = summarize_positions_file(path)

        self.assertEqual(count, 2)
        self.assertIn("600025", detail)

    def test_chat_router_strips_daocang_mention(self):
        response = handle_chat_message(ChatMessage("@daocang 帮助"))

        self.assertEqual(response.intent, "help")
        self.assertIn("daocang 飞书助手", response.text)

    def test_chat_router_summarizes_latest_trade_plan(self):
        recommendation = make_recommendation("000963", "华东医药", 30.05, 92, "条件执行")
        positions = [
            {
                "code": "600036",
                "name": "招商银行",
                "shares": 100,
                "entry_date": "2026-07-03",
                "cost": 36.89,
                "confirm": 36.80,
                "invalid": 35.28,
                "target": 39.23,
            }
        ]
        plan = build_three_day_trade_plan(
            [recommendation],
            positions,
            capital=10000,
            top_n=1,
            review_date=datetime(2026, 7, 6),
            trading_dates=["2026-07-03", "2026-07-06"],
        )
        records = build_trade_plan_decision_records(
            plan,
            as_of=datetime(2026, 7, 3, 23, 59, 59),
            context={"command": "trade_plan"},
            logged_at=datetime(2026, 7, 4, 18, 0, 0),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            append_decision_records(records, root / "data/decision_logs/recommendations.jsonl")
            response = handle_chat_message(ChatMessage("最新计划"), project_dir=root)

        self.assertEqual(response.intent, "trade_plan")
        self.assertIn("账户状态", response.text)
        self.assertIn("已有持仓处理", response.text)
        self.assertIn("招商银行", response.text)
        self.assertIn("新增候选和触发价", response.text)
        self.assertIn("华东医药", response.text)
        self.assertIn("确认", response.text)
        self.assertIn("模型未覆盖", response.text)
        self.assertIn("周一执行顺序", response.text)

    def test_chat_router_uses_latest_logged_at_when_trade_plan_run_id_repeats(self):
        context = {"command": "trade_plan"}
        as_of = datetime(2026, 7, 3, 23, 59, 59)
        old_plan = build_three_day_trade_plan(
            [make_recommendation("000963", "华东医药", 30.05, 92, "条件执行")],
            [],
            capital=10000,
            top_n=1,
        )
        new_plan = build_three_day_trade_plan(
            [make_recommendation("600900", "长江电力", 27.05, 156, "条件执行")],
            [],
            capital=10000,
            top_n=1,
        )
        old_records = build_trade_plan_decision_records(
            old_plan,
            as_of=as_of,
            context=context,
            logged_at=datetime(2026, 7, 5, 10, 0, 0),
        )
        new_records = build_trade_plan_decision_records(
            new_plan,
            as_of=as_of,
            context=context,
            logged_at=datetime(2026, 7, 5, 11, 0, 0),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "data/decision_logs/recommendations.jsonl"
            append_decision_records(old_records, path)
            append_decision_records(new_records, path)
            response = handle_chat_message(ChatMessage("最新计划"), project_dir=root)

        self.assertIn("长江电力", response.text)
        self.assertNotIn("华东医药", response.text)

    def test_feishu_event_adapter_handles_challenge(self):
        adapter = FeishuEventAdapter(webhook_sender=lambda text: {"code": 0})
        result = adapter.handle_event({"challenge": "abc"})

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.payload, {"challenge": "abc"})

    def test_feishu_event_adapter_returns_json_for_encrypted_payload_errors(self):
        adapter = FeishuEventAdapter(webhook_sender=lambda text: {"code": 0})
        result = adapter.handle_event({"encrypt": "not-a-valid-payload"})

        self.assertEqual(result.status_code, 400)
        self.assertIn("error", result.payload)

    def test_parse_decrypted_feishu_json_with_random_prefix(self):
        body = json.dumps({"challenge": "abc"}, ensure_ascii=False).encode("utf-8")
        payload = b"0123456789abcdef" + struct.pack(">I", len(body)) + body + b"app-id"

        self.assertEqual(parse_decrypted_feishu_json(payload), {"challenge": "abc"})

    def test_feishu_event_adapter_replies_to_text_message(self):
        sent: list[str] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FeishuEventAdapter(
                project_dir=tmpdir,
                verify_token="token",
                webhook_sender=lambda text: sent.append(text) or {"code": 0},
                allow_webhook_fallback=True,
            )
            result = adapter.handle_event(
                {
                    "header": {"token": "token"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_test"}},
                        "message": {
                            "message_id": "om_test",
                            "chat_id": "oc_test",
                            "message_type": "text",
                            "content": json.dumps({"text": "帮助"}, ensure_ascii=False),
                        },
                    },
                }
            )

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.response.intent, "help")
        self.assertEqual(len(sent), 1)
        self.assertIn("可用命令", sent[0])


def self_price_map():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "prices.csv"
        csv_path.write_text("\n".join(CSV_LINES), encoding="utf-8")
        return load_price_csv(csv_path)


def make_bars(code: str, name: str, closes: list[float]) -> list[Bar]:
    return [
        Bar(
            code=code,
            name=name,
            date=f"2026-07-{index:02d}",
            open=close,
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            volume=1000000,
            amount=close * 1000000,
        )
        for index, close in enumerate(closes, 1)
    ]


def make_recommendation(
    code: str,
    name: str,
    close: float,
    score: int,
    status: str,
    model_rank: float | None = None,
    up_prob: float | None = None,
    avg_return: float | None = None,
) -> Recommendation:
    return Recommendation(
        code=code,
        name=name,
        score=score,
        status=status,
        close=close,
        observation_zone=f"{close * 0.99:.2f}-{close:.2f}",
        confirm_price=round(close * 1.01, 2),
        invalid_price=round(close * 0.95, 2),
        reason="测试",
        risk="-",
        candidate_score=score,
        take_profit_price=round(close * 1.06, 2),
        model_pct_rank=model_rank,
        calibration_up_prob=up_prob,
        calibration_avg_return=avg_return,
        calibration_target_hit_prob=0.34 if up_prob is not None else None,
        calibration_stop_hit_prob=0.18 if up_prob is not None else None,
        calibration_median_return=avg_return,
        calibration_confidence="中" if up_prob is not None else "",
        calibration_sample_count=80 if up_prob is not None else 0,
        calibration_detail="历史校准：测试样本",
    )


def make_chat_candidate_record(
    code: str,
    name: str,
    score: int,
    status: str,
    confirm: float,
    stop: float,
    target: float,
) -> dict:
    return {
        "schema_version": "decision-log-v1",
        "run_id": "candidate_screen-20260703235959-test",
        "run_kind": "candidate_screen",
        "decision_kind": "candidate_recommendation",
        "logged_at": "2026-07-04T21:00:16",
        "as_of": "2026-07-03T23:59:59",
        "rank": 1,
        "code": code,
        "name": name,
        "action": "watch_buy",
        "status": status,
        "industry": "医药",
        "themes": ["医药", "创新药"],
        "prices": {
            "close": round(confirm / 1.01, 2),
            "confirm": confirm,
            "stop": stop,
            "target": target,
        },
        "scores": {
            "score": score,
            "candidate_score": score,
            "macro_event_score": 0,
        },
        "rationale": {
            "candidate_breakdown": f"测试候选+{score}",
            "reason": "测试候选",
        },
        "risk": {
            "risk_text": "测试风控",
            "stop": stop,
        },
        "outcome": {},
    }


def write_qlib_feature(path: Path, start_index: int, values: list[float]) -> None:
    path.write_bytes(struct.pack(f"<{len(values) + 1}f", float(start_index), *values))


class FakeBaostockQuery:
    fields = ["date", "code", "open", "high", "low", "close", "volume", "amount"]

    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows
        self.index = -1

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


if __name__ == "__main__":
    unittest.main()
