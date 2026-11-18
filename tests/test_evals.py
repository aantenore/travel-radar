import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "travel-radar" / "scripts"))
os.environ.setdefault("TRAVEL_RADAR_HOME", tempfile.mkdtemp(prefix="travel-radar-test-"))

import yaml  # noqa: E402

from tw import advise, config, reliability, store, watches  # noqa: E402

CFG = config.settings()
FIX = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
BASE = {"id": "w1", "kind": "flight", "origin": ["FCO"], "destination": ["PEK"], "haul": "long", "peak": True,
        "depart_window": {"from": "2027-06-01"}}


class ExcerptGateEval(unittest.TestCase):
    def test_labelled_excerpts(self):
        for case in yaml.safe_load((FIX / "fare_excerpts.yaml").read_text()):
            with self.subTest(case=case["id"]):
                price = case["expected"]["price"]
                self.assertTrue(reliability.price_in_excerpt(price, case["excerpt"]))
                self.assertFalse(reliability.price_in_excerpt(price + 7, case["excerpt"]))
                self.assertGreaterEqual(reliability.tier(CFG, case["url"]), 1)


class AdviseBacktest(unittest.TestCase):
    def test_backtests(self):
        for case in yaml.safe_load((FIX / "advise_backtests.yaml").read_text()):
            with self.subTest(case=case["name"]):
                con = store.connect(":memory:")
                for days_ago, price in case["history"]:
                    store.add_observation(con, "w1", price, "s", "https://www.kayak.it/f",
                                          ts=(NOW - timedelta(days=days_ago)).isoformat(), tier=2)
                r = advise.advise(CFG, con, {**BASE, **case["watch"]}, NOW)
                self.assertEqual(r["verdict"], case["expected"], r["reasons"])
                self.assertTrue(any("heuristic" in x for x in r["reasons"]))


class RecheckAndBudget(unittest.TestCase):
    def test_recheck_date_makes_watch_due(self):
        con = store.connect(":memory:")
        store.add_check(con, "w1", "ok", ts=NOW.isoformat())
        self.assertFalse(watches.due(CFG, con, [BASE], NOW)[0]["due"])
        store.set_recheck(con, "w1", (NOW.date() + timedelta(days=7)).isoformat(), "WAIT")
        self.assertFalse(watches.due(CFG, con, [BASE], NOW + timedelta(hours=1))[0]["due"])
        later = NOW + timedelta(days=7, hours=1)
        store.add_check(con, "w1", "ok", ts=later.isoformat())
        d = watches.due(CFG, con, [BASE], later)[0]
        self.assertEqual((d["due"], d["reasons"]), (True, ["advise_recheck"]))
        store.set_recheck(con, "w1", None, "BUY_NOW")
        self.assertFalse(watches.due(CFG, con, [BASE], later)[0]["due"])

    def test_subagent_budget_defers_farthest_departures(self):
        con = store.connect(":memory:")
        ws = [{**BASE, "id": f"w{i}", "depart_window": {"from": (date(2027, 1, 1) + timedelta(days=30 * i)).isoformat()}}
              for i in range(6)]
        out = {x["id"]: x for x in watches.due(CFG, con, ws, NOW)}
        n = CFG["research"]["max_subagent_calls_per_run"]
        self.assertEqual(sum(1 for x in out.values() if x.get("fare_reader")), n)
        self.assertTrue(out["w0"].get("fare_reader"))
        self.assertIn("deferred", out["w5"])
        self.assertFalse(out["w5"]["due"])

    def test_verdict_log(self):
        con = store.connect(":memory:")
        store.log_verdict(con, {"watch": "w1", "verdict": "WAIT", "confidence": "low", "recheck_date": "2026-11-25",
                                "reasons": ["x"]}, shadow=True)
        v = store.verdicts(con, "w1")[0]
        self.assertEqual((v["verdict"], v["shadow"]), ("WAIT", 1))
        self.assertIsNotNone(store.meta(con, "first_use"))


if __name__ == "__main__":
    unittest.main()
