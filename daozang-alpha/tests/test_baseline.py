from __future__ import annotations

from unittest import TestCase
from tempfile import TemporaryDirectory
from pathlib import Path

import pandas as pd

from daozang_alpha.baseline import _join_extra_features, _latest_scores, _load_extra_feature_frame


class BaselineExportShapeTests(TestCase):
    def test_latest_scores_exports_multi_horizon_columns_with_3d_primary_rank(self) -> None:
        index = pd.MultiIndex.from_product(
            [pd.to_datetime(["2026-07-06"]), ["SH600000", "SZ000001"]],
            names=["datetime", "instrument"],
        )
        predictions = pd.DataFrame(
            {
                "score_1d": [0.01, 0.02],
                "score_3d": [0.03, 0.01],
                "score_5d": [0.02, 0.04],
                "expected_return_1d": [0.004, -0.001],
                "up_probability_1d": [0.56, 0.48],
                "expected_return_3d": [0.011, -0.002],
                "up_probability_3d": [0.62, 0.47],
                "expected_return_5d": [0.018, -0.004],
                "up_probability_5d": [0.65, 0.45],
            },
            index=index,
        )

        latest = _latest_scores(predictions, top_n=2)

        self.assertEqual(latest.iloc[0]["instrument"], "SH600000")
        self.assertEqual(latest.iloc[0]["score"], latest.iloc[0]["score_3d"])
        self.assertEqual(latest.iloc[0]["pct_rank"], latest.iloc[0]["pct_rank_3d"])
        self.assertIn("score_1d", latest.columns)
        self.assertIn("score_3d", latest.columns)
        self.assertIn("score_5d", latest.columns)
        self.assertIn("expected_return_1d", latest.columns)
        self.assertIn("up_probability_1d", latest.columns)
        self.assertIn("expected_return_3d", latest.columns)
        self.assertIn("up_probability_3d", latest.columns)
        self.assertIn("expected_return_5d", latest.columns)
        self.assertIn("up_probability_5d", latest.columns)

    def test_extra_features_are_loaded_and_joined_by_date_and_instrument(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "beichen_features.csv"
            path.write_text(
                "\n".join(
                    [
                        "trade_date,instrument,beichen_policy_score,beichen_flow_score,name",
                        "2026-07-06,SH600000,8,-2,浦发银行",
                    ]
                ),
                encoding="utf-8",
            )

            extra = _load_extra_feature_frame(path)
            index = pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2026-07-06"), "SH600000"), (pd.Timestamp("2026-07-06"), "SZ000001")],
                names=["datetime", "instrument"],
            )
            base = pd.DataFrame({"alpha": [1.0, 2.0]}, index=index)

            joined = _join_extra_features(base, extra)

            self.assertIn("beichen_policy_score", joined.columns)
            self.assertEqual(joined.loc[(pd.Timestamp("2026-07-06"), "SH600000"), "beichen_policy_score"], 8)
            self.assertEqual(joined.loc[(pd.Timestamp("2026-07-06"), "SZ000001"), "beichen_policy_score"], 0)
