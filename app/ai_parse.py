"""Optional AI recipe reader: Claude, ChatGPT, Gemini or Copilot reads a pasted recipe into the importer's draft JSON.

Off unless you choose a provider on the Admin page and save an API key. The key is kept in .env
(AI_KEY_CLAUDE, AI_KEY_CHATGPT, AI_KEY_GEMINI, AI_KEY_COPILOT) and only sent to that provider.
Standard library only (urllib).

    read(text, lib_names)  -> draft dict (recipe_parse.from_json), or raises AIError
    test()                 -> None if the provider answers, else an error message
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import recipe_parse  # noqa: E402
import settings  # noqa: E402
from brewfather import load_env  # noqa: E402

# Default models are a reasonable choice when this was written; change them on the Admin page when a provider retires one.
PROVIDERS = {
    "claude": {"label": "Claude (Anthropic)", "api": "anthropic", "endpoint": "https://api.anthropic.com/v1/messages",
               "model": "claude-sonnet-5-5", "keys": "console.anthropic.com › API keys"},
    "chatgpt": {"label": "ChatGPT (OpenAI)", "api": "openai", "endpoint": "https://api.openai.com/v1/chat/completions",
                "model": "gpt-5-mini", "keys": "platform.openai.com › API keys"},
    "gemini": {"label": "Gemini (Google)", "api": "gemini",
               "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
               "model": "gemini-2.5-flash", "keys": "aistudio.google.com › Get API key"},
    "copilot": {"label": "Copilot (GitHub Models or Azure OpenAI)", "api": "openai",
                "endpoint": "https://models.github.ai/inference/chat/completions", "model": "openai/gpt-4.1-mini",
                "keys": "github.com › Settings › Developer settings › Personal access tokens (fine-grained, "
                        "with the Models permission); or your Azure OpenAI resource's key and chat completions URL"},
}
TIMEOUT = 120


class AIError(Exception):
    pass


def env_key(provider):
    return "AI_KEY_" + provider.upper()


def config():
    """The chosen provider, model and endpoint (with defaults) and whether a key is saved."""
    s = settings.load()
    p = s.get("ai_provider") or ""
    if p not in PROVIDERS:
        return {"provider": "", "ready": False}
    d = PROVIDERS[p]
    key = load_env(paths.ENV).get(env_key(p)) or ""
    return {"provider": p, "label": d["label"], "model": s.get("ai_model") or d["model"],
            "endpoint": s.get("ai_endpoint") or d["endpoint"], "hasKey": bool(key), "ready": bool(key), "key": key}


def public_info():
    c = config()
    c.pop("key", None)
    env = load_env(paths.ENV)
    return {**c, "providers": {k: {"label": v["label"], "model": v["model"], "endpoint": v["endpoint"], "keys": v["keys"],
                                   "hasKey": bool(env.get(env_key(k)))} for k, v in PROVIDERS.items()}}


def check_endpoint(url):
    """None when the address is acceptable: https, or http only to this computer (a local model server)."""
    u = urllib.parse.urlparse(url or "")
    if u.scheme == "https" and u.netloc:
        return None
    if u.scheme == "http" and (u.hostname or "") in ("localhost", "127.0.0.1", "::1"):
        return None
    return "The API address must start with https:// (or http://localhost for a model on this computer)."


def _post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        try:
            j = json.loads(detail)
            detail = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else (j.get("error") or j.get("message") or detail)
        except ValueError:
            pass
        hint = {401: "The API key was rejected.", 403: "The API key isn't allowed to use this model.",
                404: "The model or API address wasn't found. Check the model name on the Admin page.",
                429: "The provider says you're over a rate limit or out of credit."}.get(e.code, f"The provider returned an error ({e.code}).")
        raise AIError(f"{hint} {str(detail)[:300]}".strip())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIError(f"Couldn't reach the AI provider: {getattr(e, 'reason', e)}")
    except ValueError:
        raise AIError("The AI provider sent back something that isn't JSON.")


def ask(system, user, cfg=None, max_tokens=8000):
    """Send one prompt; returns the reply text."""
    cfg = cfg or config()
    if not cfg.get("provider"):
        raise AIError("No AI provider is set up. Choose one on the Admin page, or read the recipe yourself.")
    if not cfg.get("key"):
        raise AIError(f"No API key saved for {cfg['label']}. Add it on the Admin page.")
    problem = check_endpoint(cfg["endpoint"])
    if problem:
        raise AIError(problem)
    api, key, model = PROVIDERS[cfg["provider"]]["api"], cfg["key"], cfg["model"]
    if api == "anthropic":
        j = _post(cfg["endpoint"], {"x-api-key": key, "anthropic-version": "2023-06-01"},
                  {"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]})
        return "".join(b.get("text", "") for b in j.get("content") or [] if b.get("type") == "text")
    if api == "gemini":
        url = cfg["endpoint"].replace("{model}", urllib.parse.quote(model, safe=""))
        j = _post(url, {"x-goog-api-key": key},
                  {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                   "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_tokens}})
        cands = j.get("candidates") or []
        if not cands:
            raise AIError("Gemini didn't return an answer" + (f" ({(j.get('promptFeedback') or {}).get('blockReason')})." if j.get("promptFeedback") else "."))
        return "".join(p.get("text", "") for p in (cands[0].get("content") or {}).get("parts") or [])
    # OpenAI-compatible: ChatGPT, GitHub Models, Azure OpenAI
    host = urllib.parse.urlparse(cfg["endpoint"]).hostname or ""
    headers = {"api-key": key} if host.endswith(".azure.com") else {"Authorization": f"Bearer {key}"}
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"}}
    j = _post(cfg["endpoint"], headers, body)
    choices = j.get("choices") or []
    if not choices:
        raise AIError("The provider didn't return an answer.")
    return (choices[0].get("message") or {}).get("content") or ""


def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("No JSON object found.")
    return json.loads(text[a:b + 1])


def read(text, lib_names=None):
    cfg = config()
    system, user = recipe_parse.prompt(text, lib_names)
    reply = ask(system, user, cfg)
    try:
        obj = extract_json(reply)
    except ValueError:
        raise AIError("The AI's answer wasn't valid JSON. Try again, or read the recipe yourself.")
    try:
        d = recipe_parse.from_json(obj)
    except ValueError as e:
        raise AIError(f"The AI's answer wasn't a usable recipe: {e}")
    d["reader"] = f"{cfg['label']} ({cfg['model']})"
    return d


def test():
    """None when the provider answers with JSON, otherwise an error message."""
    try:
        reply = ask("Reply with a JSON object only.", 'Return {"ok": true}.', max_tokens=1000)
        extract_json(reply)
        return None
    except AIError as e:
        return str(e)
    except ValueError:
        return "The provider answered, but not with JSON. The model may not support JSON replies; try another model."
