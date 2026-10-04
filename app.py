"""Web dashboard and fan controller for Dell PowerEdge servers, driven through iDRAC IPMI.

Uses Dell's OEM raw IPMI commands:
  0x30 0x30 0x01 0x01        Dell dynamic fan control (default)
  0x30 0x30 0x01 0x00        manual fan control
  0x30 0x30 0x02 0xff <hex>  all fans to <hex> percent
  0x30 0xce ...              third-party PCIe card default cooling response
Works on iDRAC 6/7/8 and on iDRAC 9 up to firmware 3.30.30.30.
"""

import hashlib
import hmac
import json
import os
import random
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.environ.get("IDRAC_HOST", "local")  # "local", an iDRAC IP/hostname, or "demo"
USER = os.environ.get("IDRAC_USERNAME", "root")
PASSWORD = os.environ.get("IDRAC_PASSWORD", "calvin")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8080"))
INTERVAL = max(5, int(os.environ.get("CHECK_INTERVAL", "15")))
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
SETTINGS_FILE = DATA_DIR / "settings.json"
WEB = Path(__file__).parent
PAGES = {"/": WEB / "index.html", "/login": WEB / "login.html"}

DEFAULT_SETTINGS = {
    "mode": "curve",  # dell | fixed | curve
    "fixed_speed": 20,
    "curve": [[30, 10], [45, 15], [55, 25], [65, 45], [72, 70]],
    "failsafe_temp": 75,  # at or above this CPU temp, hand control back to Dell
    "pcie_cooling": None,  # None = leave untouched, True/False = enforce
}

# ipmitool completion codes worth explaining instead of just echoing
HINTS = {
    "rsp=0xc1": "the iDRAC does not have this command; iDRAC 9 firmware 3.34.34.34 and later removed manual fan control",
    "rsp=0xd4": "insufficient privilege; the iDRAC user must be an Administrator",
    "rsp=0xcc": "the iDRAC rejected the fan selector; some 11th-generation servers need per-fan commands, which are not supported",
}


class IPMIError(Exception):
    pass


# ---------------------------------------------------------------- IPMI

def ipmi(*args, timeout=20):
    if HOST == "local":
        cmd = ["ipmitool", "-I", "open", *args]
    else:
        # -E reads the password from IPMI_PASSWORD so it never shows up in `ps`
        cmd = ["ipmitool", "-I", "lanplus", "-H", HOST, "-U", USER, "-E", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "IPMI_PASSWORD": PASSWORD})
    except (OSError, subprocess.TimeoutExpired) as e:
        raise IPMIError(str(e)) from e
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip() or f"ipmitool exit {r.returncode}"
        hint = next((h for code, h in HINTS.items() if code in msg), None)
        raise IPMIError(f"{msg} ({hint})" if hint else msg)
    return r.stdout


def parse_sdr(text):
    """Parse `ipmitool sdr elist full` into temperatures, fans and power.

    Line shape: 'Inlet Temp | 04h | ok | 7.1 | 23 degrees C'. Entity 3.x is a processor.
    Sensor names differ between generations ("Inlet Temp" vs "Ambient Temp", "Fan1A RPM"
    vs "FAN MOD 1A RPM"), so inlet/exhaust are matched by name pattern.
    """
    temps, fans, watts = [], [], None
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 5:
            continue
        name, _, status, entity, reading = parts
        value, _, unit = reading.partition(" ")
        try:
            value = float(value)
        except ValueError:
            continue  # "No Reading", "Disabled", discrete sensors
        if unit == "degrees C":
            temps.append({"name": name, "entity": entity, "value": value,
                          "cpu": entity.startswith("3."), "ok": status == "ok"})
        elif unit == "RPM":
            fans.append({"name": re.sub(r"\s*RPM$", "", name), "rpm": int(value), "ok": status == "ok"})
        elif unit == "Watts" and watts is None:
            watts = value
    # Dell labels every CPU sensor plain "Temp"; number them so they can be told apart
    cpus = [t for t in temps if t["cpu"]]
    if len(cpus) > 1 or (cpus and cpus[0]["name"] == "Temp"):
        for i, t in enumerate(cpus, 1):
            t["name"] = f"CPU {i}"
    find = lambda pattern: next((t["value"] for t in temps if not t["cpu"] and re.search(pattern, t["name"], re.I)), None)
    return {"temps": temps, "fans": fans, "watts": watts,
            "inlet": find(r"inlet|ambient"), "exhaust": find(r"exhaust")}


def demo_sdr():
    """Simulated readings so the UI can be tried without a server (IDRAC_HOST=demo)."""
    speed = state.get("applied_speed") or 40
    t = time.time()
    cpu = demo_cpu(t)
    rpm = int(1800 + speed * 120)
    lines = [f"Inlet Temp | 04h | ok | 7.1 | {demo_inlet(t):.0f} degrees C",
             f"Exhaust Temp | 01h | ok | 7.1 | {cpu - 12:.0f} degrees C",
             f"Temp | 0Eh | ok | 3.1 | {cpu:.0f} degrees C",
             f"Temp | 0Fh | ok | 3.2 | {cpu - 3:.0f} degrees C",
             f"Pwr Consumption | 77h | ok | 7.1 | {100 + cpu + random.uniform(-2, 2):.0f} Watts"]
    lines += [f"Fan{i} RPM | 3{i}h | ok | 7.1 | {rpm + random.randint(-60, 60)} RPM" for i in range(1, 7)]
    return "\n".join(lines)


def demo_cpu(t):
    return 44 + 9 * abs(((t / 1800) % 2) - 1) + random.uniform(-.4, .4)


def demo_inlet(t):
    return 22 + abs(((t / 5400) % 2) - 1)


def set_dell_auto():
    if HOST != "demo":
        ipmi("raw", "0x30", "0x30", "0x01", "0x01")


def set_fixed(speed):
    if HOST != "demo":
        ipmi("raw", "0x30", "0x30", "0x01", "0x00")
        try:
            ipmi("raw", "0x30", "0x30", "0x02", "0xff", f"0x{speed:02x}")
        except IPMIError:
            set_dell_auto()  # never leave manual mode on with an unknown speed
            raise


def set_pcie_cooling(enabled):
    if HOST != "demo":
        ipmi("raw", "0x30", "0xce", "0x00", "0x16", "0x05", "0x00", "0x00", "0x00",
             "0x05", "0x00", "0x00" if enabled else "0x01", "0x00", "0x00")


# ---------------------------------------------------------------- control logic

def curve_speed(curve, temp):
    """Linear interpolation over [[temp, speed], ...]; flat beyond both ends."""
    pts = sorted(curve)
    if temp <= pts[0][0]:
        return pts[0][1]
    for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
        if temp <= t1:
            return round(s0 + (s1 - s0) * (temp - t0) / (t1 - t0)) if t1 > t0 else s1
    return pts[-1][1]


def decide(settings, cpu_temp):
    """Return ('dell', None, reason) or ('manual', speed, reason)."""
    if cpu_temp is None:
        return "dell", None, "no CPU temperature reading"
    if settings["mode"] == "dell":
        return "dell", None, "Dell mode selected"
    if cpu_temp >= settings["failsafe_temp"]:
        return "dell", None, f"CPU {cpu_temp:.0f}°C ≥ failsafe {settings['failsafe_temp']}°C"
    if settings["mode"] == "fixed":
        return "manual", settings["fixed_speed"], "fixed speed"
    return "manual", curve_speed(settings["curve"], cpu_temp), f"curve at {cpu_temp:.0f}°C"


def validate_settings(new):
    s = {**load_settings(), **{k: v for k, v in new.items() if k in DEFAULT_SETTINGS}}
    if s["mode"] not in ("dell", "fixed", "curve"):
        raise ValueError("mode must be dell, fixed or curve")
    if not (type(s["fixed_speed"]) is int and 0 <= s["fixed_speed"] <= 100):
        raise ValueError("fixed speed must be a whole number from 0 to 100")
    if not (type(s["failsafe_temp"]) in (int, float) and 40 <= s["failsafe_temp"] <= 100):
        raise ValueError("failsafe temperature must be between 40 and 100 °C")
    c = s["curve"]
    if not (isinstance(c, list) and 2 <= len(c) <= 10 and all(
            isinstance(p, list) and len(p) == 2 and all(type(x) in (int, float) for x in p)
            and 0 <= p[0] <= 110 and 0 <= p[1] <= 100 for p in c)):
        raise ValueError("the curve needs 2 to 10 [temperature, speed] points, speed 0-100")
    s["curve"] = sorted([round(p[0]), round(p[1])] for p in c)
    if s["pcie_cooling"] not in (None, True, False):
        raise ValueError("pcie_cooling must be null, true or false")
    return s


def load_settings():
    try:
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


def save_settings(s):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    tmp.replace(SETTINGS_FILE)


# ---------------------------------------------------------------- sessions

SESSION_SHORT = 12 * 3600
SESSION_LONG = 30 * 86400
login_lock = threading.Lock()


def session_key():
    """Signing key derived from a persisted random secret and the password, so changing
    WEB_PASSWORD (or deleting data/secret) signs everyone out."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / "secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    return hmac.new(bytes.fromhex(f.read_text().strip()), WEB_PASSWORD.encode(), hashlib.sha256).digest()


def make_token(key, ttl):
    exp = str(int(time.time()) + ttl)
    return f"{exp}.{hmac.new(key, exp.encode(), hashlib.sha256).hexdigest()}"


def valid_token(key, token):
    exp, _, sig = (token or "").partition(".")
    good = hmac.new(key, exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig.encode(), good.encode()) and exp.isdigit() and int(exp) > time.time()


# ---------------------------------------------------------------- controller thread

lock = threading.Lock()
wake = threading.Event()
history = deque(maxlen=3 * 3600 // INTERVAL)  # always 3 h, whatever the interval
events = deque(maxlen=50)
state = {"sensors": None, "cpu_temp": None, "effective": None, "applied_speed": None,
         "reason": "", "failsafe": False, "error": None, "updated": None, "power": None,
         "pcie_applied": None, "model": None}


def log(msg, level="info"):
    events.appendleft({"t": time.time(), "level": level, "msg": msg})
    print(time.strftime("%H:%M:%S"), level.upper(), msg, flush=True)


def cycle():
    settings = load_settings()
    try:
        sensors = parse_sdr(demo_sdr() if HOST == "demo" else ipmi("sdr", "elist", "full"))
        power = "on" if HOST == "demo" else ("on" if "is on" in ipmi("chassis", "power", "status") else "off")
    except IPMIError as e:
        with lock:
            if state["error"] != str(e):
                log(f"iDRAC unreachable: {e}", "error")
            state.update(error=str(e), updated=time.time())
        return

    cpus = [t["value"] for t in sensors["temps"] if t["cpu"]]
    cpu = max(cpus) if cpus else max((t["value"] for t in sensors["temps"]), default=None)
    effective, speed, reason = decide(settings, cpu)
    failsafe = settings["mode"] != "dell" and cpu is not None and cpu >= settings["failsafe_temp"]
    if power == "off":
        effective, speed, reason = "dell", None, "server is powered off"

    error = None
    try:
        if effective == "dell":
            set_dell_auto()
        else:
            set_fixed(speed)  # re-sent every cycle: an iDRAC reset silently returns to Dell mode
    except IPMIError as e:
        error = f"fan command refused: {e}"
        effective, speed, reason = "dell", None, "iDRAC refused the fan command"
    if settings["pcie_cooling"] is not None and settings["pcie_cooling"] != state["pcie_applied"]:
        try:
            set_pcie_cooling(settings["pcie_cooling"])
            log(f"Third-party PCIe cooling response {'enabled' if settings['pcie_cooling'] else 'disabled'}")
        except IPMIError as e:
            log(f"Third-party PCIe cooling command refused: {e}", "error")
        state["pcie_applied"] = settings["pcie_cooling"]  # tried once per change, not every cycle

    with lock:
        if (effective, speed) != (state["effective"], state["applied_speed"]):
            log(f"Fans → {'Dell automatic' if effective == 'dell' else f'{speed}%'} ({reason})",
                "warn" if failsafe or error else "info")
        if error and error != state["error"]:
            log(error, "error")
        state.update(sensors=sensors, cpu_temp=cpu, effective=effective, applied_speed=speed,
                     reason=reason, failsafe=failsafe, error=error, updated=time.time(), power=power)
        rpms = [f["rpm"] for f in sensors["fans"]]
        history.append({"t": round(time.time()), "cpu": cpu, "speed": speed,
                        "inlet": sensors["inlet"], "exhaust": sensors["exhaust"],
                        "rpm": round(sum(rpms) / len(rpms)) if rpms else None,
                        "watts": sensors["watts"]})


def demo_backfill():
    """Three hours of plausible past so the demo charts have something to show."""
    now = time.time()
    for i in range(history.maxlen, 0, -1):
        t = now - i * INTERVAL
        cpu = demo_cpu(t)
        speed = None if 1500 < i * INTERVAL < 1900 else curve_speed(DEFAULT_SETTINGS["curve"], cpu)
        history.append({"t": round(t), "cpu": round(cpu, 1), "speed": speed,
                        "inlet": round(demo_inlet(t), 1), "exhaust": round(cpu - 12, 1),
                        "rpm": 1800 + (speed or 60) * 120,
                        "watts": round(100 + cpu + random.uniform(-2, 2))})


def controller():
    log(f"Controller started: iDRAC {HOST}, every {INTERVAL}s")
    if HOST == "demo":
        state["model"] = "PowerEdge (demo)"
        demo_backfill()
    else:
        try:
            fru = ipmi("fru", "print", "0")
            state["model"] = next((line.split(":", 1)[1].strip() for line in fru.splitlines()
                                   if line.strip().startswith("Product Name")), None)
        except IPMIError:
            pass  # cosmetic only
    while True:
        try:
            cycle()
        except Exception as e:  # never let a bug kill the loop and leave fans pinned low
            log(f"Controller error: {e!r}; handing fans back to Dell", "error")
            try:
                set_dell_auto()
            except IPMIError:
                pass
        wake.wait(INTERVAL)
        wake.clear()


def shutdown(*_):
    log("Stopping: handing fans back to Dell automatic control")
    try:
        set_dell_auto()
    except IPMIError as e:
        print("could not restore Dell mode:", e, file=sys.stderr)
    os._exit(0)


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "idrac-fan-control"
    sys_version = ""

    def log_message(self, *_):
        pass

    def cookie(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def authed(self):
        return not WEB_PASSWORD or valid_token(KEY, self.cookie("session"))

    def send(self, code, body=b"", ctype="application/json", headers=()):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, to):
        self.send(303, headers=[("Location", to)])

    def set_session(self, value, max_age):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        age = f"; Max-Age={max_age}" if max_age is not None else ""
        return ("Set-Cookie", f"session={value}; Path=/; HttpOnly; SameSite=Strict{secure}{age}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            ok = state["updated"] and time.time() - state["updated"] < INTERVAL * 4
            return self.send(200 if ok else 503, {"ok": bool(ok)})
        if path == "/login":
            if self.authed():
                return self.redirect("/")
            return self.send(200, PAGES[path].read_bytes(), "text/html; charset=utf-8")
        if not self.authed():
            return self.redirect("/login") if path == "/" else self.send(401, {"error": "sign in required"})
        if path == "/":
            return self.send(200, PAGES[path].read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            with lock:
                return self.send(200, {**state, "host": HOST, "interval": INTERVAL, "auth": bool(WEB_PASSWORD),
                                       "settings": load_settings(), "history": list(history),
                                       "events": list(events)})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        # JSON only: a cross-site form cannot send this content type without a CORS preflight
        if self.headers.get("Content-Type") != "application/json":
            return self.send(415, {"error": "expected application/json"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 10000)) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
        except ValueError as e:
            return self.send(400, {"error": str(e)})

        if self.path == "/api/login":
            if not WEB_PASSWORD:
                return self.send(200, {"ok": True})
            # ponytail: one global lock + 1 s per failure caps guessing at ~1/s overall;
            # per-client lockout if this is ever exposed to the internet
            with login_lock:
                if not hmac.compare_digest(str(body.get("password", "")).encode(), WEB_PASSWORD.encode()):
                    time.sleep(1)
                    log("Failed sign-in attempt", "warn")
                    return self.send(401, {"error": "Wrong password"})
            ttl = SESSION_LONG if body.get("remember") else SESSION_SHORT
            return self.send(200, {"ok": True}, headers=[
                self.set_session(make_token(KEY, ttl), ttl if body.get("remember") else None)])
        if self.path == "/api/logout":
            return self.send(200, {"ok": True}, headers=[self.set_session("", 0)])

        if not self.authed():
            return self.send(401, {"error": "sign in required"})
        if self.path != "/api/settings":
            return self.send(404, {"error": "not found"})
        try:
            s = validate_settings(body)
        except (ValueError, TypeError) as e:
            return self.send(400, {"error": str(e)})
        save_settings(s)
        log(f"Settings saved: mode {s['mode']}")
        wake.set()
        self.send(200, s)


KEY = session_key() if WEB_PASSWORD else b""

if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # Windows consoles choke on ° and →
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    if not WEB_PASSWORD:
        print("WARNING: WEB_PASSWORD is not set; the dashboard is open to anyone who can reach it", flush=True)
    threading.Thread(target=controller, daemon=True).start()
    print(f"Listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
