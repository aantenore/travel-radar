"""Verify at runtime that a price source is an airline (tier 1) or a travel search/booking site
(tier 2), using Wikidata: entity class (P31/P279*), official website (P856), IATA code (P229).

The model proposes candidates (domain, plus entity name and/or IATA code); this module decides.
Wikidata unreachable or unresolved -> the source stays unverified (see reliability.confirm_source).
"""
import re
import urllib.error
import urllib.parse
import urllib.request

from . import sources, urls

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
    return urls.try_parse(url)[0]


def match_domain(domain, official_sites):
    """'exact' / 'brand' / None for a bare domain (no path). Path-scoped official sites do not
    match a bare domain: use `match_site` with the full URL."""
    ms = match_site(domain, official_sites)
    return ms[0] if ms else None


def _split(url):
    """Lenient wrapper for registry data: unparseable official sites are ignored."""
    return urls.try_parse(url)


def match_site(url, official_sites):
    """(match, scope_path) of `url` against registered official websites, or None.

    - root website (https://www.kayak.com) → matches the whole domain (scope None);
    - website with a path (https://www.google.com/travel/flights) → matches only URLs under
      that path prefix (scope "/travel/flights"); the rest of the domain does not inherit it.
    `match` is 'exact' (same registrable domain) or 'brand' (country variant of the brand)."""
    host, path = _split(url)
    best = None
    for site in official_sites:
        sh, sp = _split(site)
        if not sh:
            continue
        if registrable(sh) == registrable(host):
            m = "exact"
        elif brand(sh) == brand(host):
            m = "brand"
        else:
            continue
        if sp and not (path + "/").startswith(sp + "/"):
            continue
        cand = (m, sp or None)
        rank = (0 if m == "exact" else 1, 0 if sp else 1)          # exact first, then most specific
        if best is None or rank < best[0]:
            best = (rank, cand)
    return best[1] if best else None


_REDIRECTS = {}


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _narrower_or_sibling(orig_path, new_path):
    """A redirect may move a site sideways or deeper, never widen it: the target keeps a
    non-empty path and is not an ancestor of the original path."""
    if not new_path:
        return False
    return not (orig_path + "/").startswith(new_path + "/")


def redirect_chain(cfg, site):
    """Same-host URLs a registered official site redirects to (bounded hops, HEAD, no body).
    Official sites move (e.g. /flights -> /travel/flights). Safety rules:
    - only http/https, and only hosts resolving to public addresses (no internal targets);
    - hops to another host stop the chain;
    - a target must keep a non-empty path and must not be an ancestor of the original path,
      so a redirect never widens the verified scope to the whole domain."""
    if site in _REDIRECTS:
        return _REDIRECTS[site]
    out, cur = [], site
    host0, path0 = urls.try_parse(site)
    opener = urls.safe_opener(_NoFollow())
    for _ in range(cfg["wikidata"]["redirect_max_hops"]):
        if not urls.safe_outbound(cur):
            break
        req = urllib.request.Request(cur, method="HEAD", headers={"User-Agent": cfg["http"]["user_agent"]})
        try:
            opener.open(req, timeout=cfg["http"]["timeout_s"])
            break
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if 300 <= e.code < 400 else None
            if not loc:
                break
            nxt = urllib.parse.urljoin(cur, loc)
            h, p = urls.try_parse(nxt)
            if h != host0 or not _narrower_or_sibling(path0, p):
                break
            out.append(nxt)
            cur = nxt
        except Exception:
            break
    _REDIRECTS[site] = out
    return out


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
    ctry = " ".join(f"wd:{q}" for q in w.get("country_classes", [])) or "wd:Q0"
    q = (f"SELECT ?item ?itemLabel ?itemDescription ?site ?iata ?air ?trav ?gov ?ctry WHERE {{ VALUES ?item {{ {vals} }} "
         f"OPTIONAL {{ ?item wdt:P856 ?site }} OPTIONAL {{ ?item wdt:P229 ?iata }} "
         f"BIND(EXISTS {{ ?item (wdt:P31|wdt:P279)/wdt:P279* ?a . VALUES ?a {{ {air} }} }} AS ?air) "
         f"BIND(EXISTS {{ ?item (wdt:P31|wdt:P279)/wdt:P279* ?t . VALUES ?t {{ {trav} }} }} AS ?trav) "
         f"BIND(EXISTS {{ ?item (wdt:P31|wdt:P279)/wdt:P279* ?g . VALUES ?g {{ {gov} }} }} AS ?gov) "
         f"BIND(EXISTS {{ ?item wdt:P31 ?c . VALUES ?c {{ {ctry} }} }} AS ?ctry) "
         f'SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}')
    out = {}
    for b in _sparql(cfg, q):
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        e = out.setdefault(qid, {"label": b.get("itemLabel", {}).get("value"), "sites": set(), "iata": set(),
                                 "airline": False, "travel": False, "gov": False,
                                 "description": b.get("itemDescription", {}).get("value", "")})
        if "site" in b:
            e["sites"].add(b["site"]["value"])
        if "iata" in b:
            e["iata"].add(b["iata"]["value"])
        e["airline"] |= b["air"]["value"] == "true"
        e["travel"] |= b["trav"]["value"] == "true"
        e["gov"] |= b.get("gov", {}).get("value") == "true"
        e["country"] = e.get("country", False) or b.get("ctry", {}).get("value") == "true"
    return out


def classify(url, entities, description_terms=()):
    """Pure decision: best (tier, qid, label, match, scope, signal) for `url` among described entities.
    Tier 1 beats tier 2; an exact domain match beats a brand match; class beats description.
    A class (airline / travel service, P31 or P279 chain) is the primary signal. Only when the
    official website already matches the page, the entity description may stand in for the
    class (signal 'description'), for services the registry does not classify."""
    best, best_rank = (0, None, None, None, None, None), None
    terms = [t.lower() for t in description_terms]
    for qid, e in entities.items():
        ms = match_site(url, e["sites"])
        if not ms:
            continue
        t, signal = (1, "class") if e["airline"] else ((2, "class") if e["travel"] else (0, None))
        if not t and ms[0] == "exact" and any(term in (e.get("description") or "").lower() for term in terms):
            t, signal = 2, "description"
        if not t:
            continue
        rank = (t, 0 if signal == "class" else 1, 0 if ms[0] == "exact" else 1, 0 if ms[1] else 1)
        if best_rank is None or rank < best_rank:
            best, best_rank = (t, qid, e["label"], ms[0], ms[1], signal), rank
    return best


def official(url, entities):
    """Pure decision: an entity whose official website matches `url` (exact or brand, path-aware),
    and whether that entity is a government body / diplomatic mission / intergovernmental org."""
    best = None
    for qid, e in entities.items():
        ms = match_site(url, e["sites"])
        if not ms:
            continue
        m = ms[0]
        g = is_government(e)
        if best is None or (m == "exact" and best["match"] != "exact") or (g and not best["government"]):
            best = {"entity": qid, "label": e["label"], "match": m, "scope": ms[1], "government": g}
    return best


def parent_hosts(host, depth):
    """Parents of `host` that stay inside its registrable domain (never crossing a public-suffix
    boundary): consolato.esteri.it -> [esteri.it]; adelaide.china-consulate.gov.cn ->
    [china-consulate.gov.cn]; gov.cn is never a parent of a site registered under it.
    The registrable domain comes from a heuristic, not the full Public Suffix List."""
    labels = host.lower().split(".")
    if labels and labels[0] == "www":
        labels = labels[1:]
    reg = registrable(".".join(labels))
    out = []
    for i in range(1, len(labels)):
        cand = ".".join(labels[i:])
        if not (cand == reg or cand.endswith("." + reg)):
            break
        out.append(cand)
    return out[:depth]


def government_parent(cfg, host):
    """First parent domain whose own official website belongs to a government body (or a
    country's portal), verified on the registry. Fail-closed: errors propagate."""
    for parent in parent_hosts(host, cfg["wikidata"]["government_parent_depth"]):
        ents = describe(cfg, by_site(cfg, parent))
        off = official("https://" + parent, ents)
        if off and off["government"] and off["match"] == "exact":
            return {"parent": parent, "entity": off["entity"], "label": off["label"]}
    return None


def is_government(entity):
    """Government body (class chain), or a country item (direct P31) whose own website it is."""
    return bool(entity.get("gov") or entity.get("country"))


def verify(cfg, url, name=None, iata=None):
    """Raises sources.SourceError if Wikidata cannot be reached. `url` may carry a path."""
    domain = urls.parse(url)[0]
    qids = by_site(cfg, domain)
    if iata:
        qids += by_iata(cfg, iata)
    for n in filter(None, [name, brand(domain)]):
        qids += search(cfg, n)
    qids = list(dict.fromkeys(qids))
    ents = describe(cfg, qids)
    page_reg = registrable(domain)
    for e in ents.values():
        for site in list(e["sites"]):
            sh, sp = _split(site)
            if sp and sh and registrable(sh) == page_reg:
                e["sites"].update(redirect_chain(cfg, site))
    tier, qid, label, match, scope, signal = classify(url, ents, cfg["wikidata"].get("travel_description_terms", ()))
    off = official(url, ents)
    gov_parent = None
    if off is None:     # never replace an existing exact/brand match to a non-government entity
        gov_parent = government_parent(cfg, domain)
        if gov_parent:
            off = {"entity": gov_parent["entity"], "label": gov_parent["label"], "match": "exact",
                   "scope": None, "government": True}
    return {"domain": registrable(domain), "scope": scope if tier else (off or {}).get("scope"), "signal": signal,
            "government_via_parent": gov_parent["parent"] if gov_parent else None,
            "tier": tier, "qid": qid or (off or {}).get("entity"),
            "label": label or (off or {}).get("label"), "match": match or (off or {}).get("match"),
            "official_site": bool(off), "government": bool(off and off["government"] and off["match"] == "exact"),
            "candidates": qids, "verified_by": "wikidata"}
