import json
import unittest
import urllib.error

from fanctl import alerts
from fanctl.server import SERVERS

HOOK = "https://discord.com/api/webhooks/123456789/abc-DEF_123"


class Config(unittest.TestCase):
    def setUp(self):
        self.cfg = alerts.validate_alerts({"webhook_url": HOOK, "username": "Fans", "mention": "role:123456789012",
                                           "mention_levels": ["error", "warn"], "colors": {"warn": "#ABCDEF"},
                                           "events": {"hot": {"enabled": True, "title": "{server} hot {cpu}"}}},
                                          alerts.alert_config())

    def test_merge_and_public_view(self):
        self.assertEqual(alerts.webhook_of(self.cfg), HOOK)
        self.assertEqual(self.cfg["colors"]["warn"], "#abcdef")
        pub = alerts.public_alert_config(self.cfg)
        self.assertNotIn("webhook_url", pub)
        self.assertNotIn(HOOK, json.dumps(pub))
        self.assertEqual(pub["webhook"]["hint"], "…_123")

    def test_payload_and_mentions(self):
        p = alerts.build_payload(self.cfg, "hot", {"server": "R1", "cpu": "70"})
        self.assertEqual((p["embeds"][0]["title"], p["embeds"][0]["color"]), ("R1 hot 70", 0xABCDEF))
        self.assertEqual(p["content"], "<@&123456789012>")
        self.assertEqual(p["allowed_mentions"], {"parse": [], "roles": ["123456789012"]})
        p = alerts.build_payload(self.cfg, "recovered", {"server": "R1", "error": "@everyone"})  # "ok": no mention
        self.assertNotIn("content", p)
        self.assertEqual(p["allowed_mentions"], {"parse": []})

    def test_templates_cannot_reach_attributes(self):
        self.assertEqual(alerts.fill("{server.__class__} {x} {server}", {"server": "R1"}), "{server.__class__} {x} R1")

    def test_webhook_keep_and_clear(self):
        self.assertEqual(alerts.validate_alerts({"webhook_url": ""}, self.cfg)["webhook_url"], "")
        self.assertEqual(alerts.validate_alerts({"username": "x"}, self.cfg)["webhook_url"], HOOK)

    def test_rejects(self):
        for bad in ({"webhook_url": "https://evil.example/api/webhooks/1/x"}, {"username": "My Discord bot"},
                    {"avatar_url": "http://insecure/img.png"}, {"mention": "@everyone"}, {"mention_levels": ["loud"]},
                    {"cooldown_minutes": -1}, {"colors": {"warn": "red"}}, {"events": {"nope": {}}},
                    {"events": {"hot": {"title": ""}}}, {"enabled": "yes"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                alerts.validate_alerts(bad, alerts.alert_config())


class Channels(unittest.TestCase):
    def test_validation_and_secrets(self):
        cfg = alerts.validate_alerts({"channels": {"ntfy": {"enabled": True, "url": "https://ntfy.sh/fans-xyz42", "token": "tk_abc"}}},
                                     alerts.alert_config())
        self.assertEqual(alerts.channels_of(cfg), ["ntfy"])
        pub = alerts.public_alert_config(cfg)["channels"]["ntfy"]
        self.assertEqual((pub["url_set"], pub["token_set"]), (True, True))
        self.assertNotIn("fans-xyz42", json.dumps(alerts.public_alert_config(cfg)))   # the topic is the password
        self.assertNotIn("tk_abc", json.dumps(alerts.public_alert_config(cfg)))
        kept = alerts.validate_alerts({"channels": {"ntfy": {"enabled": False}}}, cfg)
        self.assertEqual(kept["channels"]["ntfy"]["url"], "https://ntfy.sh/fans-xyz42")
        for bad in ({"ntfy": {"url": "ftp://x/y"}}, {"ntfy": {"url": "https://ntfy.sh"}}, {"nope": {}},
                    {"gotify": {"token": "a b"}}, {"webhook": {"enabled": "yes"}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                alerts.validate_alerts({"channels": bad}, alerts.alert_config())

    def test_what_each_channel_receives(self):
        sent = []

        class Answer:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return b"{}"

        def fake(req, timeout):
            sent.append((req.full_url, dict(req.header_items()), json.loads(req.data)))
            return Answer()
        cfg = alerts.validate_alerts({"channels": {
            "ntfy": {"enabled": True, "url": "https://ntfy.example.com/sub/fans", "token": "tk_1"},
            "gotify": {"enabled": True, "url": "https://gotify.example.com/", "token": "Aabc"},
            "webhook": {"enabled": True, "url": "https://hooks.example.com/x?k=1"}}}, alerts.alert_config())
        class Opener:
            def open(self, req, timeout):
                return fake(req, timeout)
        real, alerts.urllib.request.build_opener = alerts.urllib.request.build_opener, lambda *h: Opener()
        try:
            for c in alerts.CHANNELS:
                alerts.send_channel(cfg, c, "failsafe", alerts.SAMPLE)
        finally:
            alerts.urllib.request.build_opener = real
        (nu, nh, nb), (gu, gh, gb), (wu, wh, wb) = sent
        self.assertEqual((nu, nb["topic"], nb["priority"], nh["Authorization"]), ("https://ntfy.example.com/sub/", "fans", 4, "Bearer tk_1"))
        self.assertEqual(nb["title"], "Rack A: failsafe")
        self.assertIn("CPU 71°C", nb["message"])
        self.assertEqual((gu, gh["X-gotify-key"], gb["priority"]), ("https://gotify.example.com/message", "Aabc", 6))
        self.assertEqual((wu, wb["event"], wb["level"], wb["server"]), ("https://hooks.example.com/x?k=1", "failsafe", "warn", "Rack A"))


class Reports(unittest.TestCase):
    def test_sparkline(self):
        self.assertEqual(alerts.sparkline([1, 2, 3, 4, 5, 6, 7, 8]), "▁▂▃▄▅▆▇█")
        self.assertEqual(alerts.sparkline([5, 5, 5]), "▁▁▁")
        self.assertEqual(alerts.sparkline([]), "")
        self.assertEqual(len(alerts.sparkline(range(500))), 24)

    def test_report_and_edit_in_place(self):
        servers = {k: SERVERS[k] for k in ("rack-a", "rack-b")}
        for s in servers.values():
            s.cycle()
        cfg = alerts.validate_alerts({"webhook_url": HOOK, "events": {"report": {"enabled": True}},
                                      "report_minutes": 60, "report_mode": "edit"}, alerts.alert_config())
        alerts.save_alerts(cfg)
        rep = alerts.build_report(alerts.alert_config(), servers)
        self.assertEqual(len(rep["embeds"]), 2)
        self.assertEqual(rep["embeds"][0]["title"], "Rack A: status")
        self.assertLess(len(json.dumps(rep)), 6000)

        calls = []

        def fake_post(url, payload, method="POST"):
            calls.append((method, url.rsplit("/", 2)[-1]))
            if method == "PATCH" and len(calls) > 2:
                raise urllib.error.HTTPError(url, 404, "Unknown Message", {}, None)
            return {"id": f"m{len(calls)}"}

        real, alerts.post_webhook = alerts.post_webhook, fake_post
        try:
            alerts.REPORT_STATE.unlink(missing_ok=True)
            alerts.send_report(servers)
            alerts.send_report(servers)               # not due yet: nothing sent
            alerts.send_report(servers, force=True)   # due: the same message is edited
            alerts.send_report(servers, force=True)   # deleted in Discord: a new message is posted
            self.assertEqual(calls, [("POST", "abc-DEF_123"), ("PATCH", "m1"), ("PATCH", "m1"), ("POST", "abc-DEF_123")])
            alerts.save_alerts(alerts.validate_alerts({"report_mode": "post"}, alerts.alert_config()))
            calls.clear()
            alerts.send_report(servers, force=True)
            self.assertEqual(calls, [("POST", "abc-DEF_123")])
        finally:
            alerts.post_webhook = real
            alerts.save_alerts(alerts.validate_alerts({"webhook_url": "", "events": {"report": {"enabled": False}}},
                                                      alerts.alert_config()))
