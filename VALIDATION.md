# Validation — Pars Agent 0.1.0

Date: 2026-10-06

Executed in Python 3.12 on Linux:

- Python: **54 passed**, including concurrent claim-once, cross-user isolation, CSRF/origin, account/terminal binding, stale quotes, risk admission, uncertain-order lock, stop/cancellation, idempotent results/deals, encrypted Telegram tokens and failed-login throttling.
- Frontend DOM integration: boot/auth visibility, actual supplied-candle rendering, escaped account/idea content, navigation, settings/CSRF and start/stop controls passed.
- JavaScript syntax check passed.
- Shell syntax checks for install/backup scripts passed.
- Docker Compose YAML parsed successfully.
- Installer rejection tests passed: invalid hostname/repository/branch/path and refusal to overwrite an existing directory.
- Update script rejects a directory that is not an installed Git checkout.
- The initial GitHub Actions run on commit c7bfafe succeeded, including Docker build. The updated workflow additionally validates domain/IP Caddy settings and the host-network build overlay; its new run is pending publication.
- Configuration helper: invalid domains rejected; 32-byte random encryption key; file mode 0600; live disabled by default; existing key cannot be overwritten.
- IP/port configuration: public IPv4/IPv6 accepted; private/reserved/multicast/invalid addresses and invalid ports rejected. Address migration retains the encryption key, previously encrypted Telegram data, operator settings and trading/registration policies.
- Failed-install resume rejects an unrelated repository before dependency installation. Existing directories are retained.
- Installer behavior tested with controlled Docker/curl substitutes: build failure and TLS failure do not report successful installation; success requires a verified HTTPS endpoint.
- Network preflight distinguishes DNS/TLS failure from an HTTP status error; no system or Docker DNS files are modified.
- Domain and IP Caddyfiles adapted and validated using official Caddy v2.11.7, with its release SHA-512 checksum verified. IP policy uses the public ACME issuer and shortlived profile, without a local/self-signed issuer. Port 80 handles HTTP-01; custom HTTPS port redirects are configured.

One dependency warning occurred: Starlette's current test client deprecates its httpx transport. It did not cause a test failure.

Not executed in this environment:

- MQL5 compilation / MetaEditor (EX5 is not supplied).
- Broker connection, order execution, lot sizing against actual broker specifications, partial fills or live disconnection recovery.
- Real Telegram delivery or daily delivery scheduling on a deployed server.
- IP certificate issuance on the user's VPS, VPS restart/backup restoration, and this installer against the user's real network/firewall.
- MT5 WebRequest on a custom HTTPS port (use standard port 443 for the supplied bridge).
- Strategy profitability backtests, load tests or public-service security/operations review.

The source implements these paths where described in README; unexecuted integrations must be validated on the intended deployment. Software tests do not establish investment performance or production approval.
