"""Deterministic scoring of trip options against the profile.

Each option (in trips/<id>/trip.yaml) carries metrics researched online:
  est_cost_eur, climate: {tmax_c, rain_days}, crowd_level 1-5,
  authenticity 1-5, max_transfer_h, safety_level 1-4.
Output: score 0-100, component breakdown, profile conflicts, exclusions.
"""


def _pref(profile, section, key, defaults):
    v = (profile.get(section) or {}).get(key)
    return (v, False) if v is not None else (defaults.get(key), True)


def _clamp(x):
    return max(0.0, min(1.0, x))


def _weights(cfg, profile):
    w = dict(cfg["scoring"]["default_weights"])
    w.update(profile.get("weights") or {})
    tot = sum(w.values()) or 1.0
    return {k: v / tot for k, v in w.items()}


def budget_for(profile, trip=None):
    """(budget, mode): the trip's own budget fact, else the profile budget; 'relative' when none
    is known or the user deferred it ("no strict limit, as cheap as possible")."""
    deferred = {d.get("key") for d in profile.get("deferred") or []}
    fact = (((trip or {}).get("facts") or {}).get("trip_budget_eur") or {}).get("value")
    if fact and "trip.trip_budget_eur" not in deferred:
        return fact, "budget"
    b = (profile.get("constraints") or {}).get("budget_total_eur")
    if b and "constraints.budget_total_eur" not in deferred:
        return b, "budget"
    return None, "relative"


def score_option(cfg, profile, opt, cost_ctx=None):
    """`cost_ctx` = {"mode": "budget"|"relative", "budget": n, "min": n, "max": n} from score_trip."""
    s = cfg["scoring"]
    dflt = s["profile_defaults"]
    used_defaults, conflicts, missing = [], [], []
    comp = {}

    def pref(section, key):
        v, is_default = _pref(profile, section, key, dflt)
        if is_default:
            used_defaults.append(f"{section}.{key}")
        return v

    # cost
    cost_ctx = cost_ctx or {"mode": "budget", "budget": (profile.get("constraints") or {}).get("budget_total_eur")}
    budget = cost_ctx.get("budget") if cost_ctx["mode"] == "budget" else None
    cost = opt.get("est_cost_eur")
    if cost is None:
        missing.append("est_cost_eur")
    elif cost_ctx["mode"] == "relative":
        lo, hi = cost_ctx.get("min"), cost_ctx.get("max")
        rel = s["relative_cost"]
        if lo is None or hi is None or hi <= lo:
            comp["cost"] = 1.0
        else:
            frac = (cost - lo) / (hi - lo)                      # 0 = cheapest, 1 = most expensive
            comp["cost"] = _clamp(1.0 - frac * (1.0 - rel["most_expensive_score"]))
    elif budget:
        r = cost / budget
        lo, hi = s["cost_full_score_ratio"], s["cost_zero_score_ratio"]
        comp["cost"] = 1.0 if r <= lo else _clamp((hi - r) / (hi - lo))
        if r > 1:
            conflicts.append(f"estimated cost {cost}€ over budget {budget}€")
    else:
        used_defaults.append("constraints.budget_total_eur")

    # climate
    cl = opt.get("climate") or {}
    tmax, rain = cl.get("tmax_c"), cl.get("rain_days")
    if tmax is None:
        missing.append("climate.tmax_c")
    else:
        lo, hi = pref("climate_ideal", "tmax_min_c"), pref("climate_ideal", "tmax_max_c")
        dev = max(0.0, lo - tmax, tmax - hi)
        c = 1.0 - dev * s["climate_penalty_per_deg"]
        hard_max = (profile.get("avoid") or {}).get("temp_max_c")
        hard_min = (profile.get("avoid") or {}).get("temp_min_c")
        if hard_max is not None and tmax > hard_max:
            conflicts.append(f"tmax {tmax}°C above limit {hard_max}°C")
        if hard_min is not None and tmax < hard_min:
            conflicts.append(f"tmax {tmax}°C below limit {hard_min}°C")
        if rain is not None:
            max_rain = pref("avoid", "max_rain_days_per_month")
            over = max(0.0, rain - max_rain)
            c -= over * s["rain_penalty_per_day"]
            if over > 0:
                conflicts.append(f"{rain} rain days/month above {max_rain}")
        comp["climate"] = _clamp(c)

    # crowds
    crowd = opt.get("crowd_level")
    if crowd is None:
        missing.append("crowd_level")
    else:
        comp["crowds"] = _clamp((5 - crowd) / 4)
        if (profile.get("avoid") or {}).get("crowds") and crowd >= 4:
            conflicts.append(f"high crowds ({crowd}/5)")

    # authenticity
    auth = opt.get("authenticity")
    if auth is None:
        missing.append("authenticity")
    else:
        comp["authenticity"] = _clamp((auth - 1) / 4)
        if (profile.get("travel_style") or {}).get("prefer_local") and auth <= 2:
            conflicts.append(f"very touristic option (authenticity {auth}/5)")

    # logistics
    tr = opt.get("max_transfer_h")
    if tr is None:
        missing.append("max_transfer_h")
    else:
        lim = pref("avoid", "max_transfer_h")
        comp["logistics"] = _clamp(1.0 - max(0.0, tr - lim) / lim)
        if tr > lim:
            conflicts.append(f"{tr}h transfer above {lim}h")

    # safety: hard constraint, not a weight
    excluded = None
    safety = opt.get("safety_level")
    if safety is None:
        missing.append("safety_level")
    else:
        max_s = pref("constraints", "max_safety_level")
        if safety > max_s:
            excluded = f"safety level {safety} above max {max_s}"

    w = _weights(cfg, profile)
    avail = {k: v for k, v in comp.items() if k in w}
    wsum = sum(w[k] for k in avail)
    total = round(100 * sum(w[k] * v for k, v in avail.items()) / wsum, 1) if wsum else None
    return {
        "id": opt.get("id"), "name": opt.get("name"),
        "score": None if excluded else total,
        "excluded": excluded,
        "components": {k: round(v, 2) for k, v in comp.items()},
        "conflicts": conflicts,
        "missing": missing,
        "profile_defaults_used": sorted(set(used_defaults)),
        "user_override": bool(opt.get("user_override")),
    }


def _applies(item, trip, opt):
    from . import geopolitics as g
    return g.applies(item, trip, opt)


def _apply_context(res, trip, opt, reqs, geo):
    """Requirements and geopolitics researched at runtime: exclusions and conflicts."""
    from . import geopolitics as g, requirements as r
    for it in r.for_option(reqs or [], opt.get("id")):
        if it["status"] == "required" and it.get("feasible_in_window") is False:
            res["excluded"] = res["excluded"] or f"requirement not feasible in the window: {it['action']}"
        elif it["status"] in ("required", "unknown") and it.get("feasible_in_window") == "unknown":
            res["conflicts"].append(f"requirement with uncertain feasibility: {it['action']}")
    for it in geo or []:
        if not g.applies(it, trip, opt):
            continue
        if it["severity"] == "blocker":
            res["excluded"] = res["excluded"] or f"geopolitics: {it['topic']}"
        elif it["severity"] == "warning":
            res["conflicts"].append(f"geopolitics warning: {it['topic']} ({it.get('trend', 'unknown')})")
    if res["excluded"]:
        res["score"] = None
    return res


def score_trip(cfg, profile, trip, reqs=None, geo=None):
    results = []
    budget, mode = budget_for(profile, trip)
    costs = [o["est_cost_eur"] for o in trip.get("options", []) if o.get("est_cost_eur") is not None]
    cost_ctx = {"mode": mode, "budget": budget, "min": min(costs) if costs else None, "max": max(costs) if costs else None}
    for o in trip.get("options", []):
        opt = dict(o)
        levels = [i["safety_level"] for i in geo or [] if "safety_level" in i and _applies(i, trip, o)]
        if levels:
            opt["safety_level"] = max(levels + [o.get("safety_level") or 0])
        results.append(_apply_context(score_option(cfg, profile, opt, cost_ctx), trip, o, reqs, geo))
    ranked = sorted(results, key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return {"trip": trip.get("id"), "cost_mode": mode, "budget_eur": budget,
            "cost_range_eur": [cost_ctx["min"], cost_ctx["max"]] if costs else None, "ranking": ranked}
