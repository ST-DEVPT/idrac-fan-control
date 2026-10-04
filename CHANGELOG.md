# Changelog

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
- Metrics gain a `driver` label and `idrac_fan_percent` for BMCs that report fan speed in percent.
- Redfish requests refuse redirects, so credentials never follow a redirect to another host.

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
