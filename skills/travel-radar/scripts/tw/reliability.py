"""Source reliability tiers and validation of flight price observations.

Tier 1 = airline official site, tier 2 = travel search/booking site queried with exact dates,
anything else = context only. Tiers are decided only at runtime: a registry check (Wikidata)
or, when the registry cannot resolve the entity, independent sources found now. Verdicts are
cached with provenance and TTL in the data home. There are no domain lists anywhere.
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


def verify_source(cfg, con, url, name=None, iata=None, now=None):
    """Registry check (Wikidata). Unreachable or unresolved -> stored as unverified (tier 0,
    short TTL): its prices are context only until verified by the registry or `confirm_source`."""
    from . import knowledge, sources, wikidata
    domain = wikidata.registrable(host(url))
    r = cfg["reliability"]
    try:
        v = wikidata.verify(cfg, domain, name, iata)
        prov = [f"https://www.wikidata.org/wiki/{v['qid']}"] if v["qid"] else [cfg["wikidata"]["api"]]
        if v["tier"] or v["government"]:
            verified_by, ttl = "wikidata", r["verify_ttl_days"]
        else:
            v["note"] = ("registry did not resolve this domain to an airline or travel site; "
                         "confirm with independent sources (`tw sources confirm`) or treat as context only")
            verified_by, ttl = "unverified", r["unverified_ttl_days"]
    except sources.SourceError as e:
        v = {"domain": domain, "tier": 0, "note": f"registry unreachable: {e}"}
        prov, verified_by, ttl = [cfg["wikidata"]["api"]], "unverified", r["unverified_ttl_days"]
    value = {k: v.get(k) for k in ("tier", "qid", "label", "match", "note")}
    value["official_site"] = bool(v.get("official_site"))
    value["government"] = bool(v.get("government"))
    knowledge.put(con, "source", domain, value, prov, ttl, verified_by, now)
    return {"domain": domain, **value, "verified_by": verified_by, "ttl_days": ttl}


def confirm_source(cfg, con, url, tier, evidence, now=None, government=False):
    """Second runtime check when the registry cannot decide: at least N independent sites found
    now, none of them the candidate, each with a verbatim excerpt naming the candidate domain
    as the official airline (tier 1), travel search/booking site (tier 2) or, with
    `government`, the official site of a government body (tier 0)."""
    from . import knowledge, wikidata
    domain = wikidata.registrable(host(url))
    r = cfg["reliability"]
    errors = []
    if tier not in (1, 2) and not (government and tier == 0):
        errors.append("--tier must be 1 (airline) or 2 (travel search/booking site), or 0 with --government")
    seen = set()
    for i, (src, excerpt) in enumerate(evidence):
        d = wikidata.registrable(host(src))
        if d == domain or wikidata.brand(d) == wikidata.brand(domain):
            errors.append(f"evidence {i + 1}: {d} is the candidate itself, not independent")
        elif d in seen:
            errors.append(f"evidence {i + 1}: {d} repeats a site already given")
        elif domain not in (excerpt or "").lower():
            errors.append(f"evidence {i + 1}: excerpt does not mention {domain}")
        else:
            seen.add(d)
    if len(seen) < r["independent_min_sources"]:
        errors.append(f"need {r['independent_min_sources']} independent sites, got {len(seen)} valid")
    if errors:
        raise schema.ValidationError(errors)
    value = {"tier": tier, "qid": None, "label": None, "match": "independent_sources",
             "note": "; ".join(f"{src}: {ex[:160]}" for src, ex in evidence),
             "official_site": True, "government": bool(government)}
    knowledge.put(con, "source", domain, value, [src for src, _ in evidence], r["independent_ttl_days"],
                  "independent_sources", now)
    return {"domain": domain, **value, "verified_by": "independent_sources", "ttl_days": r["independent_ttl_days"]}


def resolve(cfg, con, url, now=None):
    """Cached runtime verdict for the URL's domain: {tier, verified_by} or None if missing/expired."""
    from . import knowledge, wikidata
    k = knowledge.get(con, "source", wikidata.registrable(host(url)), now)
    if not k or not k["fresh"]:
        return None
    return {"tier": k["value"]["tier"], "verified_by": k["verified_by"],
            "official_site": bool(k["value"].get("official_site")), "government": bool(k["value"].get("government"))}


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
                             source_tier=0):
    """Return the source tier, or raise with the reason the observation is not acceptable.
    `price` is the number as shown on the page (local currency if converted).
    `source_tier` is the runtime verdict from `resolve`; unverified sources are tier 0."""
    t = source_tier or 0
    errors = []
    if not extractor:
        errors.append("--extractor is required (fare-reader or main)")
    if price is not None and not price_in_excerpt(price, excerpt):
        errors.append(f"price {price} does not appear in --excerpt: copy the verbatim page text that shows it")
    if t == 0:
        errors.append(f"{host(url)} is not verified as an airline (tier 1) or travel search/booking site (tier 2): context only")
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
