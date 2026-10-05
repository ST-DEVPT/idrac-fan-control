import unittest
from collections import deque

from fanctl.control import DEFAULT_SETTINGS, curve_speed, decide, ramped, validate_settings

CURVE = [[30, 10], [50, 30], [70, 70]]
BASE = dict(DEFAULT_SETTINGS, curve=CURVE, failsafe_temp=75, fixed_speed=25)


class Curve(unittest.TestCase):
    def test_interpolation(self):
        self.assertEqual([curve_speed(CURVE, t) for t in (20, 40, 60, 90)], [10, 20, 50, 70])


class Decide(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(decide({**BASE, "mode": "curve"}, 60)[:2], ("manual", 50))
        self.assertEqual(decide({**BASE, "mode": "fixed"}, 60)[:2], ("manual", 25))
        self.assertEqual(decide({**BASE, "mode": "auto"}, 99)[::3], ("auto", False))

    def test_failsafe_and_hysteresis(self):
        self.assertEqual(decide({**BASE, "mode": "fixed"}, 75)[::3], ("auto", True))
        self.assertEqual(decide({**BASE, "mode": "fixed"}, 73, True)[::3], ("auto", True))
        self.assertEqual(decide({**BASE, "mode": "fixed"}, 72, True)[::3], ("manual", False))

    def test_no_reading_means_automatic(self):
        self.assertEqual(decide({**BASE, "mode": "curve"}, None)[:2], ("auto", None))


class RampDown(unittest.TestCase):
    def test_holds_then_releases(self):
        w = deque()
        self.assertEqual(ramped(w, 0, 40, 60), 40)
        self.assertEqual(ramped(w, 10, 20, 60), 40)  # lower demand is held...
        self.assertEqual(ramped(w, 59, 25, 60), 40)
        self.assertEqual(ramped(w, 61, 25, 60), 25)  # ...until the delay has passed
        self.assertEqual(ramped(w, 62, 50, 60), 50)  # higher demand applies at once
        self.assertEqual(ramped(deque(), 0, 30, 0), 30)

    def test_never_slower_than_asked(self):
        w = deque()
        for step in range(500):
            want = (step * 37) % 101
            self.assertGreaterEqual(ramped(w, step * 5, want, 60), want)


class Validation(unittest.TestCase):
    def test_curve_is_sorted(self):
        self.assertEqual(validate_settings({"curve": [[60, 40], [30, 10]]}, dict(DEFAULT_SETTINGS))["curve"],
                         [[30, 10], [60, 40]])

    def test_rejects(self):
        for bad in ({"mode": "turbo"}, {"fixed_speed": 101}, {"fixed_speed": "20"}, {"fixed_speed": True},
                    {"failsafe_temp": 30}, {"curve": [[30, 10]]}, {"curve": [[30, 150], [40, 20]]},
                    {"ramp_down_seconds": -1}, {"ramp_down_seconds": 601}, {"ramp_down_seconds": 1.5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_settings(bad, dict(DEFAULT_SETTINGS))


class Protection(unittest.TestCase):
    """The failsafe looks at every sensor, not only the CPU."""
    SENSORS = {"exhaust": 40, "temps": [{"name": "CPU 1", "value": 50, "cpu": True, "warn": None},
                                        {"name": "PCIe 1", "value": 60, "cpu": False, "warn": 70}]}

    def test_exhaust_limit(self):
        s = {**BASE, "exhaust_limit": 45}
        hot = {**self.SENSORS, "exhaust": 46}
        self.assertEqual(decide(s, 50, False, hot)[::3], ("auto", True))
        self.assertIn("exhaust", decide(s, 50, False, hot)[2])
        self.assertEqual(decide({**s, "exhaust_limit": None}, 50, False, hot)[0], "manual")

    def test_bmc_warning_thresholds(self):
        near = {**self.SENSORS, "temps": [dict(self.SENSORS["temps"][1], value=66)]}  # 70 warn - 5 margin
        self.assertEqual(decide(BASE, 50, False, near)[::3], ("auto", True))
        self.assertIn("PCIe 1", decide(BASE, 50, False, near)[2])
        self.assertEqual(decide({**BASE, "bmc_thresholds": False}, 50, False, near)[0], "manual")
        self.assertEqual(decide({**BASE, "threshold_margin": 2}, 50, False, near)[0], "manual")
        self.assertEqual(decide(BASE, 50, False, self.SENSORS)[0], "manual")

    def test_hysteresis_for_every_limit(self):
        s = {**BASE, "exhaust_limit": 45}
        self.assertEqual(decide(s, 50, True, {**self.SENSORS, "exhaust": 43})[3], True)   # still within 3 °C
        self.assertEqual(decide(s, 50, True, {**self.SENSORS, "exhaust": 42})[3], False)

    def test_minimum_speed(self):
        self.assertEqual(decide({**BASE, "mode": "fixed", "fixed_speed": 5, "min_speed": 12}, 40)[1], 12)
        self.assertEqual(decide({**BASE, "mode": "curve", "min_speed": 15}, 20)[1], 15)

    def test_new_settings_are_validated(self):
        for bad in ({"min_speed": 70}, {"exhaust_limit": 10}, {"threshold_margin": 50},
                    {"bmc_thresholds": "yes"}, {"dry_run": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_settings(bad, dict(DEFAULT_SETTINGS))
        self.assertIsNone(validate_settings({"exhaust_limit": None}, dict(DEFAULT_SETTINGS))["exhaust_limit"])


class Plant:
    """A CPU on a heatsink: the die follows the heatsink within seconds, the heatsink warms over
    minutes, and more air cools it better. Read the way a BMC reports it: whole degrees, every 5 s,
    with the power draw of the whole server, fans included."""

    def __init__(self, power=20, inlet=22, speed=30):
        self.power, self.inlet, self.speed = power, inlet, speed
        self.hs = inlet + power * 0.12 / (speed / 100)
        self.die = self.hs + power * 0.18

    def run(self, seconds):
        for _ in range(seconds):
            resistance = 0.12 / (max(self.speed, 5) / 100)
            self.hs += (self.power - (self.hs - self.inlet) / resistance) / 500
            self.die += (self.hs + self.power * 0.18 - self.die) / 4

    def sensors(self):
        return {"temps": [], "inlet": self.inlet, "watts": round(70 + 2 * self.power + 60 * (self.speed / 100) ** 3)}


class Smart(unittest.TestCase):
    S = {**DEFAULT_SETTINGS, "mode": "smart", "smart_target": 60, "min_speed": 10, "exhaust_limit": None,
         "failsafe_temp": 75, "ramp_down_seconds": 60}

    def drive(self, power, minutes, settings=None, learned=None, cap=lambda t: None, plant=None):
        """Run smart mode against the plant; power(t) and cap(t) take seconds. Returns
        [(t, reading, speed, info)] for every 5 s step, and the learned map."""
        from fanctl.control import smart_step
        plant = plant or Plant()
        memory, learned, out = {}, {} if learned is None else learned, []
        for t in range(0, minutes * 60, 5):
            plant.power = power(t)
            reading = round(plant.die)
            speed, info = smart_step(memory, learned, settings or self.S, reading, plant.sensors(), t, cap(t))
            plant.speed = speed
            out.append((t, reading, speed, info))
            plant.run(5)
        return out, learned

    @staticmethod
    def between(log, a, b):
        return [x for x in log if a * 60 <= x[0] < b * 60]

    @staticmethod
    def travel(log):
        return sum(abs(p[2] - q[2]) for p, q in zip(log, log[1:]))

    def test_holds_the_target_and_settles(self):
        log, learned = self.drive(lambda t: 95, 30)
        last = self.between(log, 15, 30)
        self.assertLessEqual(max(abs(x[1] - 60) for x in last), 2)
        self.assertLessEqual(self.travel(last), 6)          # settled: hardly a change in 15 minutes
        self.assertTrue(learned)                           # and it remembered the speed

    def test_idles_at_the_floor_without_hunting(self):
        log, _ = self.drive(lambda t: 20, 30)
        self.assertEqual({x[2] for x in self.between(log, 5, 30)}, {10})

    def test_second_time_round_the_map_leads(self):
        steps = lambda t: 95 if 20 * 60 <= t < 40 * 60 or 60 * 60 <= t < 80 * 60 else 20  # noqa: E731
        log, learned = self.drive(steps, 100)
        first, second = self.between(log, 20, 25), self.between(log, 60, 65)
        self.assertLessEqual(first[12][3]["learned"], 15)   # a minute in, the first time: only idle is known
        self.assertGreaterEqual(second[12][3]["learned"], 40)  # the second time: what held the load before
        mean = lambda seg: sum(x[1] for x in seg) / len(seg)  # noqa: E731
        self.assertLess(abs(mean(second) - 60), abs(mean(first) - 60))  # closer to the target, sooner
        self.assertLess(max(x[1] for x in log), 75)

    def test_bursty_load_does_not_make_the_fans_hunt(self):
        bursty = lambda t: 95 if t % 90 < 30 else 20  # noqa: E731
        held, _ = self.drive(bursty, 40)
        chased, _ = self.drive(bursty, 40, {**self.S, "ramp_down_seconds": 0})
        self.assertLess(self.travel(self.between(held, 10, 40)), self.travel(self.between(chased, 10, 40)) / 2)
        self.assertLess(max(x[1] for x in held), 70)

    def test_quiet_hours_give_way_before_the_failsafe(self):
        cap = lambda t: 30 if 20 * 60 <= t < 40 * 60 else None  # noqa: E731
        log, _ = self.drive(lambda t: 95, 50, cap=cap)
        quiet = self.between(log, 20, 40)
        self.assertLess(max(x[1] for x in quiet), 75)       # never as far as the failsafe
        for _, temp, speed, _ in quiet:
            if speed > 30:
                self.assertGreaterEqual(temp, 67 - 1)        # louder than the cap only when close to it
        after = self.between(log, 40, 50)
        self.assertLess(max(x[2] for x in after), 80)        # no burst when quiet hours end

    def test_boost_when_heading_for_the_trip_point(self):
        from fanctl.control import smart_step
        memory = {}
        for t, temp in ((0, 60), (5, 61), (10, 62), (15, 64), (20, 66), (25, 68), (30, 69), (35, 70)):
            speed, info = smart_step(memory, {}, self.S, temp, {"temps": []}, t)
        self.assertTrue(info["boost"])
        self.assertIn("boost, CPU 70°C heading for 75°C", info["reason"])
        self.assertGreaterEqual(speed, 55)

    def test_follows_other_sensors(self):
        from fanctl.control import smart_watch
        hot = {"temps": [{"name": "PCIe 1", "value": 64, "cpu": False, "warn": 75}]}
        self.assertIn(("PCIe 1", 64, 62, 70), smart_watch(self.S, 50, hot))  # 75 warn - 5 margin = trip, -8 = target
        room = {"temps": [{"name": "System Board Inlet Temp", "value": 35, "cpu": False, "warn": 42}]}
        self.assertEqual([w[0] for w in smart_watch(self.S, 50, room)], ["CPU"])  # fans can't cool the room
        from fanctl.control import smart_step
        _, info = smart_step({}, {}, self.S, 50, hot, 0)
        self.assertEqual(info["sensor"], "PCIe 1")

    def test_dry_run_learns_nothing(self):
        _, learned = self.drive(lambda t: 95, 20, {**self.S, "dry_run": True})
        self.assertEqual(learned, {})

    def test_map(self):
        from fanctl.control import SMART_BIN, learned_curve, map_speed, validate_learned
        m = {"20": 20.0, "30": 40.0, "25": 15.0}       # a noisy point below its neighbour
        x20, x30 = SMART_BIN ** 20, SMART_BIN ** 30
        self.assertEqual(map_speed(m, x20 / 2), 20)     # below the first point: the first point
        self.assertEqual(map_speed(m, SMART_BIN ** 25), 20)  # never lower for a higher load
        self.assertAlmostEqual(map_speed(m, (SMART_BIN ** 25 + x30) / 2), 30, delta=1)
        self.assertGreater(map_speed(m, x30 * 1.2), 40)  # carried on past the last point
        self.assertIsNone(map_speed({}, 5))
        self.assertIsNone(map_speed(m, None))
        self.assertEqual(validate_learned({"3": 40, "x": 1, "4": 140, "5": "9", "6": True}), {"3": 40.0})
        self.assertEqual(validate_learned([1, 2]), {})
        self.assertEqual([p[1] for p in learned_curve(m, self.S, 22)], [20, 20, 40])

    def test_validation(self):
        self.assertEqual(validate_settings({"mode": "smart"}, dict(DEFAULT_SETTINGS))["mode"], "smart")
        with self.assertRaises(ValueError):
            validate_settings({"mode": "smart", "smart_target": 74}, dict(DEFAULT_SETTINGS))  # failsafe 75


class QuietHours(unittest.TestCase):
    def at(self, hhmm):
        import time
        return time.struct_time((2026, 1, 1, int(hhmm[:2]), int(hhmm[3:]), 0, 0, 1, -1))

    def test_window_across_midnight(self):
        from fanctl.control import quiet_cap
        q = {"enabled": True, "start": "23:00", "end": "07:00", "max_speed": 25}
        self.assertEqual([quiet_cap(q, self.at(t)) for t in ("22:59", "23:00", "03:00", "06:59", "07:00")],
                         [None, 25, 25, 25, None])
        self.assertIsNone(quiet_cap({**q, "enabled": False}, self.at("03:00")))
        self.assertEqual(quiet_cap({**q, "start": "00:00", "end": "00:00"}, self.at("15:00")), 25)  # the whole day

    def test_window_within_a_day(self):
        from fanctl.control import quiet_cap
        q = {"enabled": True, "start": "09:00", "end": "17:30", "max_speed": 30}
        self.assertEqual([quiet_cap(q, self.at(t)) for t in ("08:59", "12:00", "17:30")], [None, 30, None])

    def test_validation(self):
        good = {"enabled": True, "start": "22:30", "end": "06:00", "max_speed": 20}
        self.assertEqual(validate_settings({"quiet": good}, dict(DEFAULT_SETTINGS))["quiet"], good)
        for bad in ({**good, "start": "24:00"}, {**good, "max_speed": 120}, {**good, "extra": 1}, "on"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_settings({"quiet": bad}, dict(DEFAULT_SETTINGS))
