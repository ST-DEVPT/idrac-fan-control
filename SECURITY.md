# Security

## Reporting

Please report vulnerabilities privately through GitHub's
[security advisories](https://github.com/ST-DEVPT/rack-fan-control/security/advisories/new),
not in public issues.

## Design

This dashboard can change how a server cools itself, so it is built to be safe on a trusted network.
It is not meant to be exposed to the internet.

- **Sign-in**: a single password (`WEB_PASSWORD`). Sessions are HMAC-signed cookies (expiry, role,
  generation and a random id), `HttpOnly` and `SameSite=Strict`, and `Secure` behind a TLS proxy that sends
  `X-Forwarded-Proto: https`. Signing out revokes that session id on the server, so a copied cookie stops
  working too. **Sign out everywhere** (Backup page) ends every session at once. Changing a password or
  deleting `/data/secret` does the same.
- **Accounts**: `WEB_PASSWORD` is the admin account; `VIEW_PASSWORD` adds a read-only one, whose session
  is refused every change and the backup download. The role is part of the signed session token.
- **Brute force**: each failed sign-in takes one second, and an address with 5 failures in 10 minutes must
  wait (HTTP 429). Other addresses are not affected; the table of addresses is bounded. `X-Forwarded-For`
  is only believed with `TRUST_PROXY`, only when the request comes from a trusted proxy (`TRUSTED_PROXIES`,
  by default loopback and Docker's networks), and it is read from the right: the client writes the left
  part, so a client that reaches the app directly, or that adds addresses of its own, cannot pick the
  address it is counted under.
- **Load**: at most 64 requests are handled at once; a flood of slow or idle connections waits its turn
  and is dropped after 10 s, instead of using up threads and memory. Request bodies are refused before
  being read when their length is negative, malformed or over the limit.
- **Audit**: settings and server changes are written to the event log with the account and address.
- **CSRF**: state-changing requests must be `application/json` and, when the browser sends an `Origin`,
  it must match `Host` or `X-Forwarded-Host`.
- **Browser hardening**: strict Content-Security-Policy (`script-src 'self'`, no inline scripts),
  `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`. Only the read-only `/embed` page
  may be framed.
- **Tokens**: a widget token opens the widget page (`/embed`) and `GET /api/widget`, which returns only
  the fields chosen on the Homarr page: never the BMC address, settings, events or error messages. A
  metrics token opens `/metrics` only. Neither can sign in or make a change. Tokens created in the
  dashboard are stored as a SHA-256 hash (`tokens.json`, mode 600), shown once, compared in constant time,
  and can be revoked one by one; `EMBED_TOKEN` and `METRICS_TOKEN` from the environment work alongside.
- **Outbound requests**: only to the BMCs, the alert channels you configure, and GitHub's API twice a day
  for the latest release (`UPDATE_CHECK=false` turns that off). Nothing about your servers is sent.
- **Credentials**: iDRAC passwords are read from the environment, passed to `ipmitool` through its
  environment (never on the command line), and never sent to the browser. `ipmitool` gets no other
  environment variables.
- **Servers added in the dashboard**: their BMC passwords are stored in plain text in `/data/servers.json`
  (mode 600, readable only by the container's user) and never returned to the browser. Encrypting them with
  a key kept on the same host would add little; protect the data folder and its backups instead; editing a server without typing a password keeps the stored one.
  Addresses are validated as host names or IPs, and every tool gets them as a separate argument, never
  through a shell. Redfish requests follow a redirect only on the same BMC (scheme, host and port), so the
  `Authorization` header cannot be sent to another host; alert channels follow the same rule, and refuse
  link-local addresses (cloud metadata). Viewers cannot read the BMC account names. "Test connection" lets a signed-in user make the container contact an address of their choice,
  which is the point of the feature: keep sign-in enabled.
- **Network scan**: only an admin can run it, only on private ranges and at most 1024 addresses at a time.
  It sends one HTTPS request (`/redfish/v1`, no credentials) and one IPMI capabilities probe (UDP 623, no
  credentials) to each address.
- **TLS to BMCs**: BMCs ship self-signed certificates, so by default the first certificate a BMC shows is
  remembered (trust on first use, `redfish-pins.json`) and any later change is refused: the BMC password goes
  with every Redfish request, and a changed certificate may mean someone in the middle. A replaced
  certificate is accepted from the server's page or by saving the server. Turn on verification per server
  when the BMC has a certificate from a trusted authority. SSH to unlocked iLO 4 pins host keys the same way.
- **Input**: settings are validated by type and range; request bodies are capped at 10 kB and
  connections time out after 30 seconds. Static files come from a fixed allowlist.
- **Fan safety**: any error, missing or faulty reading, failed fan, ignored command, crash or stall in the
  control loop, and container stop hands the fans back to the BMC's own control. If the container is killed
  without warning (`kill -9`, out of memory), `scripts/fan-guard.sh` hands them back from the host's cron.
  If the Docker host itself loses power or network, the BMC keeps the last manual speed: the minimum speed
  (20 % by default) and the "BMC unreachable" alert are the mitigation.
- **Diagnostics** downloads leave out passwords, the BMC address (also from events and errors), serial
  numbers and network details.
- **Backups** with passwords unlock every BMC in them; they are only produced when explicitly asked for.
- **Container**: runs as uid 1000, works with a read-only root filesystem, `cap_drop: [ALL]` and
  `no-new-privileges`.
- **No third parties**: fonts and assets are bundled, nothing is loaded from elsewhere.

Known limits: Python's `http.server` is minimal, so put a reverse proxy with TLS in front when the
dashboard is reachable beyond your own machine, and keep the port off untrusted networks.
