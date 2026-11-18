"""Profile updates: the single write path, validated and audited."""
from datetime import date

import yaml

from . import schema

HEADER = ("# Travel preference profile. Updated only through `tw profile set`.\n"
          "# null = not known yet (scoring falls back to scoring.profile_defaults).\n")


def set_value(prof, key, value, evidence, reason=None, today=None):
    if not evidence or not evidence.strip():
        raise schema.ValidationError(["evidence is required: quote the user's own words"])
    schema.profile_value(key, value)
    section, _, field = key.partition(".")
    if field:
        prof.setdefault(section, {})
        old = prof[section].get(field)
        prof[section][field] = value
    else:
        old = prof.get(section)
        prof[section] = value
    prof.setdefault("learned", []).append({
        "date": (today or date.today()).isoformat(), "rule": f"{key} = {value!r}",
        "previous": old, "reason": reason, "evidence": evidence.strip(),
    })
    return {"key": key, "old": old, "new": value}


def save(path, prof):
    path.write_text(HEADER + yaml.safe_dump(prof, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _get(prof, key):
    section, _, field = key.partition(".")
    v = prof.get(section)
    return (v or {}).get(field) if field else v


def _empty(v):
    return v is None or v == [] or v == {} or v == ""


def gaps(cfg, prof, trip=None, step="plan"):
    """Missing facts ranked by impact on `step` (weights in config). Never returns answered or
    deferred fields. The model phrases the questions; this decides which matter most."""
    weights = cfg["intake"]["steps"].get(step)
    if weights is None:
        raise schema.ValidationError([f"unknown step {step!r}; valid: {sorted(cfg['intake']['steps'])}"])
    deferred = {d.get("key") for d in prof.get("deferred") or []}
    facts = ((trip or {}).get("facts") or {})
    shadowed = set()
    for trip_key, prof_key in (cfg["intake"].get("equivalent") or {}).items():
        if trip is not None:
            shadowed.add(prof_key)                       # the trip-level question covers it
            if trip_key in deferred or prof_key in deferred:
                deferred |= {trip_key, prof_key}
            if not _empty(_get(prof, prof_key)):
                shadowed.add(trip_key)                   # a general answer already exists
    out = []
    for key, w in weights.items():
        if key in deferred or key in shadowed:
            continue
        if key.startswith("trip."):
            if trip is None or not _empty((facts.get(key[5:]) or {}).get("value")):
                continue
        elif not _empty(_get(prof, key)):
            continue
        out.append({"key": key, "weight": w, "where": "trip" if key.startswith("trip.") else "profile"})
    out.sort(key=lambda x: -x["weight"])
    return {"step": step, "ask_now": out[: cfg["intake"]["max_questions"]], "all_missing": out}


def defer(prof, key, evidence, today=None):
    """The user left this to us ("decidi tu"): never ask it again."""
    if not evidence or not evidence.strip():
        raise schema.ValidationError(["evidence is required: quote the user's own words"])
    prof.setdefault("deferred", []).append({"key": key, "date": (today or date.today()).isoformat(),
                                            "evidence": evidence.strip()})
    return {"deferred": key}
