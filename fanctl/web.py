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
from .config import DATA_DIR, EMBED_TOKEN, INTERVAL, METRICS_TOKEN, VERSION, WEB, write_json
from .control import validate_settings
from .drivers import DRIVERS, DriverError, detect, scan
from .server import SERVERS, registry_lock, save_dashboard_servers, stalled, start, stop, unique_id, validate_server

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
        sm = st.get("smart")
        if sm and "sensor" in sm:  # smart mode in control: what it sees and why it chose that speed
            add("fanctl_smart_target_celsius", "Temperature smart mode aims at, for the sensor it follows.",
                {**lbl, "sensor": sm["sensor"]}, sm["target"])
            add("fanctl_smart_predicted_celsius", "Where that temperature is heading, 40 s on.",
                {**lbl, "sensor": sm["sensor"]}, sm["predicted"])
            add("fanctl_smart_trend_celsius_per_minute", "Trend of that temperature.", lbl, sm["trend"])
            add("fanctl_smart_learned_speed_percent", "Speed the learned map gives for the current load.", lbl, sm["learned"])
            add("fanctl_smart_trim_percent", "Correction on top of the learned map.", lbl, sm["trim"])
            add("fanctl_smart_boost", "1 while smart mode boosts the fans ahead of a trip point.", lbl, int(sm["boost"]))
            add("fanctl_smart_map_points", "Load levels smart mode has learned.", lbl, sm["points"])
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


# ---------------------------------------------------------------- backup

def export_config(secrets_too=False):
    """Servers added in the dashboard, every server's settings, and Discord. Passwords and the
    webhook only when asked for, since the file then unlocks every BMC."""
    servers = []
    for s in list(SERVERS.values()):
        if s.cfg.get("source") != "dashboard":
            continue
        row = {k: s.cfg.get(k) for k in ("id", "name", "driver", "host", "username", "verify_tls")}
        if secrets_too:
            row["password"] = s.cfg.get("password", "")
        servers.append(row)
    alerts_cfg = alert_config()
    if not secrets_too:
        alerts_cfg = {k: v for k, v in alerts_cfg.items() if k != "webhook_url"}
    return {"format": "fan-control-backup", "version": 1, "app": VERSION, "exported": int(time.time()),
            "with_secrets": secrets_too, "servers": servers,
            "settings": {s.id: s.settings() for s in list(SERVERS.values())}, "alerts": alerts_cfg,
            "widget": {"fields": widget_fields()}}


# ---------------------------------------------------------------- dashboard widgets

# What a dashboard widget (Homarr, an iframe) may show. The embed token reads only these, through
# /api/widget: never the BMC address, settings, events or error messages.
WIDGET_FIELDS = ("status", "cpu", "fans", "power", "inlet", "exhaust", "chart", "model")
WIDGET_FILE = DATA_DIR / "widget.json"


def widget_fields():
    try:
        chosen = json.loads(WIDGET_FILE.read_text())["fields"]
        if isinstance(chosen, list):
            return [f for f in WIDGET_FIELDS if f in chosen]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return [f for f in WIDGET_FIELDS if f != "model"]


def save_widget_fields(fields):
    if not isinstance(fields, list) or not all(f in WIDGET_FIELDS for f in fields):
        raise ValueError(f"fields must be a list drawn from {', '.join(WIDGET_FIELDS)}")
    write_json(WIDGET_FILE, {"fields": [f for f in WIDGET_FIELDS if f in fields]})


def widget_view(srv, fields):
    """One server as a widget may see it: its name, and only the fields shared."""
    now = time.time()
    with srv.lock:
        s = dict(srv.state)
        hour = [p for p in srv.history if p["t"] > now - 3600]
    sens = s["sensors"] or {}
    out = {"id": srv.id, "name": srv.name, "updated": s["updated"]}
    if "status" in fields:
        stale = not s["updated"] or now - s["updated"] > INTERVAL * 4
        mode = srv.settings()["mode"]
        out["health"] = "error" if s["error"] or stale else "warn" if s["failsafe"] else "ok"
        out["mode"] = ("BMC error" if s["error"] or stale else "Failsafe" if s["failsafe"]
                       else "Monitoring" if s["effective"] == "monitor" else "Automatic" if s["effective"] == "auto"
                       else {"fixed": "Fixed", "curve": "Curve", "smart": "Smart"}.get(mode, mode.title()))
    if "cpu" in fields:
        out["cpu"] = s["cpu_temp"]
    if "fans" in fields:
        pcts = [f["pct"] for f in sens.get("fans", []) if f["pct"] is not None]
        rpms = [f["rpm"] for f in sens.get("fans", []) if f["rpm"] is not None]
        out["fan_pct"] = (s["applied_speed"] if s["effective"] == "manual"
                          else round(sum(pcts) / len(pcts)) if pcts else None)
        out["fan_rpm"] = round(sum(rpms) / len(rpms)) if rpms else None
    if "power" in fields:
        out["watts"] = sens.get("watts")
    for key in ("inlet", "exhaust"):
        if key in fields:
            out[key] = sens.get(key)
    if "model" in fields:
        out["model"] = s["model"]
    if "chart" in fields:  # the last hour in 60 points, gaps left out
        pts = hour[::max(1, len(hour) // 60)]
        out["history"] = {"cpu": [p["cpu"] for p in pts if p.get("cpu") is not None],
                          "fans": [v for p in pts if (v := p["speed"] if p.get("speed") is not None else p.get("fanpct")) is not None]}
    return out


# A Homarr 2.0 Custom Widget ("homarr-custom-widget-v2"): imported under Management > Custom Widgets.
# Homarr fetches /api/widget server side with EMBED_TOKEN as a Bearer credential, which it stores
# encrypted; the definition itself carries no secret.
HOMARR_TEMPLATE = """<Stack gap="xs" p="sm" h="100%" style={{ minWidth: 0, minHeight: 0 }}>
  <Group justify="space-between" wrap="nowrap"><Text size="xs" c="dimmed" tt="uppercase" fw={700}>Fan Control</Text><RefreshButton requestId="status" label="Refresh fan status" size="xs" /></Group>
  {status.status?.loading && <Skeleton height={96} radius="md" />}
  {status.status?.ok === false && <Alert color="red" title="Fan Control unavailable">{status.status.error || "Check the address and the EMBED_TOKEN credential, then refresh."}</Alert>}
  {!status.status?.loading && status.status?.ok !== false && <ScrollArea style={{ flex: 1, minHeight: 0 }}><Stack gap="xs">
    {(data.status?.servers ?? []).length === 0 && <Alert color="gray" title="No servers">Add a server in Fan Control, or check the server option.</Alert>}
    {(data.status?.servers ?? []).map(server => <Paper key={server.id} withBorder radius="md" p="sm"><Stack gap={6}>
      <Group justify="space-between" wrap="nowrap" gap="xs">
        <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}><ColorSwatch size={9} withShadow={false} color={server.health === "error" ? "var(--mantine-color-red-6)" : server.health === "warn" ? "var(--mantine-color-orange-6)" : server.health === "ok" ? "var(--mantine-color-green-6)" : "var(--mantine-color-gray-6)"} /><Text fw={650} truncate>{server.name}</Text>{server.model && <Text size="xs" c="dimmed" truncate>{server.model}</Text>}</Group>
        {server.mode && <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>{server.mode}</Text>}
      </Group>
      <SimpleGrid cols={3} spacing="xs">
        {(data.status?.fields ?? []).includes("cpu") && <Stack gap={0}><Text size="xs" c="dimmed">CPU</Text><Text fw={700} size="xl">{server.cpu == null ? "—" : Math.round(options.unit === "fahrenheit" ? server.cpu * 9 / 5 + 32 : server.cpu) + (options.unit === "fahrenheit" ? " °F" : " °C")}</Text></Stack>}
        {(data.status?.fields ?? []).includes("fans") && <Stack gap={0}><Text size="xs" c="dimmed">Fans</Text><Text fw={700} size="xl">{server.fan_pct != null ? server.fan_pct + " %" : server.fan_rpm != null ? (server.fan_rpm / 1000).toFixed(1) + "k rpm" : "—"}</Text></Stack>}
        {(data.status?.fields ?? []).includes("power") && <Stack gap={0}><Text size="xs" c="dimmed">Power</Text><Text fw={700} size="xl">{server.watts == null ? "—" : Math.round(server.watts) + " W"}</Text></Stack>}
      </SimpleGrid>
      {options.chart && (server.history?.cpu?.length ?? 0) > 1 && <Sparkline h={34} data={server.history.cpu} curveType="linear" color="blue" fillOpacity={0.15} />}
      {options.air && ((data.status?.fields ?? []).includes("inlet") || (data.status?.fields ?? []).includes("exhaust")) && <Text size="xs" c="dimmed">{(data.status?.fields ?? []).includes("inlet") ? "Inlet " + (server.inlet == null ? "—" : Math.round(options.unit === "fahrenheit" ? server.inlet * 9 / 5 + 32 : server.inlet) + (options.unit === "fahrenheit" ? " °F" : " °C")) : ""}{(data.status?.fields ?? []).includes("inlet") && (data.status?.fields ?? []).includes("exhaust") ? " · " : ""}{(data.status?.fields ?? []).includes("exhaust") ? "Exhaust " + (server.exhaust == null ? "—" : Math.round(options.unit === "fahrenheit" ? server.exhaust * 9 / 5 + 32 : server.exhaust) + (options.unit === "fahrenheit" ? " °F" : " °C")) : ""}</Text>}
    </Stack></Paper>)}
  </Stack></ScrollArea>}
</Stack>"""


def homarr_widget(base, server="all", scope="private"):
    """The Custom Widget definition for this Fan Control, at the address Homarr will reach it on."""
    url = urlsplit(base)
    if url.scheme not in ("http", "https") or not url.netloc or url.path not in ("", "/") or url.query:
        raise ValueError("base must be the address of this dashboard, like https://fans.example.com")
    if scope not in ("private", "public", "loopback"):
        raise ValueError("scope must be private, public or loopback")
    if server != "all" and server not in SERVERS:
        raise ValueError("unknown server")
    return {
        "$schema": "homarr-custom-widget-v2",
        "name": "Fan Control" if server == "all" else f"Fan Control: {SERVERS[server].name}"[:100],
        "description": "Temperatures, fan speed and power of the servers Fan Control looks after.",
        "sources": {"default": {"name": "Fan Control", "baseUrl": f"{url.scheme}://{url.netloc}",
                                "networkScope": scope, "auth": "bearer"}},
        "requests": {"status": {"path": "/api/widget", "query": {"server": {"$option": "server"}}, "cacheSeconds": 10}},
        "options": {
            "server": {"label": "Server", "description": "A server id from Fan Control, or all", "control": "text",
                       "default": server},
            "unit": {"label": "Temperature unit", "control": "select", "default": "celsius",
                     "choices": [{"label": "Celsius", "value": "celsius"}, {"label": "Fahrenheit", "value": "fahrenheit"}]},
            "chart": {"label": "Last hour chart", "control": "switch", "default": True},
            "air": {"label": "Inlet and exhaust air", "control": "switch", "default": True},
        },
        "template": HOMARR_TEMPLATE,
    }


def import_config(data, who="import"):
    """Apply a backup: add the servers it has that are missing, then settings for every server
    that exists by then, then Discord. Everything is validated as if typed in the dashboard."""
    if not isinstance(data, dict) or data.get("format") != "fan-control-backup":
        raise ValueError("missing format marker")
    report = {"added": [], "updated": [], "skipped": []}
    for row in data.get("servers", []):
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id", ""))
        if sid in SERVERS:
            report["skipped"].append(f"{row.get('name', sid)}: already here")
            continue
        try:
            cfg = validate_server(row)
        except ValueError as e:
            report["skipped"].append(f"{row.get('name', sid)}: {e}")
            continue
        cfg["id"] = unique_id(sid or cfg["name"], SERVERS)
        start(cfg).log(f"Added from a backup by {who}")
        report["added"].append(cfg["name"])
    if report["added"]:
        save_dashboard_servers()
    for sid, settings in (data.get("settings") or {}).items():
        srv = SERVERS.get(sid)
        if not srv:
            continue
        try:
            srv.save_settings(validate_settings(settings, srv.settings()), f"{who} (backup)")
            report["updated"].append(srv.name)
        except (ValueError, TypeError) as e:
            report["skipped"].append(f"{srv.name} settings: {e}")
    if isinstance(data.get("alerts"), dict):
        try:
            save_alerts(validate_alerts(data["alerts"], alert_config()))
            report["updated"].append("Discord")
        except (ValueError, TypeError, AttributeError) as e:
            report["skipped"].append(f"Discord: {e}")
    if isinstance(data.get("widget"), dict):
        try:
            save_widget_fields(data["widget"].get("fields"))
            report["updated"].append("Widgets")
        except ValueError as e:
            report["skipped"].append(f"Widgets: {e}")
    return report


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
        return widget and (same_secret(self.query.get("token", [""])[0], EMBED_TOKEN) or same_secret(bearer, EMBED_TOKEN))

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
            if not (same_secret(bearer, METRICS_TOKEN) or (not config.WEB_PASSWORD and not METRICS_TOKEN)):
                return self.send(401 if METRICS_TOKEN else 404, {"error": "metrics need METRICS_TOKEN"})
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
        if path == "/api/export":
            if self.role() != "admin":
                return self.send(403, {"error": "this account can only look"})
            secrets_too = self.query.get("secrets", [""])[0] == "1"
            stamp = time.strftime("%Y%m%d-%H%M")
            return self.send(200, json.dumps(export_config(secrets_too), indent=2).encode(), "application/json",
                             headers=[("Content-Disposition", f'attachment; filename="fan-control-{stamp}.json"')])
        if path == "/api/integrations":
            # whether each integration is switched on; the tokens themselves never leave the server
            return self.send(200, {"auth": bool(config.WEB_PASSWORD), "metrics_token": bool(METRICS_TOKEN),
                                   "metrics_open": not config.WEB_PASSWORD and not METRICS_TOKEN,
                                   "embed_token": bool(EMBED_TOKEN), "interval": INTERVAL,
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
