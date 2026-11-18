"""Regressions for issues found in the live end-to-end test (0.9.0)."""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "travel-radar" / "scripts"))
os.environ.setdefault("TRAVEL_RADAR_HOME", tempfile.mkdtemp(prefix="travel-radar-test-"))

from tw import advise, config, reliability, scoring, sources, store, watches, wikidata  # noqa: E402

CFG = config.settings()
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


def opt(i, cost, **kw):
    return {"id": i, "est_cost_eur": cost, "climate": {"tmax_c": 24, "rain_days": 4}, "crowd_level": 2,
            "authenticity": 4, "max_transfer_h": 5, "safety_level": 1, **kw}


class RelativeCostTest(unittest.TestCase):
    TRIP = {"id": "t", "options": [opt("xinjiang", 2370, authenticity=5), opt("sichuan", 1700)]}

    def test_no_budget_scores_cost_relative_to_options(self):
        prof = {"weights": {"cost": 0.4}, "deferred": [{"key": "constraints.budget_total_eur"}]}
        r = scoring.score_trip(CFG, prof, self.TRIP)
        self.assertEqual(r["cost_mode"], "relative")
        by = {x["id"]: x for x in r["ranking"]}
        self.assertEqual(by["sichuan"]["components"]["cost"], 1.0)
        self.assertEqual(by["xinjiang"]["components"]["cost"], 0.0)
        self.assertEqual(r["ranking"][0]["id"], "sichuan")

    def test_budget_mode_when_known_and_trip_fact_wins(self):
        r = scoring.score_trip(CFG, {"constraints": {"budget_total_eur": 3000}}, self.TRIP)
        self.assertEqual((r["cost_mode"], r["budget_eur"]), ("budget", 3000))
        trip = {**self.TRIP, "facts": {"trip_budget_eur": {"value": 2000}}}
        r = scoring.score_trip(CFG, {"constraints": {"budget_total_eur": 3000}}, trip)
        self.assertEqual(r["budget_eur"], 2000)


class GeocodingTest(unittest.TestCase):
    HITS = [
        {"name": "Xiahe", "admin1": "Hebei", "admin2": "Xingtai", "country": "China", "country_code": "CN",
         "latitude": 38.7, "longitude": 114.7, "population": None},
        {"name": "Xiahe", "admin1": "Gansu", "admin2": "Gannan Tibetan Autonomous Prefecture", "country": "China",
         "country_code": "CN", "latitude": 34.85, "longitude": 103.75, "population": None},
        {"name": "Xiahe", "admin1": "Gansu", "admin2": "Dingxi Shi", "country": "China", "country_code": "CN",
         "latitude": 34.59, "longitude": 104.55, "population": None},
    ]

    def test_never_picks_silently(self):
        with self.assertRaises(sources.AmbiguousPlace) as cm:
            sources.pick_place(CFG, "Xiahe", self.HITS)
        self.assertEqual(len(cm.exception.candidates), 3)
        with self.assertRaises(sources.AmbiguousPlace):
            sources.pick_place(CFG, "Xiahe, Gansu", self.HITS)

    def test_region_or_prefecture_disambiguates(self):
        p = sources.pick_place(CFG, "Xiahe, Gannan", self.HITS)
        self.assertEqual((p["admin1"], p["resolution"]), ("Gansu", "unique"))
        p = sources.pick_place(CFG, "Xiahe", self.HITS, admin1="Hebei")
        self.assertEqual(p["admin1"], "Hebei")

    def test_dominant_population_is_reported_not_silent(self):
        hits = [{"name": "Paris", "admin1": "Île-de-France", "country": "France", "country_code": "FR",
                 "latitude": 48.85, "longitude": 2.35, "population": 2_100_000},
                {"name": "Paris", "admin1": "Texas", "country": "United States", "country_code": "US",
                 "latitude": 33.66, "longitude": -95.55, "population": 25_000}]
        p = sources.pick_place(CFG, "Paris", hits)
        self.assertEqual((p["country_code"], p["resolution"]), ("FR", "dominant_population"))
        self.assertTrue(p["alternatives"])


class GovernmentSitesTest(unittest.TestCase):
    def test_parent_hosts(self):
        self.assertEqual(wikidata.parent_hosts("adelaide.china-consulate.gov.cn", 3), ["china-consulate.gov.cn", "gov.cn"])
        self.assertEqual(wikidata.parent_hosts("www.esteri.it", 3), [])

    def test_subdomain_inherits_verified_government_parent(self):
        ents = {"gov.cn": {"Q59261": {"label": "State Council", "sites": {"https://www.gov.cn"}, "airline": False,
                                      "travel": False, "gov": True, "description": ""}}}
        with mock.patch.object(wikidata, "by_site", lambda cfg, d: [d]), \
             mock.patch.object(wikidata, "describe", lambda cfg, q: ents.get(q[0], {}) if q else {}):
            self.assertEqual(wikidata.government_parent(CFG, "adelaide.china-consulate.gov.cn")["parent"], "gov.cn")
            self.assertIsNone(wikidata.government_parent(CFG, "travel.fake-gov-cn.com"))

    def test_registry_down_is_fail_closed(self):
        def down(cfg, d):
            raise sources.SourceError("timeout")
        with mock.patch.object(wikidata, "by_site", down):
            with self.assertRaises(sources.SourceError):
                wikidata.government_parent(CFG, "adelaide.china-consulate.gov.cn")


class ProvisionalFaresTest(unittest.TestCase):
    W = {"id": "w1", "kind": "flight", "origin": ["PEK"], "destination": ["CTU"], "market": "domestic",
         "haul": "short", "peak": True, "depart_window": {"from": "2027-06-10"}, "budget_eur": 800}

    def test_horizon_by_market_and_flag(self):
        today = date(2026, 11, 18)
        self.assertEqual(reliability.fare_status(CFG, self.W, "2027-06-10", today=today), "provisional")
        self.assertEqual(reliability.fare_status(CFG, {**self.W, "market": "international"}, "2027-06-10", today=today), "normal")
        self.assertEqual(reliability.fare_status(CFG, {**self.W, "market": "international"}, "2027-06-10", True, today), "provisional")

    def test_advise_and_alerts_ignore_provisional(self):
        con = store.connect(":memory:")
        store.add_observation(con, "w1", 691, "s", "https://www.kayak.it/f", ts=NOW.isoformat(), tier=2,
                              extractor="main", fare_status="provisional")
        self.assertEqual(watches.evaluate(CFG, con, self.W, NOW), [])
        r = advise.advise(CFG, con, self.W, NOW)
        self.assertEqual(r["verified_observations"], 0)
        self.assertTrue(any("provisional" in x for x in r["reasons"]))
        self.assertEqual(len(store.observations(con, "w1", include_provisional=True)), 1)


if __name__ == "__main__":
    unittest.main()
