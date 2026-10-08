#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
# shellcheck source=deploy/runtime.sh
source deploy/runtime.sh
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
usage() {
  printf 'Usage: bash deploy/install.sh (--domain HOST | --ip PUBLIC_IP) [--port 443] [--reconfigure] [--source-build|--host-build-network]\n'
  printf 'Default: download a tested ready image; no pip runs on this VPS.\n'
}
config_args=()
mode=image
image=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain|--ip|--port)
      [[ $# -ge 2 ]] || fail "Missing value for $1"
      config_args+=("$1" "$2"); shift 2;;
    --image)
      [[ $# -ge 2 ]] || fail "Missing value for $1"
      image="$2"; shift 2;;
    --source-build|--host-build-network)
      mode=source; config_args+=("$1"); shift;;
    --reconfigure) config_args+=("$1"); shift;;
    --help|-h) usage; exit 0;;
    --*) fail "Unknown option: $1";;
    *) config_args+=(--domain "$1"); shift;;
  esac
done
[[ ${#config_args[@]} -gt 0 ]] || { usage; exit 2; }
[[ -z "$image" || "$mode" == image ]] || fail 'Choose --image or --source-build, not both.'
command -v docker >/dev/null || fail 'Install Docker Engine first.'
docker compose version >/dev/null || fail 'Docker Compose v2 is required.'
command -v python3 >/dev/null || fail 'python3 is required.'
command -v curl >/dev/null || fail 'curl is required.'
command -v timeout >/dev/null || fail 'GNU timeout is required.'
if [[ "$mode" == image ]]; then
  [[ -d .git ]] || fail 'Ready mode needs a Git checkout. For a ZIP, pass --source-build.'
  [[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail 'Tracked files changed; use --source-build or a clean checkout.'
  revision="$(git rev-parse HEAD)"
  [[ -n "$image" ]] || image="$(image_for_commit "$revision")"
  config_args+=(--image "$image")
  compose_version="$(docker compose version --short)"
  python3 - "$compose_version" <<'PY'
import re, sys
parts = re.match(r'^v?(\d+)\.(\d+)\.(\d+)', sys.argv[1])
if not parts or tuple(map(int, parts.groups())) < (2, 24, 4):
    raise SystemExit('Docker Compose >= 2.24.4 is required for ready-image mode. Update the Compose plugin or use --source-build.')
PY
fi
printf '\n[1/4] Preparing configuration; preserving existing secrets.\n'
python3 deploy/configure.py "${config_args[@]}" --reuse
docker compose config --quiet
printf '\n[2/4] Checking required HTTPS services.\n'
python3 deploy/check-network.py --mode "$mode"
if [[ "$mode" == image ]]; then
  printf '\n[3/4] Downloading ready containers; no Python package installation.\n'
  pull_image "$image"
  digest="$(verify_image "$image" "$revision")"
  set_image_config "$digest"
  # Pull the proxy separately; --no-build below prevents any accidental local build.
  timeout 300 docker compose pull caddy || fail 'Caddy download failed; retry the same installer command.'
  up_args=(--no-build --pull never)
else
  printf '\n[3/4] Building from source. PyPI connectivity is required.\n'
  up_args=(--build)
fi
printf '\n[4/4] Starting services and verifying the panel.\n'
if ! docker compose up -d "${up_args[@]}" --wait --wait-timeout 180; then
  printf '\nInstallation did not complete. Settings/data were retained.\n' >&2
  printf 'Retry the same command. For logs: bash deploy/manage.sh logs\n' >&2
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
printf 'Management menu: bash %s/deploy/manage.sh\n' "$PWD"
