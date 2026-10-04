# Security

## Reporting

Please report vulnerabilities privately through GitHub's
[security advisories](https://github.com/ST-DEVPT/idrac-fan-control/security/advisories/new),
not in public issues.

## Design

This dashboard can change how a server cools itself, so it is built to be safe on a trusted network.
It is not meant to be exposed to the internet.

- **Sign-in**: a single password (`WEB_PASSWORD`). Sessions are HMAC-signed cookies with an expiry,
  `HttpOnly` and `SameSite=Strict`, and `Secure` behind a TLS proxy that sends `X-Forwarded-Proto: https`.
  Changing the password or deleting `/data/secret` invalidates every session.
- **Brute force**: failed sign-ins take one second each and are serialised, about one guess per second
  overall. A flood of wrong passwords therefore also slows legitimate sign-ins.
- **CSRF**: state-changing requests must be `application/json` and, when the browser sends an `Origin`,
  it must match `Host` or `X-Forwarded-Host`.
- **Browser hardening**: strict Content-Security-Policy (`script-src 'self'`, no inline scripts),
  `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`. Only the read-only `/embed` page
  may be framed.
- **Tokens**: `EMBED_TOKEN` opens the read-only views (`/embed`, `GET /api/state`) and never a change.
  `METRICS_TOKEN` opens `/metrics` only. Neither can sign in.
- **Credentials**: iDRAC passwords are read from the environment, passed to `ipmitool` through its
  environment (never on the command line), and never sent to the browser. `ipmitool` gets no other
  environment variables.
- **Input**: settings are validated by type and range; request bodies are capped at 10 kB and
  connections time out after 30 seconds. Static files come from a fixed allowlist.
- **Fan safety**: any error, missing reading, crash in the control loop or container stop hands the fans
  back to the iDRAC's own control.
- **Container**: runs as uid 1000, works with a read-only root filesystem, `cap_drop: [ALL]` and
  `no-new-privileges`.
- **No third parties**: fonts and assets are bundled; the only outbound requests are the iDRAC and,
  if configured, the Discord webhook.

Known limits: Python's `http.server` is minimal, so put a reverse proxy with TLS in front when the
dashboard is reachable beyond your own machine, and keep the port off untrusted networks.
