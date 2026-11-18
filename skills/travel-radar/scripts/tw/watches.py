"""Watches: check cadence and alert evaluation (deterministic)."""
import statistics
from datetime import date, datetime, timedelta, timezone

from . import config, schema, store


def load_all(cfg, errors=None):
    """Valid watches. Invalid ones are appended to `errors` (if given) and skipped."""
    out = []
    for f in sorted(config.path(cfg, "watches_dir").glob("*.yaml")):
        w = config.load_yaml(f)
        w.setdefault("id", f.stem)
        try:
            schema.watch(w)
        except schema.ValidationError as e:
            if errors is not None:
                errors.append({"file": f.name, "errors": e.errors})
            continue
        out.append(w)
    return out


def _first_depart(w):
    win = w.get("depart_window") or {}
    v = win.get("from")
    if isinstance(v, str):
        v = date.fromisoformat(v)
    return v


def per_day(cfg, w, today):
    dep = _first_depart(w)
    days = (dep - today).days if dep else 10**6
    for rule in cfg["cadence"]:
        if days >= rule["min_days"]:
            return rule["per_day"], days
    return cfg["cadence"][-1]["per_day"], days


def due(cfg, con, watches, now=None):
    now = now or datetime.now(timezone.utc)
    out = []
    for w in watches:
        if not w.get("active", True):
            continue
        n, days = per_day(cfg, w, now.date())
        if days < 0:
            out.append({"id": w["id"], "due": False, "reason": "departure in the past: deactivate"})
            continue
        last = store.last_check(con, w["id"])
        interval = timedelta(hours=24 / n)
        is_due = last is None or now - datetime.fromisoformat(last) >= interval
        out.append({"id": w["id"], "due": is_due, "checks_per_day": n, "days_to_departure": days,
                    "last_check": last, "spec": w})
    return out


def _cooldown_ok(cfg, con, wid, kind, now):
    last = store.last_alert(con, wid, kind)
    return last is None or now - datetime.fromisoformat(last) >= timedelta(hours=cfg["alerts"]["cooldown_h"])


def evaluate(cfg, con, w, now=None):
    """Compare the latest observation with history and budget. Store and return new alerts."""
    now = now or datetime.now(timezone.utc)
    a = cfg["alerts"]
    obs = store.observations(con, w["id"])
    if not obs:
        return []
    last, prev = obs[-1], obs[:-1]
    price = last["price_eur"]
    found = []
    # Implausible price vs history: likely a misread, so no "deal" alerts.
    if len(prev) >= a["min_history"]:
        med = statistics.median(o["price_eur"] for o in prev)
        if 100 * (med - price) / med >= a["suspect_drop_pct"]:
            found = [("suspect", f"{price}€ vs median {med:.0f}€: re-verify on the source before reporting")]
    suspect = bool(found)
    budget = w.get("budget_eur")
    if not suspect and a.get("below_budget") and budget and price <= budget:
        found.append(("below_budget", f"{price}€ under budget {budget}€"))
    if not suspect and a.get("new_low") and prev and price < min(o["price_eur"] for o in prev):
        found.append(("new_low", f"new low {price}€ (previous {min(o['price_eur'] for o in prev)}€)"))
    if not suspect and len(prev) >= a["min_history"]:
        med = statistics.median(o["price_eur"] for o in prev)
        drop = 100 * (med - price) / med
        if drop >= a["drop_pct"]:
            found.append(("drop", f"{price}€, -{drop:.0f}% vs median {med:.0f}€"))
    new = []
    for kind, msg in found:
        if _cooldown_ok(cfg, con, w["id"], kind, now):
            store.add_alert(con, w["id"], kind, price, msg, ts=now.replace(microsecond=0).isoformat())
            new.append({"watch": w["id"], "kind": kind, "price_eur": price, "message": msg,
                        "url": last["url"], "source": last["source"]})
    return new


def report(con, w):
    obs = store.observations(con, w["id"])
    prices = [o["price_eur"] for o in obs]
    return {
        "id": w["id"], "observations": len(obs),
        "min_eur": min(prices) if prices else None,
        "median_eur": statistics.median(prices) if prices else None,
        "last": obs[-1] if obs else None,
        "best": min(obs, key=lambda o: o["price_eur"]) if obs else None,
    }
