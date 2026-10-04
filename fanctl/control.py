"""Fan control decisions: pure functions, no I/O, so they are easy to test."""

from .config import FAILSAFE_HYSTERESIS

DEFAULT_SETTINGS = {
    "mode": "curve",  # auto | fixed | curve
    "fixed_speed": 20,
    "curve": [[30, 10], [45, 15], [55, 25], [65, 45], [72, 70]],
    "failsafe_temp": 75,       # at or above this CPU temp, hand control back to the BMC
    "ramp_down_seconds": 60,   # fans speed up at once, slow down only after this long
    "pcie_cooling": None,      # None = leave untouched, True/False = enforce
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


def decide(settings, cpu_temp, was_failsafe=False):
    """Return (effective, target_speed, reason, failsafe).

    Once the failsafe trips, manual control resumes only when the CPU is
    FAILSAFE_HYSTERESIS degrees below the limit, so the fans don't flap at the edge.
    """
    if settings["mode"] == "auto":
        return "auto", None, "automatic mode selected", False
    if cpu_temp is None:
        return "auto", None, "no CPU temperature reading", False
    limit = settings["failsafe_temp"]
    if cpu_temp >= limit:
        return "auto", None, f"CPU {cpu_temp:.0f}°C ≥ failsafe {limit}°C", True
    if was_failsafe and cpu_temp > limit - FAILSAFE_HYSTERESIS:
        return "auto", None, f"CPU {cpu_temp:.0f}°C, resuming below {limit - FAILSAFE_HYSTERESIS}°C", True
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
    if s["mode"] == "dell":
        s["mode"] = "auto"
    if s["mode"] not in ("auto", "fixed", "curve"):
        raise ValueError("mode must be auto, fixed or curve")
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


def speed_text(effective, speed):
    return {"auto": "automatic", "monitor": "set by the BMC"}.get(effective) or ("—" if speed is None else f"{speed}%")
