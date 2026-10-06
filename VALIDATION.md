# Validation — Pars Agent 0.1.0

Date: 2026-10-06

Executed in Python 3.12 on Linux:

- Backend: **29 passed**, including concurrent claim-once, cross-user isolation, CSRF/origin, account/terminal binding, stale quotes, risk admission, uncertain-order lock, stop/cancellation, idempotent results/deals, encrypted Telegram tokens and failed-login throttling.
- Frontend DOM integration: boot/auth visibility, actual supplied-candle rendering, escaped account/idea content, navigation, settings/CSRF and start/stop controls passed.
- JavaScript syntax check passed.
- Shell syntax checks for install/backup scripts passed.
- Docker Compose YAML parsed successfully.
- Installer rejection tests passed: invalid hostname/repository/branch/path and refusal to overwrite an existing directory.
- Update script rejects a directory that is not an installed Git checkout.
- GitHub Actions workflow was added; its remote execution is pending publication.
- Configuration helper: invalid domains rejected; 32-byte random encryption key; file mode 0600; live disabled by default; existing key cannot be overwritten.

One dependency warning occurred: Starlette's current test client deprecates its httpx transport. It did not cause a test failure.

Not executed in this environment:

- MQL5 compilation / MetaEditor (EX5 is not supplied).
- Broker connection, order execution, lot sizing against actual broker specifications, partial fills or live disconnection recovery.
- Real Telegram delivery or daily delivery scheduling on a deployed server.
- Docker image build, Caddy certificate issuance, VPS restart/backup restoration.
- Strategy profitability backtests, load tests or public-service security/operations review.

The source implements these paths where described in README; unexecuted integrations must be validated on the intended deployment. Software tests do not establish investment performance or production approval.
