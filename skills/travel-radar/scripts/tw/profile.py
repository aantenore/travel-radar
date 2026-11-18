"""Profile updates: the single write path, validated and audited."""
from datetime import date

import yaml

from . import schema

HEADER = ("# Travel preference profile. Updated only through `tw profile set`.\n"
          "# null = not known yet (scoring falls back to scoring.profile_defaults).\n")


def set_value(prof, key, value, evidence, reason=None, today=None):
    if not evidence or not evidence.strip():
        raise schema.ValidationError(["evidence is required: quote the user's own words"])
    schema.profile_value(key, value)
    section, _, field = key.partition(".")
    if field:
        prof.setdefault(section, {})
        old = prof[section].get(field)
        prof[section][field] = value
    else:
        old = prof.get(section)
        prof[section] = value
    prof.setdefault("learned", []).append({
        "date": (today or date.today()).isoformat(), "rule": f"{key} = {value!r}",
        "previous": old, "reason": reason, "evidence": evidence.strip(),
    })
    return {"key": key, "old": old, "new": value}


def save(path, prof):
    path.write_text(HEADER + yaml.safe_dump(prof, sort_keys=False, allow_unicode=True), encoding="utf-8")
