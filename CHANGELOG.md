# Changelog

## [2.1.0]

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

## [2.0.1]

- Redfish: follow redirects that stay on the same BMC. HPE iLO 4 answers `/redfish/v1/Chassis` with a
  308 to the trailing-slash path, which showed as "HTTP 308 Moved Permanently". Redirects to any other
  host are still refused, so the password never leaves the BMC.

## [2.0.0]

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

## [1.2.0]

- Discord status reports: temperatures, fan speed, power, min/avg/max, time in Dell mode, trend lines
  and recent events for every server, on a schedule. By default one message is edited in place.

## [1.1.0]

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

## [1.0.0]

- First release: Dell, fixed and curve modes, failsafe, history, sign-in page.
