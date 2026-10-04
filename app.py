"""Web dashboard and fan controller for Dell PowerEdge servers, driven through iDRAC IPMI.

Uses Dell's OEM raw IPMI commands:
  0x30 0x30 0x01 0x01        Dell dynamic fan control (default)
  0x30 0x30 0x01 0x00        manual fan control
  0x30 0x30 0x02 0xff <hex>  all fans to <hex> percent (0x00.. per fan on some 11th-gen servers)
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
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.stdout.reconfigure(errors="replace")  # Windows consoles choke on ° and →

VERSION = os.environ.get("APP_VERSION", "dev")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")
EMBED_TOKEN = os.environ.get("EMBED_TOKEN", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", "8080"))
INTERVAL = max(5, int(os.environ.get("CHECK_INTERVAL", "15")))
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
WEB = Path(__file__).parent / "web"

HISTORY_SECONDS = 3 * 3600
SAVE_EVERY = 300           # seconds between history snapshots to disk
FAILSAFE_HYSTERESIS = 3    # °C the CPU must drop below the failsafe before manual control resumes

DEFAULT_SETTINGS = {
    "mode": "curve",  # dell | fixed | curve
    "fixed_speed": 20,
    "curve": [[30, 10], [45, 15], [55, 25], [65, 45], [72, 70]],
    "failsafe_temp": 75,       # at or above this CPU temp, hand control back to Dell
    "ramp_down_seconds": 60,   # fans speed up at once, slow down only after this long
    "pcie_cooling": None,      # None = leave untouched, True/False = enforce
}

# ipmitool completion codes worth explaining instead of just echoing
HINTS = {
    "rsp=0xc1": "the iDRAC does not have this command; iDRAC 9 firmware 3.34.34.34 and later removed manual fan control",
    "rsp=0xd4": "insufficient privilege; the iDRAC user must be an Administrator",
    "rsp=0xcc": "the iDRAC rejected the fan selector",
}


class IPMIError(Exception):
    pass


def is_refusal(error):
    """The BMC answered and rejected this particular fan identifier or value."""
    return any(code in str(error) for code in ("rsp=0xcc", "rsp=0xc9"))


# ---------------------------------------------------------------- pure logic

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

    def find(pattern):
        return next((t["value"] for t in temps if not t["cpu"] and re.search(pattern, t["name"], re.I)), None)

    return {"temps": temps, "fans": fans, "watts": watts,
            "inlet": find(r"inlet|ambient"), "exhaust": find(r"exhaust")}


def curve_speed(curve, temp):
    """Linear interpolation over [[temp, speed], ...]; flat beyond both ends."""
    pts = sorted(curve)
    if temp <= pts[0][0]:
        return pts[0][1]
    for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
        if temp <= t1:
            return round(s0 + (s1 - s0) * (temp - t0) / (t1 - t0)) if t1 > t0 else s1
    return pts[-1][1]


def decide(settings, cpu_temp, was_failsafe=False):
    """Return (effective, target_speed, reason, failsafe).

    Once the failsafe trips, manual control resumes only when the CPU is
    FAILSAFE_HYSTERESIS degrees below the limit, so the fans don't flap at the edge.
    """
    if settings["mode"] == "dell":
        return "dell", None, "Dell mode selected", False
    if cpu_temp is None:
        return "dell", None, "no CPU temperature reading", False
    limit = settings["failsafe_temp"]
    if cpu_temp >= limit:
        return "dell", None, f"CPU {cpu_temp:.0f}°C ≥ failsafe {limit}°C", True
    if was_failsafe and cpu_temp > limit - FAILSAFE_HYSTERESIS:
        return "dell", None, f"CPU {cpu_temp:.0f}°C, resuming below {limit - FAILSAFE_HYSTERESIS}°C", True
    if settings["mode"] == "fixed":
        return "manual", settings["fixed_speed"], "fixed speed", False
    return "manual", curve_speed(settings["curve"], cpu_temp), f"curve at {cpu_temp:.0f}°C", False


def ramped(window, now, target, hold):
    """Fans speed up at once but slow down only after `hold` seconds of lower demand:
    the speed applied is the highest target seen in the last `hold` seconds."""
    window.append((now, target))
    while window[0][0] < now - hold:
        window.popleft()
    return max(s for _, s in window)


def validate_settings(new, current):
    s = {**current, **{k: v for k, v in new.items() if k in DEFAULT_SETTINGS}}
    if s["mode"] not in ("dell", "fixed", "curve"):
        raise ValueError("mode must be dell, fixed or curve")
    if not (type(s["fixed_speed"]) is int and 0 <= s["fixed_speed"] <= 100):
        raise ValueError("fixed speed must be a whole number from 0 to 100")
    if not (type(s["failsafe_temp"]) in (int, float) and 40 <= s["failsafe_temp"] <= 100):
        raise ValueError("failsafe temperature must be between 40 and 100 °C")
    if not (type(s["ramp_down_seconds"]) is int and 0 <= s["ramp_down_seconds"] <= 600):
        raise ValueError("ramp-down delay must be a whole number of seconds from 0 to 600")
    c = s["curve"]
    if not (isinstance(c, list) and 2 <= len(c) <= 10 and all(
            isinstance(p, list) and len(p) == 2 and all(type(x) in (int, float) for x in p)
            and 0 <= p[0] <= 110 and 0 <= p[1] <= 100 for p in c)):
        raise ValueError("the curve needs 2 to 10 [temperature, speed] points, speed 0-100")
    s["curve"] = sorted([round(p[0]), round(p[1])] for p in c)
    if s["pcie_cooling"] not in (None, True, False):
        raise ValueError("pcie_cooling must be null, true or false")
    return s


def write_json(path, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.replace(path)


# ---------------------------------------------------------------- alerts

COLORS = {"error": 0xC0301C, "warn": 0xE4501B, "ok": 0x3B7A39, "info": 0x4F4D48}


def notify(title, message, level="info"):
    """Post to the Discord webhook, in the background so a slow Discord never delays the fans."""
    if not DISCORD_WEBHOOK:
        return
    payload = {
        "username": "iDRAC Fan Control",
        "allowed_mentions": {"parse": []},  # error text must never ping @everyone
        "embeds": [{"title": title[:256], "description": message[:2000], "color": COLORS[level],
                    "timestamp": datetime.now(timezone.utc).isoformat()}],
    }

    def send():
        req = urllib.request.Request(DISCORD_WEBHOOK, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": f"idrac-fan-control/{VERSION}"})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:
            print("Discord webhook failed:", e, flush=True)

    threading.Thread(target=send, daemon=True).start()


# ---------------------------------------------------------------- one server

class Server:
    def __init__(self, sid, name, host, user, password, legacy=False):
        self.id, self.name, self.host, self.user, self.password = sid, name, host, user, password
        self.demo = host == "demo"
        # the single-server setup of earlier versions keeps its settings file
        self.settings_file = DATA_DIR / ("settings.json" if legacy else f"settings-{sid}.json")
        self.history_file = DATA_DIR / f"history-{sid}.json"
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.history = deque(maxlen=HISTORY_SECONDS // INTERVAL)
        self.events = deque(maxlen=100)
        self.window = deque()  # (time, target) pairs for the ramp-down delay
        self.fan_ids = None    # None: the 0xff broadcast works; list: per-fan identifiers
        self.saved = time.time()
        self.state = {"sensors": None, "cpu_temp": None, "effective": None, "applied_speed": None,
                      "target_speed": None, "reason": "", "failsafe": False, "error": None,
                      "updated": None, "power": None, "pcie_applied": None, "model": None}

    # ---- settings and persistence

    def settings(self):
        try:
            return {**DEFAULT_SETTINGS, **json.loads(self.settings_file.read_text())}
        except (OSError, ValueError):
            return dict(DEFAULT_SETTINGS)

    def save_settings(self, s):
        write_json(self.settings_file, s)
        with self.lock:
            self.window.clear()  # a new setting applies now, not after the ramp-down delay
        self.log(f"Settings saved: mode {s['mode']}")
        self.wake.set()

    def load_history(self):
        try:
            data = json.loads(self.history_file.read_text())
        except (OSError, ValueError):
            return False
        cutoff = time.time() - HISTORY_SECONDS
        self.history.extend(p for p in data.get("history", []) if p.get("t", 0) > cutoff)
        self.events.extend(e for e in data.get("events", []) if isinstance(e, dict))
        return bool(self.history)

    def save_history(self):
        with self.lock:
            data = {"history": list(self.history), "events": list(self.events)}
        try:
            write_json(self.history_file, data)
        except OSError as e:
            print(f"[{self.id}] could not save history: {e}", flush=True)
        self.saved = time.time()

    def log(self, msg, level="info"):
        self.events.appendleft({"t": time.time(), "level": level, "msg": msg})
        print(time.strftime("%H:%M:%S"), f"[{self.id}]", level.upper(), msg, flush=True)

    # ---- IPMI

    def ipmi(self, *args, timeout=20):
        if self.host == "local":
            cmd = ["ipmitool", "-I", "open", *args]
        else:
            # -E reads the password from IPMI_PASSWORD so it never shows up in `ps`
            cmd = ["ipmitool", "-I", "lanplus", "-H", self.host, "-U", self.user, "-E", *args]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               env={"PATH": os.environ.get("PATH", ""), "IPMI_PASSWORD": self.password})
        except (OSError, subprocess.TimeoutExpired) as e:
            raise IPMIError(str(e)) from e
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).strip() or f"ipmitool exit {r.returncode}"
            hint = next((h for code, h in HINTS.items() if code in msg), None)
            raise IPMIError(f"{msg} ({hint})" if hint else msg)
        return r.stdout

    def set_dell_auto(self):
        if not self.demo:
            self.ipmi("raw", "0x30", "0x30", "0x01", "0x01")

    def set_fixed(self, speed):
        if self.demo:
            return
        self.ipmi("raw", "0x30", "0x30", "0x01", "0x00")
        try:
            if self.fan_ids is None:
                try:
                    self.ipmi("raw", "0x30", "0x30", "0x02", "0xff", f"0x{speed:02x}")
                except IPMIError as e:
                    if not is_refusal(e):
                        raise
                    self.discover_fans(speed)
            else:
                for i in self.fan_ids:
                    self.ipmi("raw", "0x30", "0x30", "0x02", f"0x{i:02x}", f"0x{speed:02x}")
        except IPMIError:
            self.set_dell_auto()  # never leave manual mode on with an unknown speed
            raise

    def discover_fans(self, speed):
        """Some 11th-generation BMCs refuse the 0xff 'all fans' selector but accept fans one by
        one. Ask which identifiers they take instead of guessing from the sensor list."""
        ids = []
        for i in range(16):
            try:
                self.ipmi("raw", "0x30", "0x30", "0x02", f"0x{i:02x}", f"0x{speed:02x}")
                ids.append(i)
            except IPMIError as e:
                if not is_refusal(e):
                    raise
        if not ids:
            raise IPMIError("the iDRAC refused every fan identifier (rsp=0xcc)")
        self.fan_ids = ids
        self.log(f"Fans are set one by one on this server (identifiers {', '.join(map(str, ids))})")

    def set_pcie_cooling(self, enabled):
        if not self.demo:
            self.ipmi("raw", "0x30", "0xce", "0x00", "0x16", "0x05", "0x00", "0x00", "0x00",
                      "0x05", "0x00", "0x00" if enabled else "0x01", "0x00", "0x00")

    # ---- demo data

    @staticmethod
    def demo_cpu(t):
        return 44 + 9 * abs(((t / 1800) % 2) - 1) + random.uniform(-.4, .4)

    @staticmethod
    def demo_inlet(t):
        return 22 + abs(((t / 5400) % 2) - 1)

    def demo_sdr(self):
        speed = self.state.get("applied_speed") or 40
        t = time.time()
        cpu = self.demo_cpu(t)
        rpm = int(1800 + speed * 120)
        lines = [f"Inlet Temp | 04h | ok | 7.1 | {self.demo_inlet(t):.0f} degrees C",
                 f"Exhaust Temp | 01h | ok | 7.1 | {cpu - 12:.0f} degrees C",
                 f"Temp | 0Eh | ok | 3.1 | {cpu:.0f} degrees C",
                 f"Temp | 0Fh | ok | 3.2 | {cpu - 3:.0f} degrees C",
                 f"Pwr Consumption | 77h | ok | 7.1 | {100 + cpu + random.uniform(-2, 2):.0f} Watts"]
        lines += [f"Fan{i} RPM | 3{i}h | ok | 7.1 | {rpm + random.randint(-60, 60)} RPM" for i in range(1, 7)]
        return "\n".join(lines)

    def demo_backfill(self):
        now = time.time()
        for i in range(self.history.maxlen, 0, -1):
            t = now - i * INTERVAL
            cpu = self.demo_cpu(t)
            speed = None if 1500 < i * INTERVAL < 1900 else curve_speed(DEFAULT_SETTINGS["curve"], cpu)
            self.history.append({"t": round(t), "cpu": round(cpu, 1), "speed": speed,
                                 "inlet": round(self.demo_inlet(t), 1), "exhaust": round(cpu - 12, 1),
                                 "rpm": 1800 + (speed or 60) * 120,
                                 "watts": round(100 + cpu + random.uniform(-2, 2))})

    # ---- control loop

    def cycle(self):
        settings = self.settings()
        try:
            sensors = parse_sdr(self.demo_sdr() if self.demo else self.ipmi("sdr", "elist", "full"))
            power = "on" if self.demo else ("on" if "is on" in self.ipmi("chassis", "power", "status") else "off")
        except IPMIError as e:
            with self.lock:
                if self.state["error"] != str(e):
                    self.log(f"iDRAC unreachable: {e}", "error")
                    if self.state["error"] is None:
                        notify(f"{self.name}: iDRAC unreachable", str(e), "error")
                self.state.update(error=str(e), updated=time.time())
            return

        cpus = [t["value"] for t in sensors["temps"] if t["cpu"]]
        cpu = max(cpus) if cpus else max((t["value"] for t in sensors["temps"]), default=None)
        was_failsafe = self.state["failsafe"]
        effective, target, reason, failsafe = decide(settings, cpu, was_failsafe)
        if power == "off":
            effective, target, reason = "dell", None, "server is powered off"
        speed = None
        with self.lock:  # save_settings() clears the window from the HTTP thread
            if effective == "manual":
                speed = ramped(self.window, time.time(), target, settings["ramp_down_seconds"])
            else:
                self.window.clear()
        if speed is not None and speed > target:
            reason += f", holding {speed}% for ramp-down"

        error = None
        try:
            if effective == "dell":
                self.set_dell_auto()
            else:
                self.set_fixed(speed)  # re-sent every cycle: an iDRAC reset silently returns to Dell mode
        except IPMIError as e:
            error = f"fan command refused: {e}"
            effective, speed, reason = "dell", None, "iDRAC refused the fan command"
        if settings["pcie_cooling"] is not None and settings["pcie_cooling"] != self.state["pcie_applied"]:
            try:
                self.set_pcie_cooling(settings["pcie_cooling"])
                self.log(f"Third-party PCIe cooling response {'enabled' if settings['pcie_cooling'] else 'disabled'}")
            except IPMIError as e:
                self.log(f"Third-party PCIe cooling command refused: {e}", "error")
            self.state["pcie_applied"] = settings["pcie_cooling"]  # tried once per change, not every cycle

        with self.lock:
            prev = self.state
            if (effective, speed) != (prev["effective"], prev["applied_speed"]):
                self.log(f"Fans → {'Dell automatic' if effective == 'dell' else f'{speed}%'} ({reason})",
                         "warn" if failsafe or error else "info")
            if prev["error"] and not error:
                self.log("iDRAC responding again", "info")
                notify(f"{self.name}: back to normal", "The iDRAC is answering and accepting fan commands again.", "ok")
            if error and error != prev["error"]:
                self.log(error, "error")
                notify(f"{self.name}: fan command refused", f"{error}\nThe iDRAC keeps control of the fans.", "error")
            if failsafe and not was_failsafe:
                notify(f"{self.name}: failsafe", f"{reason[0].upper()}{reason[1:]}. The iDRAC took over the fans.", "warn")
            if was_failsafe and not failsafe:
                self.log(f"Failsafe cleared at CPU {cpu:.0f}°C" if cpu is not None else "Failsafe cleared")
                notify(f"{self.name}: failsafe cleared", f"CPU back to {cpu:.0f}°C, manual control resumed."
                       if cpu is not None else "Manual control resumed.", "ok")
            self.state.update(sensors=sensors, cpu_temp=cpu, effective=effective, applied_speed=speed,
                              target_speed=target, reason=reason, failsafe=failsafe, error=error,
                              updated=time.time(), power=power)
            rpms = [f["rpm"] for f in sensors["fans"]]
            self.history.append({"t": round(time.time()), "cpu": cpu, "speed": speed,
                                 "inlet": sensors["inlet"], "exhaust": sensors["exhaust"],
                                 "rpm": round(sum(rpms) / len(rpms)) if rpms else None,
                                 "watts": sensors["watts"]})

    def run(self):
        restored = self.load_history()
        self.log(f"Controller started: iDRAC {self.host}, every {INTERVAL}s"
                 + (", history restored" if restored else ""))
        if self.demo:
            self.state["model"] = "PowerEdge (demo)"
            if not restored:
                self.demo_backfill()
        else:
            try:
                fru = self.ipmi("fru", "print", "0")
                self.state["model"] = next((line.split(":", 1)[1].strip() for line in fru.splitlines()
                                            if line.strip().startswith("Product Name")), None)
            except IPMIError:
                pass  # cosmetic only
        while True:
            try:
                self.cycle()
            except Exception as e:  # never let a bug kill the loop and leave fans pinned low
                self.log(f"Controller error: {e!r}; handing fans back to Dell", "error")
                notify(f"{self.name}: controller error", f"{e!r}\nFans handed back to the iDRAC.", "error")
                try:
                    self.set_dell_auto()
                except IPMIError:
                    pass
            if time.time() - self.saved > SAVE_EVERY:
                self.save_history()
            self.wake.wait(INTERVAL)
            self.wake.clear()

    def summary(self):
        s = self.state
        return {"id": self.id, "name": self.name, "model": s["model"], "cpu_temp": s["cpu_temp"],
                "effective": s["effective"], "applied_speed": s["applied_speed"],
                "failsafe": s["failsafe"], "error": bool(s["error"])}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "server"


def load_servers(env=os.environ):
    """One server from IDRAC_HOST/IDRAC_USERNAME/IDRAC_PASSWORD/IDRAC_NAME, and/or several
    from IDRAC_1_HOST, IDRAC_1_USERNAME, ... IDRAC_N_HOST."""
    numbered = sorted(int(m.group(1)) for k in env if (m := re.fullmatch(r"IDRAC_(\d+)_HOST", k)))
    specs = [("", True)] if env.get("IDRAC_HOST") or not numbered else []
    specs += [(f"{n}_", False) for n in numbered]
    servers = {}
    for prefix, legacy in specs:
        host = env.get(f"IDRAC_{prefix}HOST", "local")
        name = env.get(f"IDRAC_{prefix}NAME") or ("Demo server" if host == "demo" else host)
        sid = base = slug(name)
        n = 2
        while sid in servers:
            sid, n = f"{base}-{n}", n + 1
        servers[sid] = Server(sid, name, host, env.get(f"IDRAC_{prefix}USERNAME", "root"),
                              env.get(f"IDRAC_{prefix}PASSWORD", "calvin"), legacy)
    return servers


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

    add("idrac_fan_control_info", "Build information.", {"version": VERSION}, 1)
    for s in servers.values():
        st, sens, lbl = s.state, s.state["sensors"] or {}, {"server": s.id, "name": s.name}
        add("idrac_up", "1 if the last iDRAC reading succeeded.", lbl, 0 if st["error"] or not st["updated"] else 1)
        add("idrac_last_update_timestamp_seconds", "Unix time of the last reading.", lbl, st["updated"])
        add("idrac_power_on", "1 if the server is powered on.", lbl,
            None if st["power"] is None else int(st["power"] == "on"))
        add("idrac_dell_control", "1 if the iDRAC's own fan control is active.", lbl,
            None if st["effective"] is None else int(st["effective"] == "dell"))
        add("idrac_failsafe_active", "1 while the failsafe temperature has handed control to the iDRAC.",
            lbl, int(st["failsafe"]))
        add("idrac_fan_speed_percent", "Fan speed set by the controller (absent in Dell mode).", lbl, st["applied_speed"])
        add("idrac_cpu_temperature_celsius", "Hottest CPU temperature.", lbl, st["cpu_temp"])
        add("idrac_inlet_temperature_celsius", "Inlet air temperature.", lbl, sens.get("inlet"))
        add("idrac_exhaust_temperature_celsius", "Exhaust air temperature.", lbl, sens.get("exhaust"))
        add("idrac_power_watts", "System power draw.", lbl, sens.get("watts"))
        for t in sens.get("temps", []):
            add("idrac_temperature_celsius", "Temperature sensor reading.",
                {**lbl, "sensor": t["name"], "entity": t["entity"]}, t["value"])
        for f in sens.get("fans", []):
            add("idrac_fan_rpm", "Fan speed.", {**lbl, "fan": f["name"]}, f["rpm"])
    return "\n".join(row for rows in out.values() for row in rows) + "\n"


# ---------------------------------------------------------------- HTTP

STATIC = {  # allowlist: nothing outside it is ever read from disk
    "style.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "login.js": "text/javascript; charset=utf-8",
    "embed.js": "text/javascript; charset=utf-8",
    "icon.svg": "image/svg+xml",
    "fonts/archivo.woff2": "font/woff2",
    "fonts/plex-mono-400.woff2": "font/woff2",
    "fonts/plex-mono-500.woff2": "font/woff2",
}
PAGES = {"/": "index.html", "/login": "login.html", "/embed": "embed.html"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "idrac-fan-control"
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
        if not WEB_PASSWORD or valid_token(KEY, self.cookie("session")):
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
        return SERVERS.get(sid) or (None if sid else next(iter(SERVERS.values())))

    def parse(self):
        url = urlsplit(self.path)
        self.route, self.query = url.path, parse_qs(url.query)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.parse()
        path = self.route
        if path == "/healthz":
            stale = [s.id for s in SERVERS.values()
                     if not s.state["updated"] or time.time() - s.state["updated"] > INTERVAL * 4]
            return self.send(503 if stale else 200, {"ok": not stale, "stale": stale})
        if path.startswith("/static/") and path[8:] in STATIC:
            return self.file(path[8:], STATIC[path[8:]])
        if path == "/metrics":
            bearer = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not (same_secret(bearer, METRICS_TOKEN) or (not WEB_PASSWORD and not METRICS_TOKEN)):
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
        if path == "/api/state":
            if not self.authed(read_only=True):
                return self.send(401, {"error": "sign in required"})
            srv = self.server_arg()
            if srv is None:
                return self.send(404, {"error": "unknown server"})
            with srv.lock:
                return self.send(200, {**srv.state, "id": srv.id, "name": srv.name, "host": srv.host,
                                       "interval": INTERVAL, "auth": bool(WEB_PASSWORD),
                                       "alerts": bool(DISCORD_WEBHOOK), "version": VERSION,
                                       "settings": srv.settings(), "history": list(srv.history),
                                       "events": list(srv.events),
                                       "servers": [s.summary() for s in SERVERS.values()]})
        if not self.authed():
            return self.redirect("/login") if path == "/" else self.send(401, {"error": "sign in required"})
        if path == "/":
            return self.file(PAGES[path], "text/html; charset=utf-8")
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
            if not WEB_PASSWORD:
                return self.send(200, {"ok": True})
            # ponytail: one global lock + 1 s per failure caps guessing at ~1/s overall;
            # per-client lockout if this is ever exposed to the internet
            with login_lock:
                if not same_secret(body.get("password", ""), WEB_PASSWORD):
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
        if self.route == "/api/test-alert":
            if not DISCORD_WEBHOOK:
                return self.send(400, {"error": "DISCORD_WEBHOOK_URL is not set"})
            notify("Test alert", "Alerts from iDRAC Fan Control reach this channel.", "info")
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
        srv.save_settings(s)
        self.send(200, s)


def data_dir_writable():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DATA_DIR / ".write-test"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


if not data_dir_writable():
    sys.exit(f"ERROR: cannot write to {DATA_DIR.resolve()}. The container runs as uid 1000; "
             "fix the volume's owner with: chown -R 1000:1000 <host folder>")
SERVERS = load_servers()
KEY = session_key() if WEB_PASSWORD else b""

if DISCORD_WEBHOOK and not re.match(r"https://(discord|discordapp)\.com/api/webhooks/", DISCORD_WEBHOOK):
    print("WARNING: DISCORD_WEBHOOK_URL is not a Discord webhook URL; alerts disabled", flush=True)
    DISCORD_WEBHOOK = ""


def shutdown(*_):
    for s in SERVERS.values():
        s.log("Stopping: handing fans back to Dell automatic control")
        try:
            s.set_dell_auto()
        except IPMIError as e:
            print(f"[{s.id}] could not restore Dell mode:", e, file=sys.stderr)
        s.save_history()
    os._exit(0)


def serve():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    if not WEB_PASSWORD:
        print("WARNING: WEB_PASSWORD is not set; the dashboard is open to anyone who can reach it", flush=True)
    for srv in SERVERS.values():
        threading.Thread(target=srv.run, daemon=True, name=srv.id).start()
    print(f"iDRAC Fan Control {VERSION} listening on :{PORT} for {len(SERVERS)} server(s)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    serve()
