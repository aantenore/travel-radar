import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "travel-radar" / "scripts"))
os.environ["TRAVEL_RADAR_HOME"] = tempfile.mkdtemp(prefix="travel-radar-test-")
from datetime import date, datetime, timedelta, timezone

from tw import config, profile, schema, store, watches

CFG = config.settings()
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


class SchemaTest(unittest.TestCase):
    def test_trip_metric_without_source_rejected(self):
        t = {"id": "t", "options": [{"id": "a", "est_cost_eur": 1000, "crowd_level": 7}]}
        with self.assertRaises(schema.ValidationError) as cm:
            schema.trip(t)
        msg = " ".join(cm.exception.errors)
        self.assertIn("crowd_level", msg)
        self.assertIn("sources.est_cost_eur", msg)

    def test_valid_trip(self):
        schema.trip({"id": "t", "options": [{"id": "a", "est_cost_eur": 1000, "sources": {"est_cost_eur": ["u"]}}]})

    def test_watch_requires_window_and_route(self):
        with self.assertRaises(schema.ValidationError):
            schema.watch({"id": "w", "kind": "flight"})
        schema.watch({"id": "w", "kind": "safety", "depart_window": {"from": "2027-04-01"}})


class ProfileTest(unittest.TestCase):
    def test_set_records_evidence_and_previous(self):
        p = {"avoid": {"temp_max_c": None}}
        profile.set_value(p, "avoid.temp_max_c", 30, "I hate the heat", today=date(2026, 11, 18))
        self.assertEqual(p["avoid"]["temp_max_c"], 30)
        self.assertEqual(p["learned"][0]["evidence"], "I hate the heat")
        self.assertIsNone(p["learned"][0]["previous"])

    def test_set_rejects_bad_input(self):
        for key, val, ev in [("avoid.foo", 1, "x"), ("avoid.temp_max_c", "hot", "x"),
                             ("avoid.crowds", 1, "x"), ("avoid.crowds", True, " ")]:
            with self.assertRaises(schema.ValidationError):
                profile.set_value({}, key, val, ev)


class SuspectPriceTest(unittest.TestCase):
    def test_implausible_drop_flags_suspect_only(self):
        con = store.connect(":memory:")
        w = {"id": "w1", "budget_eur": 500, "depart_window": {"from": "2027-04-05"}}
        for i, p in enumerate([700, 690, 710, 90]):
            store.add_observation(con, "w1", p, "s", "http://x", ts=(NOW - timedelta(hours=4 - i)).isoformat())
        kinds = [a["kind"] for a in watches.evaluate(CFG, con, w, NOW)]
        self.assertEqual(kinds, ["suspect"])


if __name__ == "__main__":
    unittest.main()
