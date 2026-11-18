"""Check completeness and freshness of a destination dossier."""
from datetime import date


def _to_date(v):
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def check(checklist, dossier, today=None):
    today = today or date.today()
    items = dossier.get("items") or {}
    issues, blockers, warnings = [], [], []
    for key, spec in checklist["items"].items():
        it = items.get(key)
        if not it:
            if spec.get("required"):
                issues.append({"item": key, "problem": "missing", "hint": spec.get("hint")})
            continue
        status = it.get("status", "unknown")
        sources = it.get("sources") or []
        if status == "unknown" and spec.get("required"):
            issues.append({"item": key, "problem": "unknown", "hint": spec.get("hint")})
        if not sources and status != "unknown":
            issues.append({"item": key, "problem": "no source"})
        dates = [_to_date(s["accessed"]) for s in sources if s.get("accessed")]
        if sources and len(dates) < len(sources):
            issues.append({"item": key, "problem": "source without access date"})
        if dates:
            age = (today - max(dates)).days
            if age > spec["max_age_days"]:
                issues.append({"item": key, "problem": f"stale ({age}d > {spec['max_age_days']}d)", "hint": spec.get("hint")})
        if status == "blocker":
            blockers.append({"item": key, "summary": it.get("summary")})
        elif status == "warning":
            warnings.append({"item": key, "summary": it.get("summary")})
    return {"ok": not issues and not blockers, "issues": issues, "blockers": blockers, "warnings": warnings}
