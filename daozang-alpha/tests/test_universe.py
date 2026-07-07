import csv
import json
import struct
import tempfile
import unittest
from datetime import date
from pathlib import Path

from daozang_alpha.universe import (
    SyncUniverseOptions,
    extract_ths_page_count,
    extract_ths_stock_rows,
    merge_risk_events,
    read_universe_instruments,
    release_record_to_risk_event,
    report_record_to_risk_event,
    sync_beichen_universe,
    write_risk_calendar,
)


class UniverseSyncTest(unittest.TestCase):
    def test_sync_beichen_universe_prioritizes_positions_and_fills_to_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            beichen = root / "beichen-alpha"
            (beichen / "data/positions").mkdir(parents=True)
            (beichen / "data/watchlists").mkdir(parents=True)
            (beichen / "data/cache").mkdir(parents=True)
            (beichen / "data/positions/current_positions.json").write_text(
                json.dumps(
                    {
                        "positions": [
                            {"code": "600036", "name": "招商银行"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (beichen / "data/watchlists/broad_target_pool_2026-07-03.txt").write_text(
                "600900 # 长江电力 | candidate 100 | 条件执行 | 公用事业\n000963 # 华东医药\n",
                encoding="utf-8",
            )
            cache = beichen / "data/cache/universe_latest.jsonl"
            cache.write_text(
                "\n".join(
                    [
                        json.dumps({"code": "600036", "name": "招商银行"}, ensure_ascii=False),
                        json.dumps({"code": "600030", "name": "中信证券"}, ensure_ascii=False),
                        json.dumps({"code": "300750", "name": "宁德时代"}, ensure_ascii=False),
                    ]
                ),
                encoding="utf-8",
            )
            out = root / "daozang-alpha/data/universe/active_universe.csv"

            artifacts = sync_beichen_universe(
                SyncUniverseOptions(
                    beichen_root=beichen,
                    output_path=out,
                    limit=4,
                    watchlists=("data/watchlists/broad_target_pool_2026-07-03.txt",),
                )
            )
            with out.open(encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            instruments = read_universe_instruments(out)

        self.assertEqual(artifacts.rows, 4)
        self.assertEqual([row["code"] for row in rows], ["600036", "600900", "000963", "600030"])
        self.assertEqual(rows[1]["name"], "长江电力")
        self.assertEqual(rows[0]["instrument"], "SH600036")
        self.assertEqual(rows[2]["instrument"], "SZ000963")
        self.assertEqual(instruments, ["SH600036", "SH600900", "SZ000963", "SH600030"])

    def test_sync_beichen_universe_infers_missing_industry_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            beichen = root / "beichen-alpha"
            (beichen / "data/cache").mkdir(parents=True)
            (beichen / "data/cache/universe_latest.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"code": "688180", "name": "君实生物-U"}, ensure_ascii=False),
                        json.dumps({"code": "002916", "name": "深南电路"}, ensure_ascii=False),
                    ]
                ),
                encoding="utf-8",
            )
            out = root / "active_universe.csv"

            sync_beichen_universe(
                SyncUniverseOptions(
                    beichen_root=beichen,
                    output_path=out,
                    limit=2,
                    watchlists=(),
                    industry_map=root / "missing_industry_map.csv",
                )
            )
            with out.open(encoding="utf-8") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(rows[0]["industry"], "医药")
        self.assertIn("创新药", rows[0]["themes"])
        self.assertEqual(rows[1]["industry"], "AI硬件")
        self.assertIn("先进制造", rows[1]["themes"])

    def test_sync_beichen_universe_prefers_industry_map(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            beichen = root / "beichen-alpha"
            (beichen / "data/cache").mkdir(parents=True)
            (beichen / "data/cache/universe_latest.jsonl").write_text(
                json.dumps(
                    {
                        "code": "600036",
                        "name": "招商银行",
                        "industry": "银行",
                        "themes": ["防御"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            industry_map = root / "akshare_industry_map.csv"
            industry_map.write_text(
                "\n".join(
                    [
                        "code,name,industry,themes,industry_source",
                        "600036,招商银行,股份制银行,金融稳定;高股息,akshare_em_industry",
                    ]
                ),
                encoding="utf-8",
            )
            out = root / "active_universe.csv"

            sync_beichen_universe(
                SyncUniverseOptions(
                    beichen_root=beichen,
                    output_path=out,
                    limit=1,
                    watchlists=(),
                    industry_map=industry_map,
                )
            )
            with out.open(encoding="utf-8") as file:
                row = next(csv.DictReader(file))

        self.assertEqual(row["industry"], "股份制银行")
        self.assertEqual(row["industry_source"], "akshare_em_industry")
        self.assertIn("金融稳定", row["themes"])
        self.assertIn("高股息", row["themes"])

    def test_sync_beichen_universe_merges_risk_calendar_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            beichen = root / "beichen-alpha"
            (beichen / "data/cache").mkdir(parents=True)
            (beichen / "data/cache/universe_latest.jsonl").write_text(
                json.dumps({"code": "600036", "name": "招商银行"}, ensure_ascii=False),
                encoding="utf-8",
            )
            risk_path = root / "risk_calendar.csv"
            write_risk_calendar(
                [
                    {
                        "code": "600036",
                        "name": "招商银行",
                        "risk_tags": "财报窗口;财报披露变更",
                        "risk_source": "巨潮财报预约披露",
                        "risk_detail": "2026半年报 预约披露 2026-08-15，10天后",
                        "event_date": "2026-08-15",
                        "severity": "0.7",
                        "hard_exclude": "0",
                    }
                ],
                risk_path,
            )
            out = root / "active_universe.csv"

            sync_beichen_universe(
                SyncUniverseOptions(
                    beichen_root=beichen,
                    output_path=out,
                    limit=1,
                    watchlists=(),
                    risk_calendar=risk_path,
                )
            )
            with out.open(encoding="utf-8") as file:
                row = next(csv.DictReader(file))

        self.assertIn("财报窗口", row["risk_tags"])
        self.assertEqual(row["risk_source"], "巨潮财报预约披露")
        self.assertIn("2026半年报", row["risk_detail"])

    def test_sync_beichen_universe_enriches_qlib_liquidity_metrics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            beichen = root / "beichen-alpha"
            qlib = root / "qlib"
            (beichen / "data/cache").mkdir(parents=True)
            (qlib / "calendars").mkdir(parents=True)
            feature_dir = qlib / "features/sh600036"
            feature_dir.mkdir(parents=True)
            (qlib / "calendars/day.txt").write_text(
                "\n".join(f"2026-01-{day:02d}" for day in range(1, 31)),
                encoding="utf-8",
            )
            write_qlib_bin(feature_dir / "close.day.bin", 0, [float(day) for day in range(1, 31)])
            write_qlib_bin(feature_dir / "amount.day.bin", 0, [float(day * 100) for day in range(1, 31)])
            (beichen / "data/cache/universe_latest.jsonl").write_text(
                json.dumps(
                    {
                        "code": "600036",
                        "name": "招商银行",
                        "latest": 36.6,
                        "turnover": 3442080000,
                        "market_cap_billion": 9230.46,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            out = root / "active_universe.csv"

            sync_beichen_universe(
                SyncUniverseOptions(
                    beichen_root=beichen,
                    output_path=out,
                    limit=1,
                    watchlists=(),
                    qlib_data_dir=qlib,
                )
            )
            with out.open(encoding="utf-8") as file:
                row = next(csv.DictReader(file))

        self.assertEqual(row["data_start_date"], "2026-01-01")
        self.assertEqual(row["data_end_date"], "2026-01-30")
        self.assertEqual(row["history_days"], "30")
        self.assertEqual(row["amount_5d_avg"], "2800")
        self.assertEqual(row["amount_20d_avg"], "2050")
        self.assertTrue(float(row["volatility_20d"]) > 0)

    def test_extract_ths_stock_rows_and_page_count(self):
        html = """
        <table class="m-table m-pager-table">
          <tbody>
            <tr><td>1</td><td>688270</td><td>臻镭科技</td></tr>
            <tr><td>2</td><td>300223</td><td>北京君正</td></tr>
          </tbody>
        </table>
        <span class="page_info">1/9</span>
        """

        rows = extract_ths_stock_rows(html)

        self.assertEqual(extract_ths_page_count(html), 9)
        self.assertEqual(rows, [{"code": "688270", "name": "臻镭科技"}, {"code": "300223", "name": "北京君正"}])

    def test_release_record_to_risk_event_marks_hard_large_release(self):
        event = release_record_to_risk_event(
            {
                "股票代码": "688001",
                "股票简称": "华兴源创",
                "解禁时间": "2026-07-08",
                "实际解禁市值": 300000000,
                "占解禁前流通市值比例": 0.025,
            },
            date(2026, 7, 5),
            60,
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertTrue(event["hard_exclude"])
        self.assertIn("解禁硬风险", event["risk_tags"])
        self.assertIn("大额解禁", event["risk_tags"])

    def test_report_record_to_risk_event_marks_earnings_window(self):
        event = report_record_to_risk_event(
            {
                "股票代码": "600036",
                "股票简称": "招商银行",
                "首次预约": "2026-08-15",
                "初次变更": "2026-08-14",
                "实际披露": "",
            },
            date(2026, 8, 1),
            30,
            "2026半年报",
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertIn("财报窗口", event["risk_tags"])
        self.assertIn("财报披露变更", event["risk_tags"])
        self.assertEqual(event["risk_source"], "巨潮财报预约披露")

    def test_merge_risk_events_combines_tags(self):
        merged = merge_risk_events(
            [
                {
                    "code": "600036",
                    "name": "招商银行",
                    "risk_tags": ("解禁窗口",),
                    "risk_source": "东方财富限售解禁",
                    "risk_detail": "release",
                    "event_date": "2026-07-08",
                    "severity": 0.4,
                    "hard_exclude": False,
                }
            ],
            [
                {
                    "code": "600036",
                    "name": "招商银行",
                    "risk_tags": ("财报窗口",),
                    "risk_source": "巨潮财报预约披露",
                    "risk_detail": "report",
                    "event_date": "2026-08-15",
                    "severity": 0.7,
                    "hard_exclude": False,
                }
            ],
        )

        self.assertIn("解禁窗口", merged["600036"]["risk_tags"])
        self.assertIn("财报窗口", merged["600036"]["risk_tags"])
        self.assertIn("东方财富限售解禁", merged["600036"]["risk_source"])
        self.assertIn("巨潮财报预约披露", merged["600036"]["risk_source"])

def write_qlib_bin(path: Path, start_index: int, values: list[float]) -> None:
    payload = [float(start_index), *values]
    path.write_bytes(struct.pack("<" + "f" * len(payload), *payload))


if __name__ == "__main__":
    unittest.main()
