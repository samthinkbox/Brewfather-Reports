"""Shared header (site navigation) and footer (copyright, licence, terms) for every page.

apply(html, "report.html") adds them to a built page. write_static_pages() writes
admin.html and terms.html into output/.
"""
import html as _html
import os
import re

import paths
import settings
import version

OUT = paths.OUTPUT

CSS = """
.site-head { background: var(--surface, #fcfcfb); border-bottom: 1px solid var(--border, rgba(11,11,11,.1)); }
.site-in { max-width: 1120px; margin: 0 auto; padding: 0 16px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 20px; }
.site-brand { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 15px; color: var(--ink, #0b0b0b);
  text-decoration: none; padding: 12px 0; white-space: nowrap; }
.site-brand svg { width: 22px; height: 22px; color: var(--accent, #2a78d6); flex: none; }
.site-nav { display: flex; gap: 2px; overflow-x: auto; scrollbar-width: none; margin-left: auto; max-width: 100%; }
.site-nav::-webkit-scrollbar { display: none; }
.site-nav a { color: var(--ink-2, #52514e); text-decoration: none; font-size: 13px; padding: 14px 10px 12px; white-space: nowrap;
  border-bottom: 2px solid transparent; }
.site-nav a:hover { color: var(--ink, #0b0b0b); }
.site-nav a[aria-current=page] { color: var(--ink, #0b0b0b); font-weight: 600; border-bottom-color: var(--accent, #2a78d6); }
@media (max-width: 640px) { .site-nav { margin-left: -10px; } .site-nav a { padding: 8px 10px 10px; } .site-brand { padding-bottom: 0; } }
.site-foot { border-top: 1px solid var(--border, rgba(11,11,11,.1)); margin-top: 40px; }
.site-foot .site-in { padding: 16px; font-size: 12px; color: var(--muted, #898781); display: block; }
.site-foot a { color: var(--ink-2, #52514e); }
"""

HOP_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true">'
            '<path d="M12 2c-2 2-2 4 0 6 2-2 2-4 0-6z"/><path d="M7 7c0 3 2 4 5 4-1-3-2-4-5-4zM17 7c0 3-2 4-5 4 1-3 2-4 5-4z"/>'
            '<path d="M6.5 12c0 3 2.5 4.5 5.5 4.5-1-3-2.5-4.5-5.5-4.5zM17.5 12c0 3-2.5 4.5-5.5 4.5 1-3 2.5-4.5 5.5-4.5z"/>'
            '<path d="M8 17c.5 2.5 2 4 4 5 2-1 3.5-2.5 4-5-1.5 0-3 .5-4 1.5C11 17.5 9.5 17 8 17z"/></svg>')


def pages():
    s = settings.load()
    p = [("report.html", "Brew History"), ("versions.html", "Recipe versions")]
    if s.get("next_brew") and os.path.exists(paths.app("next_brew.py")):
        p.append(("next_brew.html", "Next Brew"))
    p.append(("import.html", "Import recipe"))
    p.append(("admin.html", "Admin"))
    return p


def header(active):
    title = _html.escape(settings.load().get("site_title") or version.NAME)
    links = "".join(f'<a href="{href}"{" aria-current=page" if href == active else ""}>{label}</a>' for href, label in pages())
    return (f'<header class="site-head"><div class="site-in"><a class="site-brand" href="report.html">{HOP_ICON}{title}</a>'
            f'<nav class="site-nav" aria-label="Pages">{links}</nav></div></header>')


def footer():
    return (f'<footer class="site-foot"><div class="site-in">{version.COPYRIGHT} · {version.NAME} {version.VERSION} · '
            f'{version.LICENSE} licence · <a href="terms.html">Terms and licence</a> · '
            'Not affiliated with or endorsed by Brewfather. Hop aroma data from '
            '<a href="https://github.com/kasperg3/HopDatabase" rel="noopener">HopDatabase</a> (MIT).</div></footer>')


def apply(page, active):
    page = page.replace("</style>", CSS + "</style>", 1)
    page = re.sub(r"<body([^>]*)>", lambda m: m.group(0) + header(active), page, count=1)
    return page.replace("</main>", "</main>" + footer(), 1)


# ------------------------------------------------------------------ terms page from TERMS.md + LICENSE
def _inline(s):
    s = _html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return re.sub(r"\[(.+?)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', s)


def markdown(text):
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()
    for line in text.splitlines():
        t = line.strip()
        m = re.match(r"(#{1,3}) (.*)", t)
        if m:
            flush()
            n = len(m.group(1)) + 1
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
        elif t.startswith(("- ", "* ")):
            if para:
                flush()
            items.append(t[2:])
        elif not t:
            flush()
        elif items and line.startswith("  "):
            items[-1] += " " + t
        else:
            para.append(t)
    flush()
    return "\n".join(out)


def write_static_pages():
    os.makedirs(OUT, exist_ok=True)
    with open(paths.template("admin.html"), encoding="utf-8") as f:
        admin = f.read()
    with open(os.path.join(OUT, "admin.html"), "w", encoding="utf-8") as f:
        f.write(apply(admin, "admin.html"))
    with open(paths.template("import.html"), encoding="utf-8") as f:
        imp = f.read()
    with open(paths.template("hop_radar.js"), encoding="utf-8") as f:
        imp = imp.replace("/*__HOP_RADAR__*/", f.read())
    with open(os.path.join(OUT, "import.html"), "w", encoding="utf-8") as f:
        f.write(apply(imp, "import.html"))
    with open(os.path.join(paths.ROOT, "TERMS.md"), encoding="utf-8") as f:
        terms = markdown(f.read())
    with open(os.path.join(paths.ROOT, "LICENSE"), encoding="utf-8") as f:
        lic = _html.escape(f.read())
    with open(paths.template("terms.html"), encoding="utf-8") as f:
        tpl = f.read()
    page = tpl.replace("<!--__TERMS__-->", terms).replace("<!--__LICENSE__-->", lic)
    with open(os.path.join(OUT, "terms.html"), "w", encoding="utf-8") as f:
        f.write(apply(page, "terms.html"))
