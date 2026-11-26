"""Files that let a browser install the site as an app: manifest, service worker and icons.

server.py serves them without sign-in (they hold no brew data). layout.HEAD links them from every page.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import settings  # noqa: E402
import version  # noqa: E402

ICONS = {"icon-192.png", "icon-512.png", "icon-maskable-512.png"}
FILES = ICONS | {"manifest.webmanifest", "sw.js"}


def manifest():
    title = settings.load().get("site_title") or version.NAME
    return {
        "name": title,
        "short_name": "Brew Reports" if title == version.NAME else title[:24],
        "description": "Reports on your brewing history from Brewfather, and a recipe importer.",
        "id": "./", "start_url": "./", "scope": "./",
        "display": "standalone",
        "background_color": "#f9f9f7", "theme_color": "#fcfcfb",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Import recipe", "url": "import.html"},
            {"name": "Recipe versions", "url": "versions.html"},
            {"name": "Admin", "url": "admin.html"},
        ],
    }


def get(name):
    """(body bytes, content type) for one of FILES."""
    if name == "manifest.webmanifest":
        return json.dumps(manifest(), indent=1).encode("utf-8"), "application/manifest+json"
    if name == "sw.js":
        with open(paths.template("sw.js"), "rb") as f:
            return f.read(), "text/javascript; charset=utf-8"
    with open(os.path.join(paths.ICONS, name), "rb") as f:
        return f.read(), "image/png"
