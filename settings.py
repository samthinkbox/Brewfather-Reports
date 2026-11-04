"""Site settings, stored in data/settings.json and edited on the Admin page."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "data", "settings.json")

DEFAULTS = {
    "site_title": "Brewfather Reports",
    "water_pattern": "",        # regex matched against Brewfather water source names; empty = every source
    "water_label": "",          # name used in the Water section, e.g. "Town supply"; empty = "tap"
    "auto_refresh_hours": 0,    # 0 = off; otherwise fetch from Brewfather this often (minimum 1)
    "public_reports": False,    # True = report pages can be viewed without signing in
    "next_brew": False,         # build the Next Brew page (needs next_brew.py set up for your recipes)
}


def load():
    s = dict(DEFAULTS)
    if os.path.exists(PATH):
        try:
            with open(PATH, encoding="utf-8") as f:
                s.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    return s


def save(changes):
    s = load()
    for k, v in changes.items():
        if k not in DEFAULTS:
            continue
        d = DEFAULTS[k]
        if isinstance(d, bool):
            v = bool(v)
        elif isinstance(d, int):
            v = max(0, min(168, int(v or 0)))
        else:
            v = str(v or "").strip()[:200]
        s[k] = v
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    return s
