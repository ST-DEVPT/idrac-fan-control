import json
import unittest

from fanctl.config import DATA_DIR as DATA, write_json
from fanctl.server import SERVERS, Server, env_servers, validate_server


class Environment(unittest.TestCase):
    def test_none(self):
        self.assertEqual(env_servers({}), [])

    def test_single_server_of_1x(self):
        s = env_servers({"IDRAC_HOST": "10.0.0.5", "IDRAC_NAME": "R720"})[0]
        self.assertEqual((s["id"], s["driver"], s["legacy"]), ("r720", "dell", True))

    def test_numbered_and_drivers(self):
        s = env_servers({"IDRAC_2_HOST": "b", "IDRAC_1_HOST": "a", "IDRAC_1_NAME": "Same", "IDRAC_2_NAME": "Same",
                         "IDRAC_2_DRIVER": "redfish"})
        self.assertEqual([x["id"] for x in s], ["same", "same-2"])
        self.assertEqual((s[1]["host"], s[1]["driver"]), ("b", "redfish"))
        self.assertEqual(env_servers({"IDRAC_1_HOST": "x", "IDRAC_1_DRIVER": "bogus"}), [])

    def test_server_prefix(self):
        s = env_servers({"SERVER_1_HOST": "10.0.0.7", "SERVER_1_DRIVER": "supermicro", "SERVER_1_NAME": "X11"})
        self.assertEqual((s[0]["id"], s[0]["driver"], s[0]["legacy"]), ("x11", "supermicro", False))

    def test_loaded(self):
        self.assertEqual(list(SERVERS)[:2], ["rack-a", "rack-b"])
        self.assertEqual(SERVERS["rack-a"].driver.kind, "demo")


class Validation(unittest.TestCase):
    def test_accepts(self):
        ok = validate_server({"name": "DL360", "driver": "redfish", "host": "10.0.0.9",
                              "username": "Administrator", "password": "pw"})
        self.assertEqual((ok["password"], ok["verify_tls"]), ("pw", False))
        self.assertEqual(validate_server({"name": "Renamed"}, ok)["password"], "pw")  # edit keeps the password
        self.assertEqual(validate_server({"name": "Demo", "driver": "demo"})["host"], "")
        for driver, host in (("redfish", "fe80::1"), ("dell", "fe80::1"), ("redfish", "[fe80::1]:443"),
                             ("redfish", "10.0.0.5:8443"), ("dell", "local")):
            validate_server({"name": "n", "driver": driver, "host": host, "username": "u", "password": "p"})

    def test_rejects(self):
        for bad in ({"name": "", "driver": "dell", "host": "h", "username": "u", "password": "p"},
                    {"name": "x", "driver": "nope", "host": "h", "username": "u", "password": "p"},
                    {"name": "x", "driver": "dell", "host": "h:623", "username": "u", "password": "p"},
                    {"name": "x", "driver": "redfish", "host": "https://h/x", "username": "u", "password": "p"},
                    {"name": "x", "driver": "redfish", "host": "local", "username": "u", "password": "p"},
                    {"name": "x", "driver": "dell", "host": "-oProxyCommand=x", "username": "u", "password": "p"},
                    {"name": "x", "driver": "dell", "host": "h", "username": "u"},
                    {"name": "x", "driver": "dell", "host": "h", "username": "", "password": "p"},
                    {"name": "x", "driver": "redfish", "host": "h", "username": "u", "password": "p", "verify_tls": "yes"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_server(bad)


class Persistence(unittest.TestCase):
    def test_1x_settings_are_converted(self):
        srv = SERVERS["rack-a"]
        write_json(srv.settings_file, {"mode": "dell"})
        self.assertEqual(srv.settings()["mode"], "auto")
        srv.settings_file.unlink()

    def test_history_round_trip(self):
        a = SERVERS["rack-a"]
        a.cycle()
        a.save_history()
        copy = Server({"id": "rack-a", "name": "Rack A", "driver": "demo"})
        self.assertTrue(copy.load_history())
        self.assertEqual(copy.history[-1]["t"], a.history[-1]["t"])


class SmartMemory(unittest.TestCase):
    def test_learned_map_is_kept_and_can_be_forgotten(self):
        srv = Server({"id": "learner", "name": "Learner", "driver": "demo"})
        srv.learned.update({"20": 25.0, "24": 40.0})
        srv.save_history()
        copy = Server({"id": "learner", "name": "Learner", "driver": "demo"})
        self.assertEqual(copy.learned, {"20": 25.0, "24": 40.0})
        copy.forget_learned("test")
        self.assertEqual(Server({"id": "learner", "name": "Learner", "driver": "demo"}).learned, {})

    def test_smart_cycle_reports_what_it_does(self):
        srv = Recorder("smart")
        write_json(srv.settings_file, {"mode": "smart", "smart_target": 60})
        srv.cycle()
        self.assertTrue(srv.state["reason"].startswith("smart: "))
        self.assertEqual(srv.state["smart"]["sensor"], "CPU")
        self.assertEqual(srv.calls, [srv.state["applied_speed"]])
        write_json(srv.settings_file, {"mode": "fixed"})
        srv.cycle()
        self.assertIsNone(srv.state["smart"])
        srv.settings_file.unlink()


class Checks(unittest.TestCase):
    def test_failed_fans(self):
        from fanctl.control import failed_fans
        spin = lambda n, rpm: {"name": n, "rpm": rpm, "pct": None, "ok": True}  # noqa: E731
        self.assertEqual(failed_fans([spin("A", 4000), spin("B", 0), spin("C", 4100)]), ["B"])
        self.assertEqual(failed_fans([spin("A", 200), spin("B", 250)]), [])        # all slow: a quiet server
        self.assertEqual(failed_fans([{"name": "P", "rpm": None, "pct": 0, "ok": True},
                                      {"name": "Q", "rpm": None, "pct": 30, "ok": True}]), ["P"])
        self.assertEqual(failed_fans([dict(spin("A", 4000), ok=False)]), ["A"])

    def test_ignored_commands_are_noticed(self):
        import time as t
        srv = Recorder("ignored")
        fans = lambda rpm: [{"name": "F", "rpm": rpm, "pct": None, "ok": True}]  # noqa: E731
        with srv.lock:
            srv.check_response("manual", 20, fans(4000), {})
            srv.check_response("manual", 60, fans(4000), {})   # raised by 40 points
            srv.probe["t"] -= 31
            srv.check_response("manual", 60, fans(4050), {})   # 30 s later the RPM has not moved
        self.assertTrue(srv.ignored)
        self.assertIn("did not follow", srv.events[0]["msg"])
        with srv.lock:
            srv.check_response("manual", 30, fans(4050), {})
            srv.probe["t"] = t.time() - 31
            srv.check_response("manual", 30, fans(2500), {})   # this time it followed
        self.assertFalse(srv.ignored)


class Scheduled(unittest.TestCase):
    def test_profile_caps_the_speed(self):
        srv = Recorder("scheduled")
        always = {"name": "Night", "days": list(range(7)), "start": "00:00", "end": "00:00", "max_speed": 25, "smart_target": None}
        write_json(srv.settings_file, {"mode": "fixed", "fixed_speed": 50, "schedule": [always]})
        srv.cycle()
        self.assertEqual(srv.calls, [25])
        self.assertTrue(srv.state["reason"].endswith(", Night cap 25%"))
        srv.settings_file.unlink()


class Durability(unittest.TestCase):
    def test_corrupt_settings_fall_back_to_automatic(self):
        srv = Recorder("corrupt")
        srv.settings_file.write_text('{"mode": "fixed", "fixed_spe')       # power cut mid-write
        self.assertEqual(srv.settings()["mode"], "auto")
        self.assertTrue(list(srv.settings_file.parent.glob(srv.settings_file.name + ".corrupt-*")))
        self.assertIn("corrupt", srv.events[0]["msg"])
        self.assertEqual(srv.settings()["mode"], "auto")                    # and it stays automatic
        srv.settings_file.unlink()

    def test_invalid_settings_fall_back_to_automatic(self):
        srv = Recorder("invalid")
        write_json(srv.settings_file, {"mode": "fixed", "fixed_speed": "fast"})
        self.assertEqual(srv.settings()["mode"], "auto")
        srv.settings()
        self.assertEqual(sum("invalid" in e["msg"] for e in srv.events), 1)  # said once, not every cycle
        srv.settings_file.unlink()

    def test_concurrent_writes_never_collide(self):
        import threading
        from fanctl.config import DATA_DIR
        target, errors = DATA_DIR / "race.json", []

        def writer(n):
            try:
                for i in range(30):
                    write_json(target, {"writer": n, "i": i, "pad": "x" * 2000})
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        import json
        self.assertEqual(json.loads(target.read_text())["i"], 29)
        self.assertEqual(list(DATA_DIR.glob("race.json.*.tmp")), [])           # nothing left behind
        target.unlink()


class Recorder(Server):
    """A demo server whose fan commands are recorded."""
    def __init__(self, sid):
        super().__init__({"id": sid, "name": sid, "driver": "demo"})
        self.calls = []
        drv = self.driver
        drv.set_auto = lambda: self.calls.append("auto")
        drv.set_speed = lambda pct: self.calls.append(pct)


def reading(temps, fans=None, power="on"):
    """A BMC reading: temps as (name, value, ok)."""
    return {"temps": [{"name": n, "entity": "", "value": v, "cpu": n.startswith("CPU"), "ok": ok, "warn": None}
                      for n, v, ok in temps],
            "fans": fans or [{"name": f"Fan{i}", "rpm": 4000, "pct": None, "ok": True} for i in range(1, 4)],
            "watts": 150, "power": power, "inlet": 22, "exhaust": None}


class Scripted(Recorder):
    """A recorded server whose BMC answers what the test says."""
    def __init__(self, sid, settings=None):
        super().__init__(sid)
        self.answers = []
        self.driver.read = self.next_reading
        write_json(self.settings_file, settings or {"mode": "fixed", "fixed_speed": 30})

    def next_reading(self):
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return json.loads(json.dumps(answer))  # a fresh copy each time, as a real read gives


class FailSafe(unittest.TestCase):
    """When the controller does not know, the BMC decides."""
    OK = [("CPU 1", 50, True), ("CPU 2", 48, True), ("Inlet Temp", 22, True)]

    def tearDown(self):
        for f in DATA.glob("settings-fs-*.json"):
            f.unlink()

    def test_no_cpu_reading_means_automatic(self):
        srv = Scripted("fs-nocpu")
        srv.answers = [reading([("Inlet Temp", 22, True), ("DIMM", 35, True)])]
        srv.cycle()
        self.assertEqual((srv.calls, srv.state["effective"]), (["auto"], "auto"))
        self.assertIn("no CPU", srv.state["reason"])

    def test_unreadable_bmc_gets_the_fans_back(self):
        from fanctl.drivers import DriverError
        srv = Scripted("fs-blind")
        srv.answers = [reading(self.OK)]
        srv.cycle()
        self.assertEqual(srv.calls, [30])
        srv.answers = [DriverError("timed out")]
        srv.cycle()
        srv.cycle()                                   # tried once a minute, not every cycle
        self.assertEqual(srv.calls, [30, "auto"])
        self.assertEqual(srv.state["effective"], "auto")

    def test_a_sensor_that_disappears_trips_the_failsafe(self):
        srv = Scripted("fs-missing")
        srv.answers = [reading(self.OK), reading(self.OK[:1] + self.OK[2:])]
        srv.cycle()
        srv.cycle()                                   # one missing reading is forgiven
        self.assertFalse(srv.state["failsafe"])
        srv.cycle()
        self.assertTrue(srv.state["failsafe"])
        self.assertEqual(srv.state["reason"], "CPU 2 stopped reporting")
        self.assertEqual(srv.calls[-1], "auto")

    def test_a_faulty_sensor_or_a_failed_fan_trips_it(self):
        srv = Scripted("fs-fault")
        srv.answers = [reading(self.OK[:2] + [("PCIe", 60, False)])]
        srv.cycle()
        self.assertEqual(srv.state["reason"], "PCIe reports a fault")
        fan = Scripted("fs-fan")
        dead = [{"name": "Fan1", "rpm": 0, "pct": None, "ok": True}] + [
            {"name": f"Fan{i}", "rpm": 4000, "pct": None, "ok": True} for i in (2, 3)]
        fan.answers = [reading(self.OK, dead)]
        fan.cycle()
        fan.cycle()                                   # two readings to count as failed
        fan.cycle()
        self.assertEqual((fan.state["reason"], fan.calls[-1]), ("fan Fan1 failed", "auto"))

    def test_failsafe_holds_then_eases_down(self):
        srv = Scripted("fs-hold", {"mode": "fixed", "fixed_speed": 30, "ramp_down_seconds": 60})
        srv.answers = [reading([("CPU 1", 80, True)])]
        srv.cycle()
        self.assertTrue(srv.state["failsafe"])
        srv.answers = [reading([("CPU 1", 40, True)])]
        srv.cycle()                                   # cool at once, but the BMC keeps the fans for a while
        self.assertTrue(srv.state["failsafe"])
        self.assertIn("failsafe held", srv.state["reason"])
        srv.failsafe_since -= 301
        srv.cycle()
        self.assertFalse(srv.state["failsafe"])
        self.assertEqual(srv.calls[-1], 50)           # back to manual from 50 %, not straight to 30 %

    def test_vendor_floor(self):
        srv = Scripted("fs-floor", {"mode": "fixed", "fixed_speed": 20})
        srv.driver.min_speed, srv.driver.vendor = 25, "Supermicro"
        srv.answers = [reading(self.OK)]
        srv.cycle()
        self.assertEqual(srv.calls, [25])
        self.assertIn("Supermicro floor 25%", srv.state["reason"])


class Shutdown(unittest.TestCase):
    def test_all_servers_released_together(self):
        import time as t
        from fanctl.server import release_all
        slow, fast = Recorder("slow"), Recorder("fast")
        slow.driver.set_auto = lambda: (t.sleep(2), slow.calls.append("auto"))
        start = t.time()
        self.assertEqual(release_all([slow, fast], deadline=10), [])
        self.assertLess(t.time() - start, 3.5)        # in parallel: one slow BMC doesn't delay the rest
        self.assertEqual((slow.calls, fast.calls), (["auto"], ["auto"]))
        self.assertTrue(slow.stop.is_set() and fast.stop.is_set())

    def test_a_stuck_loop_does_not_block_the_release(self):
        srv = Recorder("stuck")
        srv.cmd_lock.acquire()                        # a loop stuck mid-command
        try:
            self.assertTrue(srv.release(timeout=0.2))
        finally:
            srv.cmd_lock.release()
        self.assertEqual(srv.calls, ["auto"])


class DryRun(unittest.TestCase):
    def test_decides_but_sends_nothing(self):
        srv = Recorder("dry")
        write_json(srv.settings_file, {"mode": "fixed", "fixed_speed": 30, "dry_run": True})
        srv.cycle()
        srv.cycle()
        self.assertEqual(srv.calls, ["auto"])       # handed to the BMC once, then nothing
        self.assertTrue(srv.state["dry_run"])
        self.assertEqual(srv.state["applied_speed"], 30)
        self.assertIn("Dry run: would set fans to 30%", srv.events[0]["msg"])
        write_json(srv.settings_file, {"mode": "fixed", "fixed_speed": 30})
        srv.cycle()
        self.assertEqual(srv.calls, ["auto", 30])
        srv.settings_file.unlink()


class Stopping(unittest.TestCase):
    def test_no_command_after_release(self):
        import threading
        import time as _time
        srv = Recorder("slow")
        reading = threading.Event()
        real_read = srv.driver.read

        def slow_read():
            reading.set()
            _time.sleep(1)
            return real_read()

        srv.driver.read = slow_read
        t = threading.Thread(target=srv.cycle)
        t.start()
        reading.wait()
        srv.stop.set()
        srv.release()                 # while the cycle is still reading
        t.join()
        self.assertEqual(srv.calls, ["auto"])  # the late cycle sent nothing after the release


class LongHistory(unittest.TestCase):
    def test_five_minute_buckets(self):
        srv = Server({"id": "long", "name": "long", "driver": "demo"})
        for i in range(61):  # 0..300 s, every 5 s
            srv.record({"t": 1000 + i * 5, "cpu": 50 + (i % 2), "speed": 20 if i < 40 else None,
                        "inlet": 22, "exhaust": 35, "rpm": 4000, "fanpct": None, "watts": 150})
        self.assertEqual(len(srv.long), 1)
        p = srv.long[0]
        self.assertEqual((p["t"], p["cpu_max"], p["speed"], p["watts"]), (1300, 51, 20, 150))
        self.assertAlmostEqual(p["cpu"], 50.5, delta=0.05)

    def test_mostly_automatic_bucket_has_no_speed(self):
        from fanctl.control import aggregate
        pts = [{"t": i, "cpu": 50, "speed": None if i < 7 else 30} for i in range(10)]
        self.assertIsNone(aggregate(pts)["speed"])
