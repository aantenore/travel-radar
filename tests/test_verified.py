import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "travel-radar" / "scripts"))
os.environ.setdefault("TRAVEL_RADAR_HOME", tempfile.mkdtemp(prefix="travel-radar-test-"))

from tw import advise, config, reliability, schema, store, wikidata  # noqa: E402

CFG = config.settings()
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
W = {"id": "w1", "kind": "flight", "origin": ["FCO"], "destination": ["PEK"], "haul": "long", "peak": True,
     "depart_window": {"from": "2027-06-01", "to": "2027-06-10"}, "trip_length_days": [14, 21], "budget_eur": 800}


class DomainHandlingTest(unittest.TestCase):
    """Tiers are runtime verdicts; here only the deterministic domain handling is tested."""

    def test_www_case_and_scheme_ignored(self):
        for url in ("https://ita-airways.com/x", "https://www.ita-airways.com/x", "https://WWW.ITA-AIRWAYS.COM/x",
                    "ita-airways.com/x"):
            with self.subTest(url=url):
                self.assertEqual(wikidata.registrable(reliability.host(url)), "ita-airways.com")

    def test_regional_subdomains_and_cc_suffixes(self):
        self.assertEqual(wikidata.registrable("it.ceair.com"), "ceair.com")
        self.assertEqual(wikidata.registrable("www.airchina.com.cn"), "airchina.com.cn")
        self.assertEqual(wikidata.match_domain("www.airchina.it", ["http://www.airchina.com/"]), "brand")

    def test_lookalike_domains_do_not_match(self):
        for d in ("notceair.com", "ceair.com.evil.net", "evil-saudia.com"):
            with self.subTest(d=d):
                self.assertIsNone(wikidata.match_domain(d, ["https://www.ceair.com", "https://www.saudia.com"]))


class RecordRulesTest(unittest.TestCase):
    def check(self, url, dep, ret, watch=W, price=612, excerpt="FCO-PEK 612 € round trip", extractor="fare-reader",
              tier=2):
        return reliability.check_flight_observation(CFG, watch, url, dep, ret, price, excerpt, extractor, source_tier=tier)

    def test_accepts_tiered_source_with_exact_dates(self):
        self.assertEqual(self.check("https://www.kayak.it/flights/FCO-PEK", "2027-06-03", "2027-06-20"), 2)

    def test_rejects(self):
        with self.assertRaises(schema.ValidationError):   # unverified source (tier 0) = context only
            self.check("https://someblog.com/x", "2027-06-03", "2027-06-20", tier=0)
        cases = [
            ("https://www.kayak.it/f", None, "2027-06-20"),             # no depart
            ("https://www.kayak.it/f", "2027-06-03", None),             # no return on round trip
            ("https://www.kayak.it/f", "2027-07-03", "2027-07-20"),     # outside window
            ("https://www.kayak.it/f", "2027-06-03", "2027-06-05"),     # too short
            ("https://www.kayak.it/f", "2027-06-03", "2027-06-02"),     # return before depart
        ]
        for c in cases:
            with self.subTest(c=c), self.assertRaises(schema.ValidationError):
                self.check(*c)

    def test_excerpt_and_extractor_required(self):
        url, d, r = "https://www.kayak.it/f", "2027-06-03", "2027-06-20"
        for kw in ({"excerpt": "FCO-PEK 621 €"}, {"excerpt": None}, {"extractor": None}):
            with self.subTest(kw=kw), self.assertRaises(schema.ValidationError):
                self.check(url, d, r, **kw)

    def test_one_way_needs_no_return(self):
        self.assertEqual(self.check("https://www.ryanair.com/x", "2027-06-03", None, {**W, "one_way": True}, tier=1), 1)

    def test_tier_column_migrates_old_db(self):
        path = Path(tempfile.mkdtemp()) / "old.sqlite"
        import sqlite3
        con = sqlite3.connect(str(path))
        con.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY, watch_id TEXT NOT NULL, ts TEXT NOT NULL,"
                    " price_eur REAL NOT NULL, depart TEXT, return_date TEXT, carrier TEXT, stops INTEGER,"
                    " source TEXT NOT NULL, url TEXT NOT NULL, note TEXT)")
        con.commit(); con.close()
        con = store.connect(path)
        store.add_observation(con, "w1", 500, "s", "u", tier=1)
        self.assertEqual(store.observations(con, "w1")[0]["tier"], 1)


class AdviseTest(unittest.TestCase):
    def setUp(self):
        self.con = store.connect(":memory:")

    def add(self, prices, days_ago_start, tier=2):
        for i, p in enumerate(prices):
            ts = NOW - timedelta(days=days_ago_start - i)
            store.add_observation(self.con, "w1", p, "s", "https://www.kayak.it/f", ts=ts.isoformat(), tier=tier)

    def test_no_verified_history(self):
        self.add([700], 1, tier=None)
        r = advise.advise(CFG, self.con, W, NOW)
        self.assertEqual((r["verdict"], r["confidence"]), ("WATCH_CLOSELY", "low"))
        self.assertIn("no verified", r["reasons"][-1])

    def test_short_history_is_low_confidence(self):
        self.add([900, 950, 920], 3)
        r = advise.advise(CFG, self.con, W, NOW)
        self.assertEqual(r["confidence"], "low")
        self.assertTrue(any("history too short" in x for x in r["reasons"]))

    def test_good_price_in_window_buys(self):
        self.add([950, 940, 960, 930, 955, 945, 950, 935, 960, 820], 10)
        r = advise.advise(CFG, self.con, W, NOW)   # ~195 days before peak long-haul window 120-240
        self.assertEqual(r["position"], "in_window")
        self.assertEqual(r["verdict"], "BUY_NOW")
        self.assertEqual(r["confidence"], "high")

    def test_high_price_in_window_waits_with_recheck(self):
        older = [(880, 12), (800, 11), (905, 10), (810, 9), (900, 8)]
        recent = [(800, 3), (820, 2), (905, 1)]
        for p, d in older + recent:
            store.add_observation(self.con, "w1", p, "s", "https://www.kayak.it/f",
                                  ts=(NOW - timedelta(days=d)).isoformat(), tier=2)
        r = advise.advise(CFG, self.con, {**W, "budget_eur": 700}, NOW)
        self.assertEqual(r["verdict"], "WAIT")
        self.assertEqual(r["recheck_date"], (NOW.date() + timedelta(days=7)).isoformat())
        self.assertLess(r["metrics"]["trend_pct"], 0)

    def test_early_waits_until_window_opens(self):
        w = {**W, "peak": False, "depart_window": {"from": "2027-09-01"}}   # 287 days, window 90-180
        self.add([800, 810, 790, 805, 800], 5)
        r = advise.advise(CFG, self.con, w, NOW)
        self.assertEqual((r["position"], r["verdict"]), ("early", "WAIT"))
        self.assertEqual(r["recheck_date"], (NOW.date() + timedelta(days=7)).isoformat())

    def test_late_buys(self):
        w = {**W, "depart_window": {"from": (NOW.date() + timedelta(days=10)).isoformat()}}
        self.add([800, 810], 2)
        self.assertEqual(advise.advise(CFG, self.con, w, NOW)["verdict"], "BUY_NOW")


if __name__ == "__main__":
    unittest.main()
