"""Build the installable package: dist/brewfather-reports-<version>.zip

    python build_package.py

The zip holds the program, templates, hop data, licence, terms and docs. It never includes
your .env (API key), data/ (your brews, settings and admin password), output/, Next Brew/,
or your personal next_brew.py recipes.
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import version  # noqa: E402

FILES = [
    "server.py", "run.py", "fetch.py", "brewfather.py", "build_report.py", "hop_flavour.py", "layout.py",
    "settings.py", "version.py", "hop_radar.js", "hop_flavours_extra.json",
    "report_template.html", "versions_template.html", "admin_template.html", "terms_template.html",
    "README.md", "DEPLOY.md", "TERMS.md", "LICENSE", "start.bat", "start.sh",
    "hopdata/hops.json", "hopdata/LICENSE-HopDatabase.txt",
]
# exclude.txt and families.txt are not shipped (the Admin page creates them), so unzipping an
# update never overwrites a user's own copies.
STARTERS = {".gitignore": ".env\ndata/\noutput/\n__pycache__/\n"}


def main():
    root = f"brewfather-reports-{version.VERSION}"
    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    path = os.path.join(HERE, "dist", root + ".zip")
    missing = [f for f in FILES if not os.path.exists(os.path.join(HERE, f))]
    if missing:
        raise SystemExit("Missing: " + ", ".join(missing))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            info = zipfile.ZipInfo.from_file(os.path.join(HERE, f), f"{root}/{f}")
            if f == "start.sh":
                info.external_attr = 0o100755 << 16
            with open(os.path.join(HERE, f), "rb") as src:
                z.writestr(info, src.read(), zipfile.ZIP_DEFLATED)
        for name, text in STARTERS.items():
            z.writestr(f"{root}/{name}", text)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert not any(n.endswith((".env", "admin.json", "batches.json", "settings.json", "next_brew.py", "exclude.txt", "families.txt")) for n in names)
    print(f"{len(names)} files -> {path} ({os.path.getsize(path) // 1024} KB)")


if __name__ == "__main__":
    main()
