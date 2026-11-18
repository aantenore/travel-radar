import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "travel-radar" / "scripts"))
os.environ["TRAVEL_RADAR_HOME"] = tempfile.mkdtemp(prefix="travel-radar-test-")
from datetime import date, datetime, timedelta, timezone

from tw import config, dossier, scoring, store, watches

CFG = config.settings()


class ScoringTest(unittest.TestCase):
    def test_conforming_option_scores_high(self):
        prof = {"constraints": {"budget_total_eur": 3000}, "travel_style": {"prefer_local": True}}
        opt = {"id": "a", "est_cost_eur": 2000, "climate": {"tmax_c": 22, "rain_days": 4},
               "crowd_level": 1, "authenticity": 5, "max_transfer_h": 4, "safety_level": 1}
        r = scoring.score_option(CFG, prof, opt)
        self.assertEqual(r["score"], 100.0)
        self.assertEqual(r["conflicts"], [])

    def test_conflicts_reported(self):
        prof = {"constraints": {"budget_total_eur": 1500}, "avoid": {"crowds": True, "temp_max_c": 30},
                "travel_style": {"prefer_local": True}}
        opt = {"id": "b", "est_cost_eur": 1800, "climate": {"tmax_c": 35, "rain_days": 15},
               "crowd_level": 5, "authenticity": 1, "max_transfer_h": 12, "safety_level": 1}
        r = scoring.score_option(CFG, prof, opt)
        self.assertEqual(len(r["conflicts"]), 6)
        self.assertLess(r["score"], 30)

    def test_safety_excludes(self):
        r = scoring.score_option(CFG, {}, {"id": "c", "safety_level": 4, "crowd_level": 1})
        self.assertIsNone(r["score"])
        self.assertIn("safety", r["excluded"])
        self.assertIn("constraints.max_safety_level", r["profile_defaults_used"])

    def test_missing_fields_listed(self):
        r = scoring.score_option(CFG, {}, {"id": "d"})
        self.assertIn("est_cost_eur", r["missing"])
        self.assertIsNone(r["score"])


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.con = store.connect(":memory:")
        self.w = {"id": "w1", "budget_eur": 500, "depart_window": {"from": "2027-04-05"}}

    def test_cadence_by_distance(self):
        self.assertEqual(watches.per_day(CFG, self.w, date(2026, 11, 18))[0], 1)
        self.assertEqual(watches.per_day(CFG, self.w, date(2027, 3, 1))[0], 2)
        self.assertEqual(watches.per_day(CFG, self.w, date(2027, 4, 1))[0], 4)

    def test_due_respects_interval(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        self.assertTrue(watches.due(CFG, self.con, [self.w], now)[0]["due"])
        store.add_check(self.con, "w1", "ok", ts=(now - timedelta(hours=2)).isoformat())
        self.assertFalse(watches.due(CFG, self.con, [self.w], now)[0]["due"])

    def test_alerts_need_confirmation_and_respect_cooldown(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        x, y = "https://www.kayak.it/flights/FCO-PEK", "https://www.skyscanner.it/x"

        def obs(p, url, h, extractor="fare-reader"):
            store.add_observation(self.con, "w1", p, "s", url, ts=(now + timedelta(hours=h)).isoformat(),
                                  tier=2, extractor=extractor)

        for i, p in enumerate([700, 690, 710, 600]):
            obs(p, x, i - 4)
        res = watches.evaluate(CFG, self.con, self.w, now)
        self.assertEqual({a["kind"] for a in res}, {"new_low", "drop"})
        self.assertEqual({a["status"] for a in res}, {"pending_confirmation"})
        obs(600, x, 0)  # the same extractor re-reading the same site does not confirm
        self.assertEqual({a["status"] for a in watches.evaluate(CFG, self.con, self.w, now)}, {"pending_confirmation"})
        obs(600, x, 0, extractor="main")  # re-read by the main model confirms
        res = watches.evaluate(CFG, self.con, self.w, now)
        self.assertEqual({a["kind"] for a in res}, {"new_low", "drop"})
        self.assertEqual({a["status"] for a in res}, {"confirmed"})
        self.assertEqual(watches.evaluate(CFG, self.con, self.w, now + timedelta(hours=1)), [])
        obs(450, x, 2)
        res = watches.evaluate(CFG, self.con, self.w, now + timedelta(hours=2))
        self.assertEqual([(a["kind"], a["status"]) for a in res], [("below_budget", "pending_confirmation")])
        obs(460, y, 3)  # another tier 2 site within 5%
        res = watches.evaluate(CFG, self.con, self.w, now + timedelta(hours=3))
        self.assertEqual([(a["kind"], a["status"]) for a in res], [("below_budget", "confirmed")])

    def test_observation_requires_url(self):
        with self.assertRaises(ValueError):
            store.add_observation(self.con, "w1", 100, "s", "")


class DossierTest(unittest.TestCase):
    def test_issues_and_blockers(self):
        checklist = config.load_yaml(config.path(CFG, "dossier_checklist"))
        d = {"items": {
            "safety": {"status": "blocker", "summary": "conflict", "sources": [{"url": "u", "accessed": "2026-11-01"}]},
            "entry": {"status": "ok", "sources": []},
        }}
        r = dossier.check(checklist, d, today=date(2026, 11, 18))
        problems = {(i["item"], i["problem"].split(" ")[0]) for i in r["issues"]}
        self.assertIn(("safety", "stale"), problems)
        self.assertIn(("entry", "no"), problems)
        self.assertIn(("climate", "missing"), problems)
        self.assertEqual(r["blockers"][0]["item"], "safety")
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
