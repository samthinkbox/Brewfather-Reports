"""Import a pasted recipe: match its ingredients to yours, ask about anything unclear, scale it to your
batch size and efficiency, and build a Brewfather recipe on your template recipe.

Used by the Import recipe page (server.py: /api/import/*). The steps:

    ctx = context(batches)                  # your template recipe, ingredient library and hop data
    draft = recipe_parse.parse(text, hop_names(ctx))  (or ai_parse.read(...))
    draft = auto_match(draft, ctx)          # fills each ingredient's "match" when it's clear
    questions(draft, ctx)                   # what still needs an answer (each has a recommended first option)
    view, recipe = build(draft, opts, ctx)  # scaled recipe for review, and the Brewfather recipe object
    upload(recipe, opts)                    # POST to Brewfather (writes)

An ingredient's "match" is the exact name of one of your ingredients, or "new" to keep the recipe's own.
Standard library only.
"""
import copy
import json
import math
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brew_calc as calc  # noqa: E402
import build_report as br  # noqa: E402
import hop_flavour  # noqa: E402
import paths  # noqa: E402
import recipe_parse  # noqa: E402
import settings  # noqa: E402

TEMPLATE_FILE = os.path.join(paths.DATA, "import_template.json")
IMPORTED = os.path.join(paths.DATA, "imported.json")
DEFAULT_EFFICIENCY = 72  # what magazines usually assume when they don't say

# ------------------------------------------------------------------ context
_ctx = {"key": None, "value": None}


def load_template(raw):
    """(recipe, label): the recipe chosen on the Admin page, or the recipe of your latest brew."""
    tid = settings.load().get("import_template_id")
    if tid and os.path.exists(TEMPLATE_FILE):
        try:
            with open(TEMPLATE_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("id") == tid and isinstance(saved.get("recipe"), dict):
                return saved["recipe"], f"your recipe “{saved['recipe'].get('name')}”"
        except (OSError, ValueError):
            pass
    for b in sorted(raw, key=lambda b: b.get("brewDate") or 0, reverse=True):
        r = b.get("recipe") or {}
        if r.get("fermentables") and r.get("equipment"):
            return r, f"your latest brew, #{b.get('batchNo')} {(b.get('name') or r.get('name') or '').strip()}"
    return None, None


def save_template(recipe):
    os.makedirs(paths.DATA, exist_ok=True)
    with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"id": recipe.get("_id"), "saved": datetime.now().isoformat(timespec="seconds"), "recipe": recipe}, f)


def context(raw):
    key = (id(raw), len(raw), settings.load().get("import_template_id"), os.path.getmtime(TEMPLATE_FILE) if os.path.exists(TEMPLATE_FILE) else 0)
    if _ctx["key"] == key:
        return _ctx["value"]
    template, label = load_template(raw)
    lib = calc.library(raw, template or {})
    db = hop_flavour.load_db()
    alphas = {}
    if os.path.exists(hop_flavour.HOPDB):
        with open(hop_flavour.HOPDB, encoding="utf-8") as f:
            for h in json.load(f):
                a = (h.get("brewing_stats") or {}).get("avg_alpha")
                if a:
                    alphas[hop_flavour._key(h["name"])] = a
    uses = {"fermentables": {}, "hops": {}, "yeasts": {}}
    bitter = {}
    for b in raw:
        r = b.get("recipe") or {}
        for kind in uses:
            for name in {i.get("name") for i in r.get(kind) or [] if i.get("name")}:
                uses[kind][name] = uses[kind].get(name, 0) + 1
        for h in r.get("hops") or []:
            if h.get("name") in lib["hops"] and h.get("use") in ("Boil", "First Wort") and (h.get("time") or 0) >= 30:
                bitter[h["name"]] = bitter.get(h["name"], 0) + 1
    ctx = {"template": template, "label": label, "lib": lib, "db": db, "alphas": alphas, "uses": uses,
           "bittering": [n for n, _ in sorted(bitter.items(), key=lambda x: -x[1])][:3]}
    _ctx.update(key=key, value=ctx)
    return ctx


def hop_names(ctx):
    names = [h["name"] for h in ctx["db"]["hops"].values()] + list(ctx["db"]["aliases"]) + list(br.HOP_ALIASES)
    names += [br.hop_variety(n) for n in ctx["lib"]["hops"]]
    return names


def lib_names(ctx):
    return {k: sorted(ctx["lib"][k]) for k in ("fermentables", "hops", "yeasts")}


def standards(ctx):
    """What will be applied from your template, for the page."""
    t = ctx["template"] or {}
    eq = t.get("equipment") or {}
    y = (t.get("yeasts") or [{}])[0]
    return {
        "label": ctx["label"], "batch": t.get("batchSize") or eq.get("batchSize"), "eff": t.get("efficiency") or eq.get("efficiency"),
        "boil": t.get("boilTime") or eq.get("boilTime"), "equipment": eq.get("name"),
        "mash": [[s.get("stepTemp"), s.get("stepTime")] for s in (t.get("mash") or {}).get("steps") or []],
        "ferment": [[s.get("stepTemp"), s.get("stepTime"), s.get("type")] for s in (t.get("fermentation") or {}).get("steps") or []],
        "water": ((t.get("water") or {}).get("source") or {}).get("name"), "yeast": y.get("name"),
        "hopStand": [t.get("hopStandMinutes") or eq.get("whirlpoolTime") or 20, t.get("avgWeightedHopstandTemp") or 80],
        "folder": t.get("path"),
    }


# ------------------------------------------------------------------ matching
BRANDS = r"gladfield|weyermann|simpsons|briess|bestmalz|best|crisp|castle|chateau|dingemans|thomas|fawcett|muntons|rahr|great|western|" \
         r"avangard|mecca|grade|joe|white|voyager|barrett|burston|bairds|viking|malteurop|patagonia|swaen|global|malt|malts|the|and|of|" \
         r"us|uk|nz|de|a|grain|organic|premium|traditional|floor|malted|brewers|brewer"
MALT_CATS = [
    ("dextrin", r"cara-?pils|carafoam|dextrin|gladiator|carahell|cara ?blond|light crystal 10"),
    ("acid", r"acid|sauer"),
    ("choc", r"choc"),
    ("roast", r"roast|black|carafa|midnight|patent"),
    ("crystal", r"crystal|caramel|\bcara|special b|medium crystal|dark crystal"),
    ("sugar", r"dextrose|corn sugar|glucose|sucrose|table sugar|candi|cane sugar|honey(?! malt)|treacle|molasses|maple"),
    ("lactose", r"lactose"),
    ("extract", r"\b(dme|lme|malt extract|spraymalt)\b"),
    ("oats", r"\boat|avena"),
    ("wheat", r"wheat|weizen"),
    ("rye", r"\brye\b|roggen"),
    ("munich", r"munich|\baroma malt"),
    ("vienna", r"vienna"),
    ("biscuit", r"biscuit|victory|amber|aromatic|melanoidin|brown malt|toffee|honey malt|gambrinus"),
    ("pils", r"pils|lager malt"),
    ("base", r"two ?row|tworow|2 ?row|pale|ale malt|american ale|maris|otter|golden promise|halcyon|optic|base|brewers malt|"
             r"\bale\b|vienna-?style|mild"),
]
GUESS = {  # (Brewfather type, potential, colour SRM) for a malt you don't have
    "base": ("Grain", 1.037, 2.5), "pils": ("Grain", 1.037, 1.7), "munich": ("Grain", 1.035, 9), "vienna": ("Grain", 1.036, 3.5),
    "wheat": ("Grain", 1.038, 2), "oats": ("Adjunct", 1.033, 1), "rye": ("Grain", 1.035, 3), "crystal": ("Grain", 1.034, 40),
    "dextrin": ("Grain", 1.033, 1.5), "acid": ("Grain", 1.027, 2), "choc": ("Grain", 1.028, 350), "roast": ("Grain", 1.025, 500),
    "sugar": ("Sugar", 1.046, 0), "lactose": ("Sugar", 1.035, 0), "extract": ("Dry Extract", 1.044, 4), "biscuit": ("Grain", 1.035, 25),
    None: ("Grain", 1.036, 3),
}


def tokens(s):
    s = (s or "").lower().replace("®", "").replace("™", "")
    s = re.sub(r"\b(two|2)[\s-]?row\b", "tworow", s)
    s = re.sub(r"\bpils\w*", "pils", s)
    s = re.sub(r"\bcaramel\b", "crystal", s)
    return {t for t in re.findall(r"[a-z0-9]+", s) if not re.fullmatch(BRANDS, t) and not t.isdigit()}


def malt_cat(s):
    s = (s or "").lower()
    for name, rx in MALT_CATS:
        if re.search(rx, s):
            return name
    return None


def malt_score(src, cand):
    """0..1 how well one of your fermentables matches the recipe's malt."""
    sn, cn = src.get("name") or "", cand.get("name") or ""
    a, b = tokens(sn), tokens(cn)
    j = len(a & b) / len(a | b) if a and b else 0
    ca, cb = malt_cat(sn), malt_cat(cn + " " + (cand.get("grainCategory") or ""))
    c1, c2 = src.get("color_srm"), cand.get("color")
    close = max(0.0, 1 - abs(math.log((c1 + 1) / (c2 + 1))) / 1.2) if c1 is not None and c2 is not None else None
    if ca in ("crystal", "roast", "choc", "biscuit") and close is not None:
        s = 0.45 * (ca == cb) + 0.15 * j + 0.4 * close
    else:
        s = 0.5 * (ca is not None and ca == cb) + 0.5 * j
        if close is not None:
            s += 0.2 * close - 0.1
    if ca and cb and ca != cb:
        s -= 0.3
    return round(max(0.0, min(1.0, s)), 3)


def malt_choices(m, ctx, n=5):
    used = ctx["uses"]["fermentables"]
    ranked = sorted(((malt_score(m, f), min(used.get(name, 0), 20), name) for name, f in ctx["lib"]["fermentables"].items()), reverse=True)
    return [(name, sc) for sc, _, name in ranked[:n] if sc > 0.25]


def hop_key(name, ctx):
    k = hop_flavour._key(br.hop_variety(name))
    return ctx["db"]["aliases"].get(k, k)


def src_form(h):
    return "cryo" if (h.get("form") == "cryo" or re.search(r"cryo|lupulin|lupomax|lupumax", h.get("name") or "", re.I)) else (h.get("form") or "pellet")


def hop_choices(h, ctx):
    """Your hops of the same variety (same form first)."""
    key, want = hop_key(h["name"], ctx), src_form(h)
    same = [n for n, o in ctx["lib"]["hops"].items() if hop_key(n, ctx) == key]
    same.sort(key=lambda n: (("cryo" if br.hop_form(ctx["lib"]["hops"][n]) == "Cryo / lupulin" else "pellet") != want, n))
    return same


def similar_hops(h, ctx, n=4):
    """Your hop varieties with the closest aroma profile (for a hop you don't have)."""
    me = hop_flavour.lookup(ctx["db"], br.hop_variety(h["name"]))
    if not me:
        return []
    va = [me["aromas"][c] for c in hop_flavour.AROMAS]
    seen, out = set(), []
    for name, o in ctx["lib"]["hops"].items():
        v = br.hop_variety(name)
        if v in seen or br.hop_form(o) != "Pellet":
            continue
        seen.add(v)
        other = hop_flavour.lookup(ctx["db"], v)
        if not other or hop_key(v, ctx) == hop_key(h["name"], ctx):
            continue
        vb = [other["aromas"][c] for c in hop_flavour.AROMAS]
        dot = sum(x * y for x, y in zip(va, vb))
        norm = math.sqrt(sum(x * x for x in va)) * math.sqrt(sum(y * y for y in vb))
        if norm:
            out.append((round(dot / norm, 2), name))
    return [(name, sc) for sc, name in sorted(out, reverse=True)[:n]]


YEAST_FAMILIES = [
    ("American ale", r"us-?05|wlp ?001|\b1056\b|bry-?97|chico|american ale|california ale|safale american|\bm44\b|west coast|\ba07\b|flagship|cali\b"),
    ("Hazy / London Ale III", r"london ale iii|\b1318\b|wlp ?066|verdant|\ba38\b|juice|hazy|neipa|\bm66\b|vermont|conan|wlp ?095|\ba04\b|\ba24\b|boston|\bbbc\b"),
    ("English ale", r"s-?04|wlp ?002|wlp ?007|\b1968\b|\b1098\b|english ale|london ale(?! iii)|windsor|nottingham|\bm15\b|\bm36\b|\ba09\b|\ba01\b|london esb"),
    ("Kveik", r"kveik|voss|hornindal|lutra|oyl-?0\d\d|\bm12\b|lallemand voss"),
    ("Lager", r"w-?34/70|34/70|lager|s-?23|\b2124\b|\b2206\b|wlp ?830|wlp ?833|novalager|diamond|\bm54\b|\bm76\b"),
    ("Belgian / saison", r"saison|belgian|abbey|t-?58|be-?256|be-?134|\b3711\b|\b3724\b|wlp ?565|wlp ?500|\bm29\b|\bm41\b|trappist|farmhouse"),
    ("German ale / Kolsch", r"k-?97|kolsch|kölsch|\b2565\b|wlp ?029|altbier|german ale"),
    ("Wheat", r"wb-?06|hefe|weizen|\b3068\b|wlp ?300|\bm20\b|munich classic"),
]


def yeast_text(o):
    return " ".join(str(o.get(k) or "") for k in ("name", "productId", "laboratory")).lower()


def yeast_family(s):
    for name, rx in YEAST_FAMILIES:
        if re.search(rx, (s or "").lower()):
            return name
    return None


def yeast_choices(y, ctx):
    """[(name, why)] your yeasts that are the same strain, then the same family."""
    text = (y.get("name") or "").lower()
    codes = set(re.findall(r"[a-z]{0,4}-?\d{2,4}(?:/\d+)?", text.replace(" ", "")))
    exact, fam = [], []
    want = yeast_family(text)
    for name, o in ctx["lib"]["yeasts"].items():
        t = yeast_text(o)
        pid = (o.get("productId") or "").lower().replace(" ", "")
        if (pid and (pid in text.replace(" ", "") or pid.replace("-", "") in text.replace(" ", "").replace("-", ""))) or \
                (codes and any(c and c in t.replace(" ", "") for c in codes)) or (name.lower() in text):
            exact.append((name, "same strain"))
        elif want and yeast_family(t) == want:
            fam.append((name, f"same family ({want})"))
    return exact + fam


def auto_match(d, ctx):
    """Fill each ingredient's match when it's clear. Leaves anything doubtful as None for a question."""
    lib = ctx["lib"]
    for m in d["malts"]:
        if m.get("match") and m["match"] != "new" and m["match"] not in lib["fermentables"]:
            m["match"] = None
        if m.get("match") is None:
            ch = malt_choices(m, ctx, 2)
            if ch and ch[0][1] >= 0.8 and (len(ch) == 1 or ch[0][1] - ch[1][1] >= 0.08):
                m["match"] = ch[0][0]
    for h in d["hops"]:
        if h.get("match") and h["match"] != "new" and h["match"] not in lib["hops"]:
            h["match"] = None
        if h.get("match") is None:
            ch = hop_choices(h, ctx)
            if ch:
                h["match"] = ch[0]
    for y in d["yeasts"][:1]:
        if y.get("match") and y["match"] not in ("new", "template") and y["match"] not in lib["yeasts"]:
            y["match"] = None
        if y.get("match") is None:
            ch = yeast_choices(y, ctx)
            if ch and ch[0][1] == "same strain":
                y["match"] = ch[0][0]
    return d


# ------------------------------------------------------------------ questions
def _q(qid, text, options=None, field=None, kind=None, detail=None, unit=None):
    return {"id": qid, "text": text, "detail": detail, "options": options or [], "field": field, "kind": kind, "unit": unit}


def questions(d, ctx):
    """Questions for anything the reader couldn't settle. The first option is the recommended answer."""
    qs = []
    std = standards(ctx)
    if not d.get("name"):
        qs.append(_q("name", "What is this recipe called?", field="name", kind="text"))
    if not d.get("batch_l"):
        qs.append(_q("batch", "What batch size is the recipe written for?",
                     [{"label": "19 L (5 US gallons)", "set": {"batch_l": 18.93}}, {"label": "20 L", "set": {"batch_l": 20}},
                      {"label": "23 L (6 US gallons)", "set": {"batch_l": 22.71}}, {"label": "25 L (5.5 imperial gallons)", "set": {"batch_l": 25}}],
                     field="batch_l", kind="number", unit="L",
                     detail="Everything is scaled from this to your " + f"{std['batch']:g} L." if std.get("batch") else None))
    has_kg = any(m.get("kg") for m in d["malts"])
    if d["malts"] and not d.get("og") and has_kg and not d.get("efficiency"):
        qs.append(_q("eff", "The recipe doesn't give an OG or its efficiency. What efficiency was it written for?",
                     [{"label": f"{DEFAULT_EFFICIENCY}% (typical for magazine recipes)", "set": {"efficiency": DEFAULT_EFFICIENCY}},
                      {"label": "75%", "set": {"efficiency": 75}}, {"label": "65%", "set": {"efficiency": 65}},
                      {"label": f"Same as mine ({std['eff']:g}%): use the same grain per litre", "set": {"efficiency": std["eff"]}}],
                     field="efficiency", kind="number", unit="%",
                     detail="Used to work out the gravity the recipe was aiming for, so your grain bill hits the same OG."))
    if d["malts"] and not has_kg and not d.get("og"):
        qs.append(_q("og", "The malts are given as percentages only. What OG should it have?", field="og", kind="number",
                     detail="For example 1.060."))
    for i, m in enumerate(d["malts"]):
        if not m.get("kg") and has_kg:
            qs.append(_q(f"mkg{i}", f"How much {m['name']}?", field=f"malts.{i}.kg", kind="number", unit="kg", detail=m.get("raw")))
        if m.get("match") is None:
            opts = []
            for n, _ in malt_choices(m, ctx, 4):
                col = ctx["lib"]["fermentables"][n].get("color")
                opts.append({"label": n, "note": f"{col * 1.97:.0f} EBC" if col is not None else "", "set": {f"malts.{i}.match": n}})
            opts.append({"label": f"Keep “{m['name']}” as a new ingredient", "set": {f"malts.{i}.match": "new"}})
            qs.append(_q(f"malt{i}", f"Which of your malts is “{m['name']}”?", opts, detail=m.get("raw")))
    asked = set()
    for i, h in enumerate(d["hops"]):
        if not h.get("g"):
            qs.append(_q(f"hg{i}", f"How much {h['name']}?", field=f"hops.{i}.g", kind="number", unit="g", detail=h.get("raw")))
        if not h.get("use"):
            qs.append(_q(f"huse{i}", f"When does the {h['name']} go in?", [
                {"label": "Boil, 60 min", "set": {f"hops.{i}.use": "Boil", f"hops.{i}.time": 60}},
                {"label": "Boil, 10 min", "set": {f"hops.{i}.use": "Boil", f"hops.{i}.time": 10}},
                {"label": "Whirlpool", "set": {f"hops.{i}.use": "Aroma"}},
                {"label": "Dry hop", "set": {f"hops.{i}.use": "Dry Hop"}}], detail=h.get("raw")))
        key = hop_key(h["name"], ctx)
        if h.get("match") is None and key not in asked:
            asked.add(key)
            same = [j for j, o in enumerate(d["hops"]) if o.get("match") is None and hop_key(o["name"], ctx) == key]
            setall = lambda v: {f"hops.{j}.match": v for j in same}
            var = br.hop_variety(h["name"])
            keep = {"label": f"Keep {var} (a new ingredient for you)", "set": setall("new")}
            subs = [{"label": f"Use your {n} instead", "note": f"aroma {round(sc * 100)}% similar", "set": setall(n)}
                    for n, sc in similar_hops(h, ctx)]
            only_bitter = all(d["hops"][j].get("use") in ("Boil", "First Wort") and (d["hops"][j].get("time") or 0) >= 30 for j in same)
            if only_bitter and ctx["bittering"]:
                opts = [{"label": f"Use your usual bittering hop, {n}", "note": "the IBU stays the same", "set": setall(n)}
                        for n in ctx["bittering"]] + [keep]
            else:
                opts = [keep] + subs
            qs.append(_q(f"hop{i}", f"You haven't used {var} before. Keep it or substitute?", opts,
                         detail="Only used for bittering, so the variety matters little." if only_bitter else h.get("raw")))
        if h.get("match") == "new" and not h.get("alpha") and h.get("use") in ("Boil", "First Wort", "Mash"):
            a = ctx["alphas"].get(hop_key(h["name"], ctx))
            qs.append(_q(f"ha{i}", f"What alpha acid is your {br.hop_variety(h['name'])}?",
                         [{"label": f"{a:g}% (typical)", "set": {f"hops.{i}.alpha": a}}] if a else [],
                         field=f"hops.{i}.alpha", kind="number", unit="%", detail="Used for bitterness."))
    if d["yeasts"] and d["yeasts"][0].get("match") is None:
        y = d["yeasts"][0]
        opts = [{"label": n, "note": why, "set": {"yeasts.0.match": n}} for n, why in yeast_choices(y, ctx)[:3]]
        if std.get("yeast"):
            opts.append({"label": f"My usual: {std['yeast']}", "set": {"yeasts.0.match": "template"}})
        opts.append({"label": f"Keep “{y['name']}” as a new ingredient", "set": {"yeasts.0.match": "new"}})
        qs.append(_q("yeast", f"Which yeast for “{y['name']}”?", opts))
    mash = std.get("mash") or []
    if d.get("mash_c") and mash and mash[0][0] and abs(d["mash_c"] - mash[0][0]) >= 1 and not d.get("mash_choice"):
        qs.append(_q("mash", f"The recipe mashes at {d['mash_c']:g} °C. Your standard mash starts at {mash[0][0]:g} °C. Which?", [
            {"label": f"The recipe's {d['mash_c']:g} °C", "set": {"mash_choice": "recipe"}},
            {"label": f"My standard {mash[0][0]:g} °C", "set": {"mash_choice": "standard"}}],
            detail="A warmer mash leaves more body; a cooler one ferments drier."))
    return qs


def answer_defaults(d, ctx):
    """Apply the recommended answer to every open question (the automatic mode)."""
    applied = []
    for _ in range(4):
        qs = [q for q in questions(d, ctx) if q["options"]]
        if not qs:
            break
        for q in qs:
            for path, v in q["options"][0]["set"].items():
                set_path(d, path, v)
            applied.append(f"{q['text']} → {q['options'][0]['label']}")
        auto_match(d, ctx)
    return applied


def set_path(d, path, value):
    parts = path.split(".")
    obj = d
    for p in parts[:-1]:
        obj = obj[int(p)] if isinstance(obj, list) else obj[p]
    last = parts[-1]
    if isinstance(obj, list):
        obj[int(last)] = value
    else:
        obj[last] = value


# ------------------------------------------------------------------ scaling and the Brewfather recipe
def _round_kg(kg):
    return round(kg * 20) / 20 if kg >= 1 else round(kg, 2)


def _round_g(g, step):
    return max(step, round(g / step) * step) if g > 0 else 0


def guess_style_water(d):
    s = ((d.get("style") or "") + " " + (d.get("name") or "")).lower()
    if re.search(r"hazy|neipa|new england|juicy", s):
        return "NEIPA"
    if re.search(r"ipa|west coast", s):
        return "IPA"
    if re.search(r"pale|xpa|apa", s):
        return "Pale"
    return "template"


def build(d, opts, ctx):
    """(view for the page, Brewfather recipe). d must have its questions answered (missing answers get defaults)."""
    t = ctx["template"]
    if not t:
        raise ValueError("No template recipe yet. Fetch your brews from Brewfather first, or choose a template on the Admin page.")
    lib = ctx["lib"]
    eq = t.get("equipment") or {}
    size = t.get("batchSize") or eq.get("batchSize") or 22
    eff = t.get("efficiency") or eq.get("efficiency") or 70
    boil = t.get("boilTime") or eq.get("boilTime") or 60
    stand_min = t.get("hopStandMinutes") or eq.get("whirlpoolTime") or 20
    stand_temp = t.get("avgWeightedHopstandTemp") or 80
    step = 5 if str(opts.get("round")) == "5" else 1
    notes_out, warnings, assumptions = [], list(d.get("warnings") or []), []

    src_l = d.get("batch_l")
    if not src_l:
        src_l = size
        assumptions.append(f"No batch size given: treated as {size:g} L, so nothing is scaled for volume.")
    f = size / src_l

    # ---- malts
    malts, view_malts = [], []
    for i, m in enumerate(d["malts"]):
        match = m.get("match")
        if match and match != "new" and match in lib["fermentables"]:
            obj = calc.clean(lib["fermentables"][match])
        else:
            typ, pot, col = GUESS.get(malt_cat(m["name"]), GUESS[None])
            obj = {"name": m["name"], "type": typ, "potential": pot, "color": m.get("color_srm") if m.get("color_srm") is not None else col,
                   "grainCategory": None, "origin": "", "supplier": ""}
            if m.get("color_srm") is None:
                assumptions.append(f"{m['name']}: colour guessed at {obj['color'] * 1.97:.0f} EBC.")
        weight = m.get("kg") or (m.get("pct") or 0) / 100
        malts.append([obj, weight, m])
    have_kg = any(m.get("kg") for m in d["malts"])
    src_eff = d.get("efficiency") or (eff if have_kg and not d.get("og") else DEFAULT_EFFICIENCY)
    if d.get("og"):
        target_pts = (d["og"] - 1) * 1000
    elif have_kg:
        target_pts = calc.gravity_points([(o, w) for o, w, _ in malts], src_l, src_eff)
        if not d.get("efficiency"):
            assumptions.append(f"No OG or efficiency given: assumed {src_eff:g}% efficiency for the original.")
    else:
        target_pts = 50
        warnings.append("No OG and no malt weights: aimed for OG 1.050. Enter the OG to fix this.")
    unit_pts = calc.gravity_points([(o, w) for o, w, _ in malts], size, eff)
    k = target_pts / unit_pts if unit_pts else 0
    for o, w, m in malts:
        kg = m["set_kg"] if m.get("set_kg") is not None else _round_kg(w * k)
        o["amount"] = kg
    kg_total = sum(o["amount"] for o, _, _ in malts) or 1
    pairs = [(o, o["amount"]) for o, _, _ in malts]
    og = 1 + calc.gravity_points(pairs, size, eff) / 1000
    for i, (o, w, m) in enumerate(malts):
        o["percentage"] = round(o["amount"] / kg_total * 100, 2)
        view_malts.append({"i": i, "src": m["name"], "srcKg": m.get("kg"), "srcPct": m.get("pct"), "name": o["name"], "kg": o["amount"],
                           "pct": round(o["amount"] / kg_total * 100, 1), "ebc": round((o.get("color") or 0) * 1.97, 1),
                           "new": not (m.get("match") and m["match"] != "new"), "set": m.get("set_kg") is not None,
                           "choices": [n for n, _ in malt_choices(m, ctx, 5)]})

    # ---- yeast and FG
    tyeast = (t.get("yeasts") or [None])[0]
    y0 = d["yeasts"][0] if d["yeasts"] else None
    ym = (y0 or {}).get("match") or "template"
    if ym == "template" or not y0:
        yobj = calc.clean(tyeast) if tyeast else {"name": "Ale yeast", "type": "Ale", "form": "Dry", "attenuation": 77, "amount": 1, "unit": "pkg"}
        if not y0:
            assumptions.append(f"No yeast in the recipe: used your usual {yobj.get('name')}.")
    elif ym in lib["yeasts"]:
        yobj = calc.clean(lib["yeasts"][ym])
    else:
        yobj = {"name": y0["name"], "type": "Ale", "form": "Liquid" if re.search(r"wyeast|white labs|wlp|imperial|omega", y0["name"], re.I) else "Dry",
                "attenuation": 77, "laboratory": "", "productId": "", "unit": "pkg", "amount": 1}
        assumptions.append(f"{y0['name']}: attenuation assumed 77%.")
    if tyeast and yobj.get("form") == tyeast.get("form"):
        yobj["amount"], yobj["unit"] = tyeast.get("amount") or 1, tyeast.get("unit") or "pkg"
    else:
        yobj.setdefault("amount", 1)
        yobj.setdefault("unit", "pkg")
    if d.get("og") and d.get("fg") and d["og"] > d["fg"]:
        att = (d["og"] - d["fg"]) / (d["og"] - 1)
    else:
        att = (yobj.get("attenuation") or 77) / 100
    fg = 1 + (og - 1) * (1 - att)
    abv = (og - fg) * 131.25

    # ---- hops
    src_og = d.get("og") or (1 + target_pts / 1000)
    src_boil = d.get("boil_min") or 60
    hops = []
    for i, h in enumerate(d["hops"]):
        match = h.get("match")
        if match and match != "new" and match in lib["hops"]:
            obj = calc.clean(lib["hops"][match])
        else:
            alpha = h.get("alpha") or ctx["alphas"].get(hop_key(h["name"], ctx)) or 10
            form = src_form(h)
            obj = {"name": br.hop_variety(h["name"]) + (" Cryo" if form == "cryo" and "cryo" not in h["name"].lower() else ""),
                   "alpha": alpha, "type": {"cryo": "Pellet", "leaf": "Leaf", "extract": "Extract"}.get(form, "Pellet"), "origin": ""}
            if not h.get("alpha") and h.get("use") in ("Boil", "First Wort"):
                assumptions.append(f"{obj['name']}: alpha acid assumed {alpha:g}%.")
        our_alpha = obj.get("alpha") or 10
        s_alpha = h.get("alpha") or our_alpha
        s_strength = 2.0 if src_form(h) == "cryo" else 1.0
        strength = br.hop_strength(obj)
        use = h.get("use") or "Boil"
        s_time = h.get("time") if h.get("time") is not None else (src_boil if use in ("Boil", "First Wort") else stand_min)
        if use == "First Wort":
            s_time = src_boil
        g_src = h.get("g") or 0
        # bitterness of the original
        if use in ("Boil", "First Wort"):
            s_ibu = calc.tinseth(s_alpha, g_src, min(s_time, src_boil), src_og, src_l) * (1.1 if use == "First Wort" else 1)
        elif use == "Aroma":
            s_ibu = calc.tinseth(s_alpha, g_src, 20, src_og, src_l, calc.WHIRLPOOL_FACTOR)
        else:
            s_ibu = 0
        bitter = use in ("Boil", "First Wort", "Mash") and s_time >= 30
        if bitter:
            g = g_src * f * s_alpha / our_alpha
        else:
            g = g_src * f * s_strength / strength
        hops.append({"i": i, "h": h, "obj": obj, "use": use, "time": s_time, "g": g, "bitter": bitter, "s_ibu": s_ibu,
                     "alpha": our_alpha, "s_alpha": s_alpha, "strength": strength})
    if hops and not any(x["bitter"] for x in hops):
        boils = [x for x in hops if x["use"] in ("Boil", "First Wort")]
        if boils:
            longest = max(boils, key=lambda x: x["time"])
            longest["bitter"] = True
            longest["g"] = (longest["h"].get("g") or 0) * f * longest["s_alpha"] / longest["alpha"]

    def ibu_of(x, g):
        if x["use"] in ("Boil", "First Wort"):
            t_ = boil if x["use"] == "First Wort" else min(x["time"], boil)
            return calc.tinseth(x["alpha"], g, t_, og, size) * (1.1 if x["use"] == "First Wort" else 1)
        if x["use"] == "Aroma":
            return calc.tinseth(x["alpha"], g, 20, og, size, calc.WHIRLPOOL_FACTOR)
        return 0.0
    src_ibu_calc = sum(x["s_ibu"] for x in hops)
    target_ibu = d.get("ibu") if (d.get("ibu") and opts.get("ibu") != "calc") else src_ibu_calc
    others = sum(ibu_of(x, x["g"]) for x in hops if not x["bitter"])
    bit = sum(ibu_of(x, x["g"]) for x in hops if x["bitter"])
    if bit > 0 and target_ibu:
        need = target_ibu - others
        if need <= 0:
            warnings.append("The late hops alone reach the recipe's IBU, so the bittering addition was kept small.")
            need = bit * 0.25
        for x in hops:
            if x["bitter"]:
                x["g"] *= need / bit
    hops_out, view_hops = [], []
    for x in hops:
        h, obj = x["h"], x["obj"]
        g = h["set_g"] if h.get("set_g") is not None else _round_g(x["g"], step)
        x["g"] = g
        ibu = ibu_of(x, g)
        o = dict(obj)
        o.update({"amount": g, "use": x["use"], "ibu": round(ibu, 1), "actualTime": None})
        if x["use"] == "Dry Hop":
            day = h.get("day") or next((hh.get("day") for hh in t.get("hops") or [] if hh.get("use") == "Dry Hop" and hh.get("day")), None) or 5
            o.update({"time": 3, "day": day, "timeUnit": "days", "temp": None})
        elif x["use"] == "Aroma":
            o.update({"time": stand_min, "temp": stand_temp, "day": None})
        elif x["use"] == "First Wort":
            o.update({"time": boil, "day": None, "temp": None})
        else:
            o.update({"time": min(x["time"], boil) if x["use"] == "Boil" else x["time"], "day": None, "temp": None})
        hops_out.append({kk: v for kk, v in o.items() if v is not None})
        stage = br.hop_stage({"use": x["use"], "time": o.get("time")})
        view_hops.append({"i": x["i"], "src": h["name"], "srcG": h.get("g"), "srcAlpha": h.get("alpha"), "name": obj["name"],
                          "variety": br.hop_variety(obj["name"]), "g": g, "alpha": x["alpha"], "use": x["use"], "time": o.get("time"),
                          "day": o.get("day"), "ibu": round(ibu, 1), "stage": stage, "strength": x["strength"],
                          "ge": g * x["strength"], "gpl": round(g * x["strength"] / size, 2), "bitter": x["bitter"],
                          "new": not (h.get("match") and h["match"] != "new"), "set": h.get("set_g") is not None,
                          "choices": hop_choices(h, ctx), "subs": [n for n, _ in similar_hops(h, ctx)]})
    ibu = sum(v["ibu"] for v in view_hops)
    if any(x["time"] > boil for x in hops if x["use"] == "Boil"):
        assumptions.append(f"Boil additions longer than your {boil:g} min boil start at the beginning of the boil; the bittering "
                           "amount makes up the difference.")

    # ---- mash
    mash = copy.deepcopy(t.get("mash") or {"name": "Mash", "steps": [{"stepTemp": 66, "stepTime": 60, "type": "Temperature"}]})
    if d.get("mash_choice") == "recipe" and d.get("mash_c") and mash.get("steps"):
        mash["steps"][0]["stepTemp"] = d["mash_c"]
        mash["steps"][0]["displayStepTemp"] = d["mash_c"]
        mash["name"] = f"{mash.get('name') or 'Mash'} ({d['mash_c']:g} °C)"

    # ---- water and other additions
    src_water = (t.get("water") or {}).get("source") or {}
    src_prof = br.ion_profile(src_water) or {}
    water_mode = opts.get("water") or guess_style_water(d)
    tmiscs = [calc.clean(m) for m in t.get("miscs") or []]
    is_water = lambda m: (m.get("type") == "Water Agent" or m.get("waterAdjustment")) and not re.search(r"lactic|phosph|acid", m.get("name") or "", re.I)
    acid = next((m for m in tmiscs if re.search(r"lactic|phosph", m.get("name") or "", re.I)), None)
    wp = calc.water_plan(src_prof, kg_total, eq, calc.WATER_TARGETS.get(water_mode, calc.WATER_TARGETS["Pale"]),
                         (acid or {}).get("amount"), (t.get("water") or {}).get("mashPh"))
    if water_mode in calc.WATER_TARGETS and src_water:
        miscs = [m for m in tmiscs if not is_water(m)]
        names = {"Gypsum": r"gypsum|caso4", "CaCl2": r"calcium chlor|cacl", "Epsom": r"epsom|mgso4"}
        labels = {"Gypsum": "Gypsum (CaSO4)", "CaCl2": "Calcium Chloride (CaCl2)", "Epsom": "Epsom Salt (MgSO4)"}
        for key, rx in names.items():
            base = next((lib["miscs"][n] for n in lib["miscs"] if re.search(rx, n, re.I)), None)
            for where, amt in (("Mash", wp["mash"][key]), ("Sparge", wp["sparge"][key])):
                if amt > 0:
                    mo = calc.clean(base) if base else {"name": labels[key]}
                    mo.update({"amount": amt, "use": where, "unit": "g", "type": "Water Agent", "waterAdjustment": True})
                    miscs.append(mo)
        tgt = calc.WATER_TARGETS[water_mode]
        water = calc.water_block(t.get("water"), src_water, wp, f"{tgt['title']} ({src_water.get('name') or 'tap'} + salts)",
                                 "Suggested by the recipe importer.")
        water_view = {"mode": water_mode, "title": tgt["title"], "grams": wp["grams"], "result": wp["result"], "source": src_water.get("name")}
    else:
        water_mode = "template"
        miscs = tmiscs
        water = copy.deepcopy(t.get("water"))
        sal = {}
        for m in tmiscs:
            if is_water(m):
                sal[m.get("name")] = round(sal.get(m.get("name"), 0) + (m.get("amount") or 0), 2)
        water_view = {"mode": "template", "title": "Same as your template", "salts": sal, "source": src_water.get("name")}

    # ---- totals, flavour, bags
    color = calc.srm(pairs, size)
    dry = sum(v["ge"] for v in view_hops if v["use"] == "Dry Hop")
    aroma = sum(v["ge"] for v in view_hops if v["use"] == "Aroma")
    flavour = hop_flavour.profile(view_hops, ctx["db"])
    bags = {}
    for v in view_hops:
        bags[v["name"]] = bags.get(v["name"], 0) + v["g"]
    bag_view = [{"name": n, "g": g, "bags": math.ceil(g / 100 - 1e-9)} for n, g in sorted(bags.items(), key=lambda x: -x[1])]

    others_txt = "; ".join(o["name"] for o in d.get("others") or [])
    std = standards(ctx)
    name = (d.get("name") or "Imported recipe").strip()[:80]
    src_line = ", ".join(x for x in [f"{d['batch_l']:g} L" if d.get("batch_l") else None, f"OG {d['og']:.3f}" if d.get("og") else None,
                                     f"FG {d['fg']:.3f}" if d.get("fg") else None, f"{d['ibu']:g} IBU" if d.get("ibu") else None,
                                     f"{d['abv']:g}% ABV" if d.get("abv") else None,
                                     f"{d['efficiency']:g}% efficiency" if d.get("efficiency") else None] if x)
    swaps = [f"{v['src']} → {v['name']}" for v in view_hops if br.hop_variety(v["src"]).lower() != v["variety"].lower()]
    swaps += [f"{v['src']} → {v['name']}" for v in view_malts if not v["new"]]
    notes = (f"Imported from a pasted recipe on {datetime.now():%d %b %Y} ({'read by ' + d.get('reader') if d.get('reader') not in (None, 'rules') else 'read and checked by hand'}).\n"
             f"Original: {src_line or 'no figures given'}.\n"
             f"Scaled to {size:g} L at {eff:g}% efficiency with {std['label']} as the template "
             f"(equipment, mash, fermentation{', water' if water_mode == 'template' else ''}).\n"
             + (f"Your ingredients used: {'; '.join(swaps)}.\n" if swaps else "")
             + (f"Not added (from the original, add them in Brewfather if you want them): {others_txt}.\n" if others_txt else "")
             + ("\nOriginal notes:\n" + d["notes"].strip() if (d.get("notes") or "").strip() else ""))[:6000]

    r = copy.deepcopy(t)
    for kk in list(r):
        if kk.startswith("_") or kk in ("img", "thumb", "searchTags", "updatedBy", "public", "tags", "nutrition") or kk.startswith("style"):
            r.pop(kk)
    if opts.get("folder") == "none":
        r.pop("path", None)
        r.pop("folderRefs", None)
    ferms = [o for o, _, _ in malts]
    r.update({
        "name": name, "notes": notes, "fermentables": ferms, "hops": hops_out, "yeasts": [yobj], "miscs": miscs,
        "og": round(og, 4), "fg": round(fg, 4), "fgEstimated": round(fg, 4), "abv": round(abv, 2), "attenuation": round(att * 100, 1),
        "ibu": round(ibu, 1), "color": round(color, 2), "buGuRatio": round(ibu / ((og - 1) * 1000), 2) if og > 1 else None,
        "postBoilGravity": round(og, 4),
        "preBoilGravity": round(1 + (og - 1) * (eq.get("postBoilKettleVol") or 26.56) / (eq.get("boilSize") or 28.44), 4),
        "fermentablesTotalAmount": round(kg_total, 2), "hopsTotalAmount": sum(v["g"] for v in view_hops),
        "sumDryHopPerLiter": round(dry / size, 2), "sumAromaHopPerLiter": round(aroma / size, 2),
        "mash": mash, "water": water,
    })
    data = r.get("data") or {}
    data.update({"mashFermentables": ferms, "mashFermentablesAmount": round(kg_total, 2), "otherFermentables": [],
                 "otherFermentablesAmount": 0, "hopsAmount": sum(v["g"] for v in view_hops),
                 "mashWaterAmount": wp["mashL"], "strikeWaterAmount": wp["mashL"], "spargeWaterAmount": wp["spargeL"],
                 "hltWaterAmount": wp["spargeL"], "totalWaterAmount": wp["totalL"]})
    r["data"] = data

    view = {
        "name": name, "size": size, "eff": eff, "boil": boil, "srcL": d.get("batch_l"), "factor": round(f, 3),
        "src": {"og": d.get("og"), "fg": d.get("fg"), "ibu": d.get("ibu"), "abv": d.get("abv"), "eff": d.get("efficiency"),
                "ibuCalc": round(src_ibu_calc), "pts": round(target_pts, 1)},
        "og": round(og, 3), "fg": round(fg, 3), "abv": round(abv, 1), "ibu": round(ibu), "ebc": round(color * 1.97, 1),
        "buGu": round(ibu / ((og - 1) * 1000), 2) if og > 1 else None, "kg": round(kg_total, 2),
        "malts": view_malts, "hops": view_hops, "dryGpl": round(dry / size, 1), "aromaGpl": round(aroma / size, 1),
        "yeast": {"name": yobj.get("name"), "lab": yobj.get("laboratory"), "id": yobj.get("productId"), "amount": yobj.get("amount"),
                  "unit": yobj.get("unit"), "att": round(att * 100), "src": (y0 or {}).get("name"),
                  "choices": [n for n, _ in yeast_choices(y0, ctx)] if y0 else []},
        "mash": [[s.get("stepTemp"), s.get("stepTime")] for s in mash.get("steps") or []],
        "water": water_view, "flavour": flavour, "bags": bag_view, "others": [o["name"] for o in d.get("others") or []],
        "assumptions": assumptions, "warnings": warnings, "notes": notes, "folder": None if opts.get("folder") == "none" else t.get("path"),
        "bittering": ctx["bittering"],
    }
    return view, r


# ------------------------------------------------------------------ drafts and options sent back by the page
def sanitize(raw):
    """A draft posted by the page, checked and cleaned (raises ValueError)."""
    d = recipe_parse.from_json(raw)
    d["reader"] = str(raw.get("reader") or "rules")[:80]
    d["notes"] = str(raw.get("notes") or "")[:6000]
    d["unparsed"] = [str(x)[:200] for x in (raw.get("unparsed") or [])[:40] if isinstance(x, str)]
    d["mash_choice"] = raw.get("mash_choice") if raw.get("mash_choice") in ("recipe", "standard") else None
    for key, field, hi in (("malts", "set_kg", 500), ("hops", "set_g", 50000)):
        src = [x for x in raw.get(key) or [] if isinstance(x, dict) and str(x.get("name") or "").strip()]
        for item, orig in zip(d[key], src):
            item[field] = recipe_parse._f(orig.get(field), 0, hi)
    return d


def clean_opts(o):
    o = o if isinstance(o, dict) else {}
    return {"water": o.get("water") if o.get("water") in ("template", "IPA", "NEIPA", "Pale") else None,
            "round": 5 if str(o.get("round")) == "5" else 1,
            "folder": "none" if o.get("folder") == "none" else "template",
            "ibu": "calc" if o.get("ibu") == "calc" else "recipe"}


# ------------------------------------------------------------------ upload
def upload(recipe):
    """Create the recipe in Brewfather. Returns {id, name, path}."""
    from brewfather import Brewfather
    bf = Brewfather()
    resp = bf.post("/recipes", recipe)
    rid = (resp or {}).get("id") if isinstance(resp, dict) else None
    path = None
    if rid:
        check = bf.get(f"/recipes/{rid}", include="path,folderRefs") or {}
        path = check.get("path")
        if recipe.get("path") and path != recipe["path"]:
            bf.patch(f"/recipes/{rid}", {"path": recipe["path"], "folderRefs": recipe.get("folderRefs")})
            path = recipe["path"]
    done = {"id": rid, "name": recipe.get("name"), "path": path, "at": datetime.now().strftime("%d %b %Y %H:%M")}
    log = imported()
    log.insert(0, done)
    os.makedirs(paths.DATA, exist_ok=True)
    with open(IMPORTED, "w", encoding="utf-8") as f:
        json.dump(log[:50], f, indent=1)
    return done


def imported():
    try:
        with open(IMPORTED, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, list) else []
    except (OSError, ValueError):
        return []
