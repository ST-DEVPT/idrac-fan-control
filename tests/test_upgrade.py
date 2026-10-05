"""Data files written by older versions still load, and land inside today's limits. Every case is
a file as a past release actually wrote it."""
import json
import unittest

from fanctl import alerts, server
from fanctl.config import DATA_DIR
from fanctl.server import Server

# 1.0/1.1: one server, settings.json, "dell" as the name of automatic mode, no newer fields
SETTINGS_1X = {"mode": "dell", "fixed_speed": 20, "curve": [[30, 10], [45, 15], [55, 25], [65, 45], [72, 70]],
               "failsafe_temp": 75, "ramp_down_seconds": 60, "pcie_cooling": None}
# 2.0-2.2: limits wider than 2.3 allows
SETTINGS_20 = {**SETTINGS_1X, "mode": "fixed", "min_speed": 5, "failsafe_temp": 95, "exhaust_limit": 60,
               "bmc_thresholds": True, "threshold_margin": 5, "dry_run": False, "smart_target": 60,
               "quiet": {"enabled": True, "start": "23:00", "end": "07:00", "max_speed": 25}}
# 1.x history points had no fan percentage, power or rpm yet
HISTORY_1X = {"history": [{"t": 0, "cpu": 50.0, "speed": 20, "inlet": 22.0, "exhaust": 35.0}], "events": []}
# alerts.json before ntfy/Gotify, inlet and failsafe-lasting alerts
ALERTS_20 = {"enabled": True, "webhook_url": "https://discord.com/api/webhooks/1/abc", "username": "Fans",
             "events": {"failsafe": {"enabled": True, "title": "{server} hot", "message": "{cpu}"}}}


def write(name, data):
    (DATA_DIR / name).write_text(json.dumps(data))


class Upgrade(unittest.TestCase):
    def tearDown(self):
        for name in ("settings.json", "settings-up-20.json", "history-up-1x.json", "alerts.json", "servers.json"):
            (DATA_DIR / name).unlink(missing_ok=True)

    def test_1x_single_server_settings(self):
        write("settings.json", SETTINGS_1X)
        s = Server({"id": "up-1x", "name": "R720", "driver": "demo", "legacy": True}).settings()
        self.assertEqual(s["mode"], "auto")                       # "dell" was automatic
        self.assertEqual((s["min_speed"], s["schedule"], s["exhaust_limit"]), (20, [], 60))  # new fields filled in
        self.assertEqual(s["curve"], SETTINGS_1X["curve"])

    def test_20_settings_brought_inside_the_new_limits(self):
        write("settings-up-20.json", SETTINGS_20)
        s = Server({"id": "up-20", "name": "R420", "driver": "demo"}).settings()
        self.assertEqual((s["mode"], s["min_speed"], s["failsafe_temp"]), ("fixed", 10, 90))  # kept, not refused

    def test_1x_history_loads_and_aggregates(self):
        import time
        hist = {**HISTORY_1X, "history": [{**HISTORY_1X["history"][0], "t": time.time() - 60}]}
        write("history-up-1x.json", hist)
        srv = Server({"id": "up-1x", "name": "R720", "driver": "demo"})
        self.assertTrue(srv.load_history())
        from fanctl.control import aggregate
        self.assertEqual(aggregate(list(srv.history))["cpu"], 50.0)

    def test_20_alerts_gain_the_new_options(self):
        write("alerts.json", ALERTS_20)
        cfg = alerts.alert_config()
        self.assertEqual(alerts.webhook_of(cfg), ALERTS_20["webhook_url"])
        self.assertEqual(cfg["events"]["failsafe"]["title"], "{server} hot")
        self.assertEqual((cfg["inlet_threshold"], cfg["failsafe_minutes"]), (35, 10))
        self.assertEqual(cfg["channels"]["ntfy"], {"enabled": False, "url": "", "token": ""})
        self.assertTrue(cfg["events"]["fan_failed"]["enabled"])

    def test_20_dashboard_servers(self):
        write("servers.json", [{"id": "dl360", "name": "DL360", "driver": "redfish", "host": "10.0.0.9",
                                "username": "Administrator", "password": "pw", "verify_tls": False},
                               {"id": "gone", "name": "Old", "driver": "a-driver-that-no-longer-exists"}])
        self.assertEqual([s["id"] for s in server.dashboard_servers()], ["dl360"])
