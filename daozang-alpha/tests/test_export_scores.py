import csv
import tempfile
import unittest
from pathlib import Path

from daozang_alpha.config import DaozangConfig, DatasetConfig, ExportConfig, QlibConfig
from daozang_alpha.export_scores import ExportScoresOptions, export_scores, normalize_instrument


class ExportScoresTest(unittest.TestCase):
    def test_export_scores_writes_canonical_latest_without_label(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "alpha_scores_20260704_120000.csv"
            source.write_text(
                "\n".join(
                    [
                        (
                            "trade_date,instrument,score,label,rank,pct_rank,model,"
                            "feature_set,horizon_days,universe"
                        ),
                        "2026-07-03,600938,0.12,0.03,1,1.0,lightgbm,Alpha158,5,csi300",
                    ]
                ),
                encoding="utf-8",
            )
            config = DaozangConfig(
                qlib=QlibConfig(),
                dataset=DatasetConfig(),
                export=ExportConfig(path=str(root), reports_path=str(root / "reports")),
            )

            artifacts = export_scores(config, ExportScoresOptions(input_path=source))
            with artifacts.output_path.open(encoding="utf-8") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(artifacts.rows, 1)
        self.assertEqual(rows[0]["instrument"], "SH600938")
        self.assertNotIn("label", rows[0])
        self.assertEqual(rows[0]["pct_rank"], "1.0")

    def test_export_scores_preserves_multi_horizon_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "alpha_scores_20260707_120000.csv"
            source.write_text(
                "\n".join(
                    [
                        (
                            "trade_date,instrument,score,rank,pct_rank,model,feature_set,"
                            "horizon_days,universe,score_1d,score_3d,score_5d,"
                            "pct_rank_1d,pct_rank_3d,pct_rank_5d,"
                            "expected_return_3d,up_probability_3d"
                        ),
                        (
                            "2026-07-06,688167,0.031,1,0.98,lightgbm,Alpha158,"
                            "3,active_universe,0.011,0.031,0.026,"
                            "0.90,0.98,0.94,0.012,0.63"
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            config = DaozangConfig(
                qlib=QlibConfig(),
                dataset=DatasetConfig(),
                export=ExportConfig(path=str(root), reports_path=str(root / "reports")),
            )

            artifacts = export_scores(config, ExportScoresOptions(input_path=source))
            with artifacts.output_path.open(encoding="utf-8") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(artifacts.rows, 1)
        self.assertEqual(rows[0]["instrument"], "SH688167")
        self.assertEqual(rows[0]["score_3d"], "0.031")
        self.assertEqual(rows[0]["pct_rank_3d"], "0.98")
        self.assertEqual(rows[0]["expected_return_3d"], "0.012")
        self.assertEqual(rows[0]["up_probability_3d"], "0.63")

    def test_normalize_instrument_accepts_common_a_share_formats(self):
        self.assertEqual(normalize_instrument("600938"), "SH600938")
        self.assertEqual(normalize_instrument("000001.SZ"), "SZ000001")
        self.assertEqual(normalize_instrument("BJ430047"), "BJ430047")


if __name__ == "__main__":
    unittest.main()
