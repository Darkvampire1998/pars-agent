#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
usage() {
  printf 'Usage: bash deploy/install.sh (--domain HOST | --ip PUBLIC_IP) [--port 443] [--reconfigure] [--host-build-network]\n'
  printf 'Legacy: bash deploy/install.sh trade.example.com\n'
}
config_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain|--ip|--port)
      [[ $# -ge 2 ]] || fail "Missing value for $1"
      config_args+=("$1" "$2"); shift 2;;
    --reconfigure|--host-build-network)
      config_args+=("$1"); shift;;
    --help|-h) usage; exit 0;;
    --*) fail "Unknown option: $1";;
    *) config_args+=(--domain "$1"); shift;;
  esac
done
[[ ${#config_args[@]} -gt 0 ]] || { usage; exit 2; }
command -v docker >/dev/null || fail 'Install Docker Engine first.'
docker compose version >/dev/null || fail 'Docker Compose v2 is required.'
command -v python3 >/dev/null || fail 'python3 is required.'
command -v curl >/dev/null || fail 'curl is required.'
python3 deploy/configure.py "${config_args[@]}" --reuse
docker compose config --quiet
python3 deploy/check-network.py
if ! docker compose up -d --build --wait --wait-timeout 180; then
  printf '\nInstallation did not complete. Settings/data were retained.\n' >&2
  printf 'For build-only DNS errors while host checks passed, retry this command with --host-build-network.\n' >&2
  printf 'For service health/certificate errors: bash deploy/manage.sh logs\n' >&2
  exit 1
fi
panel_url="$(python3 - <<'PY'
from pathlib import Path
print(next(line.split('=', 1)[1] for line in Path('.env').read_text().splitlines() if line.startswith('PUBLIC_URL=')))
PY
)"
if ! curl --proto '=https' --tlsv1.2 --fail --silent --show-error --noproxy '*' \
  --retry 6 --retry-all-errors --retry-delay 3 --connect-timeout 5 --max-time 10 \
  "$panel_url/api/health" >/dev/null; then
  printf '\nContainers are running, but HTTPS is not verified yet. Panel target: %s\n' "$panel_url" >&2
  printf 'Keep TCP 80 and the chosen HTTPS port open in the VPS/provider firewall.\n' >&2
  printf 'Check certificate/network errors: bash deploy/manage.sh logs\n' >&2
  exit 1
fi
printf '\nPanel is responding over verified HTTPS: %s\n' "$panel_url"
printf 'Open this address in a browser and create your panel account.\n'
printf 'Management menu: sudo bash %s/deploy/manage.sh\n' "$PWD"
