"""Experiences per trip option, with how and where to book. Researched at runtime; rules here.

Stored in trips/<id>/experiences.yaml. Code rules:
- every experience has sources and at least one profile-based reason (`fits_profile`);
- `online_link` needs a URL; other booking methods need instructions;
- a price needs amount, currency and source;
- dates must fall inside the option's trip window;
- the booking link's provider is checked against runtime source verdicts: an official site or a
  verified travel site is `verified`, anything else is shown as an unverified link.
"""
from datetime import datetime, timedelta, timezone

import yaml

from . import evidence, reliability, schema, trips

METHODS = ("online_link", "on_site", "contact_operator", "local_app", "via_accommodation", "not_bookable")


def _path(cfg, trip_id):
    return trips._dir(cfg, trip_id) / "experiences.yaml"


def load(cfg, trip_id):
    p = _path(cfg, trip_id)
    return (yaml.safe_load(p.read_text()) or {}).get("items", []) if p.exists() else []


def _normalize(cfg, con, trip, it, profile_keys, now):
    where = f"experience {it.get('id')}"
    e = []
    for k in ("id", "name", "place", "option"):
        if not it.get(k):
            e.append(f"{where}: {k} is required")
    fits = it.get("fits_profile") or []
    if not fits or not all(isinstance(f, dict) and f.get("field") and f.get("reason") for f in fits):
        e.append(f"{where}: fits_profile needs at least one {{field, reason}} citing the profile")
    elif any(f["field"] not in profile_keys for f in fits):
        e.append(f"{where}: fits_profile.field must be a profile field, got {[f['field'] for f in fits if f['field'] not in profile_keys]}")
    b = it.get("booking") or {}
    if b.get("method") not in METHODS:
        e.append(f"{where}: booking.method must be one of {METHODS}")
    elif b["method"] == "online_link" and not b.get("url"):
        e.append(f"{where}: booking.url is required for online_link")
    elif b["method"] != "online_link" and not b.get("instructions"):
        e.append(f"{where}: booking.instructions are required when booking is not an online link")
    price = it.get("price")
    if price is not None and not (isinstance(price, dict) and "amount" in price and price.get("currency") and price.get("source")):
        e.append(f"{where}: price needs amount, currency and source")
    win = evidence.option_window(trip, it.get("option"))
    when = it.get("when") or {}
    if win and when.get("from"):
        lo, hi = evidence.as_date(when["from"]), evidence.as_date(when.get("to") or when["from"])
        if hi < win[0] or lo > win[1]:
            e.append(f"{where}: dates {lo}..{hi} are outside the trip window {win[0]}..{win[1]}")
    if e:
        raise schema.ValidationError(e)
    out = dict(it)
    out["sources"] = evidence.annotate(cfg, con, it.get("sources"), where, now)
    if b.get("url"):
        v = reliability.resolve(cfg, con, b["url"], now)
        ok = bool(v and (v["official_site"] or v["tier"] in (1, 2)))
        out["booking"] = {**b, "verified_by": (v["verified_by"] if ok else "unverified")}
    lt = b.get("lead_time_days")
    if isinstance(lt, int) and when.get("from"):
        out["booking_deadline"] = (evidence.as_date(when["from"]) - timedelta(days=lt)).isoformat()
    elif isinstance(lt, int) and win:
        out["booking_deadline"] = (win[0] - timedelta(days=lt)).isoformat()
    return out


def add(cfg, con, trip_id, items, profile_keys, now=None):
    trip = trips.load(cfg, trip_id)
    norm = [_normalize(cfg, con, trip, it, profile_keys, now) for it in items]
    by_id = {x["id"]: x for x in load(cfg, trip_id)}
    by_id.update({x["id"]: x for x in norm})
    _path(cfg, trip_id).write_text(yaml.safe_dump({"items": list(by_id.values())}, sort_keys=False, allow_unicode=True))
    return {"saved": [x["id"] for x in norm],
            "unverified_links": [x["id"] for x in norm if (x.get("booking") or {}).get("verified_by") == "unverified"]}


def check(cfg, trip_id, today=None):
    today = today or datetime.now(timezone.utc).date()
    ttl = cfg["experiences"]["ttl_days"]
    items = load(cfg, trip_id)
    return {"count": len(items),
            "stale": [i["id"] for i in items if evidence.stale(i["sources"], ttl, today)],
            "deadlines": sorted(({"id": i["id"], "deadline": i["booking_deadline"], "name": i["name"]}
                                 for i in items if i.get("booking_deadline")), key=lambda d: d["deadline"])}
