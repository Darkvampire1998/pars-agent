#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
umask 077
mkdir -p backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
docker compose exec -T app python -c 'import sqlite3,os; src=sqlite3.connect(os.environ["DB_PATH"]); dst=sqlite3.connect("/data/backup.sqlite"); src.backup(dst); dst.close(); src.close()'
container="$(docker compose ps -q app)"
docker cp "$container:/data/backup.sqlite" "backups/agent-$stamp.sqlite"
cp .env "backups/config-$stamp.env"
printf 'Backup saved. Database and matching encryption key must be restored together.\n'
