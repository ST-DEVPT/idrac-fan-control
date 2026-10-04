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
- **Servers added in the dashboard**: their BMC passwords are stored in `/data/servers.json` (mode 600)
  and never returned to the browser; editing a server without typing a password keeps the stored one.
  Addresses are validated as host names or IPs, and every tool gets them as a separate argument, never
  through a shell. Redfish requests refuse redirects, so the `Authorization` header cannot be sent to another
  host. "Test connection" lets a signed-in user make the container contact an address of their choice,
  which is the point of the feature: keep sign-in enabled.
- **TLS to BMCs**: certificates are not verified by default, because BMCs ship self-signed ones. Turn on
  verification per server when the BMC has a trusted certificate.
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
