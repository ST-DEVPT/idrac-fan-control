"""One server: its driver, settings, history and control loop. And the registry of all of them."""

import json
import logging
import os
import random
import re
import sys
import threading
import time
from collections import deque

from .alerts import alert_config, notify
from .config import (DATA_DIR, HISTORY_SECONDS, INTERVAL, LONG_BUCKET, LONG_SECONDS, SAVE_EVERY, STALL_SECONDS, read_json,
                     write_json)
from .control import (DEFAULT_SETTINGS, aggregate, apply_schedule, curve_speed, decide, failed_fans, learned_curve,
                      quiet_cap, ramped, smart_step, speed_text, validate_learned, validate_settings)
from .drivers import DRIVERS, HOST_RE, DemoDriver, DriverError, RedfishDriver

log = logging.getLogger("fanctl.server")
LEVELS = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}

# ---------------------------------------------------------------- one server

class Server:
    def __init__(self, cfg):
        self.cfg = cfg
        self.id, self.name, self.host = cfg["id"], cfg["name"], cfg.get("host", "")
        self.driver = DRIVERS[cfg["driver"]](cfg.get("host", ""), cfg.get("username", ""), cfg.get("password", ""),
                                             cfg.get("verify_tls", False), str(DATA_DIR))
        self.driver.pin = True  # remember the BMC's certificate (see drivers.PinnedHTTPSHandler)
        # the single-server setup of 1.0 keeps its settings file
        self.settings_file = DATA_DIR / ("settings.json" if cfg.get("legacy") else f"settings-{self.id}.json")
        self.history_file = DATA_DIR / f"history-{self.id}.json"
        self.smart_file = DATA_DIR / f"smart-{self.id}.json"  # what smart mode learned about this server
        self.lock = threading.RLock()  # re-entrant: log() takes it, and is called with it held
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.history = deque(maxlen=HISTORY_SECONDS // INTERVAL)
        self.long = deque(maxlen=LONG_SECONDS // LONG_BUCKET)  # 5-minute averages for 7 days
        self.bucket = []
        self.events = deque(maxlen=100)
        self.window = deque()  # (time, target) pairs for the ramp-down delay
        self.smart = {}        # smart mode controller memory
        self.learned = self.load_learned()  # smart mode: heat load -> speed that held the target
        self.learned_saved = dict(self.learned)
        self.logged = None     # last speed written to the event log
        self.hot = False       # above the "running hot" alert threshold
        self.inlet_hot = False
        self.failsafe_since = None  # start of the current failsafe, for the "failsafe for N min" alert
        self.failsafe_told = False
        self.fan_strikes = {}  # fan name -> readings in a row it looked failed
        self.fans_failed = set()
        self.last_manual = None  # (speed, rpm) of the previous cycle, for the command check
        self.probe = None        # a speed change whose effect on the RPM is being checked
        self.ignored = False     # the BMC did not follow the last checked change
        self.ignored_at = 0
        self.known_temps = set()   # every temperature sensor seen since the loop started
        self.missing = {}          # known sensor -> readings in a row it has been absent
        self.blind_tried = 0       # last attempt to hand the fans back while the BMC could not be read
        self.told_thresholds = False
        self.resume_until = 0      # easing out of a failsafe until then
        self.saved = time.time()
        self.bad_settings = None  # last validation error of the settings file, logged once
        self.pcie_applied = None
        self.dry_released = False
        self.thread = None
        self.tick = time.time()  # last turn of the control loop, for the watchdog
        self.cmd_lock = threading.Lock()  # fan commands vs. release(): never both at once
        self.state = {"sensors": None, "cpu_temp": None, "effective": None, "applied_speed": None,
                      "target_speed": None, "reason": "", "failsafe": False, "error": None,
                      "updated": None, "power": None, "model": None, "dry_run": False, "smart": None,
                      "smart_map": []}

    @property
    def control(self):
        return self.driver.control

    # ---- settings and persistence

    def settings(self):
        """The saved settings, validated. When the file is corrupt or holds something invalid the
        safe answer is the BMC's own fan control, never a curve nobody checked."""
        saved = read_json(self.settings_file, {}, f"settings of {self.name}", corrupt=False)
        if saved is False:  # corrupt, now kept aside: start again from automatic, and say so
            s = {**DEFAULT_SETTINGS, "mode": "auto"}
            write_json(self.settings_file, s)
            self.log("The settings file was corrupt and was kept aside; the BMC controls the fans until "
                     "settings are applied again", "error")
            return s
        if not isinstance(saved, dict):
            saved = {}
        if saved.get("mode") == "dell":  # 1.x name of automatic mode
            saved["mode"] = "auto"
        # 2.3 raised the floors: older files are brought inside them rather than refused
        if type(saved.get("min_speed")) is int and saved["min_speed"] < 10:
            saved["min_speed"] = 10
        if type(saved.get("failsafe_temp")) in (int, float) and saved["failsafe_temp"] > 90:
            saved["failsafe_temp"] = 90
        try:
            s = validate_settings(saved, dict(DEFAULT_SETTINGS))
            self.bad_settings = None
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            if self.bad_settings != str(e):
                self.bad_settings = str(e)
                self.log(f"Saved settings are invalid ({e}); the BMC controls the fans", "error")
            s = {**DEFAULT_SETTINGS, "mode": "auto"}
        return s

    def save_settings(self, s, who=""):
        write_json(self.settings_file, s)
        with self.lock:
            self.window.clear()  # a new setting applies now, not after the ramp-down delay
        self.log(f"Settings saved{' by ' + who if who else ''}: mode {s['mode']}")
        notify(self, "settings_changed", mode=s["mode"], failsafe=s["failsafe_temp"])
        self.wake.set()

    def load_history(self):
        try:
            data = json.loads(self.history_file.read_text())
        except (OSError, ValueError):
            return False
        cutoff = time.time() - HISTORY_SECONDS
        self.history.extend(p for p in data.get("history", []) if p.get("t", 0) > cutoff)
        self.long.extend(p for p in data.get("long", []) if p.get("t", 0) > time.time() - LONG_SECONDS)
        self.events.extend(e for e in data.get("events", []) if isinstance(e, dict))
        return bool(self.history)

    def save_history(self):
        with self.lock:
            data = {"history": list(self.history), "long": list(self.long), "events": list(self.events)}
            learned = dict(self.learned)
        try:
            write_json(self.history_file, data)
            if learned != self.learned_saved:
                write_json(self.smart_file, learned)
                self.learned_saved = learned
        except OSError as e:
            log.error("could not save history: %s", e, extra={"server": self.id})
        self.saved = time.time()

    def load_learned(self):
        try:
            return validate_learned(json.loads(self.smart_file.read_text()))
        except (OSError, ValueError):
            return {}

    def forget_learned(self, who=""):
        with self.lock:
            self.learned.clear()
            self.learned_saved = {}
            self.state["smart_map"] = []
        try:
            self.smart_file.unlink()
        except OSError:
            pass
        self.log(f"Smart mode starts learning afresh{' (' + who + ')' if who else ''}")

    def log(self, msg, level="info"):
        with self.lock:  # HTTP threads log too, while reports iterate over the events
            self.events.appendleft({"t": time.time(), "level": level, "msg": msg})
        log.log(LEVELS.get(level, logging.INFO), "%s", msg, extra={"server": self.id})

    def demo_backfill(self):
        now = time.time()
        for i in range(self.long.maxlen, HISTORY_SECONDS // LONG_BUCKET, -1):
            t = now - i * LONG_BUCKET
            day = 6 * abs(((t / 43200) % 2) - 1)  # warmer by day
            cpu = DemoDriver.cpu_at(t) + day
            speed = None if i % 97 < 3 else curve_speed(DEFAULT_SETTINGS["curve"], cpu)
            self.long.append({"t": round(t), "cpu": round(cpu, 1), "cpu_max": round(cpu + 2, 1), "speed": speed,
                              "inlet": round(DemoDriver.inlet_at(t) + day / 3, 1), "exhaust": round(cpu - 12, 1),
                              "rpm": 1800 + (speed or 60) * 120, "fanpct": None,
                              "watts": round(100 + cpu + random.uniform(-2, 2))})
        for i in range(self.history.maxlen, 0, -1):
            t = now - i * INTERVAL
            cpu = DemoDriver.cpu_at(t)
            speed = None if 1500 < i * INTERVAL < 1900 else curve_speed(DEFAULT_SETTINGS["curve"], cpu)
            self.history.append({"t": round(t), "cpu": round(cpu, 1), "speed": speed,
                                 "inlet": round(DemoDriver.inlet_at(t), 1), "exhaust": round(cpu - 12, 1),
                                 "rpm": 1800 + (speed or 60) * 120, "fanpct": None,
                                 "watts": round(100 + cpu + random.uniform(-2, 2))})

    # ---- control loop
    #
    # One cycle: read the BMC, work out what could be wrong (protect), decide a speed, send it, then
    # report. The rule throughout: when the controller does not know, the BMC decides. A missing CPU
    # reading, a sensor that stops reporting or reports a fault, a failed fan, a BMC that ignores
    # fan commands, an unreadable BMC: all of them hand the fans back.

    FAILSAFE_HOLD = 300   # s the BMC keeps the fans once a limit tripped, however fast things cool
    RESUME_SPEED = 50     # % manual control eases down from after a failsafe, instead of dropping at once
    MISSING_AFTER = 2     # readings in a row a known sensor may be absent before it counts as lost
    IGNORED_RETRY = 600   # s before manual control is tried again on a BMC that ignored it
    BLIND_RETRY = 60      # s between attempts to hand the fans back while the BMC cannot be read

    def cycle(self):
        now = time.time()
        local = time.localtime(now)
        settings, (profile_cap, profile) = apply_schedule(self.settings(), local)
        quiet = quiet_cap(settings["quiet"], local)
        # the lowest cap in force, and what to call it in the reason line
        cap, cap_name = min(((c, n) for c, n in ((quiet, "quiet hours"), (profile_cap, profile)) if c is not None),
                            default=(None, None))
        dry = self.control and settings["dry_run"]
        sensors = self.read_sensors(now, dry)
        if sensors is None:
            return
        power = sensors.pop("power")

        # only a CPU sensor counts as the CPU: standing in the inlet or a DIMM would run the fans on the
        # floor while the CPU cooks, so without one the BMC decides
        cpus = [t["value"] for t in sensors["temps"] if t["cpu"]]
        cpu = max(cpus) if cpus else None
        sensors["faults"] = self.faults(sensors, power, now)
        was_failsafe = self.state["failsafe"]
        effective, target, reason, failsafe = self.protect(settings, cpu, sensors, power, was_failsafe, now)

        smart = None
        if effective == "manual" and settings["mode"] == "smart":
            with self.lock:  # forget_learned() clears the map from the HTTP thread
                target, smart = smart_step(self.smart, self.learned, settings, cpu, sensors, now, cap)
            smart["reason"] = smart["reason"].replace(", quiet hours cap", f", {cap_name} cap")
            reason = smart["reason"]
            if target is None:
                effective, reason, smart = "auto", "smart mode has no temperature to aim at", None
        else:
            self.smart = {}  # start afresh next time smart mode takes over; what it learned stays
        speed, reason = self.choose_speed(settings, effective, target, reason, smart, cap, cap_name, was_failsafe, failsafe, now)
        effective, speed, reason, error = self.command(settings, effective, speed, reason, dry)
        if effective is None:  # stopped while reading: release() owns the fans now
            return
        self.report(settings, sensors, power, cpu, effective, target, speed, reason, failsafe, was_failsafe,
                    error, dry, smart, now)

    def read_sensors(self, now, dry):
        """The BMC's readings, or None when it cannot be read. Then the last manual speed would stay
        with nobody watching, so the fans are handed back whenever the BMC still takes a command."""
        try:
            sensors = self.driver.read()
        except DriverError as e:
            with self.lock:
                if self.state["error"] != str(e):
                    self.log(f"Cannot read the BMC: {e}", "error")
                    if self.state["error"] is None:
                        notify(self, "unreachable", error=str(e))
                self.state.update(error=str(e), updated=now)
            if self.control and not dry and now - self.blind_tried > self.BLIND_RETRY:
                self.blind_tried = now
                with self.cmd_lock:
                    if not self.stop.is_set():
                        try:
                            self.driver.set_auto()
                            with self.lock:
                                if self.state["effective"] != "auto":
                                    self.log("Fans handed back to the BMC while it cannot be read", "warn")
                                self.state.update(effective="auto", applied_speed=None,
                                                  reason="the BMC cannot be read; it controls the fans")
                        except DriverError:
                            pass  # it doesn't answer at all; the error above says so already
            return None
        self.blind_tried = 0
        self.state["model"] = self.driver.model or self.state["model"]
        if getattr(self.driver, "thresholds", None) == {} and not self.told_thresholds:
            self.told_thresholds = True
            self.log("The BMC's warning thresholds could not be read; protection follows the CPU and exhaust "
                     "limits until they can (retried every 10 min)", "warn")
        elif getattr(self.driver, "thresholds", None):
            self.told_thresholds = False
        return sensors

    def faults(self, sensors, power, now):
        """What makes the readings untrustworthy, each as a reason to hand the fans back: a sensor
        that was there and is gone, one the BMC reports as faulty, a failed fan, a BMC that ignores
        fan commands. Sensors are learned while the loop runs; a restart forgets removed hardware."""
        present = {t["name"] for t in sensors["temps"]}
        if power == "off":
            self.missing = {}  # an idle host drops its CPU sensors; that is not a fault
        else:
            self.missing = {n: self.missing.get(n, 0) + 1 for n in self.known_temps - present}
        self.known_temps |= present
        out = [f"{n} stopped reporting" for n, c in sorted(self.missing.items()) if c >= self.MISSING_AFTER]
        out += [f"{t['name']} reports a fault" for t in sensors["temps"] if t.get("ok") is False]
        out += [f"fan {n} failed" for n in sorted(self.fans_failed)]
        if self.ignored:
            if now - self.ignored_at < self.IGNORED_RETRY:
                out.append("the BMC ignored the last fan command")
            else:
                self.ignored = False
                self.log("Trying manual fan control again")
        return out

    def protect(self, settings, cpu, sensors, power, was_failsafe, now):
        """(effective, target, reason, failsafe). A failsafe holds for FAILSAFE_HOLD seconds even
        when things cool at once, so the fans don't flap between the BMC and the controller."""
        if not self.control:
            return "monitor", None, "monitoring only", False
        effective, target, reason, failsafe = decide(settings, cpu, was_failsafe, sensors)
        if power == "off":
            return "auto", None, "server is powered off", failsafe
        if was_failsafe and not failsafe and self.failsafe_since and now - self.failsafe_since < self.FAILSAFE_HOLD:
            left = self.FAILSAFE_HOLD - (now - self.failsafe_since)
            return "auto", None, f"failsafe held for {left / 60:.0f} more min", True
        return effective, target, reason, failsafe

    def choose_speed(self, settings, effective, target, reason, smart, cap, cap_name, was_failsafe, failsafe, now):
        """The speed to send in manual control, after ramp-down, caps, the driver's own floor and a
        gentle way out of a failsafe."""
        if effective != "manual":
            with self.lock:
                self.window.clear()
            return None, reason
        if was_failsafe and not failsafe:
            self.resume_until = now + max(60, settings["ramp_down_seconds"])
        with self.lock:  # save_settings() clears the window from the HTTP thread
            if smart:
                speed = target  # smart mode holds, slows down and keeps the caps on its own
            else:
                easing = now < self.resume_until
                speed = ramped(self.window, now, max(target, self.RESUME_SPEED) if easing else target,
                               settings["ramp_down_seconds"])
        if speed > target and not smart:
            reason += f", holding {speed}% for ramp-down"
        if cap is not None and not smart and speed > cap:
            speed = max(cap, settings["min_speed"])
            reason += f", {cap_name} cap {speed}%"
        if smart and now < self.resume_until and speed < self.RESUME_SPEED:
            speed = self.RESUME_SPEED
            self.smart["last"] = float(speed)  # smart mode eases down from here at its own pace
            reason += ", easing out of the failsafe"
        floor = getattr(self.driver, "min_speed", 0)
        if speed < floor:  # below this some BMCs see a stalled fan and go to full speed
            speed = floor
            reason += f", {self.driver.vendor} floor {floor}%"
        return speed, reason

    def command(self, settings, effective, speed, reason, dry):
        """Send the decision. Returns (effective, speed, reason, error); effective None means the loop
        was stopped while it worked and must not touch the fans."""
        error = None
        with self.cmd_lock:
            if self.stop.is_set():
                return None, None, reason, None
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
                    self.log(f"Third-party PCIe cooling response {'enabled' if pcie else 'disabled'}",
                             "info" if pcie else "warn")
                except DriverError as e:
                    self.log(f"Third-party PCIe cooling command refused: {e}", "error")
                self.pcie_applied = pcie  # tried once per change, not every cycle
        return effective, speed, reason, error

    def report(self, settings, sensors, power, cpu, effective, target, speed, reason, failsafe, was_failsafe,
               error, dry, smart, now):
        """Events, alerts, state and history for one cycle."""
        vals = {"cpu": "—" if cpu is None else f"{cpu:.0f}", "reason": reason, "error": error or "",
                "speed": speed_text(effective, speed)}
        with self.lock:
            prev = self.state
            # every change of who is in control, but speed changes only from 5 % on: a slow
            # ramp moves 1 % at a time and would push everything else out of the event log
            if effective != "monitor" and (effective != prev["effective"] or (
                    speed is not None and (self.logged is None or abs(speed - self.logged) >= 5))):
                self.log(("Dry run: would set fans to " if dry else "Fans → ")
                         + f"{'automatic' if effective == 'auto' else f'{speed}%'} ({reason})",
                         "warn" if failsafe or error else "info")
                self.logged = speed
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
            self.check_limits(sensors, cpu, failsafe, vals, now)
            self.check_fans(sensors["fans"], vals)
            self.check_response(effective if not dry else "dry", speed, sensors["fans"], vals)
            self.state.update(sensors=sensors, cpu_temp=cpu, effective=effective, applied_speed=speed,
                              target_speed=target, reason=reason, failsafe=failsafe, error=error,
                              updated=now, power=power, dry_run=dry, smart=smart,
                              smart_map=learned_curve(self.learned, settings, sensors["inlet"]))
            self.record({"t": round(now), "cpu": cpu, "speed": speed,
                         "inlet": sensors["inlet"], "exhaust": sensors["exhaust"],
                         "rpm": fan_avg(sensors["fans"], "rpm"), "fanpct": fan_avg(sensors["fans"], "pct"),
                         "watts": sensors["watts"]})

    def check_limits(self, sensors, cpu, failsafe, vals, now):
        """The temperature alerts: running hot, room running hot, failsafe lasting. Called with self.lock held."""
        cfg = alert_config()
        threshold = cfg["hot_threshold"]
        if cpu is not None and cpu >= threshold and not self.hot:
            self.hot = True
            notify(self, "hot", **vals)
        elif cpu is None or cpu < threshold - 3:
            self.hot = False
        inlet = sensors["inlet"]
        if inlet is not None and inlet >= cfg["inlet_threshold"] and not self.inlet_hot:
            self.inlet_hot = True
            self.log(f"Inlet air at {inlet:.0f}°C: the room is running hot", "warn")
            notify(self, "inlet_hot", **vals, inlet=f"{inlet:.0f}")
        elif inlet is None or inlet < cfg["inlet_threshold"] - 2:
            self.inlet_hot = False
        if failsafe:
            self.failsafe_since = self.failsafe_since or now
            minutes = (now - self.failsafe_since) / 60
            if minutes >= cfg["failsafe_minutes"] and not self.failsafe_told:
                self.failsafe_told = True
                self.log(f"Failsafe for {minutes:.0f} min", "error")
                notify(self, "failsafe_long", **vals, minutes=f"{minutes:.0f}")
        else:
            self.failsafe_since, self.failsafe_told = None, False

    def check_fans(self, fans, vals):
        """A fan that stops while the others spin. Two readings in a row, so one odd value is ignored.
        Called with self.lock held."""
        now_failed = set(failed_fans(fans))
        self.fan_strikes = {n: self.fan_strikes.get(n, 0) + 1 for n in now_failed}
        for f in fans:
            if self.fan_strikes.get(f["name"], 0) >= 2 and f["name"] not in self.fans_failed:
                self.fans_failed.add(f["name"])
                reading = f"{f['rpm']} rpm" if f["rpm"] is not None else f"{f['pct']}%" if f["pct"] is not None else "a fault"
                self.log(f"Fan {f['name']} failed: {reading}", "error")
                notify(self, "fan_failed", **vals, fan=f["name"], rpm=reading)
        for name in self.fans_failed - now_failed:
            self.fans_failed.discard(name)
            self.log(f"Fan {name} is spinning again")

    def check_response(self, effective, speed, fans, vals):
        """A BMC that accepts fan commands but ignores them (after a reset, or firmware that locks its
        fans) leaves the server on a speed nobody chose. After the speed moves by 20 points or more,
        the average RPM has to move the same way by 10 % within 30 s. Called with self.lock held."""
        rpm = fan_avg(fans, "rpm", whole=False)
        if effective != "manual" or speed is None or not rpm:
            self.last_manual = self.probe = None
            return
        now = time.time()
        if self.last_manual and abs(speed - self.last_manual[0]) >= 20:
            self.probe = {"t": now, "to": speed, "dir": 1 if speed > self.last_manual[0] else -1, "rpm": self.last_manual[1]}
        elif self.probe and abs(speed - self.probe["to"]) > 10:
            self.probe = None  # the speed moved on before the check was due
        self.last_manual = (speed, rpm)
        if not self.probe or now - self.probe["t"] < 30:
            return
        followed = (rpm - self.probe["rpm"]) / self.probe["rpm"] * self.probe["dir"] >= 0.10
        self.probe = None
        if not followed and not self.ignored:
            self.ignored, self.ignored_at = True, now
            self.log(f"The BMC did not follow the fans to {speed}%: their RPM stayed near {rpm:.0f}", "error")
            notify(self, "ignored", **vals)
        elif followed and self.ignored:
            self.ignored = False
            self.log("The BMC follows fan commands again")

    def record(self, point):
        """Add a reading to the 3-hour history and, every 5 minutes, an average to the 7-day one.
        Called with self.lock held."""
        self.history.append(point)
        self.bucket.append(point)
        if point["t"] - self.bucket[0]["t"] >= LONG_BUCKET:
            self.long.append(aggregate(self.bucket))
            self.bucket = []

    def release(self, timeout=None):
        """Hand the fans back to the BMC: on shutdown, removal, a change of driver or a stalled loop.
        timeout: how long to wait for a loop busy sending a command. Past it the command goes anyway,
        since a stuck loop must not keep the fans in manual."""
        if not self.control:
            return True
        locked = self.cmd_lock.acquire(timeout=-1 if timeout is None else timeout)
        try:
            self.driver.set_auto()
            return True
        except DriverError as e:
            self.log(f"Could not hand the fans back to the BMC: {e}", "error")
            return False
        finally:
            if locked:
                self.cmd_lock.release()

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
            self.tick = time.time()
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
        settings = self.settings()
        return {**self.info(), "model": s["model"], "cpu_temp": s["cpu_temp"], "effective": s["effective"],
                "mode": settings["mode"], "failsafe_temp": settings["failsafe_temp"],
                "applied_speed": s["applied_speed"], "failsafe": s["failsafe"], "error": s["error"],
                "dry_run": s["dry_run"], "reason": s["reason"],
                "power": s["power"], "updated": s["updated"],
                "watts": (s["sensors"] or {}).get("watts"), "inlet": (s["sensors"] or {}).get("inlet"),
                "fan_pct": fan_avg(fans, "pct"), "fan_rpm": fan_avg(fans, "rpm"),
                "spark": [[p["t"], p["cpu"], p["speed"]] for p in hour[::step]]}


def stalled(servers):
    """Servers whose control loop died or stopped turning: their fans sit at the last speed set."""
    now = time.time()
    return [s.id for s in list(servers) if s.thread and not s.stop.is_set()
            and (not s.thread.is_alive() or now - s.tick > STALL_SECONDS)]


def fan_avg(fans, key, whole=True):
    """Average of one fan reading ("rpm" or "pct") over the fans that report it, or None."""
    vals = [f[key] for f in fans if f.get(key) is not None]
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    return round(avg) if whole else avg


def release_all(servers, deadline=40):
    """Stop every loop, then hand every server's fans back at the same time: one slow BMC must not
    use up the time the others need before Docker gives up waiting. Returns the ids not released."""
    servers = list(servers)
    for s in servers:  # first, so no loop can send manual control again after its release
        s.stop.set()
        s.wake.set()
    done = {}
    threads = [threading.Thread(target=lambda s=s: done.__setitem__(s.id, s.release(timeout=5)), daemon=True)
               for s in servers]
    for t in threads:
        t.start()
    end = time.time() + deadline
    for t in threads:
        t.join(max(0, end - time.time()))
    return [s.id for s in servers if not done.get(s.id)]


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
            log.warning("%sDRIVER=%s is unknown; use one of %s", prefix, driver, ", ".join(DRIVERS))
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
    rows = read_json(SERVERS_FILE, [], "servers.json")
    return [s for s in rows if isinstance(s, dict) and s.get("driver") in DRIVERS] if isinstance(rows, list) else []


def save_dashboard_servers():
    rows = [{k: v for k, v in s.cfg.items() if k != "source"}
            for s in SERVERS.values() if s.cfg.get("source") == "dashboard"]
    write_json(SERVERS_FILE, rows, private=True)  # holds BMC passwords


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
        for f in (srv.settings_file, srv.history_file, srv.smart_file):
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
