#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
[[ -f .env ]] || { printf 'Configuration missing; run deploy/install.sh first.\n' >&2; exit 1; }
address() {
  python3 - <<'PY'
from pathlib import Path
for line in Path('.env').read_text().splitlines():
    if line.startswith('PUBLIC_URL='):
        print('Panel: ' + line.split('=', 1)[1])
PY
}
action="${1:-menu}"
if [[ "$action" == menu ]]; then
  address
  printf '\n1) Status  2) Start  3) Stop  4) Restart\n5) Logs  6) Update  7) Backup  8) Network check  9) Panel address  0) Exit\n'
  read -r -p 'Select: ' choice
  case "$choice" in
    1) action=status;; 2) action=start;; 3) action=stop;; 4) action=restart;;
    5) action=logs;; 6) action=update;; 7) action=backup;; 8) action=doctor;;
    9) action=address;; 0) exit 0;; *) printf 'Invalid choice.\n' >&2; exit 2;;
  esac
fi
case "$action" in
  status) docker compose ps; address;;
  start) docker compose up -d --wait --wait-timeout 180; address;;
  stop) docker compose stop;;
  restart) docker compose restart; address;;
  logs) docker compose logs --tail 100;;
  update) bash deploy/update.sh;;
  backup) bash deploy/backup.sh;;
  doctor)
    python3 deploy/check-network.py
    docker compose exec -T app python - < deploy/check-network.py;;
  address) address;;
  *) printf 'Usage: bash deploy/manage.sh [status|start|stop|restart|logs|update|backup|doctor|address]\n' >&2; exit 2;;
esac
