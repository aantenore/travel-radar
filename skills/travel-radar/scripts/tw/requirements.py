"""Visas, permits and everything needed for a trip: researched at runtime, rules enforced here.

Stored in trips/<id>/requirements.yaml. Code rules:
- every item has sources; `official` is computed from runtime source verdicts;
- `required` backed only by non-official sources becomes `unknown` with a verify flag;
- `required` needs `lead_time_days`; the deadline is computed by code:
  departure - lead_time - safety margin (config).
"""
from datetime import datetime, timedelta, timezone

import yaml

from . import evidence, schema, trips

STATUSES = ("required", "recommended", "not_required", "unknown")
FEASIBLE = (True, False, "unknown")


def _path(cfg, trip_id):
    return trips._dir(cfg, trip_id) / "requirements.yaml"


def load(cfg, trip_id):
    p = _path(cfg, trip_id)
    return (yaml.safe_load(p.read_text()) or {}).get("items", []) if p.exists() else []


def _nationality(profile):
    return ((profile or {}).get("traveller") or {}).get("nationality")


def _normalize(cfg, con, trip, it, now, profile=None):
    c = cfg["requirements"]
    where = f"requirement {it.get('id')}"
    e = []
    if not it.get("id"):
        e.append("requirement: id missing")
    if it.get("category") not in c["ttl_days"]:
        e.append(f"{where}: category must be one of {sorted(c['ttl_days'])}")
    if it.get("status") not in STATUSES:
        e.append(f"{where}: status must be one of {STATUSES}")
    if not it.get("action"):
        e.append(f"{where}: action missing (what the traveller must do)")
    lt = it.get("lead_time_days")
    if it.get("status") == "required" and not (isinstance(lt, int) and lt >= 0):
        e.append(f"{where}: lead_time_days (integer) is required for a required item")
    if it.get("feasible_in_window", "unknown") not in FEASIBLE:
        e.append(f"{where}: feasible_in_window must be true, false or unknown")
    cost = it.get("cost")
    if cost is not None and not (isinstance(cost, dict) and "amount" in cost and cost.get("currency") and cost.get("source")):
        e.append(f"{where}: cost needs amount, currency and source")
    if e:
        raise schema.ValidationError(e)
    out = dict(it)
    out.setdefault("feasible_in_window", "unknown")
    out.setdefault("applies_to", {})
    out["sources"] = evidence.annotate(cfg, con, it.get("sources"), where, now)
    out["flags"] = []
    nat = _nationality(profile)
    dependent = it["category"] in c["nationality_dependent"]
    out["for_nationality"] = nat if dependent else None
    if dependent and not nat:
        out["assumed_nationality"] = True
        if out["status"] != "unknown":
            out["status"] = "unknown"
            out["flags"].append("traveller nationality unknown: this depends on it; ask it (tw profile gaps --step requirements)")
    if out["status"] == "required" and not any(s["official"] for s in out["sources"]):
        out["status"] = "unknown"
        out["flags"].append("verify: stated as required only by non-official sources; check an official source")
    if lt is not None and out["status"] in ("required", "unknown", "recommended"):
        deadlines = {}
        for opt in (out["applies_to"].get("options") or [None]):
            dep = evidence.option_departure(trip, opt)
            if dep:
                deadlines[opt or "chosen"] = (dep - timedelta(days=lt + c["safety_margin_days"])).isoformat()
        out["deadlines"] = deadlines
    return out


def add(cfg, con, trip_id, items, now=None, profile=None):
    trip = trips.load(cfg, trip_id)
    norm = [_normalize(cfg, con, trip, it, now, profile) for it in items]
    by_id = {x["id"]: x for x in load(cfg, trip_id)}
    by_id.update({x["id"]: x for x in norm})
    _path(cfg, trip_id).write_text(yaml.safe_dump({"items": list(by_id.values())}, sort_keys=False, allow_unicode=True))
    return {"saved": [x["id"] for x in norm],
            "downgraded": [{"id": x["id"], "flags": x["flags"]} for x in norm if x["flags"]]}


def check(cfg, trip_id, today=None, profile=None):
    today = today or datetime.now(timezone.utc).date()
    items = load(cfg, trip_id)
    ttl = cfg["requirements"]["ttl_days"]
    nat = _nationality(profile)
    deadlines, blockers, stale, verify, ask = [], [], [], [], []
    for it in items:
        dl = it.get("deadlines") or {}
        if dl:
            earliest = min(dl.values())
            deadlines.append({"id": it["id"], "action": it["action"], "status": it["status"],
                              "deadline": earliest, "options": sorted(dl),
                              "by_option": dl if len(set(dl.values())) > 1 else None,
                              "overdue": evidence.as_date(earliest) < today})
        if it.get("assumed_nationality") and "traveller.nationality" not in ask:
            ask.append("traveller.nationality")
        if it.get("for_nationality") and nat and it["for_nationality"] != nat:
            stale.append({"id": it["id"], "category": it["category"], "reason": f"researched for nationality "
                          f"{it['for_nationality']}, profile now says {nat}"})
        if it["status"] == "required" and (it.get("feasible_in_window") is False
                                            or any(evidence.as_date(d) < today for d in (it.get("deadlines") or {}).values())):
            blockers.append({"id": it["id"], "action": it["action"],
                             "reason": "not feasible in the window" if it.get("feasible_in_window") is False else "deadline passed"})
        if evidence.stale(it["sources"], ttl[it["category"]], today):
            stale.append({"id": it["id"], "category": it["category"], "newest_source": evidence.newest(it["sources"]).isoformat()})
        if it.get("flags"):
            verify.append({"id": it["id"], "flags": it["flags"]})
    deadlines.sort(key=lambda d: d["deadline"])
    return {"blockers": blockers, "deadlines": deadlines, "stale": stale, "verify": verify, "ask": ask}


def for_option(items, option_id):
    return [i for i in items if not (i.get("applies_to") or {}).get("options") or option_id in i["applies_to"]["options"]]
