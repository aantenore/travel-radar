"""Exports: iCalendar file (trip, booking deadlines, re-check reminders) and a markdown trip brief."""
from datetime import datetime, timedelta, timezone

from . import schema, trips


def _require_chosen(trip):
    o = trips.chosen_option(trip)
    per = trips.period(trip)
    if not o or not per:
        raise schema.ValidationError([f"trip {trip['id']}: choose an option with a period first (`tw trip set --to chosen --option <id>`)"])
    return o, per


def _esc(s):
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    # RFC 5545: lines longer than 75 octets are folded with CRLF + space.
    b = line.encode("utf-8")
    if len(b) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        e = ch.encode("utf-8")
        if len(cur) + len(e) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += e
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def _event(uid, start, end, summary, description, stamp):
    return [
        "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}",
        f"SUMMARY:{_esc(summary)}", f"DESCRIPTION:{_esc(description)}", "END:VEVENT",
    ]


def ics(cfg, trip, now=None):
    o, (start, end) = _require_chosen(trip)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    tid = trip["id"]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//travel-radar//EN", "CALSCALE:GREGORIAN"]
    lines += _event(f"{tid}-trip@travel-radar", start, end + timedelta(days=1), f"Trip: {o.get('name', tid)}",
                    "Places: " + ", ".join(o.get("places") or []), stamp)
    for n, d in enumerate(trip.get("deadlines") or []):
        day = trips._as_date(d["date"])
        lines += _event(f"{tid}-deadline-{n}@travel-radar", day, day + timedelta(days=1),
                        f"Deadline: {d['title']}", f"Source: {d.get('source', '')}", stamp)
    from . import experiences, requirements
    for d in requirements.check(cfg, tid)["deadlines"]:
        if d["option"] in ("chosen", trip.get("chosen_option")):
            day = trips._as_date(d["deadline"])
            lines += _event(f"{tid}-req-{d['id']}@travel-radar", day, day + timedelta(days=1),
                            f"Deadline: {d['action']}", f"Requirement {d['id']} ({d['status']})", stamp)
    for d in experiences.check(cfg, tid)["deadlines"]:
        day = trips._as_date(d["deadline"])
        lines += _event(f"{tid}-exp-{d['id']}@travel-radar", day, day + timedelta(days=1),
                        f"Book: {d['name']}", "Booking lead time from the source", stamp)
    for days in cfg.get("export", {}).get("recheck_before_days", []):
        day = start - timedelta(days=days)
        lines += _event(f"{tid}-recheck-{days}@travel-radar", day, day + timedelta(days=1),
                        f"Re-check safety and entry rules ({o.get('name', tid)})",
                        f"{days} days before departure: run a travel-radar monitoring round.", stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


def brief(trip, dossier=None, reports=None, reqs=None, geo=None, exps=None):
    o, (start, end) = _require_chosen(trip)
    src = o.get("sources") or {}
    out = [f"# {o.get('name', trip['id'])}", "",
           f"- Dates: {start.isoformat()} to {end.isoformat()} ({(end - start).days + 1} days)",
           f"- Places: {', '.join(o.get('places') or []) or 'n/a'}",
           f"- Estimated cost: {o.get('est_cost_eur', 'unknown')} EUR",
           f"- Request: {trip.get('request', '')}", ""]
    if trip.get("deadlines"):
        out += ["## Deadlines", ""]
        out += [f"- {trips._as_date(d['date']).isoformat()}: {d['title']} ({d.get('source', '')})"
                for d in sorted(trip["deadlines"], key=lambda d: str(d["date"]))]
        out.append("")
    if reports:
        out += ["## Flights", ""]
        for r in reports:
            best = r.get("best") or {}
            out.append(f"- {r['id']}: best {r.get('min_eur')} EUR ({best.get('url', 'no link')}), "
                       f"median {r.get('median_eur')} EUR, {r.get('observations')} observations")
        out.append("")
    if dossier:
        out += ["## Dossier", ""]
        for k, v in (dossier.get("items") or {}).items():
            links = ", ".join(s.get("url", "") for s in (v or {}).get("sources") or [])
            out.append(f"- **{k}** ({(v or {}).get('status', 'unknown')}): {(v or {}).get('summary', '')} {links}".rstrip())
        out.append("")
    if geo:
        out += ["## Geopolitical situation", ""]
        for g in geo:
            links = ", ".join(s["url"] for s in g.get("sources", []))
            out.append(f"- **{g['topic']}** ({g['severity']}, {g.get('trend', 'unknown')}): {g.get('what_to_do', '')} {links}".rstrip())
        out.append("")
    if reqs:
        out += ["## Visas, permits and everything to do", ""]
        for r in sorted(reqs, key=lambda r: min((r.get("deadlines") or {"": "9999"}).values())):
            dl = ", ".join(sorted((r.get("deadlines") or {}).values()))
            off = "official" if any(s.get("official") for s in r.get("sources", [])) else "unofficial, verify"
            out.append(f"- [{r['status']}] {r['action']}" + (f" (by {dl})" if dl else "") + f" — {off}: "
                       + ", ".join(s["url"] for s in r.get("sources", [])))
        out.append("")
    if exps:
        out += ["## Experiences", ""]
        for x in exps:
            b = x.get("booking") or {}
            how = (b.get("url") + ("" if b.get("verified_by") != "unverified" else " (unverified link)")) if b.get("url") \
                else b.get("instructions", "")
            out.append(f"- **{x['name']}** ({x['place']}): {b.get('method')}: {how}"
                       + (f"; book by {x['booking_deadline']}" if x.get("booking_deadline") else ""))
        out.append("")
    if src:
        out += ["## Sources", ""] + [f"- {k}: {', '.join(map(str, v))}" for k, v in src.items()] + [""]
    return "\n".join(out)
