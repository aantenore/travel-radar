"""Trip lifecycle: validated status transitions, park/resume with snapshot diff, done."""
from datetime import date

import yaml

from . import config, dossier, schema, scoring, sources, store, watches

STATUSES = ("planning", "chosen", "monitoring", "parked", "done")
# "parked" is left only through resume() (back to the previous status) or done().
TRANSITIONS = {
    "planning": {"chosen", "parked", "done"},
    "chosen": {"planning", "monitoring", "parked", "done"},
    "monitoring": {"chosen", "parked", "done"},
    "parked": {"done"},
    "done": set(),
}


def _dir(cfg, trip_id):
    return config.path(cfg, "trips_dir") / trip_id


def load(cfg, trip_id):
    p = _dir(cfg, trip_id) / "trip.yaml"
    if not p.exists():
        raise ValueError(f"trip not found: {trip_id} (expected {p})")
    t = config.load_yaml(p)
    t.setdefault("id", trip_id)
    t.setdefault("status", "planning")
    return t


def save(cfg, trip):
    d = _dir(cfg, trip["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "trip.yaml").write_text(yaml.safe_dump(trip, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_dossier(cfg, trip_id):
    p = _dir(cfg, trip_id) / "dossier.yaml"
    return config.load_yaml(p) if p.exists() else None


def chosen_option(trip):
    oid = trip.get("chosen_option")
    for o in trip.get("options") or []:
        if o.get("id") == oid:
            return o
    return None


def _as_date(v):
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def period(trip):
    o = chosen_option(trip)
    p = (o or {}).get("period") or {}
    if p.get("from") and p.get("to"):
        return _as_date(p["from"]), _as_date(p["to"])
    return None


def list_all(cfg):
    out = []
    for f in sorted(config.path(cfg, "trips_dir").glob("*/trip.yaml")):
        t = config.load_yaml(f)
        tid = t.get("id", f.parent.name)
        p = (t.get("parked") or {})
        out.append({"id": tid, "status": t.get("status", "planning"), "chosen_option": t.get("chosen_option"),
                    "request": t.get("request"), "parked_since": p.get("since"), "parked_until": p.get("until"),
                    "parked_reason": p.get("reason")})
    return out


def _check(trip, to):
    cur = trip.get("status", "planning")
    if to not in STATUSES:
        raise schema.ValidationError([f"unknown status {to!r}; valid: {', '.join(STATUSES)}"])
    if to not in TRANSITIONS[cur]:
        hint = " (use `tw trip resume`)" if cur == "parked" else ""
        raise schema.ValidationError([f"transition {cur} -> {to} not allowed{hint}; allowed: {sorted(TRANSITIONS[cur]) or 'none'}"])


def user_confirmation(evidence, today=None):
    """The user's explicit yes, quoted. Required to start or restart monitoring."""
    if not evidence or not str(evidence).strip():
        raise schema.ValidationError(["--confirmed-by-user is required: quote the user's explicit yes to the proposed monitoring plan"])
    return {"evidence": str(evidence).strip(), "date": (today or date.today()).isoformat()}


def set_status(cfg, trip_id, to, option=None, confirmed_by_user=None):
    trip = load(cfg, trip_id)
    _check(trip, to)
    if to == "monitoring":
        trip["monitoring_confirmed_by_user"] = user_confirmation(confirmed_by_user)
    if to == "chosen":
        if not option or not any(o.get("id") == option for o in trip.get("options") or []):
            raise schema.ValidationError([f"--option must name one of the trip options to choose"])
        trip["chosen_option"] = option
    if to == "monitoring" and not trip.get("chosen_option"):
        raise schema.ValidationError(["choose an option first (status chosen)"])
    old = trip["status"]
    trip["status"] = to
    save(cfg, trip)
    return {"trip": trip_id, "from": old, "to": to}


def _trip_watch_files(cfg, trip_id):
    for f in sorted(config.path(cfg, "watches_dir").glob("*.yaml")):
        w = config.load_yaml(f)
        if w.get("trip") == trip_id:
            yield f, w


def _save_watch(f, w):
    f.write_text(yaml.safe_dump(w, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _dossier_state(cfg, trip_id, today):
    d = load_dossier(cfg, trip_id)
    if d is None:
        return None
    checklist = config.load_yaml(config.path(cfg, "dossier_checklist"))
    res = dossier.check(checklist, d, today)
    items = {k: (v or {}).get("status", "unknown") for k, v in (d.get("items") or {}).items()}
    return {"items": items, "issues": res["issues"], "blockers": res["blockers"], "warnings": res["warnings"]}


def _holidays(cfg, trip):
    country, per = trip.get("country"), period(trip)
    if not country or not per:
        return None
    try:
        rows = []
        for y in sorted({per[0].year, per[1].year}):
            rows += sources.holidays(cfg, country, y, per[0].isoformat(), per[1].isoformat())["holidays"]
        return sorted({f"{r['date']} {r['name']}" for r in rows})
    except sources.SourceError as e:
        return {"error": str(e)}


def _scores(cfg, trip):
    try:
        schema.trip(trip)
        return {r["id"]: r["score"] for r in scoring.score_trip(cfg, config.profile(cfg), trip)["ranking"]}
    except schema.ValidationError:
        return None


def snapshot(cfg, con, trip, today):
    prices = {}
    for _, w in _trip_watch_files(cfg, trip["id"]):
        r = watches.report(con, w)
        prices[w["id"]] = {"min_eur": r["min_eur"], "last_eur": (r["last"] or {}).get("price_eur")}
    return {"taken": today.isoformat(), "prices": prices, "dossier": _dossier_state(cfg, trip["id"], today),
            "scores": _scores(cfg, trip), "holidays": _holidays(cfg, trip)}


def park(cfg, con, trip_id, reason, until=None, today=None):
    today = today or date.today()
    trip = load(cfg, trip_id)
    _check(trip, "parked")
    if until is not None:
        until = _as_date(until)
        if until <= today:
            raise schema.ValidationError(["--until must be a future date"])
    paused = []
    for f, w in _trip_watch_files(cfg, trip_id):
        if w.get("active", True):
            w["active"] = False
            w["paused_by_trip"] = True
            _save_watch(f, w)
            paused.append(w["id"])
    trip["parked"] = {"since": today.isoformat(), "reason": reason, "until": until.isoformat() if until else None,
                      "previous_status": trip["status"], "paused_watches": paused,
                      "snapshot": snapshot(cfg, con, trip, today)}
    trip["status"] = "parked"
    save(cfg, trip)
    return {"trip": trip_id, "parked": True, "paused_watches": paused, "until": trip["parked"]["until"]}


def _diff_prices(snap, now):
    out = {}
    for wid, cur in now.items():
        before = snap.get(wid) or {}
        b, n = before.get("min_eur"), cur.get("min_eur")
        out[wid] = {"min_before_eur": b, "min_now_eur": n,
                    "change_pct": round(100 * (n - b) / b, 1) if b and n else None}
    return out


def resume(cfg, con, trip_id, today=None, confirmed_by_user=None):
    """Without confirmation: preview only (what changed, which watches would restart), nothing saved.
    With the user's quoted yes: reactivate the paused watches and restore the previous status."""
    today = today or date.today()
    trip = load(cfg, trip_id)
    parked = trip.get("parked")
    if trip.get("status") != "parked" or not parked:
        raise schema.ValidationError([f"trip {trip_id} is not parked"])
    apply = confirmed_by_user is not None
    confirmation = user_confirmation(confirmed_by_user, today) if apply else None
    resumed = []
    for f, w in _trip_watch_files(cfg, trip_id):
        if w.get("paused_by_trip"):
            resumed.append(w["id"])
            if apply:
                w.pop("paused_by_trip")
                w["active"] = True
                w["confirmed_by_user"] = confirmation
                _save_watch(f, w)
    snap = parked.get("snapshot") or {}
    now = snapshot(cfg, con, trip, today)
    d_before, d_now = snap.get("dossier") or {}, now.get("dossier") or {}
    status_changes = {k: {"before": d_before.get("items", {}).get(k), "now": v}
                      for k, v in (d_now.get("items") or {}).items()
                      if d_before.get("items", {}).get(k) != v}
    stale = [i["item"] for i in d_now.get("issues", []) if i["problem"].startswith("stale")]
    h_before, h_now = snap.get("holidays"), now.get("holidays")
    new_holidays = sorted(set(h_now) - set(h_before or [])) if isinstance(h_now, list) else h_now
    days = (today - _as_date(parked["since"])).days
    if apply:
        trip["status"] = parked["previous_status"]
        trip.setdefault("history", []).append({**{k: v for k, v in parked.items() if k != "snapshot"},
                                               "resumed": today.isoformat(), "confirmed_by_user": confirmation})
        del trip["parked"]
        save(cfg, trip)
    return {
        "trip": trip_id, "preview": not apply,
        "status": trip["status"] if apply else f"parked (would return to {parked['previous_status']})",
        "days_parked": days, ("resumed_watches" if apply else "watches_to_restart"): resumed,
        "prices": _diff_prices(snap.get("prices") or {}, now["prices"]),
        "reverify_now": stale,   # includes safety/entry when older than their max age
        "dossier_status_changes": status_changes,
        "new_holidays_in_window": new_holidays,
        "score_changes": {k: {"before": (snap.get("scores") or {}).get(k), "now": v}
                          for k, v in (now.get("scores") or {}).items()
                          if (snap.get("scores") or {}).get(k) != v},
    }


def done(cfg, trip_id):
    trip = load(cfg, trip_id)
    _check(trip, "done")
    stopped = []
    for f, w in _trip_watch_files(cfg, trip_id):
        if w.get("active", True) or w.get("paused_by_trip"):
            w["active"] = False
            w.pop("paused_by_trip", None)
            _save_watch(f, w)
            stopped.append(w["id"])
    trip["status"] = "done"
    trip.pop("parked", None)
    save(cfg, trip)
    return {"trip": trip_id, "status": "done", "stopped_watches": stopped,
            "debrief_topics": cfg.get("debrief", {}).get("topics", [])}


def reminders(cfg, today=None):
    today = today or date.today()
    out = []
    for t in list_all(cfg):
        if t["status"] == "parked" and t["parked_until"] and _as_date(t["parked_until"]) <= today:
            out.append({"trip": t["id"], "parked_since": t["parked_since"], "reason": t["parked_reason"],
                        "message": "time to look again: resume it or park it with a new date"})
    return out
