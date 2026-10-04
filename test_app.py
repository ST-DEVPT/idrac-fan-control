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
assert app.decide({**base, "mode": "fixed"}, 75)[::3] == ("dell", True)      # failsafe trips
assert app.decide({**base, "mode": "fixed"}, 73, True)[::3] == ("dell", True)  # hysteresis holds it
assert app.decide({**base, "mode": "fixed"}, 72, True)[::3] == ("manual", False)
assert app.decide({**base, "mode": "curve"}, None)[:2] == ("dell", None)     # no reading
assert app.decide({**base, "mode": "dell"}, 99)[::3] == ("dell", False)

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


# ---------------------------------------------------------------- per-fan fallback

class FakeBMC(app.Server):
    """Refuses the 0xff selector and fan ids above 3, like some 11th-generation BMCs."""
    def __init__(self):
        super().__init__("fake", "Fake", "10.0.0.1", "root", "x")
        self.sent = []

    def ipmi(self, *args, timeout=20):
        self.sent.append(args)
        if args[:4] == ("raw", "0x30", "0x30", "0x02") and int(args[4], 16) > 3:
            raise app.IPMIError("Unable to send RAW command (rsp=0xcc): Invalid data field in request")
        return ""


b = FakeBMC()
b.set_fixed(30)
assert b.fan_ids == [0, 1, 2, 3]
b.sent.clear()
b.set_fixed(40)
assert b.sent == [("raw", "0x30", "0x30", "0x01", "0x00")] + [("raw", "0x30", "0x30", "0x02", f"0x0{i}", "0x28") for i in range(4)]


class DeadBMC(FakeBMC):
    def ipmi(self, *args, timeout=20):
        self.sent.append(args)
        if args[:4] == ("raw", "0x30", "0x30", "0x02"):
            raise app.IPMIError("rsp=0xcc")
        return ""


b = DeadBMC()
try:
    b.set_fixed(30)
    raise AssertionError("expected a refusal")
except app.IPMIError:
    assert b.sent[-1] == ("raw", "0x30", "0x30", "0x01", "0x01")  # handed back to Dell

# ---------------------------------------------------------------- configuration

assert list(app.load_servers({}).keys()) == ["local"]
srv = app.load_servers({"IDRAC_HOST": "10.0.0.5", "IDRAC_NAME": "R720"})
assert list(srv) == ["r720"] and srv["r720"].settings_file.name == "settings.json"  # earlier versions' file
srv = app.load_servers({"IDRAC_2_HOST": "b", "IDRAC_1_HOST": "a", "IDRAC_1_NAME": "Same", "IDRAC_2_NAME": "Same"})
assert list(srv) == ["same", "same-2"] and srv["same-2"].host == "b"
assert list(app.SERVERS) == ["rack-a", "rack-b"]

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
copy = app.Server("rack-a", "Rack A", "demo", "", "")
assert copy.load_history() and copy.history[-1]["t"] == a.history[-1]["t"]
app.SERVERS["rack-b"].cycle()

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
assert st == 200 and d["id"] == "rack-b" and [x["id"] for x in d["servers"]] == ["rack-a", "rack-b"]
assert "password" not in data.decode().lower()                        # credentials never leave the server
assert req("GET", "/api/state?server=nope", headers=cookie)[0] == 404

assert req("POST", "/api/settings?server=rack-b", {"mode": "fixed", "fixed_speed": 33}, cookie)[0] == 200
assert app.SERVERS["rack-b"].settings()["fixed_speed"] == 33
assert app.SERVERS["rack-a"].settings()["fixed_speed"] == 20         # other server untouched
assert req("POST", "/api/settings?server=rack-b", {"fixed_speed": 500}, cookie)[0] == 400
assert req("POST", "/api/settings", {"mode": "dell"})[0] == 401
assert req("POST", "/api/settings?server=rack-b", {"x": "y" * 20000}, cookie)[0] == 413
assert req("POST", "/api/test-alert", {}, cookie)[0] == 400         # no webhook configured

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
assert st == 200 and 'idrac_up{server="rack-a",name="Rack A"} 1' in text
assert 'idrac_fan_rpm{server="rack-a",name="Rack A",fan="Fan1"}' in text
assert "idrac_last_update_timestamp_seconds" in text and "e+" not in text

st, h, _ = req("POST", "/api/logout", {}, cookie)
assert "Max-Age=0" in h["Set-Cookie"]

app.WEB_PASSWORD = "changed"
assert app.session_key() != key                                       # new password signs everyone out

httpd.shutdown()
shutil.rmtree(DATA, ignore_errors=True)
print("ok")
