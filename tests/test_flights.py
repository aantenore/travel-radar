import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "travel-radar" / "scripts"))
os.environ.setdefault("TRAVEL_RADAR_HOME", tempfile.mkdtemp(prefix="travel-radar-test-"))

from tw import access, advise, config, itinerary, profile, schema, store  # noqa: E402

CFG = config.settings()
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


def leg(segs, lays, total):
    return {"segments": [{"from": a, "to": b, "carrier": c, "depart": d, "arrive": r} for a, b, c, d, r in segs],
            "layovers": [{"airport": ap, "duration_min": m, "self_transfer": st} for ap, m, st in lays], "total_min": total}


DIRECT = {"outbound": leg([("MXP", "PEK", "Air China", "2027-06-04T13:00", "2027-06-05T06:10")], [], 610),
          "return": leg([("PEK", "MXP", "Air China", "2027-06-22T01:30", "2027-06-22T07:30")], [], 660)}
VIA_AUH = {"outbound": leg([("MXP", "AUH", "Etihad", "2027-06-04T10:00", "2027-06-04T18:00"),
                            ("AUH", "PEK", "Etihad", "2027-06-04T19:10", "2027-06-05T07:30")], [("AUH", 70, False)], 870),
           "return": leg([("PEK", "AUH", "Etihad", "2027-06-22T09:00", "2027-06-22T14:00"),
                          ("AUH", "MXP", "Etihad", "2027-06-22T15:50", "2027-06-22T20:00")], [("AUH", 110, False)], 900)}
LONG_CKG = {"outbound": leg([("MXP", "CKG", "Hainan", "2027-06-04T12:00", "2027-06-05T05:00"),
                             ("CKG", "PEK", "Hainan", "2027-06-05T12:15", "2027-06-05T14:45")], [("CKG", 435, False)], 1185),
            "return": leg([("PEK", "SZX", "Hainan", "2027-06-22T07:00", "2027-06-22T10:30"),
                           ("SZX", "MXP", "Hainan", "2027-06-23T00:30", "2027-06-23T07:30")], [("SZX", 840, False)], 1830)}


class ItineraryValidationTest(unittest.TestCase):
    def test_valid_and_computed_fields(self):
        it = itinerary.validate(CFG, LONG_CKG, "2027-06-04", "2027-06-22")
        self.assertEqual(it["return"]["layovers"][0]["duration_min"], 840)
        self.assertTrue(it["return"]["layovers"][0]["overnight"])           # 10:30 -> 00:30 crosses midnight
        self.assertFalse(itinerary.validate(CFG, VIA_AUH)["outbound"]["layovers"][0]["overnight"])

    def test_rejects_inconsistent_itineraries(self):
        bad_duration = {"outbound": leg([("MXP", "AUH", "E", "2027-06-04T10:00", "2027-06-04T18:00"),
                                         ("AUH", "PEK", "E", "2027-06-04T19:10", "2027-06-05T07:30")], [("AUH", 20, False)], 870)}
        wrong_airport = {"outbound": leg([("MXP", "AUH", "E", "2027-06-04T10:00", "2027-06-04T18:00"),
                                          ("AUH", "PEK", "E", "2027-06-04T19:10", "2027-06-05T07:30")], [("DXB", 70, False)], 870)}
        broken_chain = {"outbound": leg([("MXP", "AUH", "E", "2027-06-04T10:00", "2027-06-04T18:00"),
                                         ("DXB", "PEK", "E", "2027-06-04T19:10", "2027-06-05T07:30")], [("AUH", 70, False)], 870)}
        missing_lay = {"outbound": leg([("MXP", "AUH", "E", "2027-06-04T10:00", "2027-06-04T18:00"),
                                        ("AUH", "PEK", "E", "2027-06-04T19:10", "2027-06-05T07:30")], [], 870)}
        for it in (bad_duration, wrong_airport, broken_chain, missing_lay):
            with self.subTest(it=it), self.assertRaises(schema.ValidationError):
                itinerary.validate(CFG, it)
        with self.assertRaises(schema.ValidationError):
            itinerary.validate(CFG, DIRECT, depart="2027-06-05")


class ItineraryRankingTest(unittest.TestCase):
    PROF = {"flights": {"max_layover_h": 4, "prefer_shorter_layovers": True}}

    def rows(self):
        return [{"price_eur": 589, "itinerary": itinerary.validate(CFG, LONG_CKG)},
                {"price_eur": 721, "itinerary": itinerary.validate(CFG, DIRECT)},
                {"price_eur": 712, "itinerary": itinerary.validate(CFG, VIA_AUH)}]

    def test_hard_filter_shown_and_fit_ranking(self):
        r = itinerary.rank(CFG, self.PROF, self.rows())
        self.assertEqual(r["cheapest_overall"]["price_eur"], 589)
        self.assertEqual(r["cheapest_that_fits"]["price_eur"], 712)
        cheap = [x for x in r["ranked"] if x["price_eur"] == 589][0]
        self.assertFalse(cheap["fits"])
        self.assertTrue(any("over your max 4h" in c for c in cheap["conflicts"]))
        self.assertEqual(r["ranked"][-1]["price_eur"], 589)                 # non-fitting last, not hidden

    def test_no_preferences_everything_fits(self):
        r = itinerary.rank(CFG, {}, self.rows())
        self.assertTrue(all(x["fits"] for x in r["ranked"]))

    def test_advise_and_alerts_use_only_fitting_itineraries(self):
        con = store.connect(":memory:")
        w = {"id": "w1", "origin": ["MXP"], "destination": ["PEK"], "haul": "long", "peak": True,
             "depart_window": {"from": "2027-06-04"}}
        for i, row in enumerate(self.rows()):
            store.add_observation(con, "w1", row["price_eur"], "s", "https://www.kayak.it/f",
                                  ts=(NOW - timedelta(hours=i)).isoformat(), tier=2, itinerary=row["itinerary"])
        store.add_observation(con, "w1", 500, "s", "https://www.kayak.it/f", ts=NOW.isoformat(), tier=2)  # no itinerary
        r = advise.advise(CFG, con, w, NOW, profile=self.PROF)
        self.assertEqual(r["verified_observations"], 2)
        self.assertTrue(any("1 excluded for layovers" in x and "1 without itinerary" in x for x in r["reasons"]))
        r = advise.advise(CFG, con, {**w, "flight_override": {"evidence": "va bene anche lo scalo lungo"}}, NOW, profile=self.PROF)
        self.assertEqual(r["verified_observations"], 4)

    def test_profile_fields_and_gaps_step(self):
        p = {}
        profile.set_value(p, "flights.max_layover_h", 4, "massimo 4 ore di scalo")
        profile.set_value(p, "flights.prefer_shorter_layovers", True, "meno ore è meglio")
        g = profile.gaps(CFG, p, None, "flights")
        keys = [x["key"] for x in g["all_missing"]]
        self.assertNotIn("flights.max_layover_h", keys)
        self.assertIn("flights.max_stops", keys)


class ChallengeTest(unittest.TestCase):
    def test_detect_generic_challenge_pages(self):
        for text in ("Sei una persona o un robot? Tieni premuto", "<div id='px-captcha'>", "Verify you are human",
                     "Our systems have detected unusual traffic"):
            with self.subTest(text=text):
                self.assertIsNotNone(access.detect_challenge(CFG, text))
        self.assertIsNone(access.detect_challenge(CFG, "Air China 721 € direct, 10h 10m"))

    def test_new_outcome_and_backoff(self):
        con = store.connect(":memory:")
        access.record(con, "kayak.it", "browser", "blocked", "rate limited", now=NOW)
        p = access.plan(CFG, con, [("kayak.it", 2)], browser=True, now=NOW + timedelta(hours=1))
        self.assertIn("back off", p["skipped"][0]["reason"])
        p = access.plan(CFG, con, [("kayak.it", 2)], browser=True,
                        now=NOW + timedelta(hours=CFG["access"]["blocked_backoff_hours"] + 1))
        self.assertTrue(any(s["method"] == "browser" for s in p["steps"]))
        r = access.record(con, "skyscanner.it", "browser", "ok_after_user_challenge", now=NOW)
        self.assertTrue(r["challenge"])

    def test_challenge_gated_only_in_attended_and_last(self):
        con = store.connect(":memory:")
        access.record(con, "skyscanner.it", "browser", "blocked", "px-captcha", now=NOW, challenge=True)
        for _ in range(3):
            access.record(con, "kayak.it", "browser", "ok", now=NOW)
        cands = [("skyscanner.it", 2), ("kayak.it", 2)]
        un = access.plan(CFG, con, cands, browser=True, attended=False, now=NOW)
        self.assertNotIn("skyscanner.it", [s["domain"] for s in un["steps"]])
        self.assertTrue(any(s["domain"] == "skyscanner.it" and "challenge-gated" in s["reason"] for s in un["skipped"]))
        at = access.plan(CFG, con, cands, browser=True, attended=True, now=NOW)
        last = at["steps"][-1]
        self.assertEqual((last["domain"], last["method"], last["handoff"]), ("skyscanner.it", "browser", "user_solves_challenge"))
        self.assertEqual(last["max_searches_this_session"], CFG["access"]["max_attended_searches_per_site"])
        self.assertNotIn(("skyscanner.it", "webfetch"), [(s["domain"], s["method"]) for s in at["steps"]])


if __name__ == "__main__":
    unittest.main()
