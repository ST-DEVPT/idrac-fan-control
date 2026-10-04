# Fan Control

**Quiet rack servers, without cooking them.** A self-hosted dashboard for Dell PowerEdge, Supermicro,
HPE ProLiant and any Redfish or IPMI server: it takes over fan control where the vendor allows it,
follows a temperature curve you draw, hands control back to the BMC the moment anything looks wrong,
and watches everything else.

[![CI](https://github.com/ST-DEVPT/idrac-fan-control/actions/workflows/docker.yml/badge.svg)](https://github.com/ST-DEVPT/idrac-fan-control/actions/workflows/docker.yml)
[![Release](https://img.shields.io/github/v/release/ST-DEVPT/idrac-fan-control)](https://github.com/ST-DEVPT/idrac-fan-control/releases)
[![Image](https://img.shields.io/badge/image-ghcr.io-blue)](https://github.com/ST-DEVPT/idrac-fan-control/pkgs/container/idrac-fan-control)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/dashboard-dark.jpg">
  <img alt="Dashboard: live readings, history chart and fan curve" src="docs/dashboard-light.jpg">
</picture>

## Highlights

- **Every server in one place**: an overview of the whole rack, and a page per server.
- **Add servers from the browser**: pick the hardware, enter the BMC address, test the connection, save.
- **Fixed speed or a fan curve** you drag into shape, with the BMC's automatic mode one click away.
- **Fails safe**: a failsafe temperature, missing readings, a refused command, a crash or `docker stop`
  all hand the fans back to the BMC.
- **Smooth**: fans speed up at once and slow down only after a delay, so they don't hunt up and down.
- **Discord**: alerts you can fully customise, plus a status card that keeps itself up to date.
- **Prometheus metrics**, a ready-made **Grafana** dashboard and a widget for **Homarr**.
- **Small and private**: plain Python, no dependencies, no third-party requests, runs as non-root.

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
overview card, history, Discord alerts and reports, metrics and the Homarr widget, which is often enough to
find what makes a server loud: on HPE, a third-party PCIe card or disk the iLO cannot read is the usual cause.

Dell iDRAC 9 from firmware 3.34.34.34, and iDRAC 10, no longer accept fan commands: add them as Redfish.

## Contents

[Quick start](#quick-start) ·
[Configuration](#configuration) ·
[Fan control](#fan-control) ·
[Discord](#discord) ·
[Prometheus and Grafana](#prometheus-and-grafana) ·
[Homarr and other dashboards](#homarr-and-other-dashboards) ·
[Reverse proxy and security](#reverse-proxy-and-security) ·
[Troubleshooting](#troubleshooting) ·
[Upgrading](#upgrading) ·
[Development](#development)

## Quick start

**1. Run the container**

```bash
mkdir fan-control && cd fan-control
curl -O https://raw.githubusercontent.com/ST-DEVPT/idrac-fan-control/main/docker-compose.yml
mkdir data && chown 1000:1000 data
```

Set `WEB_PASSWORD` in `docker-compose.yml`, then:

```bash
docker compose up -d
```

**2. Open `http://<docker-host>:8080`**, sign in, and choose **Add server**.

**3. Pick your hardware**, enter the BMC address, user and password, and press **Test connection**.
The test reads the BMC once and shows the model, sensors and power draw before anything is saved.

Before adding a server, prepare its BMC:

- **Dell**: enable **IPMI over LAN** (iDRAC Settings → Network → IPMI Settings); the user must be an Administrator.
- **Supermicro**: IPMI over LAN is on by default; use an Administrator account.
- **HPE and other Redfish**: any account that can read the system health. Redfish uses HTTPS (port 443).
- **HPE iLO 4 unlocked**: SSH (port 22) and HTTPS must both be reachable, and the firmware must be the patched 2.77.

Images are built for `linux/amd64` and `linux/arm64`: `ghcr.io/st-devpt/idrac-fan-control:latest`,
or pin a version such as `:2.0`.

## Configuration

Servers, fan settings and Discord are configured in the dashboard. The environment holds the rest:

| Variable | Default | Description |
| --- | --- | --- |
| `WEB_PASSWORD` | empty | Dashboard password. Empty disables sign-in; only do that on a trusted network |
| `CHECK_INTERVAL` | `15` | Seconds between readings and fan commands. Minimum 5 |
| `DISCORD_WEBHOOK_URL` | empty | Default Discord webhook. A webhook pasted in the dashboard takes precedence |
| `METRICS_TOKEN` | empty | Enables `/metrics` for Prometheus, with `Authorization: Bearer <token>` |
| `EMBED_TOKEN` | empty | Enables the read-only `/embed` widget with `?token=<token>` |
| `PORT` | `8080` | HTTP port inside the container |

### Servers in the environment

Servers can also be declared in `docker-compose.yml`, for example to keep credentials in a `.env` file.
They show up in the dashboard like the others, but can only be changed in the environment. Number them:

```yaml
    environment:
      IDRAC_1_NAME: Compute
      IDRAC_1_DRIVER: dell              # dell, supermicro, ilo4-unlocked, redfish, ipmi or demo
      IDRAC_1_HOST: 192.168.1.120       # "local" for IPMI from the server itself
      IDRAC_1_USERNAME: root
      IDRAC_1_PASSWORD: ${COMPUTE_BMC_PASSWORD}
      IDRAC_2_NAME: DL360
      IDRAC_2_DRIVER: redfish
      IDRAC_2_HOST: 192.168.1.121
      IDRAC_2_USERNAME: Administrator
      IDRAC_2_PASSWORD: ${DL360_ILO_PASSWORD}
      IDRAC_2_VERIFY_TLS: "false"       # BMCs ship self-signed certificates
```

The 1.x single-server variables (`IDRAC_HOST`, `IDRAC_USERNAME`, `IDRAC_PASSWORD`, `IDRAC_NAME`)
still work and keep their settings.

### What is stored in `/data`

| File | Contents |
| --- | --- |
| `servers.json` | Servers added in the dashboard, with their BMC passwords (file mode 600, never sent to the browser) |
| `settings.json`, `settings-<server>.json` | Mode, fixed speed, curve, failsafe, ramp-down delay, PCIe setting |
| `history-<server>.json` | The last three hours of readings and events, saved every 5 minutes and on stop |
| `alerts.json` | Discord settings, including the webhook (file mode 600, never sent to the browser) |
| `report-state.json` | Which Discord message the status card is edited into |
| `known_hosts` | SSH host keys of unlocked iLO 4 servers, recorded on first connection |
| `secret` | Key that signs sign-in sessions. Delete it to sign everyone out |

## Fan control

For servers whose type has fan control (Dell, Supermicro, unlocked iLO 4):

| Mode | What happens |
| --- | --- |
| **Automatic** | The BMC runs its factory profile. Loudest, and the fallback for every problem |
| **Fixed** | Every fan at one speed while the CPU is below the failsafe |
| **Curve** | Speed follows the hottest CPU along the points you drag. Double-click adds or removes a point |

**Failsafe temperature.** At or above it, the BMC takes over. Manual control resumes once the CPU
is 3 °C below it, so the fans don't flap at the edge.

**Ramp-down delay.** Fans speed up as soon as the curve asks for it, but slow down only after the lower
speed has been asked for during the whole delay (60 s by default, 0 turns it off). It never runs the fans
*slower* than the curve: it only keeps them faster for a little longer, which smooths out the noise of
short load spikes. If the server settles at a temperature you don't like, raise the curve; the delay does
not change where temperatures settle.

**A starting curve** for a quiet homelab server with Xeon E5 processors:

| CPU | 35 °C | 45 °C | 55 °C | 62 °C | 68 °C | failsafe |
| --- | --- | --- | --- | --- | --- | --- |
| Fans | 12 % | 15 % | 22 % | 35 % | 55 % | 72 °C |

Some BMCs raise fan alarms below about 10 %. Third-party PCIe cards (HBAs, 10 GbE NICs, GPUs) are not
measured by the controller: with any of them installed, stay at 15 % or above, and on Dell leave the
third-party PCIe cooling response **On**.

**Per vendor.** Dell gets one IPMI command for all fans (some 11th-generation servers refuse it; the
controller then finds the fans they accept and sets them one by one). Supermicro is put in *Full* fan mode
and both zones are set; it goes back to *Optimal* when released. On an unlocked iLO 4 the controller caps
every fan over SSH (`fan p N max`), so the iLO's own curve still runs underneath the cap; releasing removes
the cap.

**When something goes wrong** the fans go back to the BMC: no CPU reading, server powered off,
a fan command refused, an exception in the control loop, the server being removed, the container stopping.
The manual command is also re-sent on every cycle, because a BMC reset silently returns to automatic mode.

## Discord

Open the **Discord alerts** section of the dashboard and paste a webhook (Server Settings → Integrations
→ Webhooks → Copy Webhook URL). Everything else is optional.

- **Look**: bot name, avatar, footer, and a colour for each level (error, warning, resolved, info).
- **Mentions**: nobody, `@here`, `@everyone`, a role or a user, only for the levels you choose.
  Text that comes from a BMC can never ping anyone.
- **Cooldown**: minimum time between two alerts of the same kind for the same server.
- **Events**: turn each on or off and write its title and message. A live preview shows the result and
  **Send test** posts it before you save.

| Event | Level | Sent when |
| --- | --- | --- |
| Failsafe reached | warning | The CPU reaches the failsafe and the BMC takes over |
| Failsafe cleared | resolved | Manual control resumes |
| Running hot | warning | The CPU passes the "running hot" temperature (off by default) |
| BMC unreachable | error | Readings fail |
| Fan command refused | error | The BMC rejects a fan command |
| Back to normal | resolved | The BMC answers and accepts commands again |
| Controller error | error | The control loop hits an unexpected error |
| Settings changed | info | Someone applies new settings (off by default) |
| Controller started | info | The container starts (off by default) |
| Status report | info | On a schedule (off by default, see below) |

Placeholders for titles and messages: `{server}` `{host}` `{model}` `{cpu}` `{speed}` `{mode}`
`{reason}` `{error}` `{failsafe}` `{threshold}` `{interval}` `{time}`. Status reports add `{period}`
`{cpu_min}` `{cpu_avg}` `{cpu_max}` `{speed_avg}` `{power_avg}` `{dell_pct}`.

**Status report.** Every few minutes or hours, one card per server: CPU, fans and power now, minimum,
average and maximum over the period, inlet and exhaust air, time spent in Dell mode, trend lines for CPU
and fan speed, and the latest events. By default the same message is edited each time, so the channel
holds one live status card; it can also post a new message each time.

<img alt="Discord status report" src="docs/discord-report.jpg" width="640">

## Prometheus and Grafana

Set `METRICS_TOKEN`, then scrape `/metrics`:

```yaml
scrape_configs:
  - job_name: idrac-fan-control
    authorization:
      credentials: <METRICS_TOKEN>
    static_configs:
      - targets: ["<docker-host>:8080"]
```

| Metric | Labels |
| --- | --- |
| `idrac_up`, `idrac_power_on`, `idrac_dell_control`, `idrac_failsafe_active` | `server`, `name` |
| `idrac_cpu_temperature_celsius`, `idrac_inlet_temperature_celsius`, `idrac_exhaust_temperature_celsius` | `server`, `name` |
| `idrac_fan_speed_percent`, `idrac_power_watts`, `idrac_last_update_timestamp_seconds` | `server`, `name` |
| `idrac_temperature_celsius` | `server`, `name`, `sensor`, `entity` |
| `idrac_fan_rpm` | `server`, `name`, `fan` |

Grafana: Dashboards → New → Import, upload `idrac-fan-control.json` (download it from the **Grafana** page of the dashboard, or `web/grafana.json` in this repository) and pick your
Prometheus data source.

## Homarr and other dashboards

Set `EMBED_TOKEN` and use the read-only widget:

```
http://<docker-host>:8080/embed?server=<id>&token=<EMBED_TOKEN>&theme=dark&bg=solid
```

| Parameter | Values |
| --- | --- |
| `server` | Server id, as in the dashboard URL (`#server=<id>`). Defaults to the first server |
| `token` | `EMBED_TOKEN`. Opens the read-only views only; it cannot sign in or change anything |
| `theme` | `light` or `dark`. Defaults to the viewer's system theme |
| `bg` | `solid` for the theme's background. Transparent by default |

<img alt="Embed widget" src="docs/embed.jpg" width="380">

In **Homarr**, add an *iFrame* widget with that URL, and an app tile pointing at the dashboard with its
status check on `/healthz` (answers `200` while every server is being read).

## Reverse proxy and security

The dashboard can change how a server cools itself. Keep it on your LAN or VPN, set `WEB_PASSWORD`, and
put a reverse proxy with TLS in front if you open it beyond your own machine. With the proxy on the same
host, publish the port on localhost only:

```yaml
    ports:
      - "127.0.0.1:8080:8080"
```

In the proxy, forward to `http://127.0.0.1:8080` and keep the `Host` header (the default in
Nginx Proxy Manager). The session cookie gets the `Secure` flag when the proxy sends
`X-Forwarded-Proto: https`.

Built in: signed `HttpOnly`, `SameSite=Strict` session cookies, slowed sign-in attempts, origin checks on
every change, a strict Content-Security-Policy, request size and time limits, and a container that runs as
uid 1000 with a read-only root filesystem and no capabilities. Details and how to report a vulnerability
are in [SECURITY.md](SECURITY.md).

## Troubleshooting

Use **Test connection** on the server's page (Edit) to see the BMC's answer without saving anything.

| Message | Meaning |
| --- | --- |
| `rsp=0xc1` | The firmware does not have the fan commands (Dell iDRAC 9 3.34.34.34 or later): use the Redfish type |
| `rsp=0xd4` | The BMC user is not an Administrator |
| `Unable to establish IPMI v2 / RMCP+ session` | Wrong address or credentials, or IPMI over LAN is off |
| `Redfish ...: HTTP 401` | Wrong user name or password |
| `Redfish ...: timed out` | The BMC is not reachable on HTTPS from the container |
| `SSH: ... (is the iLO firmware unlocked?)` | The iLO answered, but does not have the `fan` command: stock firmware |
| `ERROR: cannot write to /data` (container log) | The data folder is not owned by uid 1000 |

## Upgrading

```bash
docker compose pull && docker compose up -d
```

**From 1.x:** servers declared with `IDRAC_*` variables keep working, with their settings and history,
and are shown as defined in the environment. To manage one from the dashboard instead, add it there and
remove its variables. The fan mode called "Dell" is now "Automatic"; saved settings are converted.

**From 1.0:** the container runs as uid 1000. Give it the data folder once:

```bash
chown -R 1000:1000 ./data
```

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
python -m unittest              # tests: drivers, control logic, server registry, alerts, HTTP
```

Python 3.10 or newer, no dependencies. The code is in `fanctl/`: `drivers.py` (how each kind of BMC is read
and driven), `control.py` (decisions, no I/O), `server.py` (control loop and server registry), `alerts.py`
(Discord) and `web.py` (HTTP and metrics); `app.py` starts it all. `drivers.py` holds one class per kind of server: adding a vendor
means implementing `read()` and, if it can control fans, `set_speed()` and `set_auto()`. The web pages are
in `web/`, served with a strict Content-Security-Policy, so scripts and styles live in their own files.

## License

[MIT](LICENSE). Bundled fonts: Archivo and IBM Plex Mono, under the SIL Open Font License
(`web/fonts/`).
