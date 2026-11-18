"""Price observations, checks and alerts history (SQLite)."""
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY, watch_id TEXT NOT NULL, ts TEXT NOT NULL,
  price_eur REAL NOT NULL, depart TEXT, return_date TEXT, carrier TEXT, stops INTEGER,
  source TEXT NOT NULL, url TEXT NOT NULL, note TEXT, tier INTEGER, excerpt TEXT, extractor TEXT,
  verified_by TEXT, fare_status TEXT, itinerary TEXT);
CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY, watch_id TEXT NOT NULL, ts TEXT NOT NULL, status TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, watch_id TEXT NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL,
  price_eur REAL, message TEXT);
CREATE TABLE IF NOT EXISTS ideas (
  id INTEGER PRIMARY KEY, text TEXT NOT NULL, best_months TEXT, created TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', last_checked TEXT, note TEXT, promoted_to TEXT);
CREATE TABLE IF NOT EXISTS rechecks (
  watch_id TEXT PRIMARY KEY, recheck_date TEXT NOT NULL, verdict TEXT NOT NULL, set_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS verdicts (
  id INTEGER PRIMARY KEY, watch_id TEXT NOT NULL, ts TEXT NOT NULL, verdict TEXT NOT NULL,
  confidence TEXT NOT NULL, price_eur REAL, recheck_date TEXT, shadow INTEGER NOT NULL, reasons TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge (
  kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, sources TEXT NOT NULL,
  fetched TEXT NOT NULL, ttl_days INTEGER NOT NULL, verified_by TEXT NOT NULL, PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS access_stats (
  domain TEXT NOT NULL, method TEXT NOT NULL, ok INTEGER NOT NULL DEFAULT 0, no_data INTEGER NOT NULL DEFAULT 0,
  blocked INTEGER NOT NULL DEFAULT 0, error INTEGER NOT NULL DEFAULT 0, last_success TEXT, last_attempt TEXT,
  last_note TEXT, last_outcome TEXT, challenge INTEGER NOT NULL DEFAULT 0, last_blocked TEXT,
  PRIMARY KEY (domain, method));
CREATE INDEX IF NOT EXISTS ix_obs ON observations(watch_id, ts);
CREATE INDEX IF NOT EXISTS ix_chk ON checks(watch_id, ts);
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path):
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _migrate(con):
    """Add columns introduced after 0.2.0 to existing databases; record the first-use date."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(observations)")}
    for name, typ in (("tier", "INTEGER"), ("excerpt", "TEXT"), ("extractor", "TEXT"), ("verified_by", "TEXT"),
                      ("fare_status", "TEXT"), ("itinerary", "TEXT")):
        if name not in cols:
            con.execute(f"ALTER TABLE observations ADD COLUMN {name} {typ}")
    acols = {r["name"] for r in con.execute("PRAGMA table_info(access_stats)")}
    for name, typ in (("last_outcome", "TEXT"), ("challenge", "INTEGER NOT NULL DEFAULT 0"), ("last_blocked", "TEXT")):
        if name not in acols:
            con.execute(f"ALTER TABLE access_stats ADD COLUMN {name} {typ}")
    con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('first_use', ?)", (now_iso(),))
    con.commit()


def add_observation(con, watch_id, price_eur, source, url, depart=None, return_date=None,
                    carrier=None, stops=None, note=None, ts=None, tier=None, excerpt=None, extractor=None,
                    verified_by=None, fare_status=None, itinerary=None):
    if not url:
        raise ValueError("source url is required")
    con.execute(
        "INSERT INTO observations(watch_id, ts, price_eur, depart, return_date, carrier, stops, source, url, note,"
        " tier, excerpt, extractor, verified_by, fare_status, itinerary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (watch_id, ts or now_iso(), price_eur, depart, return_date, carrier, stops, source, url, note,
         tier, excerpt, extractor, verified_by, fare_status, json.dumps(itinerary) if itinerary else None))
    con.commit()


def add_check(con, watch_id, status, note=None, ts=None):
    con.execute("INSERT INTO checks(watch_id, ts, status, note) VALUES (?,?,?,?)",
                (watch_id, ts or now_iso(), status, note))
    con.commit()


def last_check(con, watch_id):
    r = con.execute("SELECT ts FROM checks WHERE watch_id=? ORDER BY ts DESC LIMIT 1", (watch_id,)).fetchone()
    return r["ts"] if r else None


def observations(con, watch_id, include_provisional=False):
    """Price history. Provisional fares (far-future or flagged) are excluded unless asked for."""
    q = "SELECT * FROM observations WHERE watch_id=?" + ("" if include_provisional else
        " AND (fare_status IS NULL OR fare_status != 'provisional')") + " ORDER BY ts"
    return [dict(r) for r in con.execute(q, (watch_id,))]


def provisional_count(con, watch_id):
    return con.execute("SELECT COUNT(*) FROM observations WHERE watch_id=? AND fare_status='provisional'",
                       (watch_id,)).fetchone()[0]


def last_alert(con, watch_id, kind):
    r = con.execute("SELECT ts FROM alerts WHERE watch_id=? AND kind=? ORDER BY ts DESC LIMIT 1",
                    (watch_id, kind)).fetchone()
    return r["ts"] if r else None


def add_alert(con, watch_id, kind, price_eur, message, ts=None):
    con.execute("INSERT INTO alerts(watch_id, ts, kind, price_eur, message) VALUES (?,?,?,?,?)",
                (watch_id, ts or now_iso(), kind, price_eur, message))
    con.commit()


def meta(con, key):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def set_recheck(con, watch_id, recheck_date, verdict):
    if recheck_date is None:
        con.execute("DELETE FROM rechecks WHERE watch_id=?", (watch_id,))
    else:
        con.execute("INSERT INTO rechecks(watch_id, recheck_date, verdict, set_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(watch_id) DO UPDATE SET recheck_date=excluded.recheck_date,"
                    " verdict=excluded.verdict, set_at=excluded.set_at",
                    (watch_id, recheck_date, verdict, now_iso()))
    con.commit()


def recheck(con, watch_id):
    r = con.execute("SELECT recheck_date, verdict FROM rechecks WHERE watch_id=?", (watch_id,)).fetchone()
    return dict(r) if r else None


def log_verdict(con, res, shadow):
    import json
    con.execute("INSERT INTO verdicts(watch_id, ts, verdict, confidence, price_eur, recheck_date, shadow, reasons)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (res["watch"], now_iso(), res["verdict"], res["confidence"],
                 (res.get("metrics") or {}).get("price_eur"), res.get("recheck_date"), int(shadow),
                 json.dumps(res.get("reasons", []))))
    con.commit()


def verdicts(con, watch_id, limit=20):
    return [dict(r) for r in con.execute(
        "SELECT * FROM verdicts WHERE watch_id=? ORDER BY ts DESC LIMIT ?", (watch_id, limit))]
