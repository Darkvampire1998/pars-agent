#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ $# -ne 1 ]]; then
  printf 'Usage: bash deploy/install.sh trade.your-domain.com\n'
  exit 2
fi
command -v docker >/dev/null || { printf 'Install Docker Engine from the official Docker documentation first.\n'; exit 1; }
docker compose version >/dev/null
command -v python3 >/dev/null || { printf 'python3 is required.\n'; exit 1; }
if [[ ! -f .env ]]; then
  python3 deploy/configure.py --domain "$1"
else
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
lines = Path('.env').read_text().splitlines()
if 'DOMAIN=' + sys.argv[1] not in lines:
    raise SystemExit('Existing configuration uses a different domain. Review .env before installing.')
PY
fi
docker compose up -d --build
printf 'Panel containers started. Check health with: docker compose ps\n'
