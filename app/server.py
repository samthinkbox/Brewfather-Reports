"""Brewfather Reports web server: the report pages plus the Admin page.

    python app/server.py                         # http://127.0.0.1:8642
    python app/server.py --port 8080
    python app/server.py --proxy                 # behind nginx/Caddy/IIS: trust X-Forwarded-For and -Proto
    python app/server.py --host 0.0.0.0          # listen on every network interface (use HTTPS in front of it)

Standard library only. The first time it runs (no admin password yet) it prints a one-time
setup code. Open /admin.html, enter the code, your Brewfather User ID and API key, and choose
an admin password.
"""
import argparse
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ai_parse  # noqa: E402
import build_report  # noqa: E402
import hop_flavour  # noqa: E402
import layout  # noqa: E402
import paths  # noqa: E402
import pwa  # noqa: E402
import recipe_import  # noqa: E402
import recipe_parse  # noqa: E402
import settings  # noqa: E402
import updates  # noqa: E402
import version  # noqa: E402
from brewfather import Brewfather, load_env  # noqa: E402

ROOT = paths.ROOT
OUT = paths.OUTPUT
DATA = paths.DATA
ADMIN_FILE = os.path.join(DATA, "admin.json")
ENV_FILE = paths.ENV
BATCHES = paths.BATCHES
UPLOADED = os.path.join(paths.NEXT_BREW_DIR, "uploaded.json")
SESSION_SECONDS = 12 * 3600
COOKIE = "bfr_session"
EDITABLE = {"families.txt": "text", "exclude.txt": "text", "hop_flavours_extra.json": "json"}
STATIC = {".html": "text/html; charset=utf-8", ".csv": "text/csv; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}
ALWAYS_PUBLIC = {"admin.html", "terms.html", "import.html"}  # pages with no brew data in them
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
PY = [sys.executable, "-I"]
JOBS = {
    "refresh": ("Fetch from Brewfather and rebuild", PY + [paths.app("run.py")]),
    "rebuild": ("Rebuild pages from saved data", PY + [paths.app("run.py"), "--offline"]),
    "hops": ("Update hop data and rebuild", PY + [paths.app("run.py"), "--offline", "--update-hops"]),
    "upload": ("Send Next Brew recipes to Brewfather", PY + [paths.app("next_brew.py"), "--upload"]),
}

LOCK = threading.Lock()
SESSIONS = {}          # token -> expiry time
FAILS = {}             # ip -> [failed attempts, locked until]
JOB = {"name": None, "label": None, "running": False, "started": None, "finished": None, "ok": None, "log": ""}
STATE = {"setup_code": None, "proxy": False, "last_auto": 0.0, "stop_token": "", "server": None, "last_seen": time.time()}
_cache = {"mtime": None, "batches": []}


# ------------------------------------------------------------------ helpers
def now():
    return time.time()


def mtime(path):
    return os.path.getmtime(path) if os.path.exists(path) else None


def load_batches():
    m = mtime(BATCHES)
    if m != _cache["mtime"]:
        try:
            with open(BATCHES, encoding="utf-8") as f:
                _cache["batches"] = json.load(f)
        except (OSError, ValueError):
            _cache["batches"] = []
        _cache["mtime"] = m
    return _cache["batches"]


def creds():
    env = load_env(ENV_FILE)
    return env.get("BREWFATHER_USER_ID") or "", env.get("BREWFATHER_API_KEY") or ""


def set_env(values):
    """Write or (with value None) remove keys in .env, keeping every other line."""
    keep = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            keep = [ln.rstrip("\n") for ln in f if ln.split("=", 1)[0].strip() not in values]
    keep += [f"{k}={v}" for k, v in values.items() if v is not None]
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + "\n")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass


def save_creds(user_id, api_key):
    set_env({"BREWFATHER_USER_ID": user_id, "BREWFATHER_API_KEY": api_key})


# ------------------------------------------------------------------ recipe import
RECIPE_LIST = {"at": 0, "items": []}


def brewfather_recipes(force=False):
    """Your Brewfather recipes (name, id, folder), cached for 10 minutes to save API calls."""
    if force or now() - RECIPE_LIST["at"] > 600:
        rows = Brewfather().list_all("/recipes", include="path")
        RECIPE_LIST["items"] = sorted(({"id": r.get("_id"), "name": (r.get("name") or "").strip(), "path": r.get("path") or ""}
                                       for r in rows), key=lambda r: (r["path"], r["name"].lower()))
        RECIPE_LIST["at"] = now()
    return RECIPE_LIST["items"]


def import_info():
    ctx = recipe_import.context(load_batches())
    return {"ready": bool(ctx["template"]), "standards": recipe_import.standards(ctx) if ctx["template"] else None,
            "ai": ai_parse.public_info(), "imported": recipe_import.imported()[:10], "credsSet": all(creds())}


def import_result(d, opts, applied=None):
    """The draft with its open questions and the scaled recipe (open questions take their recommended answer
    in the preview until they are answered)."""
    ctx = recipe_import.context(load_batches())
    recipe_import.auto_match(d, ctx)
    qs = recipe_import.questions(d, ctx)
    preview = json.loads(json.dumps(d))
    recipe_import.answer_defaults(preview, ctx)
    view = recipe_import.build(preview, opts, ctx)[0]
    return {"ok": True, "draft": d, "questions": qs, "applied": applied or [], "view": view, "opts": opts,
            "waterGuess": recipe_import.guess_style_water(d)}


def test_creds(user_id, api_key):
    """None when Brewfather accepts them, otherwise an error message."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", user_id or "") or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", api_key or ""):
        return "That doesn't look like a Brewfather User ID and API key (Brewfather › Settings › API)."
    try:
        Brewfather(user_id, api_key).get("/batches", limit=1)
        return None
    except SystemExit as e:
        msg = str(e)
        if " 401 " in msg or " 403 " in msg:
            return "Brewfather rejected the User ID or API key. Check them, and that the key has read access to batches and recipes."
        return "Couldn't reach Brewfather: " + msg[:300]
    except Exception as e:  # network errors
        return f"Couldn't reach Brewfather: {e}"


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt, hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 240_000).hex()


def set_password(pw):
    salt, h = hash_pw(pw)
    os.makedirs(DATA, exist_ok=True)
    with open(ADMIN_FILE, "w", encoding="utf-8") as f:
        json.dump({"salt": salt, "hash": h, "set": datetime.now().isoformat(timespec="seconds")}, f)
    try:
        os.chmod(ADMIN_FILE, 0o600)
    except OSError:
        pass


def check_password(pw):
    try:
        with open(ADMIN_FILE, encoding="utf-8") as f:
            a = json.load(f)
    except (OSError, ValueError):
        return False
    return hmac.compare_digest(hash_pw(pw or "", a["salt"])[1], a["hash"])


def setup_needed():
    return not os.path.exists(ADMIN_FILE)


def pw_problem(pw):
    if len(pw or "") < 10:
        return "Use at least 10 characters for the admin password."
    return None


# ------------------------------------------------------------------ jobs
def start_job(name):
    if name not in JOBS:
        return "Unknown action."
    if name == "upload" and not os.path.exists(paths.app("next_brew.py")):
        return "Next Brew isn't set up in this installation."
    if name in ("refresh", "upload") and not all(creds()):
        return "Add your Brewfather User ID and API key first."
    with LOCK:
        if JOB["running"]:
            return f"Busy: {JOB['label']} is still running."
        label, cmd = JOBS[name]
        JOB.update(name=name, label=label, running=True, started=now(), finished=None, ok=None, log="")
    threading.Thread(target=_run_job, args=(cmd,), daemon=True).start()
    return None


def _run_job(cmd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    ok = False
    try:
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                             text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            with LOCK:
                JOB["log"] = (JOB["log"] + line)[-100_000:]
        ok = p.wait() == 0
    except Exception as e:
        with LOCK:
            JOB["log"] += f"\n{e}\n"
    with LOCK:
        JOB.update(running=False, finished=now(), ok=ok)
        JOB["log"] += "\nDone.\n" if ok else "\nFailed. See the messages above.\n"


def scheduler():
    while True:
        time.sleep(60)
        try:
            hours = int(settings.load().get("auto_refresh_hours") or 0)
            if hours <= 0 or not all(creds()) or JOB["running"]:
                continue
            due = hours * 3600
            if now() - (mtime(BATCHES) or 0) >= due and now() - STATE["last_auto"] >= due:
                STATE["last_auto"] = now()
                start_job("refresh")
        except Exception as e:  # keep the scheduler alive
            sys.stderr.write(f"scheduler: {e}\n")


def idle_watch(seconds):
    """--idle-exit: stop the server once no page has been open (no request at all) for this long, unless a job is
    running. Open pages ping /api/ping every minute (layout.HEAD), so this is a few minutes after the last window closes."""
    while True:
        time.sleep(15)
        if now() - STATE["last_seen"] > seconds and not JOB["running"]:
            print("No app window open: stopping.")
            sys.stdout.flush()
            STATE["server"].shutdown()
            return


# ------------------------------------------------------------------ exclusions
EXCLUDE = paths.config("exclude.txt")
EXCLUDE_HEADER = ("# Batches to leave out of the reports (and the CSVs).\n"
                  "# One batch number per line. Anything after the number is just a note for you.\n"
                  "# Lines starting with # are ignored. You can also add #noreport to a batch note in Brewfather.\n\n")


def set_excluded(no, exclude, label=""):
    lines = []
    if os.path.exists(EXCLUDE):
        with open(EXCLUDE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    else:
        lines = EXCLUDE_HEADER.splitlines()

    def is_no(ln):
        m = re.match(r"\s*(\d+)", ln)
        return bool(m) and not ln.lstrip().startswith("#") and int(m.group(1)) == no
    lines = [ln for ln in lines if not is_no(ln)]
    if exclude:
        lines.append(f"{no}  {label}".rstrip())
    with open(EXCLUDE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def brews_list():
    ex = build_report.load_excludes()
    out = []
    for b in load_batches():
        out.append({"no": b.get("batchNo"), "name": (b.get("name") or "").strip(), "date": build_report.iso(b.get("brewDate")),
                    "status": b.get("status"), "excluded": b.get("batchNo") in ex,
                    "noreport": build_report.flagged_in_brewfather(b)})
    out.sort(key=lambda x: -(x["no"] or 0))
    return out


# ------------------------------------------------------------------ status
APP_SEEN = os.path.join(DATA, "app_seen.json")
APP_SEEN_DAYS = 60  # the Install as app button comes back if the installed app hasn't been opened for this long


def app_installed():
    """Has the installed app (or the shortcut's app window) been opened lately, in any browser? Browsers don't tell
    each other, so the pages tell the server (layout.HEAD -> /api/app-seen) and the Admin page asks it here."""
    try:
        with open(APP_SEEN, encoding="utf-8") as f:
            return now() - float(json.load(f).get("seen") or 0) < APP_SEEN_DAYS * 86400
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def status(logged_in):
    user, key = creds()
    s = {"name": version.NAME, "version": version.VERSION, "copyright": version.COPYRIGHT,
         "setupNeeded": setup_needed(), "loggedIn": logged_in, "credsSet": bool(user and key),
         "appInstalled": app_installed()}
    if not logged_in:
        return s
    batches = load_batches()
    try:
        sources = [x["name"] for x in build_report.water_sources(batches)]
    except Exception:
        sources = []
    uploaded = {}
    if os.path.exists(UPLOADED):
        try:
            with open(UPLOADED, encoding="utf-8") as f:
                uploaded = json.load(f)
        except (OSError, ValueError):
            pass
    s.update({
        "userId": (user[:4] + "…" + user[-3:]) if len(user) > 8 else ("set" if user else ""),
        "settings": settings.load(),
        "data": {"batches": len(batches), "fetched": mtime(BATCHES), "built": mtime(os.path.join(OUT, "report.html"))},
        "hops": hop_flavour.info(),
        "waterSources": sources,
        "nextBrew": {"available": os.path.exists(paths.app("next_brew.py")), "uploaded": uploaded},
        "ai": ai_parse.public_info(),
        "downloads": [n for n in ("brews.csv", "hops.csv", "malts.csv") if os.path.exists(os.path.join(OUT, n))],
        "job": job_view(tail=True),
    })
    return s


def job_view(tail=False):
    with LOCK:
        j = dict(JOB)
    if tail:
        j["log"] = j["log"][-4000:]
    return j


# ------------------------------------------------------------------ HTTP
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "BrewfatherReports/" + version.VERSION
    sys_version = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.ip(), fmt % args))

    def ip(self):
        if STATE["proxy"] and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[0].strip()
        return self.client_address[0]

    def https(self):
        return STATE["proxy"] and self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    # ---- sessions
    def token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def logged_in(self):
        t = self.token()
        with LOCK:
            exp = SESSIONS.get(t) if t else None
            if exp and exp > now():
                SESSIONS[t] = now() + SESSION_SECONDS
                return True
            if t in SESSIONS:
                SESSIONS.pop(t, None)
        return False

    def new_session(self):
        t = secrets.token_urlsafe(32)
        with LOCK:
            for k in [k for k, e in SESSIONS.items() if e < now()]:
                SESSIONS.pop(k)
            SESSIONS[t] = now() + SESSION_SECONDS
        return self.cookie(t, SESSION_SECONDS)

    def cookie(self, value, age):
        return (f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}" + ("; Secure" if self.https() else ""))

    # ---- responses
    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, code=200, headers=None):
        self.send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", headers)

    def err(self, msg, code=400):
        self.json({"ok": False, "error": msg}, code)

    def redirect(self, where):
        self.send(302, b"", headers={"Location": where})

    # ---- GET
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        STATE["last_seen"] = now()
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/api/"):
            return self.api_get(u.path, urllib.parse.parse_qs(u.query))
        name = u.path.lstrip("/")
        if not name:
            return self.redirect("report.html" if os.path.exists(os.path.join(OUT, "report.html")) and not setup_needed() else "admin.html")
        if name in pwa.FILES:  # app install files, no brew data in them
            body, ctype = pwa.get(name)
            return self.send(200, body, ctype)
        ext = os.path.splitext(name)[1].lower()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or ext not in STATIC or name.startswith("."):
            return self.send(404, b"Not found")
        path = os.path.join(OUT, name)
        if not os.path.isfile(path):
            if name in ALWAYS_PUBLIC:
                layout.write_static_pages()
            if not os.path.isfile(path):
                return self.send(404, b"Not found. Run a refresh from the Admin page.")
        if name not in ALWAYS_PUBLIC and not settings.load().get("public_reports") and not self.logged_in():
            return self.redirect("admin.html?next=" + urllib.parse.quote(name))
        with open(path, "rb") as f:
            body = f.read()
        hdr = {"Content-Disposition": f'attachment; filename="{name}"'} if ext == ".csv" else None
        self.send(200, body, STATIC[ext], hdr)

    def api_get(self, path, q):
        if path == "/api/status":
            return self.json(status(self.logged_in()))
        if path == "/api/ping":  # open pages say they're still there (see idle_watch)
            return self.send(204)
        if not self.logged_in():
            return self.err("Please sign in.", 401)
        if path == "/api/job":
            return self.json(job_view())
        if path == "/api/brews":
            return self.json(brews_list())
        if path == "/api/file":
            name = (q.get("name") or [""])[0]
            if name not in EDITABLE:
                return self.err("Unknown file.", 404)
            p = paths.config(name)
            text = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
            return self.json({"name": name, "content": text})
        if path == "/api/update":  # newer version on GitHub? (daily, or now with ?force=1)
            force = bool(q.get("force"))
            if not force and not settings.load().get("update_check"):
                return self.json({"current": version.VERSION, "off": True})
            return self.json(updates.check(force))
        if path == "/api/import/info":
            return self.json(import_info())
        if path == "/api/recipes":
            if not all(creds()):
                return self.err("Add your Brewfather User ID and API key first.")
            try:
                return self.json({"ok": True, "recipes": brewfather_recipes(bool(q.get("refresh")))})
            except SystemExit as e:
                return self.err("Brewfather: " + str(e)[:300], 502)
        if path == "/api/download/batches.json":
            if not os.path.exists(BATCHES):
                return self.err("No data yet.", 404)
            with open(BATCHES, "rb") as f:
                body = f.read()
            return self.send(200, body, "application/json; charset=utf-8",
                             {"Content-Disposition": f'attachment; filename="brewfather-batches-{datetime.now():%Y%m%d}.json"'})
        return self.err("Not found.", 404)

    # ---- POST
    def do_POST(self):
        STATE["last_seen"] = now()
        if self.headers.get("X-BFR") != "1":  # blocks cross-site form posts
            return self.err("Forbidden.", 403)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            return self.err("Too large.", 413)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.err("Bad request.")
        path = urllib.parse.urlparse(self.path).path
        try:
            return self.api_post(path, body)
        except Exception as e:
            sys.stderr.write(f"{path}: {e!r}\n")
            return self.err("Something went wrong on the server.", 500)

    def throttled(self):
        f = FAILS.get(self.ip())
        return bool(f and f[1] > now())

    def failed(self):
        f = FAILS.setdefault(self.ip(), [0, 0])
        f[0] += 1
        if f[0] >= 5:
            f[0], f[1] = 0, now() + 300
        time.sleep(0.6)

    def api_post(self, path, b):
        if path == "/api/app-seen":  # a page was opened as an installed app (no brew data; just a time)
            os.makedirs(DATA, exist_ok=True)
            with open(APP_SEEN, "w", encoding="utf-8") as f:
                json.dump({"seen": now()}, f)
            return self.json({"ok": True})
        if path == "/api/stop":  # used by desktop.py --stop; only works with the token it started the server with
            tok = STATE.get("stop_token") or ""
            if (len(tok) < 32 or self.client_address[0] not in ("127.0.0.1", "::1")
                    or not hmac.compare_digest(str(b.get("token") or ""), tok)):
                return self.err("Forbidden.", 403)
            threading.Thread(target=STATE["server"].shutdown, daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/setup":
            if not setup_needed():
                return self.err("Setup is already done. Sign in instead.", 409)
            if self.throttled():
                return self.err("Too many attempts. Wait five minutes.", 429)
            if not hmac.compare_digest(str(b.get("code") or "").strip().upper(), STATE["setup_code"] or "-"):
                self.failed()
                return self.err("The setup code is wrong. It is shown in the window where the server is running.")
            if not b.get("accept"):
                return self.err("Please accept the terms to continue.")
            problem = pw_problem(b.get("password"))
            if problem:
                return self.err(problem)
            user, key = str(b.get("userId") or "").strip(), str(b.get("apiKey") or "").strip()
            if not (user or key) and all(creds()):
                user, key = creds()  # keep the key already in .env
            problem = test_creds(user, key)
            if problem:
                return self.err(problem)
            save_creds(user, key)
            set_password(b["password"])
            STATE["setup_code"] = None
            start_job("refresh")
            return self.json({"ok": True}, headers={"Set-Cookie": self.new_session()})

        if path == "/api/login":
            if self.throttled():
                return self.err("Too many attempts. Wait five minutes.", 429)
            if setup_needed() or not check_password(b.get("password")):
                self.failed()
                return self.err("Wrong password.", 401)
            FAILS.pop(self.ip(), None)
            return self.json({"ok": True}, headers={"Set-Cookie": self.new_session()})

        if path == "/api/logout":
            t = self.token()
            with LOCK:
                SESSIONS.pop(t, None)
            return self.json({"ok": True}, headers={"Set-Cookie": self.cookie("", 0)})

        if not self.logged_in():
            return self.err("Please sign in.", 401)

        if path == "/api/password":
            if not check_password(b.get("current")):
                self.failed()
                return self.err("The current password is wrong.")
            problem = pw_problem(b.get("new"))
            if problem:
                return self.err(problem)
            set_password(b["new"])
            with LOCK:
                SESSIONS.clear()
            return self.json({"ok": True}, headers={"Set-Cookie": self.new_session()})

        if path == "/api/credentials":
            user, key = str(b.get("userId") or "").strip(), str(b.get("apiKey") or "").strip()
            problem = test_creds(user, key)
            if problem:
                return self.err(problem)
            save_creds(user, key)
            return self.json({"ok": True})

        if path == "/api/test":
            user, key = creds()
            problem = test_creds(user, key) if user and key else "No Brewfather User ID and API key saved yet."
            return self.json({"ok": not problem, "error": problem})

        if path == "/api/job":
            problem = start_job(str(b.get("name")))
            return self.err(problem, 409) if problem else self.json({"ok": True})

        if path == "/api/settings":
            pat = b.get("water_pattern")
            if pat:
                try:
                    re.compile(pat)
                except re.error:
                    return self.err("The water source pattern isn't valid. Use plain words separated by |.")
            s = settings.save(b)
            if os.path.exists(BATCHES):
                start_job("rebuild")
            else:
                layout.write_static_pages()
            return self.json({"ok": True, "settings": s})

        if path == "/api/file":
            name, content = b.get("name"), b.get("content")
            if name not in EDITABLE or not isinstance(content, str) or len(content) > 200_000:
                return self.err("Unknown file.")
            if EDITABLE[name] == "json":
                try:
                    json.loads(content)
                except ValueError as e:
                    return self.err(f"That isn't valid JSON: {e}")
            os.makedirs(paths.CONFIG, exist_ok=True)
            with open(paths.config(name), "w", encoding="utf-8") as f:
                f.write(content.replace("\r\n", "\n"))
            if os.path.exists(BATCHES):
                start_job("rebuild")
            return self.json({"ok": True})

        if path == "/api/import/read":
            text = str(b.get("text") or "")
            if not text.strip():
                return self.err("Paste a recipe first.")
            if len(text) > 60_000:
                return self.err("That's too long for one recipe (60,000 characters at most).")
            ctx = recipe_import.context(load_batches())
            if not ctx["template"]:
                return self.err("Fetch your brews from Brewfather first (Admin page): your latest brew is used as your standard.")
            try:
                pasted = None
                if "{" in text and re.search(r'"(malts|hops)"\s*:', text):
                    try:
                        pasted = ai_parse.extract_json(text)
                    except ValueError:
                        return self.err("That looks like JSON from a chat AI but it isn't complete. Copy the whole answer.")
                if pasted is not None:
                    d = recipe_parse.from_json(pasted)
                    d["reader"] = "your chat AI"
                elif b.get("mode") == "ai":
                    d = ai_parse.read(text, recipe_import.lib_names(ctx))
                else:
                    d = recipe_parse.parse(text, recipe_import.hop_names(ctx))
            except ai_parse.AIError as e:
                return self.err(str(e), 502)
            except ValueError as e:
                return self.err(f"Couldn't read that as a recipe: {e}")
            if not d["malts"] and not d["hops"]:
                return self.err("No malts or hops were found. Check the paste includes the ingredient list"
                                + (", or try the AI reader." if ai_parse.config()["ready"] else "."))
            recipe_import.auto_match(d, ctx)
            applied = recipe_import.answer_defaults(d, ctx) if b.get("auto") else []
            return self.json(import_result(d, recipe_import.clean_opts(b.get("opts")), applied))

        if path == "/api/import/check":
            try:
                d = recipe_import.sanitize(b.get("draft") or {})
            except (ValueError, TypeError, AttributeError) as e:
                return self.err(f"That recipe can't be used: {e}")
            applied = recipe_import.answer_defaults(d, recipe_import.context(load_batches())) if b.get("auto") else []
            return self.json(import_result(d, recipe_import.clean_opts(b.get("opts")), applied))

        if path == "/api/import/prompt":
            ctx = recipe_import.context(load_batches())
            system, user = recipe_parse.prompt(str(b.get("text") or "")[:60_000], recipe_import.lib_names(ctx))
            return self.json({"ok": True, "prompt": system + "\n\n" + user})

        if path == "/api/import/upload":
            if not all(creds()):
                return self.err("Add your Brewfather User ID and API key first.")
            ctx = recipe_import.context(load_batches())
            try:
                d = recipe_import.sanitize(b.get("draft") or {})
            except (ValueError, TypeError, AttributeError) as e:
                return self.err(f"That recipe can't be used: {e}")
            recipe_import.auto_match(d, ctx)
            open_qs = recipe_import.questions(d, ctx)
            if open_qs:
                return self.err(f"Answer the {len(open_qs)} open question(s) first.")
            _view, recipe = recipe_import.build(d, recipe_import.clean_opts(b.get("opts")), ctx)
            name = re.sub(r"\s+", " ", str(b.get("name") or "")).strip()[:80]
            if name:
                recipe["name"] = name
            try:
                done = recipe_import.upload(recipe)
            except SystemExit as e:
                msg = str(e)
                if " 403 " in msg or " 401 " in msg:
                    return self.err("Brewfather refused: the API key needs write access to recipes "
                                    "(Brewfather \u203a Settings \u203a API: create a key that can edit recipes).", 502)
                return self.err("Brewfather: " + msg[:300], 502)
            RECIPE_LIST["at"] = 0
            return self.json({"ok": True, "done": done})

        if path == "/api/import/template":
            rid = str(b.get("id") or "").strip()
            if rid and not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", rid):
                return self.err("Bad recipe id.")
            name = ""
            if rid:
                if not all(creds()):
                    return self.err("Add your Brewfather User ID and API key first.")
                try:
                    r = Brewfather().get(f"/recipes/{rid}")
                except SystemExit as e:
                    return self.err("Brewfather: " + str(e)[:300], 502)
                if not isinstance(r, dict) or not r.get("equipment"):
                    return self.err("That recipe has no equipment profile, so it can't be your standard.")
                recipe_import.save_template(r)
                name = (r.get("name") or "").strip()
            settings.save({"import_template_id": rid, "import_template_name": name})
            return self.json({"ok": True, "standards": import_info()["standards"]})

        if path == "/api/ai":
            prov = str(b.get("provider") or "")
            if prov and prov not in ai_parse.PROVIDERS:
                return self.err("Unknown AI provider.")
            model = re.sub(r"[^\w.:/@+-]", "", str(b.get("model") or ""))[:100]
            endpoint = str(b.get("endpoint") or "").strip()[:200]
            if endpoint:
                problem = ai_parse.check_endpoint(endpoint)
                if problem:
                    return self.err(problem)
            key = str(b.get("apiKey") or "").strip()
            if key and (len(key) > 400 or not re.fullmatch(r"[\x21-\x7e]+", key)):
                return self.err("That doesn't look like an API key.")
            if b.get("forget") in ai_parse.PROVIDERS:
                set_env({ai_parse.env_key(b["forget"]): None})
            else:
                settings.save({"ai_provider": prov, "ai_model": model if prov else "", "ai_endpoint": endpoint if prov else ""})
                if prov and key:
                    set_env({ai_parse.env_key(prov): key})
            return self.json({"ok": True, "ai": ai_parse.public_info()})

        if path == "/api/ai/test":
            problem = ai_parse.test()
            return self.json({"ok": not problem, "error": problem})

        if path == "/api/exclude":
            try:
                no = int(b.get("no"))
            except (TypeError, ValueError):
                return self.err("Bad batch number.")
            label = next((x["name"] for x in brews_list() if x["no"] == no), "")
            set_excluded(no, bool(b.get("exclude")), re.sub(r"[\r\n]+", " ", label)[:80])
            return self.json({"ok": True})

        return self.err("Not found.", 404)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second server share the port (and silently take some requests),
    # so ask for the port exclusively there instead.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        import socket
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    ap = argparse.ArgumentParser(description="Brewfather Reports web server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--proxy", action="store_true", help="trust X-Forwarded-For/-Proto from a reverse proxy")
    ap.add_argument("--idle-exit", type=int, default=0, metavar="SECONDS",
                    help="stop when no page has been open for this long (used by the desktop launcher)")
    a = ap.parse_args()
    STATE["proxy"] = a.proxy
    os.makedirs(OUT, exist_ok=True)
    layout.write_static_pages()
    # The desktop launcher (desktop.py) passes its own setup code, which it gives to the app window, and a
    # token it can later use to stop this server. Neither is passed on to the job subprocesses.
    given = os.environ.pop("BFR_SETUP_CODE", "").strip().upper()
    STATE["stop_token"] = os.environ.pop("BFR_STOP_TOKEN", "")
    if setup_needed():
        STATE["setup_code"] = (given if re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){2}", given)
                               else "-".join(secrets.token_hex(2).upper() for _ in range(3)))
    try:
        srv = Server((a.host, a.port), Handler)
    except OSError as e:
        raise SystemExit(f"Can't use port {a.port} ({e.strerror or e}). Brewfather Reports (or another program) is probably "
                         f"already running there: close it, or start this one with --port {a.port + 1}.")
    STATE["server"] = srv
    if a.idle_exit > 0:
        threading.Thread(target=idle_watch, args=(max(a.idle_exit, 120),), daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    host = "localhost" if a.host in ("127.0.0.1", "0.0.0.0", "::") else a.host
    print(f"{version.NAME} {version.VERSION} - {version.COPYRIGHT.replace(chr(169), '(c)')}")
    print(f"Open http://{host}:{a.port}/admin.html")
    if STATE["setup_code"]:
        print(f"First-time setup code: {STATE['setup_code']}")
    sys.stdout.flush()
    desktop_file = os.path.join(DATA, "desktop.json")  # how desktop.py --stop finds this server
    if STATE["stop_token"]:
        tmp = desktop_file + f".{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "port": a.port, "token": STATE["stop_token"]}, f)
        os.replace(tmp, desktop_file)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            with open(desktop_file, encoding="utf-8") as f:
                mine = json.load(f).get("token") == STATE["stop_token"] != ""
            if mine:
                os.remove(desktop_file)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    main()
