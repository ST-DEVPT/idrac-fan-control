"""Dashboard widgets: what the embed token may read (/api/widget), and the Homarr 2.0 Custom Widget."""

import json
import time
from urllib.parse import urlsplit

from .config import DATA_DIR, INTERVAL, write_json
from .server import SERVERS

# What a dashboard widget (Homarr, an iframe) may show. The embed token reads only these, through
# /api/widget: never the BMC address, settings, events or error messages.
WIDGET_FIELDS = ("status", "cpu", "fans", "power", "inlet", "exhaust", "chart", "model")
WIDGET_FILE = DATA_DIR / "widget.json"


def widget_fields():
    try:
        chosen = json.loads(WIDGET_FILE.read_text())["fields"]
        if isinstance(chosen, list):
            return [f for f in WIDGET_FIELDS if f in chosen]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return [f for f in WIDGET_FIELDS if f != "model"]


def save_widget_fields(fields):
    if not isinstance(fields, list) or not all(f in WIDGET_FIELDS for f in fields):
        raise ValueError(f"fields must be a list drawn from {', '.join(WIDGET_FIELDS)}")
    write_json(WIDGET_FILE, {"fields": [f for f in WIDGET_FIELDS if f in fields]})


def widget_view(srv, fields):
    """One server as a widget may see it: its name, and only the fields shared."""
    now = time.time()
    with srv.lock:
        s = dict(srv.state)
        hour = [p for p in srv.history if p["t"] > now - 3600]
    sens = s["sensors"] or {}
    out = {"id": srv.id, "name": srv.name, "updated": s["updated"]}
    if "status" in fields:
        stale = not s["updated"] or now - s["updated"] > INTERVAL * 4
        mode = srv.settings()["mode"]
        out["health"] = "error" if s["error"] or stale else "warn" if s["failsafe"] else "ok"
        out["mode"] = ("BMC error" if s["error"] or stale else "Failsafe" if s["failsafe"]
                       else "Monitoring" if s["effective"] == "monitor" else "Automatic" if s["effective"] == "auto"
                       else {"fixed": "Fixed", "curve": "Curve", "smart": "Smart"}.get(mode, mode.title()))
    if "cpu" in fields:
        out["cpu"] = s["cpu_temp"]
    if "fans" in fields:
        pcts = [f["pct"] for f in sens.get("fans", []) if f["pct"] is not None]
        rpms = [f["rpm"] for f in sens.get("fans", []) if f["rpm"] is not None]
        out["fan_pct"] = (s["applied_speed"] if s["effective"] == "manual"
                          else round(sum(pcts) / len(pcts)) if pcts else None)
        out["fan_rpm"] = round(sum(rpms) / len(rpms)) if rpms else None
    if "power" in fields:
        out["watts"] = sens.get("watts")
    for key in ("inlet", "exhaust"):
        if key in fields:
            out[key] = sens.get(key)
    if "model" in fields:
        out["model"] = s["model"]
    if "chart" in fields:  # the last hour in 60 points, gaps left out
        pts = hour[::max(1, len(hour) // 60)]
        out["history"] = {"cpu": [p["cpu"] for p in pts if p.get("cpu") is not None],
                          "fans": [v for p in pts if (v := p["speed"] if p.get("speed") is not None else p.get("fanpct")) is not None]}
    return out


# A Homarr 2.0 Custom Widget ("homarr-custom-widget-v2"): imported under Management > Custom Widgets.
# Homarr fetches /api/widget server side with EMBED_TOKEN as a Bearer credential, which it stores
# encrypted; the definition itself carries no secret.
HOMARR_TEMPLATE = """<Stack gap="xs" p="sm" h="100%" style={{ minWidth: 0, minHeight: 0 }}>
  <Group justify="space-between" wrap="nowrap"><Text size="xs" c="dimmed" tt="uppercase" fw={700}>Fan Control</Text><RefreshButton requestId="status" label="Refresh fan status" size="xs" /></Group>
  {status.status?.loading && <Skeleton height={96} radius="md" />}
  {status.status?.ok === false && <Alert color="red" title="Fan Control unavailable">{status.status.error || "Check the address and the EMBED_TOKEN credential, then refresh."}</Alert>}
  {!status.status?.loading && status.status?.ok !== false && <ScrollArea style={{ flex: 1, minHeight: 0 }}><Stack gap="xs">
    {(data.status?.servers ?? []).length === 0 && <Alert color="gray" title="No servers">Add a server in Fan Control, or check the server option.</Alert>}
    {(data.status?.servers ?? []).map(server => <Paper key={server.id} withBorder radius="md" p="sm"><Stack gap={6}>
      <Group justify="space-between" wrap="nowrap" gap="xs">
        <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}><ColorSwatch size={9} withShadow={false} color={server.health === "error" ? "var(--mantine-color-red-6)" : server.health === "warn" ? "var(--mantine-color-orange-6)" : server.health === "ok" ? "var(--mantine-color-green-6)" : "var(--mantine-color-gray-6)"} /><Text fw={650} truncate>{server.name}</Text>{server.model && <Text size="xs" c="dimmed" truncate>{server.model}</Text>}</Group>
        {server.mode && <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>{server.mode}</Text>}
      </Group>
      <SimpleGrid cols={3} spacing="xs">
        {(data.status?.fields ?? []).includes("cpu") && <Stack gap={0}><Text size="xs" c="dimmed">CPU</Text><Text fw={700} size="xl">{server.cpu == null ? "—" : Math.round(options.unit === "fahrenheit" ? server.cpu * 9 / 5 + 32 : server.cpu) + (options.unit === "fahrenheit" ? " °F" : " °C")}</Text></Stack>}
        {(data.status?.fields ?? []).includes("fans") && <Stack gap={0}><Text size="xs" c="dimmed">Fans</Text><Text fw={700} size="xl">{server.fan_pct != null ? server.fan_pct + " %" : server.fan_rpm != null ? (server.fan_rpm / 1000).toFixed(1) + "k rpm" : "—"}</Text></Stack>}
        {(data.status?.fields ?? []).includes("power") && <Stack gap={0}><Text size="xs" c="dimmed">Power</Text><Text fw={700} size="xl">{server.watts == null ? "—" : Math.round(server.watts) + " W"}</Text></Stack>}
      </SimpleGrid>
      {options.chart && (server.history?.cpu?.length ?? 0) > 1 && <Sparkline h={34} data={server.history.cpu} curveType="linear" color="blue" fillOpacity={0.15} />}
      {options.air && ((data.status?.fields ?? []).includes("inlet") || (data.status?.fields ?? []).includes("exhaust")) && <Text size="xs" c="dimmed">{(data.status?.fields ?? []).includes("inlet") ? "Inlet " + (server.inlet == null ? "—" : Math.round(options.unit === "fahrenheit" ? server.inlet * 9 / 5 + 32 : server.inlet) + (options.unit === "fahrenheit" ? " °F" : " °C")) : ""}{(data.status?.fields ?? []).includes("inlet") && (data.status?.fields ?? []).includes("exhaust") ? " · " : ""}{(data.status?.fields ?? []).includes("exhaust") ? "Exhaust " + (server.exhaust == null ? "—" : Math.round(options.unit === "fahrenheit" ? server.exhaust * 9 / 5 + 32 : server.exhaust) + (options.unit === "fahrenheit" ? " °F" : " °C")) : ""}</Text>}
    </Stack></Paper>)}
  </Stack></ScrollArea>}
</Stack>"""


def homarr_widget(base, server="all", scope="private"):
    """The Custom Widget definition for this Fan Control, at the address Homarr will reach it on."""
    url = urlsplit(base)
    if url.scheme not in ("http", "https") or not url.netloc or url.path not in ("", "/") or url.query:
        raise ValueError("base must be the address of this dashboard, like https://fans.example.com")
    if scope not in ("private", "public", "loopback"):
        raise ValueError("scope must be private, public or loopback")
    if server != "all" and server not in SERVERS:
        raise ValueError("unknown server")
    return {
        "$schema": "homarr-custom-widget-v2",
        "name": "Fan Control" if server == "all" else f"Fan Control: {SERVERS[server].name}"[:100],
        "description": "Temperatures, fan speed and power of the servers Fan Control looks after.",
        "sources": {"default": {"name": "Fan Control", "baseUrl": f"{url.scheme}://{url.netloc}",
                                "networkScope": scope, "auth": "bearer"}},
        "requests": {"status": {"path": "/api/widget", "query": {"server": {"$option": "server"}}, "cacheSeconds": 10}},
        "options": {
            "server": {"label": "Server", "description": "A server id from Fan Control, or all", "control": "text",
                       "default": server},
            "unit": {"label": "Temperature unit", "control": "select", "default": "celsius",
                     "choices": [{"label": "Celsius", "value": "celsius"}, {"label": "Fahrenheit", "value": "fahrenheit"}]},
            "chart": {"label": "Last hour chart", "control": "switch", "default": True},
            "air": {"label": "Inlet and exhaust air", "control": "switch", "default": True},
        },
        "template": HOMARR_TEMPLATE,
    }
