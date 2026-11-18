"""Verify at runtime that a price source is an airline (tier 1) or a travel search/booking site
(tier 2), using Wikidata: entity class (P31/P279*), official website (P856), IATA code (P229).

The model proposes candidates (domain, plus entity name and/or IATA code); this module decides.
Wikidata unreachable or unresolved -> the source stays unverified (see reliability.confirm_source).
"""
import re

from . import sources

# Second-level labels that form a public suffix together with a 2-letter ccTLD (co.uk, com.au, ...).
_SLD = {"co", "com", "net", "org", "gov", "ac", "edu", "or", "ne", "go"}


def registrable(domain):
    """`it.ceair.com` -> `ceair.com`; `www.kayak.co.uk` -> `kayak.co.uk`."""
    labels = domain.lower().strip(".").split(".")
    if labels and labels[0] == "www":
        labels = labels[1:]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SLD:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def brand(domain):
    return registrable(domain).split(".")[0]


def _host(url):
    m = re.match(r"^(?:[a-z]+://)?([^/:?#]+)", url.strip().lower())
    return m.group(1) if m else ""


def match_domain(domain, official_sites):
    """'exact' if the registrable domains match, 'brand' if only the brand label matches
    (country variants such as kayak.it vs kayak.com), else None."""
    reg = registrable(domain)
    regs = {registrable(_host(s)) for s in official_sites if _host(s)}
    if reg in regs:
        return "exact"
    if brand(domain) in {r.split(".")[0] for r in regs}:
        return "brand"
    return None


def _sparql(cfg, query):
    data = sources._get_json(cfg, cfg["wikidata"]["sparql"], {"query": query, "format": "json"})
    return data["results"]["bindings"]


def search(cfg, name):
    data = sources._get_json(cfg, cfg["wikidata"]["api"], {
        "action": "wbsearchentities", "search": name, "language": "en", "type": "item",
        "limit": cfg["wikidata"]["search_limit"], "format": "json"})
    return [r["id"] for r in data.get("search", [])]


def by_iata(cfg, code):
    rows = _sparql(cfg, f'SELECT ?item WHERE {{ ?item wdt:P229 "{code.upper()}" . }}')
    return [r["item"]["value"].rsplit("/", 1)[-1] for r in rows]


def by_site(cfg, domain):
    """Entities whose official website (P856) is exactly this domain (scheme/www/slash variants)."""
    reg = registrable(domain)
    hosts = {domain.lower(), reg, "www." + reg}
    uris = " ".join(f"<{s}://{h}{t}>" for s in ("http", "https") for h in hosts for t in ("", "/"))
    rows = _sparql(cfg, f"SELECT DISTINCT ?item WHERE {{ VALUES ?site {{ {uris} }} ?item wdt:P856 ?site . }}")
    return [r["item"]["value"].rsplit("/", 1)[-1] for r in rows]


def describe(cfg, qids):
    """{qid: {label, sites, iata, airline, travel}} for the given entities."""
    if not qids:
        return {}
    w = cfg["wikidata"]
    vals = " ".join(f"wd:{q}" for q in qids if re.fullmatch(r"Q\d+", q))
    air = " ".join(f"wd:{q}" for q in w["airline_classes"])
    trav = " ".join(f"wd:{q}" for q in w["travel_classes"])
    gov = " ".join(f"wd:{q}" for q in w["government_classes"])
    q = (f"SELECT ?item ?itemLabel ?site ?iata ?air ?trav ?gov WHERE {{ VALUES ?item {{ {vals} }} "
         f"OPTIONAL {{ ?item wdt:P856 ?site }} OPTIONAL {{ ?item wdt:P229 ?iata }} "
         f"BIND(EXISTS {{ ?item wdt:P31/wdt:P279* ?a . VALUES ?a {{ {air} }} }} AS ?air) "
         f"BIND(EXISTS {{ ?item wdt:P31/wdt:P279* ?t . VALUES ?t {{ {trav} }} }} AS ?trav) "
         f"BIND(EXISTS {{ ?item wdt:P31/wdt:P279* ?g . VALUES ?g {{ {gov} }} }} AS ?gov) "
         f'SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}')
    out = {}
    for b in _sparql(cfg, q):
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        e = out.setdefault(qid, {"label": b.get("itemLabel", {}).get("value"), "sites": set(), "iata": set(),
                                 "airline": False, "travel": False, "gov": False})
        if "site" in b:
            e["sites"].add(b["site"]["value"])
        if "iata" in b:
            e["iata"].add(b["iata"]["value"])
        e["airline"] |= b["air"]["value"] == "true"
        e["travel"] |= b["trav"]["value"] == "true"
        e["gov"] |= b.get("gov", {}).get("value") == "true"
    return out


def classify(domain, entities):
    """Pure decision: best (tier, qid, label, match) for `domain` among described entities.
    Tier 1 beats tier 2; an exact domain match beats a brand (country variant) match."""
    best, best_rank = (0, None, None, None), None
    for qid, e in entities.items():
        m = match_domain(domain, e["sites"])
        t = 1 if e["airline"] else (2 if e["travel"] else 0)
        if not m or not t:
            continue
        rank = (t, 0 if m == "exact" else 1)
        if best_rank is None or rank < best_rank:
            best, best_rank = (t, qid, e["label"], m), rank
    return best


def official(domain, entities):
    """Pure decision: an entity whose official website matches `domain` (exact or brand), and
    whether that entity is a government body / diplomatic mission / intergovernmental org."""
    best = None
    for qid, e in entities.items():
        m = match_domain(domain, e["sites"])
        if m and (best is None or (m == "exact" and best["match"] != "exact") or (e["gov"] and not best["government"])):
            best = {"entity": qid, "label": e["label"], "match": m, "government": e["gov"]}
    return best


def verify(cfg, domain, name=None, iata=None):
    """Raises sources.SourceError if Wikidata cannot be reached."""
    qids = by_site(cfg, domain)
    if iata:
        qids += by_iata(cfg, iata)
    for n in filter(None, [name, brand(domain)]):
        qids += search(cfg, n)
    qids = list(dict.fromkeys(qids))
    ents = describe(cfg, qids)
    tier, qid, label, match = classify(domain, ents)
    off = official(domain, ents)
    return {"domain": registrable(domain), "tier": tier, "qid": qid or (off or {}).get("entity"),
            "label": label or (off or {}).get("label"), "match": match or (off or {}).get("match"),
            "official_site": bool(off), "government": bool(off and off["government"] and off["match"] == "exact"),
            "candidates": qids, "verified_by": "wikidata"}
