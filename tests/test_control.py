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


class Smart(unittest.TestCase):
    """Smart mode in a simple thermal model: heat from load, cooling that grows with fan speed."""
    S = {**DEFAULT_SETTINGS, "mode": "smart", "smart_target": 60, "min_speed": 10, "exhaust_limit": None}

    def run_model(self, load, steps=600, dt=15, start=40.0):
        from fanctl.control import smart_step
        temp, memory, speeds = start, {}, []
        for _ in range(steps):
            speed, _ = smart_step(memory, self.S, temp, {"temps": []}, dt)
            for _ in range(dt):
                temp += 0.01 * (load - (temp - 25) * (0.3 + speed / 25))
            speeds.append(speed)
        return temp, speeds

    def test_holds_the_target_under_load(self):
        temp, speeds = self.run_model(load=60)
        self.assertAlmostEqual(temp, 60, delta=1.5)
        self.assertTrue(30 <= speeds[-1] <= 42)

    def test_idles_at_the_floor_without_hunting(self):
        temp, speeds = self.run_model(load=20)
        self.assertLess(temp, 60)
        self.assertEqual(set(speeds[-100:]), {10})

    def test_follows_other_sensors(self):
        from fanctl.control import smart_errors
        hot_pcie = {"temps": [{"name": "PCIe 1", "value": 64, "cpu": False, "warn": 75}]}
        name, err = max(smart_errors(self.S, 50, hot_pcie), key=lambda e: e[1])
        self.assertEqual((name, err), ("PCIe 1", 2))  # aims at 75 - 5 margin - 8

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
