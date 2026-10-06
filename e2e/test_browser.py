"""The dashboard in a real browser (Chromium through Playwright), against a running container with a
demo server. CI runs these after building the image:

    FANCTL_URL=http://localhost:8080 FANCTL_PASSWORD=... FANCTL_VIEWER=... python -m unittest discover -s e2e

What they check is what the unit tests cannot: that the pages load without a script error, that the
controls work by mouse, keyboard and touch-sized screens, that Portuguese is complete on screen, and
that no page has a serious accessibility problem (axe-core, WCAG 2 AA)."""
import json
import os
import unittest
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

URL = os.environ.get("FANCTL_URL", "http://localhost:8080")
PASSWORD = os.environ.get("FANCTL_PASSWORD", "")
VIEWER = os.environ.get("FANCTL_VIEWER", "")
AXE = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.12.0/axe.min.js"
SERVER = "demo-server"


class Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def page(self, lang="en", **context):
        # the app's CSP (script-src 'self') would also stop the test's own scripts: axe, and the
        # expressions Playwright evaluates. The tests bypass it; the app keeps it.
        ctx = self.browser.new_context(bypass_csp=True, **context)
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and "401" not in m.text and self.errors.append(m.text))
        page.goto(URL + "/login")
        page.evaluate(f"localStorage.setItem('fanctl-prefs', JSON.stringify({{lang: '{lang}', unit: 'C'}}))")
        page.fill("input[type=password]", PASSWORD)
        page.keyboard.press("Enter")
        page.wait_for_url(URL + "/")
        page.wait_for_selector(".fleet .card:not(.add)")
        return page

    def tearDown(self):
        if getattr(self, "errors", []):
            self.fail("the page logged errors:" + "".join("\n  " + e for e in self.errors))

    def axe(self, page, label):
        page.add_script_tag(url=AXE)
        result = page.evaluate("axe.run(document, {runOnly: ['wcag2a', 'wcag2aa']})")
        serious = [f"{v['id']}: {v['help']} ({len(v['nodes'])}×, e.g. {v['nodes'][0]['target']})"
                   for v in result["violations"] if v["impact"] in ("serious", "critical")]
        if serious:
            self.fail(f"accessibility problems on {label}:" + "".join("\n  " + v for v in serious))

    # ---- pages

    def test_overview_and_server_page(self):
        page = self.page()
        self.assertIn("Demo", page.inner_text(".fleet"))
        page.goto(f"{URL}/#/server/{SERVER}")
        page.wait_for_function("document.querySelector('#v-cpu')?.textContent.match(/\\d/)")
        self.assertTrue(page.is_visible("#chart"))
        self.axe(page, "the server page")

    def test_every_page_loads_cleanly(self):
        page = self.page()
        for view in ("alerts", "prometheus", "grafana", "homarr", "backup", "add"):
            with self.subTest(view=view):
                page.goto(f"{URL}/#/{view}")
                page.wait_for_selector(f"#v-{'edit' if view == 'add' else view}:not([hidden])")
                self.axe(page, view)

    # ---- controls

    def test_curve_point_edited_by_field_and_keyboard(self):
        page = self.page()
        page.goto(f"{URL}/#/server/{SERVER}")
        page.click('.seg button[data-mode="curve"]')
        speed = page.locator('#curve-points .cp input[data-k="1"]').nth(1)
        speed.fill("33")
        speed.dispatch_event("change")
        self.assertEqual(page.evaluate("draft.curve[1][1]"), 33)
        page.evaluate("""document.querySelector('#curve .pt[data-i="1"]').focus()""")
        self.assertEqual(page.evaluate("document.activeElement.getAttribute('data-i')"), "1",
                         page.evaluate("document.activeElement.outerHTML.slice(0, 200)"))
        page.keyboard.press("Shift+ArrowUp")
        self.assertEqual(page.evaluate("draft.curve[1][1]"), 38)
        self.assertEqual(page.evaluate("document.activeElement.getAttribute('data-i')"), "1")  # focus stays on the point
        page.click("#save")
        page.wait_for_selector(".toast.show")
        page.reload()
        page.wait_for_function("typeof draft !== 'undefined' && draft && draft.curve")
        self.assertEqual(page.evaluate("draft.curve[1][1]"), 38)

    def test_reducing_cooling_asks_first(self):
        page = self.page()
        page.goto(f"{URL}/#/server/{SERVER}")
        page.wait_for_function("typeof draft !== 'undefined' && draft")
        asked = []
        page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
        page.click("#advanced summary")
        page.click('#bmc-thr button[data-thr="false"]')
        page.click("#save")
        self.assertTrue(asked and "reduce cooling" in asked[0])
        self.assertTrue(page.evaluate("dirty"))  # dismissed: nothing applied
        page.click("#discard")

    def test_out_of_range_is_said_not_swallowed(self):
        page = self.page()
        page.goto(f"{URL}/#/server/{SERVER}")
        page.fill("#failsafe", "120")
        self.assertEqual(page.get_attribute("#failsafe", "aria-invalid"), "true")
        self.assertIn("40", page.inner_text("#failsafe >> xpath=ancestor::div[contains(@class,'f')] >> .field-error"))

    # ---- language, phones, widgets, accounts

    def test_portuguese_on_screen(self):
        page = self.page(lang="pt")
        self.assertIn("Visão geral", page.inner_text(".side"))
        page.goto(f"{URL}/#/prometheus")
        page.wait_for_selector("#v-prometheus:not([hidden])")
        self.assertIn("Todas as séries têm as etiquetas", page.inner_text("#v-prometheus"))  # a whole sentence
        page.goto(f"{URL}/#/server/{SERVER}")
        page.wait_for_selector("#control-body")
        text = page.inner_text("#control-body")
        for english in ("Minimum speed", "Quiet hours", "CPU failsafe"):
            self.assertNotIn(english, text)

    def test_phone_layout(self):
        page = self.page(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        page.goto(f"{URL}/#/server/{SERVER}")
        page.wait_for_function("document.querySelector('#v-cpu')?.textContent.match(/\\d/)")
        self.assertLess(page.evaluate("document.querySelector('.side').offsetHeight"), 200)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 375)  # nothing sideways
        small = page.evaluate("""[...document.querySelectorAll('#control-body button, .actions button')].filter(b => b.offsetParent)
            .filter(b => b.getBoundingClientRect().height < 39).map(b => b.textContent + ' ' + Math.round(b.getBoundingClientRect().height))""")
        self.assertEqual(small, [], "touch targets under 40 px")  # names of the buttons that are too small

    def test_widget_with_a_token(self):
        page = self.page()
        token = page.evaluate("""fetch('/api/tokens', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: 'e2e', kind: 'widget'})}).then(r => r.json())""")
        try:
            anon = self.browser.new_context(bypass_csp=True)
            self.addCleanup(anon.close)
            w = anon.new_page()
            w.goto(f"{URL}/embed?server={SERVER}&token={token['token']}")
            w.wait_for_selector(".emb .nums")
            self.assertIn("CPU", w.inner_text(".emb"))
            req = urllib.request.Request(f"{URL}/api/state?server={SERVER}&token={token['token']}")
            with self.assertRaises(urllib.error.HTTPError):  # a widget token reads widgets, nothing else
                urllib.request.urlopen(req)
        finally:
            page.evaluate(f"""fetch('/api/tokens/revoke', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{id: '{token['id']}'}})}})""")

    @unittest.skipUnless(VIEWER, "needs FANCTL_VIEWER")
    def test_viewer_cannot_change_anything(self):
        ctx = self.browser.new_context(bypass_csp=True)
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        page.goto(URL + "/login")
        page.fill("input[type=password]", VIEWER)
        page.keyboard.press("Enter")
        page.wait_for_url(URL + "/")
        page.goto(f"{URL}/#/server/{SERVER}")
        page.wait_for_selector("body.viewer")
        self.assertFalse(page.is_visible("#s-edit"))
        status = page.evaluate("""fetch('/api/settings?server=demo-server', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: '{"mode": "auto"}'}).then(r => r.status)""")
        self.assertEqual(status, 403)
        self.errors = []


if __name__ == "__main__":
    print(json.dumps({"url": URL}))
    unittest.main()
