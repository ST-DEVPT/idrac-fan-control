"""Self-check for the parsing, control and session logic. Run: python test_app.py"""
import os
import shutil
import time

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test-data")
os.environ["DATA_DIR"] = DATA
import app

# 12th/13th generation naming
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

curve = [[30, 10], [50, 30], [70, 70]]
assert [app.curve_speed(curve, t) for t in (20, 40, 60, 90)] == [10, 20, 50, 70]

base = dict(app.DEFAULT_SETTINGS, curve=curve, failsafe_temp=75, fixed_speed=25)
assert app.decide({**base, "mode": "curve"}, 60)[:2] == ("manual", 50)
assert app.decide({**base, "mode": "fixed"}, 60)[:2] == ("manual", 25)
assert app.decide({**base, "mode": "fixed"}, 75)[:2] == ("dell", None)   # failsafe
assert app.decide({**base, "mode": "curve"}, None)[:2] == ("dell", None)  # no reading
assert app.decide({**base, "mode": "dell"}, 40)[:2] == ("dell", None)

assert app.validate_settings({"curve": [[60, 40], [30, 10]]})["curve"] == [[30, 10], [60, 40]]
for bad in ({"mode": "turbo"}, {"fixed_speed": 101}, {"fixed_speed": "20"}, {"fixed_speed": True},
            {"failsafe_temp": 30}, {"curve": [[30, 10]]}, {"curve": [[30, 150], [40, 20]]}):
    try:
        app.validate_settings(bad)
        raise AssertionError(f"accepted {bad}")
    except ValueError:
        pass

app.WEB_PASSWORD = "hunter2"
key = app.session_key()
assert app.valid_token(key, app.make_token(key, 60))
assert not app.valid_token(key, app.make_token(key, -1))             # expired
assert not app.valid_token(key, app.make_token(b"other", 60))        # wrong key
assert not app.valid_token(key, "")
assert not app.valid_token(key, f"{int(time.time()) + 999}.deadbeef")  # forged expiry
assert not app.valid_token(key, "x.é")                                # non-ASCII cookie
app.WEB_PASSWORD = "changed"
assert app.session_key() != key                                       # new password signs everyone out

shutil.rmtree(DATA, ignore_errors=True)
print("ok")
