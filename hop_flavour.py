"""Hop flavour profiles from the HopDatabase project (HopDatabase/website/public/data/hops.json).

Each hop there has nine aroma scores from 0 to 5 (Citrus, Resin/Pine, Spice, Herbal, Grassy,
Floral, Berry, Stone Fruit, Tropical Fruit), averaged across the suppliers that publish them
(Yakima Chief, Barth Haas, Hopsteiner, Crosby). A brew's profile is the average of its hops'
scores weighted by grams, counting only the flavour and aroma additions:
    whirlpool and dry hop x1, late boil (under 30 min) x0.5, bittering left out.

Hops that are missing from HopDatabase, or have no aroma scores there, can be added or
overridden in hop_flavours_extra.json next to this file.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
HOPDB = os.path.join(HERE, "hopdata", "hops.json")  # copy of HopDatabase's website/public/data/hops.json
if not os.path.exists(HOPDB):
    HOPDB = os.path.join(HERE, "HopDatabase", "website", "public", "data", "hops.json")
HOPDB_URL = "https://raw.githubusercontent.com/kasperg3/HopDatabase/main/website/public/data/hops.json"
EXTRA = os.path.join(HERE, "hop_flavours_extra.json")

AROMAS = ["Citrus", "Resin/Pine", "Spice", "Herbal", "Grassy", "Floral", "Berry", "Stone Fruit", "Tropical Fruit"]
STAGE_WEIGHT = {"Whirlpool": 1.0, "Dry hop": 1.0, "Late boil": 0.5}


def _key(name):
    n = re.sub(r"\(.*?\)|[®™]", "", name or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def load_db():
    """{key: {name, aromas, notes, source, estimate}} for every hop with aroma scores."""
    db, aliases = {}, {}
    if os.path.exists(HOPDB):
        with open(HOPDB, encoding="utf-8") as f:
            for h in json.load(f):
                a = {c: float(h.get("aromas", {}).get(c) or 0) for c in AROMAS}
                if sum(a.values()) > 0:
                    db[_key(h["name"])] = {"name": h["name"], "aromas": a, "notes": h.get("notes") or [],
                                           "source": h.get("source") or "HopDatabase", "estimate": False}
    if os.path.exists(EXTRA):
        with open(EXTRA, encoding="utf-8") as f:
            extra = json.load(f)
        for name, a in (extra.get("hops") or {}).items():
            db[_key(name)] = {"name": name, "aromas": {c: float(a.get(c) or 0) for c in AROMAS},
                              "notes": a.get("notes") or [], "source": "hop_flavours_extra.json", "estimate": True}
        aliases = {_key(k): _key(v) for k, v in (extra.get("aliases") or {}).items()}
    return {"hops": db, "aliases": aliases}


def lookup(db, variety):
    k = _key(variety)
    k = db["aliases"].get(k, k)
    return db["hops"].get(k)


def profile(hops, db):
    """hops: dicts with variety, stage and g (as in build_report). Returns the weighted profile or None."""
    weights, missing = {}, {}
    for h in hops:
        w = STAGE_WEIGHT.get(h.get("stage"))
        if not w or not h.get("g"):
            continue
        if lookup(db, h["variety"]):
            weights[h["variety"]] = weights.get(h["variety"], 0) + h["g"] * w
        else:
            missing[h["variety"]] = missing.get(h["variety"], 0) + h["g"] * w
    total = sum(weights.values())
    if not total:
        return None
    aromas, contrib = {}, {}
    for c in AROMAS:
        parts = [(v, w * lookup(db, v)["aromas"][c] / total) for v, w in weights.items()]
        aromas[c] = round(sum(p for _, p in parts), 2)
        contrib[c] = [[v, round(p, 2)] for v, p in sorted(parts, key=lambda x: -x[1]) if p > 0][:3]
    grams = {}
    for h in hops:
        if h.get("stage") in STAGE_WEIGHT:
            grams[h["variety"]] = grams.get(h["variety"], 0) + (h.get("g") or 0)
    hop_list = [{"variety": v, "g": round(g), "share": round(weights.get(v, 0) / total * 100),
                 "estimate": bool((lookup(db, v) or {}).get("estimate")), "known": v in weights}
                for v, g in sorted(grams.items(), key=lambda x: -x[1])]
    covered = total / (total + sum(missing.values())) * 100
    return {"aromas": aromas, "contrib": contrib, "hops": hop_list, "covered": round(covered),
            "missing": sorted(missing), "top": sorted(AROMAS, key=lambda c: -aromas[c])[:3]}


def update_from_github():
    """Download the latest hops.json from HopDatabase on GitHub into hopdata/hops.json."""
    import urllib.request
    req = urllib.request.Request(HOPDB_URL, headers={"User-Agent": "BrewfatherReports"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not isinstance(data, list) or len(data) < 50 or not all(isinstance(h, dict) and "name" in h and "aromas" in h for h in data):
        raise ValueError("The downloaded file doesn't look like HopDatabase hop data; nothing was changed.")
    path = os.path.join(HERE, "hopdata", "hops.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    print(f"Saved {len(data)} hops from HopDatabase to {path}")
    return len(data)


def info():
    path = HOPDB if os.path.exists(HOPDB) else None
    db = load_db()
    return {"hops": len(db["hops"]), "file": os.path.relpath(path, HERE) if path else None,
            "updated": os.path.getmtime(path) if path else None}


def radar_js():
    with open(os.path.join(HERE, "hop_radar.js"), encoding="utf-8") as f:
        return f.read()
