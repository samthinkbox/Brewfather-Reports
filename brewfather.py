"""Minimal Brewfather API v2 client (standard library only).

Credentials are read from .env next to this file:
    BREWFATHER_USER_ID=...
    BREWFATHER_API_KEY=...
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.brewfather.app/v2"
HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path=os.path.join(HERE, ".env")):
    env = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith("BREWFATHER_")})
    return env


class Brewfather:
    def __init__(self, user_id=None, api_key=None):
        env = load_env()
        user_id = user_id or env.get("BREWFATHER_USER_ID")
        api_key = api_key or env.get("BREWFATHER_API_KEY")
        if not user_id or not api_key:
            raise SystemExit("Set BREWFATHER_USER_ID and BREWFATHER_API_KEY in .env")
        token = base64.b64encode(f"{user_id}:{api_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def get(self, path, **params):
        return self.request("GET", path, params=params)

    def post(self, path, body):
        return self.request("POST", path, body=body)

    def patch(self, path, body):
        return self.request("PATCH", path, body=body)

    def request(self, method, path, params=None, body=None):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = dict(self.headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(4):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    text = r.read().decode("utf-8")
                    try:
                        return json.loads(text) if text else None
                    except ValueError:
                        return text
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(int(e.headers.get("Retry-After", "30")))
                    continue
                body = e.read().decode("utf-8", "replace")[:3000]
                raise SystemExit(f"Brewfather API {e.code} on {path}: {body}")

    def list_all(self, path, **params):
        """Page through a list endpoint (max 50 per page)."""
        out, last = [], None
        while True:
            page = self.get(path, limit=50, start_after=last, **params)
            out.extend(page)
            if len(page) < 50:
                return out
            last = page[-1]["_id"]
