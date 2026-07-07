from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from daozang_alpha.beichen_features import (
    ExportBeichenFeaturesOptions,
    export_beichen_features,
)


class BeichenFeatureExportTests(TestCase):
    def test_exports_structured_factor_scores_to_daily_features(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "beichen-alpha"
            log_path = root / "data/decision_logs/recommendations.jsonl"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-06T15:00:00",
                        "logged_at": "2026-07-06T15:01:00",
                        "run_kind": "candidate_screen",
                        "code": "600036",
                        "name": "招商银行",
                        "factor_scores": [
                            {"name": "政策关键词", "score": 8, "passed": True},
                            {"name": "主力资金", "score": -6, "passed": False},
                            {"name": "公告风险", "score": -180, "passed": False},
                            {"name": "板块启动", "score": 12, "passed": True},
                            {"name": "预期透支", "score": -26, "passed": False},
                        ],
                        "scores": {"candidate_score": 123, "model_pct_rank": 0.72},
                        "execution": {"execution_score": 41},
                        "final_action": {"action": "BUY_WATCH"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            output = Path(tmp) / "features.csv"

            artifacts = export_beichen_features(
                ExportBeichenFeaturesOptions(beichen_root=root, output_path=output)
            )

            self.assertEqual(artifacts.rows, 1)
            text = output.read_text(encoding="utf-8")
            self.assertIn("beichen_policy_score", text)
            self.assertIn("SH600036", text)
            self.assertIn("-180.0", text)
            self.assertIn("-26.0", text)

    def test_falls_back_to_candidate_breakdown_groups(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "beichen-alpha"
            log_path = root / "data/decision_logs/recommendations.jsonl"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-06T15:00:00",
                        "run_kind": "candidate_screen",
                        "code": "300750",
                        "name": "宁德时代",
                        "rationale": {
                            "candidate_breakdown": "宏观事件+10 资金博弈-4 板块生命周期+12 预期定价-8"
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            output = Path(tmp) / "features.csv"

            export_beichen_features(
                ExportBeichenFeaturesOptions(beichen_root=root, output_path=output)
            )

            text = output.read_text(encoding="utf-8")
            self.assertIn("SZ300750", text)
            self.assertIn("10.0", text)
            self.assertIn("-8.0", text)
