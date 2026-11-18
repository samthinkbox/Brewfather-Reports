"""Brewing maths and Brewfather recipe helpers shared by next_brew.py and recipe_import.py.

Gravity, colour (Morey), bitterness (Tinseth) and the water/salt plan. Standard library only.
"""
import copy
import math
import re

# Water targets, the same as the Water section of report.html.
WATER_TARGETS = {
    "IPA": {"title": "West Coast IPA", "Mg": 20, "Cl": 50, "SO4": 275},
    "NEIPA": {"title": "NEIPA / Hazy", "Mg": 12, "Cl": 175, "SO4": 100},
    "Pale": {"title": "Pale ale / XPA", "Mg": 12, "Cl": 70, "SO4": 175},
}

WHIRLPOOL_FACTOR = 0.21  # Brewfather's 20 min at 80 C hop stand, relative to a boil addition

STRIP = re.compile(r"^_(rev|created|timestamp|timestamp_ms|version)$")


def clean(obj):
    """Drop Brewfather bookkeeping fields from an ingredient object."""
    return {k: v for k, v in copy.deepcopy(obj).items() if not STRIP.match(k) and k not in ("inventory", "usedIn")}


def library(raw, template):
    """Latest copy of every ingredient you've used, by exact name."""
    lib = {"fermentables": {}, "hops": {}, "yeasts": {}, "miscs": {}}
    for b in sorted(raw, key=lambda b: b.get("brewDate") or 0):
        r = b.get("recipe") or {}
        for kind in lib:
            for item in r.get(kind) or []:
                if item.get("name"):
                    lib[kind][item["name"]] = item
    for kind in lib:
        for item in (template or {}).get(kind) or []:
            if item.get("name"):
                lib[kind].setdefault(item["name"], item)
    return lib


def gravity_points(malts, size, eff):
    """malts: [(fermentable object, kg)]. Gravity points (OG - 1) x 1000 at this size and efficiency."""
    return sum(kg * ((f.get("potential") or 1.036) - 1) * 1000 * 8.3454 for f, kg in malts) * eff / 100 / size


def srm(malts, size):
    mcu = sum(kg * 2.20462 * (f.get("color") or 2) for f, kg in malts) / (size * 0.264172)
    return 1.4922 * mcu ** 0.6859 if mcu > 0 else 0.0


def tinseth(alpha, grams, minutes, og, size, factor=1.0):
    bigness = 1.65 * 0.000125 ** (og - 1)
    time = (1 - math.exp(-0.04 * minutes)) / 4.15
    return bigness * time * alpha / 100 * grams * 1000 / size * factor


def water_plan(src, kg, equipment, target, lactic_ml, ph):
    """Salts for the whole brew to reach target Mg, Cl and SO4 from the source water, split mash/sparge."""
    src = {k: (src or {}).get(k) or 0 for k in ("Ca", "Mg", "Na", "Cl", "SO4", "HCO3")}
    boil = equipment.get("boilSize") or 28.44
    mash = kg * (equipment.get("waterGrainRatio") or 2.4) + (equipment.get("mashTunDeadSpace") or 3.5)
    total = boil + kg * (equipment.get("grainAbsorptionRate") or 0.8)
    sparge = total - mash
    epsom = max(0, target["Mg"] - src["Mg"]) / 98.6
    cacl = max(0, target["Cl"] - src["Cl"]) / 482.3
    gyp = max(0, target["SO4"] - src["SO4"] - epsom * 389.6) / 557.9
    grams = {k: round(v * total * 2) / 2 for k, v in (("Gypsum", gyp), ("CaCl2", cacl), ("Epsom", epsom))}
    res = dict(src)
    res["Ca"] += (grams["Gypsum"] * 232.8 + grams["CaCl2"] * 272.6) / total
    res["Mg"] += grams["Epsom"] * 98.6 / total
    res["SO4"] += (grams["Gypsum"] * 557.9 + grams["Epsom"] * 389.6) / total
    res["Cl"] += grams["CaCl2"] * 482.3 / total
    split = {k: (round(v * mash / total * 2) / 2) for k, v in grams.items()}
    return {
        "mashL": round(mash, 2), "spargeL": round(sparge, 2), "totalL": round(total, 2),
        "grams": grams, "mash": split, "sparge": {k: round(grams[k] - split[k], 2) for k in grams},
        "lacticMl": lactic_ml, "ph": ph,
        "result": {k: round(v, 1) for k, v in res.items()},
    }


ION_NAMES = {"calcium": "Ca", "magnesium": "Mg", "sodium": "Na", "chloride": "Cl", "sulfate": "SO4", "bicarbonate": "HCO3"}


def water_block(template_water, src_water, wp, target_name, description):
    """Brewfather's recipe.water for a salt plan from water_plan(), built on the template's water block."""
    w = copy.deepcopy(template_water or {})
    res = wp["result"]

    def prof(p, name, typ):
        cl = p.get("Cl") or 0
        return {**{k: round(p.get(v) or 0, 1) for k, v in ION_NAMES.items()}, "name": name, "type": typ,
                "soClRatio": round((p.get("SO4") or 0) / cl, 2) if cl else None}
    w["source"] = copy.deepcopy(src_water)
    w["target"] = prof({**res, "HCO3": 0}, target_name, "target")
    w["target"]["description"] = description
    w["total"] = {**copy.deepcopy(src_water), **prof(res, (src_water or {}).get("name"), "source")}
    w["mash"] = copy.deepcopy(w["total"])
    w["sparge"] = copy.deepcopy(w["total"])
    for key, vol, gr in (("mashAdjustments", wp["mashL"], wp["mash"]), ("spargeAdjustments", wp["spargeL"], wp["sparge"]),
                         ("totalAdjustments", wp["totalL"], wp["grams"])):
        adj = w.get(key) or {}
        adj.update({"volume": vol, "calciumSulfate": gr["Gypsum"], "calciumChloride": gr["CaCl2"], "magnesiumSulfate": gr["Epsom"]})
        if key == "mashAdjustments" and wp.get("lacticMl"):
            adj["acids"] = [{"type": "lactic", "concentration": 80, "amount": wp["lacticMl"]}]
        w[key] = adj
    if wp.get("ph"):
        w["mashPh"] = wp["ph"]
    for salt in ("calciumSulfate", "calciumChloride", "magnesiumSulfate"):
        if isinstance((w.get("settings") or {}).get(salt), dict):
            w["settings"][salt]["auto"] = False
    return w
