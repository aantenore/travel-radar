#!/usr/bin/env python3
"""Score a fare extractor against the labelled excerpts.

Usage:
  python3 evals/score_extraction.py <outputs.json> [--threshold 0.95]

<outputs.json> maps fixture id -> the extractor's JSON for that excerpt
({price, currency, depart, return, carrier, stops}). Produce it by giving each excerpt in
tests/fixtures/fare_excerpts.yaml to the extractor under test (e.g. the fare-reader agent
with model: haiku) and saving its answer. A case passes only if every field matches exactly.
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

FIELDS = ("price", "currency", "depart", "return", "carrier", "stops")
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fare_excerpts.yaml"


def _norm(field, v):
    if v is None:
        return None
    if field == "price":
        return round(float(v), 2)
    if field == "stops":
        return int(v)
    return str(v).strip().lower()


def score(fixtures, outputs):
    rows = []
    for case in fixtures:
        got = outputs.get(case["id"]) or {}
        wrong = [f for f in FIELDS if _norm(f, got.get(f)) != _norm(f, case["expected"].get(f))]
        rows.append({"id": case["id"], "pass": not wrong, "wrong_fields": wrong})
    rate = sum(r["pass"] for r in rows) / len(rows)
    return {"cases": len(rows), "exact_match_rate": round(rate, 3), "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outputs")
    ap.add_argument("--threshold", type=float, default=0.95)
    a = ap.parse_args()
    res = score(yaml.safe_load(FIXTURES.read_text()), json.loads(Path(a.outputs).read_text()))
    res["threshold"] = a.threshold
    res["adopt"] = res["exact_match_rate"] >= a.threshold
    json.dump(res, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if res["adopt"] else 1


if __name__ == "__main__":
    sys.exit(main())
