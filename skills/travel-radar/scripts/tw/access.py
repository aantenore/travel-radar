"""Adaptive access cascade: which (source, method) to try first, learned from measured outcomes.

Methods: `webfetch` (fare-reader agent), `browser` (rendered page via the session's browser
tools, when available) and `api:<adapter>` (only when the user configured a key). Every
attempt is recorded; `plan` orders attempts by smoothed success rate, so the system adapts
when a site starts blocking or rendering client-side.
"""
import json
import os
import urllib.parse
from datetime import datetime, timezone

from . import schema, sources, store
from .wikidata import registrable

OUTCOMES = ("ok", "no_data", "blocked", "error")


def record(con, domain, method, outcome, note=None, now=None):
    if outcome not in OUTCOMES:
        raise schema.ValidationError([f"outcome must be one of {OUTCOMES}"])
    ts = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    d = registrable(domain)
    con.execute("INSERT OR IGNORE INTO access_stats(domain, method) VALUES (?,?)", (d, method))
    con.execute(f"UPDATE access_stats SET {outcome}={outcome}+1, last_attempt=?, last_note=?"
                + (", last_success=?" if outcome == "ok" else "") + " WHERE domain=? AND method=?",
                (ts, note, ts, d, method) if outcome == "ok" else (ts, note, d, method))
    con.commit()
    return {"domain": d, "method": method, "outcome": outcome}


def stats(con):
    return [dict(r) for r in con.execute("SELECT * FROM access_stats ORDER BY domain, method")]


def available_methods(cfg, browser):
    ms = [m for m in cfg["access"]["methods"] if m != "browser" or browser]
    ms += [f"api:{name}" for name, a in (cfg["access"].get("api_adapters") or {}).items() if os.environ.get(a["env"])]
    return ms


def _score(cfg, row):
    if not row:
        return cfg["access"]["prior_success"], 0
    n = row["ok"] + row["no_data"] + row["blocked"] + row["error"]
    return (row["ok"] + 1) / (n + 2), n          # Laplace-smoothed success rate


def plan(cfg, con, candidates, browser=False):
    """Ordered attempt plan for verified candidate domains. `candidates` = [(domain, tier)]."""
    rows = {(r["domain"], r["method"]): r for r in stats(con)}
    steps = []
    methods = available_methods(cfg, browser)
    for domain, tier in candidates:
        d = registrable(domain)
        for m in methods:
            if m.startswith("api:"):
                continue
            score, n = _score(cfg, rows.get((d, m)))
            steps.append({"domain": d, "tier": tier, "method": m, "success_rate": round(score, 3), "attempts": n})
    for m in methods:
        if m.startswith("api:"):
            score, n = _score(cfg, rows.get((m, m)))
            steps.append({"domain": m, "tier": 2, "method": m, "success_rate": round(score, 3), "attempts": n})
    order = {m: i for i, m in enumerate(methods)}
    steps.sort(key=lambda s: (-s["success_rate"], s["tier"], order[s["method"]]))
    return {"methods_available": methods, "steps": steps[: cfg["access"]["max_plan_steps"]]}


# ---- API adapters (opt-in: only with a key the user configured) ----

def serpapi_fares(cfg, watch, depart, return_date):
    a = cfg["access"]["api_adapters"]["serpapi"]
    key = os.environ.get(a["env"])
    if not key:
        raise schema.ValidationError([f"serpapi adapter not configured ({a['env']} is not set)"])
    params = {"engine": a["engine"], "departure_id": ",".join(watch["origin"]),
              "arrival_id": ",".join(watch["destination"]), "outbound_date": depart, "currency": "EUR",
              "hl": "en", "api_key": key}
    if return_date:
        params["return_date"] = return_date
    else:
        params["type"] = "2"
    data = sources._get_json(cfg, a["endpoint"], params)
    return parse_serpapi(data, depart, return_date)


def parse_serpapi(data, depart, return_date):
    """Normalise a SerpApi Google Flights response to fare-reader output (key never included)."""
    url = (data.get("search_metadata") or {}).get("google_flights_url") or "https://www.google.com/travel/flights"
    out = []
    for f in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        legs = f.get("flights") or []
        if f.get("price") is None or not legs:
            continue
        carriers = sorted({l.get("airline") for l in legs if l.get("airline")})
        excerpt = json.dumps({"price": f["price"], "airlines": carriers, "layovers": len(f.get("layovers") or [])})
        out.append({"price": f["price"], "currency": "EUR", "depart": depart, "return": return_date,
                    "carrier": ", ".join(carriers), "stops": len(f.get("layovers") or []), "url": url,
                    "source_domain": urllib.parse.urlparse(url).hostname, "excerpt": excerpt})
    return out
