"""Itinerary shape of a fare (segments, layovers, durations): validation, hard filter against
the traveller's flight preferences, and ranking. All rules here; the model only reports what
the page shows.

Itinerary format (per direction: `outbound`, optional `return`):
  {segments: [{from, to, carrier, operated_by, depart: "YYYY-MM-DDTHH:MM", arrive: "..."}],
   layovers: [{airport, duration_min, self_transfer: bool, overnight: bool}],
   total_min}
Times are local to each airport, as airline sites show them, so layover durations can be checked
(same airport, same time zone) but total time across zones is taken from the page.
"""
import json
from datetime import datetime, timedelta

from . import schema

DIRECTIONS = ("outbound", "return")


def _dt(v, where):
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        raise schema.ValidationError([f"{where}: time {v!r} must be YYYY-MM-DDTHH:MM"])


def _overnight(arrive, depart, cfg):
    """True if the layover covers part of the night window (local time at the layover airport)."""
    start, end = cfg["flights"]["night_window_h"]
    t = arrive
    while t < depart:
        in_night = (start <= t.hour < end) if start < end else (t.hour >= start or t.hour < end)
        if in_night:
            return True
        t += timedelta(minutes=30)
    return False


def validate(cfg, itin, depart=None, return_date=None):
    """Raise ValidationError with every problem; return the itinerary normalized with
    code-computed layover durations and overnight flags."""
    if isinstance(itin, str):
        itin = json.loads(itin)
    e, out = [], {}
    tol = cfg["flights"]["layover_tolerance_min"]
    for d in DIRECTIONS:
        leg = itin.get(d)
        if leg is None:
            if d == "outbound":
                e.append("itinerary.outbound is required")
            continue
        segs, lays = leg.get("segments") or [], leg.get("layovers") or []
        if not segs:
            e.append(f"{d}: at least one segment is required")
            continue
        for i, s in enumerate(segs):
            for k in ("from", "to", "carrier", "depart", "arrive"):
                if not s.get(k):
                    e.append(f"{d}.segments[{i}].{k} missing")
        if e:
            continue
        times = [(_dt(s["depart"], f"{d}.segments[{i}].depart"), _dt(s["arrive"], f"{d}.segments[{i}].arrive"))
                 for i, s in enumerate(segs)]
        for i in range(len(segs) - 1):
            if segs[i]["to"].upper() != segs[i + 1]["from"].upper():
                e.append(f"{d}: segment {i} arrives at {segs[i]['to']} but segment {i + 1} departs from {segs[i + 1]['from']}"
                         " (airport change: report it as a separate layover with self_transfer)")
        if len(lays) != len(segs) - 1:
            e.append(f"{d}: {len(segs) - 1} layover(s) expected for {len(segs)} segments, got {len(lays)}")
            continue
        norm_lays = []
        for i, l in enumerate(lays):
            ap = (l.get("airport") or "").upper()
            if ap != segs[i]["to"].upper():
                e.append(f"{d}.layovers[{i}].airport {ap or '?'} is not the connecting airport {segs[i]['to']}")
                continue
            arr, dep = times[i][1], times[i + 1][0]
            computed = int((dep - arr).total_seconds() // 60)
            if computed < 0:
                e.append(f"{d}.layovers[{i}]: next segment departs before arrival")
                continue
            stated = l.get("duration_min")
            if stated is not None and abs(stated - computed) > tol:
                e.append(f"{d}.layovers[{i}].duration_min {stated} does not match the segment times ({computed} min)")
            norm_lays.append({"airport": ap, "duration_min": computed, "self_transfer": bool(l.get("self_transfer")),
                              "overnight": _overnight(arr, dep, cfg)})
        total = leg.get("total_min")
        if not isinstance(total, int) or total <= 0:
            e.append(f"{d}.total_min (integer minutes, as shown on the page) is required")
        elif norm_lays and total < sum(l["duration_min"] for l in norm_lays):
            e.append(f"{d}.total_min {total} is shorter than its layovers")
        out[d] = {"segments": segs, "layovers": norm_lays, "total_min": total,
                  "carriers": sorted({s["carrier"] for s in segs} | {s.get("operated_by") for s in segs if s.get("operated_by")})}
    if not e and depart and out.get("outbound") and str(out["outbound"]["segments"][0]["depart"])[:10] != str(depart):
        e.append(f"itinerary outbound departs {str(out['outbound']['segments'][0]['depart'])[:10]}, not --depart {depart}")
    if not e and return_date and out.get("return") and str(out["return"]["segments"][0]["depart"])[:10] != str(return_date):
        e.append(f"itinerary return departs {str(out['return']['segments'][0]['depart'])[:10]}, not --return {return_date}")
    if e:
        raise schema.ValidationError(e)
    return out


def metrics(itin):
    legs = [itin[d] for d in DIRECTIONS if d in itin]
    lays = [l for leg in legs for l in leg["layovers"]]
    return {"stops_max": max(len(leg["layovers"]) for leg in legs),
            "max_layover_min": max([l["duration_min"] for l in lays] or [0]),
            "layover_min_total": sum(l["duration_min"] for l in lays),
            "total_min_max": max(leg["total_min"] for leg in legs),
            "total_min_sum": sum(leg["total_min"] for leg in legs),
            "self_transfer": any(l["self_transfer"] for l in lays),
            "overnight": any(l["overnight"] for l in lays)}


def prefs(profile):
    return (profile or {}).get("flights") or {}


def has_prefs(profile):
    return any(v is not None for v in prefs(profile).values())


def conflicts(itin, profile):
    """Hard-filter violations against the traveller's stated flight preferences."""
    p, m, out = prefs(profile), metrics(itin), []
    if p.get("max_layover_h") is not None and m["max_layover_min"] > p["max_layover_h"] * 60:
        out.append(f"layover of {m['max_layover_min'] / 60:.1f}h over your max {p['max_layover_h']}h")
    if p.get("max_stops") is not None and m["stops_max"] > p["max_stops"]:
        out.append(f"{m['stops_max']} stops over your max {p['max_stops']}")
    if p.get("max_total_h") is not None and m["total_min_max"] > p["max_total_h"] * 60:
        out.append(f"{m['total_min_max'] / 60:.1f}h travel time over your max {p['max_total_h']}h")
    if p.get("avoid_self_transfer") and m["self_transfer"]:
        out.append("self-transfer / separate tickets")
    if p.get("avoid_overnight_layover") and m["overnight"]:
        out.append("overnight layover")
    return out


def rank(cfg, profile, rows):
    """rows: [{price_eur, itinerary, ...}]. Returns ranked rows with fit, conflicts and a score
    mixing price, total time and a layover penalty (weights in config)."""
    w = cfg["flights"]["ranking"]
    shorter = prefs(profile).get("prefer_shorter_layovers")
    scored = []
    prices = [r["price_eur"] for r in rows]
    times = [metrics(r["itinerary"])["total_min_sum"] for r in rows]
    pmin, pmax, tmin, tmax = min(prices), max(prices), min(times), max(times)
    for r in rows:
        m = metrics(r["itinerary"])
        pn = 0 if pmax == pmin else (r["price_eur"] - pmin) / (pmax - pmin)
        tn = 0 if tmax == tmin else (m["total_min_sum"] - tmin) / (tmax - tmin)
        lay_pen = (m["layover_min_total"] / 60) * w["layover_penalty_per_h"] * (w["shorter_layover_factor"] if shorter else 1)
        cost = w["price"] * pn + w["time"] * tn + lay_pen
        c = conflicts(r["itinerary"], profile)
        scored.append({**r, "metrics": m, "conflicts": c, "fits": not c, "rank_cost": round(cost, 3)})
    scored.sort(key=lambda x: (not x["fits"], x["rank_cost"]))
    fits = [x for x in scored if x["fits"]]
    return {"ranked": scored,
            "cheapest_overall": min(scored, key=lambda x: x["price_eur"]) if scored else None,
            "cheapest_that_fits": min(fits, key=lambda x: x["price_eur"]) if fits else None,
            "best_that_fits": fits[0] if fits else None}


def fit_filter(cfg, profile, watch, obs):
    """Observations that may drive alerts and advise: all of them when the user has no flight
    preferences or overrode them for this watch; otherwise only itineraries that pass the hard
    filter (fares without itinerary data follow config `flights.unknown_fit`)."""
    if not has_prefs(profile) or (watch.get("flight_override") or {}).get("evidence"):
        return obs, {"excluded_conflicts": 0, "excluded_unknown": 0}
    kept, conf, unknown = [], 0, 0
    for o in obs:
        it = o.get("itinerary")
        if not it:
            if cfg["flights"]["unknown_fit"] == "include":
                kept.append(o)
            else:
                unknown += 1
            continue
        if conflicts(json.loads(it) if isinstance(it, str) else it, profile):
            conf += 1
        else:
            kept.append(o)
    return kept, {"excluded_conflicts": conf, "excluded_unknown": unknown}
