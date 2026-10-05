"""Tells the dashboard when a newer release is out. Asks GitHub's API twice a day for the latest
release of this project; nothing is sent but the request itself. UPDATE_CHECK=false turns it off."""

import json
import os
import re
import time
import urllib.request

from .config import VERSION

RELEASES = "https://api.github.com/repos/ST-DEVPT/rack-fan-control/releases/latest"
ENABLED = os.environ.get("UPDATE_CHECK", "true").lower() not in ("0", "false", "no")
latest = {}  # {"version": "2.3.0", "url": "https://github.com/..."} once known


def parse(version):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(version).strip())
    return tuple(map(int, m.groups())) if m else None


def newer(candidate, current=VERSION):
    """True when candidate is a later release than current; never for a development build."""
    a, b = parse(candidate), parse(current)
    return bool(a and b and a > b)


def check():
    req = urllib.request.Request(RELEASES, headers={"Accept": "application/vnd.github+json",
                                                    "User-Agent": f"fan-control/{VERSION}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read(200_000))
    tag, url = data.get("tag_name", ""), data.get("html_url", "")
    if parse(tag) and url.startswith("https://github.com/"):
        latest.update(version=tag.lstrip("v"), url=url)


def loop():
    if not ENABLED or not parse(VERSION):  # development builds have nothing to compare
        return
    time.sleep(60)  # not while the controllers are starting
    while True:
        try:
            check()
        except Exception as e:  # offline, rate limited: try again next time
            print("update check failed:", e, flush=True)
        time.sleep(12 * 3600)


def available():
    """The newer release, or None."""
    return dict(latest) if latest and newer(latest["version"]) else None
