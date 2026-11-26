"""Build the installable package: dist/brewfather-reports-<version>.zip

    python tools/build_package.py

The zip holds the program, templates, hop data, licence, terms and docs. It never includes
your .env (API key), data/ (your brews, settings and admin password), output/, Next Brew/,
your personal next_brew.py recipes, or your exclude.txt and families.txt.
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import version  # noqa: E402

FILES = [
    # the program
    "app/server.py", "app/run.py", "app/fetch.py", "app/brewfather.py", "app/build_report.py",
    "app/hop_flavour.py", "app/layout.py", "app/settings.py", "app/version.py", "app/paths.py",
    "app/brew_calc.py", "app/recipe_parse.py", "app/recipe_import.py", "app/ai_parse.py", "app/pwa.py", "app/desktop.py",
    # page templates
    "templates/report.html", "templates/versions.html", "templates/admin.html", "templates/terms.html", "templates/import.html",
    "templates/hop_radar.js", "templates/sw.js",
    "templates/icons/icon-192.png", "templates/icons/icon-512.png", "templates/icons/icon-maskable-512.png",
    "templates/icons/app.ico",
    # settings users edit (exclude.txt and families.txt are created by the Admin page, so unzipping
    # an update never overwrites a user's own copies)
    "config/hop_flavours_extra.json",
    # data, docs and launchers
    "hopdata/hops.json", "hopdata/LICENSE-HopDatabase.txt",
    "README.md", "TERMS.md", "LICENSE", "docs/DEPLOY.md", "docs/brew-reports.service", "start.bat", "start.sh",
    "install-app.bat", "install-app.sh",
]
STARTERS = {".gitignore": ".env\ndata/\noutput/\n__pycache__/\n"}
NEVER = (".env", "admin.json", "batches.json", "settings.json", "next_brew.py", "exclude.txt", "families.txt",
         "import_template.json", "imported.json")


def main():
    name = f"brewfather-reports-{version.VERSION}"
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    path = os.path.join(ROOT, "dist", name + ".zip")
    missing = [f for f in FILES if not os.path.exists(os.path.join(ROOT, f))]
    if missing:
        raise SystemExit("Missing: " + ", ".join(missing))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            info = zipfile.ZipInfo.from_file(os.path.join(ROOT, f), f"{name}/{f}")
            if f.endswith(".sh"):
                info.external_attr = 0o100755 << 16
            with open(os.path.join(ROOT, f), "rb") as src:
                z.writestr(info, src.read(), zipfile.ZIP_DEFLATED)
        for f, text in STARTERS.items():
            z.writestr(f"{name}/{f}", text)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert not any(n.endswith(NEVER) for n in names), "private file in package"
    print(f"{len(names)} files -> {path} ({os.path.getsize(path) // 1024} KB)")


if __name__ == "__main__":
    main()
