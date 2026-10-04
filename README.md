# Fan Control

**Quiet rack servers, without cooking them.** A self-hosted dashboard for Dell PowerEdge, Supermicro,
HPE ProLiant and any Redfish or IPMI server. It takes over fan control where the vendor allows it, holds
the temperatures you choose, hands control back to the BMC the moment anything looks wrong, and watches
everything else.

[![CI](https://github.com/ST-DEVPT/rack-fan-control/actions/workflows/docker.yml/badge.svg)](https://github.com/ST-DEVPT/rack-fan-control/actions/workflows/docker.yml)
[![Release](https://img.shields.io/github/v/release/ST-DEVPT/rack-fan-control)](https://github.com/ST-DEVPT/rack-fan-control/releases)
[![Image](https://img.shields.io/badge/image-ghcr.io-blue)](https://github.com/ST-DEVPT/rack-fan-control/pkgs/container/rack-fan-control)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<img alt="Overview: two servers under fan control and an HPE server that cannot be reached" src="docs/overview.jpg">

<img alt="A server's page: live readings and history" src="docs/server.jpg">

## Highlights

- **Every server in one place**: an overview of the rack and a page per server. Scan your network for BMCs,
  let each one tell its vendor and firmware, test the connection, add it.
- **Smart mode**: finds the slowest fan speed that holds the CPU at a target and keeps every other sensor
  clear of its limits. Or draw a curve, or pick a fixed speed.
- **Safe by design**: the BMC takes over on a CPU failsafe, on hot exhaust air, when any sensor nears the
  warning level its BMC defines, on missing readings, refused commands, crashes and `docker stop`.
  A minimum speed and a dry-run mode for trying things out.
- **Quiet hours**, ramp-down smoothing, curve presets, and 3 h / 24 h / 7 d history that survives restarts.
- **Discord** alerts you can fully customise, and a status card that keeps itself up to date.
- **Prometheus**, a ready-made **Grafana** dashboard, a **Homarr** widget, each with its own setup page.
- **Read-only accounts**, backups, English or Portuguese, °C or °F.
- **Small and private**: plain Python, no dependencies, no third-party requests, runs as non-root.

## Contents

[Supported hardware](#supported-hardware) ·
[Quick start](#quick-start) ·
[Configuration](#configuration) ·
[Fan control](#fan-control) ·
[Discord](#discord) ·
[Integrations](#integrations) ·
[Accounts and security](#accounts-and-security) ·
[Backup](#backup) ·
[Troubleshooting](#troubleshooting) ·
[Upgrading](#upgrading) ·
[Development](#development)

## Supported hardware

| Type | Servers | Fan control | Reads |
| --- | --- | --- | --- |
| **Dell PowerEdge (iDRAC)** | iDRAC 6, 7, 8, and iDRAC 9 up to firmware 3.30.30.30 | Yes | IPMI |
| **Supermicro** | X9, X10 and X11 boards | Yes, experimental | IPMI |
| **HPE iLO 4, unlocked firmware** | ProLiant Gen8 / Gen9 with the community-patched iLO 4 2.77 | Yes (caps over SSH), experimental | Redfish |
| **Redfish** | HPE iLO 4 (2.30+), iLO 5, iLO 6, Lenovo XCC, Dell iDRAC 9, most recent BMCs | Monitoring only | Redfish |
| **Other IPMI** | Any BMC with IPMI over LAN | Monitoring only | IPMI |
| **Demo** | Simulated readings | Yes (simulated) | — |

Monitoring only means the vendor firmware offers no way to set fan speed. Those servers still get the
overview card, history, alerts, reports, metrics and the widget, which is often enough to find what makes
a server loud: on HPE, a third-party PCIe card or disk the iLO cannot read is the usual cause.

Dell iDRAC 9 from firmware 3.34.34.34, and iDRAC 10, no longer accept fan commands: add them as Redfish.
Not sure what you have? **Scan network** and **Detect** on the *Add server* page ask the BMCs and pick the
type for you. The scan only accepts private ranges, up to a /22, and only an admin can run it.

## Quick start

**1. Run the container**

```bash
mkdir fan-control && cd fan-control
curl -O https://raw.githubusercontent.com/ST-DEVPT/rack-fan-control/main/docker-compose.yml
mkdir data && chown 1000:1000 data
```

Set `WEB_PASSWORD` (and `TZ`) in `docker-compose.yml`, then:

```bash
docker compose up -d
```

**2. Open `http://<docker-host>:8080`**, sign in, and choose **Add server**.

**3. Find your servers**: **Scan network** probes a private range (for example `192.168.1.0/24`) for BMCs
answering Redfish or IPMI and suggests the type of each; **Use** fills in the form. Or enter one address and
press **Detect**, or pick the hardware yourself. Fill in the user and
password and press **Test connection**: it reads the BMC once and shows the model, sensors and power draw
before anything is saved.

Before adding a server, prepare its BMC:

- **Dell**: enable **IPMI over LAN** (iDRAC Settings → Network → IPMI Settings); the user must be an Administrator.
- **Supermicro**: IPMI over LAN is on by default; use an Administrator account.
- **HPE and other Redfish**: any account that can read the system health. Redfish uses HTTPS (port 443).
- **HPE iLO 4 unlocked**: SSH (port 22) and HTTPS must both be reachable, and the firmware must be the patched 2.77.

Images are built for `linux/amd64` and `linux/arm64`: `ghcr.io/st-devpt/rack-fan-control:latest`,
or pin a version such as `:2.0`.

## Configuration

Servers, fan settings and Discord are configured in the dashboard. The environment holds the rest:

| Variable | Default | Description |
| --- | --- | --- |
| `WEB_PASSWORD` | empty | Admin password. Empty disables sign-in; only do that on a trusted network |
| `VIEW_PASSWORD` | empty | Optional read-only account: sees everything, changes nothing |
| `TZ` | UTC | Time zone for quiet hours and the event log, e.g. `Europe/Lisbon` |
| `CHECK_INTERVAL` | `15` | Seconds between readings and fan commands. Minimum 5 |
| `DISCORD_WEBHOOK_URL` | empty | Default Discord webhook. A webhook pasted in the dashboard takes precedence |
| `METRICS_TOKEN` | empty | Enables `/metrics` for Prometheus, with `Authorization: Bearer <token>` |
| `EMBED_TOKEN` | empty | Enables the read-only `/embed` widget with `?token=<token>` |
| `TRUST_PROXY` | off | Set to `true` behind a reverse proxy, so sign-in limits and the event log use `X-Forwarded-For` |
| `PORT` | `8080` | HTTP port inside the container |

### Servers in the environment

Servers can also be declared in `docker-compose.yml`, for example to keep credentials in a `.env` file.
They show up in the dashboard like the others, but can only be changed in the environment:

```yaml
    environment:
      SERVER_1_NAME: Compute
      SERVER_1_DRIVER: dell              # dell, supermicro, ilo4-unlocked, redfish, ipmi or demo
      SERVER_1_HOST: 192.168.1.120       # "local" for IPMI from the server itself
      SERVER_1_USERNAME: root
      SERVER_1_PASSWORD: ${COMPUTE_BMC_PASSWORD}
      SERVER_2_NAME: DL360
      SERVER_2_DRIVER: redfish
      SERVER_2_HOST: 192.168.1.121
      SERVER_2_USERNAME: Administrator
      SERVER_2_PASSWORD: ${DL360_ILO_PASSWORD}
      SERVER_2_VERIFY_TLS: "false"       # BMCs ship self-signed certificates
```

The 1.x variables still work and keep their settings: `IDRAC_HOST`, `IDRAC_USERNAME`, `IDRAC_PASSWORD`,
`IDRAC_NAME` for one server and `IDRAC_1_HOST`, ... for several.

### What is stored in `/data`

| File | Contents |
| --- | --- |
| `servers.json` | Servers added in the dashboard, with their BMC passwords (file mode 600, never sent to the browser) |
| `settings.json`, `settings-<server>.json` | Mode, curve, targets, limits, quiet hours and the other fan settings |
| `history-<server>.json` | Three hours of readings, seven days of 5-minute averages, and the events |
| `alerts.json` | Discord settings, including the webhook (file mode 600, never sent to the browser) |
| `report-state.json` | Which Discord message the status card is edited into |
| `known_hosts` | SSH host keys of unlocked iLO 4 servers, recorded on first connection |
| `secret` | Key that signs sign-in sessions. Delete it to sign everyone out |

## Fan control

For servers whose type has fan control (Dell, Supermicro, unlocked iLO 4):

| Mode | What happens |
| --- | --- |
| **Automatic** | The BMC runs its factory profile. Loudest, and the fallback for every problem |
| **Fixed** | Every fan at one speed |
| **Curve** | Speed follows the hottest CPU along points you drag. Start from a preset (quiet, balanced, cool, storage) or copy another server's curve |
| **Smart** | Finds the slowest speed that holds the CPU at a target you set, and keeps every other sensor clear of its limits |

### Smart mode

A PI controller (proportional and integral) looks at the CPU against its target, the exhaust air against
8 °C below its limit, and every sensor with a BMC warning threshold against 8 °C below the point where the
failsafe would trip. It follows whichever is worst. It may raise the speed by 15 % in one step, lowers it by
at most 0.2 % per second, and ignores corrections under 2 %, so you don't hear it hunting. At idle it rests
on the minimum speed.

### Protection

The BMC takes over, whatever the mode, when:

- the hottest CPU reaches the **CPU failsafe**;
- the exhaust air reaches the **exhaust air limit** (on by default, empty turns it off);
- any sensor comes within the margin (5 °C by default) of the **warning threshold its BMC defines**:
  PCIe cards, disks, DIMMs, the RAID controller, which a CPU-only curve would never see;
- there is no CPU reading, the server is off, a fan command is refused, the control loop fails, the
  server is removed or the container stops.

Manual control resumes once things are 3 °C below the limit that tripped, so the fans don't flap at the
edge. Fans never run below the **minimum speed** in manual modes.

**Dry run** decides and logs what it would send ("Dry run: would set fans to 25%"), but leaves the fans
to the BMC. Use it to try a new server type or a new curve.

### Smoothing and quiet hours

**Ramp-down delay** (curve and fixed modes): fans speed up at once but slow down only after the lower speed
has been asked for during the whole delay. It never runs the fans slower than the curve.

**Quiet hours** cap the speed between two times of day, for example 23:00 to 07:00 at 25 %. Protection
still applies at any hour.

### A starting point

For a quiet homelab server with Xeon E5 processors, the **Quiet** preset (35 °C → 12 %, 45 → 15, 55 → 22,
62 → 35, 68 → 55) with a 72 °C failsafe, or **Smart** with a 60 °C target. Some BMCs raise fan alarms below
about 10 %. With third-party PCIe cards installed, stay at 15 % or above, and on Dell leave the
third-party PCIe cooling response **On**.

**Per vendor.** Dell gets one IPMI command for all fans; some 11th-generation servers refuse it, and the
controller then finds the fans they accept and sets them one by one. Supermicro is switched to *Full* fan
mode once, then both zones are set; it goes back to *Optimal* when released. An unlocked iLO 4 gets a cap on
every fan over SSH (`fan p N max`), so its own curve still runs underneath; releasing removes the cap.

## Discord

Open **Discord** in the sidebar and paste a webhook (Server Settings → Integrations → Webhooks → Copy
Webhook URL). Everything else is optional: bot name, avatar, footer, a colour per level, mentions (nobody,
`@here`, `@everyone`, a role or a user, only for the levels you choose), a cooldown, and which events to send,
each with its own title and message. A live preview shows the result and **Send test** posts it before you
save. Text that comes from a BMC can never ping anyone.

| Event | Level | Sent when |
| --- | --- | --- |
| Failsafe reached | warning | A limit trips and the BMC takes over |
| Failsafe cleared | resolved | Manual control resumes |
| Running hot | warning | The CPU passes the "running hot" temperature (off by default) |
| BMC unreachable | error | Readings fail |
| Fan command refused | error | The BMC rejects a fan command |
| Back to normal | resolved | The BMC answers and accepts commands again |
| Controller error | error | The control loop hits an unexpected error |
| Settings changed | info | Someone applies new settings (off by default) |
| Controller started | info | The container starts (off by default) |
| Status report | info | On a schedule (off by default) |

Placeholders: `{server}` `{host}` `{model}` `{cpu}` `{speed}` `{mode}` `{reason}` `{error}` `{failsafe}`
`{threshold}` `{interval}` `{time}`; status reports add `{period}` `{cpu_min}` `{cpu_avg}` `{cpu_max}`
`{speed_avg}` `{power_avg}` `{dell_pct}`.

The **status report** is one card per server: CPU, fans and power now, minimum, average and maximum over
the period, air temperatures, time under automatic control, trend lines and the latest events. By default
the same message is edited each time, so the channel holds one live status card.

<img alt="Discord status report" src="docs/discord-report.jpg" width="640">

## Integrations

Each has its own page in the sidebar, with the snippets filled in for your setup.

**Prometheus**: set `METRICS_TOKEN`; the page shows the scrape job to paste and the live `/metrics` output.

```yaml
scrape_configs:
  - job_name: fan-control
    authorization:
      credentials: <METRICS_TOKEN>
    static_configs:
      - targets: ["<docker-host>:8080"]
```

| Metric | Labels |
| --- | --- |
| `fanctl_up`, `fanctl_power_on`, `fanctl_bmc_control`, `fanctl_failsafe_active` | `server`, `name`, `driver` |
| `fanctl_cpu_temperature_celsius`, `fanctl_inlet_temperature_celsius`, `fanctl_exhaust_temperature_celsius` | `server`, `name`, `driver` |
| `fanctl_fan_speed_percent`, `fanctl_power_watts`, `fanctl_last_update_timestamp_seconds` | `server`, `name`, `driver` |
| `fanctl_temperature_celsius` | ... `sensor`, `entity` |
| `fanctl_fan_rpm`, `fanctl_fan_percent` | ... `fan` |

**Grafana**: download the dashboard from the Grafana page (or `web/grafana.json`), then
Dashboards → New → Import, and pick your Prometheus data source.

**Homarr** (or any dashboard that shows a web page): set `EMBED_TOKEN`; the page builds the widget address
for the server, theme and background you pick, with a live preview. Add it as an *iFrame* widget, and use
`/healthz` as the status check of an app tile.

<img alt="Embed widget" src="docs/embed.jpg" width="380">

## Accounts and security

- `WEB_PASSWORD` is the admin account. `VIEW_PASSWORD` adds a read-only one: everything is visible,
  nothing can be changed, and the backup can't be downloaded.
- Changes are written to the server's event log with the account and address that made them.
- Failed sign-ins are slowed and limited to 5 per address in 10 minutes; one address guessing does not lock
  out the others. Behind a proxy, set `TRUST_PROXY=true` so the real address is used.
- Keep the dashboard on your LAN or VPN. With a reverse proxy on the same host, publish the port on
  localhost only (`"127.0.0.1:8080:8080"`), forward to it, and keep the `Host` header (the default in
  Nginx Proxy Manager). The session cookie gets the `Secure` flag when the proxy sends `X-Forwarded-Proto: https`.

Details, and how to report a vulnerability, are in [SECURITY.md](SECURITY.md).

## Backup

The **Backup** page downloads the servers added in the dashboard, every server's fan settings and the
Discord configuration as one JSON file, and imports such a file on this or another machine. Passwords and
the webhook are included only when you tick the box; a backup without them restores the settings, but
servers have to be added again with their passwords.

## Troubleshooting

Use **Test connection** (Edit on the server's page) to see the BMC's answer without saving anything, and
**Dry run** to watch what the controller would do.

| Message | Meaning |
| --- | --- |
| `rsp=0xc1` | The firmware does not have the fan commands (Dell iDRAC 9 3.34.34.34 or later): use the Redfish type |
| `rsp=0xd4` | The BMC user is not an Administrator |
| `Unable to establish IPMI v2 / RMCP+ session` | Wrong address or credentials, or IPMI over LAN is off |
| `Redfish ...: HTTP 401` | Wrong user name or password |
| `Redfish ...: timed out` | The BMC is not reachable on HTTPS from the container |
| `SSH: ... (is the iLO firmware unlocked?)` | The iLO answered, but does not have the `fan` command: stock firmware |
| `... near its N°C warning threshold` | A sensor other than the CPU is hot; see the Temperatures table, where each bar marks its threshold |
| `ERROR: cannot write to /data` (container log) | The data folder is not owned by uid 1000 |

## Upgrading

```bash
docker compose pull && docker compose up -d
```

**From 1.x:** the image is now `ghcr.io/st-devpt/rack-fan-control`; change it in your compose file.
`IDRAC_*` variables keep working, with their settings and history. Metrics are renamed from `idrac_*` to
`fanctl_*`: re-import the Grafana dashboard. The mode called "Dell" is now "Automatic"; saved settings are
converted. Sessions from 1.x are signed out once.

**From 1.0:** the container runs as uid 1000. Give it the data folder once with `chown -R 1000:1000 ./data`.
With `IDRAC_HOST=local`, the container needs `/dev/ipmi0` and root to open it:

```yaml
    user: "0:0"
    devices:
      - /dev/ipmi0:/dev/ipmi0
```

Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Development

```bash
python app.py                   # dashboard on http://localhost:8080; add a "Demo server" to try it
python -m unittest              # tests: drivers, control logic, smart mode, server registry, alerts, HTTP
```

Python 3.10 or newer, no dependencies. The code is in `fanctl/`: `drivers.py` (how each kind of BMC is read
and driven), `control.py` (decisions and smart mode, no I/O), `server.py` (control loop and server registry),
`alerts.py` (Discord) and `web.py` (HTTP, sessions, metrics, backup); `app.py` starts it all. Adding a vendor
means a class in `drivers.py` with `read()` and, if it can control fans, `set_speed()` and `set_auto()`.
The web pages are in `web/`, served with a strict Content-Security-Policy; `web/i18n.js` holds the
Portuguese translation.

## License

[MIT](LICENSE). Bundled fonts: Archivo and IBM Plex Mono, under the SIL Open Font License (`web/fonts/`).
