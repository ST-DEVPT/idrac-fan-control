"""Every piece of text the pages show has a Portuguese translation. i18n.js translates text nodes and
the placeholder, title and aria-label attributes one by one, as written in the HTML; this reads the
pages the same way, and fails on any text that neither the dictionary nor a pattern covers."""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

WEB = Path(__file__).parent.parent / "web"
SKIP = {"script", "style", "pre", "code", "textarea"}
ATTRS = ("placeholder", "title", "aria-label")
# names and units, the same in both languages
SAME = {"Fan Control", "Homarr", "Grafana", "Prometheus", "Discord", "ntfy", "Gotify", "Smart", "CPU", "IPMI",
        "Redfish", "iDRAC", "iLO", "BMC", "EN", "PT", "°C", "°F", "%", "W", "s", "min", "rpm", "k rpm", "Auto",
        "@here", "@everyone", "iFrame", "Webhook", "JSON", "Bearer", "Supermicro", "Dell", "HPE", "max", "URL",
        "RPM", "UP", "fan-control", "Rack A", "tk_…",
        # menu names in Homarr, Grafana and Discord, which the reader looks for in English
        "Add board content", "Management → Custom Widgets", "Dashboards → New → Import", "Developer Mode → Copy ID"}


def dictionary():
    src = (WEB / "i18n.js").read_text(encoding="utf-8")
    body = src[src.index("const PT = {"):src.index("const PT_PATTERNS")]
    keys = {bytes(k, "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
            for k in re.findall(r'"((?:[^"\\]|\\.)+)":', body)}
    pats = src[src.index("const PT_PATTERNS"):src.index("function localize")]
    patterns = [re.compile(p.replace(r"\/", "/")) for p in re.findall(r"\[/(.+?)/,\s", pats)]
    return keys, patterns


class Texts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.found = 0, set()

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ATTRS and v:
                self.found.add(v)
        if tag in SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.found.add(data)


def needs(text):
    t = re.sub(r"\s+", " ", text).strip()
    technical = t.startswith(("http://", "https://")) or re.fullmatch(r"[\w.-]+\.(json|yml|js)", t)
    return t if re.search(r"[A-Za-z]{2}", t) and t not in SAME and not technical else None


class Portuguese(unittest.TestCase):
    def test_every_page_text_is_translated(self):
        keys, patterns = dictionary()
        missing = {}
        for page in ("index.html", "login.html", "embed.html"):
            p = Texts()
            p.feed((WEB / page).read_text(encoding="utf-8"))
            for text in filter(None, map(needs, p.found)):
                if text not in keys and not any(r.search(text) for r in patterns):
                    missing.setdefault(page, []).append(text)
        self.assertEqual(missing, {}, "add these to PT in web/i18n.js")

    def test_no_key_twice(self):
        """A key written twice silently keeps only its last translation."""
        src = (WEB / "i18n.js").read_text(encoding="utf-8")
        body = src[src.index("const PT = {"):src.index("const PT_PATTERNS")]
        keys = re.findall(r'"((?:[^"\\]|\\.)+)":', body)
        self.assertEqual(sorted({k for k in keys if keys.count(k) > 1}), [])

    def test_strings_in_scripts_are_translated(self):
        """Literal strings handed to translate() and toast() in the scripts."""
        keys, patterns = dictionary()
        missing = []
        for js in ("app.js", "editor.js", "alerts.js", "integrations.js", "embed.js"):
            src = (WEB / js).read_text(encoding="utf-8")
            for text in re.findall(r'(?:translate|toast)\("((?:[^"\\]|\\.)+)"', src):
                probe = text + "x" if text.endswith(": ") else text  # "Could not save: " + the error
                if needs(text) and text not in keys and not any(r.search(probe) for r in patterns):
                    missing.append(f"{js}: {text}")
        self.assertEqual(missing, [])
