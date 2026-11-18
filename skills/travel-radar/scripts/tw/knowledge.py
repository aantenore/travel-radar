"""Runtime knowledge with provenance and TTL.

Anything perishable (source reliability, booking windows, seasonality, entry rules) is
discovered at runtime, stored here with its sources, fetch date and TTL, and must be
re-verified once expired. Code validates the shape of each kind before storing it.
"""
import json
from datetime import datetime, timedelta, timezone

from . import schema, store


def _validate(kind, value):
    e = []
    if kind == "booking_window":
        d = value.get("ideal_days")
        if not (isinstance(d, list) and len(d) == 2 and all(isinstance(x, int) for x in d) and 1 <= d[0] < d[1] <= 365):
            e.append("booking_window.ideal_days must be [min, max] days before departure, 1-365, min < max")
    elif kind == "source":
        if value.get("tier") not in (0, 1, 2):
            e.append("source.tier must be 0, 1 or 2")
    if e:
        raise schema.ValidationError(e)


def put(con, kind, key, value, sources, ttl_days, verified_by="research", now=None):
    if not sources or not all(str(s).strip() for s in sources):
        raise schema.ValidationError([f"{kind} {key}: at least one source URL is required"])
    if not isinstance(ttl_days, int) or ttl_days < 1:
        raise schema.ValidationError(["ttl_days must be a positive integer"])
    _validate(kind, value)
    fetched = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    con.execute("INSERT INTO knowledge(kind, key, value, sources, fetched, ttl_days, verified_by) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(kind, key) DO UPDATE SET value=excluded.value, sources=excluded.sources,"
                " fetched=excluded.fetched, ttl_days=excluded.ttl_days, verified_by=excluded.verified_by",
                (kind, key, json.dumps(value), json.dumps(list(sources)), fetched, ttl_days, verified_by))
    con.commit()
    return {"kind": kind, "key": key, "value": value, "fetched": fetched, "ttl_days": ttl_days}


def _row(r, now):
    fetched = datetime.fromisoformat(r["fetched"])
    expires = fetched + timedelta(days=r["ttl_days"])
    return {"kind": r["kind"], "key": r["key"], "value": json.loads(r["value"]), "sources": json.loads(r["sources"]),
            "fetched": r["fetched"], "expires": expires.isoformat(), "fresh": now < expires,
            "verified_by": r["verified_by"]}


def get(con, kind, key, now=None):
    """The stored item with `fresh` computed now, or None. Expired items must be re-verified."""
    now = now or datetime.now(timezone.utc)
    r = con.execute("SELECT * FROM knowledge WHERE kind=? AND key=?", (kind, key)).fetchone()
    return _row(r, now) if r else None


def stale(con, now=None):
    now = now or datetime.now(timezone.utc)
    return [x for x in (_row(r, now) for r in con.execute("SELECT * FROM knowledge ORDER BY kind, key")) if not x["fresh"]]
