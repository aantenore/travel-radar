import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "travel-radar" / "scripts"))

from tw import (config, experiences, export, geopolitics, knowledge, profile, requirements,  # noqa: E402
                schema, scoring, store, trips)

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
PROF = {"traveller": {"nationality": "IT"}}
TODAY = NOW.date()
ACC = TODAY.isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("TRAVEL_RADAR_HOME")
        self.home = Path(tempfile.mkdtemp(prefix="travel-radar-ctx-"))
        os.environ["TRAVEL_RADAR_HOME"] = str(self.home)
        self.cfg = config.settings()
        config.init(self.cfg)
        self.con = store.connect(":memory:")
        # runtime source verdicts, as `tw sources verify` would cache them
        for dom, gov in (("gov.example", True), ("embassy.example", True), ("news-a.example", False),
                         ("news-b.example", False), ("museum.example", False)):
            knowledge.put(self.con, "source", dom, {"tier": 0, "official_site": True, "government": gov},
                          ["https://www.wikidata.org/wiki/Q1"], 30, "wikidata", now=NOW)
        trips.save(self.cfg, {"id": "t1", "status": "chosen", "country": "XX", "chosen_option": "a", "options": [
            {"id": "a", "name": "North", "places": ["P1", "P2"], "period": {"from": "2027-06-05", "to": "2027-06-20"},
             "est_cost_eur": 2000, "climate": {"tmax_c": 24, "rain_days": 4}, "crowd_level": 2, "authenticity": 4,
             "max_transfer_h": 5, "safety_level": 1},
            {"id": "b", "name": "South", "places": ["P3"], "period": {"from": "2027-06-05", "to": "2027-06-20"},
             "est_cost_eur": 2000, "climate": {"tmax_c": 24, "rain_days": 4}, "crowd_level": 2, "authenticity": 4,
             "max_transfer_h": 5, "safety_level": 1}]})

    def tearDown(self):
        if self._old is not None:
            os.environ["TRAVEL_RADAR_HOME"] = self._old

    def src(self, *domains):
        return [{"url": f"https://www.{d}/page", "accessed": ACC} for d in domains]


class RequirementsTest(Base):
    def test_required_from_unofficial_only_becomes_unknown(self):
        r = requirements.add(self.cfg, self.con, "t1", profile=PROF, items=[
            {"id": "visa", "category": "entry", "status": "required", "action": "Get a visa", "lead_time_days": 30,
             "sources": self.src("news-a.example")}], now=NOW)
        self.assertEqual(r["downgraded"][0]["id"], "visa")
        self.assertEqual(requirements.load(self.cfg, "t1")[0]["status"], "unknown")

    def test_deadline_computed_and_lead_time_required(self):
        with self.assertRaises(schema.ValidationError):
            requirements.add(self.cfg, self.con, "t1", profile=PROF, items=[
                {"id": "visa", "category": "entry", "status": "required", "action": "Get a visa",
                 "sources": self.src("gov.example")}], now=NOW)
        with self.assertRaises(schema.ValidationError):
            requirements.add(self.cfg, self.con, "t1", profile=PROF, items=[
                {"id": "x", "category": "entry", "status": "required", "action": "x", "lead_time_days": 3, "sources": []}], now=NOW)
        requirements.add(self.cfg, self.con, "t1", profile=PROF, items=[
            {"id": "visa", "category": "entry", "status": "required", "action": "Get a visa", "lead_time_days": 30,
             "sources": self.src("gov.example")},
            {"id": "permit", "category": "permits", "status": "required", "action": "Area permit", "lead_time_days": 60,
             "applies_to": {"options": ["a"]}, "sources": self.src("embassy.example")}], now=NOW)
        margin = self.cfg["requirements"]["safety_margin_days"]
        items = {i["id"]: i for i in requirements.load(self.cfg, "t1")}
        self.assertEqual(items["visa"]["deadlines"]["chosen"], date.fromordinal(date(2027, 6, 5).toordinal() - 30 - margin).isoformat())
        c = requirements.check(self.cfg, "t1", TODAY)
        self.assertEqual([d["id"] for d in c["deadlines"]], ["permit", "visa"])     # sorted by date
        self.assertEqual(c["blockers"], [])
        late = requirements.check(self.cfg, "t1", date(2027, 5, 1))
        self.assertEqual({b["id"] for b in late["blockers"]}, {"visa", "permit"})
        self.assertEqual({s["id"] for s in late["stale"]}, {"visa", "permit"})

    def test_nationality_required_for_dependent_categories(self):
        requirements.add(self.cfg, self.con, "t1", profile={}, items=[
            {"id": "visa", "category": "entry", "status": "required", "action": "Get a visa", "lead_time_days": 30,
             "sources": self.src("gov.example")},
            {"id": "drone", "category": "drones", "status": "required", "action": "Drone permit", "lead_time_days": 10,
             "sources": self.src("gov.example")}], now=NOW)
        items = {i["id"]: i for i in requirements.load(self.cfg, "t1")}
        self.assertEqual((items["visa"]["status"], items["visa"]["assumed_nationality"]), ("unknown", True))
        self.assertEqual(items["drone"]["status"], "required")          # not nationality-dependent
        c = requirements.check(self.cfg, "t1", TODAY, profile={})
        self.assertEqual(c["ask"], ["traveller.nationality"])

    def test_nationality_change_makes_items_stale(self):
        requirements.add(self.cfg, self.con, "t1", profile=PROF, items=[
            {"id": "visa", "category": "entry", "status": "required", "action": "Get a visa", "lead_time_days": 30,
             "sources": self.src("gov.example")}], now=NOW)
        c = requirements.check(self.cfg, "t1", TODAY, profile={"traveller": {"nationality": "FR"}})
        self.assertIn("nationality", c["stale"][0]["reason"])

    def test_deadlines_grouped_by_item(self):
        requirements.add(self.cfg, self.con, "t1", profile=PROF, items=[
            {"id": "visa", "category": "entry", "status": "required", "action": "Get a visa", "lead_time_days": 30,
             "applies_to": {"options": ["a", "b"]}, "sources": self.src("gov.example")}], now=NOW)
        d = requirements.check(self.cfg, "t1", TODAY, profile=PROF)["deadlines"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["options"], ["a", "b"])

    def test_infeasible_required_permit_excludes_option(self):
        requirements.add(self.cfg, self.con, "t1", profile=PROF, items=[
            {"id": "permit", "category": "permits", "status": "required", "action": "Border-zone permit",
             "lead_time_days": 45, "feasible_in_window": False, "applies_to": {"options": ["a"]},
             "sources": self.src("gov.example")}], now=NOW)
        res = scoring.score_trip(self.cfg, {}, trips.load(self.cfg, "t1"), reqs=requirements.load(self.cfg, "t1"))
        by = {r["id"]: r for r in res["ranking"]}
        self.assertIn("not feasible", by["a"]["excluded"])
        self.assertIsNone(by["b"]["excluded"])


class GeopoliticsTest(Base):
    def item(self, sev, *domains, **kw):
        return {"id": f"g-{sev}", "topic": "Tensions", "scope": {"country": "XX"}, "severity": sev, "trend": "stable",
                "impact": ["movement"], "what_to_do": "Avoid border areas", "sources": self.src(*domains), **kw}

    def test_single_source_cannot_be_warning_and_blocker_needs_official(self):
        r = geopolitics.add(self.cfg, self.con, "t1", [self.item("warning", "news-a.example")], now=NOW)
        self.assertEqual(geopolitics.load(self.cfg, "t1")[0]["severity"], "watch")
        self.assertTrue(r["adjusted"])
        geopolitics.add(self.cfg, self.con, "t1", [self.item("blocker", "news-a.example", "news-b.example")], now=NOW)
        self.assertEqual({i["id"]: i["severity"] for i in geopolitics.load(self.cfg, "t1")}["g-blocker"], "warning")

    def test_advisory_normalization_from_source_scale(self):
        self.assertEqual(geopolitics.normalize_level(4, 4), 4)
        self.assertEqual(geopolitics.normalize_level(1, 4), 1)
        self.assertEqual(geopolitics.normalize_level(3, 5), 3)
        self.assertEqual(geopolitics.normalize_level(5, 5), 4)
        with self.assertRaises(schema.ValidationError):
            geopolitics.normalize_level(6, 5)
        geopolitics.add(self.cfg, self.con, "t1", [self.item("info", "gov.example", advisory={
            "level": 3, "scale_max": 4, "source": {"url": "https://www.gov.example/advice", "accessed": ACC}})], now=NOW)
        self.assertEqual(geopolitics.load(self.cfg, "t1")[0]["safety_level"], 3)
        self.assertEqual(geopolitics.check(self.cfg, "t1", TODAY)["safety_level"], 3)

    def test_ttl_and_scoring(self):
        geopolitics.add(self.cfg, self.con, "t1", [
            self.item("blocker", "gov.example", "news-a.example", scope={"places": ["P3"]})], now=NOW)
        self.assertEqual(geopolitics.check(self.cfg, "t1", TODAY)["stale"], [])
        self.assertEqual(geopolitics.check(self.cfg, "t1", date(2026, 11, 30))["stale"], ["g-blocker"])
        res = scoring.score_trip(self.cfg, {}, trips.load(self.cfg, "t1"), geo=geopolitics.load(self.cfg, "t1"))
        by = {r["id"]: r for r in res["ranking"]}
        self.assertIn("geopolitics", by["b"]["excluded"])
        self.assertIsNone(by["a"]["excluded"])


class ExperiencesTest(Base):
    KEYS = set(schema.PROFILE_FIELDS)

    def exp(self, **kw):
        e = {"id": "e1", "name": "Tea workshop", "place": "P1", "option": "a",
             "when": {"from": "2027-06-08", "to": "2027-06-08"},
             "fits_profile": [{"field": "travel_style.prefer_local", "reason": "run by a local family"}],
             "booking": {"method": "online_link", "url": "https://www.museum.example/book", "lead_time_days": 10},
             "sources": self.src("museum.example")}
        e.update(kw)
        return e

    def test_rules(self):
        bad = [self.exp(booking={"method": "online_link"}), self.exp(sources=[]),
               self.exp(when={"from": "2027-07-08"}), self.exp(booking={"method": "on_site"}),
               self.exp(price={"amount": 20, "currency": "EUR"}), self.exp(fits_profile=[{"field": "nonsense", "reason": "x"}])]
        for e in bad:
            with self.subTest(e=e), self.assertRaises(schema.ValidationError):
                experiences.add(self.cfg, self.con, "t1", [e], self.KEYS, now=NOW)

    def test_link_verification_and_export_deadlines(self):
        r = experiences.add(self.cfg, self.con, "t1", [
            self.exp(), self.exp(id="e2", name="Night market", booking={
                "method": "online_link", "url": "https://random-tickets.example/x", "lead_time_days": 3})], self.KEYS, now=NOW)
        self.assertEqual(r["unverified_links"], ["e2"])
        requirements.add(self.cfg, self.con, "t1", profile=PROF, items=[
            {"id": "visa", "category": "entry", "status": "required", "action": "Apply for visa", "lead_time_days": 30,
             "sources": self.src("gov.example")}], now=NOW)
        cal = export.ics(self.cfg, trips.load(self.cfg, "t1"), now=NOW)
        self.assertIn("SUMMARY:Book: Tea workshop", cal)
        self.assertIn("DTSTART;VALUE=DATE:20270529", cal)          # 2027-06-08 - 10 days
        self.assertIn("SUMMARY:Deadline: Apply for visa", cal)
        md = export.brief(trips.load(self.cfg, "t1"), None, [], requirements.load(self.cfg, "t1"),
                          [], experiences.load(self.cfg, "t1"))
        self.assertIn("(unverified link)", md)
        self.assertIn("Visas, permits", md)


class IntakeTest(Base):
    def test_gaps_ranked_by_config_weights_and_skip_known_or_deferred(self):
        prof = {"constraints": {"origin_airports": ["FCO"]}}
        g = profile.gaps(self.cfg, prof, trips.load(self.cfg, "t1"), "plan")
        keys = [x["key"] for x in g["all_missing"]]
        self.assertNotIn("constraints.origin_airports", keys)
        self.assertEqual(keys[0], "trip.trip_budget_eur")
        self.assertLessEqual(len(g["ask_now"]), self.cfg["intake"]["max_questions"])
        profile.defer(prof, "trip.trip_budget_eur", "decidi tu")
        trips.fact(self.cfg, "t1", "dates_flexibility_days", 3, "posso spostarmi di 3 giorni")
        keys = [x["key"] for x in profile.gaps(self.cfg, prof, trips.load(self.cfg, "t1"), "plan")["all_missing"]]
        self.assertNotIn("trip.trip_budget_eur", keys)
        self.assertNotIn("trip.dates_flexibility_days", keys)

    def test_equivalent_trip_and_profile_keys_are_asked_once(self):
        g = profile.gaps(self.cfg, {}, trips.load(self.cfg, "t1"), "plan")
        keys = [x["key"] for x in g["ask_now"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn("trip.trip_budget_eur", keys)
        self.assertNotIn("constraints.budget_total_eur", [x["key"] for x in g["all_missing"]])
        g = profile.gaps(self.cfg, {"constraints": {"budget_total_eur": 3000}}, trips.load(self.cfg, "t1"), "plan")
        self.assertNotIn("trip.trip_budget_eur", [x["key"] for x in g["all_missing"]])
        g = profile.gaps(self.cfg, {}, None, "plan")          # no trip: the profile question remains
        self.assertIn("constraints.budget_total_eur", [x["key"] for x in g["all_missing"]])

    def test_trip_specific_vs_profile_routing(self):
        with self.assertRaises(schema.ValidationError) as cm:
            profile.set_value({}, "dates_flexibility_days", 3, "x")
        self.assertIn("tw trip fact", cm.exception.errors[0])
        with self.assertRaises(schema.ValidationError):
            trips.fact(self.cfg, "t1", "nationality", "IT", "sono italiano")
        p = {}
        profile.set_value(p, "traveller.nationality", "IT", "sono italiano")
        profile.set_value(p, "interests", ["food", "crafts"], "adoro cibo e artigianato")
        self.assertEqual((p["traveller"]["nationality"], p["interests"]), ("IT", ["food", "crafts"]))


if __name__ == "__main__":
    unittest.main()
