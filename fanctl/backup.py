"""Backup and restore: servers added in the dashboard, every fan setting, alerts, widgets and what
smart mode learned. Not included: history (it is not configuration), tokens and sessions (tokens are
shown once by design; make new ones on the new machine)."""

import time

from .alerts import alert_config, save_alerts, validate_alerts
from .config import VERSION
from .control import validate_settings
from .server import SERVERS, save_dashboard_servers, start, unique_id, validate_server
from .widgets import save_widget_fields, widget_fields


def export_config(secrets_too=False):
    """Servers added in the dashboard, every server's settings, and Discord. Passwords and the
    webhook only when asked for, since the file then unlocks every BMC."""
    servers = []
    for s in list(SERVERS.values()):
        if s.cfg.get("source") != "dashboard":
            continue
        row = {k: s.cfg.get(k) for k in ("id", "name", "driver", "host", "username", "verify_tls")}
        if secrets_too:
            row["password"] = s.cfg.get("password", "")
        servers.append(row)
    alerts_cfg = alert_config()
    if not secrets_too:  # the webhook, channel addresses (an ntfy topic is its password) and tokens
        alerts_cfg = {**{k: v for k, v in alerts_cfg.items() if k != "webhook_url"},
                      "channels": {c: {"enabled": ch["enabled"]} for c, ch in alerts_cfg["channels"].items()}}
    return {"format": "fan-control-backup", "version": 1, "app": VERSION, "exported": int(time.time()),
            "with_secrets": secrets_too, "servers": servers,
            "settings": {s.id: s.settings() for s in list(SERVERS.values())}, "alerts": alerts_cfg,
            "widget": {"fields": widget_fields()},
            # what smart mode learned: days of steady running that should survive a move to another machine
            "smart": {s.id: dict(s.learned) for s in list(SERVERS.values()) if s.learned}}


def import_config(data, who="import"):
    """Apply a backup: add the servers it has that are missing, then settings for every server
    that exists by then, then Discord. Everything is validated as if typed in the dashboard."""
    if not isinstance(data, dict) or data.get("format") != "fan-control-backup":
        raise ValueError("missing format marker")
    report = {"added": [], "updated": [], "skipped": []}
    for row in data.get("servers", []):
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id", ""))
        if sid in SERVERS:
            report["skipped"].append(f"{row.get('name', sid)}: already here")
            continue
        try:
            cfg = validate_server(row)
        except ValueError as e:
            report["skipped"].append(f"{row.get('name', sid)}: {e}")
            continue
        cfg["id"] = unique_id(sid or cfg["name"], SERVERS)
        start(cfg).log(f"Added from a backup by {who}")
        report["added"].append(cfg["name"])
    if report["added"]:
        save_dashboard_servers()
    for sid, settings in (data.get("settings") or {}).items():
        srv = SERVERS.get(sid)
        if not srv:
            continue
        try:
            srv.save_settings(validate_settings(settings, srv.settings()), f"{who} (backup)")
            report["updated"].append(srv.name)
        except (ValueError, TypeError) as e:
            report["skipped"].append(f"{srv.name} settings: {e}")
    if isinstance(data.get("alerts"), dict):
        try:
            save_alerts(validate_alerts(data["alerts"], alert_config()))
            report["updated"].append("Discord")
        except (ValueError, TypeError, AttributeError) as e:
            report["skipped"].append(f"Discord: {e}")
    for sid, learned in (data.get("smart") or {}).items():
        srv = SERVERS.get(sid)
        if srv and isinstance(learned, dict):
            from .control import validate_learned
            with srv.lock:
                srv.learned.update(validate_learned(learned))
            report["updated"].append(f"{srv.name} smart map")
    if isinstance(data.get("widget"), dict):
        try:
            save_widget_fields(data["widget"].get("fields"))
            report["updated"].append("Widgets")
        except ValueError as e:
            report["skipped"].append(f"Widgets: {e}")
    return report
