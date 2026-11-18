"""Read a recipe pasted from a magazine, book or web page into a plain "draft" dict.

    draft = parse(text, hop_names)      # rule-based, no network
    draft = from_json(obj)              # the same shape from an AI reply (or pasted JSON), checked and cleaned

Draft (all amounts metric; None when the recipe doesn't say):
    name, style, batch_l, batch_raw, og, fg, ibu, abv, efficiency, boil_min, mash_c, ferment_c
    malts:  [{name, kg, pct, color_srm, raw}]
    hops:   [{name, g, alpha, form, use, time, day, raw}]   use: Boil | First Wort | Mash | Aroma (whirlpool) | Dry Hop | None
    yeasts: [{name, raw}]
    others: [{name, raw}]       salts, finings, fruit, spices: listed in the notes, not imported
    notes:  method text kept for the recipe notes
    unparsed: lines with amounts that weren't understood
    warnings: things the reader noticed
Standard library only.
"""
import re
import unicodedata

LB, OZ = 0.45359237, 28.349523125
US_GAL, IMP_GAL, QUART = 3.785411784, 4.54609, 0.946352946

FRACTIONS = {"½": " 1/2", "⅓": " 1/3", "⅔": " 2/3", "¼": " 1/4", "¾": " 3/4", "⅛": " 1/8", "⅜": " 3/8", "⅝": " 5/8", "⅞": " 7/8"}
NUM = r"(\d+\s+\d/\d+|\d/\d+|\d+(?:[.,]\d+)?)"
MASS_RX = re.compile(NUM + r"\s*(kg|kgs|kilo(?:gram)?s?|g|gr|grams?|gms?|lbs?|pounds?|oz|ounces?)\b\.?", re.I)
VOL_RX = re.compile(NUM + r"\s*(l|lt|ltrs?|litres?|liters?|(?:us\s+|u\.s\.\s+|imperial\s+|imp\.?\s+|uk\s+)?gal(?:lons?)?|qts?|quarts?)\b\.?", re.I)
PCT_RX = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
TEMP_RX = re.compile(r"(\d{2,3}(?:[.,]\d+)?)\s*°?\s*(c|f)\b(?![a-z])", re.I)

MALT_WORDS = re.compile(
    r"\b(malts?|pils\w*|[26][- ]?row|two[- ]row|six[- ]row|maris|otter|golden promise|munich|vienna|crystal|caramel|cara\w*|"
    r"wheat|oats?|rye|barley|flaked|torrified|chocolate|roast(?:ed)?|black|biscuit|victory|aromatic|melanoidin|special b|"
    r"brown|amber|acid(?:ulated)?|sauer\w*|dextrose|sugar|corn|rice|lme|dme|malt extract|gladiator|candi|lactose|maltodextrin|"
    r"base|spelt|smoked|peated|grits|honey|treacle|molasses|syrup|chit|crisp|weyermann|briess|simpsons|gladfield|dingemans|"
    r"bestmalz|best malz|castle|thomas fawcett|muntons|great western|rahr|avangard|mecca grade)\b", re.I)
MISC_WORDS = re.compile(
    r"\b(gypsum|caso4|calcium chloride|cacl2?|epsom|mgso4|lactic|phosphoric|whirlfloc|irish moss|protafloc|nutrients?|servomyces|"
    r"campden|salt|baking soda|chalk|brewtan|biofine|gelatin|clarity|aldc|vanilla|coffee|cocoa|cacao|nibs|cinnamon|coriander|"
    r"orange peel|lemon peel|zest|ginger|fruit|puree|purée|juice|chili|chilli|pepper|spice|tea|lactobacillus|enzyme|amylase|"
    r"silica|kettle finings|yeast nutrient|zinc|antifoam|fermcap|co2|carbonat|priming|sugar for priming)\b", re.I)
YEAST_RX = re.compile(
    r"\b(yeast|wlp\s?-?\d{3}|wyeast|safale|saflager|safbrew|fermentis|lallemand|lalbrew|us-?05|s-?04|s-?33|s-?23|w-?34/70|"
    r"bry-?97|nottingham|windsor|verdant|voss|kveik|omega(?: yeast)?|oyl-?\d+|imperial (?:yeast|organic)|white labs|"
    r"mangrove jack'?s?|m\d{2}\b|escarpment|chico|london ale|cbc-?1|k-?97|t-?58|be-?256|wb-?06|novalager|diamond lager|"
    r"\d{4}\s+(?:american|london|irish|scottish|german|belgian|british|kolsch|kölsch|bavarian|czech|vermont|northwest))", re.I)
YEAST_SECTION_OK = re.compile(r"\b(yeast|ale|lager|wlp|wyeast|safale|lal|kveik|strain|pack|vial|sachet|starter)\b", re.I)

SECTIONS = [
    ("extract", re.compile(r"^(extract|partial[- ]mash|mini[- ]mash)\b.*(version|option|recipe|brewers?|only)?", re.I)),
    ("allgrain", re.compile(r"^all[- ]?grain\b", re.I)),
    ("dryhop", re.compile(r"\bdry[- ]?hop", re.I)),
    ("whirlpool", re.compile(r"\b(whirlpool|hop ?stand|flame[- ]?out|knock[- ]?out|steep(ed)? hops)\b", re.I)),
    ("hops", re.compile(r"\b(hops?|hop schedule|hop additions|hop bill|boil additions|kettle hops|bittering)\b", re.I)),
    ("malts", re.compile(r"\b(malts?|grains?|grist|fermentables?|grain bill|malt bill|mash ingredients)\b", re.I)),
    ("yeast", re.compile(r"\b(yeast|culture)\b", re.I)),
    ("other", re.compile(r"\b(other|extras?|water|additions|misc\w*|finings|kettle additions|spices?)\b", re.I)),
    ("method", re.compile(r"\b(method|directions|procedure|step[- ]by[- ]step|brewing notes|instructions|tips|process|brew day|mash|boil|fermentation|packaging|notes?)\b", re.I)),
]

STAGE_RULES = [
    ("Dry Hop", re.compile(r"\b(dry[- ]?hop\w*|dh\b|keg[- ]?hop\w*|biotrans\w*|during fermentation|high krausen|in the fermenter|fermenter)", re.I)),
    ("First Wort", re.compile(r"\b(first[- ]wort|fwh)\b", re.I)),
    ("Mash", re.compile(r"\bmash hop", re.I)),
    ("Aroma", re.compile(r"\b(whirlpool|hop ?stand|flame[- ]?out|knock[- ]?out|steep|at end of boil|end of boil|after (?:the )?boil|(?<![\d.])0(?:\.0)? min(?:ute)?s?\b)", re.I)),
]


# ------------------------------------------------------------------ small helpers
def to_num(s):
    s = s.strip()
    m = re.fullmatch(r"(\d+)\s+(\d+)/(\d+)", s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.fullmatch(r"(\d+)/(\d+)", s)
    if m:
        return int(m.group(1)) / int(m.group(2)) if int(m.group(2)) else None
    if re.fullmatch(r"\d+,\d{1,2}", s):
        s = s.replace(",", ".")
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def normalise_text(text):
    text = unicodedata.normalize("NFKC", text or "")
    for k, v in FRACTIONS.items():
        text = text.replace(k, v)
    text = (text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
            .replace("–", "-").replace("—", "-").replace(" ", " ").replace("•", "-")
            .replace("®", "").replace("™", "").replace("\t", " | "))
    text = re.sub(r"(\d)\s*-\s*(\d+/\d)", r"\1 \2", text)  # "1-1/2 oz"
    return text


def masses(line):
    """[(grams, metric?, start, end)] for each weight in the line; lb followed by oz is one weight."""
    out = []
    items = list(MASS_RX.finditer(line))
    skip = set()
    for i, m in enumerate(items):
        if i in skip:
            continue
        v, u = to_num(m.group(1)), m.group(2).lower()
        if v is None:
            continue
        if u.startswith("k"):
            out.append((v * 1000, True, m.start(), m.end()))
        elif u.startswith("g"):
            out.append((v, True, m.start(), m.end()))
        elif u.startswith(("lb", "pound")):
            g, end = v * LB * 1000, m.end()
            if i + 1 < len(items) and items[i + 1].group(2).lower().startswith(("oz", "ounce")) and items[i + 1].start() - m.end() <= 3:
                g += (to_num(items[i + 1].group(1)) or 0) * OZ
                end = items[i + 1].end()
                skip.add(i + 1)
            out.append((g, False, m.start(), end))
        else:
            out.append((v * OZ, False, m.start(), m.end()))
    return out


def best_mass(line):
    """Grams for the line, preferring a metric figure when both are given."""
    ms = masses(line)
    if not ms:
        return None
    metric = [m for m in ms if m[1]]
    return (metric or ms)[0][0]


def volume_l(m):
    v, u = to_num(m.group(1)), m.group(2).lower().replace(".", "")
    if v is None:
        return None, None
    if u.startswith(("l",)):
        return v, True
    if u.startswith(("qt", "quart")):
        return v * QUART, False
    if u.startswith(("imperial", "imp", "uk")):
        return v * IMP_GAL, False
    return v * US_GAL, False


def c_from(v, unit):
    return round((v - 32) * 5 / 9, 1) if unit.lower() == "f" else v


def sg(v):
    """1.065, 1065, 65 points or 16 °P -> specific gravity."""
    if v is None:
        return None
    if 1.0 < v < 1.2:
        return round(v, 4)
    if 1000 < v < 1200:
        return round(v / 1000, 4)
    return None


def plato_to_sg(p):
    return round(1 + p / (258.6 - (p / 258.2) * 227.1), 4)


# ------------------------------------------------------------------ whole-recipe figures
def find_stats(text, lines):
    d = {}

    def grab(rx, flags=re.I):
        m = re.search(rx, text, flags)
        return m

    m = grab(r"\b(?:o\.?g\.?|original gravity|starting gravity|pre-?ferment(?:ation)? gravity)\b[^0-9\n]{0,12}(1[.,]\d{3}|1\d{3})")
    if m:
        d["og"] = sg(to_num(m.group(1).replace(",", ".")))
    else:
        m = grab(r"\b(?:o\.?g\.?|original gravity)\b[^0-9\n]{0,12}(\d{1,2}(?:[.,]\d)?)\s*°?\s*(?:p\b|plato)")
        if m:
            d["og"] = plato_to_sg(to_num(m.group(1)))
    m = grab(r"\b(?:f\.?g\.?|final gravity|terminal gravity)\b[^0-9\n]{0,12}(1[.,]\d{3}|1\d{3})")
    if m:
        d["fg"] = sg(to_num(m.group(1).replace(",", ".")))
    m = grab(r"\bibus?\b[ \t]*[:=(]?[ \t]*(\d{1,3}(?:\.\d+)?)") or grab(r"(\d{1,3}(?:\.\d+)?)[ \t]*ibus?\b")
    if m:
        d["ibu"] = float(m.group(1))
    m = grab(r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:abv|alc)") or grab(r"\babv\b[^0-9\n]{0,6}(\d{1,2}(?:[.,]\d+)?)")
    if m:
        d["abv"] = to_num(m.group(1))
    m = grab(r"(\d{2}(?:[.,]\d+)?)\s*%\s*(?:brewhouse |mash |overall |extract )?efficiency") or \
        grab(r"efficiency[^0-9\n]{0,25}(\d{2}(?:[.,]\d+)?)\s*%")
    if m:
        d["efficiency"] = to_num(m.group(1))
    m = grab(r"\bboil(?:[ \t]*time)?(?:[ \t]*for)?[ \t]*[:=]?[ \t]*(\d{2,3})[ \t]*min") or grab(r"(\d{2,3})[- ]min(?:ute)?s?[ \t]+boil")
    if m:
        d["boil_min"] = int(m.group(1))
    m = grab(r"\bmash\w*\b[^\n]{0,60}?(\d{2,3}(?:[.,]\d+)?)\s*°?\s*([cf])\b(?![a-z])")
    if m:
        v = c_from(to_num(m.group(1)), m.group(2))
        if v and 60 <= v <= 72:
            d["mash_c"] = v
    m = grab(r"\bferment\w*\b[^\n]{0,60}?(\d{2,3}(?:[.,]\d+)?)\s*°?\s*([cf])\b(?![a-z])")
    if m:
        v = c_from(to_num(m.group(1)), m.group(2))
        if v and 8 <= v <= 40:
            d["ferment_c"] = v

    # batch size: a labelled figure first, then a volume near the top (magazine headers like "(5 gallons/19 L, all-grain)")
    def vol_in(line):
        vols = [(volume_l(m), m) for m in VOL_RX.finditer(line)]
        vols = [((v, metric), m) for (v, metric), m in vols if v and 3 <= v <= 2000]
        if not vols:
            return None
        metric = [x for x in vols if x[0][1]]
        return (metric or vols)[0][0][0]
    label = re.compile(r"\b(batch(?: size)?|yield|volume|brew length|makes|recipe size|into (?:the )?fermenter|final volume|"
                       r"size|fermenter volume|post[- ]boil)\b", re.I)
    for ln in lines:
        if label.search(ln):
            v = vol_in(ln)
            if v:
                d["batch_l"], d["batch_raw"] = round(v, 2), ln.strip()[:120]
                break
    if "batch_l" not in d:
        for ln in lines[:15]:
            if re.search(r"gal|\bl\b|litre|liter", ln, re.I) and not MASS_RX.search(ln):
                v = vol_in(ln)
                if v:
                    d["batch_l"], d["batch_raw"] = round(v, 2), ln.strip()[:120]
                    break
    return d


# ------------------------------------------------------------------ line readers
def hop_regex(names):
    names = sorted({n for n in names if n and len(n) >= 3}, key=len, reverse=True)
    parts = [re.escape(n).replace(r"\ ", r"[\s-]?") for n in names]
    return re.compile(r"(?<![A-Za-z])(" + "|".join(parts) + r")(?![A-Za-z])", re.I) if parts else None


def strip_amounts(line):
    s = MASS_RX.sub(" ", line)
    s = re.sub(r"\(\s*\d+(?:[.,]\d+)?\s*°?\s*(?:l|lovibond|srm|ebc)\s*\)", " ", s, flags=re.I)
    s = re.sub(r"\d+(?:[.,]\d+)?\s*°\s*(?:l|lovibond|srm|ebc)\b|\b\d+(?:[.,]\d+)?\s*(?:lovibond|srm|ebc)\b", " ", s, flags=re.I)
    s = PCT_RX.sub(" ", s)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"\(\s*[/,;:.\s-]*\)", " ", s)
    s = re.sub(r"^\s*[-*•·>]+\s*|\s*\|\s*", " ", s)
    s = re.sub(r"\b(grain|adjunct|item|type|amount|name|hop|yeast|misc|sugar|extract|water agent)\b\s*\d*\s*-?\s*$", " ", s, flags=re.I)
    s = re.sub(r"^\s*\d+(?:[.,]\d+)?\s*(?:x\s*)?(?:pkgs?|packs?|packets?|sachets?|vials?|pouch(?:es)?|units?|ml|l)\b\.?", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -:;,.|")
    return s


def malt_colour(line):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*°?\s*(l|lovibond|srm|ebc)\b(?![a-z])", line, re.I)
    if m:
        v = to_num(m.group(1))
        if v is not None and v <= 1600:
            return round(v / 1.97, 1) if m.group(2).lower() == "ebc" else v
    m = re.search(r"\b(?:crystal|caramel|cara|caramunich)\s*[- ]?(\d{2,3})\b", line, re.I)
    if m:
        return float(m.group(1))
    return None


def read_hop(line, m, section):
    name = m.group(1)
    form = None
    fm = re.search(r"\b(cryo|lupulin|lupomax|lupumax|incognito|t-?90|t-?45|pellets?|leaf|whole|plugs?|extract|spectrum|salvo)\b", line, re.I)
    if fm:
        w = fm.group(1).lower()
        form = "cryo" if w in ("cryo", "lupulin", "lupomax", "lupumax", "t-45", "t45") else ("extract" if w in ("extract", "incognito", "spectrum", "salvo") else
                                                                                                ("leaf" if w in ("leaf", "whole", "plug", "plugs") else "pellet"))
    alpha = None
    am = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:a\.?\s*a\.?|alpha|α)", line, re.I) or \
        re.search(r"(?:a\.?a\.?|alpha|α)\s*[:=]?\s*(\d{1,2}(?:[.,]\d+)?)\s*%?", line, re.I) or \
        re.search(r"\[\s*(\d{1,2}(?:[.,]\d+)?)\s*%\s*\]", line)
    if not am:
        pcts = [to_num(x) for x in PCT_RX.findall(line)]
        pcts = [p for p in pcts if p is not None and 1 <= p <= 25]
        if len(pcts) == 1 and not re.search(r"grist|of (?:the )?(?:bill|total)", line, re.I):
            alpha = pcts[0]
    else:
        alpha = to_num(am.group(1))
    use = None
    for stage, rx in STAGE_RULES:
        if rx.search(line):
            use = stage
            break
    time = None
    tm = re.search(r"(\d{1,3}(?:[.,]\d+)?)\s*(?:min(?:ute)?s?\b|mins?\.|')", line, re.I) or re.search(r"@\s*(\d{1,3})\b(?!\s*°)", line)
    if tm:
        time = to_num(tm.group(1))
    day = None
    dm = re.search(r"\bday\s*(\d{1,2})\b", line, re.I) or re.search(r"\bafter\s*(\d{1,2})\s*days?\b", line, re.I)
    if dm:
        day = int(dm.group(1))
    elif use == "Dry Hop" and re.search(r"high krausen|biotrans|active fermentation|during fermentation", line, re.I):
        day = 2
    if not use:
        if section == "dryhop":
            use = "Dry Hop"
        elif section == "whirlpool":
            use = "Aroma"
        elif time is not None:
            use = "Aroma" if time == 0 else "Boil"
    if use == "Boil" and re.search(r"\bdays?\b", line, re.I) and time is None:
        use = "Dry Hop"
    return {"name": name.strip(), "g": None, "alpha": alpha, "form": form, "use": use,
            "time": time, "day": day, "raw": line.strip()[:200]}


HOP_CONTEXT = re.compile(r"\b(min|mins|minutes|boil|whirlpool|dry|hops?|aa|alpha|flame\w*|knock\w*|days?|hop ?stand|fwh)\b|@|%\s*a", re.I)


def read_hops(line, hop_rx, section):
    """One or more hops from a line: "Dry hop: 60 g Nelson Sauvin and 40 g Motueka on day 4" is two."""
    ms = masses(line)
    names = list(hop_rx.finditer(line))
    if len(ms) < 2 or len(names) < 2:
        h = read_hop(line, names[0], section)
        h["g"] = round(best_mass(line), 1)
        return [h]
    # one segment per weight; words shared by the whole line (stage, day) are added to every segment
    starts = [m[2] for m in ms] + [len(line)]
    shared = line
    for m in sorted(ms + [(0, 0, n.start(), n.end()) for n in names], key=lambda x: -x[2]):
        shared = shared[:m[2]] + " " + shared[m[3]:]
    out = []
    for i, m in enumerate(ms):
        seg = line[(0 if i == 0 else starts[i]):starts[i + 1]]
        nm = hop_rx.search(seg)
        if not nm:
            continue
        h = read_hop(seg + " " + shared, nm, section)
        h["g"] = round(m[0], 1)
        h["raw"] = line.strip()[:200]
        out.append(h)
    return out


def heading_of(line):
    """Section name when the line looks like a heading ("Hops:", "MALT BILL", "Dry hop") else None."""
    t = line.strip().strip("#*:=-_ ").strip()
    if not t or len(t) > 40 or MASS_RX.search(t) or re.search(r"\d{2,}", t):
        return None
    for name, rx in SECTIONS:
        if rx.search(t):
            if name == "method" and len(t.split()) > 4:
                return None
            return name
    return None


# ------------------------------------------------------------------ tables (web pages, spreadsheets, Markdown)
COLS = [
    ("batch", r"batch|yield|volume|size"), ("boil", r"boil"), ("og", r"\bog\b|original"), ("fg", r"\bfg\b|final"),
    ("ibu", r"\bibu"), ("abv", r"\babv"), ("eff", r"effic"), ("srm", r"\bsrm\b|\bebc\b|colou?r"),
    ("amount", r"amount|amt|qty|quantity|weight|mass"), ("time", r"time|min"), ("use", r"\buse\b|stage|step|addition"),
    ("form", r"form|type"), ("alpha", r"alpha|\baa\b|α"), ("pct", r"%|percent|share"), ("lab", r"\blab|brand|maker|supplier"),
    ("atten", r"attenu"), ("temp", r"temp"), ("name", r"name|ingredient|variety|malt|hop|grain|fermentable|yeast|item"),
]


def cells(line):
    if "|" not in line:
        return None
    c = [x.strip() for x in line.strip().strip("|").split("|")]
    return c if len(c) >= 2 else None


def col_kind(header):
    h = header.lower()
    for k, rx in COLS:
        if re.search(rx, h):
            return k
    return None


def untable(lines):
    """Rewrite table rows as plain lines the line readers understand, using the header row's column names."""
    out, head = [], None
    for ln in lines:
        c = cells(ln)
        if c is None:
            head = None
            out.append(ln)
            continue
        if all(re.fullmatch(r":?-{2,}:?", x) or not x for x in c):
            continue
        kinds = [col_kind(x) for x in c]
        is_head = sum(1 for x in c if re.search(r"\d", x)) <= len(c) // 4 and sum(1 for k in kinds if k) >= 2
        if is_head:
            head = kinds
            continue
        if not head or len(c) != len(head):
            out.append(" ".join(c))
            continue
        row = dict(zip(head, c))
        if {"og", "fg", "ibu", "abv", "batch"} & set(k for k in head if k) and "name" not in row:
            labels = {"batch": "Batch size", "boil": "Boil", "og": "OG", "fg": "FG", "ibu": "IBU", "abv": "ABV", "eff": "Efficiency", "srm": "SRM"}
            for k, v in row.items():
                if k in labels and v:
                    v = v + (" %" if k in ("abv", "eff") and "%" not in v else "") + (" min" if k == "boil" and "min" not in v else "")
                    out.append(f"{labels[k]}: {v}")
            continue
        parts = []
        if row.get("amount"):
            parts.append(row["amount"])
        if row.get("lab"):
            parts.append(row["lab"])
        parts.append(row.get("name") or "")
        if row.get("srm") and re.fullmatch(r"\d+(?:\.\d+)?", row["srm"]):
            parts.append(f"({row['srm']} °L)")
        if row.get("alpha"):
            parts.append(row["alpha"] + ("" if "%" in row["alpha"] else " %") + " AA")
        if row.get("time"):
            parts.append(row["time"] + ("" if re.search(r"[a-z]", row["time"], re.I) else " min"))
        if row.get("use"):
            parts.append("whirlpool" if re.fullmatch(r"aroma|steep|hop ?stand", row["use"].strip(), re.I) else row["use"])
        if row.get("form"):
            parts.append(row["form"])
        if row.get("pct") and re.fullmatch(r"\d+(?:\.\d+)?", row["pct"]):
            parts.append(row["pct"] + "%")
        out.append(" ".join(p for p in parts if p))
    return out


# extra varieties HopDatabase doesn't list (names only; aroma scores can go in hop_flavours_extra.json)
EXTRA_HOPS = [
    "Strata", "Talus", "Nectaron", "Sabro", "Idaho 7", "Motueka", "Riwaka", "Nelson Sauvin", "Nelson", "Pacific Jade", "Wai-iti",
    "Waimea", "Moutere", "Southern Cross", "Green Bullet", "Kohatu", "Rakau", "Pacifica", "Pacific Gem", "Dr Rudi", "Superdelic",
    "Taiheke", "Wairua", "Krush", "Zeus", "Tomahawk", "CTZ", "Columbus", "Warrior", "Magnum", "Galaxy", "Vic Secret", "Enigma",
    "Ella", "Topaz", "Eclipse", "Melba", "Cashmere", "Zappa", "Luminosa", "Bru-1", "Citra", "Mosaic", "El Dorado", "Azacca", "Loral",
    "Lotus", "Ekuanot", "Mandarina Bavaria", "Hallertau Blanc", "Huell Melon", "Callista", "Ariana", "Sultana", "Denali",
    "Lemondrop", "Comet", "Calypso", "Bravo", "Apollo", "Galena", "Nugget", "Northern Brewer", "Perle", "Tettnanger", "Saaz",
    "Styrian Golding", "East Kent Golding", "EKG", "Fuggle", "Challenger", "Admiral", "Bramling Cross", "Jester", "Olicana",
    "Harlequin", "Ernest", "Simcoe", "Amarillo", "Cascade", "Centennial", "Chinook", "Willamette", "Hallertau Mittelfruh",
    "Mittelfruh", "Hersbrucker", "Spalt", "Sorachi Ace", "Idaho Gem", "HBC 586", "HBC 630", "HBC 1019", "Anchovy", "Peacharine",
    "Veronica", "Vista", "Triumph", "Sterling", "Crystal", "Summit", "Chelan", "Millennium", "Polaris", "Herkules", "Hallertau",
]


# ------------------------------------------------------------------ main
def parse(text, hop_names=()):
    text = normalise_text(text)
    lines = untable([ln.rstrip() for ln in text.splitlines()])
    text = "\n".join(lines)
    hop_names = list(hop_names) + EXTRA_HOPS
    nonblank = [ln for ln in lines if ln.strip()]
    d = {"name": None, "style": None, "batch_l": None, "batch_raw": None, "og": None, "fg": None, "ibu": None, "abv": None,
         "efficiency": None, "boil_min": None, "mash_c": None, "ferment_c": None,
         "malts": [], "hops": [], "yeasts": [], "others": [], "notes": "", "unparsed": [], "warnings": [], "reader": "rules"}
    d.update(find_stats(text, nonblank))
    if re.search(r"all[- ]grain option|partial[- ]mash option|extract option", text, re.I):
        d["warnings"].append("This recipe has an extract and an all-grain version. Check the malts are the all-grain ones.")
    hop_rx = hop_regex(hop_names)
    section, notes, skipping = None, [], False
    for ln in nonblank:
        h = heading_of(ln)
        if h:
            if h == "extract":
                skipping = True
                continue
            if h == "allgrain":
                skipping = False
                section = "malts"
                continue
            skipping = False
            section = h
            continue
        if skipping:
            continue
        line = ln.strip()
        grams = best_mass(line)
        hm = hop_rx.search(line) if hop_rx else None
        # "crystal malt (45 °L)" is a malt even though Crystal is also a hop
        rest = (line[:hm.start()] + " " + line[hm.end():]) if hm else line
        is_malt_word = bool(MALT_WORDS.search(rest)) or bool(re.search(r"\d\s*°\s*l\b|lovibond|\bebc\b|\bsrm\b", line, re.I))
        if hm and is_malt_word and not HOP_CONTEXT.search(rest):
            hm = None
        # yeast lines rarely have a weight
        if (YEAST_RX.search(line) and (section == "yeast" or grams is None or re.search(r"\byeast\b", line, re.I))
                and len(line) < 140 and not (hm and grams)):
            if not re.search(r"\b(pitch|add the|cool|ferment at|aerate|oxygenate|rehydrat)\w*", line, re.I) or line.lower().startswith("yeast"):
                d["yeasts"].append({"name": strip_amounts(re.sub(r"^\s*yeast\s*[:\-]\s*", "", line, flags=re.I))[:120], "raw": line[:200]})
                continue
        if section == "yeast" and grams is None and YEAST_SECTION_OK.search(line) and len(line) < 120:
            d["yeasts"].append({"name": strip_amounts(line)[:120], "raw": line[:200]})
            continue
        if grams is None:
            if MISC_WORDS.search(line) and len(line) <= 80 and section != "method":
                d["others"].append({"name": strip_amounts(line)[:120], "raw": line[:200]})
            elif section == "method" or len(line) > 60 or not re.search(r"\d", line):
                notes.append(line)
            elif section == "malts" and PCT_RX.search(line) and is_malt_word:
                d["malts"].append({"name": strip_amounts(line)[:120], "kg": None, "pct": to_num(PCT_RX.search(line).group(1)),
                                   "color_srm": malt_colour(line), "raw": line[:200]})
            else:
                notes.append(line)
            continue
        if MISC_WORDS.search(line) and not (hm and section in ("hops", "whirlpool", "dryhop")) and not (is_malt_word and section == "malts"):
            d["others"].append({"name": strip_amounts(line)[:120], "raw": line[:200]})
            continue
        hop_like = hm and (section in ("hops", "whirlpool", "dryhop") or HOP_CONTEXT.search(line)
                           or (grams < 500 and section != "malts")) and not (is_malt_word and (section == "malts" or grams >= 400))
        if hop_like:
            d["hops"].extend(read_hops(line, hop_rx, section))
            continue
        if section == "malts" or is_malt_word or grams >= 400:
            pct = PCT_RX.search(line)
            d["malts"].append({"name": strip_amounts(line)[:120], "kg": round(grams / 1000, 3),
                               "pct": to_num(pct.group(1)) if pct else None, "color_srm": malt_colour(line), "raw": line[:200]})
            continue
        if section in ("hops", "whirlpool", "dryhop"):
            hop = read_hop(line, re.match(r"(.*)", strip_amounts(line)), section)
            hop["g"] = round(grams, 1)
            d["hops"].append(hop)
            continue
        if section == "other":
            d["others"].append({"name": strip_amounts(line)[:120], "raw": line[:200]})
            continue
        d["unparsed"].append(line[:200])
    # name: the first short line that isn't a figure or heading
    for ln in nonblank[:6]:
        t = ln.strip().strip("#*=_ ").strip()
        if t and len(t) <= 70 and not MASS_RX.search(t) and not heading_of(t) and not re.match(
                r"^(by|recipe|batch|og|fg|abv|ibu|srm|ebc|efficiency|yield|style)\b", t, re.I) and not re.search(r"\d+\s*%|1\.0\d\d", t):
            d["name"] = re.sub(r"\s*\(.*$", "", t)[:80] or None
            break
    m = re.search(r"\b(west coast ipa|new england ipa|neipa|hazy ipa|hazy pale|double ipa|dipa|session ipa|black ipa|ipa|xpa|"
                  r"american pale ale|pale ale|amber ale|brown ale|stout|porter|pilsner|lager|saison|kolsch|kölsch|hefeweizen|"
                  r"wheat beer|red ale|barleywine|sour|gose|bitter|esb|mild|scotch ale|wee heavy|belgian \w+|tripel|dubbel)\b", text, re.I)
    if m:
        d["style"] = m.group(1)
    d["notes"] = "\n".join(notes)[:6000]
    return d


# ------------------------------------------------------------------ AI / pasted JSON
USES = {"boil": "Boil", "first wort": "First Wort", "fwh": "First Wort", "mash": "Mash", "aroma": "Aroma", "whirlpool": "Aroma",
        "hopstand": "Aroma", "hop stand": "Aroma", "flameout": "Aroma", "dry hop": "Dry Hop", "dryhop": "Dry Hop", "dry-hop": "Dry Hop"}


def _f(v, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def _s(v, n=120):
    return re.sub(r"\s+", " ", str(v)).strip()[:n] if v not in (None, "") else None


def from_json(obj):
    """Clean an AI reply (or JSON pasted by the user) into a draft. Raises ValueError if it isn't a recipe."""
    if not isinstance(obj, dict):
        raise ValueError("The reply isn't a JSON object.")
    if isinstance(obj.get("recipe"), dict):
        obj = obj["recipe"]
    d = {"name": _s(obj.get("name"), 80), "style": _s(obj.get("style"), 60),
         "batch_l": _f(obj.get("batch_l"), 1, 2000), "batch_raw": _s(obj.get("batch_raw")),
         "og": sg(_f(obj.get("og"), 1, 1200)), "fg": sg(_f(obj.get("fg"), 1, 1200)),
         "ibu": _f(obj.get("ibu"), 0, 300), "abv": _f(obj.get("abv"), 0, 25),
         "efficiency": _f(obj.get("efficiency"), 30, 100), "boil_min": _f(obj.get("boil_min"), 0, 240),
         "mash_c": _f(obj.get("mash_c"), 40, 80), "ferment_c": _f(obj.get("ferment_c"), 0, 45),
         "malts": [], "hops": [], "yeasts": [], "others": [], "notes": str(obj.get("notes") or "")[:6000],
         "unparsed": [], "warnings": [_s(w, 300) for w in (obj.get("warnings") or [])[:10] if _s(w)], "reader": obj.get("reader") or "ai"}
    for m in (obj.get("malts") or [])[:40]:
        if isinstance(m, dict) and _s(m.get("name")):
            d["malts"].append({"name": _s(m["name"]), "kg": _f(m.get("kg"), 0, 2000), "pct": _f(m.get("pct"), 0, 100),
                               "color_srm": _f(m.get("color_srm"), 0, 800), "raw": _s(m.get("raw"), 200) or _s(m["name"]),
                               "match": _s(m.get("match"))})
    for h in (obj.get("hops") or [])[:40]:
        if isinstance(h, dict) and _s(h.get("name")):
            use = USES.get(str(h.get("use") or "").strip().lower(), h.get("use") if h.get("use") in USES.values() else None)
            form = str(h.get("form") or "").lower() or None
            d["hops"].append({"name": _s(h["name"]), "g": _f(h.get("g"), 0, 100000), "alpha": _f(h.get("alpha"), 0, 30),
                              "form": form if form in ("pellet", "cryo", "leaf", "extract") else None,
                              "use": use, "time": _f(h.get("time"), 0, 240), "day": int(_f(h.get("day"), 0, 60) or 0) or None,
                              "raw": _s(h.get("raw"), 200) or _s(h["name"]), "match": _s(h.get("match"))})
    for y in (obj.get("yeasts") or [])[:5]:
        if isinstance(y, dict) and _s(y.get("name")):
            d["yeasts"].append({"name": _s(y["name"]), "raw": _s(y.get("raw"), 200) or _s(y["name"]), "match": _s(y.get("match"))})
        elif isinstance(y, str) and y.strip():
            d["yeasts"].append({"name": _s(y), "raw": _s(y, 200)})
    for o in (obj.get("others") or [])[:30]:
        name = _s(o.get("name") if isinstance(o, dict) else o)
        if name:
            d["others"].append({"name": name, "raw": _s((o.get("raw") if isinstance(o, dict) else None) or name, 200)})
    if not d["malts"] and not d["hops"]:
        raise ValueError("No malts or hops were found in the reply.")
    return d


SCHEMA_TEXT = """{
  "name": "recipe name",
  "style": "beer style or null",
  "batch_l": litres into the fermenter (number; US gallons x 3.785, imperial gallons x 4.546) or null,
  "batch_raw": "the batch size text exactly as written",
  "og": 1.065 or null, "fg": 1.012 or null, "ibu": number or null, "abv": number or null,
  "efficiency": brewhouse efficiency % the recipe assumes, or null,
  "boil_min": number or null, "mash_c": main mash temperature in C or null, "ferment_c": fermentation temperature in C or null,
  "malts": [{"name": "...", "kg": number or null, "pct": % of grist or null, "color_srm": Lovibond/SRM (EBC / 1.97) or null,
             "raw": "the line as written", "match": "exact name from MY MALTS that is the same malt, or null"}],
  "hops": [{"name": "variety", "g": grams, "alpha": alpha acid % or null, "form": "pellet|cryo|leaf|extract" or null,
            "use": "Boil|First Wort|Mash|Aroma|Dry Hop" (Aroma = whirlpool, hop stand or flameout),
            "time": minutes (boil or whirlpool) or null, "day": dry hop day of fermentation or null,
            "raw": "the line as written", "match": "exact name from MY HOPS of the same variety and form, or null"}],
  "yeasts": [{"name": "...", "raw": "...", "match": "exact name from MY YEASTS that is the same strain or a close equivalent, or null"}],
  "others": [{"name": "salts, finings, nutrients, fruit, spices and other additions", "raw": "..."}],
  "notes": "the method, mash, fermentation and packaging instructions, shortened to the useful facts",
  "warnings": ["anything unclear or assumed"]
}"""


def prompt(text, lib_names=None):
    """(system, user) messages asking a chat AI to turn the pasted recipe into draft JSON."""
    system = ("You read home-brewing recipes and return them as JSON for a recipe importer. Return only one JSON object, no "
              "commentary. Convert every amount to metric (kg for malts, g for hops, litres, Celsius). When a recipe gives both "
              "imperial and metric amounts use the metric ones. Gallons are US gallons unless the recipe says imperial or UK. "
              "If the recipe has extract and all-grain versions, give the all-grain one. Never invent amounts: use null when the "
              "recipe doesn't say. Use this shape:\n" + SCHEMA_TEXT)
    user = "Recipe:\n<<<\n" + (text or "").strip()[:30000] + "\n>>>"
    if lib_names:
        user += ("\n\nFor the match fields, these are the ingredients I already use. Pick one only when it is the same thing "
                 "(or, for malts and yeasts, a close equivalent); otherwise null.\n"
                 "MY MALTS: " + "; ".join(lib_names.get("fermentables", [])[:200]) + "\n"
                 "MY HOPS: " + "; ".join(lib_names.get("hops", [])[:200]) + "\n"
                 "MY YEASTS: " + "; ".join(lib_names.get("yeasts", [])[:100]))
    return system, user
