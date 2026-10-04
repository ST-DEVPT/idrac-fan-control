# Changelog

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
