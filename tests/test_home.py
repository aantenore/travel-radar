import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "travel-radar" / "scripts"))

from tw import config  # noqa: E402


class DataHomeTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("TRAVEL_RADAR_HOME")
        self.home = Path(tempfile.mkdtemp(prefix="travel-radar-home-"))
        os.environ["TRAVEL_RADAR_HOME"] = str(self.home)

    def tearDown(self):
        if self._old is not None:
            os.environ["TRAVEL_RADAR_HOME"] = self._old

    def test_init_creates_home_once(self):
        cfg = config.settings()
        first = config.init(cfg)
        self.assertTrue((self.home / "profile.yaml").exists())
        self.assertTrue((self.home / "watches").is_dir())
        self.assertIn(str(self.home / "profile.yaml"), first["created"])
        (self.home / "profile.yaml").write_text("travel_style: {party: solo}\n")
        self.assertEqual(config.init(cfg)["created"], [])
        self.assertEqual(config.profile(cfg)["travel_style"]["party"], "solo")

    def test_user_settings_override_and_plugin_checklist(self):
        (self.home / "settings.yaml").write_text("alerts: {drop_pct: 25}\n")
        cfg = config.settings()
        self.assertEqual(cfg["alerts"]["drop_pct"], 25)
        self.assertEqual(cfg["alerts"]["cooldown_h"], 24)
        self.assertEqual(config.path(cfg, "dossier_checklist").parent, config.PLUGIN_CONFIG)


if __name__ == "__main__":
    unittest.main()
