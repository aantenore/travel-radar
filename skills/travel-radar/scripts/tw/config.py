"""Configuration and data-home resolution.

Plugin files (code, default config, checklist) are read-only and may be replaced
on update. All user data lives in the data home: $TRAVEL_RADAR_HOME or ~/.travel-radar.
"""
import copy
import os
import shutil
from pathlib import Path

import yaml

PLUGIN_CONFIG = Path(__file__).resolve().parent.parent.parent / "config"
DEFAULTS_FILE = PLUGIN_CONFIG / "defaults.yaml"
PROFILE_TEMPLATE = PLUGIN_CONFIG / "profile.template.yaml"


def home():
    return Path(os.environ.get("TRAVEL_RADAR_HOME", Path.home() / ".travel-radar")).expanduser()


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# Keys that used to hold domain lists. Tiers are decided at runtime only, so these are ignored.
DEPRECATED_RELIABILITY_KEYS = ("tier1", "tier2", "tier1_extra", "tier2_extra", "fallback")


def settings():
    """Plugin defaults, deep-merged with the optional user override <home>/settings.yaml."""
    cfg = load_yaml(DEFAULTS_FILE)
    user = home() / "settings.yaml"
    if not user.exists():
        return cfg
    over = load_yaml(user)
    rel = over.get("reliability") or {}
    dropped = [k for k in DEPRECATED_RELIABILITY_KEYS if k in rel]
    if dropped:
        over = {**over, "reliability": {k: v for k, v in rel.items() if k not in dropped}}
    cfg = _merge(cfg, over)
    if dropped:
        cfg["_deprecations"] = [f"{user}: reliability.{', reliability.'.join(dropped)} ignored (deprecated): "
                                "source tiers are verified at runtime; remove these lists"]
    return cfg


def path(cfg, key):
    """Data paths resolve against the data home; the checklist falls back to the plugin copy."""
    p = Path(cfg["paths"][key]).expanduser()
    if p.is_absolute():
        return p
    if key == "dossier_checklist" and not (home() / p).exists():
        return PLUGIN_CONFIG / p.name
    return home() / p


def init(cfg):
    """Create the data home on first use. Idempotent; never overwrites user files."""
    created = []
    for key in ("trips_dir", "watches_dir", "cache_dir"):
        d = path(cfg, key)
        if not d.exists():
            d.mkdir(parents=True)
            created.append(str(d))
    prof = path(cfg, "profile")
    if not prof.exists():
        prof.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROFILE_TEMPLATE, prof)
        created.append(str(prof))
    return {"home": str(home()), "created": created}


def profile(cfg):
    p = path(cfg, "profile")
    return load_yaml(p) if p.exists() else {}
