"""Deterministic buy/wait advisor for a flight watch.

Verdicts: BUY_NOW | WAIT (with recheck_date) | WATCH_CLOSELY (with recheck_date).
Only verified observations (tier 1/2) count. Reasons are returned as data; low history
lowers confidence instead of faking certainty.
"""
import statistics
from datetime import date, datetime, timedelta, timezone

from . import knowledge, schema, store


def window_key(w):
    """Knowledge key of the booking window for this route and season."""
    route = "/".join(sorted(w.get("origin") or [])) + ">" + "/".join(sorted(w.get("destination") or []))
    return f"{route}:{'peak' if w.get('peak') else 'offpeak'}"


def _window(cfg, con, w, now):
    """Researched window for this route/season if fresh; otherwise the generic fallback (heuristic)."""
    k = knowledge.get(con, "booking_window", window_key(w), now) if con is not None else None
    if k and k["fresh"]:
        return {"name": f"researched for {window_key(w)}", "ideal_days": k["value"]["ideal_days"],
                "confidence": "researched", "source": ", ".join(k["sources"]), "fetched": k["fetched"]}, None
    for rule in cfg["advise"]["windows"]:
        if rule["haul"] == w.get("haul", "long") and bool(rule["peak"]) == bool(w.get("peak", False)):
            need = {"kind": "booking_window", "key": window_key(w),
                    "why": "expired" if k else "no runtime research yet"}
            return rule, need
    raise schema.ValidationError([f"no fallback window for haul={w.get('haul')} peak={w.get('peak')}"])


def _depart(w):
    v = (w.get("depart_window") or {}).get("from")
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def _percentile(values, x):
    return round(100 * sum(1 for v in values if v <= x) / len(values))


def _trend(obs, now, days):
    cut = now - timedelta(days=days)
    recent = [o["price_eur"] for o in obs if datetime.fromisoformat(o["ts"]) >= cut]
    older = [o["price_eur"] for o in obs if datetime.fromisoformat(o["ts"]) < cut]
    if not recent or not older:
        return None
    before = statistics.median(older)
    return round(100 * (statistics.median(recent) - before) / before, 1)


def advise(cfg, con, w, now=None):
    now = now or datetime.now(timezone.utc)
    c = cfg["advise"]
    today = now.date()
    days = (_depart(w) - today).days
    win, research_needed = _window(cfg, con, w, now)
    lo, hi = win["ideal_days"]
    position = "early" if days > hi else ("late" if days < lo else "in_window")
    obs = [o for o in store.observations(con, w["id"]) if o.get("tier") in (1, 2)]
    n_prov = store.provisional_count(con, w["id"])
    reasons = [f"{days} days to departure; booking window '{win['name']}' is {lo}-{hi} days before ({position})",
               f"booking window is a {win.get('confidence', 'heuristic')}: {win.get('source', 'no source given')}"]
    out = {"watch": w["id"], "days_to_departure": days, "window": win["name"], "position": position,
           "window_confidence": win.get("confidence", "heuristic"), "verified_observations": len(obs),
           "research_needed": research_needed}

    def verdict(v, confidence, recheck=None):
        out.update(verdict=v, confidence=confidence, reasons=reasons,
                   recheck_date=recheck.isoformat() if recheck else None)
        return out

    if n_prov:
        reasons.append(f"{n_prov} provisional fare(s) ignored (departure beyond the published-fare horizon or flagged)")
    if days < 0:
        reasons.append("departure date has passed")
        return verdict("WATCH_CLOSELY", "low")
    if not obs:
        reasons.append("no verified (tier 1/2) price yet: nothing to judge")
        return verdict("WATCH_CLOSELY", "low", today + timedelta(days=c["close_recheck_days"]))

    prices = [o["price_eur"] for o in obs]
    price = obs[-1]["price_eur"]
    pct = _percentile(prices, price)
    trend = _trend(obs, now, c["trend_days"])
    cv = round(statistics.pstdev(prices) / statistics.mean(prices), 3) if len(prices) > 1 else None
    budget = w.get("budget_eur")
    under = bool(budget) and price <= budget
    out["metrics"] = {"price_eur": price, "url": obs[-1]["url"], "percentile": pct, "min_eur": min(prices),
                      "median_eur": statistics.median(prices), "trend_pct": trend, "volatility_cv": cv,
                      "budget_eur": budget, "under_budget": under}
    reasons.append(f"current {price}€ is at percentile {pct} of {len(prices)} verified prices (min {min(prices)}€)")
    if trend is not None:
        reasons.append(f"{c['trend_days']}-day trend {trend:+}%")
    if budget:
        reasons.append(f"{'under' if under else 'over'} budget {budget}€")

    enough = len(obs) >= c["min_history"]
    if len(obs) >= 2 * c["min_history"]:
        reasons.append("your own verified history is long enough to drive the price-level judgement; the window only informs timing")
    if not enough:
        reasons.append(f"only {len(obs)} verified observations (< {c['min_history']}): history too short to judge the price level")
    confidence = "high" if len(obs) >= 2 * c["min_history"] else ("medium" if enough else "low")
    if cv is not None and cv > c["high_volatility_cv"] and confidence == "high":
        confidence = "medium"
        reasons.append(f"volatile prices (cv {cv})")
    close = today + timedelta(days=c["close_recheck_days"])
    wait = today + timedelta(days=c["wait_recheck_days"])

    if days < c["late_days"]:
        reasons.append(f"inside {c['late_days']} days of departure: waiting rarely pays")
        return verdict("BUY_NOW", confidence if under or pct <= c["bad_percentile"] else "low")
    if not enough:
        if under:
            reasons.append("price is under budget even without enough history")
            return verdict("BUY_NOW", "low")
        return verdict("WATCH_CLOSELY", "low", close)
    if position == "early":
        if pct <= c["good_percentile"] and under:
            reasons.append("unusually good price for this route even before the booking window")
            return verdict("BUY_NOW", confidence)
        opens = _depart(w) - timedelta(days=hi)
        reasons.append(f"before the usual booking window (opens {opens})")
        return verdict("WAIT", confidence, min(wait, opens) if opens > today else wait)
    if position == "late":
        reasons.append("past the ideal booking window")
        return verdict("BUY_NOW" if pct <= c["bad_percentile"] or under else "WATCH_CLOSELY", confidence,
                       None if pct <= c["bad_percentile"] or under else close)
    if pct <= c["good_percentile"] or (under and pct <= 50):
        return verdict("BUY_NOW", confidence)
    if trend is not None and trend >= c["rising_trend_pct"]:
        reasons.append("prices rising inside the booking window")
        return verdict("BUY_NOW", confidence)
    if pct >= c["bad_percentile"]:
        reasons.append("price high vs own history")
        return verdict("WAIT", confidence, wait)
    return verdict("WATCH_CLOSELY", confidence, close)
