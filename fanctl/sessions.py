"""Sign-in sessions and sign-in throttling.

A session is a signed cookie: expiry, role, generation and a random id, with an HMAC over them. The
key comes from a persisted secret and the passwords, so changing a password signs everyone out.
Sessions can also be ended one by one (sign out revokes that id) or all at once (the generation goes
up). Throttling counts failed sign-ins per client address, in memory, bounded in size."""

import hashlib
import hmac
import ipaddress
import secrets
import threading
import time

from . import config
from .config import DATA_DIR, read_json, write_json

SESSION_SHORT = 12 * 3600
SESSION_LONG = 30 * 86400
LOGIN_WINDOW = 600     # seconds over which failed sign-ins are counted, per address
LOGIN_ATTEMPTS = 5     # failures allowed in that window before the address has to wait
MAX_TRACKED = 10_000   # addresses remembered at most; the oldest are forgotten first
SESSIONS_FILE = DATA_DIR / "sessions.json"

failures = {}          # address -> times of recent failed sign-ins
failures_lock = threading.Lock()
_state_lock = threading.Lock()
_state = None          # {"generation": int, "revoked": {sid: expiry}}


def session_key():
    """Signing key derived from a persisted random secret and the passwords, so changing
    WEB_PASSWORD or VIEW_PASSWORD (or deleting data/secret) signs everyone out."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / "secret"
    if not f.exists():
        _write_secret(f, secrets.token_hex(32))
    passwords = f"{config.WEB_PASSWORD}\0{config.VIEW_PASSWORD}".encode()
    return hmac.new(bytes.fromhex(f.read_text().strip()), passwords, hashlib.sha256).digest()


def _write_secret(path, text):
    """The secret is plain hex, written private from the first byte like the JSON files."""
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _sessions():
    global _state
    if _state is None:
        saved = read_json(SESSIONS_FILE, {}, "sessions.json")
        saved = saved if isinstance(saved, dict) else {}
        revoked = saved.get("revoked") if isinstance(saved.get("revoked"), dict) else {}
        _state = {"generation": saved.get("generation", 0) if type(saved.get("generation")) is int else 0,
                  "revoked": {str(k): v for k, v in revoked.items() if type(v) is int}}
    return _state


def _save():
    now = time.time()
    _state["revoked"] = {k: v for k, v in _state["revoked"].items() if v > now}  # expired ids need no list
    write_json(SESSIONS_FILE, _state, private=True)


def make_token(key, ttl, role="admin"):
    with _state_lock:
        gen = _sessions()["generation"]
    body = f"{int(time.time()) + ttl}.{role}.{gen}.{secrets.token_hex(8)}"
    return f"{body}.{hmac.new(key, body.encode(), hashlib.sha256).hexdigest()}"


def _parts(key, token):
    body, _, sig = (token or "").rpartition(".")
    good = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig.encode(), good.encode()):
        return None
    parts = body.split(".")
    if len(parts) != 4 or not parts[0].isdigit() or not parts[2].isdigit():
        return None
    return int(parts[0]), parts[1], int(parts[2]), parts[3]


def valid_token(key, token):
    """The role a session token grants ("admin" or "viewer"), or None if it is forged, expired,
    signed out, or from before "sign out everywhere"."""
    p = _parts(key, token)
    if not p:
        return None
    exp, role, gen, sid = p
    with _state_lock:
        st = _sessions()
        if gen != st["generation"] or sid in st["revoked"]:
            return None
    return role if exp > time.time() and role in ("admin", "viewer") else None


def revoke(key, token):
    """Sign out: this session id stops working, everywhere it was copied to."""
    p = _parts(key, token)
    if not p:
        return
    with _state_lock:
        _sessions()["revoked"][p[3]] = p[0]
        _save()


def revoke_all():
    """Sign out everywhere: every session made so far stops working."""
    with _state_lock:
        st = _sessions()
        st["generation"] += 1
        st["revoked"] = {}
        _save()


# ---------------------------------------------------------------- sign-in throttling

def throttled(address, now=None):
    """Seconds this address must wait before trying to sign in again, 0 if it may try now."""
    now = now or time.time()
    with failures_lock:
        recent = [t for t in failures.get(address, []) if t > now - LOGIN_WINDOW]
        if recent:
            failures[address] = recent
        else:
            failures.pop(address, None)
        return int(recent[0] + LOGIN_WINDOW - now) + 1 if len(recent) >= LOGIN_ATTEMPTS else 0


def failed(address, now=None):
    now = now or time.time()
    with failures_lock:
        if len(failures) >= MAX_TRACKED:  # sweep the stale, then drop the oldest: memory stays bounded
            for a in [a for a, ts in failures.items() if ts[-1] <= now - LOGIN_WINDOW]:
                del failures[a]
            while len(failures) >= MAX_TRACKED:
                del failures[min(failures, key=lambda a: failures[a][-1])]
        failures.setdefault(address, []).append(now)


# ---------------------------------------------------------------- client address

DEFAULT_PROXIES = ("127.0.0.0/8", "::1/128", "172.16.0.0/12")  # loopback and Docker's own networks


def trusted_proxies():
    raw = config.TRUSTED_PROXIES or ",".join(DEFAULT_PROXIES)
    nets = []
    for part in raw.split(","):
        try:
            nets.append(ipaddress.ip_network(part.strip(), strict=False))
        except ValueError:
            pass
    return nets


def _trusted(address, nets):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in n for n in nets)


def client_address(peer, forwarded):
    """The address a request comes from. X-Forwarded-For is believed only with TRUST_PROXY and
    only from a trusted proxy (TRUSTED_PROXIES, or loopback and Docker's networks), and read from
    the right: each proxy appends the address it saw, so the rightmost address that is not one of
    our proxies is the client. Anything to its left was written by the client and proves nothing."""
    if not (config.TRUST_PROXY and forwarded):
        return peer
    nets = trusted_proxies()
    if not _trusted(peer, nets):
        return peer  # someone reached the app directly: their header is their own invention
    for hop in reversed([h.strip() for h in forwarded.split(",") if h.strip()]):
        if not _trusted(hop, nets):
            return hop
    return peer
