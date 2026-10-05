"""Alerts (Discord, ntfy, Gotify, any webhook) and Discord status reports."""

import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .config import DATA_DIR, DISCORD_WEBHOOK, INTERVAL, VERSION, read_json, write_json
from .control import speed_text
from .drivers import SameOriginRedirect

# ---------------------------------------------------------------- alerts

log = logging.getLogger("fanctl.alerts")
ALERTS_FILE = DATA_DIR / "alerts.json"
WEBHOOK_RE = re.compile(r"https://(?:(?:ptb|canary)\.)?(?:discord|discordapp)\.com/api/webhooks/\d+/[\w-]+")
LEVELS = ("error", "warn", "ok", "info")
CHANNELS = ("ntfy", "gotify", "webhook")  # besides Discord; they get the same events as plain text

# kind: (level, default title, default message). {placeholders} are filled per alert.
ALERT_KINDS = {
    "failsafe":         ("warn",  "{server}: failsafe",            "CPU at {cpu}°C reached the {failsafe}°C failsafe. The BMC took over the fans."),
    "failsafe_cleared": ("ok",    "{server}: failsafe cleared",    "CPU back to {cpu}°C. Manual fan control resumed."),
    "hot":              ("warn",  "{server}: running hot",         "CPU at {cpu}°C, above the {threshold}°C warning. Fans at {speed}."),
    "unreachable":      ("error", "{server}: BMC unreachable",     "{error}"),
    "refused":          ("error", "{server}: fan command refused", "{error}\nThe BMC keeps control of the fans."),
    "recovered":        ("ok",    "{server}: back to normal",      "The BMC is answering and accepting fan commands again."),
    "controller_error": ("error", "{server}: controller error",    "{error}\nFans handed back to the BMC."),
    "failsafe_long":    ("error", "{server}: failsafe for {minutes} min", "The BMC has held the fans for {minutes} minutes and the CPU is at {cpu}°C. Something keeps it hot."),
    "fan_failed":       ("error", "{server}: fan {fan} failed",    "{fan} reads {rpm} while the other fans spin. Check it before the next hot day."),
    "ignored":          ("error", "{server}: fan commands ignored", "Fans were set to {speed} but their RPM did not follow. The BMC may have reset or locked its fans."),
    "inlet_hot":        ("warn",  "{server}: room running hot",    "Inlet air at {inlet}°C, above {inlet_threshold}°C. No fan speed cools the room."),
    "settings_changed": ("info",  "{server}: settings changed",    "Mode {mode}, failsafe {failsafe}°C."),
    "started":          ("info",  "{server}: controller started",  "Watching {host} every {interval} s."),
    "report":           ("info",  "{server}: status",              "{model} · last {period}"),
}
REPORT_PLACEHOLDERS = ("period", "cpu_min", "cpu_avg", "cpu_max", "speed_avg", "power_avg", "dell_pct")
PLACEHOLDERS = ("server", "host", "model", "cpu", "speed", "mode", "reason", "error", "failsafe", "threshold", "interval", "time",
                "inlet", "inlet_threshold", "minutes", "fan", "rpm")
SAMPLE = {"server": "Rack A", "host": "192.168.1.120", "model": "PowerEdge R730", "cpu": "71", "speed": "45%",
          "mode": "curve", "reason": "curve at 71°C", "error": "Unable to establish IPMI v2 / RMCP+ session",
          "failsafe": "75", "threshold": "68", "interval": "15", "time": "12:00:00",
          "inlet": "36", "inlet_threshold": "35", "minutes": "10", "fan": "Fan3", "rpm": "0 rpm",
          "period": "1 h", "cpu_min": "48", "cpu_avg": "55", "cpu_max": "71", "speed_avg": "24%",
          "power_avg": "152", "dell_pct": "0"}

ALERT_DEFAULTS = {
    "enabled": True,
    "webhook_url": "",       # empty: fall back to the DISCORD_WEBHOOK_URL environment variable
    "username": "Fan Control",
    "avatar_url": "",
    "footer": "Fan Control",
    "details": True,         # add CPU / fans / mode fields under the message
    "mention": "",           # "", "here", "everyone", "role:<id>", "user:<id>"
    "mention_levels": ["error"],
    "cooldown_minutes": 0,   # minimum gap between two alerts of the same kind for the same server
    "hot_threshold": 68,
    "inlet_threshold": 35,   # "room running hot" at this inlet air temperature
    "failsafe_minutes": 10,  # "failsafe for N min" once the BMC has held the fans this long
    "report_minutes": 60,    # status report period
    "report_mode": "edit",   # "edit": keep one message up to date; "post": a new message each time
    "colors": {"error": "#c0301c", "warn": "#e4501b", "ok": "#3b7a39", "info": "#4f4d48"},
    "events": {k: {"enabled": k not in ("hot", "settings_changed", "started", "report"), "title": t, "message": m}
               for k, (_, t, m) in ALERT_KINDS.items()},
    # ntfy: the topic URL (https://ntfy.sh/my-topic) and an optional access token; Gotify: the server
    # URL and an application token; webhook: any URL, which receives the alert as JSON
    "channels": {c: {"enabled": False, "url": "", "token": ""} for c in CHANNELS},
}
alert_lock = threading.Lock()
SENT = {"sent": 0, "failed": 0}  # alerts handed to a channel, and those that failed: for /metrics
last_alert = {}  # (server id, kind) -> time, for the cooldown


def alert_config():
    saved = read_json(ALERTS_FILE, {}, "alerts.json")
    if not isinstance(saved, dict):
        saved = {}
    cfg = {**ALERT_DEFAULTS, **{k: v for k, v in saved.items() if k in ALERT_DEFAULTS}}
    cfg["colors"] = {**ALERT_DEFAULTS["colors"], **saved.get("colors", {})}
    cfg["events"] = {k: {**ALERT_DEFAULTS["events"][k], **saved.get("events", {}).get(k, {})} for k in ALERT_KINDS}
    cfg["channels"] = {c: {**ALERT_DEFAULTS["channels"][c], **(saved.get("channels") or {}).get(c, {})} for c in CHANNELS}
    return cfg


def channels_of(cfg):
    return [c for c in CHANNELS if cfg["channels"][c]["enabled"] and cfg["channels"][c]["url"]]


def webhook_of(cfg):
    url = cfg["webhook_url"] or DISCORD_WEBHOOK
    return url if WEBHOOK_RE.fullmatch(url or "") else ""


def public_alert_config(cfg):
    """What the browser may see: everything except the webhook secret."""
    own, env = cfg["webhook_url"], DISCORD_WEBHOOK if WEBHOOK_RE.fullmatch(DISCORD_WEBHOOK or "") else ""
    shown = own or env
    channels = {c: {"enabled": ch["enabled"], "url_set": bool(ch["url"]), "url_hint": hint_of(ch["url"]),
                    "token_set": bool(ch["token"])} for c, ch in cfg["channels"].items()}
    return {**{k: v for k, v in cfg.items() if k not in ("webhook_url", "channels")}, "channels": channels,
            "webhook": {"set": bool(shown), "source": "dashboard" if own else "environment" if env else None,
                        "hint": "…" + shown[-4:] if shown else ""},
            "kinds": {k: lvl for k, (lvl, _, _) in ALERT_KINDS.items()}, "placeholders": PLACEHOLDERS,
            "report_placeholders": REPORT_PLACEHOLDERS,
            "defaults": {k: {"title": t, "message": m} for k, (_, t, m) in ALERT_KINDS.items()}}


def hint_of(url):
    """Enough of a URL to recognise it, not enough to use it: an ntfy topic name is its password."""
    if not url:
        return ""
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}/…{url[-4:]}" if len(u.path) > 5 else f"{u.scheme}://{u.netloc}{u.path}"


def validate_alerts(new, current):
    """Merge a partial update from the dashboard into the stored config, checking every field.
    webhook_url: absent keeps the stored one, "" clears it, anything else must be a Discord webhook."""
    cfg = json.loads(json.dumps(current))
    for key in ("enabled", "details"):
        if key in new:
            if type(new[key]) is not bool:
                raise ValueError(f"{key} must be true or false")
            cfg[key] = new[key]
    if "webhook_url" in new:
        url = str(new["webhook_url"]).strip()
        if url and not WEBHOOK_RE.fullmatch(url):
            raise ValueError("webhook URL must look like https://discord.com/api/webhooks/<id>/<token>")
        cfg["webhook_url"] = url
    for key, limit in (("username", 80), ("footer", 200)):
        if key in new:
            v = str(new[key]).strip()
            if len(v) > limit:
                raise ValueError(f"{key} is limited to {limit} characters")
            if key == "username" and ("discord" in v.lower() or not v):
                raise ValueError("bot name cannot be empty or contain \"discord\" (Discord rejects it)")
            cfg[key] = v
    if "avatar_url" in new:
        v = str(new["avatar_url"]).strip()
        if v and not re.fullmatch(r"https://[^\s\"<>]{1,500}", v):
            raise ValueError("avatar must be an https:// image URL")
        cfg["avatar_url"] = v
    if "mention" in new:
        v = str(new["mention"]).strip()
        if not re.fullmatch(r"|here|everyone|(role|user):\d{5,25}", v):
            raise ValueError("mention must be empty, here, everyone, role:<id> or user:<id>")
        cfg["mention"] = v
    if "mention_levels" in new:
        v = new["mention_levels"]
        if not (isinstance(v, list) and all(x in LEVELS for x in v)):
            raise ValueError(f"mention levels must be a list of {', '.join(LEVELS)}")
        cfg["mention_levels"] = sorted(set(v), key=LEVELS.index)
    if "cooldown_minutes" in new:
        v = new["cooldown_minutes"]
        if not (type(v) is int and 0 <= v <= 1440):
            raise ValueError("cooldown must be a whole number of minutes from 0 to 1440")
        cfg["cooldown_minutes"] = v
    if "report_minutes" in new:
        v = new["report_minutes"]
        if not (type(v) is int and 5 <= v <= 1440):
            raise ValueError("report period must be a whole number of minutes from 5 to 1440")
        cfg["report_minutes"] = v
    if "report_mode" in new:
        if new["report_mode"] not in ("edit", "post"):
            raise ValueError("report mode must be edit or post")
        cfg["report_mode"] = new["report_mode"]
    if "hot_threshold" in new:
        v = new["hot_threshold"]
        if not (type(v) in (int, float) and 30 <= v <= 100):
            raise ValueError("temperature warning must be between 30 and 100 °C")
        cfg["hot_threshold"] = v
    if "inlet_threshold" in new:
        v = new["inlet_threshold"]
        if not (type(v) in (int, float) and 15 <= v <= 60):
            raise ValueError("inlet warning must be between 15 and 60 °C")
        cfg["inlet_threshold"] = v
    if "failsafe_minutes" in new:
        v = new["failsafe_minutes"]
        if not (type(v) is int and 1 <= v <= 1440):
            raise ValueError("failsafe alert delay must be a whole number of minutes from 1 to 1440")
        cfg["failsafe_minutes"] = v
    for level, color in (new.get("colors") or {}).items():
        if level not in LEVELS or not re.fullmatch(r"#[0-9a-fA-F]{6}", str(color)):
            raise ValueError("colors must be #rrggbb for error, warn, ok and info")
        cfg["colors"][level] = color.lower()
    for c, ch in (new.get("channels") or {}).items():
        if c not in CHANNELS or not isinstance(ch, dict):
            raise ValueError(f"unknown channel {c}")
        if "enabled" in ch:
            if type(ch["enabled"]) is not bool:
                raise ValueError(f"{c} enabled must be true or false")
            cfg["channels"][c]["enabled"] = ch["enabled"]
        if "url" in ch:  # absent keeps the stored one, "" clears it
            v = str(ch["url"]).strip()
            if v and not re.fullmatch(r"https?://[^\s\"<>]{3,500}", v):
                raise ValueError(f"{c} address must be an http:// or https:// URL")
            if v and c == "ntfy" and len(urlsplit(v).path.strip("/")) == 0:
                raise ValueError("the ntfy address needs the topic, like https://ntfy.sh/my-topic")
            cfg["channels"][c]["url"] = v
        if "token" in ch:
            v = str(ch["token"]).strip()
            if len(v) > 200 or re.search(r"\s", v):
                raise ValueError(f"{c} token must be up to 200 characters, without spaces")
            cfg["channels"][c]["token"] = v
    for kind, ev in (new.get("events") or {}).items():
        if kind not in ALERT_KINDS or not isinstance(ev, dict):
            raise ValueError(f"unknown alert {kind}")
        if "enabled" in ev:
            if type(ev["enabled"]) is not bool:
                raise ValueError("event enabled must be true or false")
            cfg["events"][kind]["enabled"] = ev["enabled"]
        for key, limit in (("title", 256), ("message", 2000)):
            if key in ev:
                v = str(ev[key])
                if not v.strip() or len(v) > limit:
                    raise ValueError(f"{kind} {key} must be 1 to {limit} characters")
                cfg["events"][kind][key] = v
    return cfg


def save_alerts(cfg):
    write_json(ALERTS_FILE, cfg, private=True)  # holds the webhook and channel tokens


def fill(template, values):
    """Replace {name} placeholders. Deliberately not str.format, which would let a template
    reach object attributes."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template)


def build_payload(cfg, kind, values):
    level = ALERT_KINDS[kind][0]
    ev = cfg["events"][kind]
    embed = {"title": fill(ev["title"], values)[:256], "description": fill(ev["message"], values)[:4000],
             "color": int(cfg["colors"][level][1:], 16), "timestamp": datetime.now(timezone.utc).isoformat()}
    if cfg["footer"]:
        embed["footer"] = {"text": fill(cfg["footer"], values)[:2048]}
    if cfg["details"]:
        embed["fields"] = [{"name": n, "value": str(values.get(k) or "—")[:1024], "inline": True}
                           for n, k in (("CPU", "cpu"), ("Fans", "speed"), ("Mode", "mode"))]
        embed["fields"][0]["value"] += "°C" if values.get("cpu") not in (None, "", "—") else ""
    payload = {"username": cfg["username"], "embeds": [embed],
               "allowed_mentions": {"parse": []}}  # text from the BMC can never ping anyone
    m = cfg["mention"]
    if m and level in cfg["mention_levels"]:
        kind_, _, ident = m.partition(":")
        payload["content"] = {"here": "@here", "everyone": "@everyone",
                              "role": f"<@&{ident}>", "user": f"<@{ident}>"}[kind_]
        payload["allowed_mentions"] = ({"parse": ["everyone"]} if not ident else
                                       {"parse": [], ("roles" if kind_ == "role" else "users"): [ident]})
    if cfg["avatar_url"]:
        payload["avatar_url"] = cfg["avatar_url"]
    return payload


def plain(cfg, kind, values):
    """An alert as plain text, for every channel but Discord: (level, title, message)."""
    level, ev = ALERT_KINDS[kind][0], cfg["events"][kind]
    message = fill(ev["message"], values)[:4000]
    if cfg["details"]:
        cpu = values.get("cpu")
        message += f"\nCPU {cpu}°C · fans {values.get('speed') or '—'} · mode {values.get('mode') or '—'}" if cpu not in (None, "", "—") \
            else f"\nFans {values.get('speed') or '—'} · mode {values.get('mode') or '—'}"
    return level, fill(ev["title"], values)[:250], message


NTFY_PRIORITY = {"error": 5, "warn": 4, "ok": 3, "info": 2}
NTFY_TAGS = {"error": "rotating_light", "warn": "warning", "ok": "white_check_mark", "info": "information_source"}
GOTIFY_PRIORITY = {"error": 8, "warn": 6, "ok": 4, "info": 2}


def send_channel(cfg, channel, kind, values, prefix=""):
    """Send one alert to ntfy, Gotify or a webhook. Raises on failure."""
    level, title, message = plain(cfg, kind, values)
    title = prefix + title
    ch = cfg["channels"][channel]
    headers = {"Content-Type": "application/json", "User-Agent": f"fan-control/{VERSION}"}
    if channel == "ntfy":
        u = urlsplit(ch["url"])
        base, _, topic = u.path.rstrip("/").rpartition("/")
        url = f"{u.scheme}://{u.netloc}{base}/"  # JSON publishing goes to the server root, topic in the body
        body = {"topic": topic, "title": title, "message": message, "priority": NTFY_PRIORITY[level],
                "tags": [NTFY_TAGS[level]]}
        if ch["token"]:
            headers["Authorization"] = f"Bearer {ch['token']}"
    elif channel == "gotify":
        url = ch["url"].rstrip("/") + "/message"
        body = {"title": title, "message": message, "priority": GOTIFY_PRIORITY[level]}
        headers["X-Gotify-Key"] = ch["token"]
    else:
        url = ch["url"]
        body = {"event": kind, "level": level, "title": title, "message": message,
                "server": values.get("server"), "time": datetime.now(timezone.utc).isoformat(),
                "values": {k: str(v) for k, v in values.items()}}
    host = urlsplit(url).hostname or ""
    if host.startswith("169.254.") or host in ("metadata.google.internal",):
        raise ValueError("link-local addresses are not alert channels")
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    # a redirect to another host would carry the token along: refused, as for Redfish
    opener = urllib.request.build_opener(SameOriginRedirect)
    with opener.open(req, timeout=10) as r:
        r.read(65536)


def post_webhook(url, payload, method="POST"):
    """Send to Discord and return the message it created or edited (wait=true)."""
    req = urllib.request.Request(url + ("&" if "?" in url else "?") + "wait=true", data=json.dumps(payload).encode(),
                                 method=method, headers={"Content-Type": "application/json",
                                                         "User-Agent": f"fan-control/{VERSION}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


# ---------------------------------------------------------------- status reports

SPARK = "▁▂▃▄▅▆▇█"
REPORT_STATE = DATA_DIR / "report-state.json"


def sparkline(values, width=24):
    """Unicode bar chart of `values` squeezed into `width` buckets (bucket averages)."""
    values = [v for v in values if v is not None]
    if not values:
        return ""
    n = min(width, len(values))
    buckets = [values[i * len(values) // n:(i + 1) * len(values) // n] for i in range(n)]
    avgs = [sum(b) / len(b) for b in buckets]
    lo, hi = min(avgs), max(avgs)
    return "".join(SPARK[0 if hi == lo else round((a - lo) / (hi - lo) * (len(SPARK) - 1))] for a in avgs)


def period_text(minutes):
    return f"{minutes // 60} h" if minutes % 60 == 0 else f"{minutes} min"


def report_values(server, minutes):
    """Summary of the last `minutes` of history, as template placeholders plus the raw series."""
    with server.lock:
        pts = [p for p in server.history if p["t"] > time.time() - minutes * 60]
        st, events = dict(server.state), [e for e in server.events if e["t"] > time.time() - minutes * 60]
    cpu = [p["cpu"] for p in pts if p.get("cpu") is not None]
    speed = [p["speed"] for p in pts if p.get("speed") is not None]
    watts = [p["watts"] for p in pts if p.get("watts") is not None]
    sens = st["sensors"] or {}
    avg = lambda xs: sum(xs) / len(xs) if xs else None
    num = lambda v, unit="": "—" if v is None else f"{v:.0f}{unit}"
    values = {
        "server": server.name, "host": server.host, "model": st["model"] or server.driver.label,
        "cpu": num(st["cpu_temp"]), "mode": server.settings()["mode"], "reason": st["reason"],
        "speed": speed_text(st["effective"], st["applied_speed"]),
        "error": st["error"] or "", "failsafe": server.settings()["failsafe_temp"], "interval": INTERVAL,
        "time": time.strftime("%H:%M:%S"), "period": period_text(minutes),
        "cpu_min": num(min(cpu) if cpu else None), "cpu_avg": num(avg(cpu)), "cpu_max": num(max(cpu) if cpu else None),
        "speed_avg": num(avg(speed), "%"), "power_avg": num(avg(watts)),
        "dell_pct": num(100 * sum(p.get("speed") is None for p in pts) / len(pts) if pts else None),
        "fan_now": num(avg([f["pct"] for f in sens.get("fans", []) if f["pct"] is not None]), "%"),
        "watts": num(sens.get("watts")), "inlet": num(sens.get("inlet")), "exhaust": num(sens.get("exhaust")),
    }
    return values, {"cpu": cpu, "speed": speed, "watts": watts}, events


def build_report(cfg, servers):
    ev = cfg["events"]["report"]
    embeds = []
    for server in list(servers.values())[:10]:  # Discord allows 10 embeds per message
        v, series, events = report_values(server, cfg["report_minutes"])
        state = "🔴 BMC error" if v["error"] else "🟠 failsafe" if server.state["failsafe"] else "🟢 ok"
        fields = [
            {"name": "CPU", "value": f"**{v['cpu']}°C** now\n{v['cpu_min']}–{v['cpu_max']}°C · avg {v['cpu_avg']}°C", "inline": True},
            {"name": "Fans", "value": (f"**{v['speed']}**\navg {v['speed_avg']} · automatic {v['dell_pct']}% of the time"
                                       if server.control else f"**{v['fan_now']}** (set by the BMC)"), "inline": True},
            {"name": "Power", "value": f"**{v['watts']} W** now\navg {v['power_avg']} W", "inline": True},
            {"name": "Air", "value": f"in {v['inlet']}°C · out {v['exhaust']}°C", "inline": True},
            {"name": "Status", "value": f"{state} · mode {v['mode']}", "inline": True},
        ]
        if series["cpu"]:
            fields.append({"name": f"CPU, last {v['period']}",
                           "value": f"`{sparkline(series['cpu'])}` {v['cpu_min']}→{v['cpu_max']}°C", "inline": False})
        if series["speed"]:
            fields.append({"name": f"Fans, last {v['period']}",
                           "value": f"`{sparkline(series['speed'])}` {min(series['speed'])}→{max(series['speed'])}%", "inline": False})
        if events:
            lines = [f"`{time.strftime('%H:%M', time.localtime(e['t']))}` {e['msg']}"[:150] for e in events[:5]]
            fields.append({"name": "Recent events", "value": "\n".join(lines)[:1024], "inline": False})
        embed = {"title": fill(ev["title"], v)[:256], "description": fill(ev["message"], v)[:4000],
                 "color": int(cfg["colors"]["error" if v["error"] else "warn" if server.state["failsafe"] else "info"][1:], 16),
                 "fields": fields, "timestamp": datetime.now(timezone.utc).isoformat()}
        if cfg["footer"]:
            embed["footer"] = {"text": fill(cfg["footer"], v)[:2048]}
        embeds.append(embed)
    payload = {"username": cfg["username"], "embeds": embeds, "allowed_mentions": {"parse": []}}
    if cfg["avatar_url"]:
        payload["avatar_url"] = cfg["avatar_url"]
    return payload


def send_report(servers, force=False):
    """Post the status report when it is due. In "edit" mode the same message is updated in
    place, so the channel holds one live status card instead of a stream of them."""
    cfg = alert_config()
    url = webhook_of(cfg)
    if not (url and cfg["enabled"] and cfg["events"]["report"]["enabled"]):
        return
    try:
        st = json.loads(REPORT_STATE.read_text())
    except (OSError, ValueError):
        st = {}
    if not force and time.time() - st.get("last", 0) < cfg["report_minutes"] * 60:
        return
    payload = build_report(cfg, servers)
    hook = hashlib.sha256(url.encode()).hexdigest()[:16]  # a new webhook starts a new message
    msg_id = st.get("message_id") if cfg["report_mode"] == "edit" and st.get("hook") == hook else None
    try:
        if msg_id:
            try:
                post_webhook(f"{url}/messages/{msg_id}", payload, "PATCH")
            except urllib.error.HTTPError as e:
                if e.code != 404:  # message deleted in Discord: post a fresh one
                    raise
                msg_id = None
        if not msg_id:
            msg_id = post_webhook(url, payload).get("id")
    except Exception as e:
        log.error("Discord status report failed: %s", e)
        return
    write_json(REPORT_STATE, {"last": time.time(), "message_id": msg_id, "hook": hook})


def reporter(servers):
    while True:
        time.sleep(30)
        try:
            send_report(servers)
        except Exception as e:  # a reporting bug must never stop the reports for good
            log.exception("status report error: %r", e)


def notify(server, kind, **values):
    """Send one alert in the background, so a slow Discord never delays the fans."""
    cfg = alert_config()
    url, chans = webhook_of(cfg), channels_of(cfg)
    if kind == "report" or not ((url or chans) and cfg["enabled"] and cfg["events"][kind]["enabled"]):
        return
    key, now = (server.id, kind), time.time()
    with alert_lock:
        if now - last_alert.get(key, 0) < cfg["cooldown_minutes"] * 60:
            return
        last_alert[key] = now
    st, s = server.state, server.settings()
    base = {"server": server.name, "host": server.host, "model": st["model"] or server.driver.label,
            "cpu": "—" if st["cpu_temp"] is None else f"{st['cpu_temp']:.0f}",
            "speed": speed_text(st["effective"], st["applied_speed"]),
            "mode": s["mode"], "reason": st["reason"], "error": st["error"] or "",
            "failsafe": s["failsafe_temp"], "threshold": cfg["hot_threshold"], "interval": INTERVAL,
            "time": time.strftime("%H:%M:%S"), "inlet_threshold": cfg["inlet_threshold"],
            "inlet": "—" if (st["sensors"] or {}).get("inlet") is None else f"{st['sensors']['inlet']:.0f}"}
    values = {**base, **values}

    def send():
        if url:
            try:
                post_webhook(url, build_payload(cfg, kind, values))
                SENT["sent"] += 1
            except Exception as e:
                log.error("Discord alert failed: %s", e, extra={"server": server.id})
                SENT["failed"] += 1
        for c in chans:  # one failing channel never stops the others
            try:
                send_channel(cfg, c, kind, values)
                SENT["sent"] += 1
            except Exception as e:
                log.error("%s alert failed: %s", c, e, extra={"server": server.id})
                SENT["failed"] += 1

    threading.Thread(target=send, daemon=True).start()
