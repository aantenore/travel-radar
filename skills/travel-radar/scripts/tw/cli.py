"""Internal CLI used by the agent. Output is always JSON on stdout."""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import advise as advise_mod, config, dossier, export, reliability, ideas, profile as profile_mod, schema, scoring, sources, store, trips, watches


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

    tsub = sub.add_parser("trip").add_subparsers(dest="tcmd", required=True)
    tsub.add_parser("list")
    ts = tsub.add_parser("set", help="change status (validated transitions)")
    ts.add_argument("trip"); ts.add_argument("--to", required=True); ts.add_argument("--option")
    ts.add_argument("--confirmed-by-user", help="the user's explicit yes (required for --to monitoring)")
    tp = tsub.add_parser("park", help="pause a trip and its watches, keep a snapshot")
    tp.add_argument("trip"); tp.add_argument("--reason", required=True); tp.add_argument("--until")
    tr = tsub.add_parser("resume", help="preview what changed; with --confirmed-by-user, reactivate")
    tr.add_argument("trip"); tr.add_argument("--confirmed-by-user")
    td = tsub.add_parser("done", help="close the trip, stop watches, return debrief topics")
    td.add_argument("trip")

    isub = sub.add_parser("idea").add_subparsers(dest="icmd", required=True)
    ia = isub.add_parser("add"); ia.add_argument("text"); ia.add_argument("--best-months", help="e.g. 4,5,10")
    il = isub.add_parser("list"); il.add_argument("--all", action="store_true")
    ic = isub.add_parser("checked"); ic.add_argument("id", type=int); ic.add_argument("--note")
    ip = isub.add_parser("promote"); ip.add_argument("id", type=int); ip.add_argument("--trip", required=True)
    idr = isub.add_parser("drop"); idr.add_argument("id", type=int)

    esub = sub.add_parser("export").add_subparsers(dest="ecmd", required=True)
    for name in ("ics", "brief"):
        e = esub.add_parser(name); e.add_argument("trip"); e.add_argument("--out")

    wsub = sub.add_parser("watch").add_subparsers(dest="wcmd", required=True)
    wsub.add_parser("list")
    wsub.add_parser("due")
    wa = wsub.add_parser("add", help="create/update a watch after the user's explicit yes")
    wa.add_argument("file"); wa.add_argument("--confirmed-by-user", required=True)
    r = wsub.add_parser("record")
    r.add_argument("--watch", required=True); r.add_argument("--price-eur", type=float, required=True)
    r.add_argument("--source", required=True); r.add_argument("--url", required=True)
    r.add_argument("--depart"); r.add_argument("--return", dest="return_date")
    r.add_argument("--carrier"); r.add_argument("--stops", type=int); r.add_argument("--note")
    r.add_argument("--excerpt", help="verbatim page text that shows the price")
    r.add_argument("--extractor", help="who read the page: fare-reader or main")
    r.add_argument("--excerpt-price", type=float, help="price as shown on the page, if not in EUR")
    ck = wsub.add_parser("checked")
    ck.add_argument("--watch", required=True)
    ck.add_argument("--status", required=True, choices=["ok", "no_data", "error"]); ck.add_argument("--note")
    ev = wsub.add_parser("evaluate"); ev.add_argument("--watch")
    rp = wsub.add_parser("report"); rp.add_argument("--watch")

    ad = sub.add_parser("advise", help="buy/wait verdict for a flight watch")
    ad.add_argument("--watch", required=True)
    vh = sub.add_parser("verdicts", help="logged buy/wait verdicts for a watch")
    vh.add_argument("--watch", required=True)
    sr = sub.add_parser("source-tier", help="tier of a URL: 1 airline, 2 meta-search, 0 context only")
    sr.add_argument("url")
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
        if a.kind == "trip":
            schema.trip(data)
            return _out({"valid": True})
        confirmed = bool(data.get("confirmed_by_user"))
        schema.watch({**data, "confirmed_by_user": data.get("confirmed_by_user") or {"evidence": "draft", "date": "draft"}})
        return _out({"valid": True, "confirmed_by_user": confirmed})
    if a.cmd == "profile":
        prof = config.profile(cfg)
        if a.pcmd == "show":
            return _out({"path": str(config.path(cfg, "profile")), "profile": prof})
        change = profile_mod.set_value(prof, a.key, json.loads(a.value), a.evidence, a.reason)
        profile_mod.save(config.path(cfg, "profile"), prof)
        return _out({"saved": change})

    if a.cmd == "source-tier":
        return _out({"url": a.url, "tier": reliability.tier(cfg, a.url)})
    con = store.connect(config.path(cfg, "db"))
    if a.cmd == "advise":
        w = _watch(cfg, a.watch)
        res = advise_mod.advise(cfg, con, w)
        store.set_recheck(con, w["id"], res.get("recheck_date"), res["verdict"])
        first = datetime.fromisoformat(store.meta(con, "first_use"))
        shadow = datetime.now(timezone.utc) < first + timedelta(days=cfg["rollout"]["shadow_days"])
        res["shadow"] = shadow
        res["notify"] = not shadow
        store.log_verdict(con, res, shadow)
        return _out(res)
    if a.cmd == "verdicts":
        return _out(store.verdicts(con, a.watch))
    if a.cmd == "trip":
        if a.tcmd == "list":
            return _out(trips.list_all(cfg))
        if a.tcmd == "set":
            return _out(trips.set_status(cfg, a.trip, a.to, a.option, a.confirmed_by_user))
        if a.tcmd == "park":
            return _out(trips.park(cfg, con, a.trip, a.reason, a.until))
        if a.tcmd == "resume":
            return _out(trips.resume(cfg, con, a.trip, confirmed_by_user=a.confirmed_by_user))
        return _out(trips.done(cfg, a.trip))
    if a.cmd == "idea":
        if a.icmd == "add":
            return _out(ideas.add(con, a.text, a.best_months))
        if a.icmd == "list":
            return _out(ideas.list_all(con, a.all))
        if a.icmd == "checked":
            return _out(ideas.checked(con, a.id, a.note))
        if a.icmd == "promote":
            return _out(ideas.promote(cfg, con, a.id, a.trip))
        return _out(ideas.drop(con, a.id))
    if a.cmd == "export":
        trip = trips.load(cfg, a.trip)
        if a.ecmd == "ics":
            text, name = export.ics(cfg, trip), f"{a.trip}.ics"
        else:
            reps = [watches.report(con, w) for w in watches.load_all(cfg) if w.get("trip") == a.trip]
            text, name = export.brief(trip, trips.load_dossier(cfg, a.trip), reps), "brief.md"
        out = Path(a.out).expanduser() if a.out else trips._dir(cfg, a.trip) / name
        out.write_text(text, encoding="utf-8")
        return _out({"written": str(out)})

    invalid = []
    if a.wcmd == "list":
        return _out({"dir": str(config.path(cfg, "watches_dir")), "watches": watches.load_all(cfg, invalid),
                     "invalid": invalid})
    if a.wcmd == "due":
        ws = watches.due(cfg, con, watches.load_all(cfg, invalid))
        rem, ids = trips.reminders(cfg), ideas.due(cfg, con)
        nothing = not any(x.get("due") for x in ws) and not rem and not ids and not invalid
        return _out({"nothing_due": nothing, "watches": ws, "invalid": invalid, "reminders": rem, "ideas": ids})
    if a.wcmd == "add":
        return _out(watches.add(cfg, Path(a.file).expanduser(), a.confirmed_by_user))
    if a.wcmd == "record":
        w = _watch(cfg, a.watch)
        tier = None
        if w.get("kind") in ("flight", "deal"):
            shown = a.excerpt_price if a.excerpt_price is not None else a.price_eur
            tier = reliability.check_flight_observation(cfg, w, a.url, a.depart, a.return_date,
                                                        shown, a.excerpt, a.extractor)
        store.add_observation(con, a.watch, a.price_eur, a.source, a.url, a.depart, a.return_date,
                              a.carrier, a.stops, a.note, tier=tier, excerpt=a.excerpt, extractor=a.extractor)
        return _out({"recorded": a.watch, "price_eur": a.price_eur, "tier": tier})
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
