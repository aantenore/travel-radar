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


def host(url):
    return _key(url).split("/", 1)[0]


def _d(v, name):
    try:
        return v if isinstance(v, date) else date.fromisoformat(str(v))
    except ValueError:
        raise schema.ValidationError([f"--{name} must be a date YYYY-MM-DD, got {v!r}"])


def check_flight_observation(cfg, watch, url, depart, return_date):
    """Return the source tier, or raise with the reason the observation is not acceptable."""
    t = tier(cfg, url)
    errors = []
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
