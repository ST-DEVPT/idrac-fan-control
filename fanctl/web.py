"""Sign-in sessions and the HTTP server."""

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from . import config, tokens, updates
from .alerts import (ALERT_KINDS, CHANNELS, SAMPLE, alert_config, build_payload, build_report, post_webhook,
                     public_alert_config, save_alerts, send_channel, validate_alerts, webhook_of)
from .backup import export_config, import_config
from .config import DATA_DIR, INTERVAL, VERSION, WEB
from .control import validate_settings
from .drivers import DRIVERS, DriverError, detect, scan
from .metrics import metrics
from .server import SERVERS, registry_lock, save_dashboard_servers, stalled, start, stop, unique_id, validate_server
from .widgets import WIDGET_FIELDS, homarr_widget, save_widget_fields, widget_fields, widget_view

KEY = b""  # set by app.main() once the data folder is known to be writable

# ---------------------------------------------------------------- sessions

SESSION_SHORT = 12 * 3600
SESSION_LONG = 30 * 86400
LOGIN_WINDOW = 600     # seconds over which failed sign-ins are counted, per address
LOGIN_ATTEMPTS = 5     # failures allowed in that window before the address has to wait
failures = {}          # address -> times of recent failed sign-ins
failures_lock = threading.Lock()


def session_key():
    """Signing key derived from a persisted random secret and the passwords, so changing
    WEB_PASSWORD or VIEW_PASSWORD (or deleting data/secret) signs everyone out."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / "secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    passwords = f"{config.WEB_PASSWORD}\0{config.VIEW_PASSWORD}".encode()
    return hmac.new(bytes.fromhex(f.read_text().strip()), passwords, hashlib.sha256).digest()


def make_token(key, ttl, role="admin"):
    body = f"{int(time.time()) + ttl}.{role}"
    return f"{body}.{hmac.new(key, body.encode(), hashlib.sha256).hexdigest()}"


def valid_token(key, token):
    """The role a session token grants ("admin" or "viewer"), or None if it is forged or expired."""
    body, _, sig = (token or "").rpartition(".")
    exp, _, role = body.partition(".")
    good = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig.encode(), good.encode()) and exp.isdigit() and int(exp) > time.time() \
            and role in ("admin", "viewer"):
        return role
    return None


def throttled(address, now=None):
    """Seconds this address must wait before trying to sign in again, 0 if it may try now."""
    now = now or time.time()
    with failures_lock:
        recent = [t for t in failures.get(address, []) if t > now - LOGIN_WINDOW]
        failures[address] = recent
        return int(recent[0] + LOGIN_WINDOW - now) + 1 if len(recent) >= LOGIN_ATTEMPTS else 0


def failed(address):
    with failures_lock:
        failures.setdefault(address, []).append(time.time())


def same_secret(given, expected):
    return bool(expected) and hmac.compare_digest(str(given).encode(), expected.encode())


# ---------------------------------------------------------------- HTTP

STATIC = {  # allowlist: nothing outside it is ever read from disk
    "style.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "editor.js": "text/javascript; charset=utf-8",
    "login.js": "text/javascript; charset=utf-8",
    "embed.js": "text/javascript; charset=utf-8",
    "alerts.js": "text/javascript; charset=utf-8",
    "integrations.js": "text/javascript; charset=utf-8",
    "i18n.js": "text/javascript; charset=utf-8",
    "grafana.json": "application/json",
    "icon.svg": "image/svg+xml",
    "fonts/archivo.woff2": "font/woff2",
    "fonts/plex-mono-400.woff2": "font/woff2",
    "fonts/plex-mono-500.woff2": "font/woff2",
}
PAGES = {"/": "index.html", "/login": "login.html", "/embed": "embed.html"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
       "font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'")


# ---------------------------------------------------------------- diagnostics

def diagnostics(srv):
    """Everything needed to look into a server's behaviour without access to it: the raw BMC
    answers, what was made of them, settings and recent events. No password, no BMC address."""
    with srv.lock:
        state = {k: v for k, v in srv.state.items() if k != "smart_map"}
        events = list(srv.events)[:50]
    try:
        raw = srv.driver.diagnose()
    except Exception as e:  # a diagnostics bug must still return what it has
        raw = {"error": repr(e)}
    return {"format": "fan-control-diagnostics", "app": VERSION, "created": int(time.time()),
            "driver": srv.driver.kind, "driver_label": srv.driver.label, "model": srv.driver.model,
            "interval": INTERVAL, "settings": srv.settings(), "state": state, "events": events, "raw": raw}


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

    def role(self):
        """"admin", "viewer" or None for the person behind this request."""
        if not config.WEB_PASSWORD:
            return "admin"
        return valid_token(KEY, self.cookie("session"))

    def authed(self, widget=False):
        """GETs need a session of any role; changes need the admin role (see do_POST).
        The embed token opens the widget page and /api/widget, nothing else. Dashboards that fetch
        server side (Homarr's custom widgets) send it as a Bearer token instead."""
        if self.role():
            return True
        bearer = self.headers.get("Authorization", "").removeprefix("Bearer ")
        return widget and (tokens.check(self.query.get("token", [""])[0], "widget") or tokens.check(bearer, "widget"))

    def client(self):
        """The address to account sign-ins and changes to. X-Forwarded-For is only believed with
        TRUST_PROXY, since anyone can send it."""
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() if config.TRUST_PROXY and forwarded else self.client_address[0]

    def who(self):
        return f"{self.role()} at {self.client()}"

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
        if path == "/livez":
            # the process and every control loop turn, whatever the BMCs answer: Docker's health check
            stuck = stalled(SERVERS.values())
            return self.send(503 if stuck else 200, {"ok": not stuck, "stalled": stuck})
        if path == "/healthz":
            # 200 only while every BMC answers: an app tile goes red when one stops responding
            down = [s.id for s in list(SERVERS.values()) if s.state["error"]
                    or not s.state["updated"] or time.time() - s.state["updated"] > INTERVAL * 4]
            return self.send(503 if down else 200, {"ok": not down, "down": down})
        if path.startswith("/static/") and path[8:] in STATIC:
            return self.file(path[8:], STATIC[path[8:]])
        if path == "/metrics":
            bearer = self.headers.get("Authorization", "").removeprefix("Bearer ")
            needed = tokens.any_token("metrics")
            if not (tokens.check(bearer, "metrics") or (not config.WEB_PASSWORD and not needed)):
                return self.send(401 if needed else 404, {"error": "metrics need a metrics token"})
            return self.send(200, metrics(SERVERS).encode(), "text/plain; version=0.0.4; charset=utf-8")
        if path == "/login":
            if self.authed():
                return self.redirect("/")
            return self.file(PAGES[path], "text/html; charset=utf-8")
        if path == "/embed":
            if not self.authed(widget=True):
                return self.send(401, b"Sign in, or add ?token= with EMBED_TOKEN", "text/plain; charset=utf-8", frame=True)
            return self.file(PAGES[path], "text/html; charset=utf-8", frame=True)
        if path == "/api/history":
            if not self.authed():
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
        if path == "/api/widget":
            if not self.authed(widget=True):
                return self.send(401, {"error": "sign in, or send EMBED_TOKEN as a Bearer token or ?token="})
            sid = self.query.get("server", ["all"])[0] or "all"
            servers = [s for s in list(SERVERS.values()) if sid == "all" or s.id == sid]
            if sid != "all" and not servers:
                return self.send(404, {"error": "unknown server"})
            fields = widget_fields()
            return self.send(200, {"servers": [widget_view(s, fields) for s in servers], "fields": fields,
                                   "unit": "C", "interval": INTERVAL})
        if path == "/api/state":
            if not self.authed():
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
        if path == "/api/homarr-widget":
            try:
                definition = homarr_widget(self.query.get("base", [""])[0], self.query.get("server", ["all"])[0],
                                           self.query.get("scope", ["private"])[0])
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            return self.send(200, json.dumps(definition, indent=2, ensure_ascii=False).encode(), "application/json",
                             headers=[("Content-Disposition", 'attachment; filename="fan-control-homarr-widget.json"')])
        if path == "/api/overview":
            return self.send(200, {"servers": [s.summary() for s in list(SERVERS.values())], "role": self.role(),
                                   "drivers": [d.info() for d in DRIVERS.values()],
                                   "auth": bool(config.WEB_PASSWORD), "version": VERSION, "interval": INTERVAL,
                                   "update": updates.available(),
                                   "alerts": bool(webhook_of(alert_config()))})
        if path.startswith("/api/servers/") and path.endswith("/diagnostics") and path.count("/") == 4:
            srv = SERVERS.get(path.split("/")[3])
            if srv is None:
                return self.send(404, {"error": "unknown server"})
            if self.role() != "admin":
                return self.send(403, {"error": "this account can only look"})
            report = diagnostics(srv)
            stamp = time.strftime("%Y%m%d-%H%M")
            return self.send(200, json.dumps(report, indent=2, ensure_ascii=False).encode(), "application/json",
                             headers=[("Content-Disposition", f'attachment; filename="fan-control-{srv.id}-diagnostics-{stamp}.json"')])
        if path.startswith("/api/servers/") and path.count("/") == 3:
            srv = SERVERS.get(path.rsplit("/", 1)[1])
            if srv is None:
                return self.send(404, {"error": "unknown server"})
            return self.send(200, {**srv.info(), "username": srv.cfg.get("username", ""),
                                   "verify_tls": srv.cfg.get("verify_tls", False),
                                   "has_password": bool(srv.cfg.get("password"))})
        if path == "/api/alerts":
            return self.send(200, public_alert_config(alert_config()))
        if path == "/api/export":
            if self.role() != "admin":
                return self.send(403, {"error": "this account can only look"})
            secrets_too = self.query.get("secrets", [""])[0] == "1"
            stamp = time.strftime("%Y%m%d-%H%M")
            return self.send(200, json.dumps(export_config(secrets_too), indent=2).encode(), "application/json",
                             headers=[("Content-Disposition", f'attachment; filename="fan-control-{stamp}.json"')])
        if path == "/api/integrations":
            # whether each integration is switched on; the tokens themselves never leave the server
            return self.send(200, {"auth": bool(config.WEB_PASSWORD), "metrics_token": tokens.any_token("metrics"),
                                   "metrics_open": not config.WEB_PASSWORD and not tokens.any_token("metrics"),
                                   "embed_token": tokens.any_token("widget"), "interval": INTERVAL,
                                   "tokens": tokens.public(), "role": self.role(),
                                   "env_tokens": {"widget": bool(config.EMBED_TOKEN), "metrics": bool(config.METRICS_TOKEN)},
                                   "widget_fields": widget_fields(), "widget_all": WIDGET_FIELDS,
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
        if length > (1_000_000 if self.route == "/api/import" else 10000):  # backups can be large
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
            address = self.client()
            wait = throttled(address)
            if wait:
                return self.send(429, {"error": f"Too many attempts. Try again in {wait // 60 + 1} min."},
                                 headers=[("Retry-After", str(wait))])
            password = str(body.get("password", ""))
            role = ("admin" if same_secret(password, config.WEB_PASSWORD)
                    else "viewer" if same_secret(password, config.VIEW_PASSWORD) else None)
            if not role:
                failed(address)
                time.sleep(1)
                print(time.strftime("%H:%M:%S"), "WARN failed sign-in from", address, flush=True)
                return self.send(401, {"error": "Wrong password"})
            ttl = SESSION_LONG if body.get("remember") else SESSION_SHORT
            print(time.strftime("%H:%M:%S"), f"INFO {role} signed in from {address}", flush=True)
            return self.send(200, {"ok": True, "role": role}, headers=[
                self.set_session(make_token(KEY, ttl, role), ttl if body.get("remember") else None)])
        if self.route == "/api/logout":
            return self.send(200, {"ok": True}, headers=[self.set_session("", 0)])

        if not self.authed():
            return self.send(401, {"error": "sign in required"})
        if self.role() != "admin":
            return self.send(403, {"error": "this account can only look"})
        if self.route == "/api/alerts":
            try:
                cfg = validate_alerts(body, alert_config())
            except (ValueError, TypeError, AttributeError) as e:
                return self.send(400, {"error": str(e)})
            save_alerts(cfg)
            print(time.strftime("%H:%M:%S"), f"INFO Discord settings changed by {self.who()}", flush=True)
            return self.send(200, public_alert_config(cfg))
        if self.route == "/api/test-alert":
            # tests the configuration being edited, before it is saved
            try:
                cfg = validate_alerts(body.get("config") or {}, alert_config())
            except (ValueError, TypeError, AttributeError) as e:
                return self.send(400, {"error": str(e)})
            kind = body.get("kind", "failsafe")
            channel = body.get("channel")
            if channel is not None:  # ntfy, Gotify or the webhook: plain text, sample values
                if channel not in CHANNELS or kind not in ALERT_KINDS or kind == "report":
                    return self.send(400, {"error": "unknown channel or alert"})
                if not cfg["channels"][channel]["url"]:
                    return self.send(400, {"error": f"no {channel} address configured"})
                try:
                    send_channel(cfg, channel, kind, SAMPLE, "[test] ")
                except Exception as e:
                    return self.send(502, {"error": f"{channel} answered: {e}"})
                return self.send(200, {"ok": True})
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
        if self.route == "/api/import":
            with registry_lock:
                try:
                    report = import_config(body.get("data"), self.who())
                except (ValueError, TypeError, AttributeError) as e:
                    return self.send(400, {"error": f"not a Fan Control backup: {e}"})
            return self.send(200, report)
        if self.route == "/api/servers/test":
            return self.test_server(body)
        if self.route == "/api/servers/scan":
            try:
                found = scan(str(body.get("range", "")))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            known = {s.cfg.get("host", "").split(":")[0].strip("[]") for s in list(SERVERS.values())}
            for f in found:
                f["added"] = f["host"] in known
            print(time.strftime("%H:%M:%S"), f"INFO {self.who()} scanned {body.get('range')}: {len(found)} found", flush=True)
            return self.send(200, {"found": found})
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
                srv.log(f"Added by {self.who()}")
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
                    print(time.strftime("%H:%M:%S"), f"INFO {srv.name} removed by {self.who()}", flush=True)
                    return self.send(200, {"ok": True})
                try:
                    cfg = validate_server(body, srv.cfg)
                except (ValueError, TypeError) as e:
                    return self.send(400, {"error": str(e)})
                stop(srv)  # restart with the new address, credentials or driver
                srv = start(cfg)
                save_dashboard_servers()
                srv.log(f"Connection settings changed by {self.who()}")
            return self.send(200, srv.info())
        if self.route == "/api/tokens":
            try:
                token, row = tokens.create(body.get("name"), body.get("kind"))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            print(time.strftime("%H:%M:%S"), f"INFO {row['kind']} token \"{row['name']}\" created by {self.who()}", flush=True)
            return self.send(200, {"token": token, **row})
        if self.route == "/api/tokens/revoke":
            try:
                tokens.revoke(str(body.get("id", "")))
            except ValueError as e:
                return self.send(404, {"error": str(e)})
            print(time.strftime("%H:%M:%S"), f"INFO token {body.get('id')} revoked by {self.who()}", flush=True)
            return self.send(200, {"ok": True})
        if self.route == "/api/widget-config":
            try:
                save_widget_fields(body.get("fields"))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            print(time.strftime("%H:%M:%S"), f"INFO widget fields set to {widget_fields()} by {self.who()}", flush=True)
            return self.send(200, {"fields": widget_fields()})
        if self.route == "/api/smart/forget":
            srv = self.server_arg()
            if srv is None:
                return self.send(404, {"error": "unknown server"})
            srv.forget_learned(self.who())
            return self.send(200, {"ok": True})
        if self.route != "/api/settings":
            return self.send(404, {"error": "not found"})
        srv = self.server_arg()
        if srv is None:
            return self.send(404, {"error": "unknown server"})
        try:
            s = validate_settings(body, srv.settings())
        except (ValueError, TypeError) as e:
            return self.send(400, {"error": str(e)})
        srv.save_settings(s, self.who())
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
