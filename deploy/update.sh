#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
[[ -d .git && -f .env ]] || { printf 'Run this inside an installed Git checkout with .env.\n' >&2; exit 1; }
source deploy/runtime.sh
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { printf 'Tracked files have local changes; update stopped.\n' >&2; exit 1; }
branch="$(git symbolic-ref --quiet --short HEAD)" || { printf 'Detached checkout; review the intended release before updating.\n' >&2; exit 1; }
[[ "$branch" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || { printf 'Invalid branch.\n' >&2; exit 1; }
git fetch origin "$branch"
git merge-base --is-ancestor HEAD FETCH_HEAD || { printf 'Local history has diverged; resolve it before updating.\n' >&2; exit 1; }
old_head="$(git rev-parse HEAD)"
new_head="$(git rev-parse FETCH_HEAD)"
if [[ "$old_head" == "$new_head" ]]; then
  printf 'Already at the latest commit.\n'
  exit 0
fi
# Download and verify the new image before interrupting a running panel.
mode="$(python3 - <<'PY'
from pathlib import Path
settings = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
print('image' if settings.get('COMPOSE_FILE') == 'compose.yaml:deploy/compose.image.yaml' else 'source')
PY
)"
if [[ "$mode" == image ]]; then
  next_image="$(image_for_commit "$new_head")"
  pull_image "$next_image"
  next_digest="$(verify_image "$next_image" "$new_head")"
fi
# Stop new admissions before taking the database backup. Broker SL/TP remains active.
docker compose stop app
trap 'docker compose start app >/dev/null || true' EXIT
docker compose run --rm --no-deps --entrypoint python app -c 'import sqlite3,os; src=sqlite3.connect(os.environ["DB_PATH"]); dst=sqlite3.connect("/data/pre-update.sqlite"); src.backup(dst); dst.close(); src.close()'
umask 077
mkdir -p backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
container="$(docker compose ps --all -q app)"
docker cp "$container:/data/pre-update.sqlite" "backups/pre-update-$stamp.sqlite"
cp .env "backups/pre-update-$stamp.env"
printf '%s\n' "$old_head" > "backups/pre-update-$stamp.commit"
git merge --ff-only FETCH_HEAD
if [[ "$mode" == image ]]; then
  set_image_config "$next_digest"
  docker compose pull caddy
  docker compose up -d --no-build --pull never --wait --wait-timeout 180
else
  docker compose up -d --build --wait --wait-timeout 180
fi
trap - EXIT
printf 'Updated to %s. Settings, encryption key and data volume were preserved.\n' "$new_head"
