# iDRAC Fan Control

**Quiet Dell PowerEdge servers, without cooking them.** A self-hosted web dashboard that takes over
fan control through the iDRAC, follows a temperature curve you draw, and hands control back to Dell
the moment anything looks wrong.

[![CI](https://github.com/ST-DEVPT/idrac-fan-control/actions/workflows/docker.yml/badge.svg)](https://github.com/ST-DEVPT/idrac-fan-control/actions/workflows/docker.yml)
[![Release](https://img.shields.io/github/v/release/ST-DEVPT/idrac-fan-control)](https://github.com/ST-DEVPT/idrac-fan-control/releases)
[![Image](https://img.shields.io/badge/image-ghcr.io-blue)](https://github.com/ST-DEVPT/idrac-fan-control/pkgs/container/idrac-fan-control)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/dashboard-dark.jpg">
  <img alt="Dashboard: live readings, history chart and fan curve" src="docs/dashboard-light.jpg">
</picture>

## Highlights

- **Fixed speed or a fan curve** you drag into shape, with Dell's automatic mode one click away.
- **Fails safe**: a failsafe temperature, missing readings, a refused command, a crash or `docker stop`
  all hand the fans back to the iDRAC.
- **Smooth**: fans speed up at once and slow down only after a delay, so they don't hunt up and down.
- **Several servers** in one dashboard, with three hours of history that survives restarts.
- **Discord**: alerts you can fully customise, plus a status card that keeps itself up to date.
- **Prometheus metrics**, a ready-made **Grafana** dashboard and a widget for **Homarr**.
- **Small and private**: one Python file, no dependencies, no third-party requests, runs as non-root.

## Contents

[Quick start](#quick-start) ·
[Configuration](#configuration) ·
[Fan control](#fan-control) ·
[Discord](#discord) ·
[Prometheus and Grafana](#prometheus-and-grafana) ·
[Homarr and other dashboards](#homarr-and-other-dashboards) ·
[Reverse proxy and security](#reverse-proxy-and-security) ·
[Compatibility and troubleshooting](#compatibility-and-troubleshooting) ·
[Upgrading](#upgrading) ·
[Development](#development)

## Quick start

**1. Prepare the iDRAC**

- The iDRAC user must be an **Administrator**.
- Enable **IPMI over LAN**: iDRAC Settings → Network → IPMI Settings.
- Optional check from any Linux machine:

  ```bash
  ipmitool -I lanplus -H <idrac-ip> -U <user> -P <password> sdr type temperature
  ```

**2. Get the compose file and a data folder**

```bash
mkdir idrac-fan-control && cd idrac-fan-control
curl -O https://raw.githubusercontent.com/ST-DEVPT/idrac-fan-control/main/docker-compose.yml
mkdir data && chown 1000:1000 data
```

**3. Set the iDRAC address, its credentials and a dashboard password**

Edit `IDRAC_HOST`, `IDRAC_USERNAME`, `IDRAC_PASSWORD` and `WEB_PASSWORD` in `docker-compose.yml`. Then:

```bash
docker compose up -d
```

**4. Open `http://<docker-host>:8080`** and sign in with `WEB_PASSWORD`.

Images are built for `linux/amd64` and `linux/arm64`: `ghcr.io/st-devpt/idrac-fan-control:latest`,
or pin a version such as `:1.2`.

## Configuration

Everything about the fans and Discord is set in the dashboard. The environment only holds what the
dashboard should not: addresses, passwords and tokens.

| Variable | Default | Description |
| --- | --- | --- |
| `IDRAC_HOST` | `local` | iDRAC IP or hostname. `local` when the container runs on the server itself (see [Upgrading](#upgrading)). `demo` for simulated data |
| `IDRAC_USERNAME` | `root` | iDRAC user (must be an Administrator) |
| `IDRAC_PASSWORD` | `calvin` | iDRAC password. Given to `ipmitool` through its environment, never on the command line |
| `IDRAC_NAME` | the host | Name shown in the dashboard and in Discord |
| `WEB_PASSWORD` | empty | Dashboard password. Empty disables sign-in; only do that on a trusted network |
| `CHECK_INTERVAL` | `15` | Seconds between readings and fan commands. Minimum 5 |
| `DISCORD_WEBHOOK_URL` | empty | Default Discord webhook. A webhook pasted in the dashboard takes precedence |
| `METRICS_TOKEN` | empty | Enables `/metrics` for Prometheus, with `Authorization: Bearer <token>` |
| `EMBED_TOKEN` | empty | Enables the read-only `/embed` widget with `?token=<token>` |
| `PORT` | `8080` | HTTP port inside the container |

### Several servers

Number the variables. Each server gets its own tab, settings, history and alerts:

```yaml
    environment:
      IDRAC_1_NAME: Compute
      IDRAC_1_HOST: 192.168.1.120
      IDRAC_1_USERNAME: root
      IDRAC_1_PASSWORD: ${COMPUTE_IDRAC_PASSWORD}
      IDRAC_2_NAME: Storage
      IDRAC_2_HOST: 192.168.1.121
      IDRAC_2_USERNAME: root
      IDRAC_2_PASSWORD: ${STORAGE_IDRAC_PASSWORD}
```

### What is stored in `/data`

| File | Contents |
| --- | --- |
| `settings.json`, `settings-<server>.json` | Mode, fixed speed, curve, failsafe, ramp-down delay, PCIe setting |
| `history-<server>.json` | The last three hours of readings and events, saved every 5 minutes and on stop |
| `alerts.json` | Discord settings, including the webhook (file mode 600, never sent to the browser) |
| `report-state.json` | Which Discord message the status card is edited into |
| `secret` | Key that signs sign-in sessions. Delete it to sign everyone out |

## Fan control

| Mode | What happens |
| --- | --- |
| **Dell** | The iDRAC runs its factory profile. Loudest, and the fallback for every problem |
| **Fixed** | Every fan at one speed while the CPU is below the failsafe |
| **Curve** | Speed follows the hottest CPU along the points you drag. Double-click adds or removes a point |

**Failsafe temperature.** At or above it, the iDRAC takes over. Manual control resumes once the CPU
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

Some iDRACs raise fan alarms below about 10 %. Third-party PCIe cards (HBAs, 10 GbE NICs, GPUs) are not
measured by the controller: with any of them installed, stay at 15 % or above and leave Dell's PCIe cooling
response **On**.

**When something goes wrong** the fans go back to the iDRAC: no CPU reading, server powered off,
a fan command refused, an exception in the control loop, the container stopping. The manual command is
also re-sent on every cycle, because an iDRAC reset silently returns to Dell mode.

## Discord

Open the **Discord alerts** section of the dashboard and paste a webhook (Server Settings → Integrations
→ Webhooks → Copy Webhook URL). Everything else is optional.

- **Look**: bot name, avatar, footer, and a colour for each level (error, warning, resolved, info).
- **Mentions**: nobody, `@here`, `@everyone`, a role or a user, only for the levels you choose.
  Text that comes from the iDRAC can never ping anyone.
- **Cooldown**: minimum time between two alerts of the same kind for the same server.
- **Events**: turn each on or off and write its title and message. A live preview shows the result and
  **Send test** posts it before you save.

| Event | Level | Sent when |
| --- | --- | --- |
| Failsafe reached | warning | The CPU reaches the failsafe and the iDRAC takes over |
| Failsafe cleared | resolved | Manual control resumes |
| Running hot | warning | The CPU passes the "running hot" temperature (off by default) |
| iDRAC unreachable | error | Readings fail |
| Fan command refused | error | The iDRAC rejects a fan command |
| Back to normal | resolved | The iDRAC answers and accepts commands again |
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

Grafana: Dashboards → New → Import, upload `docs/grafana/idrac-fan-control.json` and pick your
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

## Compatibility and troubleshooting

Manual fan control uses Dell's OEM IPMI commands. Whether a server accepts them depends on the iDRAC
firmware, not on the model:

| iDRAC | Manual fan control |
| --- | --- |
| iDRAC 6, 7 and 8 (11th to 13th generation) | Supported |
| iDRAC 9 up to firmware 3.30.30.30 | Supported |
| iDRAC 9 from firmware 3.34.34.34, iDRAC 10 | Removed by Dell |

Some 11th-generation servers reject the "all fans" selector. The controller then finds the fan
identifiers they accept and sets the fans one by one; the event log says so.

| In the event log | Meaning |
| --- | --- |
| `rsp=0xc1` | The firmware does not have the fan commands (iDRAC 9 3.34.34.34 or later) |
| `rsp=0xd4` | The iDRAC user is not an Administrator |
| `Unable to establish IPMI v2 / RMCP+ session` | Wrong address or credentials, or IPMI over LAN is off |
| `ERROR: cannot write to /data` (container log) | The data folder is not owned by uid 1000 |

## Upgrading

```bash
docker compose pull && docker compose up -d
```

**From 1.0:** the container now runs as uid 1000. Give it the data folder once:

```bash
chown -R 1000:1000 ./data
```

With `IDRAC_HOST=local`, the container needs `/dev/ipmi0` and root to open it:

```yaml
    user: "0:0"
    devices:
      - /dev/ipmi0:/dev/ipmi0
```

Settings, curves and history carry over. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Development

```bash
IDRAC_HOST=demo python app.py   # dashboard on http://localhost:8080 with simulated data
python test_app.py              # self-check: parsing, control logic, sessions, HTTP, alerts
```

Python 3.10 or newer, no dependencies. The web pages are in `web/`, served with a strict
Content-Security-Policy, so scripts and styles live in their own files.

## License

[MIT](LICENSE). Bundled fonts: Archivo and IBM Plex Mono, under the SIL Open Font License
(`web/fonts/`).
