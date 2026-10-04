"""Hardware drivers: how each kind of server is read and, where the vendor allows it, controlled.

Every driver returns readings in one shape:
    {"temps": [{"name", "entity", "value", "cpu", "ok"}],
     "fans":  [{"name", "rpm" or None, "pct" or None, "ok"}],
     "watts", "inlet", "exhaust", "power": "on" | "off" | None}
and, if it can control fans, implements set_auto() and set_speed(percent).
"""

import base64
import json
import math
import os
import random
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request


class DriverError(Exception):
    pass


HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]{2,45}\]|[0-9A-Fa-f]*:[0-9A-Fa-f:.]{1,44}")

# ipmitool completion codes worth explaining instead of just echoing
HINTS = {
    "rsp=0xc1": "the BMC does not have this command; iDRAC 9 firmware 3.34.34.34 and later removed manual fan control",
    "rsp=0xd4": "insufficient privilege; the user must be an Administrator",
    "rsp=0xcc": "the BMC rejected the fan selector",
}


def bare_host(host):
    """Address without brackets or :port, for tools that take the port separately."""
    if host.startswith("["):
        return host[1:host.index("]")]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_refusal(error):
    """The BMC answered and rejected this particular fan identifier or value."""
    return any(code in str(error) for code in ("rsp=0xcc", "rsp=0xc9"))


def finish(temps, fans, watts=None, power=None):
    """Number the CPUs and pick inlet/exhaust by name: every vendor labels them differently."""
    cpus = [t for t in temps if t["cpu"]]
    if len(cpus) > 1 or (cpus and cpus[0]["name"] == "Temp"):
        for i, t in enumerate(cpus, 1):
            t["name"] = f"CPU {i}"

    def find(pattern):
        return next((t["value"] for t in temps if not t["cpu"] and re.search(pattern, t["name"], re.I)), None)

    return {"temps": temps, "fans": fans, "watts": watts, "power": power,
            "inlet": find(r"inlet|ambient|intake"), "exhaust": find(r"exhaust|outlet")}


def parse_sdr(text):
    """Parse `ipmitool sdr elist full`. Line shape: 'Inlet Temp | 04h | ok | 7.1 | 23 degrees C'.
    Entity 3.x is a processor. Names differ between generations ("Inlet Temp" vs "Ambient Temp",
    "Fan1A RPM" vs "FAN MOD 1A RPM"), so inlet/exhaust are matched by pattern."""
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
                          "cpu": entity.startswith("3.") or bool(re.match(r"CPU\d* Temp", name)),
                          "ok": status == "ok"})
        elif unit == "RPM":
            fans.append({"name": re.sub(r"\s*RPM$", "", name), "rpm": int(value), "pct": None, "ok": status == "ok"})
        elif unit == "Watts" and watts is None:
            watts = value
    return finish(temps, fans, watts)


def parse_redfish(thermal, power=None):
    """Redfish Thermal (+ Power) resources. iLO 4 says FanName/CurrentReading/Units, the
    standard says Name/Reading/ReadingUnits."""
    temps, fans = [], []
    for t in thermal.get("Temperatures", []):
        value = t.get("ReadingCelsius")
        state = (t.get("Status") or {}).get("State", "Enabled")
        if value is None or state in ("Absent", "Disabled") or value <= 0:
            continue
        name = t.get("Name") or t.get("MemberId") or "Sensor"
        context = t.get("PhysicalContext") or ""
        temps.append({"name": name, "entity": context, "value": float(value),
                      "cpu": context == "CPU" or bool(re.search(r"\bCPU\s*\d", name)),
                      "ok": (t.get("Status") or {}).get("Health", "OK") in ("OK", None)})
    for f in thermal.get("Fans", []):
        state = (f.get("Status") or {}).get("State", "Enabled")
        reading = f.get("Reading", f.get("CurrentReading"))
        if reading is None or state == "Absent":
            continue
        unit = (f.get("ReadingUnits") or f.get("Units") or "").lower()
        is_pct = unit.startswith("percent") or (not unit and reading <= 100)
        fans.append({"name": f.get("Name") or f.get("FanName") or "Fan",
                     "rpm": None if is_pct else int(reading), "pct": int(reading) if is_pct else None,
                     "ok": (f.get("Status") or {}).get("Health", "OK") in ("OK", None)})
    watts = None
    for pc in (power or {}).get("PowerControl", []):
        if pc.get("PowerConsumedWatts") is not None:
            watts = float(pc["PowerConsumedWatts"])
            break
    return temps, fans, watts


class Driver:
    kind = ""
    label = ""
    vendor = ""
    description = ""
    control = False        # can set fan speeds
    pcie = False           # supports Dell's third-party PCIe cooling switch
    experimental = False
    needs_host = True
    monitor_reason = ""    # why a monitoring-only driver cannot control fans

    def __init__(self, host="", username="", password="", verify_tls=False, data_dir="."):
        self.host, self.username, self.password = host, username, password
        self.verify_tls, self.data_dir = verify_tls, data_dir
        self.model = None

    def read(self):
        raise NotImplementedError

    def set_auto(self):
        pass

    def set_speed(self, pct):
        raise DriverError("this driver cannot control fans")

    def set_pcie(self, enabled):
        raise DriverError("not supported")

    @classmethod
    def info(cls):
        return {"kind": cls.kind, "label": cls.label, "vendor": cls.vendor, "description": cls.description,
                "control": cls.control, "pcie": cls.pcie, "experimental": cls.experimental,
                "needs_host": cls.needs_host, "monitor_reason": cls.monitor_reason}


# ---------------------------------------------------------------- IPMI

class IPMIDriver(Driver):
    kind = "ipmi"
    label = "Other server (IPMI)"
    vendor = "Generic"
    description = "Any BMC that answers IPMI over LAN: temperatures, fans and power. Monitoring only."
    monitor_reason = "IPMI has no standard command for fan speed"

    def ipmi(self, *args, timeout=20):
        if self.host == "local":
            cmd = ["ipmitool", "-I", "open", *args]
        else:
            # -E reads the password from IPMI_PASSWORD so it never shows up in `ps`
            cmd = ["ipmitool", "-I", "lanplus", "-H", self.host, "-U", self.username, "-E", *args]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               env={"PATH": os.environ.get("PATH", ""), "IPMI_PASSWORD": self.password})
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DriverError(str(e)) from e
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).strip() or f"ipmitool exit {r.returncode}"
            hint = next((h for code, h in HINTS.items() if code in msg), None)
            raise DriverError(f"{msg} ({hint})" if hint else msg)
        return r.stdout

    def read(self):
        data = parse_sdr(self.ipmi("sdr", "elist", "full"))
        data["power"] = "on" if "is on" in self.ipmi("chassis", "power", "status") else "off"
        if self.model is None:
            try:
                fru = self.ipmi("fru", "print", "0")
                self.model = next((line.split(":", 1)[1].strip() for line in fru.splitlines()
                                   if line.strip().startswith("Product Name")), "")
            except DriverError:
                self.model = ""
        return data


class DellDriver(IPMIDriver):
    kind = "dell"
    label = "Dell PowerEdge (iDRAC)"
    vendor = "Dell"
    description = "iDRAC 6, 7 and 8, and iDRAC 9 up to firmware 3.30.30.30. Full fan control."
    control = True
    pcie = True
    monitor_reason = ""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fan_ids = None  # None: the 0xff broadcast works; list: per-fan identifiers

    def set_auto(self):
        self.ipmi("raw", "0x30", "0x30", "0x01", "0x01")

    def set_speed(self, pct):
        self.ipmi("raw", "0x30", "0x30", "0x01", "0x00")
        try:
            if self.fan_ids is None:
                try:
                    self.ipmi("raw", "0x30", "0x30", "0x02", "0xff", f"0x{pct:02x}")
                except DriverError as e:
                    if not is_refusal(e):
                        raise
                    self.discover_fans(pct)
            else:
                for i in self.fan_ids:
                    self.ipmi("raw", "0x30", "0x30", "0x02", f"0x{i:02x}", f"0x{pct:02x}")
        except DriverError:
            self.set_auto()  # never leave manual mode on with an unknown speed
            raise

    def discover_fans(self, pct):
        """Some 11th-generation BMCs refuse the 0xff 'all fans' selector but accept fans one by
        one. Ask which identifiers they take instead of guessing from the sensor list."""
        ids = []
        for i in range(16):
            try:
                self.ipmi("raw", "0x30", "0x30", "0x02", f"0x{i:02x}", f"0x{pct:02x}")
                ids.append(i)
            except DriverError as e:
                if not is_refusal(e):
                    raise
        if not ids:
            raise DriverError("the iDRAC refused every fan identifier (rsp=0xcc)")
        self.fan_ids = ids

    def set_pcie(self, enabled):
        self.ipmi("raw", "0x30", "0xce", "0x00", "0x16", "0x05", "0x00", "0x00", "0x00",
                  "0x05", "0x00", "0x00" if enabled else "0x01", "0x00", "0x00")


class SupermicroDriver(IPMIDriver):
    kind = "supermicro"
    label = "Supermicro (X9 / X10 / X11)"
    vendor = "Supermicro"
    description = "Switches the BMC to Full fan mode and sets the CPU and peripheral zones. Experimental."
    control = True
    experimental = True
    monitor_reason = ""

    def set_auto(self):
        self.ipmi("raw", "0x30", "0x45", "0x01", "0x02")  # Optimal mode

    def set_speed(self, pct):
        try:
            self.ipmi("raw", "0x30", "0x45", "0x01", "0x01")  # Full mode, so the BMC stops overriding
            for zone in ("0x00", "0x01"):  # CPU zone, peripheral zone
                self.ipmi("raw", "0x30", "0x70", "0x66", "0x01", zone, f"0x{pct:02x}")
        except DriverError:
            self.set_auto()
            raise


# ---------------------------------------------------------------- Redfish

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the Basic auth header to wherever the BMC points: refuse it."""
    def redirect_request(self, *args, **kwargs):
        return None


class RedfishDriver(Driver):
    kind = "redfish"
    label = "HPE iLO 4 / 5 / 6 and other Redfish"
    vendor = "HPE"
    description = "Any Redfish BMC (HPE iLO, Lenovo XCC, recent Supermicro, ...): temperatures, fans and power. Monitoring only."
    monitor_reason = "the vendor firmware does not expose fan control"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.paths = None  # discovered once: thermal, power, system

    def get(self, path):
        if self.verify_tls:
            ctx = ssl.create_default_context()
        else:
            ctx = ssl._create_unverified_context()  # BMCs ship self-signed certificates
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        host = f"[{self.host}]" if self.host.count(":") > 1 and not self.host.startswith("[") else self.host
        req = urllib.request.Request(f"https://{host}{path}", headers={
            "Authorization": f"Basic {token}", "Accept": "application/json", "OData-Version": "4.0"})
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), NoRedirect)
        try:
            with opener.open(req, timeout=20) as r:
                return json.loads(r.read(5_000_000))
        except urllib.error.HTTPError as e:
            raise DriverError(f"Redfish {path}: HTTP {e.code} {e.reason}"
                              + (" (check the user name and password)" if e.code == 401 else "")) from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise DriverError(f"Redfish {path}: {getattr(e, 'reason', e)}") from e

    def discover(self):
        chassis = self.get("/redfish/v1/Chassis")["Members"][0]["@odata.id"]
        c = self.get(chassis)
        systems = self.get("/redfish/v1/Systems")["Members"][0]["@odata.id"]
        self.paths = {"thermal": (c.get("Thermal") or {}).get("@odata.id", chassis.rstrip("/") + "/Thermal"),
                      "power": (c.get("Power") or {}).get("@odata.id"), "system": systems}

    def read(self):
        if self.paths is None:
            try:
                self.discover()
            except (KeyError, IndexError, TypeError) as e:
                raise DriverError(f"unexpected Redfish layout: {e!r}") from e
        thermal = self.get(self.paths["thermal"])
        power = None
        if self.paths["power"]:
            try:
                power = self.get(self.paths["power"])
            except DriverError:
                pass  # not every BMC reports power; temperatures still count
        system = self.get(self.paths["system"])
        self.model = system.get("Model") or self.model
        temps, fans, watts = parse_redfish(thermal, power)
        state = (system.get("PowerState") or "").lower()
        return finish(temps, fans, watts, "on" if state == "on" else "off" if state == "off" else None)


class ILO4UnlockedDriver(RedfishDriver):
    kind = "ilo4-unlocked"
    label = "HPE iLO 4 with unlocked firmware"
    vendor = "HPE"
    description = ("ProLiant Gen8 / Gen9 running the community-patched iLO 4 2.77. Reads over Redfish, "
                   "caps fan speed over SSH. Experimental.")
    control = True
    experimental = True
    monitor_reason = ""
    RESEND = 600  # re-send unchanged caps every 10 min, in case the iLO restarted

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.sent, self.sent_at, self.fan_count = None, 0, None

    def ssh(self, command):
        cmd = ["sshpass", "-e", "ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=no",
               "-o", "StrictHostKeyChecking=accept-new",
               "-o", f"UserKnownHostsFile={os.path.join(self.data_dir, 'known_hosts')}",
               # iLO 4 only speaks old algorithms
               "-o", "KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1",
               "-o", "HostKeyAlgorithms=+ssh-rsa,ssh-dss", "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
               "-l", self.username, bare_host(self.host), command]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                               env={"PATH": os.environ.get("PATH", ""), "SSHPASS": self.password})
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DriverError(f"SSH: {e}") from e
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0 or "invalid command" in out.lower() or "unknown command" in out.lower():
            raise DriverError("SSH: " + (out or f"exit {r.returncode}")
                              + (" (is the iLO firmware unlocked?)" if "command" in out.lower() else ""))
        return out

    def read(self):
        data = super().read()
        self.fan_count = len(data["fans"]) or self.fan_count
        return data

    def caps(self, value):
        """Cap every PWM at value (0-255). The iLO's own control still runs underneath the cap."""
        if self.sent == value and time.time() - self.sent_at < self.RESEND:
            return
        for i in range(self.fan_count or 8):
            self.ssh(f"fan p {i} max {value}")
        self.sent, self.sent_at = value, time.time()

    def set_speed(self, pct):
        try:
            self.caps(math.ceil(pct / 100 * 255))
        except DriverError:
            self.set_auto()
            raise

    def set_auto(self):
        self.sent = None
        try:
            self.caps(255)  # no cap: the iLO decides
        except DriverError:
            pass


# ---------------------------------------------------------------- demo

class DemoDriver(Driver):
    kind = "demo"
    label = "Demo server"
    vendor = "Demo"
    description = "Simulated readings, to try the dashboard without hardware."
    control = True
    pcie = True
    needs_host = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.speed, self.model = 40, "PowerEdge (demo)"

    @staticmethod
    def cpu_at(t):
        return 44 + 9 * abs(((t / 1800) % 2) - 1) + random.uniform(-.4, .4)

    @staticmethod
    def inlet_at(t):
        return 22 + abs(((t / 5400) % 2) - 1)

    def read(self):
        t = time.time()
        cpu = self.cpu_at(t)
        rpm = int(1800 + self.speed * 120)
        lines = [f"Inlet Temp | 04h | ok | 7.1 | {self.inlet_at(t):.0f} degrees C",
                 f"Exhaust Temp | 01h | ok | 7.1 | {cpu - 12:.0f} degrees C",
                 f"Temp | 0Eh | ok | 3.1 | {cpu:.0f} degrees C",
                 f"Temp | 0Fh | ok | 3.2 | {cpu - 3:.0f} degrees C",
                 f"Pwr Consumption | 77h | ok | 7.1 | {100 + cpu + random.uniform(-2, 2):.0f} Watts"]
        lines += [f"Fan{i} RPM | 3{i}h | ok | 7.1 | {rpm + random.randint(-60, 60)} RPM" for i in range(1, 7)]
        data = parse_sdr("\n".join(lines))
        data["power"] = "on"
        return data

    def set_auto(self):
        self.speed = 60

    def set_speed(self, pct):
        self.speed = pct

    def set_pcie(self, enabled):
        pass


DRIVERS = {d.kind: d for d in (DellDriver, SupermicroDriver, ILO4UnlockedDriver, RedfishDriver, IPMIDriver, DemoDriver)}
