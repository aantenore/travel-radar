"""Validation of files written by the agent (trip, watch, profile)."""
from datetime import date

NUM = (int, float)
DATE = (date, str)


class ValidationError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def _is_date(v):
    if isinstance(v, date):
        return True
    try:
        date.fromisoformat(str(v))
        return True
    except ValueError:
        return False


def _range(errors, where, v, lo, hi):
    if v is None:
        return
    if not isinstance(v, NUM) or isinstance(v, bool) or not lo <= v <= hi:
        errors.append(f"{where}: expected number {lo}-{hi}, got {v!r}")


def _nonneg(errors, where, v):
    if v is not None and (not isinstance(v, NUM) or isinstance(v, bool) or v < 0):
        errors.append(f"{where}: expected number >= 0, got {v!r}")


def trip(t):
    e = []
    if not t.get("id"):
        e.append("trip.id missing")
    opts = t.get("options")
    if not isinstance(opts, list):
        e.append("trip.options must be a list")
        raise ValidationError(e)
    seen = set()
    for i, o in enumerate(opts):
        w = f"options[{i}]"
        if not o.get("id"):
            e.append(f"{w}.id missing")
        elif o["id"] in seen:
            e.append(f"{w}.id duplicated: {o['id']}")
        seen.add(o.get("id"))
        _nonneg(e, f"{w}.est_cost_eur", o.get("est_cost_eur"))
        _range(e, f"{w}.crowd_level", o.get("crowd_level"), 1, 5)
        _range(e, f"{w}.authenticity", o.get("authenticity"), 1, 5)
        _range(e, f"{w}.safety_level", o.get("safety_level"), 1, 4)
        _nonneg(e, f"{w}.max_transfer_h", o.get("max_transfer_h"))
        cl = o.get("climate") or {}
        _range(e, f"{w}.climate.tmax_c", cl.get("tmax_c"), -60, 60)
        _range(e, f"{w}.climate.rain_days", cl.get("rain_days"), 0, 31)
        # every metric with a value needs a source
        srcs = o.get("sources") or {}
        for k in ("est_cost_eur", "safety_level", "crowd_level"):
            if o.get(k) is not None and not srcs.get(k):
                e.append(f"{w}.sources.{k} missing: every metric needs at least one link")
    if t.get("status", "planning") not in ("planning", "chosen", "monitoring", "parked", "done"):
        e.append(f"trip.status invalid: {t.get('status')!r}")
    if t.get("chosen_option") is not None and t["chosen_option"] not in seen:
        e.append(f"trip.chosen_option {t['chosen_option']!r} is not an option id")
    if t.get("country") is not None and not (isinstance(t["country"], str) and len(t["country"]) == 2):
        e.append("trip.country must be an ISO 3166-1 alpha-2 code, e.g. CN")
    for i, d in enumerate(t.get("deadlines") or []):
        if not _is_date(d.get("date")) or not d.get("title") or not d.get("source"):
            e.append(f"deadlines[{i}]: needs date, title and source")
    if e:
        raise ValidationError(e)


def watch(w):
    e = []
    if w.get("kind") not in ("flight", "safety", "weather", "deal"):
        e.append(f"watch {w.get('id')}: invalid kind {w.get('kind')!r}")
    win = w.get("depart_window") or {}
    if not _is_date(win.get("from")):
        e.append(f"watch {w.get('id')}: depart_window.from missing or not a date")
    if win.get("to") is not None and not _is_date(win["to"]):
        e.append(f"watch {w.get('id')}: depart_window.to is not a date")
    if w.get("kind") in ("flight", "deal"):
        for k in ("origin", "destination"):
            if not w.get(k):
                e.append(f"watch {w.get('id')}: {k} missing")
    _nonneg(e, f"watch {w.get('id')}.budget_eur", w.get("budget_eur"))
    if w.get("haul") is not None and w["haul"] not in ("long", "short"):
        e.append(f"watch {w.get('id')}: haul must be long or short")
    for k in ("peak", "one_way"):
        if w.get(k) is not None and not isinstance(w[k], bool):
            e.append(f"watch {w.get('id')}: {k} must be true or false")
    lens = w.get("trip_length_days")
    if lens is not None and not (isinstance(lens, list) and 1 <= len(lens) <= 2 and all(isinstance(x, int) for x in lens)):
        e.append(f"watch {w.get('id')}: trip_length_days must be [min, max] days")
    if e:
        raise ValidationError(e)


PROFILE_FIELDS = {
    "travel_style.party": (str,), "travel_style.pace": (str,),
    "travel_style.tourist_must_sees": (bool,), "travel_style.prefer_local": (bool,),
    "constraints.origin_airports": (list,), "constraints.budget_total_eur": NUM,
    "constraints.budget_flight_eur": NUM, "constraints.typical_length_days": NUM,
    "constraints.available_windows": (list,), "constraints.max_safety_level": (int,),
    "avoid.crowds": (bool,), "avoid.temp_max_c": NUM, "avoid.temp_min_c": NUM,
    "avoid.max_rain_days_per_month": NUM, "avoid.max_transfer_h": NUM, "avoid.other": (list,),
    "climate_ideal.tmax_min_c": NUM, "climate_ideal.tmax_max_c": NUM,
    "weights": (dict,),
}


def profile_value(key, value):
    if key not in PROFILE_FIELDS:
        raise ValidationError([f"unknown profile field: {key}. Valid: {', '.join(sorted(PROFILE_FIELDS))}"])
    t = PROFILE_FIELDS[key]
    if value is not None and (not isinstance(value, t) or (bool not in t and isinstance(value, bool))):
        raise ValidationError([f"{key}: expected type {[x.__name__ for x in t]}, got {type(value).__name__}"])
