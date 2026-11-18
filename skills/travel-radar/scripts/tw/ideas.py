"""Idea inbox: places or wishes to keep an eye on, with season flags and promotion to a trip."""
from datetime import date, datetime, timedelta, timezone

from . import schema, store, trips


def _months(s):
    if not s:
        return []
    ms = sorted({int(x) for x in str(s).split(",") if x.strip()})
    if any(m < 1 or m > 12 for m in ms):
        raise schema.ValidationError([f"best months must be 1-12, got {s!r}"])
    return ms


def add(con, text, best_months=None):
    if not text or not text.strip():
        raise schema.ValidationError(["idea text is required"])
    ms = _months(best_months)
    cur = con.execute("INSERT INTO ideas(text, best_months, created, status) VALUES (?,?,?, 'open')",
                      (text.strip(), ",".join(map(str, ms)) or None, store.now_iso()))
    con.commit()
    return {"id": cur.lastrowid, "text": text.strip(), "best_months": ms}


def _row(con, idea_id):
    r = con.execute("SELECT * FROM ideas WHERE id=?", (idea_id,)).fetchone()
    if not r:
        raise ValueError(f"idea not found: {idea_id}")
    return dict(r)


def list_all(con, include_closed=False):
    q = "SELECT * FROM ideas" + ("" if include_closed else " WHERE status='open'") + " ORDER BY id"
    return [dict(r) for r in con.execute(q)]


def season(best_months, today, lead_months):
    """Months until the nearest best month (0 = in season) and whether it is within the lead time."""
    ms = _months(best_months)
    if not ms:
        return None
    gap = min((m - today.month) % 12 for m in ms)
    return {"months_to_best": gap, "in_season": gap == 0, "approaching": gap <= lead_months}


def due(cfg, con, now=None):
    """Open ideas whose low-frequency check is due, with deterministic season flags."""
    now = now or datetime.now(timezone.utc)
    c = cfg["ideas"]
    out = []
    for i in list_all(con):
        last = i["last_checked"]
        if last and now - datetime.fromisoformat(last) < timedelta(days=c["check_every_days"]):
            continue
        out.append({"id": i["id"], "text": i["text"], "last_checked": last,
                    "season": season(i["best_months"], now.date(), c["season_lead_months"])})
    return out


def checked(con, idea_id, note=None):
    _row(con, idea_id)
    con.execute("UPDATE ideas SET last_checked=?, note=? WHERE id=?", (store.now_iso(), note, idea_id))
    con.commit()
    return {"checked": idea_id}


def drop(con, idea_id):
    _row(con, idea_id)
    con.execute("UPDATE ideas SET status='dropped' WHERE id=?", (idea_id,))
    con.commit()
    return {"dropped": idea_id}


def promote(cfg, con, idea_id, trip_id):
    i = _row(con, idea_id)
    if i["status"] != "open":
        raise schema.ValidationError([f"idea {idea_id} is {i['status']}, not open"])
    if (trips._dir(cfg, trip_id) / "trip.yaml").exists():
        raise schema.ValidationError([f"trip {trip_id} already exists"])
    trips.save(cfg, {"id": trip_id, "request": i["text"], "status": "planning", "from_idea": idea_id,
                     "best_months": _months(i["best_months"]), "options": []})
    con.execute("UPDATE ideas SET status='promoted', promoted_to=? WHERE id=?", (trip_id, idea_id))
    con.commit()
    return {"promoted": idea_id, "trip": trip_id, "next": "plan it with skill section 1"}
