from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from daozang_alpha.config import ENV_PROVIDER_URI, load_config


class ConfigTests(TestCase):
    def test_load_config_reads_provider_uri(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "daozang.toml"
            path.write_text(
                """
[qlib]
provider_uri = "~/custom/cn_data"
region = "cn"

[dataset]
label_horizon_days = 10

[model]
objective = "lambdarank"
multi_label = true
ensemble = ["xgb", "catboost"]
""".strip(),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertEqual(config.qlib.provider_uri, "~/custom/cn_data")
        self.assertEqual(config.qlib.region, "cn")
        self.assertEqual(config.dataset.label_horizon_days, 10)
        self.assertEqual(config.model.objective, "lambdarank")
        self.assertTrue(config.model.multi_label)
        self.assertEqual(config.model.ensemble, ("xgb", "catboost"))

    def test_env_provider_uri_overrides_file(self) -> None:
        old_value = os.environ.get(ENV_PROVIDER_URI)
        os.environ[ENV_PROVIDER_URI] = "/tmp/qlib-cn"
        try:
            config = load_config(Path("/path/that/does/not/exist.toml"))
        finally:
            if old_value is None:
                os.environ.pop(ENV_PROVIDER_URI, None)
            else:
                os.environ[ENV_PROVIDER_URI] = old_value

        self.assertEqual(config.qlib.provider_uri, "/tmp/qlib-cn")
