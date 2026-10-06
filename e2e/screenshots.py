"""The README's screenshots and the repository's social preview, taken from a running container with
three demo servers (see .github/workflows/screenshots.yml). Run by hand with:

    FANCTL_URL=http://localhost:8080 FANCTL_PASSWORD=... python e2e/screenshots.py

They are regenerated rather than edited, so they always show the dashboard as it is."""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = os.environ.get("FANCTL_URL", "http://localhost:8080")
PASSWORD = os.environ.get("FANCTL_PASSWORD", "")
DOCS = Path(__file__).parent.parent / "docs"
JPEG = {"type": "jpeg", "quality": 86}


def signed_in(browser, **context):
    ctx = browser.new_context(color_scheme="dark", bypass_csp=True, **context)
    page = ctx.new_page()
    page.goto(URL + "/login")
    page.evaluate("localStorage.setItem('fanctl-prefs', JSON.stringify({lang: 'en', unit: 'C'}))")
    page.fill("input[type=password]", PASSWORD)
    page.keyboard.press("Enter")
    page.wait_for_url(URL + "/")
    page.wait_for_selector(".fleet .card:not(.add)")
    return ctx, page


def settle(page, ms=1200):
    page.wait_for_timeout(ms)  # charts and sparklines draw after the data arrives


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx, page = signed_in(browser, viewport={"width": 1440, "height": 900})
        post = """([url, body]) => fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                             body: JSON.stringify(body)}).then(r => r.status)"""
        # three servers doing three different things
        page.evaluate(post, ["/api/settings?server=r720", {"mode": "smart", "smart_target": 58}])
        page.evaluate(post, ["/api/settings?server=r620", {"mode": "fixed", "fixed_speed": 30}])
        page.wait_for_timeout(6000)

        page.goto(URL + "/#/")
        settle(page)
        page.screenshot(path=DOCS / "overview.jpg", clip={"x": 0, "y": 0, "width": 1440, "height": 660}, **JPEG)

        page.goto(URL + "/#/server/r420")
        page.wait_for_function("document.querySelector('#v-cpu')?.textContent.match(/\\d/)")
        settle(page)
        page.screenshot(path=DOCS / "server.jpg", **JPEG)

        body = page.locator("#control-body")
        body.scroll_into_view_if_needed()
        settle(page, 400)
        box = body.bounding_box()  # the panel is narrow; the column around it is not
        page.screenshot(path=DOCS / "control.jpg", full_page=True, **JPEG,
                        clip={"x": box["x"] - 16, "y": box["y"] - 16, "width": 600, "height": box["height"] + 32})

        page.goto(URL + "/#/alerts")
        page.wait_for_selector("#v-alerts:not([hidden])")
        settle(page)
        page.screenshot(path=DOCS / "alerts.jpg", **JPEG)
        ctx.close()

        ctx, page = signed_in(browser, viewport={"width": 390, "height": 844}, device_scale_factor=2,
                              is_mobile=True, has_touch=True)
        page.goto(URL + "/#/server/r720")
        page.wait_for_function("document.querySelector('#v-cpu')?.textContent.match(/\\d/)")
        settle(page)
        page.screenshot(path=DOCS / "phone.jpg", **JPEG)
        ctx.close()

        ctx, page = signed_in(browser, viewport={"width": 420, "height": 230}, device_scale_factor=2)
        page.goto(URL + "/embed?server=r720&theme=dark&bg=solid")
        page.wait_for_selector(".emb .spark")
        settle(page)
        page.screenshot(path=DOCS / "embed.jpg", **JPEG)
        ctx.close()

        # the card GitHub shows when the repository is shared: 1280 x 640
        card = browser.new_page(viewport={"width": 1280, "height": 640})
        card.goto((DOCS / "social" / "preview.html").as_uri())
        card.wait_for_load_state("networkidle")
        card.screenshot(path=DOCS / "social-preview.png")
        browser.close()
    print("screenshots written to", DOCS)


if __name__ == "__main__":
    main()
