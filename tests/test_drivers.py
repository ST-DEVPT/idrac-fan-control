import unittest

from fanctl import drivers
from fanctl.drivers import DriverError, bare_host, parse_redfish, parse_sdr

SDR = """Fan1 RPM         | 30h | ok  |  7.1 | 3720 RPM
Fan2 RPM         | 31h | ok  |  7.1 | 3600 RPM
Fan Redundancy   | 75h | ok  |  7.1 | Fully Redundant
Inlet Temp       | 04h | ok  |  7.1 | 23 degrees C
Exhaust Temp     | 01h | ok  |  7.1 | 31 degrees C
Temp             | 0Eh | ok  |  3.1 | 44 degrees C
Temp             | 0Fh | ok  |  3.2 | 41 degrees C
Temp             | 10h | ns  |  3.3 | No Reading
Pwr Consumption  | 77h | ok  |  7.1 | 112 Watts
Current 1        | 6Ah | ok  | 10.1 | 0.40 Amps"""

ILO_THERMAL = {
    "Fans": [{"FanName": "Fan 1", "CurrentReading": 23, "Units": "Percent", "Status": {"Health": "OK", "State": "Enabled"}},
             {"FanName": "Fan 2", "CurrentReading": 25, "Units": "Percent", "Status": {"Health": "OK", "State": "Enabled"}},
             {"FanName": "Fan 7", "CurrentReading": 0, "Units": "Percent", "Status": {"State": "Absent"}}],
    "Temperatures": [
        {"Name": "01-Inlet Ambient", "ReadingCelsius": 21, "PhysicalContext": "Intake", "Status": {"Health": "OK", "State": "Enabled"}},
        {"Name": "02-CPU 1", "ReadingCelsius": 40, "PhysicalContext": "CPU", "Status": {"Health": "OK", "State": "Enabled"}},
        {"Name": "03-CPU 2", "ReadingCelsius": 44, "PhysicalContext": "CPU", "Status": {"Health": "OK", "State": "Enabled"}},
        {"Name": "04-P1 DIMM 1-6", "ReadingCelsius": 0, "Status": {"State": "Absent"}},
        {"Name": "32-PCI 1", "ReadingCelsius": 60, "PhysicalContext": "SystemBoard", "Status": {"Health": "OK", "State": "Enabled"}}]}
ILO_POWER = {"PowerControl": [{"PowerConsumedWatts": 118}]}


class Recording:
    """Records ipmitool calls instead of running them."""
    def __init__(self, *a, **kw):
        super().__init__("10.0.0.1", "root", "x")
        self.sent = []

    def ipmi(self, *args, timeout=20):
        self.sent.append(args)
        return self.answer(args)

    def answer(self, args):
        return ""


class FakeDell(Recording, drivers.DellDriver):
    """Refuses the 0xff selector and fan ids above 3, like some 11th-generation BMCs."""
    def answer(self, args):
        if args[:4] == ("raw", "0x30", "0x30", "0x02") and int(args[4], 16) > 3:
            raise DriverError("Unable to send RAW command (rsp=0xcc): Invalid data field in request")
        return ""


class DeadDell(Recording, drivers.DellDriver):
    def answer(self, args):
        if args[:4] == ("raw", "0x30", "0x30", "0x02"):
            raise DriverError("rsp=0xcc")
        return ""


class FakeSupermicro(Recording, drivers.SupermicroDriver):
    pass


class FakeILO(drivers.ILO4UnlockedDriver):
    def __init__(self):
        super().__init__("10.0.0.3", "Administrator", "x")
        self.ssh_sent = []

    def get(self, path):
        return {"/redfish/v1/Chassis": {"Members": [{"@odata.id": "/redfish/v1/Chassis/1/"}]},
                "/redfish/v1/Chassis/1/": {"Thermal": {"@odata.id": "/redfish/v1/Chassis/1/Thermal/"},
                                           "Power": {"@odata.id": "/redfish/v1/Chassis/1/Power/"}},
                "/redfish/v1/Systems": {"Members": [{"@odata.id": "/redfish/v1/Systems/1/"}]},
                "/redfish/v1/Systems/1/": {"Model": "ProLiant DL360 Gen9", "PowerState": "On"},
                "/redfish/v1/Chassis/1/Thermal/": ILO_THERMAL,
                "/redfish/v1/Chassis/1/Power/": ILO_POWER}[path]

    def ssh(self, command):
        self.ssh_sent.append(command)
        return ""


class Parsing(unittest.TestCase):
    def test_sdr_12th_generation(self):
        s = parse_sdr(SDR)
        self.assertEqual([f["name"] for f in s["fans"]], ["Fan1", "Fan2"])
        self.assertEqual([f["rpm"] for f in s["fans"]], [3720, 3600])
        self.assertEqual([t["name"] for t in s["temps"]], ["Inlet Temp", "Exhaust Temp", "CPU 1", "CPU 2"])
        self.assertEqual((s["inlet"], s["exhaust"], s["watts"]), (23, 31, 112))

    def test_sdr_11th_generation(self):
        s = parse_sdr("Ambient Temp     | 0Eh | ok  |  7.1 | 21 degrees C\n"
                      "FAN MOD 1A RPM   | 30h | ok  |  7.1 | 4200 RPM\n"
                      "Temp             | 01h | ok  |  3.1 | 40 degrees C")
        self.assertEqual((s["inlet"], s["exhaust"]), (21, None))
        self.assertEqual(s["fans"][0]["name"], "FAN MOD 1A")
        self.assertEqual(s["temps"][1]["name"], "CPU 1")

    def test_redfish_standard_names(self):
        _, fans, _ = parse_redfish({"Fans": [{"Name": "Fan1", "Reading": 5400, "ReadingUnits": "RPM"}]})
        self.assertEqual((fans[0]["rpm"], fans[0]["pct"]), (5400, None))

    def test_bare_host(self):
        self.assertEqual([bare_host(h) for h in ("10.0.0.5", "10.0.0.5:8443", "[fe80::1]:443", "[fe80::1]", "fe80::1")],
                         ["10.0.0.5", "10.0.0.5", "fe80::1", "fe80::1", "fe80::1"])


class Thresholds(unittest.TestCase):
    SENSOR = """Inlet Temp       | 23.000     | degrees C  | ok    | na        | -7.000    | 3.000     | 42.000    | 47.000    | na
Exhaust Temp     | 31.000     | degrees C  | ok    | na        | 3.000     | 8.000     | na        | 75.000    | na
Temp             | 44.000     | degrees C  | ok    | na        | 3.000     | 8.000     | 85.000    | 90.000    | na
Temp             | 41.000     | degrees C  | ok    | na        | 3.000     | 8.000     | 86.000    | 90.000    | na
Fan1 RPM         | 3720.000   | RPM        | ok    | na        | 360.000   | 600.000   | na        | na        | na"""

    def test_ipmi(self):
        t = drivers.parse_thresholds(self.SENSOR)
        self.assertEqual(t, {"Inlet Temp": [42.0], "Exhaust Temp": [75.0], "Temp": [85.0, 86.0]})
        s = parse_sdr(SDR, t)
        self.assertEqual([x["warn"] for x in s["temps"]], [42.0, 75.0, 85.0, 86.0])  # same-name CPUs keep their order

    def test_redfish(self):
        temps, _, _ = parse_redfish({"Temperatures": [
            {"Name": "PCI 1", "ReadingCelsius": 50, "UpperThresholdCritical": 100, "UpperThresholdFatal": 110},
            {"Name": "Inlet", "ReadingCelsius": 20, "UpperThresholdNonCritical": 42, "UpperThresholdCritical": 47},
            {"Name": "DIMM", "ReadingCelsius": 30, "UpperThresholdCritical": 0}]})
        self.assertEqual([t["warn"] for t in temps], [100.0, 42.0, None])


class Dell(unittest.TestCase):
    def test_per_fan_fallback(self):
        d = FakeDell()
        d.set_speed(30)
        self.assertEqual(d.fan_ids, [0, 1, 2, 3])
        d.sent.clear()
        d.set_speed(40)
        self.assertEqual(d.sent, [("raw", "0x30", "0x30", "0x01", "0x00")] +
                         [("raw", "0x30", "0x30", "0x02", f"0x0{i}", "0x28") for i in range(4)])

    def test_refusal_hands_back_to_automatic(self):
        d = DeadDell()
        with self.assertRaises(DriverError):
            d.set_speed(30)
        self.assertEqual(d.sent[-1], ("raw", "0x30", "0x30", "0x01", "0x01"))


class Supermicro(unittest.TestCase):
    def test_full_mode_then_both_zones(self):
        d = FakeSupermicro()
        d.set_speed(35)
        self.assertEqual(d.sent, [("raw", "0x30", "0x45", "0x01", "0x01"),
                                  ("raw", "0x30", "0x70", "0x66", "0x01", "0x00", "0x23"),
                                  ("raw", "0x30", "0x70", "0x66", "0x01", "0x01", "0x23")])
        d.sent.clear()
        d.set_speed(40)  # Full mode is not switched again on every cycle
        self.assertEqual(d.sent, [("raw", "0x30", "0x70", "0x66", "0x01", "0x00", "0x28"),
                                  ("raw", "0x30", "0x70", "0x66", "0x01", "0x01", "0x28")])
        d.set_auto()
        self.assertEqual(d.sent[-1], ("raw", "0x30", "0x45", "0x01", "0x02"))
        d.set_speed(40)  # after a release, Full mode is needed again
        self.assertEqual(d.sent[-3], ("raw", "0x30", "0x45", "0x01", "0x01"))


class ILO4(unittest.TestCase):
    def test_read(self):
        d = FakeILO()
        r = d.read()
        self.assertEqual((d.model, r["power"], r["watts"]), ("ProLiant DL360 Gen9", "on", 118))
        self.assertEqual([f["pct"] for f in r["fans"]], [23, 25])
        self.assertTrue(all(f["rpm"] is None for f in r["fans"]))
        self.assertEqual([t["name"] for t in r["temps"]], ["01-Inlet Ambient", "CPU 1", "CPU 2", "32-PCI 1"])
        self.assertEqual((r["inlet"], r["exhaust"]), (21, None))

    def test_caps(self):
        d = FakeILO()
        d.read()
        d.set_speed(30)
        self.assertEqual(d.ssh_sent, ["fan p 0 max 77", "fan p 1 max 77"])
        d.set_speed(30)
        self.assertEqual(len(d.ssh_sent), 2)  # an unchanged cap is not re-sent every cycle
        d.set_auto()
        self.assertEqual(d.ssh_sent[-2:], ["fan p 0 max 255", "fan p 1 max 255"])


class Redirects(unittest.TestCase):
    def test_redfish_refuses_redirects(self):
        import http.server
        import threading
        import urllib.error
        import urllib.request

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/steal")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        opener = urllib.request.build_opener(drivers.NoRedirect)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            opener.open(f"http://127.0.0.1:{srv.server_address[1]}/redfish/v1", timeout=5)
        cm.exception.close()
        srv.shutdown()
        srv.server_close()
