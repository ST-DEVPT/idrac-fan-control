"""One server: its driver, settings, history and control loop. And the registry of all of them."""

import json
import os
import random
import re
import sys
import threading
import time
from collections import deque

from .alerts import alert_config, notify
from .config import DATA_DIR, HISTORY_SECONDS, INTERVAL, SAVE_EVERY, write_json
from .control import DEFAULT_SETTINGS, curve_speed, decide, ramped, speed_text
from .drivers import DRIVERS, HOST_RE, DemoDriver, DriverError, RedfishDriver

# ---------------------------------------------------------------- one server

class Server:
    def __init__(self, cfg):
        self.cfg = cfg
        self.id, self.name, self.host = cfg["id"], cfg["name"], cfg.get("host", "")
        self.driver = DRIVERS[cfg["driver"]](cfg.get("host", ""), cfg.get("username", ""), cfg.get("password", ""),
                                             cfg.get("verify_tls", False), str(DATA_DIR))
        # the single-server setup of 1.0 keeps its settings file
        self.settings_file = DATA_DIR / ("settings.json" if cfg.get("legacy") else f"settings-{self.id}.json")
        self.history_file = DATA_DIR / f"history-{self.id}.json"
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.history = deque(maxlen=HISTORY_SECONDS // INTERVAL)
        self.events = deque(maxlen=100)
        self.window = deque()  # (time, target) pairs for the ramp-down delay
        self.hot = False       # above the "running hot" alert threshold
        self.saved = time.time()
        self.pcie_applied = None
        self.dry_released = False
        self.thread = None
        self.cmd_lock = threading.Lock()  # fan commands vs. release(): never both at once
        self.state = {"sensors": None, "cpu_temp": None, "effective": None, "applied_speed": None,
                      "target_speed": None, "reason": "", "failsafe": False, "error": None,
                      "updated": None, "power": None, "model": None, "dry_run": False}

    @property
    def control(self):
        return self.driver.control

    # ---- settings and persistence

    def settings(self):
        try:
            s = {**DEFAULT_SETTINGS, **json.loads(self.settings_file.read_text())}
        except (OSError, ValueError):
            s = dict(DEFAULT_SETTINGS)
        if s["mode"] == "dell":  # 1.x name of automatic mode
            s["mode"] = "auto"
        return s

    def save_settings(self, s):
        write_json(self.settings_file, s)
        with self.lock:
            self.window.clear()  # a new setting applies now, not after the ramp-down delay
        self.log(f"Settings saved: mode {s['mode']}")
        notify(self, "settings_changed", mode=s["mode"], failsafe=s["failsafe_temp"])
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

    def demo_backfill(self):
        now = time.time()
        for i in range(self.history.maxlen, 0, -1):
            t = now - i * INTERVAL
            cpu = DemoDriver.cpu_at(t)
            speed = None if 1500 < i * INTERVAL < 1900 else curve_speed(DEFAULT_SETTINGS["curve"], cpu)
            self.history.append({"t": round(t), "cpu": round(cpu, 1), "speed": speed,
                                 "inlet": round(DemoDriver.inlet_at(t), 1), "exhaust": round(cpu - 12, 1),
                                 "rpm": 1800 + (speed or 60) * 120, "fanpct": None,
                                 "watts": round(100 + cpu + random.uniform(-2, 2))})

    # ---- control loop

    def cycle(self):
        settings = self.settings()
        try:
            sensors = self.driver.read()
        except DriverError as e:
            with self.lock:
                if self.state["error"] != str(e):
                    self.log(f"Cannot read the BMC: {e}", "error")
                    if self.state["error"] is None:
                        notify(self, "unreachable", error=str(e))
                self.state.update(error=str(e), updated=time.time())
            return
        power = sensors.pop("power")
        self.state["model"] = self.driver.model or self.state["model"]

        cpus = [t["value"] for t in sensors["temps"] if t["cpu"]]
        cpu = max(cpus) if cpus else max((t["value"] for t in sensors["temps"]), default=None)
        was_failsafe = self.state["failsafe"]
        if self.control:
            effective, target, reason, failsafe = decide(settings, cpu, was_failsafe, sensors)
            if power == "off":
                effective, target, reason = "auto", None, "server is powered off"
        else:
            effective, target, reason, failsafe = "monitor", None, "monitoring only", False
        speed = None
        with self.lock:  # save_settings() clears the window from the HTTP thread
            if effective == "manual":
                speed = ramped(self.window, time.time(), target, settings["ramp_down_seconds"])
            else:
                self.window.clear()
        if speed is not None and speed > target:
            reason += f", holding {speed}% for ramp-down"

        error = None
        dry = self.control and settings["dry_run"]
        with self.cmd_lock:
            if self.stop.is_set():  # stopped while reading: release() owns the fans now
                return
            if dry:
                # decide as usual, but leave the fans to the BMC: hand them over once, then send nothing
                if not self.dry_released:
                    try:
                        self.driver.set_auto()
                    except DriverError as e:
                        self.log(f"Could not hand the fans to the BMC for the dry run: {e}", "error")
                    self.dry_released = True
            elif self.control:
                self.dry_released = False
                try:
                    if effective == "auto":
                        self.driver.set_auto()
                    else:
                        self.driver.set_speed(speed)  # re-sent every cycle: a BMC reset silently returns to auto
                except DriverError as e:
                    error = f"fan command refused: {e}"
                    effective, speed, reason = "auto", None, "the BMC refused the fan command"
            pcie = settings["pcie_cooling"]
            if self.control and not dry and self.driver.pcie and pcie is not None and pcie != self.pcie_applied:
                try:
                    self.driver.set_pcie(pcie)
                    self.log(f"Third-party PCIe cooling response {'enabled' if pcie else 'disabled'}")
                except DriverError as e:
                    self.log(f"Third-party PCIe cooling command refused: {e}", "error")
                self.pcie_applied = pcie  # tried once per change, not every cycle

        vals = {"cpu": "—" if cpu is None else f"{cpu:.0f}", "reason": reason, "error": error or "",
                "speed": speed_text(effective, speed)}
        with self.lock:
            prev = self.state
            if (effective, speed) != (prev["effective"], prev["applied_speed"]) and effective != "monitor":
                self.log(("Dry run: would set fans to " if dry else "Fans → ")
                         + f"{'automatic' if effective == 'auto' else f'{speed}%'} ({reason})",
                         "warn" if failsafe or error else "info")
            if prev["error"] and not error:
                self.log("BMC responding again", "info")
                notify(self, "recovered", **vals)
            if error and error != prev["error"]:
                self.log(error, "error")
                notify(self, "refused", **vals)
            if failsafe and not was_failsafe:
                notify(self, "failsafe", **vals)
            if was_failsafe and not failsafe:
                self.log(f"Failsafe cleared at CPU {cpu:.0f}°C" if cpu is not None else "Failsafe cleared")
                notify(self, "failsafe_cleared", **vals)
            threshold = alert_config()["hot_threshold"]
            if cpu is not None and cpu >= threshold and not self.hot:
                self.hot = True
                notify(self, "hot", **vals)
            elif cpu is None or cpu < threshold - 3:
                self.hot = False
            self.state.update(sensors=sensors, cpu_temp=cpu, effective=effective, applied_speed=speed,
                              target_speed=target, reason=reason, failsafe=failsafe, error=error,
                              updated=time.time(), power=power, dry_run=dry)
            rpms = [f["rpm"] for f in sensors["fans"] if f["rpm"] is not None]
            pcts = [f["pct"] for f in sensors["fans"] if f["pct"] is not None]
            self.history.append({"t": round(time.time()), "cpu": cpu, "speed": speed,
                                 "inlet": sensors["inlet"], "exhaust": sensors["exhaust"],
                                 "rpm": round(sum(rpms) / len(rpms)) if rpms else None,
                                 "fanpct": round(sum(pcts) / len(pcts)) if pcts else None,
                                 "watts": sensors["watts"]})

    def release(self):
        """Hand the fans back to the BMC: on shutdown, removal, or a change of driver."""
        if self.control:
            with self.cmd_lock:
                try:
                    self.driver.set_auto()
                except DriverError as e:
                    print(f"[{self.id}] could not restore automatic fan control:", e, file=sys.stderr)

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name=self.id)
        self.thread.start()

    def run(self):
        restored = self.load_history()
        where = "simulated data" if self.driver.kind == "demo" else self.host or "local BMC"
        self.log(f"Started: {self.driver.label}, {where}, every {INTERVAL}s" + (", history restored" if restored else ""))
        if self.driver.kind == "demo" and not restored:
            self.demo_backfill()
        notify(self, "started")
        while not self.stop.is_set():
            try:
                self.cycle()
            except Exception as e:  # never let a bug kill the loop and leave fans pinned low
                self.log(f"Controller error: {e!r}; handing fans back to the BMC", "error")
                notify(self, "controller_error", error=repr(e))
                self.release()
            if time.time() - self.saved > SAVE_EVERY:
                self.save_history()
            self.wake.wait(INTERVAL)
            self.wake.clear()

    def info(self):
        """Server description for the browser. Never includes the password."""
        d = self.driver
        return {"id": self.id, "name": self.name, "host": self.host, "driver": d.kind, "driver_label": d.label,
                "vendor": d.vendor, "control": d.control, "pcie": d.pcie, "experimental": d.experimental,
                "monitor_reason": d.monitor_reason, "source": self.cfg.get("source", "dashboard")}

    def summary(self):
        now = time.time()
        with self.lock:
            s = dict(self.state)
            hour = [p for p in self.history if p["t"] > now - 3600]
        step = max(1, len(hour) // 60)
        fans = (s["sensors"] or {}).get("fans", [])
        return {**self.info(), "model": s["model"], "cpu_temp": s["cpu_temp"], "effective": s["effective"],
                "mode": self.settings()["mode"],
                "applied_speed": s["applied_speed"], "failsafe": s["failsafe"], "error": s["error"],
                "dry_run": s["dry_run"], "reason": s["reason"],
                "power": s["power"], "updated": s["updated"],
                "watts": (s["sensors"] or {}).get("watts"), "inlet": (s["sensors"] or {}).get("inlet"),
                "fan_pct": round(sum(f["pct"] for f in fans if f["pct"] is not None) / max(1, sum(f["pct"] is not None for f in fans)))
                if any(f["pct"] is not None for f in fans) else None,
                "fan_rpm": round(sum(f["rpm"] for f in fans if f["rpm"] is not None) / max(1, sum(f["rpm"] is not None for f in fans)))
                if any(f["rpm"] is not None for f in fans) else None,
                "spark": [[p["t"], p["cpu"], p["speed"]] for p in hour[::step]]}


# ---------------------------------------------------------------- server registry

SERVERS_FILE = DATA_DIR / "servers.json"
SERVERS = {}
registry_lock = threading.Lock()


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "server"


def unique_id(name, taken):
    sid = base = slug(name)
    n = 2
    while sid in taken:
        sid, n = f"{base}-{n}", n + 1
    return sid


def env_servers(env=os.environ):
    """Servers declared in the environment: SERVER_1_HOST, SERVER_1_DRIVER, ... for each server.
    1.x names still work: IDRAC_HOST/... for a single server and IDRAC_1_HOST/... for several.
    The driver defaults to Dell (what 1.x supported), and to demo for HOST=demo."""
    specs = [("IDRAC_", True)] if env.get("IDRAC_HOST") else []
    for family in ("SERVER", "IDRAC"):
        numbered = sorted(int(m.group(1)) for k in env if (m := re.fullmatch(family + r"_(\d+)_HOST", k)))
        specs += [(f"{family}_{n}_", False) for n in numbered]
    out, taken = [], set()
    for prefix, legacy in specs:
        host = env.get(f"{prefix}HOST", "")
        driver = env.get(f"{prefix}DRIVER") or ("demo" if host == "demo" else "dell")
        if driver not in DRIVERS:
            print(f"WARNING: {prefix}DRIVER={driver} is unknown; use one of {', '.join(DRIVERS)}", flush=True)
            continue
        name = env.get(f"{prefix}NAME") or ("Demo server" if driver == "demo" else host)
        sid = unique_id(name, taken)
        taken.add(sid)
        out.append({"id": sid, "name": name, "driver": driver, "host": "" if driver == "demo" else host,
                    "username": env.get(f"{prefix}USERNAME", "root"),
                    "password": env.get(f"{prefix}PASSWORD", "calvin"),
                    "verify_tls": env.get(f"{prefix}VERIFY_TLS", "").lower() in ("1", "true", "yes"),
                    "source": "environment", "legacy": legacy})
    return out


def dashboard_servers():
    try:
        return [s for s in json.loads(SERVERS_FILE.read_text()) if s.get("driver") in DRIVERS]
    except (OSError, ValueError):
        return []


def save_dashboard_servers():
    rows = [{k: v for k, v in s.cfg.items() if k != "source"}
            for s in SERVERS.values() if s.cfg.get("source") == "dashboard"]
    write_json(SERVERS_FILE, rows)
    try:
        SERVERS_FILE.chmod(0o600)  # holds BMC passwords
    except OSError:
        pass


def start(cfg):
    srv = Server(cfg)
    SERVERS[srv.id] = srv
    srv.start()
    return srv


def stop(srv, forget=False):
    """Stop the control loop and hand the fans back. A loop busy reading a slow BMC is not waited
    for: cmd_lock and the stop flag guarantee it sends no fan command after release(), so it can
    never race the replacement loop for the same BMC."""
    srv.stop.set()
    srv.wake.set()
    if srv.thread and srv.thread is not threading.current_thread():
        srv.thread.join(timeout=2)
    srv.release()
    srv.save_history()
    SERVERS.pop(srv.id, None)
    if forget:
        for f in (srv.settings_file, srv.history_file):
            try:
                f.unlink()
            except OSError:
                pass


def validate_server(new, current=None):
    """A server added or edited in the dashboard. On edit, an empty password keeps the stored one."""
    cfg = dict(current or {"source": "dashboard"})
    name = str(new.get("name", cfg.get("name", ""))).strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("give the server a name of 1 to 60 characters")
    driver = new.get("driver", cfg.get("driver"))
    if driver not in DRIVERS:
        raise ValueError("pick a server type")
    host = str(new.get("host", cfg.get("host", ""))).strip()
    if DRIVERS[driver].needs_host:
        m = re.fullmatch(r"(.+?)(?::(\d{1,5}))?", host)
        name_part, port = (m.group(1), m.group(2)) if m and not (host.count(":") > 1 and not host.startswith("[")) else (host, None)
        redfish = issubclass(DRIVERS[driver], RedfishDriver)
        if not HOST_RE.fullmatch(name_part) or (port and (not redfish or not 0 < int(port) < 65536)):
            raise ValueError("the address must be a host name or IP" + (", optionally with :port" if redfish else ""))
        if host == "local" and redfish:
            raise ValueError("local access only works for IPMI servers")
    else:
        host = ""
    username = str(new.get("username", cfg.get("username", ""))).strip()
    if DRIVERS[driver].needs_host and not 1 <= len(username) <= 64:
        raise ValueError("enter the BMC user name")
    password = new.get("password")
    if password:
        if len(str(password)) > 128:
            raise ValueError("the password is limited to 128 characters")
        cfg["password"] = str(password)
    elif DRIVERS[driver].needs_host and not cfg.get("password"):
        raise ValueError("enter the BMC password")
    verify = new.get("verify_tls", cfg.get("verify_tls", False))
    if type(verify) is not bool:
        raise ValueError("verify_tls must be true or false")
    cfg.update(name=name, driver=driver, host=host, username=username, verify_tls=verify)
    return cfg


def load_registry():
    taken = set()
    for cfg in env_servers() + dashboard_servers():
        if cfg["id"] in taken:  # an environment server and a saved one with the same name
            cfg["id"] = unique_id(cfg["id"], taken)
        taken.add(cfg["id"])
        cfg.setdefault("source", "dashboard")
        SERVERS[cfg["id"]] = Server(cfg)
