"""Read-only access tokens created in the dashboard: "widget" (the embed page and /api/widget) and
"metrics" (/metrics). Only a SHA-256 of each is stored, so the file cannot hand them out; the token
itself is shown once, when it is created. EMBED_TOKEN and METRICS_TOKEN still work alongside."""

import hashlib
import hmac
import secrets
import threading
import time

from . import config
from .config import DATA_DIR, read_json, write_json

TOKENS_FILE = DATA_DIR / "tokens.json"
KINDS = ("widget", "metrics")
lock = threading.Lock()
_cache = None


def _digest(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def _load():
    global _cache
    if _cache is None:
        rows = read_json(TOKENS_FILE, [], "tokens.json")
        _cache = [r for r in rows if isinstance(r, dict) and r.get("kind") in KINDS
                  and len(str(r.get("hash", ""))) == 64] if isinstance(rows, list) else []
    return _cache


def _save():
    write_json(TOKENS_FILE, _cache, private=True)


def env_token(kind):
    return config.EMBED_TOKEN if kind == "widget" else config.METRICS_TOKEN


def any_token(kind):
    with lock:
        return bool(env_token(kind)) or any(r["kind"] == kind for r in _load())


def check(token, kind):
    """True for the environment token of this kind or any dashboard token of it. Every stored hash
    is compared, in constant time, so the answer takes as long whichever matched."""
    if not token:
        return False
    env = env_token(kind)
    ok = bool(env) and hmac.compare_digest(str(token).encode(), env.encode())
    digest = _digest(token)
    with lock:
        for r in _load():
            if r["kind"] == kind and hmac.compare_digest(digest, r["hash"]):
                ok = True
                if time.time() - r.get("used", 0) > 600:  # last use, to the nearest 10 minutes
                    r["used"] = int(time.time())
                    _save()
    return ok


def public():
    """The list for the dashboard: names and dates, never a hash."""
    with lock:
        return [{k: r.get(k) for k in ("id", "name", "kind", "created", "used")} for r in _load()]


def create(name, kind):
    name = str(name or "").strip()
    if kind not in KINDS:
        raise ValueError("kind must be widget or metrics")
    if not 1 <= len(name) <= 40:
        raise ValueError("give the token a name of 1 to 40 characters")
    token = "fc_" + secrets.token_urlsafe(24)
    with lock:
        rows = _load()
        if len(rows) >= 50:
            raise ValueError("50 tokens at most; revoke some first")
        row = {"id": secrets.token_hex(4), "name": name, "kind": kind, "hash": _digest(token),
               "created": int(time.time()), "used": 0}
        rows.append(row)
        _save()
    return token, {k: row[k] for k in ("id", "name", "kind", "created", "used")}


def revoke(token_id):
    with lock:
        rows = _load()
        keep = [r for r in rows if r["id"] != token_id]
        if len(keep) == len(rows):
            raise ValueError("unknown token")
        rows[:] = keep
        _save()
