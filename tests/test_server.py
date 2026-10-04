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
