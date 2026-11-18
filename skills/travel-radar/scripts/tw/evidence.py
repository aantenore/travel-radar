"""Shared rules for sourced items (requirements, geopolitics, experiences).

`official` is never taken from the model: code sets it from the runtime source verdicts
(`tw sources verify` / `confirm`). Unverified sources count as non-official context.
"""
from datetime import date, datetime, timezone

from . import reliability, schema
from .wikidata import registrable


def as_date(v):
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def annotate(cfg, con, sources, where, now=None):
    """Validate and annotate [{url, accessed}] with code-computed official/verified/outlet."""
    if not sources:
        raise schema.ValidationError([f"{where}: at least one source is required"])
    out, errors = [], []
    for i, s in enumerate(sources):
        if not isinstance(s, dict) or not s.get("url") or not s.get("accessed"):
            errors.append(f"{where}.sources[{i}]: needs url and accessed date")
            continue
        try:
            as_date(s["accessed"])
        except ValueError:
            errors.append(f"{where}.sources[{i}].accessed is not a date")
            continue
        v = reliability.resolve(cfg, con, s["url"], now) if con is not None else None
        out.append({"url": s["url"], "accessed": str(s["accessed"]),
                    "outlet": registrable(reliability.host(s["url"])),
                    # an inherited (parent-domain) government verdict never counts as official on its own
                    "official": bool(v and v["government"] and not v.get("government_inherited")),
                    "official_inherited": bool(v and v["government"] and v.get("government_inherited")),
                    "official_site": bool(v and v["official_site"]),
                    "verified": v is not None})
    if errors:
        raise schema.ValidationError(errors)
    return out


def independent_count(sources):
    return len({s["outlet"] for s in sources})


def newest(sources):
    return max(as_date(s["accessed"]) for s in sources)


def stale(sources, ttl_days, today=None):
    today = today or datetime.now(timezone.utc).date()
    return (today - newest(sources)).days > ttl_days


def option_departure(trip, option_id=None):
    opts = trip.get("options") or []
    pick = [o for o in opts if o.get("id") == (option_id or trip.get("chosen_option"))] or opts
    deps = [as_date(o["period"]["from"]) for o in pick if (o.get("period") or {}).get("from")]
    return min(deps) if deps else None


def option_window(trip, option_id=None):
    opts = trip.get("options") or []
    pick = [o for o in opts if o.get("id") == (option_id or trip.get("chosen_option"))]
    p = (pick[0].get("period") if pick else None) or {}
    return (as_date(p["from"]), as_date(p["to"])) if p.get("from") and p.get("to") else None
