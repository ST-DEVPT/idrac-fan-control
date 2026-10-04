import unittest

from fanctl.config import write_json
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


class Recorder(Server):
    """A demo server whose fan commands are recorded."""
    def __init__(self, sid):
        super().__init__({"id": sid, "name": sid, "driver": "demo"})
        self.calls = []
        drv = self.driver
        drv.set_auto = lambda: self.calls.append("auto")
        drv.set_speed = lambda pct: self.calls.append(pct)


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
