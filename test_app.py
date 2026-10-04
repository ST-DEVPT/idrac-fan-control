"""Self-check for the parsing, control, session and HTTP logic. Run: python test_app.py"""
import http.client
import json
import os
import shutil
import threading
import time
from collections import deque

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test-data")
shutil.rmtree(DATA, ignore_errors=True)
os.environ.update(DATA_DIR=DATA, WEB_PASSWORD="hunter2", METRICS_TOKEN="m-token", EMBED_TOKEN="e-token",
                  IDRAC_1_HOST="demo", IDRAC_1_NAME="Rack A", IDRAC_2_HOST="demo", IDRAC_2_NAME="Rack B")
import app  # noqa: E402

# ---------------------------------------------------------------- parsing

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

s = app.parse_sdr(SDR)
assert [f["name"] for f in s["fans"]] == ["Fan1", "Fan2"]
assert [f["rpm"] for f in s["fans"]] == [3720, 3600]
assert [t["name"] for t in s["temps"]] == ["Inlet Temp", "Exhaust Temp", "CPU 1", "CPU 2"]
assert (s["inlet"], s["exhaust"], s["watts"]) == (23, 31, 112)

# 11th generation naming: "Ambient Temp", fan modules, no exhaust sensor
s = app.parse_sdr("""Ambient Temp     | 0Eh | ok  |  7.1 | 21 degrees C
FAN MOD 1A RPM   | 30h | ok  |  7.1 | 4200 RPM
Temp             | 01h | ok  |  3.1 | 40 degrees C""")
assert (s["inlet"], s["exhaust"]) == (21, None)
assert s["fans"][0]["name"] == "FAN MOD 1A"
assert s["temps"][1]["name"] == "CPU 1"

# ---------------------------------------------------------------- control

curve = [[30, 10], [50, 30], [70, 70]]
assert [app.curve_speed(curve, t) for t in (20, 40, 60, 90)] == [10, 20, 50, 70]

base = dict(app.DEFAULT_SETTINGS, curve=curve, failsafe_temp=75, fixed_speed=25)
assert app.decide({**base, "mode": "curve"}, 60)[:2] == ("manual", 50)
assert app.decide({**base, "mode": "fixed"}, 60)[:2] == ("manual", 25)
assert app.decide({**base, "mode": "fixed"}, 75)[::3] == ("auto", True)      # failsafe trips
assert app.decide({**base, "mode": "fixed"}, 73, True)[::3] == ("auto", True)  # hysteresis holds it
assert app.decide({**base, "mode": "fixed"}, 72, True)[::3] == ("manual", False)
assert app.decide({**base, "mode": "curve"}, None)[:2] == ("auto", None)     # no reading
assert app.decide({**base, "mode": "auto"}, 99)[::3] == ("auto", False)

w = deque()
assert app.ramped(w, 0, 40, 60) == 40
assert app.ramped(w, 10, 20, 60) == 40   # lower demand is held...
assert app.ramped(w, 59, 25, 60) == 40
assert app.ramped(w, 61, 25, 60) == 25   # ...until the delay has passed
assert app.ramped(w, 62, 50, 60) == 50   # higher demand applies at once
assert app.ramped(deque(), 0, 30, 0) == 30
# the delay may hold the fans faster than the curve asks, never slower
w = deque()
for step in range(500):
    want = (step * 37) % 101
    assert app.ramped(w, step * 5, want, 60) >= want

cur = dict(app.DEFAULT_SETTINGS)
assert app.validate_settings({"curve": [[60, 40], [30, 10]]}, cur)["curve"] == [[30, 10], [60, 40]]
for bad in ({"mode": "turbo"}, {"fixed_speed": 101}, {"fixed_speed": "20"}, {"fixed_speed": True},
            {"failsafe_temp": 30}, {"curve": [[30, 10]]}, {"curve": [[30, 150], [40, 20]]},
            {"ramp_down_seconds": -1}, {"ramp_down_seconds": 601}, {"ramp_down_seconds": 1.5}):
    try:
        app.validate_settings(bad, cur)
        raise AssertionError(f"accepted {bad}")
    except ValueError:
        pass


# ---------------------------------------------------------------- drivers

import drivers  # noqa: E402


class FakeDell(drivers.DellDriver):
    """Refuses the 0xff selector and fan ids above 3, like some 11th-generation BMCs."""
    def __init__(self):
        super().__init__("10.0.0.1", "root", "x")
        self.sent = []

    def ipmi(self, *args, timeout=20):
        self.sent.append(args)
        if args[:4] == ("raw", "0x30", "0x30", "0x02") and int(args[4], 16) > 3:
            raise drivers.DriverError("Unable to send RAW command (rsp=0xcc): Invalid data field in request")
        return ""


d = FakeDell()
d.set_speed(30)
assert d.fan_ids == [0, 1, 2, 3]
d.sent.clear()
d.set_speed(40)
assert d.sent == [("raw", "0x30", "0x30", "0x01", "0x00")] + [("raw", "0x30", "0x30", "0x02", f"0x0{i}", "0x28") for i in range(4)]


class DeadDell(FakeDell):
    def ipmi(self, *args, timeout=20):
        self.sent.append(args)
        if args[:4] == ("raw", "0x30", "0x30", "0x02"):
            raise drivers.DriverError("rsp=0xcc")
        return ""


d = DeadDell()
try:
    d.set_speed(30)
    raise AssertionError("expected a refusal")
except drivers.DriverError:
    assert d.sent[-1] == ("raw", "0x30", "0x30", "0x01", "0x01")  # handed back to automatic


class FakeSupermicro(drivers.SupermicroDriver):
    def __init__(self):
        super().__init__("10.0.0.2", "ADMIN", "x")
        self.sent = []

    def ipmi(self, *args, timeout=20):
        self.sent.append(args)
        return ""


d = FakeSupermicro()
d.set_speed(35)
assert d.sent == [("raw", "0x30", "0x45", "0x01", "0x01"), ("raw", "0x30", "0x70", "0x66", "0x01", "0x00", "0x23"),
                  ("raw", "0x30", "0x70", "0x66", "0x01", "0x01", "0x23")]
d.set_auto()
assert d.sent[-1] == ("raw", "0x30", "0x45", "0x01", "0x02")

# Redfish as an iLO 4 answers it: FanName / CurrentReading / Units, absent sensors, no exhaust
ILO_THERMAL = {"Fans": [{"FanName": "Fan 1", "CurrentReading": 23, "Units": "Percent", "Status": {"Health": "OK", "State": "Enabled"}},
                        {"FanName": "Fan 2", "CurrentReading": 25, "Units": "Percent", "Status": {"Health": "OK", "State": "Enabled"}},
                        {"FanName": "Fan 7", "CurrentReading": 0, "Units": "Percent", "Status": {"State": "Absent"}}],
               "Temperatures": [{"Name": "01-Inlet Ambient", "ReadingCelsius": 21, "PhysicalContext": "Intake", "Status": {"Health": "OK", "State": "Enabled"}},
                                {"Name": "02-CPU 1", "ReadingCelsius": 40, "PhysicalContext": "CPU", "Status": {"Health": "OK", "State": "Enabled"}},
                                {"Name": "03-CPU 2", "ReadingCelsius": 44, "PhysicalContext": "CPU", "Status": {"Health": "OK", "State": "Enabled"}},
                                {"Name": "04-P1 DIMM 1-6", "ReadingCelsius": 0, "Status": {"State": "Absent"}},
                                {"Name": "32-PCI 1", "ReadingCelsius": 60, "PhysicalContext": "SystemBoard", "Status": {"Health": "OK", "State": "Enabled"}}]}
ILO_POWER = {"PowerControl": [{"PowerConsumedWatts": 118}]}


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


d = FakeILO()
r = d.read()
assert d.model == "ProLiant DL360 Gen9" and r["power"] == "on" and r["watts"] == 118
assert [f["pct"] for f in r["fans"]] == [23, 25] and all(f["rpm"] is None for f in r["fans"])
assert [t["name"] for t in r["temps"]] == ["01-Inlet Ambient", "CPU 1", "CPU 2", "32-PCI 1"]
assert (r["inlet"], r["exhaust"]) == (21, None)
d.set_speed(30)
assert d.ssh_sent == ["fan p 0 max 77", "fan p 1 max 77"]
d.set_speed(30)
assert len(d.ssh_sent) == 2                 # unchanged cap is not re-sent every cycle
d.set_auto()
assert d.ssh_sent[-2:] == ["fan p 0 max 255", "fan p 1 max 255"]

# standard Redfish: Name / Reading / ReadingUnits
_, fans, _ = drivers.parse_redfish({"Fans": [{"Name": "Fan1", "Reading": 5400, "ReadingUnits": "RPM"}]})
assert fans[0]["rpm"] == 5400 and fans[0]["pct"] is None
assert drivers.bare_host("[fe80::1]:443") == "fe80::1" and drivers.bare_host("10.0.0.5:8443") == "10.0.0.5"

# ---------------------------------------------------------------- configuration

assert app.env_servers({}) == []
srv = app.env_servers({"IDRAC_HOST": "10.0.0.5", "IDRAC_NAME": "R720"})
assert srv[0]["id"] == "r720" and srv[0]["driver"] == "dell" and srv[0]["legacy"]   # keeps 1.0's settings.json
srv = app.env_servers({"IDRAC_2_HOST": "b", "IDRAC_1_HOST": "a", "IDRAC_1_NAME": "Same", "IDRAC_2_NAME": "Same",
                       "IDRAC_2_DRIVER": "redfish"})
assert [s["id"] for s in srv] == ["same", "same-2"] and srv[1]["host"] == "b" and srv[1]["driver"] == "redfish"
assert app.env_servers({"IDRAC_1_HOST": "x", "IDRAC_1_DRIVER": "bogus"}) == []
assert list(app.SERVERS) == ["rack-a", "rack-b"] and app.SERVERS["rack-a"].driver.kind == "demo"

ok = app.validate_server({"name": "DL360", "driver": "redfish", "host": "10.0.0.9", "username": "Administrator", "password": "pw"})
assert ok["password"] == "pw" and ok["verify_tls"] is False
assert app.validate_server({"name": "Renamed"}, ok)["password"] == "pw"        # edit keeps the password
assert app.validate_server({"name": "Demo", "driver": "demo"})["host"] == ""
for bad in ({"name": "", "driver": "dell", "host": "h", "username": "u", "password": "p"},
            {"name": "x", "driver": "nope", "host": "h", "username": "u", "password": "p"},
            {"name": "x", "driver": "dell", "host": "h:623", "username": "u", "password": "p"},
            {"name": "x", "driver": "redfish", "host": "https://h/x", "username": "u", "password": "p"},
            {"name": "x", "driver": "redfish", "host": "local", "username": "u", "password": "p"},
            {"name": "x", "driver": "dell", "host": "h", "username": "u"},
            {"name": "x", "driver": "dell", "host": "h", "username": "", "password": "p"},
            {"name": "x", "driver": "redfish", "host": "h", "username": "u", "password": "p", "verify_tls": "yes"}):
    try:
        app.validate_server(bad)
        raise AssertionError(f"accepted {bad}")
    except ValueError:
        pass

# 1.x settings files said "dell" for automatic mode
app.write_json(app.SERVERS["rack-a"].settings_file, {"mode": "dell"})
assert app.SERVERS["rack-a"].settings()["mode"] == "auto"
app.SERVERS["rack-a"].settings_file.unlink()

# ---------------------------------------------------------------- sessions

key = app.session_key()
assert app.valid_token(key, app.make_token(key, 60))
assert not app.valid_token(key, app.make_token(key, -1))             # expired
assert not app.valid_token(key, app.make_token(b"other", 60))        # wrong key
assert not app.valid_token(key, "")
assert not app.valid_token(key, f"{int(time.time()) + 999}.deadbeef")  # forged expiry
assert not app.valid_token(key, "x.é")                                # non-ASCII cookie

# ---------------------------------------------------------------- history persistence

a = app.SERVERS["rack-a"]
a.cycle()
a.save_history()
copy = app.Server({"id": "rack-a", "name": "Rack A", "driver": "demo"})
assert copy.load_history() and copy.history[-1]["t"] == a.history[-1]["t"]
app.SERVERS["rack-b"].cycle()

# ---------------------------------------------------------------- alerts

HOOK = "https://discord.com/api/webhooks/123456789/abc-DEF_123"
cfg = app.validate_alerts({"webhook_url": HOOK, "username": "Fans", "mention": "role:123456789012",
                           "mention_levels": ["error", "warn"], "colors": {"warn": "#ABCDEF"},
                           "events": {"hot": {"enabled": True, "title": "{server} hot {cpu}"}}},
                          app.alert_config())
assert app.webhook_of(cfg) == HOOK and cfg["colors"]["warn"] == "#abcdef" and cfg["events"]["hot"]["enabled"]
pub = app.public_alert_config(cfg)
assert "webhook_url" not in pub and HOOK not in json.dumps(pub) and pub["webhook"]["hint"] == "…_123"
p = app.build_payload(cfg, "hot", {"server": "R1", "cpu": "70"})
assert p["embeds"][0]["title"] == "R1 hot 70" and p["embeds"][0]["color"] == 0xABCDEF
assert p["content"] == "<@&123456789012>" and p["allowed_mentions"] == {"parse": [], "roles": ["123456789012"]}
p = app.build_payload(cfg, "recovered", {"server": "R1", "error": "@everyone"})   # "ok" level: no mention
assert "content" not in p and p["allowed_mentions"] == {"parse": []}
assert app.fill("{server.__class__} {x} {server}", {"server": "R1"}) == "{server.__class__} {x} R1"
for bad in ({"webhook_url": "https://evil.example/api/webhooks/1/x"}, {"username": "My Discord bot"},
            {"avatar_url": "http://insecure/img.png"}, {"mention": "@everyone"}, {"mention_levels": ["loud"]},
            {"cooldown_minutes": -1}, {"colors": {"warn": "red"}}, {"events": {"nope": {}}},
            {"events": {"hot": {"title": ""}}}, {"enabled": "yes"}):
    try:
        app.validate_alerts(bad, app.alert_config())
        raise AssertionError(f"accepted {bad}")
    except ValueError:
        pass
assert app.validate_alerts({"webhook_url": ""}, cfg)["webhook_url"] == ""            # "" clears it
assert app.validate_alerts({"username": "x"}, cfg)["webhook_url"] == HOOK            # absent keeps it

# ---------------------------------------------------------------- HTTP

httpd = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
PORT = httpd.server_address[1]


def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    h = {"Content-Type": "application/json", **(headers or {})}
    c.request(method, path, None if body is None else json.dumps(body), h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, dict(r.getheaders()), data


st, h, _ = req("GET", "/")
assert st == 303 and h["Location"] == "/login"
assert req("GET", "/api/state")[0] == 401
assert req("GET", "/healthz")[0] == 200
st, h, _ = req("GET", "/login")
assert st == 200 and "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["X-Frame-Options"] == "DENY"
assert "script-src 'self';" in h["Content-Security-Policy"]
assert req("GET", "/static/style.css")[0] == 200
assert req("GET", "/static/../app.py")[0] in (401, 404)
assert req("GET", "/static/%2e%2e/app.py")[0] in (401, 404)
assert req("GET", "/static/fonts/../../app.py")[0] in (401, 404)
etag = req("GET", "/static/app.js")[1]["ETag"]
assert req("GET", "/static/app.js", headers={"If-None-Match": etag})[0] == 304

t0 = time.time()
assert req("POST", "/api/login", {"password": "nope"})[0] == 401
assert time.time() - t0 >= 1                                          # failures are slowed down
assert req("POST", "/api/login", {"password": "hunter2"}, {"Content-Type": "text/plain"})[0] == 415
assert req("POST", "/api/login", {"password": "hunter2"}, {"Origin": "http://evil.example"})[0] == 403
st, h, _ = req("POST", "/api/login", {"password": "hunter2"})
assert st == 200 and "HttpOnly" in h["Set-Cookie"] and "SameSite=Strict" in h["Set-Cookie"]
assert "Secure" not in h["Set-Cookie"]
cookie = {"Cookie": h["Set-Cookie"].split(";")[0]}
assert "Secure" in req("POST", "/api/login", {"password": "hunter2"}, {"X-Forwarded-Proto": "https"})[1]["Set-Cookie"]

st, _, data = req("GET", "/api/state?server=rack-b", headers=cookie)
d = json.loads(data)
assert st == 200 and d["id"] == "rack-b" and d["driver"] == "demo" and d["control"] is True
assert "password" not in data.decode().lower()                        # credentials never leave the server
assert req("GET", "/api/state?server=nope", headers=cookie)[0] == 404

assert req("POST", "/api/settings?server=rack-b", {"mode": "fixed", "fixed_speed": 33}, cookie)[0] == 200
assert app.SERVERS["rack-b"].settings()["fixed_speed"] == 33
assert app.SERVERS["rack-a"].settings()["fixed_speed"] == 20         # other server untouched
assert req("POST", "/api/settings?server=rack-b", {"fixed_speed": 500}, cookie)[0] == 400
assert req("POST", "/api/settings", {"mode": "dell"})[0] == 401
assert req("POST", "/api/settings?server=rack-b", {"x": "y" * 20000}, cookie)[0] == 413
assert req("POST", "/api/test-alert", {}, cookie)[0] == 400         # no webhook configured
st, _, data = req("GET", "/api/alerts", headers=cookie)
assert st == 200 and json.loads(data)["webhook"]["set"] is False
assert req("GET", "/api/alerts?token=e-token")[0] == 401                # embed token can't read alerts
st, _, data = req("POST", "/api/alerts", {"webhook_url": HOOK, "cooldown_minutes": 5}, cookie)
assert st == 200 and HOOK not in data.decode() and json.loads(data)["cooldown_minutes"] == 5
assert req("POST", "/api/alerts", {"webhook_url": "https://evil.example/x"}, cookie)[0] == 400
assert req("POST", "/api/alerts", {"cooldown_minutes": 1}, {"Content-Type": "application/json"})[0] == 401
assert req("POST", "/api/test-alert", {"kind": "nope"}, cookie)[0] == 400

# embed token: read-only views only
assert req("GET", "/embed")[0] == 401
st, h, _ = req("GET", "/embed?token=e-token")
assert st == 200 and "frame-ancestors *" in h["Content-Security-Policy"] and "X-Frame-Options" not in h
assert req("GET", "/api/state?server=rack-a&token=e-token")[0] == 200
assert req("GET", "/api/state?token=wrong")[0] == 401
assert req("POST", "/api/settings?token=e-token", {"mode": "dell"})[0] == 401

# metrics
assert req("GET", "/metrics")[0] == 401
st, _, data = req("GET", "/metrics", headers={"Authorization": "Bearer m-token"})
text = data.decode()
assert st == 200 and 'idrac_up{server="rack-a",name="Rack A",driver="demo"} 1' in text
assert 'idrac_fan_rpm{server="rack-a",name="Rack A",driver="demo",fan="Fan1"}' in text
assert "idrac_last_update_timestamp_seconds" in text and "e+" not in text

# servers managed in the dashboard
st, _, data = req("GET", "/api/overview", headers=cookie)
ov = json.loads(data)
assert st == 200 and [x["id"] for x in ov["servers"]] == ["rack-a", "rack-b"]
assert {"dell", "supermicro", "ilo4-unlocked", "redfish", "ipmi", "demo"} == {x["kind"] for x in ov["drivers"]}
assert "spark" in ov["servers"][0] and "password" not in data.decode().lower().replace("has_password", "")
assert req("GET", "/api/overview?token=e-token")[0] == 401
st, _, data = req("GET", "/api/integrations", headers=cookie)
ig = json.loads(data)
assert st == 200 and ig["metrics_token"] is True and ig["embed_token"] is True and "m-token" not in data.decode()
assert "e-token" not in data.decode() and req("GET", "/api/integrations?token=e-token")[0] == 401
st, _, data = req("GET", "/api/metrics-preview", headers=cookie)
assert st == 200 and b"idrac_up" in data and req("GET", "/api/metrics-preview")[0] == 401
assert req("GET", "/static/grafana.json")[0] == 200
st, _, data = req("POST", "/api/servers/test", {"name": "T", "driver": "demo"}, cookie)
assert st == 200 and json.loads(data)["ok"] is True and json.loads(data)["fans"] == 6
st, _, data = req("POST", "/api/servers/test", {"name": "T", "driver": "redfish", "host": "nope..", "username": "u", "password": "p"}, cookie)
assert st == 400
st, _, data = req("POST", "/api/servers", {"name": "Lab box", "driver": "demo"}, cookie)
assert st == 201 and json.loads(data)["id"] == "lab-box" and "lab-box" in app.SERVERS
assert json.loads(app.SERVERS_FILE.read_text())[0]["name"] == "Lab box"
st, _, data = req("POST", "/api/servers", {"name": "Lab box", "driver": "demo"}, cookie)
assert json.loads(data)["id"] == "lab-box-2"
st, _, data = req("POST", "/api/servers/lab-box", {"name": "Lab box", "driver": "redfish", "host": "10.9.9.9",
                                                   "username": "admin", "password": "s3cret"}, cookie)
assert st == 200 and app.SERVERS["lab-box"].driver.kind == "redfish"
st, _, data = req("GET", "/api/servers/lab-box", headers=cookie)
assert json.loads(data)["has_password"] is True and "s3cret" not in data.decode()
assert req("POST", "/api/servers/rack-a", {"name": "x"}, cookie)[0] == 409      # environment servers are read-only
assert req("POST", "/api/servers/lab-box/delete", {}, cookie)[0] == 200 and "lab-box" not in app.SERVERS
assert req("POST", "/api/servers/lab-box-2/delete", {}, cookie)[0] == 200
assert req("POST", "/api/servers", {"name": "x", "driver": "demo"})[0] == 401
assert json.loads(app.SERVERS_FILE.read_text()) == []

st, h, _ = req("POST", "/api/logout", {}, cookie)
assert "Max-Age=0" in h["Set-Cookie"]

# ---------------------------------------------------------------- status reports

assert app.sparkline([1, 2, 3, 4, 5, 6, 7, 8]) == "▁▂▃▄▅▆▇█"
assert app.sparkline([5, 5, 5]) == "▁▁▁" and app.sparkline([]) == "" and len(app.sparkline(range(500))) == 24
app.save_alerts(app.validate_alerts({"events": {"report": {"enabled": True}}, "report_minutes": 60,
                                     "report_mode": "edit"}, app.alert_config()))
rep = app.build_report(app.alert_config(), app.SERVERS)
assert len(rep["embeds"]) == 2 and rep["embeds"][0]["title"] == "Rack A: status"
assert any(f["name"].startswith("CPU, last") for f in rep["embeds"][0]["fields"])
assert len(json.dumps(rep)) < 6000 and rep["allowed_mentions"] == {"parse": []}

calls = []
def fake_post(url, payload, method="POST"):
    calls.append((method, url.rsplit("/", 2)[-1]))
    if method == "PATCH" and len(calls) > 2:
        raise app.urllib.error.HTTPError(url, 404, "Unknown Message", {}, None)
    return {"id": f"m{len(calls)}"}
app.post_webhook = fake_post
app.send_report()
app.send_report()                          # not due yet: nothing sent
app.send_report(force=True)                # due: the same message is edited
app.send_report(force=True)                # deleted in Discord: a new message is posted
assert calls == [("POST", "abc-DEF_123"), ("PATCH", "m1"), ("PATCH", "m1"), ("POST", "abc-DEF_123")], calls
assert json.loads(app.REPORT_STATE.read_text())["message_id"] == "m4"
app.save_alerts(app.validate_alerts({"report_mode": "post"}, app.alert_config()))
calls.clear()
app.send_report(force=True)
assert calls == [("POST", "abc-DEF_123")]

app.WEB_PASSWORD = "changed"
assert app.session_key() != key                                       # new password signs everyone out

httpd.shutdown()
shutil.rmtree(DATA, ignore_errors=True)
print("ok")
