"""Sign-in sessions, Prometheus metrics and the HTTP server."""

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from . import config
from .alerts import (ALERT_KINDS, SAMPLE, alert_config, build_payload, build_report, post_webhook,
                     public_alert_config, save_alerts, validate_alerts, webhook_of)
from .config import DATA_DIR, EMBED_TOKEN, INTERVAL, METRICS_TOKEN, VERSION, WEB
from .control import validate_settings
from .drivers import DRIVERS, DriverError, detect
from .server import SERVERS, registry_lock, save_dashboard_servers, start, stop, unique_id, validate_server

KEY = b""  # set by app.main() once the data folder is known to be writable

# ---------------------------------------------------------------- sessions

SESSION_SHORT = 12 * 3600
SESSION_LONG = 30 * 86400
login_lock = threading.Lock()


def session_key():
    """Signing key derived from a persisted random secret and the password, so changing
    config.WEB_PASSWORD (or deleting data/secret) signs everyone out."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / "secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    return hmac.new(bytes.fromhex(f.read_text().strip()), config.WEB_PASSWORD.encode(), hashlib.sha256).digest()


def make_token(key, ttl):
    exp = str(int(time.time()) + ttl)
    return f"{exp}.{hmac.new(key, exp.encode(), hashlib.sha256).hexdigest()}"


def valid_token(key, token):
    exp, _, sig = (token or "").partition(".")
    good = hmac.new(key, exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig.encode(), good.encode()) and exp.isdigit() and int(exp) > time.time()


def same_secret(given, expected):
    return bool(expected) and hmac.compare_digest(str(given).encode(), expected.encode())


# ---------------------------------------------------------------- metrics

def metrics(servers):
    def esc(v):
        return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    out = {}

    def add(name, help_, labels, value):
        if value is None:
            return
        rows = out.setdefault(name, [f"# HELP {name} {help_}", f"# TYPE {name} gauge"])
        lab = ",".join('%s="%s"' % (k, esc(v)) for k, v in labels.items())
        rows.append(f"{name}{{{lab}}} {value:.15g}")

    add("fanctl_info", "Build information.", {"version": VERSION}, 1)
    for s in list(servers.values()):
        with s.lock:
            st = dict(s.state)
        sens = st["sensors"] or {}
        lbl = {"server": s.id, "name": s.name, "driver": s.driver.kind}
        add("fanctl_up", "1 if the last BMC reading succeeded.", lbl, 0 if st["error"] or not st["updated"] else 1)
        add("fanctl_last_update_timestamp_seconds", "Unix time of the last reading.", lbl, st["updated"])
        add("fanctl_power_on", "1 if the server is powered on.", lbl,
            None if st["power"] is None else int(st["power"] == "on"))
        add("fanctl_bmc_control", "1 if the BMC's own fan control is active.", lbl,
            None if st["effective"] is None else int(st["effective"] != "manual"))
        add("fanctl_failsafe_active", "1 while the failsafe temperature has handed control to the BMC.",
            lbl, int(st["failsafe"]))
        add("fanctl_fan_speed_percent", "Fan speed set by the controller (absent in automatic mode).", lbl, st["applied_speed"])
        add("fanctl_cpu_temperature_celsius", "Hottest CPU temperature.", lbl, st["cpu_temp"])
        add("fanctl_inlet_temperature_celsius", "Inlet air temperature.", lbl, sens.get("inlet"))
        add("fanctl_exhaust_temperature_celsius", "Exhaust air temperature.", lbl, sens.get("exhaust"))
        add("fanctl_power_watts", "System power draw.", lbl, sens.get("watts"))
        for t in sens.get("temps", []):
            add("fanctl_temperature_celsius", "Temperature sensor reading.",
                {**lbl, "sensor": t["name"], "entity": t["entity"]}, t["value"])
        for f in sens.get("fans", []):
            add("fanctl_fan_rpm", "Fan speed in RPM.", {**lbl, "fan": f["name"]}, f["rpm"])
            add("fanctl_fan_percent", "Fan speed in percent, as reported by the BMC.", {**lbl, "fan": f["name"]}, f["pct"])
    return "\n".join(row for rows in out.values() for row in rows) + "\n"


# ---------------------------------------------------------------- HTTP

STATIC = {  # allowlist: nothing outside it is ever read from disk
    "style.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "login.js": "text/javascript; charset=utf-8",
    "embed.js": "text/javascript; charset=utf-8",
    "alerts.js": "text/javascript; charset=utf-8",
    "integrations.js": "text/javascript; charset=utf-8",
    "grafana.json": "application/json",
    "icon.svg": "image/svg+xml",
    "fonts/archivo.woff2": "font/woff2",
    "fonts/plex-mono-400.woff2": "font/woff2",
    "fonts/plex-mono-500.woff2": "font/woff2",
}
PAGES = {"/": "index.html", "/login": "login.html", "/embed": "embed.html"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
       "font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "fan-control"
    sys_version = ""
    timeout = 30  # drop clients that open a connection and never finish the request

    def log_message(self, *_):
        pass

    def cookie(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def authed(self, read_only=False):
        if not config.WEB_PASSWORD or valid_token(KEY, self.cookie("session")):
            return True
        # the embed token opens the read-only views only, never a POST
        return read_only and same_secret(self.query.get("token", [""])[0], EMBED_TOKEN)

    def send(self, code, body=b"", ctype="application/json", headers=(), frame=False):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if frame:  # the read-only embed may sit in a dashboard's iframe
            self.send_header("Content-Security-Policy", CSP + "; frame-ancestors *")
        else:
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", CSP + "; frame-ancestors 'none'")
        for k, v in headers:
            self.send_header(k, v)
        if not any(k == "Cache-Control" for k, _ in headers):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def file(self, rel, ctype, frame=False):
        body = (WEB / rel).read_bytes()
        etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
        cache = [("Cache-Control", "no-cache"), ("ETag", etag)]
        if self.headers.get("If-None-Match") == etag:
            return self.send(304, headers=cache, frame=frame)
        self.send(200, body, ctype, headers=cache, frame=frame)

    def redirect(self, to):
        self.send(303, headers=[("Location", to)])

    def set_session(self, value, max_age):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        age = f"; Max-Age={max_age}" if max_age is not None else ""
        return ("Set-Cookie", f"session={value}; Path=/; HttpOnly; SameSite=Strict{secure}{age}")

    def server_arg(self):
        sid = self.query.get("server", [""])[0]
        return SERVERS.get(sid) or (None if sid or not SERVERS else next(iter(SERVERS.values())))

    def parse(self):
        url = urlsplit(self.path)
        self.route, self.query = url.path, parse_qs(url.query)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.parse()
        path = self.route
        if path == "/healthz":
            stale = [s.id for s in list(SERVERS.values())
                     if not s.state["updated"] or time.time() - s.state["updated"] > INTERVAL * 4]
            return self.send(503 if stale else 200, {"ok": not stale, "stale": stale})
        if path.startswith("/static/") and path[8:] in STATIC:
            return self.file(path[8:], STATIC[path[8:]])
        if path == "/metrics":
            bearer = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not (same_secret(bearer, METRICS_TOKEN) or (not config.WEB_PASSWORD and not METRICS_TOKEN)):
                return self.send(401 if METRICS_TOKEN else 404, {"error": "metrics need METRICS_TOKEN"})
            return self.send(200, metrics(SERVERS).encode(), "text/plain; version=0.0.4; charset=utf-8")
        if path == "/login":
            if self.authed():
                return self.redirect("/")
            return self.file(PAGES[path], "text/html; charset=utf-8")
        if path == "/embed":
            if not self.authed(read_only=True):
                return self.send(401, b"Sign in, or add ?token= with EMBED_TOKEN", "text/plain; charset=utf-8", frame=True)
            return self.file(PAGES[path], "text/html; charset=utf-8", frame=True)
        if path == "/api/history":
            if not self.authed(read_only=True):
                return self.send(401, {"error": "sign in required"})
            srv = self.server_arg()
            if srv is None:
                return self.send(404, {"error": "unknown server"})
            try:
                seconds = min(7 * 86400, max(3600, int(self.query.get("seconds", ["86400"])[0])))
            except ValueError:
                return self.send(400, {"error": "seconds must be a number"})
            since = time.time() - seconds
            with srv.lock:
                return self.send(200, {"points": [p for p in srv.long if p["t"] >= since], "bucket": 300})
        if path == "/api/state":
            if not self.authed(read_only=True):
                return self.send(401, {"error": "sign in required"})
            srv = self.server_arg()
            if srv is None:
                return self.send(404, {"error": "unknown server"})
            with srv.lock:
                return self.send(200, {**srv.state, **srv.info(), "interval": INTERVAL, "auth": bool(config.WEB_PASSWORD),
                                       "version": VERSION, "settings": srv.settings(),
                                       "history": list(srv.history), "events": list(srv.events)})
        if not self.authed():
            return self.redirect("/login") if path == "/" else self.send(401, {"error": "sign in required"})
        if path == "/":
            return self.file(PAGES[path], "text/html; charset=utf-8")
        if path == "/api/overview":
            return self.send(200, {"servers": [s.summary() for s in list(SERVERS.values())],
                                   "drivers": [d.info() for d in DRIVERS.values()],
                                   "auth": bool(config.WEB_PASSWORD), "version": VERSION, "interval": INTERVAL,
                                   "alerts": bool(webhook_of(alert_config()))})
        if path.startswith("/api/servers/") and path.count("/") == 3:
            srv = SERVERS.get(path.rsplit("/", 1)[1])
            if srv is None:
                return self.send(404, {"error": "unknown server"})
            return self.send(200, {**srv.info(), "username": srv.cfg.get("username", ""),
                                   "verify_tls": srv.cfg.get("verify_tls", False),
                                   "has_password": bool(srv.cfg.get("password"))})
        if path == "/api/alerts":
            return self.send(200, public_alert_config(alert_config()))
        if path == "/api/integrations":
            # whether each integration is switched on; the tokens themselves never leave the server
            return self.send(200, {"auth": bool(config.WEB_PASSWORD), "metrics_token": bool(METRICS_TOKEN),
                                   "metrics_open": not config.WEB_PASSWORD and not METRICS_TOKEN,
                                   "embed_token": bool(EMBED_TOKEN), "interval": INTERVAL,
                                   "servers": [{"id": s.id, "name": s.name} for s in list(SERVERS.values())]})
        if path == "/api/metrics-preview":
            return self.send(200, metrics(SERVERS).encode(), "text/plain; charset=utf-8")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        self.parse()
        # JSON only: a cross-site form cannot send this content type without a CORS preflight
        if self.headers.get("Content-Type") != "application/json":
            return self.send(415, {"error": "expected application/json"})
        origin = self.headers.get("Origin")
        hosts = {self.headers.get("Host"), self.headers.get("X-Forwarded-Host")}
        if origin and urlsplit(origin).netloc not in hosts:
            return self.send(403, {"error": "cross-origin request refused"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > 10000:
            return self.send(413, {"error": "request too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
        except ValueError as e:
            return self.send(400, {"error": str(e)})

        if self.route == "/api/login":
            if not config.WEB_PASSWORD:
                return self.send(200, {"ok": True})
            # ponytail: one global lock + 1 s per failure caps guessing at ~1/s overall;
            # per-client lockout if this is ever exposed to the internet
            with login_lock:
                if not same_secret(body.get("password", ""), config.WEB_PASSWORD):
                    time.sleep(1)
                    print(time.strftime("%H:%M:%S"), "WARN failed sign-in from",
                          self.headers.get("X-Forwarded-For", self.client_address[0]), flush=True)
                    return self.send(401, {"error": "Wrong password"})
            ttl = SESSION_LONG if body.get("remember") else SESSION_SHORT
            return self.send(200, {"ok": True}, headers=[
                self.set_session(make_token(KEY, ttl), ttl if body.get("remember") else None)])
        if self.route == "/api/logout":
            return self.send(200, {"ok": True}, headers=[self.set_session("", 0)])

        if not self.authed():
            return self.send(401, {"error": "sign in required"})
        if self.route == "/api/alerts":
            try:
                cfg = validate_alerts(body, alert_config())
            except (ValueError, TypeError, AttributeError) as e:
                return self.send(400, {"error": str(e)})
            save_alerts(cfg)
            return self.send(200, public_alert_config(cfg))
        if self.route == "/api/test-alert":
            # tests the configuration being edited, before it is saved
            try:
                cfg = validate_alerts(body.get("config") or {}, alert_config())
            except (ValueError, TypeError, AttributeError) as e:
                return self.send(400, {"error": str(e)})
            kind = body.get("kind", "failsafe")
            url = webhook_of(cfg)
            if kind not in ALERT_KINDS or not url:
                return self.send(400, {"error": "unknown alert" if url else "no Discord webhook configured"})
            if kind == "report" and not SERVERS:
                return self.send(400, {"error": "add a server first"})
            # the status report is tested with the real readings, everything else with sample values
            payload = build_report(cfg, SERVERS) if kind == "report" else build_payload(cfg, kind, SAMPLE)
            payload["embeds"][0]["title"] = "[test] " + payload["embeds"][0]["title"][:249]
            try:
                post_webhook(url, payload)
            except Exception as e:
                return self.send(502, {"error": f"Discord answered: {e}"})
            return self.send(200, {"ok": True})
        if self.route == "/api/servers/test":
            return self.test_server(body)
        if self.route == "/api/servers/detect":
            host = str(body.get("host", "")).strip()
            try:
                validate_server({"name": "detect", "driver": "redfish", "host": host, "username": "-", "password": "-"})
            except ValueError as e:
                return self.send(400, {"found": False, "error": str(e)})
            return self.send(200, detect(host, str(body.get("username", "")), str(body.get("password", "")),
                                         body.get("verify_tls") is True))
        if self.route == "/api/servers":
            with registry_lock:
                try:
                    cfg = validate_server(body)
                except (ValueError, TypeError) as e:
                    return self.send(400, {"error": str(e)})
                cfg["id"] = unique_id(cfg["name"], SERVERS)
                srv = start(cfg)
                save_dashboard_servers()
            return self.send(201, srv.info())
        m = re.fullmatch(r"/api/servers/([a-z0-9-]+)(/delete)?", self.route)
        if m:
            with registry_lock:
                srv = SERVERS.get(m.group(1))
                if srv is None:
                    return self.send(404, {"error": "unknown server"})
                if srv.cfg.get("source") == "environment":
                    return self.send(409, {"error": "this server is defined in the environment; change it there"})
                if m.group(2):
                    stop(srv, forget=True)
                    save_dashboard_servers()
                    return self.send(200, {"ok": True})
                try:
                    cfg = validate_server(body, srv.cfg)
                except (ValueError, TypeError) as e:
                    return self.send(400, {"error": str(e)})
                stop(srv)  # restart with the new address, credentials or driver
                srv = start(cfg)
                save_dashboard_servers()
            return self.send(200, srv.info())
        if self.route != "/api/settings":
            return self.send(404, {"error": "not found"})
        srv = self.server_arg()
        if srv is None:
            return self.send(404, {"error": "unknown server"})
        try:
            s = validate_settings(body, srv.settings())
        except (ValueError, TypeError) as e:
            return self.send(400, {"error": str(e)})
        srv.save_settings(s)
        self.send(200, s)

    def test_server(self, body):
        """Read the BMC once with the details being entered, before they are saved."""
        stored = SERVERS.get(str(body.get("id", "")))
        try:
            cfg = validate_server(body, stored.cfg if stored and stored.cfg.get("source") == "dashboard" else None)
        except (ValueError, TypeError) as e:
            return self.send(400, {"ok": False, "error": str(e)})
        drv = DRIVERS[cfg["driver"]](cfg["host"], cfg["username"], cfg.get("password", ""), cfg["verify_tls"], str(DATA_DIR))
        try:
            data = drv.read()
        except DriverError as e:
            return self.send(200, {"ok": False, "error": str(e)})
        except Exception as e:
            return self.send(200, {"ok": False, "error": repr(e)})
        cpus = [t["value"] for t in data["temps"] if t["cpu"]]
        return self.send(200, {"ok": True, "model": drv.model, "temps": len(data["temps"]), "fans": len(data["fans"]),
                               "cpu": max(cpus) if cpus else None, "watts": data["watts"], "power": data["power"],
                               "control": drv.control})
