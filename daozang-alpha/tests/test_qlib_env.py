from __future__ import annotations

from tempfile import TemporaryDirectory
from unittest import TestCase

from daozang_alpha.config import DatasetConfig, QlibConfig, DaozangConfig
from daozang_alpha.qlib_env import check_environment


class QlibEnvTests(TestCase):
    def test_provider_shape_detects_expected_dirs(self) -> None:
        with TemporaryDirectory() as tmp:
            import pathlib

            root = pathlib.Path(tmp)
            for name in ["calendars", "features", "instruments"]:
                (root / name).mkdir()

            config = DaozangConfig(
                qlib=QlibConfig(provider_uri=str(root), region="cn"),
                dataset=DatasetConfig(label_horizon_days=5),
            )
            results = check_environment(config)

        by_name = {item.name: item for item in results}
        self.assertTrue(by_name["provider path"].ok)
        self.assertTrue(by_name["provider shape"].ok)
        self.assertTrue(by_name["label horizon"].ok)
