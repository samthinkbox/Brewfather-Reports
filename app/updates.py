"""Is there a newer version? Asks GitHub for the latest release of version.REPO, at most once a day.

Only a plain GET to the GitHub API is made: nothing about you or your brews is sent. The answer is kept in
data/update.json. Turn it off on the Admin page (setting "update_check").
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import version  # noqa: E402

CACHE = os.path.join(paths.DATA, "update.json")
EVERY = 24 * 3600
# BFR_UPDATE_API points the check at a test server (https, or http on this computer only)
API = os.environ.get("BFR_UPDATE_API") or f"https://api.github.com/repos/{version.REPO}/releases/latest"


def parse(v):
    """'v1.10.2' -> (1, 10, 2); None if it isn't a version number."""
    m = re.fullmatch(r"\s*v?(\d+(?:\.\d+){0,3})\s*", str(v or ""))
    return tuple(int(x) for x in m.group(1).split(".")) + (0,) * (3 - m.group(1).count(".")) if m else None


def newer(latest, current=version.VERSION):
    a, b = parse(latest), parse(current)
    return bool(a and b and a > b)


def _safe_api(url):
    u = urllib.parse.urlparse(url)
    return u.scheme == "https" or (u.scheme == "http" and (u.hostname or "") in ("localhost", "127.0.0.1", "::1"))


def fetch():
    """The latest release from GitHub, as the dict kept in the cache."""
    out = {"checked": time.time(), "latest": None, "url": "", "notes": "", "error": ""}
    if not _safe_api(API):
        out["error"] = "The update address must start with https://."
        return out
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"Brewfather-Reports/{version.VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            j = json.loads(r.read(500_000) or b"{}")
    except urllib.error.HTTPError as e:
        out["error"] = ("No versions have been published on GitHub yet." if e.code == 404
                        else f"GitHub answered {e.code}. Try again later.")
        return out
    except (OSError, ValueError):
        out["error"] = "Couldn't reach GitHub. Check the internet connection and try again."
        return out
    tag = str(j.get("tag_name") or "")
    if not parse(tag):
        out["error"] = "The latest release on GitHub has no version number."
        return out
    url = str(j.get("html_url") or "")
    out.update(latest=tag.lstrip("v"), notes=str(j.get("body") or "")[:3000],
               url=url if url.startswith("https://github.com/") else f"https://github.com/{version.REPO}/releases/latest")
    return out


def check(force=False):
    """What the Admin page shows. Asks GitHub when forced or when the last answer is a day old."""
    s = {}
    try:
        with open(CACHE, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        pass
    if force or time.time() - float(s.get("checked") or 0) > EVERY:
        s = fetch()
        try:
            os.makedirs(paths.DATA, exist_ok=True)
            with open(CACHE, "w", encoding="utf-8") as f:
                json.dump(s, f, indent=1)
        except OSError:
            pass
    return {"current": version.VERSION, "latest": s.get("latest"), "newer": newer(s.get("latest")),
            "url": s.get("url") or "", "notes": s.get("notes") or "", "checked": s.get("checked"),
            "error": s.get("error") or ""}
