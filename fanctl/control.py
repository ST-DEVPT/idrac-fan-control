"""Fan control decisions: pure functions, no I/O, so they are easy to test."""

from .config import FAILSAFE_HYSTERESIS

DEFAULT_SETTINGS = {
    "mode": "curve",  # auto | fixed | curve | smart
    "fixed_speed": 20,
    "curve": [[30, 10], [45, 15], [55, 25], [65, 45], [72, 70]],
    "failsafe_temp": 75,       # at or above this CPU temp, hand control back to the BMC
    "ramp_down_seconds": 60,   # fans speed up at once, slow down only after this long
    "pcie_cooling": None,      # None = leave untouched, True/False = enforce
    "min_speed": 10,           # never run the fans slower than this in manual modes
    "exhaust_limit": 60,       # °C of exhaust air at which the BMC takes over; None turns it off
    "bmc_thresholds": True,    # also hand over when any sensor nears the warning threshold the BMC defines
    "threshold_margin": 5,     # how far below that threshold, in °C
    "dry_run": False,          # decide and log, but leave the fans to the BMC
    "smart_target": 60,        # smart mode: CPU temperature to hold, °C
}


def curve_speed(curve, temp):
    """Linear interpolation over [[temp, speed], ...]; flat beyond both ends."""
    pts = sorted(curve)
    if temp <= pts[0][0]:
        return pts[0][1]
    for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
        if temp <= t1:
            return round(s0 + (s1 - s0) * (temp - t0) / (t1 - t0)) if t1 > t0 else s1
    return pts[-1][1]


def hazard(settings, cpu_temp, sensors=None, tripped=False):
    """Why the BMC must take over, or None.

    Checks the hottest CPU against the failsafe, the exhaust air against its limit, and every
    sensor against the warning threshold the BMC itself defines (PCIe cards, disks, DIMMs, RAID
    controller: the parts a CPU-only curve would cook). Once tripped, each limit is lowered by
    FAILSAFE_HYSTERESIS, so manual control resumes only when things have clearly cooled.
    """
    def over(value, limit):
        return value >= limit or (tripped and value > limit - FAILSAFE_HYSTERESIS)

    limit = settings["failsafe_temp"]
    if cpu_temp is not None and over(cpu_temp, limit):
        return (f"CPU {cpu_temp:.0f}°C ≥ failsafe {limit}°C" if cpu_temp >= limit
                else f"CPU {cpu_temp:.0f}°C, resuming below {limit - FAILSAFE_HYSTERESIS}°C")
    sensors = sensors or {}
    exhaust, ex_limit = sensors.get("exhaust"), settings.get("exhaust_limit")
    if ex_limit is not None and exhaust is not None and over(exhaust, ex_limit):
        return f"exhaust air {exhaust:.0f}°C, limit {ex_limit}°C"
    if settings.get("bmc_thresholds", True):
        margin = settings.get("threshold_margin", 5)
        for t in sensors.get("temps", []):
            warn = t.get("warn")
            if warn and over(t["value"], warn - margin):
                return f"{t['name']} {t['value']:.0f}°C, near its {warn:.0f}°C warning threshold"
    return None


def decide(settings, cpu_temp, was_failsafe=False, sensors=None):
    """Return (effective, target_speed, reason, failsafe). See hazard() for what trips the failsafe."""
    if settings["mode"] == "auto":
        return "auto", None, "automatic mode selected", False
    if cpu_temp is None:
        return "auto", None, "no CPU temperature reading", False
    why = hazard(settings, cpu_temp, sensors, was_failsafe)
    if why:
        return "auto", None, why, True
    floor = settings.get("min_speed", 0)
    if settings["mode"] == "smart":
        return "manual", None, "smart", False  # the speed comes from smart_step(), which keeps state
    if settings["mode"] == "fixed":
        return "manual", max(settings["fixed_speed"], floor), "fixed speed", False
    return "manual", max(curve_speed(settings["curve"], cpu_temp), floor), f"curve at {cpu_temp:.0f}°C", False


def ramped(window, now, target, hold):
    """Fans speed up at once but slow down only after `hold` seconds of lower demand:
    the speed applied is the highest target seen in the last `hold` seconds."""
    window.append((now, target))
    while window[0][0] < now - hold:
        window.popleft()
    return max(s for _, s in window)


def validate_settings(new, current):
    s = {**current, **{k: v for k, v in new.items() if k in DEFAULT_SETTINGS}}
    if s["mode"] == "dell":
        s["mode"] = "auto"
    if s["mode"] not in ("auto", "fixed", "curve", "smart"):
        raise ValueError("mode must be auto, fixed, curve or smart")
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
    if not (type(s["min_speed"]) is int and 0 <= s["min_speed"] <= 60):
        raise ValueError("minimum speed must be a whole number from 0 to 60")
    if s["exhaust_limit"] is not None and not (type(s["exhaust_limit"]) in (int, float) and 30 <= s["exhaust_limit"] <= 90):
        raise ValueError("exhaust limit must be between 30 and 90 °C, or empty to turn it off")
    if not (type(s["threshold_margin"]) in (int, float) and 0 <= s["threshold_margin"] <= 20):
        raise ValueError("threshold margin must be between 0 and 20 °C")
    if not (type(s["smart_target"]) in (int, float) and 40 <= s["smart_target"] <= 85):
        raise ValueError("smart target must be between 40 and 85 °C")
    if s["mode"] == "smart" and s["smart_target"] >= s["failsafe_temp"] - 3:
        raise ValueError("the smart target must be at least 3 °C below the CPU failsafe")
    for key in ("bmc_thresholds", "dry_run"):
        if type(s[key]) is not bool:
            raise ValueError(f"{key} must be true or false")
    return s


def speed_text(effective, speed):
    return {"auto": "automatic", "monitor": "set by the BMC"}.get(effective) or ("—" if speed is None else f"{speed}%")


# ---------------------------------------------------------------- smart mode

SMART_KP = 2.0        # % of fan speed per °C above target, applied at once
SMART_KI = 0.04       # % per °C per second, accumulated: removes the steady-state error
SMART_UP = 15         # most the speed may rise in one step, in %
SMART_DOWN = 0.2      # most it may fall per second, in % (a slow fall is a quiet fall)
SMART_MIN_CHANGE = 2  # % below which a correction is not worth a change of fan noise


def smart_errors(settings, cpu, sensors):
    """How far each watched temperature is above where smart mode wants it, in °C.
    The CPU aims at smart_target; the exhaust air and every sensor with a BMC warning
    threshold aim at 8 °C below the point where the failsafe would trip."""
    out = []
    if cpu is not None:
        out.append(("CPU", cpu - settings["smart_target"]))
    sensors = sensors or {}
    if settings.get("exhaust_limit") is not None and sensors.get("exhaust") is not None:
        out.append(("exhaust", sensors["exhaust"] - (settings["exhaust_limit"] - 8)))
    if settings.get("bmc_thresholds", True):
        margin = settings.get("threshold_margin", 5)
        for t in sensors.get("temps", []):
            if t.get("warn") and not t["cpu"]:
                out.append((t["name"], t["value"] - (t["warn"] - margin - 8)))
    return out


def smart_step(memory, settings, cpu, sensors, dt):
    """One step of a PI controller on the worst error. memory holds the integral and the last
    output between calls; pass {} to start. Returns (speed, reason)."""
    errors = smart_errors(settings, cpu, sensors)
    if not errors:
        return None, "no temperature to aim at"
    name, err = max(errors, key=lambda e: e[1])
    if "e" in memory:  # light smoothing: BMC readings jitter by a degree
        err = 0.5 * memory["e"] + 0.5 * err
    memory["e"] = err
    floor = settings.get("min_speed", 0)
    last = memory.get("last", max(30, floor))
    integral = memory.get("i", last - SMART_KP * err)  # start where we are, without a jump
    # clamping anti-windup: the integral never pushes the output past the floor or 100 %
    integral = min(100 - SMART_KP * err, max(floor - SMART_KP * err, integral + SMART_KI * err * dt))
    raw = SMART_KP * err + integral
    if abs(raw - last) < SMART_MIN_CHANGE and floor < raw < 100:
        raw = last  # don't chase sensor noise one percent at a time
    speed = min(raw, last + SMART_UP) if raw > last else max(raw, last - SMART_DOWN * dt)
    speed = round(min(100, max(floor, speed)))
    memory.update(i=integral, last=speed)
    where = f"{name} {err:+.0f} °C from target" if name != "CPU" else f"CPU {cpu:.0f}°C, target {settings['smart_target']}°C"
    return speed, f"smart: {where}"
