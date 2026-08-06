"""统一配置入口的最小自检。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from settings import load_settings  # noqa: E402


class SettingsTest(unittest.TestCase):
    def test_grouped_config_and_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps({
                "mineru": {"api_url": "http://mineru:8000", "timeout_seconds": 30},
                "runtime": {"runs_root": "runs", "log_level": "WARNING"},
            }), encoding="utf-8")
            with patch.dict(os.environ, {"MINERU_API_URL": "http://127.0.0.1:9000"}, clear=False):
                settings = load_settings(path)
            self.assertEqual(settings["mineru"]["api_url"], "http://127.0.0.1:9000")
            self.assertEqual(settings["workflow"]["extract_workers"], 3)
            self.assertEqual(settings["runtime"]["runs_root"], str((path.parent / "runs").resolve()))


if __name__ == "__main__":
    unittest.main()
