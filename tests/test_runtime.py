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
_REAL_REDIRECT_CHAIN = wikidata.redirect_chain
wikidata.redirect_chain = lambda cfg, site: []   # unit tests never touch the network
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

    def test_unresolved_stays_unverified(self):
        v = self.verify("https://www.google.com/travel/flights?q=x", name="Google Flights",
                        search={"Google Flights": ["Q5583836"]})
        self.assertEqual((v["tier"], v["verified_by"], v["ttl_days"]), (0, "unverified", CFG["reliability"]["unverified_ttl_days"]))

    def test_unreachable_never_falls_back_to_a_list(self):
        def down(cfg, url, params):
            raise sources.SourceError("timeout")
        with mock.patch.object(sources, "_get_json", down):
            v = reliability.verify_source(CFG, self.con, "https://www.ita-airways.com/x", now=NOW)
        self.assertEqual((v["tier"], v["verified_by"]), (0, "unverified"))
        self.assertIn("unreachable", v["note"])
        with self.assertRaises(schema.ValidationError):     # its prices cannot be recorded
            reliability.check_flight_observation(
                CFG, {"depart_window": {"from": "2027-06-01"}, "one_way": True}, "https://www.ita-airways.com/x",
                "2027-06-02", None, 500, "500 EUR", "main",
                source_tier=reliability.resolve(CFG, self.con, "https://www.ita-airways.com/x", NOW)["tier"])

    def test_confirm_with_independent_sources(self):
        url = "https://www.google.com/travel/flights"
        ev = [("https://en.wikipedia.org/wiki/Google_Flights", "Google Flights ... website google.com/travel/flights"),
              ("https://www.theverge.com/x", "the flight search engine at google.com/travel/flights")]
        v = reliability.confirm_source(CFG, self.con, url, 2, ev, now=NOW)
        self.assertEqual((v["tier"], v["verified_by"]), (2, "independent_sources"))
        self.assertEqual(reliability.resolve(CFG, self.con, url, NOW)["tier"], 2)

    def test_confirm_rejects_weak_evidence(self):
        url = "https://www.google.com/travel/flights"
        bad = [
            [("https://www.google.com/about", "google.com/travel/flights")],                       # candidate itself, one source
            [("https://a.com/x", "google.com"), ("https://a.com/y", "google.com")],               # same site twice
            [("https://a.com/x", "Google Flights"), ("https://b.com/y", "Google Flights")],       # domain not named
        ]
        for ev in bad:
            with self.subTest(ev=ev), self.assertRaises(schema.ValidationError):
                reliability.confirm_source(CFG, self.con, url, 2, ev, now=NOW)

    def test_ttl_expiry_forces_reverification(self):
        self.verify("https://www.kayak.it/flights", name="Kayak", search={"Kayak": ["Q6380402"]})
        url = "https://www.kayak.it/flights/x"
        self.assertEqual(reliability.resolve(CFG, self.con, url, NOW + timedelta(days=29))["tier"], 2)
        self.assertIsNone(reliability.resolve(CFG, self.con, url, NOW + timedelta(days=31)))
        self.assertEqual(knowledge.stale(self.con, NOW + timedelta(days=31))[0]["key"], "kayak.it")


class NoDomainListsGuard(unittest.TestCase):
    """Guard: shipped config must hold rules and thresholds only, never lists of sites."""
    ALLOWED = ("sources.", "wikidata.api", "wikidata.sparql", "access.api_adapters.", "http.user_agent")
    DOMAIN = __import__("re").compile(r"[a-z0-9-]+\\?\.(com|net|org|it|cn|co|io|travel|aero)\b", __import__("re").I)

    def walk(self, node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from self.walk(v, f"{path}{k}.")
        elif isinstance(node, list):
            for v in node:
                yield from self.walk(v, path)
        elif isinstance(node, str):
            yield path.rstrip("."), node

    def test_defaults_have_no_domain_lists(self):
        raw = config.load_yaml(config.DEFAULTS_FILE)
        self.assertFalse({"tier1", "tier2", "tier1_extra", "tier2_extra", "fallback"} & set(raw["reliability"]))
        offenders = [(p, v) for p, v in self.walk(raw)
                     if self.DOMAIN.search(v) and not p.startswith(self.ALLOWED)]
        self.assertEqual(offenders, [])

    def test_user_lists_are_ignored_with_a_warning(self):
        home = Path(tempfile.mkdtemp())
        (home / "settings.yaml").write_text("reliability: {tier1_extra: ['^x\\.com/'], confirm_window_h: 12}\n")
        with mock.patch.dict(os.environ, {"TRAVEL_RADAR_HOME": str(home)}):
            cfg = config.settings()
        self.assertNotIn("tier1_extra", cfg["reliability"])
        self.assertEqual(cfg["reliability"]["confirm_window_h"], 12)
        self.assertIn("deprecated", cfg["_deprecations"][0])


class PathScopeTest(unittest.TestCase):
    """Official websites registered with a path cover only that path, not the whole domain."""
    ENTS = {"Q1": {"label": "Flight search", "sites": ["https://www.google.com/travel/flights"], "airline": False,
                   "travel": True, "gov": False},
            "Q2": {"label": "Google Flights (real record)", "sites": ["https://www.google.com/flights/"],
                   "airline": False, "travel": False, "gov": False}}

    def setUp(self):
        self.con = store.connect(":memory:")

    def test_match_site_path_prefix(self):
        sites = ["https://www.google.com/travel/flights"]
        self.assertEqual(wikidata.match_site("https://www.google.com/travel/flights/search?q=x", sites),
                         ("exact", "/travel/flights"))
        self.assertIsNone(wikidata.match_site("https://www.google.com/search?q=flights", sites))
        self.assertIsNone(wikidata.match_site("https://www.google.com/travel/flightsabc", sites))
        self.assertIsNone(wikidata.match_domain("www.google.com", sites))
        self.assertEqual(wikidata.match_site("https://www.kayak.it/flights/x", ["https://www.kayak.com/"]), ("brand", None))

    def test_classify_path_scoped_and_class_check_still_required(self):
        self.assertEqual(wikidata.classify("https://www.google.com/travel/flights/s", {"Q1": self.ENTS["Q1"]})[:5:4],
                         (2, "/travel/flights"))
        self.assertEqual(wikidata.classify("https://www.google.com/search", {"Q1": self.ENTS["Q1"]})[0], 0)
        self.assertEqual(wikidata.classify("https://www.google.com/flights/x", {"Q2": self.ENTS["Q2"]})[0], 0)

    def test_path_scoped_verdict_does_not_cover_rest_of_domain(self):
        ents = {k: {**v, "sites": v["sites"]} for k, v in self.ENTS.items() if k == "Q1"}
        with mock.patch.object(wikidata, "by_site", lambda cfg, d: []), \
             mock.patch.object(wikidata, "search", lambda cfg, n: ["Q1"]), \
             mock.patch.object(wikidata, "describe", lambda cfg, q: {k: dict(v, sites=set(v["sites"])) for k, v in ents.items()}):
            v = reliability.verify_source(CFG, self.con, "https://www.google.com/travel/flights", "x", now=NOW)
        self.assertEqual((v["tier"], v["key"]), (2, "google.com/travel/flights"))
        self.assertEqual(reliability.resolve(CFG, self.con, "https://www.google.com/travel/flights/search?tfs=1", NOW)["tier"], 2)
        self.assertIsNone(reliability.resolve(CFG, self.con, "https://www.google.com/search?q=x", NOW))

    def test_confirm_path_scope(self):
        knowledge.put(self.con, "source", "google.com", {"tier": 0}, ["https://www.wikidata.org"], 1, "unverified", now=NOW)
        ev = [("https://en.wikipedia.org/wiki/Google_Flights", "Google Flights at google.com/travel/flights"),
              ("https://www.theverge.com/x", "flight search on google.com/travel/flights")]
        r = reliability.confirm_source(CFG, self.con, "https://www.google.com/travel/flights", 2, ev, now=NOW, scope="path")
        self.assertEqual(r["key"], "google.com/travel/flights")
        self.assertEqual(reliability.resolve(CFG, self.con, "https://www.google.com/travel/flights/search", NOW)["tier"], 2)
        self.assertEqual(reliability.resolve(CFG, self.con, "https://www.google.com/search", NOW)["tier"], 0)
        with self.assertRaises(schema.ValidationError):
            reliability.confirm_source(CFG, self.con, "https://www.google.com", 2, ev, now=NOW, scope="path")


class RegistryGapsTest(unittest.TestCase):
    """Entities the registry does not classify, whose registered website moved."""
    GF = {"Q5583836": {"label": "Google Flights", "sites": {"https://www.google.com/flights/"}, "airline": False,
                       "travel": False, "gov": False, "description": "online flight booking service from Google"},
          "Q9366": {"label": "Google Search", "sites": {"https://www.google.com/"}, "airline": False,
                    "travel": False, "gov": False, "description": "search engine developed by Google"}}

    def run_verify(self, url, chain):
        ents = {k: dict(v, sites=set(v["sites"])) for k, v in self.GF.items()}
        with mock.patch.object(wikidata, "by_site", lambda cfg, d: ["Q9366"]), \
             mock.patch.object(wikidata, "search", lambda cfg, n: ["Q5583836"]), \
             mock.patch.object(wikidata, "describe", lambda cfg, q: ents), \
             mock.patch.object(wikidata, "redirect_chain", lambda cfg, site: chain.get(site, [])):
            return wikidata.verify(CFG, url, "Google Flights")

    def test_moved_site_plus_description_passes_only_under_its_path(self):
        chain = {"https://www.google.com/flights/": ["https://www.google.com/travel/flights"]}
        v = self.run_verify("https://www.google.com/travel/flights?q=x", chain)
        self.assertEqual((v["tier"], v["scope"], v["signal"], v["qid"]), (2, "/travel/flights", "description", "Q5583836"))
        v = self.run_verify("https://www.google.com/search?q=flights", chain)
        self.assertEqual(v["tier"], 0)

    def test_without_redirect_evidence_it_stays_unverified(self):
        self.assertEqual(self.run_verify("https://www.google.com/travel/flights?q=x", {})["tier"], 0)

    def test_description_alone_never_matches_another_site(self):
        ents = {"Q1": {"label": "X", "sites": {"https://www.other.example/"}, "airline": False, "travel": False,
                       "gov": False, "description": "online flight booking service"}}
        self.assertEqual(wikidata.classify("https://www.google.com/travel/flights", ents, ["flight booking"])[0], 0)


class RedirectSafetyTest(unittest.TestCase):
    def test_outbound_only_public_http(self):
        from tw import urls
        for bad in ("file:///etc/passwd", "ftp://example.com/x", "http://127.0.0.1/x", "http://localhost/x",
                    "http://10.0.0.5/x", "http://169.254.169.254/latest/meta-data", "http://[::1]/x"):
            with self.subTest(url=bad):
                self.assertFalse(urls.safe_outbound(bad))

    def test_redirect_never_widens_scope(self):
        self.assertTrue(wikidata._narrower_or_sibling("/flights", "/travel/flights"))
        self.assertTrue(wikidata._narrower_or_sibling("/flights", "/flights/search"))
        for wider in ("", "/travel"):
            self.assertFalse(wikidata._narrower_or_sibling("/travel/flights", wider))

    def chain(self, location):
        import urllib.error
        wikidata._REDIRECTS.clear()

        class Opener:
            def open(self, req, timeout=None):
                if req.full_url == "https://www.google.com/flights/":
                    raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": location}, None)
                return None
        with mock.patch.object(wikidata.urllib.request, "build_opener", lambda *a: Opener()), \
             mock.patch.object(wikidata.urls, "safe_outbound", lambda u: u.startswith("https://www.google.com")):
            return _REAL_REDIRECT_CHAIN(CFG, "https://www.google.com/flights/")

    def test_redirect_chain_rules(self):
        self.assertEqual(self.chain("/travel/flights"), ["https://www.google.com/travel/flights"])
        self.assertEqual(self.chain("/"), [])                                   # root: would widen to the domain
        self.assertEqual(self.chain("https://consent.google.com/m?x"), [])      # other host
        self.assertEqual(self.chain("https://evil.example/flights"), [])        # other host


class URLParserTest(unittest.TestCase):
    def test_rejects_confusing_urls(self):
        from tw import urls
        for bad in ("https://kayak.it@evil.com/f", "https://evil.com:abc/", "https://g.com/a/%2e%2e/b",
                    "https://g.com/travel/flights/../search", "https://g.com/a%2Fb"):
            with self.subTest(url=bad), self.assertRaises(urls.URLError):
                urls.parse(bad)
            with self.subTest(url=bad), self.assertRaises(schema.ValidationError):
                reliability.resolve(CFG, store.connect(":memory:"), bad)
        self.assertEqual(urls.parse("HTTPS://WWW.Google.com/Travel/Flights/?q=1"), ("www.google.com", "/travel/flights"))


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
