"""Geopolitical situation per trip: researched at runtime, rules enforced here.

Stored in trips/<id>/geopolitics.yaml. Code rules:
- `warning` or `blocker` needs at least 2 independent outlets, otherwise it becomes `watch`;
- `blocker` also needs an official (government) source, otherwise it becomes `warning`;
- an official advisory is reported as stated by the source (level, scale_max); code maps it
  to the 1-4 safety_level used in scoring. The scale comes from the source, not from code;
- items expire after geopolitics.ttl_days and must be re-verified before any advice.
"""
from datetime import datetime, timezone

import yaml

from . import evidence, schema, trips

SEVERITIES = ("info", "watch", "warning", "blocker")
TRENDS = ("improving", "stable", "escalating", "unknown")
IMPACTS = {"entry", "movement", "flights", "payments", "insurance", "connectivity", "health", "safety"}


def _path(cfg, trip_id):
    return trips._dir(cfg, trip_id) / "geopolitics.yaml"


def load(cfg, trip_id):
    p = _path(cfg, trip_id)
    return (yaml.safe_load(p.read_text()) or {}).get("items", []) if p.exists() else []


def normalize_level(level, scale_max):
    """Map an advisory level on the source's own 1..scale_max scale to 1..4."""
    if not (isinstance(level, int) and isinstance(scale_max, int) and scale_max >= 2 and 1 <= level <= scale_max):
        raise schema.ValidationError([f"advisory level {level!r} / scale_max {scale_max!r}: need integers with 1 <= level <= scale_max, scale_max >= 2"])
    return 1 + round((level - 1) * 3 / (scale_max - 1))


def _normalize(cfg, con, it, now):
    where = f"geopolitics {it.get('id')}"
    e = []
    if not it.get("id") or not it.get("topic"):
        e.append(f"{where}: id and topic are required")
    if it.get("severity") not in SEVERITIES:
        e.append(f"{where}: severity must be one of {SEVERITIES}")
    if it.get("trend", "unknown") not in TRENDS:
        e.append(f"{where}: trend must be one of {TRENDS}")
    bad = set(it.get("impact") or []) - IMPACTS
    if bad:
        e.append(f"{where}: unknown impact {sorted(bad)}; valid {sorted(IMPACTS)}")
    if not (it.get("scope") or {}):
        e.append(f"{where}: scope (country, places or routes) is required")
    if e:
        raise schema.ValidationError(e)
    out = dict(it)
    out.setdefault("trend", "unknown")
    out["sources"] = evidence.annotate(cfg, con, it.get("sources"), where, now)
    out["flags"] = []
    n, official = evidence.independent_count(out["sources"]), any(s["official"] for s in out["sources"])
    if out["severity"] == "blocker" and not official:
        out["severity"] = "warning"
        out["flags"].append("blocker needs an official source: downgraded to warning")
    if out["severity"] in ("warning", "blocker") and n < 2:
        out["severity"] = "watch"
        out["flags"].append(f"only {n} independent source: watch, not warning")
    adv = it.get("advisory")
    if adv:
        src = evidence.annotate(cfg, con, [adv.get("source") or {}], f"{where}.advisory", now)[0]
        if src["official"]:
            out["safety_level"] = normalize_level(adv.get("level"), adv.get("scale_max"))
        else:
            out["flags"].append("advisory source is not a verified official site: level not used")
        out["advisory"] = {**adv, "source": src}
    return out


def add(cfg, con, trip_id, items, now=None):
    trips.load(cfg, trip_id)
    norm = [_normalize(cfg, con, it, now) for it in items]
    by_id = {x["id"]: x for x in load(cfg, trip_id)}
    by_id.update({x["id"]: x for x in norm})
    _path(cfg, trip_id).write_text(yaml.safe_dump({"items": list(by_id.values())}, sort_keys=False, allow_unicode=True))
    return {"saved": [x["id"] for x in norm], "adjusted": [{"id": x["id"], "flags": x["flags"]} for x in norm if x["flags"]]}


def check(cfg, trip_id, today=None):
    today = today or datetime.now(timezone.utc).date()
    items = load(cfg, trip_id)
    ttl = cfg["geopolitics"]["ttl_days"]
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    return {
        "items": sorted(({"id": i["id"], "topic": i["topic"], "severity": i["severity"], "trend": i["trend"],
                          "impact": i.get("impact", []), "scope": i["scope"],
                          "stale": evidence.stale(i["sources"], ttl, today)} for i in items),
                        key=lambda x: -rank[x["severity"]]),
        "stale": [i["id"] for i in items if evidence.stale(i["sources"], ttl, today)],
        "safety_level": max([i["safety_level"] for i in items if "safety_level" in i] or [None], key=lambda v: v or 0),
    }


def applies(item, trip, option):
    sc = item.get("scope") or {}
    if sc.get("options") and option.get("id") not in sc["options"]:
        return False
    if sc.get("country") and trip.get("country") and sc["country"] == trip["country"]:
        return True
    return bool(set(sc.get("places") or []) & set(option.get("places") or [])) or bool(sc.get("options"))
