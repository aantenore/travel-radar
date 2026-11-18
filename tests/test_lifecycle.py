import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "travel-radar" / "scripts"))

import yaml  # noqa: E402

from tw import config, export, ideas, schema, store, trips, watches  # noqa: E402

TODAY = date(2026, 11, 18)


class Base(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("TRAVEL_RADAR_HOME")
        self.home = Path(tempfile.mkdtemp(prefix="travel-radar-life-"))
        os.environ["TRAVEL_RADAR_HOME"] = str(self.home)
        self.cfg = config.settings()
        config.init(self.cfg)
        self.con = store.connect(":memory:")

    def tearDown(self):
        if self._old is not None:
            os.environ["TRAVEL_RADAR_HOME"] = self._old

    def make_trip(self, status="monitoring"):
        trips.save(self.cfg, {
            "id": "t1", "request": "China in June", "status": status, "chosen_option": "a",
            "options": [{"id": "a", "name": "North", "places": ["Beijing", "Xi'an"],
                         "period": {"from": "2027-06-05", "to": "2027-06-20"}, "est_cost_eur": 2500,
                         "sources": {"est_cost_eur": ["https://example.org/c"]}}],
            "deadlines": [{"date": "2027-04-20", "title": "Apply for visa", "source": "https://example.org/v"}],
        })
        w = {"id": "w1", "kind": "flight", "trip": "t1", "origin": ["FCO"], "destination": ["PEK"],
             "depart_window": {"from": "2027-06-01"}, "budget_eur": 800, "active": True}
        (self.home / "watches" / "w1.yaml").write_text(yaml.safe_dump(w))
        d = {"items": {"safety": {"status": "ok", "sources": [{"url": "u", "accessed": TODAY.isoformat()}]}}}
        (self.home / "trips" / "t1" / "dossier.yaml").write_text(yaml.safe_dump(d))

    def watch(self):
        return config.load_yaml(self.home / "watches" / "w1.yaml")


class TransitionTest(Base):
    def test_valid_and_invalid_transitions(self):
        trips.save(self.cfg, {"id": "t2", "status": "planning", "options": [{"id": "a"}]})
        with self.assertRaises(schema.ValidationError):
            trips.set_status(self.cfg, "t2", "monitoring")
        with self.assertRaises(schema.ValidationError):
            trips.set_status(self.cfg, "t2", "chosen", option="zzz")
        trips.set_status(self.cfg, "t2", "chosen", option="a")
        with self.assertRaises(schema.ValidationError):
            trips.set_status(self.cfg, "t2", "monitoring")          # no user yes
        trips.set_status(self.cfg, "t2", "monitoring", confirmed_by_user="yes, monitor it")
        self.assertEqual(trips.load(self.cfg, "t2")["monitoring_confirmed_by_user"]["evidence"], "yes, monitor it")
        trips.done(self.cfg, "t2")
        with self.assertRaises(schema.ValidationError):
            trips.set_status(self.cfg, "t2", "planning")


class ParkResumeTest(Base):
    def test_park_pauses_watches_and_resume_reports_changes(self):
        self.make_trip()
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        store.add_observation(self.con, "w1", 900, "s", "http://x", ts=now.isoformat())
        trips.park(self.cfg, self.con, "t1", "not now", until="2027-02-01", today=TODAY)
        self.assertFalse(self.watch()["active"])
        self.assertEqual(trips.load(self.cfg, "t1")["status"], "parked")
        with self.assertRaises(schema.ValidationError):
            trips.set_status(self.cfg, "t1", "monitoring")

        self.assertEqual(trips.reminders(self.cfg, date(2027, 1, 31)), [])
        self.assertEqual(trips.reminders(self.cfg, date(2027, 2, 1))[0]["trip"], "t1")

        store.add_observation(self.con, "w1", 720, "s", "http://y", ts=(now + timedelta(days=60)).isoformat())
        preview = trips.resume(self.cfg, self.con, "t1", today=TODAY + timedelta(days=60))
        self.assertTrue(preview["preview"])
        self.assertEqual(preview["watches_to_restart"], ["w1"])
        self.assertFalse(self.watch()["active"])
        self.assertEqual(trips.load(self.cfg, "t1")["status"], "parked")
        with self.assertRaises(schema.ValidationError):
            trips.resume(self.cfg, self.con, "t1", today=TODAY, confirmed_by_user="  ")
        diff = trips.resume(self.cfg, self.con, "t1", today=TODAY + timedelta(days=60), confirmed_by_user="ok riprendilo")
        self.assertTrue(self.watch()["active"])
        self.assertEqual(self.watch()["confirmed_by_user"]["evidence"], "ok riprendilo")
        self.assertNotIn("paused_by_trip", self.watch())
        self.assertEqual(diff["status"], "monitoring")
        self.assertEqual(diff["prices"]["w1"]["change_pct"], -20.0)
        self.assertIn("safety", diff["reverify_now"])
        self.assertEqual(trips.load(self.cfg, "t1")["history"][0]["reason"], "not now")

    def test_park_rejects_past_until(self):
        self.make_trip()
        with self.assertRaises(schema.ValidationError):
            trips.park(self.cfg, self.con, "t1", "x", until="2026-01-01", today=TODAY)

    def test_done_stops_watches_and_returns_debrief(self):
        self.make_trip()
        r = trips.done(self.cfg, "t1")
        self.assertEqual(r["stopped_watches"], ["w1"])
        self.assertFalse(self.watch()["active"])
        self.assertTrue(r["debrief_topics"])


class WatchConfirmationTest(Base):
    def draft(self, **kw):
        w = {"id": "w9", "kind": "flight", "trip": "t1", "origin": ["FCO"], "destination": ["PEK"],
             "depart_window": {"from": "2027-06-01"}, **kw}
        f = self.home / "trips" / "draft-w9.yaml"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(yaml.safe_dump(w))
        return f

    def test_unconfirmed_watch_is_invalid_and_never_due(self):
        (self.home / "watches" / "w9.yaml").write_text(self.draft().read_text())
        invalid = []
        self.assertEqual(watches.load_all(self.cfg, invalid), [])
        self.assertIn("not confirmed by the user", invalid[0]["errors"][0])

    def test_add_requires_and_stores_the_users_yes(self):
        with self.assertRaises(schema.ValidationError):
            watches.add(self.cfg, self.draft(), "")
        r = watches.add(self.cfg, self.draft(), "sì, solo i voli", today=TODAY)
        self.assertTrue(r["created"])
        w = watches.load_all(self.cfg)[0]
        self.assertEqual((w["id"], w["confirmed_by_user"]["evidence"]), ("w9", "sì, solo i voli"))
        self.assertTrue(watches.add(self.cfg, self.draft(budget_eur=700), "sì")["updated"])


class IdeasTest(Base):
    def test_season_flags_and_due(self):
        self.assertEqual(ideas.season("12,1", date(2026, 11, 18), 3)["months_to_best"], 1)
        self.assertTrue(ideas.season("11", date(2026, 11, 18), 3)["in_season"])
        self.assertFalse(ideas.season("6", date(2026, 11, 18), 3)["approaching"])
        with self.assertRaises(schema.ValidationError):
            ideas.add(self.con, "x", "13")
        i = ideas.add(self.con, "Patagonia", "11,12,1,2")
        self.assertEqual(len(ideas.due(self.cfg, self.con)), 1)
        ideas.checked(self.con, i["id"], "no deal")
        self.assertEqual(ideas.due(self.cfg, self.con), [])

    def test_promote_creates_planning_trip(self):
        i = ideas.add(self.con, "Patagonia")
        ideas.promote(self.cfg, self.con, i["id"], "patagonia")
        t = trips.load(self.cfg, "patagonia")
        self.assertEqual((t["status"], t["request"]), ("planning", "Patagonia"))
        with self.assertRaises(schema.ValidationError):
            ideas.promote(self.cfg, self.con, i["id"], "again")


class ExportTest(Base):
    def test_ics_and_brief(self):
        self.make_trip()
        trip = trips.load(self.cfg, "t1")
        cal = export.ics(self.cfg, trip, now=datetime(2026, 11, 18, tzinfo=timezone.utc))
        self.assertIn("DTSTART;VALUE=DATE:20270605", cal)
        self.assertIn("DTEND;VALUE=DATE:20270621", cal)
        self.assertIn("SUMMARY:Deadline: Apply for visa", cal)
        self.assertIn("DTSTART;VALUE=DATE:20270522", cal)   # 14 days before departure
        self.assertTrue(all(len(line.encode()) <= 75 for line in cal.split("\r\n")))
        md = export.brief(trip, trips.load_dossier(self.cfg, "t1"), [])
        self.assertIn("2027-06-05 to 2027-06-20 (16 days)", md)
        self.assertIn("Apply for visa", md)

    def test_export_requires_chosen_option(self):
        trips.save(self.cfg, {"id": "t3", "status": "planning", "options": []})
        with self.assertRaises(schema.ValidationError):
            export.ics(self.cfg, trips.load(self.cfg, "t3"))


if __name__ == "__main__":
    unittest.main()
