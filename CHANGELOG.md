# Changelog

Versions follow [semantic versioning](https://semver.org): a major version (`:2`) never removes a setting
or changes what an existing one does, so pinning the image to it is safe.

## [2.3.0] - 2026-10-05

Hardware safety: when the controller does not know, the BMC decides.

- **No CPU reading means automatic.** Another sensor (inlet air, a DIMM) no longer stands in for the CPU.
- **An unreadable BMC gets the fans back** as soon as it takes a command, instead of keeping the last
  manual speed with nobody watching.
- **Lost or faulty sensors and failed fans trip the failsafe**, and so does a BMC that ignored a fan
  command (manual control is tried again after 10 minutes).
- **The failsafe holds for at least 5 minutes**, and manual control resumes from 50 %, easing down, so
  fans no longer flap between the BMC and the controller.
- **Minimum speed is 20 % by default and 10 % at the lowest**; the failsafe goes up to 90 °C. Older
  settings outside these limits are brought inside them, never refused.
- **Supermicro**: the fan mode the BMC had (HeavyIO on a GPU box, say) is read once, kept on disk and
  restored, instead of always Optimal; never below 25 %; X9 is no longer claimed (it needs other commands).
- **HPE iLO 4 unlocked**: a failed release is now reported instead of silently leaving a low cap.
- **docker stop releases every server at once** after stopping every loop, and the example compose file
  allows 60 s. The watchdog no longer waits on a stuck loop's lock.
- BMC warning thresholds that could not be read are retried every 10 minutes, and the dashboard says so.
- **Smart mode** takes the fans' own power out of the load signal and carries the learned map at most
  15 % past its last point. It was re-checked against a harsher simulation: readings every 15 s and 10 s
  old, fans that stall below 8 %, heat transfer that saturates with airflow, two CPUs sharing air.
- The control loop is split into read, protect, choose speed, command and report.

## [2.2.1] - 2026-10-05

Fixes from an independent review of 2.2.0.

- **fan-guard** found the container by a fixed name, `idrac_fan_web`, which the example compose file does
  not use: with it, the guard took a running Fan Control for dead and handed the fans to the BMC every
  minute. It now finds the container by its image, leaves a removed container alone, also acts when Docker
  is down, and covers Supermicro and HPE iLO 4 unlocked as well as Dell. It refuses an `.env` others can
  write and keeps its state in `/run`.
- **Data files are written atomically and durably**: a unique temporary file, flushed to disk, renamed,
  and the folder flushed too. Two writers can no longer collide on one temporary file, and files with
  passwords or tokens are private from the first byte.
- **A corrupt or invalid settings file now means automatic fans**, logged once, instead of falling back
  to the default curve. A corrupt `servers.json`, `alerts.json` or `tokens.json` is kept aside as
  `<name>.corrupt-<time>` instead of being overwritten by the next save.
- A negative or malformed `Content-Length` is refused before anything is read.
- The server page and history are copied under the lock and sent after it, so a slow browser can no
  longer hold up a control loop. Events are logged under the lock.
- A backup without secrets no longer contains the ntfy, Gotify or webhook addresses and tokens.

## [2.2.0] - 2026-10-05

- **Watchdog.** A control loop that stops turning for 3 minutes hands every fan back to its BMC and
  restarts the process. The health check follows the control loops (`/livez`), not the BMCs.
  `scripts/fan-guard.sh`, run from the host's cron, hands Dell fans back to the iDRAC when the container
  itself is gone.
- **Fans are checked.** New alerts for a fan that stops while the others spin, a BMC whose fans do not
  follow a 20-point speed change within 30 s, a failsafe that lasts longer than a set time, and inlet air
  above a warning temperature.
- **Alerts on ntfy, Gotify and any webhook**, besides Discord. The page is now called Alerts.
- **Tokens in the dashboard.** Widget and metrics tokens are created and revoked on the Homarr and
  Prometheus pages, shown once and stored as a hash.
- **Schedule.** Up to 8 profiles by day and time, each with a speed cap and/or a smart mode target.
- **Diagnostics.** A server page downloads what its BMC answers, without passwords or addresses, for bug
  reports about untested hardware.
- **Smart mode metrics** in Prometheus, and two Grafana panels that show why it chose each speed.
- The sidebar shows when a newer release is out (`UPDATE_CHECK=false` turns it off).
- The docs pin the image to its major version, `:2`.
- A test fails on any page text without a Portuguese translation; the 40 it found are translated.
- Code: `metrics.py`, `widgets.py`, `backup.py`, `tokens.py` and `updates.py` come out of `web.py`;
  `editor.js` out of `app.js`.

## [2.1.0] - 2026-10-05

- **Smart mode, rebuilt.** It learns the fan speed each heat load needs on your server and goes straight
  there when the load changes. It judges temperatures where their trend puts them 40 s on, so heat is met
  before it arrives. It keeps a PI trim on top, which the learned map slowly takes over. Heat load is power
  draw over the room between the inlet air and the target, so a warm day or a new target needs no
  relearning.
- Smart mode rides out bursts: fans fall only after lower demand has lasted the ramp-down delay. In a
  simulated job that runs 30 s of every 90 s, the fans travel less than half as far.
- Smart mode boosts the fans when anything heads for its trip point, and quiet hours give way gradually
  near a trip point. In simulation the failsafe no longer trips under a quiet-hours cap that is too low
  for the load.
- Smart mode no longer chases the inlet air temperature, which no fan speed can lower.
- The server page shows what smart mode is doing (trend, learned speed) and draws what it has learned,
  with a button to forget it. The map is kept in `smart-<server>.json` in the data folder.
- The event log records speed changes from 5 % on, so slow ramps no longer push everything else out.
- Portuguese: text that wraps over several lines in the page is translated too.
- **Homarr**: choose what widgets may show (status, CPU, fans, power, inlet, exhaust, chart, model). The
  embed token now reads only those, through the new `/api/widget`; it no longer opens `/api/state` or
  `/api/history`, which carried the BMC address, settings and events.
- **Native Homarr 2.0 widget**: a Custom Widget to import, which Homarr fetches itself with the token as a
  Bearer credential. It has options for the server or the whole rack, °C or °F, the chart and the air.
- The iFrame widget fits any frame: the chart gives way first, then the footer, instead of being cut. It
  can show the whole rack, and only the fields you pick.
- `/healthz` answers `503` when a BMC stops answering, not only when the controller stalls, so a Homarr
  app tile goes red when it should.
- Fixed: opening the Prometheus, Grafana or Homarr page directly could leave it empty.

## [2.0.1] - 2026-10-05

- Redfish: follow redirects that stay on the same BMC. HPE iLO 4 answers `/redfish/v1/Chassis` with a
  308 to the trailing-slash path, which showed as "HTTP 308 Moved Permanently". Redirects to any other
  host are still refused, so the password never leaves the BMC.

## [2.0.0] - 2026-10-05

- Supports more than Dell: Supermicro (fan control, experimental), HPE iLO 4 with unlocked firmware
  (fan caps over SSH, experimental), any Redfish BMC such as HPE iLO 4/5/6 and Lenovo XCC, and any IPMI
  BMC (monitoring only).
- Servers are added, tested, edited and removed in the dashboard. Environment variables still work and
  gain `IDRAC_<n>_DRIVER` and `IDRAC_<n>_VERIFY_TLS`.
- Redesign: sidebar with every server, an overview of the whole rack, a page per server and a setup
  assistant with a connection test.
- Prometheus, Grafana and Homarr each have their own page: generated scrape config and live metrics,
  dashboard download with import steps, and a widget address builder with a live preview.
- "Dell" mode is now "Automatic"; saved settings are converted.
- **Renamed for what it has become.** The project is "Fan Control" (repository `rack-fan-control`).
  Metrics are `fanctl_*` instead of `idrac_*` (`idrac_dell_control` is now `fanctl_bmc_control`), with a
  `driver` label and a new `fanctl_fan_percent`; re-import the Grafana dashboard. Servers in the environment
  use `SERVER_<n>_*`; the 1.x `IDRAC_*` variables keep working.
- Redfish requests refuse redirects, so credentials never follow a redirect to another host.
- **Smart mode**: a PI controller that finds the slowest speed holding the CPU at a target while keeping
  the exhaust air and every sensor with a BMC warning threshold clear of their limits.
- **Protection beyond the CPU**: the BMC also takes over on hot exhaust air and when any sensor (PCIe cards,
  disks, DIMMs, RAID controller) nears the warning threshold its own BMC defines. Minimum fan speed. Dry run.
- **Quiet hours**: cap the fan speed between two times of day (`TZ` sets the time zone).
- **History for 24 hours and 7 days**, as 5-minute averages kept on disk.
- Curve presets (quiet, balanced, cool, storage) and copying the curve of another server.
- **Network discovery**: scan a private range for BMCs answering Redfish (HTTPS) or IPMI (UDP 623), with the
  suggested type for each, then add them in a click. **Detect** asks a single BMC for its vendor and firmware.
- **Read-only account** with `VIEW_PASSWORD`; changes are attributed in the event log; failed sign-ins are
  limited per address (`TRUST_PROXY` to believe `X-Forwarded-For`).
- **Backup**: export and import servers, settings and Discord, with passwords only when asked for.
- Portuguese and °F, chosen per browser.
- Supermicro switches to Full fan mode once instead of on every cycle. A server being removed or edited can
  no longer receive a fan command after it was handed back to the BMC.
- Code split into the `fanctl` package; tests use `unittest` (`python -m unittest`); CI starts the image and
  checks it serves pages and a demo server before publishing.

## [1.2.0] - 2026-10-04

- Discord status reports: temperatures, fan speed, power, min/avg/max, time in Dell mode, trend lines
  and recent events for every server, on a schedule. By default one message is edited in place.

## [1.1.0] - 2026-10-04

- Several servers in one dashboard (`IDRAC_1_HOST`, `IDRAC_2_HOST`, ...), with tabs.
- Ramp-down delay: fans speed up at once and slow down only after demand stays lower.
- Failsafe hysteresis: manual control resumes 3 °C below the failsafe temperature.
- History and events survive restarts (`/data/history-<server>.json`).
- Discord alerts, configured in the dashboard: webhook, bot name, avatar, footer, colours, mentions,
  cooldown, nine events with editable title and message templates, live preview and test sends.
- Prometheus `/metrics` (`METRICS_TOKEN`) and a Grafana dashboard in `docs/grafana`.
- Read-only `/embed` widget for Homarr and other dashboards (`EMBED_TOKEN`).
- Per-fan commands for 11th-generation servers that refuse the "all fans" selector.
- Fonts bundled: the dashboard makes no third-party requests.
- Fixed a race between saving settings and the control loop that could briefly hand the fans to Dell mode.
- Security: strict Content-Security-Policy, origin check on POST, request size and time limits,
  container runs as uid 1000 with a read-only root filesystem.

## [1.0.0] - 2026-10-04

- First release: Dell, fixed and curve modes, failsafe, history, sign-in page.
