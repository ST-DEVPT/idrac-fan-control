import http.client
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from fanctl import config, server, web
from fanctl.server import SERVERS


class Sessions(unittest.TestCase):
    def test_tokens(self):
        key = web.session_key()
        self.assertEqual(web.valid_token(key, web.make_token(key, 60)), "admin")
        self.assertEqual(web.valid_token(key, web.make_token(key, 60, "viewer")), "viewer")
        forged = web.make_token(key, 60, "viewer").replace(".viewer.", ".admin.")
        self.assertIsNone(web.valid_token(key, forged))                            # role can't be edited
        self.assertFalse(web.valid_token(key, web.make_token(key, -1)))              # expired
        self.assertFalse(web.valid_token(key, web.make_token(b"other", 60)))         # wrong key
        self.assertFalse(web.valid_token(key, ""))
        self.assertFalse(web.valid_token(key, f"{int(time.time()) + 999}.deadbeef"))  # forged expiry
        self.assertFalse(web.valid_token(key, "x.é"))                                # non-ASCII cookie

    def test_new_password_signs_everyone_out(self):
        key = web.session_key()
        real, config.WEB_PASSWORD = config.WEB_PASSWORD, "changed"
        try:
            self.assertNotEqual(web.session_key(), key)
        finally:
            config.WEB_PASSWORD = real


class Base(unittest.TestCase):
    """Starts the dashboard on a free port and signs in as admin."""
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        for s in SERVERS.values():
            s.cycle()
        st, h, _ = cls.req("POST", "/api/login", {"password": "hunter2"})
        cls.cookie = {"Cookie": h["Set-Cookie"].split(";")[0]}

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    @classmethod
    def req(cls, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", cls.httpd.server_address[1], timeout=10)
        c.request(method, path, None if body is None else json.dumps(body),
                  {"Content-Type": "application/json", **(headers or {})})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def get(self, path, **kw):
        return self.req("GET", path, headers={**self.cookie, **kw.get("headers", {})})

    def post(self, path, body=None, **kw):
        return self.req("POST", path, {} if body is None else body, {**self.cookie, **kw.get("headers", {})})



class HTTP(Base):
    # ---- pages and headers

    def test_pages_need_sign_in(self):
        st, h, _ = self.req("GET", "/")
        self.assertEqual((st, h["Location"]), (303, "/login"))
        self.assertEqual(self.req("GET", "/api/state")[0], 401)
        self.assertEqual(self.req("GET", "/healthz")[0], 200)

    def test_security_headers(self):
        st, h, _ = self.req("GET", "/login")
        self.assertEqual(st, 200)
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])
        self.assertIn("script-src 'self';", h["Content-Security-Policy"])
        self.assertEqual(h["X-Frame-Options"], "DENY")

    def test_static_allowlist_and_etag(self):
        self.assertEqual(self.req("GET", "/static/style.css")[0], 200)
        for path in ("/static/../app.py", "/static/%2e%2e/app.py", "/static/fonts/../../app.py"):
            self.assertIn(self.req("GET", path)[0], (401, 404))
        etag = self.req("GET", "/static/app.js")[1]["ETag"]
        self.assertEqual(self.req("GET", "/static/app.js", headers={"If-None-Match": etag})[0], 304)

    # ---- sign-in

    def test_sign_in(self):
        t0 = time.time()
        self.assertEqual(self.req("POST", "/api/login", {"password": "nope"})[0], 401)
        self.assertGreaterEqual(time.time() - t0, 1)  # failures are slowed down
        self.assertEqual(self.req("POST", "/api/login", {"password": "hunter2"}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.req("POST", "/api/login", {"password": "hunter2"}, {"Origin": "http://evil.example"})[0], 403)
        st, h, _ = self.req("POST", "/api/login", {"password": "hunter2"})
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertIn("SameSite=Strict", h["Set-Cookie"])
        self.assertNotIn("Secure", h["Set-Cookie"])
        h = self.req("POST", "/api/login", {"password": "hunter2"}, {"X-Forwarded-Proto": "https"})[1]
        self.assertIn("Secure", h["Set-Cookie"])
        st, h, _ = self.req("POST", "/api/logout", {})
        self.assertIn("Max-Age=0", h["Set-Cookie"])

    # ---- server state and settings

    def test_state(self):
        st, _, data = self.get("/api/state?server=rack-b")
        d = json.loads(data)
        self.assertEqual((st, d["id"], d["driver"], d["control"]), (200, "rack-b", "demo", True))
        self.assertNotIn("password", data.decode().lower())
        self.assertEqual(self.get("/api/state?server=nope")[0], 404)

    def test_settings(self):
        self.assertEqual(self.post("/api/settings?server=rack-b", {"mode": "fixed", "fixed_speed": 33})[0], 200)
        self.assertEqual(SERVERS["rack-b"].settings()["fixed_speed"], 33)
        self.assertEqual(SERVERS["rack-a"].settings()["fixed_speed"], 20)  # other server untouched
        self.assertEqual(self.post("/api/settings?server=rack-b", {"fixed_speed": 500})[0], 400)
        self.assertEqual(self.req("POST", "/api/settings", {"mode": "auto"})[0], 401)
        self.assertEqual(self.post("/api/settings?server=rack-b", {"x": "y" * 20000})[0], 413)

    # ---- alerts

    def test_alerts(self):
        hook = "https://discord.com/api/webhooks/123456789/abc-DEF_123"
        self.assertEqual(self.req("GET", "/api/alerts?token=e-token")[0], 401)  # the embed token can't read alerts
        st, _, data = self.post("/api/alerts", {"webhook_url": hook, "cooldown_minutes": 5})
        self.assertEqual(st, 200)
        self.assertNotIn(hook, data.decode())
        self.assertEqual(self.post("/api/alerts", {"webhook_url": "https://evil.example/x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/alerts", {"cooldown_minutes": 1})[0], 401)
        self.assertEqual(self.post("/api/test-alert", {"kind": "nope"})[0], 400)
        self.post("/api/alerts", {"webhook_url": "", "cooldown_minutes": 0})
        self.assertEqual(self.post("/api/test-alert", {})[0], 400)  # no webhook configured

    # ---- read-only tokens

    def test_embed_token_is_read_only(self):
        self.assertEqual(self.req("GET", "/embed")[0], 401)
        st, h, _ = self.req("GET", "/embed?token=e-token")
        self.assertEqual(st, 200)
        self.assertIn("frame-ancestors *", h["Content-Security-Policy"])
        self.assertNotIn("X-Frame-Options", h)
        self.assertEqual(self.req("GET", "/api/state?server=rack-a&token=e-token")[0], 200)
        self.assertEqual(self.req("GET", "/api/state?token=wrong")[0], 401)
        self.assertEqual(self.req("POST", "/api/settings?token=e-token", {"mode": "auto"})[0], 401)
        self.assertEqual(self.req("GET", "/api/overview?token=e-token")[0], 401)

    def test_metrics(self):
        self.assertEqual(self.req("GET", "/metrics")[0], 401)
        st, _, data = self.req("GET", "/metrics", headers={"Authorization": "Bearer m-token"})
        text = data.decode()
        self.assertEqual(st, 200)
        self.assertRegex(text, r'_up\{server="rack-a",name="Rack A",driver="demo"\} 1')
        self.assertRegex(text, r'_fan_rpm\{server="rack-a",name="Rack A",driver="demo",fan="Fan1"\}')
        self.assertNotIn("e+", text)

    def test_integrations(self):
        st, _, data = self.get("/api/integrations")
        ig = json.loads(data)
        self.assertTrue(ig["metrics_token"] and ig["embed_token"])
        self.assertNotIn("m-token", data.decode())
        self.assertNotIn("e-token", data.decode())
        self.assertEqual(self.req("GET", "/api/integrations?token=e-token")[0], 401)
        st, _, data = self.get("/api/metrics-preview")
        self.assertIn(b"_up{", data)
        self.assertEqual(self.req("GET", "/api/metrics-preview")[0], 401)
        self.assertEqual(self.req("GET", "/static/grafana.json")[0], 200)

    # ---- servers managed in the dashboard

    def test_overview(self):
        st, _, data = self.get("/api/overview")
        ov = json.loads(data)
        self.assertEqual([x["id"] for x in ov["servers"]][:2], ["rack-a", "rack-b"])
        self.assertEqual({x["kind"] for x in ov["drivers"]}, {"dell", "supermicro", "ilo4-unlocked", "redfish", "ipmi", "demo"})
        self.assertIn("spark", ov["servers"][0])

    def test_server_lifecycle(self):
        st, _, data = self.post("/api/servers/test", {"name": "T", "driver": "demo"})
        self.assertEqual((json.loads(data)["ok"], json.loads(data)["fans"]), (True, 6))
        self.assertEqual(self.post("/api/servers/test", {"name": "T", "driver": "redfish", "host": "nope..",
                                                         "username": "u", "password": "p"})[0], 400)
        st, _, data = self.post("/api/servers", {"name": "Lab box", "driver": "demo"})
        self.assertEqual((st, json.loads(data)["id"]), (201, "lab-box"))
        self.assertEqual(json.loads(server.SERVERS_FILE.read_text())[0]["name"], "Lab box")
        self.assertEqual(json.loads(self.post("/api/servers", {"name": "Lab box", "driver": "demo"})[2])["id"], "lab-box-2")
        st, _, _ = self.post("/api/servers/lab-box", {"name": "Lab box", "driver": "redfish", "host": "10.9.9.9",
                                                      "username": "admin", "password": "s3cret"})
        self.assertEqual((st, SERVERS["lab-box"].driver.kind), (200, "redfish"))
        st, _, data = self.get("/api/servers/lab-box")
        self.assertTrue(json.loads(data)["has_password"])
        self.assertNotIn("s3cret", data.decode())
        self.assertEqual(self.post("/api/servers/rack-a", {"name": "x"})[0], 409)  # environment servers are read-only
        self.assertEqual(self.post("/api/servers/lab-box/delete")[0], 200)
        self.assertNotIn("lab-box", SERVERS)
        self.assertEqual(self.post("/api/servers/lab-box-2/delete")[0], 200)
        self.assertEqual(self.req("POST", "/api/servers", {"name": "x", "driver": "demo"})[0], 401)
        self.assertEqual(json.loads(server.SERVERS_FILE.read_text()), [])


class Roles(Base):
    """A read-only account sees everything and changes nothing."""

    def test_viewer(self):
        st, h, data = self.req("POST", "/api/login", {"password": "lookonly"})
        self.assertEqual(json.loads(data)["role"], "viewer")
        viewer = {"Cookie": h["Set-Cookie"].split(";")[0]}
        st, _, data = self.req("GET", "/api/overview", headers=viewer)
        self.assertEqual((st, json.loads(data)["role"]), (200, "viewer"))
        self.assertEqual(self.req("GET", "/api/state?server=rack-a", headers=viewer)[0], 200)
        self.assertEqual(self.req("POST", "/api/settings?server=rack-a", {"mode": "auto"}, viewer)[0], 403)
        self.assertEqual(self.req("POST", "/api/servers", {"name": "x", "driver": "demo"}, viewer)[0], 403)
        self.assertEqual(self.req("POST", "/api/alerts", {"enabled": False}, viewer)[0], 403)
        self.assertEqual(json.loads(self.get("/api/overview")[2])["role"], "admin")

    def test_changes_are_attributed(self):
        self.post("/api/settings?server=rack-a", {"ramp_down_seconds": 30})
        self.assertRegex(SERVERS["rack-a"].events[0]["msg"], r"Settings saved by admin at 127\.0\.0\.1")
        self.post("/api/settings?server=rack-a", {"ramp_down_seconds": 60})

    def test_throttling_is_per_address(self):
        now = time.time()
        web.failures.clear()
        for _ in range(web.LOGIN_ATTEMPTS):
            web.failures.setdefault("10.0.0.66", []).append(now)
        self.assertGreater(web.throttled("10.0.0.66"), 0)
        self.assertEqual(web.throttled("10.0.0.67"), 0)                  # someone else can still sign in
        self.assertEqual(web.throttled("10.0.0.66", now + web.LOGIN_WINDOW + 1), 0)
        web.failures["127.0.0.1"] = [now] * web.LOGIN_ATTEMPTS
        st, h, _ = self.req("POST", "/api/login", {"password": "hunter2"})
        self.assertEqual(st, 429)
        self.assertIn("Retry-After", h)
        web.failures.clear()

    def test_forwarded_for_needs_trust_proxy(self):
        web.failures.clear()
        self.req("POST", "/api/login", {"password": "nope"}, {"X-Forwarded-For": "203.0.113.9"})
        self.assertIn("127.0.0.1", web.failures)                         # the header was not believed
        web.failures.clear()
        config.TRUST_PROXY = True
        try:
            self.req("POST", "/api/login", {"password": "nope"}, {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
            self.assertIn("203.0.113.9", web.failures)
        finally:
            config.TRUST_PROXY = False
            web.failures.clear()


class Backup(Base):
    def test_export_and_import(self):
        self.post("/api/servers", {"name": "Backed up", "driver": "redfish", "host": "10.1.1.1",
                                   "username": "admin", "password": "pw-1"})
        st, h, data = self.get("/api/export")
        self.assertEqual(st, 200)
        self.assertIn("attachment", h["Content-Disposition"])
        plain = json.loads(data)
        self.assertNotIn("pw-1", data.decode())
        self.assertIn("rack-a", plain["settings"])
        self.assertEqual([s["id"] for s in plain["servers"]], ["backed-up"])  # environment servers left out
        with_secrets = json.loads(self.get("/api/export?secrets=1")[2])
        self.assertEqual(with_secrets["servers"][0]["password"], "pw-1")

        self.post("/api/servers/backed-up/delete")
        with_secrets["settings"]["rack-b"]["fixed_speed"] = 44
        st, _, data = self.post("/api/import", {"data": with_secrets})
        report = json.loads(data)
        self.assertEqual(report["added"], ["Backed up"])
        self.assertIn("Rack B", report["updated"])
        self.assertEqual(SERVERS["rack-b"].settings()["fixed_speed"], 44)
        self.assertEqual(SERVERS["backed-up"].cfg["password"], "pw-1")
        # without passwords, a missing server can't be added and says why
        self.post("/api/servers/backed-up/delete")
        report = json.loads(self.post("/api/import", {"data": plain})[2])
        self.assertEqual(report["added"], [])
        self.assertIn("password", report["skipped"][0])
        self.assertEqual(self.post("/api/import", {"data": {"servers": []}})[0], 400)

    def test_viewer_cannot_export(self):
        st, h, _ = self.req("POST", "/api/login", {"password": "lookonly"})
        viewer = {"Cookie": h["Set-Cookie"].split(";")[0]}
        self.assertEqual(self.req("GET", "/api/export", headers=viewer)[0], 403)
