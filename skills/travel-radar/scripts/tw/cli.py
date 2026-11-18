"""Internal CLI used by the agent. Output is always JSON on stdout."""
import argparse
import json
import sys
from pathlib import Path

from . import config, dossier, profile as profile_mod, schema, scoring, sources, store, watches


def _out(obj):
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


def _months(s):
    return [int(x) for x in s.split(",")] if s else None


def _watch(cfg, wid):
    for w in watches.load_all(cfg):
        if w["id"] == wid:
            return w
    raise ValueError(f"watch not found or invalid: {wid} (run `tw watch list`)")


def _parser():
    p = argparse.ArgumentParser(prog="tw", description="travel-radar internal CLI (JSON output)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the data home (idempotent)")

    g = sub.add_parser("geocode"); g.add_argument("name")
    for name in ("climate", "forecast"):
        c = sub.add_parser(name)
        c.add_argument("--place"); c.add_argument("--lat", type=float); c.add_argument("--lon", type=float)
        if name == "climate":
            c.add_argument("--months", help="e.g. 4,5")

    h = sub.add_parser("holidays")
    h.add_argument("--country", required=True, help="ISO 3166-1 alpha-2, e.g. CN")
    h.add_argument("--year", type=int, required=True)
    h.add_argument("--from", dest="start"); h.add_argument("--to", dest="end")

    s = sub.add_parser("score"); s.add_argument("trip_file")
    sub.add_parser("checklist", help="print the destination dossier checklist")
    d = sub.add_parser("dossier-check"); d.add_argument("dossier_file")
    v = sub.add_parser("validate"); v.add_argument("kind", choices=["trip", "watch"]); v.add_argument("file")

    psub = sub.add_parser("profile").add_subparsers(dest="pcmd", required=True)
    psub.add_parser("show")
    ps = psub.add_parser("set")
    ps.add_argument("--key", required=True, help="e.g. avoid.temp_max_c")
    ps.add_argument("--value", required=True, help='JSON value, e.g. 30, true, ["FCO"], "solo"')
    ps.add_argument("--evidence", required=True, help="the user's own words that justify it")
    ps.add_argument("--reason")

    wsub = sub.add_parser("watch").add_subparsers(dest="wcmd", required=True)
    wsub.add_parser("list")
    wsub.add_parser("due")
    r = wsub.add_parser("record")
    r.add_argument("--watch", required=True); r.add_argument("--price-eur", type=float, required=True)
    r.add_argument("--source", required=True); r.add_argument("--url", required=True)
    r.add_argument("--depart"); r.add_argument("--return", dest="return_date")
    r.add_argument("--carrier"); r.add_argument("--stops", type=int); r.add_argument("--note")
    ck = wsub.add_parser("checked")
    ck.add_argument("--watch", required=True)
    ck.add_argument("--status", required=True, choices=["ok", "no_data", "error"]); ck.add_argument("--note")
    ev = wsub.add_parser("evaluate"); ev.add_argument("--watch")
    rp = wsub.add_parser("report"); rp.add_argument("--watch")
    return p


def main(argv=None):
    a = _parser().parse_args(argv)
    cfg = config.settings()
    try:
        init = config.init(cfg)
        if a.cmd == "init":
            return _out(init)
        return _run(a, cfg)
    except sources.SourceError as e:
        _out({"error": str(e), "fix": "source unavailable: retry later or record the item as unknown"}); return 2
    except schema.ValidationError as e:
        _out({"error": "validation", "details": e.errors, "fix": "fix the file or value and run again"}); return 3
    except FileNotFoundError as e:
        _out({"error": f"file not found: {e.filename}"}); return 4
    except (ValueError, KeyError) as e:
        _out({"error": f"invalid input: {e}"}); return 5


def _run(a, cfg):
    if a.cmd == "geocode":
        return _out(sources.geocode(cfg, a.name))
    if a.cmd == "climate":
        return _out(sources.climate(cfg, a.place, a.lat, a.lon, _months(a.months)))
    if a.cmd == "forecast":
        return _out(sources.forecast(cfg, a.place, a.lat, a.lon))
    if a.cmd == "holidays":
        return _out(sources.holidays(cfg, a.country, a.year, a.start, a.end))
    if a.cmd == "score":
        trip = config.load_yaml(Path(a.trip_file))
        schema.trip(trip)
        return _out(scoring.score_trip(cfg, config.profile(cfg), trip))
    if a.cmd == "checklist":
        return _out(config.load_yaml(config.path(cfg, "dossier_checklist")))
    if a.cmd == "dossier-check":
        checklist = config.load_yaml(config.path(cfg, "dossier_checklist"))
        return _out(dossier.check(checklist, config.load_yaml(Path(a.dossier_file))))
    if a.cmd == "validate":
        data = config.load_yaml(Path(a.file))
        (schema.trip if a.kind == "trip" else schema.watch)(data)
        return _out({"valid": True})
    if a.cmd == "profile":
        prof = config.profile(cfg)
        if a.pcmd == "show":
            return _out({"path": str(config.path(cfg, "profile")), "profile": prof})
        change = profile_mod.set_value(prof, a.key, json.loads(a.value), a.evidence, a.reason)
        profile_mod.save(config.path(cfg, "profile"), prof)
        return _out({"saved": change})

    con = store.connect(config.path(cfg, "db"))
    invalid = []
    if a.wcmd == "list":
        return _out({"dir": str(config.path(cfg, "watches_dir")), "watches": watches.load_all(cfg, invalid),
                     "invalid": invalid})
    if a.wcmd == "due":
        return _out({"watches": watches.due(cfg, con, watches.load_all(cfg, invalid)), "invalid": invalid})
    if a.wcmd == "record":
        _watch(cfg, a.watch)
        store.add_observation(con, a.watch, a.price_eur, a.source, a.url, a.depart, a.return_date,
                              a.carrier, a.stops, a.note)
        return _out({"recorded": a.watch, "price_eur": a.price_eur})
    if a.wcmd == "checked":
        _watch(cfg, a.watch)
        store.add_check(con, a.watch, a.status, a.note)
        return _out({"checked": a.watch, "status": a.status})
    if a.wcmd == "evaluate":
        ws = [_watch(cfg, a.watch)] if a.watch else watches.load_all(cfg)
        return _out([al for w in ws for al in watches.evaluate(cfg, con, w)])
    if a.wcmd == "report":
        ws = [_watch(cfg, a.watch)] if a.watch else watches.load_all(cfg)
        return _out([watches.report(con, w) for w in ws])


if __name__ == "__main__":
    sys.exit(main())
