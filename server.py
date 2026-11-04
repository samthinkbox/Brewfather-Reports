"""Brewfather Reports web server: the report pages plus the Admin page.

    python server.py                         # http://127.0.0.1:8765
    python server.py --port 8080
    python server.py --proxy                 # behind nginx/Caddy/IIS: trust X-Forwarded-For and -Proto
    python server.py --host 0.0.0.0          # listen on every network interface (use HTTPS in front of it)

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
import build_report  # noqa: E402
import hop_flavour  # noqa: E402
import layout  # noqa: E402
import settings  # noqa: E402
import version  # noqa: E402
from brewfather import Brewfather, load_env  # noqa: E402

OUT = os.path.join(HERE, "output")
DATA = os.path.join(HERE, "data")
ADMIN_FILE = os.path.join(DATA, "admin.json")
ENV_FILE = os.path.join(HERE, ".env")
BATCHES = os.path.join(DATA, "batches.json")
UPLOADED = os.path.join(HERE, "Next Brew", "uploaded.json")
SESSION_SECONDS = 12 * 3600
COOKIE = "bfr_session"
EDITABLE = {"families.txt": "text", "exclude.txt": "text", "hop_flavours_extra.json": "json"}
STATIC = {".html": "text/html; charset=utf-8", ".csv": "text/csv; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}
ALWAYS_PUBLIC = {"admin.html", "terms.html"}
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
PY = [sys.executable, "-I"]
JOBS = {
    "refresh": ("Fetch from Brewfather and rebuild", PY + ["run.py"]),
    "rebuild": ("Rebuild pages from saved data", PY + ["run.py", "--offline"]),
    "hops": ("Update hop data and rebuild", PY + ["run.py", "--offline", "--update-hops"]),
    "upload": ("Send Next Brew recipes to Brewfather", PY + ["next_brew.py", "--upload"]),
}

LOCK = threading.Lock()
SESSIONS = {}          # token -> expiry time
FAILS = {}             # ip -> [failed attempts, locked until]
JOB = {"name": None, "label": None, "running": False, "started": None, "finished": None, "ok": None, "log": ""}
STATE = {"setup_code": None, "proxy": False, "last_auto": 0.0}
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


def save_creds(user_id, api_key):
    keep = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            keep = [ln.rstrip("\n") for ln in f if not ln.strip().startswith(("BREWFATHER_USER_ID", "BREWFATHER_API_KEY"))]
    keep += [f"BREWFATHER_USER_ID={user_id}", f"BREWFATHER_API_KEY={api_key}"]
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + "\n")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass


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
    if name == "upload" and not os.path.exists(os.path.join(HERE, "next_brew.py")):
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
        p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
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


# ------------------------------------------------------------------ exclusions
EXCLUDE = os.path.join(HERE, "exclude.txt")
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
def status(logged_in):
    user, key = creds()
    s = {"name": version.NAME, "version": version.VERSION, "copyright": version.COPYRIGHT,
         "setupNeeded": setup_needed(), "loggedIn": logged_in, "credsSet": bool(user and key)}
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
        "nextBrew": {"available": os.path.exists(os.path.join(HERE, "next_brew.py")), "uploaded": uploaded},
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
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/api/"):
            return self.api_get(u.path, urllib.parse.parse_qs(u.query))
        name = u.path.lstrip("/")
        if not name:
            return self.redirect("report.html" if os.path.exists(os.path.join(OUT, "report.html")) and not setup_needed() else "admin.html")
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
            p = os.path.join(HERE, name)
            text = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
            return self.json({"name": name, "content": text})
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
            with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
                f.write(content.replace("\r\n", "\n"))
            if os.path.exists(BATCHES):
                start_job("rebuild")
            return self.json({"ok": True})

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
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--proxy", action="store_true", help="trust X-Forwarded-For/-Proto from a reverse proxy")
    a = ap.parse_args()
    STATE["proxy"] = a.proxy
    os.makedirs(OUT, exist_ok=True)
    layout.write_static_pages()
    if setup_needed():
        STATE["setup_code"] = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    try:
        srv = Server((a.host, a.port), Handler)
    except OSError as e:
        raise SystemExit(f"Can't use port {a.port} ({e.strerror or e}). Brewfather Reports (or another program) is probably "
                         f"already running there: close it, or start this one with --port {a.port + 1}.")
    threading.Thread(target=scheduler, daemon=True).start()
    host = "localhost" if a.host in ("127.0.0.1", "0.0.0.0", "::") else a.host
    print(f"{version.NAME} {version.VERSION} - {version.COPYRIGHT.replace(chr(169), '(c)')}")
    print(f"Open http://{host}:{a.port}/admin.html")
    if STATE["setup_code"]:
        print(f"First-time setup code: {STATE['setup_code']}")
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
