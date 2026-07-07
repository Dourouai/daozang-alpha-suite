from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from beichen_alpha.data_prewarm import combine_factor_rows, upsert_csv_rows, write_snapshot_json


class DataPrewarmTest(unittest.TestCase):
    def test_write_snapshot_json_normalizes_datetime(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            write_snapshot_json(path, {"at": datetime(2026, 7, 7, 9, 30), "items": [1]})
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["at"], "2026-07-07T09:30:00")
        self.assertEqual(payload["items"], [1])

    def test_upsert_csv_rows_replaces_existing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.csv"
            upsert_csv_rows(path, [{"date": "2026-07-07", "code": "600036", "x": 1}], ("date", "code"))
            upsert_csv_rows(path, [{"date": "2026-07-07", "code": "600036", "x": 2}], ("date", "code"))
            text = path.read_text(encoding="utf-8")
        self.assertIn("600036", text)
        self.assertIn(",2", text)
        self.assertNotIn(",1", text)

    def test_upsert_csv_rows_merges_existing_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "combined.csv"
            upsert_csv_rows(
                path,
                [{"date": "2026-07-07", "code": "600036", "flow": 1}],
                ("date", "code"),
            )
            upsert_csv_rows(
                path,
                [{"date": "2026-07-07", "code": "600036", "sentiment": 2}],
                ("date", "code"),
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn("flow", text)
        self.assertIn("sentiment", text)
        self.assertIn(",1,", text)
        self.assertIn(",2", text)

    def test_combine_factor_rows_adds_global_columns(self):
        rows = combine_factor_rows(
            [{"date": "2026-07-07", "code": "600036", "flow_main_net_inflow_10k": 10}],
            [{"date": "2026-07-07", "code": "600036", "sentiment_zt_count": 0}],
            {"date": "2026-07-07", "global_score": -3},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["global_score"], -3)
        self.assertEqual(rows[0]["sentiment_zt_count"], 0)


if __name__ == "__main__":
    unittest.main()
