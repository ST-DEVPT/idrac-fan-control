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
