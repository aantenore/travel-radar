"""Price observations, checks and alerts history (SQLite)."""
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY, watch_id TEXT NOT NULL, ts TEXT NOT NULL,
  price_eur REAL NOT NULL, depart TEXT, return_date TEXT, carrier TEXT, stops INTEGER,
  source TEXT NOT NULL, url TEXT NOT NULL, note TEXT, tier INTEGER);
CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY, watch_id TEXT NOT NULL, ts TEXT NOT NULL, status TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, watch_id TEXT NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL,
  price_eur REAL, message TEXT);
CREATE TABLE IF NOT EXISTS ideas (
  id INTEGER PRIMARY KEY, text TEXT NOT NULL, best_months TEXT, created TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', last_checked TEXT, note TEXT, promoted_to TEXT);
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
    """Add columns introduced after 0.2.0 to existing databases."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(observations)")}
    if "tier" not in cols:
        con.execute("ALTER TABLE observations ADD COLUMN tier INTEGER")
        con.commit()


def add_observation(con, watch_id, price_eur, source, url, depart=None, return_date=None,
                    carrier=None, stops=None, note=None, ts=None, tier=None):
    if not url:
        raise ValueError("source url is required")
    con.execute(
        "INSERT INTO observations(watch_id, ts, price_eur, depart, return_date, carrier, stops, source, url, note, tier)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (watch_id, ts or now_iso(), price_eur, depart, return_date, carrier, stops, source, url, note, tier))
    con.commit()


def add_check(con, watch_id, status, note=None, ts=None):
    con.execute("INSERT INTO checks(watch_id, ts, status, note) VALUES (?,?,?,?)",
                (watch_id, ts or now_iso(), status, note))
    con.commit()


def last_check(con, watch_id):
    r = con.execute("SELECT ts FROM checks WHERE watch_id=? ORDER BY ts DESC LIMIT 1", (watch_id,)).fetchone()
    return r["ts"] if r else None


def observations(con, watch_id):
    return [dict(r) for r in con.execute(
        "SELECT * FROM observations WHERE watch_id=? ORDER BY ts", (watch_id,))]


def last_alert(con, watch_id, kind):
    r = con.execute("SELECT ts FROM alerts WHERE watch_id=? AND kind=? ORDER BY ts DESC LIMIT 1",
                    (watch_id, kind)).fetchone()
    return r["ts"] if r else None


def add_alert(con, watch_id, kind, price_eur, message, ts=None):
    con.execute("INSERT INTO alerts(watch_id, ts, kind, price_eur, message) VALUES (?,?,?,?,?)",
                (watch_id, ts or now_iso(), kind, price_eur, message))
    con.commit()
