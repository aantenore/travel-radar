"""Adaptive access cascade: which (source, method) to try first, learned from measured outcomes.

Methods: `webfetch` (fare-reader agent), `browser` (rendered page via the session's browser
tools, when available) and `api:<adapter>` (only when the user configured a key). Every
attempt is recorded; `plan` orders attempts by smoothed success rate, so the system adapts
when a site starts blocking or rendering client-side.
"""
import json
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import schema, sources, store
from .wikidata import registrable

OUTCOMES = ("ok", "no_data", "blocked", "error", "ok_after_user_challenge")


def detect_challenge(cfg, text):
    """Generic anti-bot / CAPTCHA page detection from page text (markers in config).
    Returns the matched marker or None. Detection only: challenges are never solved here."""
    low = (text or "").lower()
    for m in cfg["access"]["challenge_markers"]:
        if m.lower() in low:
            return m
    return None


def record(con, domain, method, outcome, note=None, now=None, challenge=False):
    """`challenge=True` with `blocked` = an anti-bot page was shown; `ok_after_user_challenge` =
    the user solved it in an attended session. Both mark the source as challenge-gated."""
    if outcome not in OUTCOMES:
        raise schema.ValidationError([f"outcome must be one of {OUTCOMES}"])
    ts = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    d = registrable(domain)
    con.execute("INSERT OR IGNORE INTO access_stats(domain, method) VALUES (?,?)", (d, method))
    col = "ok" if outcome == "ok_after_user_challenge" else outcome
    sets = [f"{col}={col}+1", "last_attempt=?", "last_note=?", "last_outcome=?"]
    args = [ts, note, outcome]
    if outcome in ("ok", "ok_after_user_challenge"):
        sets.append("last_success=?"); args.append(ts)
    if outcome == "blocked":
        sets.append("last_blocked=?"); args.append(ts)
    if outcome == "ok_after_user_challenge" or (outcome == "blocked" and challenge):
        sets.append("challenge=challenge+1")
    con.execute(f"UPDATE access_stats SET {', '.join(sets)} WHERE domain=? AND method=?", (*args, d, method))
    con.commit()
    return {"domain": d, "method": method, "outcome": outcome, "challenge": bool(challenge) or outcome == "ok_after_user_challenge"}


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


def _gated(row):
    """Challenge-gated: an anti-bot page was met and no plain success happened since."""
    return bool(row and row.get("challenge") and row.get("last_outcome") != "ok")


def _in_backoff(cfg, row, now):
    if not row or row.get("last_outcome") != "blocked" or not row.get("last_blocked"):
        return None
    until = datetime.fromisoformat(row["last_blocked"]) + timedelta(hours=cfg["access"]["blocked_backoff_hours"])
    return until.isoformat() if now < until else None


def plan(cfg, con, candidates, browser=False, attended=False, now=None):
    """Ordered attempt plan for verified candidate domains. `candidates` = [(domain, tier)].
    Unattended: challenge-gated sources are excluded and recently blocked ones backed off.
    Attended: challenge-gated sources come last, as user handoffs with a per-site search cap."""
    now = now or datetime.now(timezone.utc)
    rows = {(r["domain"], r["method"]): r for r in stats(con)}
    gated_domains = {d for (d, _), r in rows.items() if _gated(r)}   # a challenge on one method gates the site
    steps, skipped = [], []
    methods = available_methods(cfg, browser)
    for domain, tier in candidates:
        d = registrable(domain)
        for m in methods:
            if m.startswith("api:"):
                continue
            row = rows.get((d, m))
            score, n = _score(cfg, row)
            step = {"domain": d, "tier": tier, "method": m, "success_rate": round(score, 3), "attempts": n}
            if d in gated_domains:
                if not attended or m != "browser":
                    skipped.append({**step, "reason": "challenge-gated: only in attended sessions, via the browser"})
                    continue
                step.update(handoff="user_solves_challenge", max_searches_this_session=cfg["access"]["max_attended_searches_per_site"])
            elif _in_backoff(cfg, row, now):
                skipped.append({**step, "reason": f"blocked recently: back off until {_in_backoff(cfg, row, now)}"})
                continue
            steps.append(step)
    for m in methods:
        if m.startswith("api:"):
            score, n = _score(cfg, rows.get((m, m)))
            steps.append({"domain": m, "tier": 2, "method": m, "success_rate": round(score, 3), "attempts": n})
    order = {m: i for i, m in enumerate(methods)}
    steps.sort(key=lambda s: (bool(s.get("handoff")), -s["success_rate"], s["tier"], order[s["method"]]))
    return {"methods_available": methods, "attended": attended,
            "steps": steps[: cfg["access"]["max_plan_steps"]], "skipped": skipped}


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
