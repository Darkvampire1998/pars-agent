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
  printf '\n1) Status  2) Start  3) Stop  4) Restart\n5) Logs  6) Update  7) Backup  8) Network check  9) Panel address\n10) MT5 account connection  0) Exit\n'
  read -r -p 'Select: ' choice
  case "$choice" in
    1) action=status;; 2) action=start;; 3) action=stop;; 4) action=restart;;
    5) action=logs;; 6) action=update;; 7) action=backup;; 8) action=doctor;;
    9) action=address;; 10) action=connect;; 0) exit 0;; *) printf 'Invalid choice.\n' >&2; exit 2;;
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
    mode="$(python3 - <<'PY'
from pathlib import Path
settings = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
print('image' if settings.get('COMPOSE_FILE') == 'compose.yaml:deploy/compose.image.yaml' else 'source')
PY
)"
    python3 deploy/check-network.py --mode "$mode"
    # Copy to /tmp: subprocess probe deadlines need a real file, not stdin.
    container="$(docker compose ps -q app)"
    [[ -n "$container" ]] || { printf 'Application is not running.\n' >&2; exit 1; }
    docker cp deploy/check-network.py "$container:/tmp/pars-agent-network.py"
    docker compose exec -T app python /tmp/pars-agent-network.py --mode image;;
  address) address;;
  connect) python3 deploy/connect-account.py;;
  *) printf 'Usage: bash deploy/manage.sh [status|start|stop|restart|logs|update|backup|doctor|address|connect]\n' >&2; exit 2;;
esac
