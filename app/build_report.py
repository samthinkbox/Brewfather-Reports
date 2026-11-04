"""Turn data/batches.json into report.html plus CSV files in output/.

Run fetch.py first (or use run.py, which does both).
"""
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hop_flavour  # noqa: E402
import paths  # noqa: E402
import layout  # noqa: E402
import settings  # noqa: E402
DATA = paths.BATCHES
OUT = paths.OUTPUT
TEMPLATE = paths.template("report.html")

SRM_TO_EBC = 1.97

EXCLUDE_FILE = paths.config("exclude.txt")
NOREPORT = re.compile(r"#\s*no-?report", re.I)


def load_excludes(path=EXCLUDE_FILE):
    """Batch numbers listed in exclude.txt (one per line, # for comments)."""
    out = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*(\d+)", line)
                if m and not line.lstrip().startswith("#"):
                    out.add(int(m.group(1)))
    return out


def flagged_in_brewfather(b):
    """True when the batch name or any batch note contains #noreport."""
    if NOREPORT.search(b.get("name") or ""):
        return True
    notes = b.get("notes") or []
    if isinstance(notes, str):
        return bool(NOREPORT.search(notes))
    return any(NOREPORT.search((n or {}).get("note") or "") for n in notes if isinstance(n, dict))

# ---------------------------------------------------------------- hops
HOP_FORM_WORDS = r"\b(cryo|lupulin|lupumax|lupomax|incognito|oil|extract|co2|pellets?|t-?90)\b"
HOP_ALIASES = {
    "nelson": "Nelson Sauvin",
    "columbus": "Columbus",
    "ctz": "Columbus",
    "tomahawk": "Columbus",
    "idaho #7": "Idaho 7",
    "idaho7": "Idaho 7",
}


def hop_variety(name):
    n = re.sub(r"\(.*?\)", "", name or "")
    n = re.sub(HOP_FORM_WORDS, "", n, flags=re.I)
    n = re.sub(r"\s+", " ", n).strip(" -")
    key = n.lower()
    return HOP_ALIASES.get(key, n) or (name or "Unknown").strip()


def hop_form(h):
    t = (h.get("type") or "").lower()
    n = (h.get("name") or "").lower()
    if "co2" in t or "extract" in t or re.search(r"\b(oil|extract)\b", n):
        return "Extract / oil"
    if "cryo" in t or re.search(r"cryo|lupulin|lupumax|lupomax", n):
        return "Cryo / lupulin"
    return "Pellet"


def hop_stage(h):
    use = h.get("use") or ""
    t = h.get("time") or 0
    if use == "Dry Hop":
        return "Dry hop"
    if use == "First Wort":
        return "Bittering"
    if use == "Aroma":
        return "Whirlpool"
    if use == "Boil":
        if t >= 30:
            return "Bittering"
        if t > 0:
            return "Late boil"
        return "Whirlpool"
    return use or "Other"


# ---------------------------------------------------------------- malts
def malt_group(f):
    cat = f.get("grainCategory") or ""
    typ = f.get("type") or ""
    name = (f.get("name") or "").lower()
    if typ == "Sugar":
        return "Sugar / adjunct"
    if typ in ("Dry Extract", "Liquid Extract", "Extract"):
        return "Extract"
    if cat.startswith("Base"):
        return "Base"
    if cat == "Crystal/Caramel" or re.search(r"crystal|caramel|cara", name):
        return "Crystal"
    if cat == "Acidulated" or "acid" in name:
        return "Acidulated"
    if cat == "Roasted" or re.search(r"roast|chocolate|black", name):
        return "Roasted"
    if re.search(r"oat|wheat|rye|flake|dextrin|carapils", name):
        return "Oats / wheat / dextrin"
    if typ == "Grain" and re.search(r"pale|pils|ale malt|lager|maris|munich|vienna", name):
        return "Base"
    if typ == "Other":
        return "Sugar / adjunct"
    return "Other specialty"


def base_detail(f):
    cat = f.get("grainCategory") or ""
    m = re.match(r"Base \((.*)\)", cat)
    return m.group(1) if m else None


# ---------------------------------------------------------------- yeast
def yeast_name(y):
    name = (y.get("name") or "").strip()
    pid = (y.get("productId") or "").strip().upper()
    lab = (y.get("laboratory") or "").strip()
    low = name.lower()
    if pid == "US-05" or re.search(r"us-?05|safale 05", low):
        return "Fermentis US-05"
    if pid == "S-04" or "s-04" in low:
        return "Fermentis S-04"
    if "verdant" in low:
        return "Lallemand Verdant IPA"
    if "voss" in low:
        return "Lallemand Voss Kveik"
    if pid == "BRY-97" or "west coast ale" in low and "lallemand" in lab.lower():
        return "Lallemand BRY-97"
    if pid:
        short_lab = lab.split("(")[0].replace("/", " ").split()[0] if lab else ""
        return f"{short_lab} {pid} ({name})".strip()
    return name or "Unknown"


NOT_YEAST = re.compile(r"\baldc\b", re.I)


# ---------------------------------------------------------------- helpers
def num(v):
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def nz(v):
    v = num(v)
    return v if v else None


def r1(v, d=1):
    return None if v is None else round(v, d)


def iso(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def steps_text(steps, unit):
    out = []
    for s in steps or []:
        t, d = num(s.get("stepTemp")), num(s.get("stepTime"))
        if t is None:
            continue
        label = f"{t:g}°C"
        if d:
            label += f" {d:g}{unit}"
        out.append(label)
    return ", ".join(out)


def water_profile(recipe):
    w = (recipe.get("water") or {}).get("total") or (recipe.get("water") or {}).get("mash") or {}
    if not w:
        return None
    keys = {"Ca": "calcium", "Mg": "magnesium", "Na": "sodium", "Cl": "chloride", "SO4": "sulfate", "HCO3": "bicarbonate"}
    prof = {k: r1(num(w.get(v)), 0) for k, v in keys.items()}
    prof["SO4:Cl"] = r1(num(w.get("soClRatio")), 2)
    prof["source"] = w.get("name")
    return prof


ION_KEYS = {"Ca": "calcium", "Mg": "magnesium", "Na": "sodium", "Cl": "chloride", "SO4": "sulfate", "HCO3": "bicarbonate"}
SALTS = [  # (label, regex on the misc name, unit)
    ("Gypsum", r"gypsum|caso4|calcium sul", "g"),
    ("CaCl2", r"calcium chlor|cacl", "g"),
    ("Epsom", r"epsom|mgso4|magnesium sul", "g"),
    ("NaCl", r"\bnacl\b|table salt|sodium chlor|non.?iodi", "g"),
    ("Baking soda", r"baking soda|bicarb|nahco3", "g"),
    ("Chalk", r"chalk|caco3|calcium carb", "g"),
    ("Lactic", r"lactic", "ml"),
    ("Phosphoric", r"phosph", "ml"),
]


def ion_profile(p):
    if not p:
        return None
    out = {k: r1(num(p.get(v)), 1) for k, v in ION_KEYS.items()}
    return out if any(v is not None for v in out.values()) else None


def water_detail(b, r):
    """Source water, salts and acid added, final profile and mash pH for one brew."""
    w = r.get("water") or {}
    if not w:
        return None
    salts = {}
    for m in r.get("miscs") or []:
        name = (m.get("name") or "").lower()
        if (m.get("type") or "") != "Water Agent" and not m.get("waterAdjustment"):
            continue
        amt = num(m.get("amount")) or 0
        for label, rx, _unit in SALTS:
            if re.search(rx, name):
                salts[label] = r1((salts.get(label) or 0) + amt, 2)
                break
    acid_malt = sum(num(f.get("amount")) or 0 for f in r.get("fermentables") or []
                    if (f.get("grainCategory") or "") == "Acidulated" or re.search(r"acid|sour grapes", (f.get("name") or ""), re.I))
    src = w.get("source") or {}
    vol = num((w.get("totalAdjustments") or {}).get("volume")) or num((r.get("data") or {}).get("totalWaterAmount"))
    return {
        "source": src.get("name"),
        "src": ion_profile(src),
        "final": ion_profile(w.get("total") or w.get("mash")),
        "ratio": r1(num((w.get("total") or w.get("mash") or {}).get("soClRatio")), 2),
        "target": (w.get("target") or {}).get("name"),
        "salts": salts,
        "acidMaltKg": r1(acid_malt, 2) or None,
        "volume": r1(vol),
        "phPlan": r1(nz(w.get("mashPh")), 2),
        "phMeas": r1(nz(b.get("measuredMashPh")), 2),
    }


def water_sources(raw):
    """Distinct source-water profiles with how often and when they were used."""
    seen = {}
    for b in raw:
        src = ((b.get("recipe") or {}).get("water") or {}).get("source") or {}
        name = src.get("name")
        prof = ion_profile(src)
        if not name or not prof:
            continue
        o = seen.setdefault(name, {"name": name, **prof, "alk": r1(num(src.get("alkalinity")), 0),
                                   "brews": 0, "first": None, "last": None})
        o["brews"] += 1
        d = iso(b.get("brewDate"))
        if d and (not o["first"] or d < o["first"]):
            o["first"] = d
        if d and (not o["last"] or d > o["last"]):
            o["last"] = d
    return sorted(seen.values(), key=lambda o: o["last"] or "", reverse=True)


def user_notes(b):
    seen, out = set(), []
    for n in b.get("notes") or []:
        if not isinstance(n, dict) or n.get("hidden"):
            continue
        text = (n.get("note") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append({"date": iso(n.get("timestamp")), "status": n.get("status"), "text": text})
    out.sort(key=lambda n: n["date"] or "")
    return out


def mash_temp(recipe):
    steps = ((recipe.get("mash") or {}).get("steps")) or []
    temps = [num(s.get("stepTemp")) for s in steps if num(s.get("stepTemp"))]
    return temps[0] if temps else None


# ---------------------------------------------------------------- build
def normalise(raw):
    brews = []
    excluded_list = load_excludes()
    skipped = []
    hopdb = hop_flavour.load_db()
    for b in raw:
        if b.get("batchNo") in excluded_list:
            skipped.append(f"#{b.get('batchNo')} {b.get('name')} (exclude.txt)")
            continue
        if flagged_in_brewfather(b):
            skipped.append(f"#{b.get('batchNo')} {b.get('name')} (#noreport in Brewfather)")
            continue
        r = b.get("recipe") or {}
        size = nz(r.get("batchSize")) or nz(b.get("measuredBatchSize")) or 20
        style = r.get("style") or {}

        ferms = []
        total_kg = sum(num(f.get("amount")) or 0 for f in r.get("fermentables") or [])
        for f in r.get("fermentables") or []:
            kg = num(f.get("amount")) or 0
            ferms.append({
                "name": (f.get("name") or "Unknown").strip(),
                "supplier": (f.get("supplier") or "").strip(),
                "kg": r1(kg, 3),
                "pct": r1(kg / total_kg * 100 if total_kg else 0),
                "group": malt_group(f),
                "base": base_detail(f),
                "ebc": r1((num(f.get("color")) or 0) * SRM_TO_EBC),
            })
        ferms.sort(key=lambda f: -f["kg"])

        hops = []
        for h in r.get("hops") or []:
            g = num(h.get("amount")) or 0
            hops.append({
                "name": (h.get("name") or "Unknown").strip(),
                "variety": hop_variety(h.get("name")),
                "form": hop_form(h),
                "stage": hop_stage(h),
                "use": h.get("use") or "",
                "g": r1(g),
                "gpl": r1(g / size, 2),
                "alpha": num(h.get("alpha")),
                "time": num(h.get("time")),
                "day": num(h.get("day")),
                "temp": num(h.get("temp")),
                "ibu": r1(num(h.get("ibu"))),
            })

        yeasts = [yeast_name(y) for y in r.get("yeasts") or [] if not NOT_YEAST.search(y.get("name") or "")]

        planned_att = num(r.get("attenuation"))
        if planned_att is not None and planned_att <= 2:  # stored as a fraction (1.2 = 120%)
            planned_att *= 100

        brews.append({
            "id": b.get("_id"),
            "no": b.get("batchNo"),
            "name": (b.get("name") or "").strip(),
            "recipe": (r.get("name") or "").lstrip("*").strip(),
            "date": iso(b.get("brewDate")),
            "status": b.get("status"),
            "brewer": b.get("brewer"),
            "style": style.get("name") or "Unspecified",
            "rating": b.get("tasteRating"),
            "size": r1(size),
            "sizeActual": nz(b.get("measuredBatchSize")),
            "boil": num(r.get("boilTime")),
            "mashTemp": mash_temp(r),
            "ogPlan": r1(nz(r.get("og")), 3),
            "fgPlan": r1(nz(r.get("fg")), 3),
            "abvPlan": r1(nz(r.get("abv"))),
            "og": r1(nz(b.get("measuredOg")), 3),
            "fg": r1(nz(b.get("measuredFg")), 3),
            "abv": r1(nz(b.get("measuredAbv"))),
            "ibu": r1(nz(r.get("ibu")) or nz(b.get("estimatedIbu"))),
            "ebc": r1((nz(r.get("color")) or nz(b.get("estimatedColor")) or 0) * SRM_TO_EBC),
            "effPlan": r1(nz(r.get("efficiency"))),
            "eff": r1(nz(b.get("measuredEfficiency"))),
            "attPlan": r1(planned_att),
            "att": r1(nz(b.get("measuredAttenuation"))),
            "grainKg": r1(total_kg, 2),
            "ferms": ferms,
            "hops": hops,
            "flavour": hop_flavour.profile(hops, hopdb),
            "yeasts": yeasts,
            "mashSteps": steps_text((r.get("mash") or {}).get("steps"), " min"),
            "fermSteps": steps_text((r.get("fermentation") or {}).get("steps"), "d"),
            "fermTemp": num(r.get("primaryTemp")),
            "water": water_profile(r),
            "wd": water_detail(b, r),
            "miscs": [{
                "name": (m.get("name") or "").strip(),
                "amount": num(m.get("amount")),
                "unit": m.get("unit") or "",
                "use": m.get("use") or "",
                "type": m.get("type") or "",
            } for m in r.get("miscs") or []],
            "notes": user_notes(b),
            "recipeNotes": (r.get("notes") or "").strip(),
        })
    brews.sort(key=lambda x: (x["date"] or "", x["no"] or 0))
    if skipped:
        print("Left out: " + "; ".join(skipped))
    return brews


def write_csvs(brews):
    os.makedirs(OUT, exist_ok=True)
    cols = ["no", "date", "name", "style", "status", "size", "og", "fg", "abv", "ogPlan", "fgPlan",
            "abvPlan", "ibu", "ebc", "eff", "effPlan", "att", "attPlan", "mashTemp", "boil", "rating"]
    with open(os.path.join(OUT, "brews.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols + ["yeast", "hop_varieties", "total_hops_g", "dry_hop_g_per_l"])
        for b in brews:
            dh = sum(h["g"] for h in b["hops"] if h["stage"] == "Dry hop")
            w.writerow([b[c] for c in cols] + [
                "; ".join(b["yeasts"]),
                ", ".join(sorted({h["variety"] for h in b["hops"]})),
                r1(sum(h["g"] for h in b["hops"])),
                r1(dh / b["size"], 2) if b["size"] else None,
            ])
    with open(os.path.join(OUT, "hops.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["no", "date", "brew", "hop", "variety", "form", "stage", "grams", "g_per_l",
                    "alpha", "boil_or_stand_min", "dry_hop_day", "temp_c", "ibu"])
        for b in brews:
            for h in b["hops"]:
                w.writerow([b["no"], b["date"], b["name"], h["name"], h["variety"], h["form"], h["stage"],
                            h["g"], h["gpl"], h["alpha"], h["time"] if h["stage"] != "Dry hop" else None,
                            h["day"] if h["stage"] == "Dry hop" else None, h["temp"], h["ibu"]])
    with open(os.path.join(OUT, "malts.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["no", "date", "brew", "fermentable", "supplier", "group", "kg", "percent", "ebc"])
        for b in brews:
            for m in b["ferms"]:
                w.writerow([b["no"], b["date"], b["name"], m["name"], m["supplier"], m["group"],
                            m["kg"], m["pct"], m["ebc"]])


def write_html(brews, sources=None):
    cfg = settings.load()
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    payload = json.dumps({
        "generated": datetime.now().strftime("%d %b %Y %H:%M"),
        "brews": brews,
        "sources": sources or [],
        "water": {"pattern": cfg.get("water_pattern") or "", "label": cfg.get("water_label") or ""},
    }, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/null", payload).replace("/*__HOP_RADAR__*/", hop_flavour.radar_js())
    html = layout.apply(html, "report.html")
    path = os.path.join(OUT, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


FAMILIES_FILE = paths.config("families.txt")
VERSIONS_TEMPLATE = paths.template("versions.html")


def load_families(path=FAMILIES_FILE):
    fams = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                name, nums = line.rsplit(":", 1)
                nos = [int(n) for n in re.findall(r"\d+", nums)]
                if nos:
                    fams.append({"name": name.strip(), "nos": nos})
    return fams


def write_versions(brews):
    by_no = {b["no"]: b for b in brews}
    families, used = [], set()
    for fam in load_families():
        found = [n for n in fam["nos"] if n in by_no]
        missing = [n for n in fam["nos"] if n not in by_no]
        if missing:
            print(f"families.txt: {fam['name']} - batch {', '.join(map(str, missing))} not found or excluded")
        if len(found) >= 2:
            found.sort(key=lambda n: (by_no[n]["date"] or "", n))
            families.append({"name": fam["name"], "nos": found})
            used.update(found)
    with open(VERSIONS_TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    payload = json.dumps({
        "generated": datetime.now().strftime("%d %b %Y %H:%M"),
        "families": families,
        "brews": [b for b in brews if b["no"] in used],
    }, separators=(",", ":")).replace("</", "<\\/")
    path = os.path.join(OUT, "versions.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(layout.apply(tpl.replace("/*__DATA__*/null", payload), "versions.html"))
    print(f"{len(families)} recipe families -> {path}")


def main():
    with open(DATA, encoding="utf-8") as f:
        raw = json.load(f)
    brews = normalise(raw)
    write_csvs(brews)
    write_versions(brews)
    path = write_html(brews, water_sources(raw))
    layout.write_static_pages()
    v = Counter(h["variety"] for b in brews for h in b["hops"])
    print(f"{len(brews)} brews, {len(v)} hop varieties -> {path}")


if __name__ == "__main__":
    main()
