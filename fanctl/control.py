"""Fan control decisions: pure functions, no I/O, so they are easy to test."""

import math
import re

from .config import FAILSAFE_HYSTERESIS

DEFAULT_SETTINGS = {
    "mode": "curve",  # auto | fixed | curve | smart
    "fixed_speed": 20,
    "curve": [[30, 10], [45, 15], [55, 25], [65, 45], [72, 70]],
    "failsafe_temp": 75,       # at or above this CPU temp, hand control back to the BMC
    "ramp_down_seconds": 60,   # fans speed up at once, slow down only after this long
    "pcie_cooling": None,      # None = leave untouched, True/False = enforce
    "min_speed": 20,           # never run the fans slower than this in manual modes: cards without a
                               # sensor (HBAs, NICs, NVMe, GPUs) are cooled by this airflow alone
    "exhaust_limit": 60,       # °C of exhaust air at which the BMC takes over; None turns it off
    "bmc_thresholds": True,    # also hand over when any sensor nears the warning threshold the BMC defines
    "threshold_margin": 5,     # how far below that threshold, in °C
    "dry_run": False,          # decide and log, but leave the fans to the BMC
    "smart_target": 60,        # smart mode: CPU temperature to hold, °C
    # quiet hours: cap the manual fan speed between start and end (local time). The failsafe still
    # hands control to the BMC whatever the hour.
    "quiet": {"enabled": False, "start": "23:00", "end": "07:00", "max_speed": 25},
    # profiles by day and time: each may cap the speed and/or change the smart mode target, e.g.
    # {"name": "Weekend", "days": [5, 6], "start": "00:00", "end": "00:00", "max_speed": 30, "smart_target": 65}
    # days: 0 = Monday. start == end means the whole day. Overlapping profiles: the lowest cap and target win.
    "schedule": [],
}


def curve_speed(curve, temp):
    """Linear interpolation over [[temp, speed], ...]; flat beyond both ends."""
    pts = sorted(curve)
    if temp <= pts[0][0]:
        return pts[0][1]
    for (t0, s0), (t1, s1) in zip(pts, pts[1:], strict=False):
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

    for fault in (sensors or {}).get("faults", []):  # missing or faulty readings, failed fans
        return fault

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


def failed_fans(fans):
    """Fans that stopped while the others spin, or that the BMC itself reports as failed."""
    rpms = [f["rpm"] for f in fans if f["rpm"]]
    pcts = [f["pct"] for f in fans if f["pct"]]
    return [f["name"] for f in fans if f.get("ok", True) is False
            or (f["rpm"] is not None and f["rpm"] < 300 and rpms and max(rpms) > 1500)
            or (f["pct"] is not None and f["pct"] == 0 and pcts and max(pcts) >= 10)]


TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def in_window(start, end, now):
    """Whether `now` (a time.struct_time) falls between two HH:MM times. A window may run past
    midnight; start == end means the whole day, for quiet hours and schedule profiles alike."""
    minute, a, b = now.tm_hour * 60 + now.tm_min, _minutes(start), _minutes(end)
    return a == b or (a <= minute < b if a < b else minute >= a or minute < b)


def quiet_cap(quiet, now):
    """The speed cap in force at `now` (a time.struct_time), or None outside quiet hours."""
    return quiet["max_speed"] if quiet.get("enabled") and in_window(quiet["start"], quiet["end"], now) else None


def _minutes(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def active_profiles(schedule, now):
    """The profiles in force at `now` (a time.struct_time). A profile that runs past midnight
    belongs to the day it starts on."""
    minute, day = now.tm_hour * 60 + now.tm_min, now.tm_wday
    out = []
    for p in schedule:
        start, end = _minutes(p["start"]), _minutes(p["end"])
        if start < end or start == end:
            on = day in p["days"] and in_window(p["start"], p["end"], now)
        else:  # past midnight: the morning part belongs to the day before
            on = (day in p["days"] and minute >= start) or ((day - 1) % 7 in p["days"] and minute < end)
        if on:
            out.append(p)
    return out


def apply_schedule(settings, now):
    """Settings with the profiles in force applied, and the speed cap they set (or None) with the
    name of the profile that set it."""
    active = active_profiles(settings.get("schedule", []), now)
    caps = [(p["max_speed"], p["name"]) for p in active if p.get("max_speed") is not None]
    targets = [p["smart_target"] for p in active if p.get("smart_target") is not None]
    if targets:
        settings = {**settings, "smart_target": min(targets)}
    return settings, min(caps) if caps else (None, None)


def aggregate(points):
    """One long-history point from a bucket of readings: averages, the hottest CPU, and the
    fan speed set by this controller only if it was in control for most of the bucket."""
    def avg(key):
        vals = [p[key] for p in points if p.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    manual = [p["speed"] for p in points if p.get("speed") is not None]
    cpus = [p["cpu"] for p in points if p.get("cpu") is not None]
    return {"t": points[-1]["t"], "cpu": avg("cpu"), "cpu_max": max(cpus) if cpus else None,
            "speed": round(sum(manual) / len(manual)) if len(manual) * 2 > len(points) else None,
            "inlet": avg("inlet"), "exhaust": avg("exhaust"), "rpm": avg("rpm"), "fanpct": avg("fanpct"),
            "watts": avg("watts")}


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
    if not (type(s["failsafe_temp"]) in (int, float) and 40 <= s["failsafe_temp"] <= 90):
        raise ValueError("failsafe temperature must be between 40 and 90 °C")
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
    if not (type(s["min_speed"]) is int and 10 <= s["min_speed"] <= 60):
        raise ValueError("minimum speed must be a whole number from 10 to 60")
    if s["exhaust_limit"] is not None and not (type(s["exhaust_limit"]) in (int, float) and 30 <= s["exhaust_limit"] <= 90):
        raise ValueError("exhaust limit must be between 30 and 90 °C, or empty to turn it off")
    if not (type(s["threshold_margin"]) in (int, float) and 0 <= s["threshold_margin"] <= 20):
        raise ValueError("threshold margin must be between 0 and 20 °C")
    q = s["quiet"]
    if not (isinstance(q, dict) and set(q) == {"enabled", "start", "end", "max_speed"}
            and type(q["enabled"]) is bool and type(q["max_speed"]) is int and 0 <= q["max_speed"] <= 100
            and all(isinstance(q[k], str) and TIME_RE.fullmatch(q[k]) for k in ("start", "end"))):
        raise ValueError("quiet hours need enabled, start and end as HH:MM, and a max_speed from 0 to 100")
    sched = s["schedule"]
    if not (isinstance(sched, list) and len(sched) <= 8):
        raise ValueError("the schedule holds up to 8 profiles")
    for p in sched:
        if not (isinstance(p, dict) and set(p) == {"name", "days", "start", "end", "max_speed", "smart_target"}):
            raise ValueError("each profile needs name, days, start, end, max_speed and smart_target")
        if not (isinstance(p["name"], str) and 1 <= len(p["name"].strip()) <= 30):
            raise ValueError("give each profile a name of 1 to 30 characters")
        if not (isinstance(p["days"], list) and p["days"] and all(type(d) is int and 0 <= d <= 6 for d in p["days"])
                and len(set(p["days"])) == len(p["days"])):
            raise ValueError(f"{p['name']}: pick at least one day")
        if not all(isinstance(p[k], str) and TIME_RE.fullmatch(p[k]) for k in ("start", "end")):
            raise ValueError(f"{p['name']}: start and end must be HH:MM")
        if p["max_speed"] is not None and not (type(p["max_speed"]) is int and 0 <= p["max_speed"] <= 100):
            raise ValueError(f"{p['name']}: the maximum speed must be a whole number from 0 to 100")
        if p["smart_target"] is not None and not (type(p["smart_target"]) in (int, float)
                                                  and 40 <= p["smart_target"] <= s["failsafe_temp"] - 3):
            raise ValueError(f"{p['name']}: the smart target must be 40 °C or more, and 3 °C below the failsafe")
        if p["max_speed"] is None and p["smart_target"] is None:
            raise ValueError(f"{p['name']}: set a maximum speed, a smart target, or both")
        p["name"], p["days"] = p["name"].strip(), sorted(p["days"])
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
#
# Smart mode holds every temperature it watches at a target with as little fan as it can. Three
# parts, each covering what the others can't:
#   - a map, learned while it runs, from heat load to the speed that held the target at that load.
#     When the load changes, the fans go straight to what worked before instead of waiting for the
#     heat: a CPU die warms within seconds of a load, its heatsink over minutes.
#   - a look-ahead: each temperature is judged where its trend puts it SMART_HORIZON seconds on.
#   - a PI trim on top, which corrects what the map gets wrong, and which the map slowly absorbs.
# And a boost when anything heads for its trip point, so the BMC rarely has to take over.

SMART_HORIZON = 40          # s of look-ahead
SMART_WINDOW = 60           # s of readings a trend is fitted over
SMART_KP = 1.5              # % per °C of predicted error, applied at once
SMART_KI = 0.03             # % per °C per second of actual error, accumulated
SMART_DEADBAND = 0.5        # °C either side of the target where the trim stops accumulating
SMART_UP = 20               # most the speed rises in one step, in %
SMART_DOWN = 0.15           # most it falls per second near the target, in % (a slow fall is a quiet fall)
SMART_DOWN_FAR = 0.4        # ...and when everything is well below target
SMART_MIN_CHANGE = 2        # % below which a correction is not worth a change of fan noise
SMART_BOOST = 25            # % added at once when something heads for its trip point
SMART_BOOST_MARGIN = 4      # °C: "heading for" = predicted within this of the trip point, and already close
SMART_YIELD = 8             # °C below a trip point where quiet hours start to give way...
SMART_YIELD_RATE = 10       # ...by this many % per °C closer
SMART_LOAD_TAU = 60         # s: power is averaged over about this long; a burst shorter than the
                            # heatsink can feel should not move the fans
SMART_FAN_WATTS = 80        # what the fans of a 1U/2U server draw at 100 %, roughly; their power grows with the
                            # cube of speed and is taken out of the load, or more fan would read as more heat
SMART_EXTRAPOLATE = 15      # % the map may be carried past its last learned point; the trim does the rest
SMART_LEARN_RATE = 1 / 120  # per second of steady running: a few minutes to trust a new reading
SMART_BIN = 1.05            # map resolution: one point per 5 % of heat load


def smart_watch(settings, cpu, sensors):
    """What smart mode looks after: (name, value, target, trip point). The CPU aims at smart_target
    and trips at the failsafe; the exhaust air and every sensor with a BMC warning threshold aim at
    8 °C below the point where they would trip. Inlet air is left out: no fan speed cools the room."""
    out = []
    if cpu is not None:
        out.append(("CPU", cpu, settings["smart_target"], settings["failsafe_temp"]))
    sensors = sensors or {}
    limit = settings.get("exhaust_limit")
    if limit is not None and sensors.get("exhaust") is not None:
        out.append(("exhaust", sensors["exhaust"], limit - 8, limit))
    if settings.get("bmc_thresholds", True):
        margin = settings.get("threshold_margin", 5)
        for t in sensors.get("temps", []):
            if t.get("warn") and not t["cpu"] and not re.search(r"inlet|ambient|intake", t["name"], re.I):
                trip = t["warn"] - margin
                out.append((t["name"], t["value"], trip - 8, trip))
    return out


def trend(points):
    """Least-squares line through [(t, value), ...]: (value now, °C per second). The fit smooths the
    whole-degree steps BMCs report in; with under half a window of readings there is no trend yet."""
    t_now, v_now = points[-1]
    if t_now - points[0][0] < SMART_WINDOW / 2:
        return v_now, 0.0
    n = len(points)
    mt = sum(t for t, _ in points) / n
    mv = sum(v for _, v in points) / n
    slope = sum((t - mt) * (v - mv) for t, v in points) / sum((t - mt) ** 2 for t, _ in points)
    return mv + slope * (t_now - mt), slope


def heat_load(settings, watts, inlet):
    """How much cooling the server needs, as one number: its power draw over the room the target
    leaves above the inlet air, in W/°C. The same work needs more air on a warm day and less with a
    warmer target, so one learned map holds across seasons and targets."""
    if not watts:
        return None
    return watts / max(5, settings["smart_target"] - (25 if inlet is None else inlet))


def map_speed(learned, load):
    """The speed the learned map gives for a heat load, or None: interpolated between learned
    points, never lower for a higher load, and carried on past the last point along their slope."""
    if load is None or not learned:
        return None
    pts, top = [], 0
    for b, v in sorted((int(k), v) for k, v in learned.items()):
        top = max(top, v)
        pts.append((SMART_BIN ** b, top))
    if load <= pts[0][0]:
        return pts[0][1]
    for (x0, v0), (x1, v1) in zip(pts, pts[1:], strict=False):
        if load <= x1:
            return v0 + (v1 - v0) * (load - x0) / (x1 - x0)
    (x0, v0), (x1, v1) = pts[0], pts[-1]
    return min(100, v1 + min(SMART_EXTRAPOLATE, (v1 - v0) / (x1 - x0) * (load - x1) if x1 > x0 else 0))


def learned_curve(learned, settings, inlet):
    """The map as [[watts, speed], ...] at today's inlet temperature, for the dashboard."""
    room = max(5, settings["smart_target"] - (25 if inlet is None else inlet))
    out, top = [], 0
    for b, v in sorted((int(k), v) for k, v in learned.items()):
        top = max(top, v)
        out.append([round(SMART_BIN ** b * room), round(top, 1)])
    return out


def validate_learned(data):
    """A learned map read back from disk: {bin: speed}. Anything odd is dropped, never trusted."""
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in list(data.items())[:500]:
        try:
            b = int(k)
        except (TypeError, ValueError):
            continue
        if -100 <= b <= 200 and type(v) in (int, float) and 0 <= v <= 100:
            out[str(b)] = float(v)
    return out


def steady_speed(trail, worst, floor, now):
    """The speed that has held things steady for the last SMART_WINDOW, or None. Steady means the
    speed and the load barely moved and the worst temperature sits at its target with no trend; or
    the fans sat on the floor with everything well below target, so the floor is all this load needs."""
    recent = [p for p in trail if p[0] >= now - SMART_WINDOW]
    if not recent or now - recent[0][0] < SMART_WINDOW * 0.75 or any(p[3] for p in recent):
        return None
    speeds, loads = [p[1] for p in recent], [p[2] for p in recent]
    if None in loads or max(loads) > min(loads) * 1.08 or max(speeds) - min(speeds) > 3:
        return None
    err = worst["fit"] - worst["target"]
    if abs(err) <= 1.5 and abs(worst["slope"]) <= 0.02:
        return sum(speeds) / len(speeds)
    if max(speeds) <= floor + 0.5 and err <= -2 and worst["slope"] <= 0.01:
        return float(floor)
    return None


def smart_step(memory, learned, settings, cpu, sensors, now, ceiling=None):
    """One step of smart mode. memory carries trends and the trim between calls (pass {} to start
    afresh); learned is the load map, updated in place while things are steady. ceiling is the
    quiet-hours cap, which only a boost may pass. Returns (speed, info): info says what it is
    doing, for the reason line and the dashboard."""
    sensors = sensors or {}
    watch = smart_watch(settings, cpu, sensors)
    if not watch:
        return None, {"reason": "no temperature to aim at"}
    floor = settings.get("min_speed", 0)
    ceiling = 100 if ceiling is None else max(floor, min(100, ceiling))
    dt = min(120, max(0, now - memory.get("now", now)))
    memory["now"] = now

    # where each temperature is and where it is heading; a fall is trusted half as much as a rise
    hist, items = memory.setdefault("temps", {}), []
    for name, value, target, trip in watch:
        pts = hist[name] = [p for p in hist.get(name, []) if p[0] > now - SMART_WINDOW] + [(now, value)]
        fit, slope = trend(pts)
        ahead = max(-10, min(8, slope * SMART_HORIZON * (1 if slope > 0 else 0.5)))
        pred = fit + ahead
        items.append({"name": name, "value": value, "fit": fit, "slope": slope, "pred": pred,
                      "target": target, "trip": trip})
    for name in set(hist) - {x["name"] for x in items}:
        del hist[name]
    gov = max(items, key=lambda x: x["pred"] - x["target"])     # what the speed answers to
    worst = max(items, key=lambda x: x["fit"] - x["target"])    # what the trim answers to
    e_pred, e_now = gov["pred"] - gov["target"], worst["fit"] - worst["target"]

    # heat load from the power draw averaged over SMART_LOAD_TAU: the heatsink only feels sustained power.
    # The fans' own draw comes out first, so speeding them up never reads as a bigger load.
    watts = sensors.get("watts")
    if watts and memory.get("last") is not None:
        watts = max(watts * 0.5, watts - SMART_FAN_WATTS * (memory["last"] / 100) ** 3)
    if watts and memory.get("watts"):
        memory["watts"] += (watts - memory["watts"]) * min(1, dt / SMART_LOAD_TAU)
    else:
        memory["watts"] = watts or None
    load = heat_load(settings, memory["watts"], sensors.get("inlet"))
    base = map_speed(learned, load)

    last = memory.get("last")
    if last is None:
        memory["i"] = 0.0 if base is not None else float(max(30, floor))
    elif (base is None) != (memory.get("base") is None):  # the map came or went: no jump
        memory["i"] += (memory.get("base") or 0) - (base or 0)

    # learn from the last minute if it was steady; the map takes over from the trim without a jump
    learning = False
    if load is not None and last is not None and not settings.get("dry_run"):
        seen = steady_speed(memory.get("trail", []), worst, floor, now)
        if seen is not None:
            k = str(round(math.log(load) / math.log(SMART_BIN)))
            old = learned.get(k)
            learned[k] = round(seen if old is None else old + min(1, dt * SMART_LEARN_RATE) * (seen - old), 2)
            new = map_speed(learned, load)
            memory["i"] -= new - (base or 0)
            base, learning = new, True

    # quiet hours give way gradually as anything nears its trip point: louder fans beat a failsafe
    closest = max(x["fit"] - (x["trip"] - SMART_YIELD) for x in items)
    ceiling = min(100, ceiling + SMART_YIELD_RATE * max(0, closest)) if ceiling < 100 else 100

    b, p = base or 0, SMART_KP * e_pred
    raw = b + p + memory["i"]
    # accumulate the actual error beyond the deadband, never further into a limit (anti-windup)
    if abs(e_now) > SMART_DEADBAND and not (raw >= ceiling and e_now > 0) and not (raw <= floor and e_now < 0):
        excess = e_now - math.copysign(SMART_DEADBAND, e_now)
        memory["i"] = max(-100, min(100, memory["i"] + SMART_KI * excess * dt))
        raw = b + p + memory["i"]

    near = [x for x in items if x["pred"] >= x["trip"] - SMART_BOOST_MARGIN
            and x["value"] >= x["trip"] - 2 * SMART_BOOST_MARGIN]
    if last is None:
        speed = raw
    elif near:
        speed = max(raw, last + SMART_BOOST)
        memory["i"] = speed - b - p  # stay up there until the temperature turns
    elif abs(raw - last) < SMART_MIN_CHANGE and floor < raw < ceiling:
        speed = last  # don't chase sensor noise one percent at a time
    elif raw > last:
        speed = min(raw, last + SMART_UP)
    elif now - memory.get("wanted", now) < settings.get("ramp_down_seconds", 60):
        speed = last  # lower demand must last the ramp-down delay: a burst every minute holds the fans steady
    else:
        speed = max(raw, last - (SMART_DOWN_FAR if e_now < -4 else SMART_DOWN) * dt)
    speed = min(100 if near else ceiling, max(floor, speed))
    if last is None or raw >= speed - SMART_MIN_CHANGE:
        memory["wanted"] = now  # last time the demand reached the speed
    memory.update(last=speed, base=base)
    trail = memory["trail"] = [x for x in memory.get("trail", []) if x[0] > now - 2 * SMART_WINDOW]
    trail.append((now, speed, load, bool(near)))

    if near:
        x = max(near, key=lambda x: x["pred"] - x["trip"])
        reason = f"smart: boost, {x['name']} {x['value']:.0f}°C heading for {x['trip']:.0f}°C"
    else:
        reason = f"smart: {gov['name']} {gov['value']:.0f}°C, target {gov['target']:.0f}°C"
        if gov["pred"] >= gov["value"] + 1:
            reason += f", rising to {gov['pred']:.0f}°C"
        if raw > ceiling and speed >= ceiling:
            reason += f", quiet hours cap {ceiling}%"
    return round(speed), {
        "reason": reason, "sensor": gov["name"], "value": gov["value"], "target": gov["target"],
        "predicted": round(gov["pred"], 1), "trend": round(gov["slope"] * 60, 2),  # °C per minute
        "learned": None if base is None else round(base), "trim": round(p + memory["i"], 1),
        "boost": bool(near), "learning": learning, "points": len(learned)}
