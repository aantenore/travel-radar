"""Watches: check cadence and alert evaluation (deterministic)."""
import statistics
from datetime import date, datetime, timedelta, timezone

from . import config, reliability, schema, store


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


def add(cfg, src, confirmed_by_user, today=None):
    """Create or update a watch from a YAML file, stamped with the user's quoted yes."""
    import yaml
    from .trips import user_confirmation
    w = config.load_yaml(src)
    w.setdefault("id", src.stem)
    w["confirmed_by_user"] = user_confirmation(confirmed_by_user, today)
    w.setdefault("active", True)
    schema.watch(w)
    d = config.path(cfg, "watches_dir")
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{w['id']}.yaml"
    existed = dest.exists()
    dest.write_text(yaml.safe_dump(w, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"watch": w["id"], "path": str(dest), "updated" if existed else "created": True}


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
    """Which watches to check now. A watch is due by cadence, or because a buy/wait recheck date
    arrived (`advise_recheck`). Flight/deal watches needing page reading are capped per run by
    research.max_subagent_calls_per_run (closest departures first); the rest are deferred."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    out = []
    for w in watches:
        if not w.get("active", True):
            continue
        n, days = per_day(cfg, w, today)
        if days < 0:
            out.append({"id": w["id"], "due": False, "reasons": ["departure in the past: deactivate"]})
            continue
        last = store.last_check(con, w["id"])
        reasons = []
        if last is None or now - datetime.fromisoformat(last) >= timedelta(hours=24 / n):
            reasons.append("cadence")
        rc = store.recheck(con, w["id"])
        if rc and date.fromisoformat(rc["recheck_date"]) <= today:
            reasons.append("advise_recheck")
        out.append({"id": w["id"], "due": bool(reasons), "reasons": reasons, "checks_per_day": n,
                    "days_to_departure": days, "last_check": last, "spec": w})
    budget = cfg["research"]["max_subagent_calls_per_run"]
    readers = sorted((x for x in out if x["due"] and x["spec"].get("kind") in ("flight", "deal")),
                     key=lambda x: x["days_to_departure"])
    for i, x in enumerate(readers):
        if i < budget:
            x["fare_reader"] = True
        else:
            x["due"], x["deferred"] = False, "subagent budget for this run reached: next run"
    return out


def _cooldown_ok(cfg, con, wid, kind, now):
    last = store.last_alert(con, wid, kind)
    return last is None or now - datetime.fromisoformat(last) >= timedelta(hours=cfg["alerts"]["cooldown_h"])


def _partners(cfg, obs, last):
    """Earlier observations that independently confirm `last` within the window and tolerance:
    another tier 1/2 site, or the same site re-read by an independent extractor (main model)."""
    r = cfg["reliability"]
    t_last = datetime.fromisoformat(last["ts"])
    window = timedelta(hours=r["confirm_window_h"])
    out = []
    for o in obs:
        if abs(t_last - datetime.fromisoformat(o["ts"])) > window or not reliability.independent(cfg, o, last):
            continue
        diff = 100 * abs(o["price_eur"] - last["price_eur"]) / last["price_eur"]
        same = reliability.host(o["url"]) == reliability.host(last["url"])
        if diff <= r["same_source_tolerance_pct" if same else "cross_source_tolerance_pct"]:
            out.append(o)
    return out


def evaluate(cfg, con, w, now=None):
    """Compare the latest observation with history and budget.

    Price alerts (below_budget, new_low, drop) are `confirmed` and stored only when a second
    observation confirms the fare; otherwise they are returned as `pending_confirmation`
    (not stored) so the monitoring round re-checks them. `suspect` = implausible drop."""
    now = now or datetime.now(timezone.utc)
    a = cfg["alerts"]
    obs = store.observations(con, w["id"])           # provisional fares excluded
    if not obs:
        return []
    last = obs[-1]
    partners = _partners(cfg, obs[:-1], last)
    hist = [o for o in obs[:-1] if o not in partners]
    price = last["price_eur"]
    found = []
    # Implausible price vs history: likely a misread, so no "deal" alerts.
    if len(hist) >= a["min_history"]:
        med = statistics.median(o["price_eur"] for o in hist)
        if 100 * (med - price) / med >= a["suspect_drop_pct"]:
            found = [("suspect", f"{price}€ vs median {med:.0f}€: re-verify on the source before reporting")]
    suspect = bool(found)
    budget = w.get("budget_eur")
    if not suspect and a.get("below_budget") and budget and price <= budget:
        found.append(("below_budget", f"{price}€ under budget {budget}€"))
    if not suspect and a.get("new_low") and hist and price < min(o["price_eur"] for o in hist):
        found.append(("new_low", f"new low {price}€ (previous {min(o['price_eur'] for o in hist)}€)"))
    if not suspect and len(hist) >= a["min_history"]:
        med = statistics.median(o["price_eur"] for o in hist)
        drop = 100 * (med - price) / med
        if drop >= a["drop_pct"]:
            found.append(("drop", f"{price}€, -{drop:.0f}% vs median {med:.0f}€"))
    new = []
    for kind, msg in found:
        base = {"watch": w["id"], "kind": kind, "price_eur": price, "message": msg,
                "url": last["url"], "source": last["source"], "tier": last.get("tier")}
        if not _cooldown_ok(cfg, con, w["id"], kind, now):
            continue
        if kind != "suspect" and not partners:
            new.append({**base, "status": "pending_confirmation",
                        "next": "main model: re-read the same fare (record with --extractor main) or find it on another tier 1/2 site, then evaluate again"})
            continue
        store.add_alert(con, w["id"], kind, price, msg, ts=now.replace(microsecond=0).isoformat())
        status = "needs_reverify" if kind == "suspect" else "confirmed"
        new.append({**base, "status": status, "confirmed_by": [o["url"] for o in partners]})
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
