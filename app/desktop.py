"""Run Brewfather Reports like a desktop app.

    python app/desktop.py                 # start the server in the background (if it isn't running) and open the app window
    python app/desktop.py --install       # add Start Menu and desktop shortcuts (Windows) or an app menu entry (Linux)
    python app/desktop.py --install --autostart   # ...and start the server when you sign in
    python app/desktop.py --uninstall     # remove those shortcuts
    python app/desktop.py --stop          # stop a server this launcher started
    python app/desktop.py --server-only   # start the server, no window (used by --autostart)

The window is Edge, Chrome, Chromium or Brave in "app mode" (no tabs or address bar); without one of
those it opens your default browser. The server listens on this computer only (127.0.0.1); its output
goes to data/server.log. The browser's own "Install app" button works too (see pwa.py), but the
installed app needs the server running: use the shortcut once, or --autostart.

Standard library only. Shortcuts on Windows are made with PowerShell's WScript.Shell.
"""
import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import version  # noqa: E402

STATE_FILE = os.path.join(paths.DATA, "desktop.json")   # pid, port and stop token, written by the server itself
LOG = os.path.join(paths.DATA, "server.log")
IDLE_EXIT = 300   # seconds: the server stops this long after the last app window closes (not with --server-only)
ADMIN_FILE = os.path.join(paths.DATA, "admin.json")
WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"


# ------------------------------------------------------------------ messages (no console under pythonw)
def tell(text, error=False):
    if WINDOWS and (sys.stdout is None or os.path.basename(sys.executable).lower() == "pythonw.exe"):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, version.NAME, 0x10 if error else 0x40)
    else:
        print(text, file=sys.stderr if error else sys.stdout)


def fail(text):
    tell(text, error=True)
    raise SystemExit(1)


# ------------------------------------------------------------------ server
def status(port):
    """The server's /api/status, None if nothing answers, {} if something else is on the port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            j = json.loads(r.read() or b"{}")
            return j if j.get("name") == version.NAME else {}
    except (urllib.error.HTTPError, ValueError):
        return {}
    except OSError:
        return None


def pythonw():
    """The windowless Python on Windows, else this one."""
    if WINDOWS:
        w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.exists(w):
            return w
    return sys.executable


def start_server(port, idle_exit=True):
    """Start the server in the background. Returns the setup code given to it, or None."""
    os.makedirs(paths.DATA, exist_ok=True)
    env = dict(os.environ)
    token = secrets.token_urlsafe(32)
    env["BFR_STOP_TOKEN"] = token
    code = None
    if not os.path.exists(ADMIN_FILE):
        code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        env["BFR_SETUP_CODE"] = code
    kw = {}
    if WINDOWS:
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS, NEW_PROCESS_GROUP, NO_WINDOW
    else:
        kw["start_new_session"] = True
    with open(LOG, "w", encoding="utf-8") as log:
        p = subprocess.Popen([pythonw(), "-I", paths.app("server.py"), "--port", str(port)] + (["--idle-exit", str(IDLE_EXIT)] if idle_exit else []), cwd=paths.ROOT, env=env,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True, **kw)
    for _ in range(60):
        time.sleep(0.25)
        if status(port):
            return code
        if p.poll() is not None:
            break
    tail = open(LOG, encoding="utf-8", errors="replace").read()[-600:].strip() if os.path.exists(LOG) else ""
    fail(f"The Brewfather Reports server didn't start.\n\n{tail}\n\nThe full log is in {LOG}")


def ensure_server(port, idle_exit=True):
    """Make sure our server answers on port. Returns a setup code for the window, or None."""
    st = status(port)
    if st == {}:
        fail(f"Another program is using port {port}. Close it, or start Brewfather Reports with --port {port + 1}.")
    if st is None:
        # a double-click can run two launchers at once: only one starts the server, the other waits for it
        lock = os.path.join(paths.DATA, "desktop.lock")
        os.makedirs(paths.DATA, exist_ok=True)
        try:
            if os.path.exists(lock) and time.time() - os.path.getmtime(lock) > 30:
                os.remove(lock)  # left by a launcher that crashed
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            for _ in range(80):
                time.sleep(0.25)
                if status(port):
                    return None
            fail("The Brewfather Reports server didn't start. Try the shortcut again.")
        try:
            return start_server(port, idle_exit)
        finally:
            os.remove(lock)
    return None


def server_pids():
    """Process ids of every Brewfather Reports server running from this folder (shortcut, start.bat or sign-in)."""
    target = os.path.normcase(paths.app("server.py"))
    if WINDOWS:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           creationflags=0x08000000)
    else:
        r = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True)
    pids = []
    for line in r.stdout.splitlines():
        pid, _, cmd = line.strip().replace("\t", " ", 1).partition(" ")
        if pid.isdigit() and int(pid) != os.getpid() and target in os.path.normcase(cmd):
            pids.append(int(pid))
    return pids


def listener_pids(ports):
    """Process ids listening on those of ports where Brewfather Reports answers (catches a server started with a
    relative path, as start.bat does)."""
    ports = [p for p in ports if status(p)]
    if not ports:
        return []
    if WINDOWS:
        ps = ("Get-NetTCPConnection -State Listen -LocalPort " + ",".join(map(str, ports))
              + " -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess }")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           creationflags=0x08000000)
    elif shutil.which("lsof"):
        r = subprocess.run(["lsof", "-t", "-sTCP:LISTEN"] + [f"-iTCP:{p}" for p in ports], capture_output=True, text=True)
    else:
        return []
    return [int(x) for x in r.stdout.split() if x.isdigit() and int(x) not in (0, 4, os.getpid())]


def gone(ports, seconds=5):
    for _ in range(int(seconds * 4)):
        if not any(status(p) for p in ports):
            return True
        time.sleep(0.25)
    return False


def stop(port):
    """Close down Brewfather Reports: ask the server to stop, and end any server process still running."""
    ports = {port}
    s = {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
        ports.add(int(s["port"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    was_running = any(status(p) for p in ports) or bool(server_pids())
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{s['port']}/api/stop", method="POST",
                                     data=json.dumps({"token": s["token"]}).encode(),
                                     headers={"X-BFR": "1", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except (OSError, ValueError, KeyError, TypeError):
        pass  # no file, or an old/other server: ended below
    gone(ports, 2)
    # anything still running: a server from this folder on any port, or whatever Brewfather Reports answers from
    pids = sorted(set(server_pids()) | set(listener_pids(ports)))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)  # on Windows this ends the process (TerminateProcess)
        except OSError:
            pass
    if not gone(ports):
        fail("Brewfather Reports couldn't be closed down. Restart the computer, or end \"Python\" in Task Manager.")
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass
    tell("Brewfather Reports is closed down." if was_running else
         "Brewfather Reports is closed down (it wasn't running).")


# ------------------------------------------------------------------ app window
def find_browser():
    """A Chromium-based browser that supports --app, or None."""
    names = ["msedge", "chrome", "brave", "chromium"]
    if WINDOWS:
        import winreg
        for n in names:
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{n}.exe") as k:
                        p = winreg.QueryValue(k, None)
                        if p and os.path.exists(p.strip('"')):
                            return p.strip('"')
                except OSError:
                    pass
        bases = [os.environ.get(v) for v in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        for b in filter(None, bases):
            for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                if os.path.exists(os.path.join(b, rel)):
                    return os.path.join(b, rel)
        return None
    if MAC:
        for app in ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"):
            p = f"/Applications/{app}.app/Contents/MacOS/{app}"
            if os.path.exists(p):
                return p
        return None
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
        if shutil.which(n):
            return shutil.which(n)
    return None


def open_window(url):
    b = find_browser()
    if not b:
        webbrowser.open(url)
        return
    kw = {"creationflags": 0x00000008} if WINDOWS else {"start_new_session": True}
    subprocess.Popen([b, f"--app={url}"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **kw)


# ------------------------------------------------------------------ shortcuts
PS_SHORTCUT = r"""
$ErrorActionPreference = 'Stop'
$sh = New-Object -ComObject WScript.Shell
foreach ($dir in ($env:BFR_DIRS -split '\|')) {
  if ($dir -eq 'DESKTOP') { $dir = [Environment]::GetFolderPath('Desktop') }
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $s = $sh.CreateShortcut((Join-Path $dir $env:BFR_NAME))
  $s.TargetPath = $env:BFR_TARGET; $s.Arguments = $env:BFR_ARGS; $s.WorkingDirectory = $env:BFR_CWD
  $s.IconLocation = $env:BFR_ICON; $s.Description = $env:BFR_DESC; $s.Save()
}
"""


def win_dirs():
    appdata = os.environ["APPDATA"]
    return {"menu": os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs", version.NAME),
            "startup": os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup")}


def win_shortcut(dirs, name, args, desc):
    env = dict(os.environ, BFR_DIRS="|".join(dirs), BFR_NAME=name + ".lnk", BFR_TARGET=pythonw(),
               BFR_ARGS=f'-I "{paths.app("desktop.py")}"' + (" " + args if args else ""), BFR_CWD=paths.ROOT,
               BFR_ICON=os.path.join(paths.ICONS, "app.ico") + ",0", BFR_DESC=desc)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_SHORTCUT], env=env,
                       capture_output=True, text=True)
    if r.returncode:
        fail("Couldn't make the shortcut:\n" + (r.stderr or r.stdout).strip()[-600:])


def linux_files():
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    conf = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return (os.path.join(data, "applications", "brewfather-reports.desktop"),
            os.path.join(conf, "autostart", "brewfather-reports.desktop"))


def linux_entry(path, args, name):
    py = shutil.which("python3") or sys.executable
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"[Desktop Entry]\nType=Application\nName={name}\nComment=Reports on your Brewfather brewing history\n"
                f'Exec="{py}" -I "{paths.app("desktop.py")}"{(" " + args) if args else ""}\n'
                f'Icon={os.path.join(paths.ICONS, "icon-512.png")}\nTerminal=false\nCategories=Utility;\n')
    os.chmod(path, 0o755)


def install(autostart, port):
    extra = f"--port {port}" if port != 8642 else ""
    if WINDOWS:
        d = win_dirs()
        win_shortcut([d["menu"], "DESKTOP"], version.NAME, extra, "Open Brewfather Reports")
        win_shortcut([d["menu"]], "Stop " + version.NAME, ("--stop " + extra).strip(), "Stop the Brewfather Reports server")
        if autostart:
            win_shortcut([d["startup"]], version.NAME + " server", ("--server-only " + extra).strip(),
                         "Start the Brewfather Reports server when you sign in")
        where = "the Start Menu and on your desktop"
    elif MAC:
        return tell("On a Mac, run start.sh once, open http://localhost:8642 in Chrome or Edge and choose "
                    "\"Install Brewfather Reports\" from the browser menu. Then add start.sh to Login Items "
                    "(System Settings › General) so the server is running when you open the app.")
    else:
        menu, auto = linux_files()
        linux_entry(menu, extra, version.NAME)
        if autostart:
            linux_entry(auto, ("--server-only " + extra).strip(), version.NAME + " server")
        where = "your applications menu"
    tell(f"Brewfather Reports is now in {where}."
         + (" The server will also start when you sign in." if autostart else "")
         + "\n\nTip: in the app window, the browser menu also offers \"Install Brewfather Reports\" "
           "so it appears with your other apps.")


def uninstall():
    removed = []
    if WINDOWS:
        d = win_dirs()
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                           capture_output=True, text=True)
        desktop = r.stdout.strip()
        cands = [os.path.join(d["startup"], version.NAME + " server.lnk")]
        if desktop:
            cands.append(os.path.join(desktop, version.NAME + ".lnk"))
        for p in cands:
            if os.path.exists(p):
                os.remove(p)
                removed.append(p)
        if os.path.isdir(d["menu"]):
            shutil.rmtree(d["menu"])
            removed.append(d["menu"])
    elif not MAC:
        for p in linux_files():
            if os.path.exists(p):
                os.remove(p)
                removed.append(p)
    tell("Removed:\n" + "\n".join(removed) if removed else "There were no shortcuts to remove.")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Run Brewfather Reports like a desktop app")
    ap.add_argument("--port", type=int, default=8642)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="add Start Menu / desktop shortcuts or an app menu entry")
    g.add_argument("--uninstall", action="store_true", help="remove the shortcuts")
    g.add_argument("--stop", action="store_true", help="stop the server started by this launcher")
    g.add_argument("--server-only", action="store_true", help="start the server without opening a window")
    ap.add_argument("--autostart", action="store_true", help="with --install: start the server when you sign in")
    a = ap.parse_args()
    if a.install:
        return install(a.autostart, a.port)
    if a.uninstall:
        return uninstall()
    if a.stop:
        return stop(a.port)
    code = ensure_server(a.port, idle_exit=not a.server_only)  # a sign-in start keeps running
    if a.server_only:
        return
    # "/" opens the reports, or the Admin page while setup is needed; the setup code rides in the #fragment,
    # which the browser never sends to the server
    open_window(f"http://localhost:{a.port}/" + (f"admin.html#setup={code}" if code else ""))


if __name__ == "__main__":
    main()
