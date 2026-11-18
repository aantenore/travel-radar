"""Source reliability tiers and validation of flight price observations.

Tier 1 = airline official sites, tier 2 = meta-search queried with exact dates.
Anything else is context only and cannot become a price observation.
Patterns live in config (`reliability`), matched against host+path without "www.".
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
    r = cfg["reliability"]
    k = _key(url)
    if any(re.search(p, k) for p in r.get("tier1", []) + r.get("tier1_extra", [])):
        return 1
    if any(re.search(p, k) for p in r.get("tier2", []) + r.get("tier2_extra", [])):
        return 2
    return 0


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


def check_flight_observation(cfg, watch, url, depart, return_date, price=None, excerpt=None, extractor=None):
    """Return the source tier, or raise with the reason the observation is not acceptable.
    `price` is the number as shown on the page (local currency if converted)."""
    t = tier(cfg, url)
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
