"""Source reliability tiers and validation of flight price observations.

Tier 1 = airline official site, tier 2 = travel search/booking site queried with exact dates,
anything else = context only. The authority is runtime verification (Wikidata, cached with a
TTL in the knowledge store). The regex lists in config are an offline fallback only, and
observations relying on them are marked `verified_by: fallback`.
"""
import re
from datetime import date
from urllib.parse import urlparse

from . import schema


def _key(url):
    u = urlparse(url if "://" in url else "https://" + url)
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host + (u.path or "/").lower()


def tier(cfg, url):
    """Offline fallback tier from the config regex lists (never the authority)."""
    r = cfg["reliability"]
    fb = r.get("fallback", {})
    k = _key(url)
    if any(re.search(p, k) for p in fb.get("tier1", []) + r.get("tier1_extra", [])):
        return 1
    if any(re.search(p, k) for p in fb.get("tier2", []) + r.get("tier2_extra", [])):
        return 2
    return 0


def verify_source(cfg, con, url, name=None, iata=None, now=None):
    """Verify a source at runtime and cache the verdict with provenance and TTL."""
    from . import knowledge, sources, wikidata
    domain = wikidata.registrable(host(url))
    r = cfg["reliability"]
    try:
        v = wikidata.verify(cfg, domain, name, iata)
        prov = [f"https://www.wikidata.org/wiki/{v['qid']}"] if v["qid"] else [cfg["wikidata"]["api"]]
        verified_by, ttl = "wikidata", r["verify_ttl_days"]
        if v["tier"] == 0 and tier(cfg, url):
            v.update(tier=tier(cfg, url), note="not classified as airline/travel site on Wikidata; offline fallback list used")
            verified_by, ttl = "fallback", r["fallback_ttl_days"]
    except sources.SourceError as e:
        v = {"domain": domain, "tier": tier(cfg, url), "note": f"Wikidata unreachable: {e}"}
        prov, verified_by, ttl = ["config:reliability.fallback"], "fallback", r["fallback_ttl_days"]
    value = {k: v.get(k) for k in ("tier", "qid", "label", "match", "note")}
    knowledge.put(con, "source", domain, value, prov, ttl, verified_by, now)
    return {"domain": domain, **value, "verified_by": verified_by, "ttl_days": ttl}


def resolve(cfg, con, url, now=None):
    """Cached runtime verdict for the URL's domain: {tier, verified_by} or None if missing/expired."""
    from . import knowledge, wikidata
    k = knowledge.get(con, "source", wikidata.registrable(host(url)), now)
    if not k or not k["fresh"]:
        return None
    return {"tier": k["value"]["tier"], "verified_by": k["verified_by"]}


def independent(cfg, a, b):
    """Two observations confirm each other if they come from different tier 1/2 sites, or if at
    least one of them was re-read by an independent extractor (the main model)."""
    if host(a["url"]) != host(b["url"]):
        return a.get("tier") in (1, 2) and b.get("tier") in (1, 2)
    indep = set(cfg["reliability"]["independent_extractors"])
    return a.get("extractor") != b.get("extractor") and bool({a.get("extractor"), b.get("extractor")} & indep)


def host(url):
    return _key(url).split("/", 1)[0]


def _d(v, name):
    try:
        return v if isinstance(v, date) else date.fromisoformat(str(v))
    except ValueError:
        raise schema.ValidationError([f"--{name} must be a date YYYY-MM-DD, got {v!r}"])


# Grouped thousands (1.234 / 1,234 / 1 234 / 1'234, optional decimals) or a plain number.
_GROUPED = re.compile(r"\d{1,3}(?:[ \u00a0\u202f.,']\d{3})+(?:[.,]\d{1,2})?(?!\d)")
_PLAIN = re.compile(r"\d+(?:[.,]\d{1,2})?(?!\d)")


def _readings(token):
    """Possible numeric values of a price token under both decimal conventions."""
    t = re.sub(r"[\u00a0\u202f' ]", "", token)
    out = set()
    for dec in (".", ","):
        grp = "," if dec == "." else "."
        u = t.replace(grp, "")
        if u.count(dec) <= 1:
            try:
                out.add(float(u.replace(dec, ".")))
            except ValueError:
                pass
    return out


def price_in_excerpt(price, excerpt):
    """True if the number `price` appears in the verbatim page excerpt (1.234,56 / 1,234.56 / 1 234 forms)."""
    if not excerpt:
        return False
    tokens = _GROUPED.findall(excerpt) + _PLAIN.findall(excerpt)
    return any(abs(v - float(price)) < 0.01 for tok in tokens for v in _readings(tok))


def check_flight_observation(cfg, watch, url, depart, return_date, price=None, excerpt=None, extractor=None,
                             source_tier=None):
    """Return the source tier, or raise with the reason the observation is not acceptable.
    `price` is the number as shown on the page (local currency if converted).
    `source_tier` comes from `resolve`; without it the offline fallback list is used."""
    t = tier(cfg, url) if source_tier is None else source_tier
    errors = []
    if not extractor:
        errors.append("--extractor is required (fare-reader or main)")
    if price is not None and not price_in_excerpt(price, excerpt):
        errors.append(f"price {price} does not appear in --excerpt: copy the verbatim page text that shows it")
    if t == 0:
        errors.append(f"{host(url)} is not a tier 1 (airline) or tier 2 (meta-search) source: use it as context only")
    if not depart:
        errors.append("--depart is required: the price must refer to exact dates")
    one_way = bool(watch.get("one_way"))
    if not return_date and not one_way:
        errors.append("--return is required (set one_way: true on the watch for one-way fares)")
    if errors:
        raise schema.ValidationError(errors)
    dep = _d(depart, "depart")
    win = watch.get("depart_window") or {}
    lo, hi = _d(win["from"], "from"), _d(win["to"], "to") if win.get("to") else None
    if dep < lo or (hi and dep > hi):
        errors.append(f"--depart {dep} is outside the watch window {lo}..{hi or ''}")
    if return_date:
        ret = _d(return_date, "return")
        if ret <= dep:
            errors.append("--return must be after --depart")
        lens = watch.get("trip_length_days")
        if lens and not lens[0] <= (ret - dep).days <= lens[-1]:
            errors.append(f"trip length {(ret - dep).days} days is outside {lens}")
    if errors:
        raise schema.ValidationError(errors)
    return t
