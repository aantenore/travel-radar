import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "travel-radar" / "scripts"))
os.environ.setdefault("TRAVEL_RADAR_HOME", tempfile.mkdtemp(prefix="travel-radar-test-"))

from tw import access, advise, config, knowledge, reliability, schema, sources, store, wikidata  # noqa: E402

CFG = config.settings()
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


def fake_wikidata(entities, search=None, iata=None):
    """Mock of sources._get_json answering wbsearchentities and the SPARQL queries."""
    def get(cfg, url, params):
        if url == cfg["wikidata"]["api"]:
            return {"search": [{"id": q} for q in (search or {}).get(params["search"], [])]}
        q = params["query"]
        if "wdt:P229" in q and "VALUES" not in q:
            code = q.split('"')[1]
            return {"results": {"bindings": [{"item": {"value": f"http://www.wikidata.org/entity/{x}"}}
                                             for x in (iata or {}).get(code, [])]}}
        rows = []
        for qid, e in entities.items():
            if f"wd:{qid} " not in q + " ":
                continue
            for site in e["sites"] or [None]:
                b = {"item": {"value": f"http://www.wikidata.org/entity/{qid}"}, "itemLabel": {"value": e["label"]},
                     "air": {"value": str(e["airline"]).lower()}, "trav": {"value": str(e["travel"]).lower()}}
                if site:
                    b["site"] = {"value": site}
                rows.append(b)
        return {"results": {"bindings": rows}}
    return get


ENTS = {
    "Q51747": {"label": "Air China", "sites": ["http://www.airchina.com/"], "airline": True, "travel": False},
    "Q6380402": {"label": "kayak.com", "sites": ["https://www.kayak.com", "https://www.kayak.co.uk/"], "airline": False, "travel": True},
    "Q5583836": {"label": "Google Flights", "sites": ["https://www.google.com/flights/"], "airline": False, "travel": False},
    "Q999": {"label": "Some Blog", "sites": ["https://someblog.com"], "airline": False, "travel": False},
}


class DomainMatchTest(unittest.TestCase):
    def test_registrable_and_brand(self):
        self.assertEqual(wikidata.registrable("it.ceair.com"), "ceair.com")
        self.assertEqual(wikidata.registrable("www.kayak.co.uk"), "kayak.co.uk")
        self.assertEqual(wikidata.match_domain("www.kayak.it", ["https://www.kayak.com"]), "brand")
        self.assertEqual(wikidata.match_domain("kayak.co.uk", ["https://www.kayak.co.uk/"]), "exact")
        self.assertIsNone(wikidata.match_domain("kayak-deals.net", ["https://www.kayak.com"]))


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.con = store.connect(":memory:")

    def verify(self, url, name=None, code=None, search=None, iata_map=None):
        with mock.patch.object(sources, "_get_json", fake_wikidata(ENTS, search=search, iata=iata_map)):
            return reliability.verify_source(CFG, self.con, url, name, code, now=NOW)

    def test_airline_by_iata_matches_official_site(self):
        v = self.verify("https://www.airchina.it/it/booking", code="CA", iata_map={"CA": ["Q51747"]})
        self.assertEqual((v["tier"], v["verified_by"], v["qid"], v["match"]), (1, "wikidata", "Q51747", "brand"))

    def test_travel_site_by_name(self):
        v = self.verify("https://www.kayak.it/flights", name="Kayak", search={"Kayak": ["Q6380402"]})
        self.assertEqual((v["tier"], v["verified_by"]), (2, "wikidata"))

    def test_domain_mismatch_is_context_only(self):
        v = self.verify("https://cheap-kayak-flights.com/", name="Kayak", search={"Kayak": ["Q6380402"]})
        self.assertEqual(v["tier"], 0)
        v = self.verify("https://someblog.com/china", name="Some Blog", search={"Some Blog": ["Q999"]})
        self.assertEqual(v["tier"], 0)

    def test_unclassified_on_wikidata_uses_marked_fallback(self):
        v = self.verify("https://www.google.com/travel/flights?q=x", name="Google Flights",
                        search={"Google Flights": ["Q5583836"]})
        self.assertEqual((v["tier"], v["verified_by"], v["ttl_days"]), (2, "fallback", CFG["reliability"]["fallback_ttl_days"]))

    def test_unreachable_uses_fallback(self):
        def down(cfg, url, params):
            raise sources.SourceError("timeout")
        with mock.patch.object(sources, "_get_json", down):
            v = reliability.verify_source(CFG, self.con, "https://www.ita-airways.com/x", now=NOW)
        self.assertEqual((v["tier"], v["verified_by"]), (1, "fallback"))
        self.assertIn("unreachable", v["note"])

    def test_ttl_expiry_forces_reverification(self):
        self.verify("https://www.kayak.it/flights", name="Kayak", search={"Kayak": ["Q6380402"]})
        url = "https://www.kayak.it/flights/x"
        self.assertEqual(reliability.resolve(CFG, self.con, url, NOW + timedelta(days=29))["tier"], 2)
        self.assertIsNone(reliability.resolve(CFG, self.con, url, NOW + timedelta(days=31)))
        self.assertEqual(knowledge.stale(self.con, NOW + timedelta(days=31))[0]["key"], "kayak.it")


class KnowledgeTest(unittest.TestCase):
    def test_put_requires_sources_and_valid_window(self):
        con = store.connect(":memory:")
        with self.assertRaises(schema.ValidationError):
            knowledge.put(con, "booking_window", "k", {"ideal_days": [90, 150]}, [], 90)
        with self.assertRaises(schema.ValidationError):
            knowledge.put(con, "booking_window", "k", {"ideal_days": [150, 90]}, ["https://x"], 90)
        knowledge.put(con, "booking_window", "k", {"ideal_days": [60, 150]}, ["https://x"], 90, now=NOW)
        self.assertTrue(knowledge.get(con, "booking_window", "k", NOW)["fresh"])

    def test_advise_uses_researched_window_then_falls_back_when_expired(self):
        con = store.connect(":memory:")
        w = {"id": "w1", "origin": ["FCO"], "destination": ["PEK"], "haul": "long", "peak": True,
             "depart_window": {"from": "2027-06-01"}}
        r = advise.advise(CFG, con, w, NOW)
        self.assertEqual(r["window_confidence"], "heuristic")
        self.assertEqual(r["research_needed"]["key"], "FCO>PEK:peak")
        knowledge.put(con, "booking_window", "FCO>PEK:peak", {"ideal_days": [60, 150]}, ["https://study"], 90, now=NOW)
        r = advise.advise(CFG, con, w, NOW)
        self.assertEqual((r["window_confidence"], r["position"], r["research_needed"]), ("researched", "early", None))
        r = advise.advise(CFG, con, w, NOW + timedelta(days=91))
        self.assertEqual(r["research_needed"]["why"], "expired")


class AccessPlanTest(unittest.TestCase):
    def test_plan_learns_from_outcomes(self):
        con = store.connect(":memory:")
        for _ in range(4):
            access.record(con, "www.kayak.it", "webfetch", "no_data", "client-rendered")
        for _ in range(3):
            access.record(con, "kayak.it", "browser", "ok")
        p = access.plan(CFG, con, [("www.kayak.it", 2), ("www.airchina.it", 1)], browser=True)
        first = p["steps"][0]
        self.assertEqual((first["domain"], first["method"]), ("kayak.it", "browser"))
        last = p["steps"][-1]
        self.assertEqual((last["domain"], last["method"]), ("kayak.it", "webfetch"))
        # untried airline: prior 0.5, tier 1 before the tier 2 webfetch that keeps failing
        self.assertEqual(p["steps"][1]["domain"], "airchina.it")

    def test_browser_and_api_only_when_available(self):
        con = store.connect(":memory:")
        p = access.plan(CFG, con, [("kayak.it", 2)], browser=False)
        self.assertEqual(p["methods_available"], ["webfetch"])
        with mock.patch.dict(os.environ, {"SERPAPI_KEY": "x"}):
            p = access.plan(CFG, con, [("kayak.it", 2)], browser=True)
        self.assertIn("api:serpapi", p["methods_available"])

    def test_invalid_outcome(self):
        with self.assertRaises(schema.ValidationError):
            access.record(store.connect(":memory:"), "kayak.it", "webfetch", "great")

    def test_parse_serpapi_never_leaks_key(self):
        data = {"search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?tfs=abc"},
                "best_flights": [{"price": 612, "flights": [{"airline": "Air China"}], "layovers": []}],
                "other_flights": [{"price": 580, "flights": [{"airline": "Finnair"}, {"airline": "Finnair"}],
                                   "layovers": [{"id": "HEL"}]}]}
        out = access.parse_serpapi(data, "2027-06-03", "2027-06-20")
        self.assertEqual([(f["price"], f["stops"], f["carrier"]) for f in out], [(612, 0, "Air China"), (580, 1, "Finnair")])
        self.assertTrue(all(reliability.price_in_excerpt(f["price"], f["excerpt"]) for f in out))
        self.assertNotIn("api_key", str(out))


if __name__ == "__main__":
    unittest.main()
