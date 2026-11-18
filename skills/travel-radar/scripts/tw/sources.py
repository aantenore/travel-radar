"""Adapters for sources with public APIs (climate, forecast, geocoding, holidays).

Sources without an API (flight prices, safety, deals) are researched online by the
agent and recorded with `tw watch record` or in the dossier.
"""
import hashlib
import json
import statistics
import time
import urllib.parse
import urllib.request
from datetime import date

from . import config


class SourceError(RuntimeError):
    pass


def _get_json(cfg, url, params):
    full = url + "?" + urllib.parse.urlencode(params, doseq=True) if params else url
    cache_dir = config.path(cfg, "cache_dir")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / (hashlib.sha256(full.encode()).hexdigest() + ".json")
    ttl = cfg["http"]["cache_ttl_s"]
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
        return json.loads(cache_file.read_text())
    req = urllib.request.Request(full, headers={"User-Agent": cfg["http"]["user_agent"]})
    try:
        with urllib.request.urlopen(req, timeout=cfg["http"]["timeout_s"]) as r:
            data = json.loads(r.read().decode())
    except Exception as e:  # network, HTTP, JSON
        raise SourceError(f"{url}: {e}") from e
    cache_file.write_text(json.dumps(data))
    return data


def geocode(cfg, name, count=3):
    """Search the name in each configured language (names are matched per language, e.g.
    'Pechino' only in Italian); results are merged, first language first."""
    out, seen = [], set()
    for lang in cfg["geocoding"]["languages"]:
        data = _get_json(cfg, cfg["sources"]["geocoding"], {"name": name, "count": count, "language": lang})
        for r in data.get("results", []):
            if r.get("id") in seen:
                continue
            seen.add(r.get("id"))
            out.append({k: r.get(k) for k in ("name", "country", "country_code", "admin1", "admin2", "admin3",
                                               "latitude", "longitude", "timezone", "population", "feature_code")})
    return out


class AmbiguousPlace(ValueError):
    def __init__(self, place, candidates):
        super().__init__(f"place {place!r} is ambiguous: pass --admin1 or --country, write it as "
                         f"'Name, Region', or give --lat/--lon")
        self.candidates = candidates


def pick_place(cfg, place, hits, admin1=None, country=None):
    """Choose one geocoding hit without guessing. Filters: admin1/country (also from
    'Name, Region' or 'Name, Region, Country'). Several different places left -> AmbiguousPlace,
    unless one is clearly dominant by population (ratio in config), which is then reported."""
    parts = [p.strip() for p in (place or "").split(",") if p.strip()]
    if len(parts) > 1:
        admin1 = admin1 or parts[1]
        if len(parts) > 2:
            country = country or parts[2]

    def norm(x):
        return (x or "").strip().lower()
    if admin1:   # region, province, prefecture or county
        hits = [h for h in hits if any(norm(admin1) in norm(h.get(k)) for k in ("admin1", "admin2", "admin3"))]
    if country:
        hits = [h for h in hits if norm(country) in (norm(h.get("country")), norm(h.get("country_code")))]
    distinct = {}
    for h in hits:
        distinct.setdefault((h.get("country_code"), h.get("admin1"), round(h["latitude"], 1), round(h["longitude"], 1)), h)
    hits = list(distinct.values())
    if not hits:
        raise SourceError(f"place not found: {place}" + (f" in {admin1}" if admin1 else ""))
    if len(hits) == 1:
        return {**hits[0], "resolution": "unique"}
    ranked = sorted(hits, key=lambda h: -(h.get("population") or 0))
    p0, p1 = ranked[0].get("population") or 0, ranked[1].get("population") or 0
    ratio = cfg["geocoding"]["dominance_ratio"]
    if p0 and p0 >= ratio * max(p1, 1):
        return {**ranked[0], "resolution": "dominant_population",
                "alternatives": [{k: h.get(k) for k in ("name", "admin1", "country")} for h in ranked[1:4]]}
    raise AmbiguousPlace(place, [{k: h.get(k) for k in ("name", "admin1", "admin2", "country", "latitude", "longitude", "population")}
                                 for h in ranked])


def _resolve(cfg, place, lat, lon, admin1=None, country=None):
    if lat is not None and lon is not None:
        return {"name": place or f"{lat},{lon}", "latitude": lat, "longitude": lon, "resolution": "coordinates"}
    name = (place or "").split(",")[0].strip()
    filtered = bool(admin1 or country or "," in (place or ""))
    n = cfg["geocoding"]["candidates_filtered" if filtered else "candidates"]
    return pick_place(cfg, place, geocode(cfg, name, n), admin1, country)


def climate(cfg, place=None, lat=None, lon=None, months=None, today=None, admin1=None, country=None):
    """Monthly climate normals over the last N complete years (Open-Meteo archive)."""
    loc = _resolve(cfg, place, lat, lon, admin1, country)
    today = today or date.today()
    years = cfg["climate"]["years_back"]
    start = date(today.year - years, 1, 1)
    end = date(today.year - 1, 12, 31)
    data = _get_json(cfg, cfg["sources"]["climate_archive"], {
        "latitude": loc["latitude"], "longitude": loc["longitude"],
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
    })
    d = data["daily"]
    rain_mm = cfg["climate"]["rain_day_mm"]
    by_month = {}
    for day, tmax, tmin, pr in zip(d["time"], d["temperature_2m_max"], d["temperature_2m_min"], d["precipitation_sum"]):
        y, m = int(day[:4]), int(day[5:7])
        b = by_month.setdefault(m, {"tmax": [], "tmin": [], "rain_days": {}, "precip": {}})
        if tmax is not None:
            b["tmax"].append(tmax)
        if tmin is not None:
            b["tmin"].append(tmin)
        if pr is not None:
            b["precip"][y] = b["precip"].get(y, 0) + pr
            b["rain_days"][y] = b["rain_days"].get(y, 0) + (1 if pr >= rain_mm else 0)
    out = []
    for m in sorted(by_month):
        if months and m not in months:
            continue
        b = by_month[m]
        out.append({
            "month": m,
            "tmax_mean_c": round(statistics.mean(b["tmax"]), 1) if b["tmax"] else None,
            "tmin_mean_c": round(statistics.mean(b["tmin"]), 1) if b["tmin"] else None,
            "rain_days_mean": round(statistics.mean(b["rain_days"].values()), 1) if b["rain_days"] else None,
            "precip_mm_mean": round(statistics.mean(b["precip"].values()), 1) if b["precip"] else None,
        })
    return {"place": loc, "period": f"{start.year}-{end.year}", "source": cfg["sources"]["climate_archive"], "months": out}


def forecast(cfg, place=None, lat=None, lon=None, days=16, admin1=None, country=None):
    loc = _resolve(cfg, place, lat, lon, admin1, country)
    data = _get_json(cfg, cfg["sources"]["forecast"], {
        "latitude": loc["latitude"], "longitude": loc["longitude"],
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        "forecast_days": days, "timezone": "auto",
    })
    d = data["daily"]
    rows = [dict(zip(["date", "tmax_c", "tmin_c", "precip_mm", "precip_prob"], r)) for r in zip(
        d["time"], d["temperature_2m_max"], d["temperature_2m_min"], d["precipitation_sum"], d["precipitation_probability_max"])]
    return {"place": loc, "source": cfg["sources"]["forecast"], "days": rows}


def holidays(cfg, country, year, start=None, end=None):
    data = _get_json(cfg, f"{cfg['sources']['holidays']}/{year}/{country.upper()}", None)
    rows = [{"date": h["date"], "name": h.get("name"), "local_name": h.get("localName"), "types": h.get("types")} for h in data]
    if start:
        rows = [r for r in rows if r["date"] >= start]
    if end:
        rows = [r for r in rows if r["date"] <= end]
    return {"country": country.upper(), "year": year, "source": cfg["sources"]["holidays"], "holidays": rows}
