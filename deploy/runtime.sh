#!/usr/bin/env bash
# Shared installer/update helpers. Never source .env as shell code.
configure_build_backend() {
  local compose_files
  compose_files="$(python3 - <<'PY'
from pathlib import Path
settings = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
print(settings.get('COMPOSE_FILE', ''))
PY
)" || return 1
  # Compose's Bake backend requires a separate interactive host-network grant.
  # Use Compose's direct BuildKit backend for the explicitly selected host build.
  if [[ "$compose_files" == 'compose.yaml:deploy/compose.host-build.yaml' ]]; then
    export COMPOSE_BAKE=false
  fi
}
image_for_commit() {
  local revision="$1" origin repository
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { printf 'Invalid source revision.\n' >&2; return 1; }
  origin="$(git remote get-url origin)" || return 1
  [[ "$origin" =~ ^https://github\.com/([A-Za-z0-9_-]+/[A-Za-z0-9_.-]+)\.git$ ]] || {
    printf 'Ready-image installation needs an HTTPS GitHub origin; use --source-build for local/private builds.\n' >&2; return 1;
  }
  repository="${BASH_REMATCH[1],,}"
  printf 'ghcr.io/%s:git-%s\n' "$repository" "$revision"
}
pull_image() {
  local image="$1" attempt
  for attempt in 1 2 3; do
    printf 'Downloading ready image (attempt %s/3): %s\n' "$attempt" "$image" >&2
    if timeout 300 docker pull "$image" >&2; then return 0; fi
  done
  printf 'Image download failed. Check GHCR connectivity and that this commit has a successful public image build. Settings/data were retained.\n' >&2
  return 1
}
verify_image() {
  local image="$1" revision="$2" actual digest
  actual="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" || return 1
  [[ "$actual" == "$revision" ]] || { printf 'Image revision does not match the checkout; installation stopped.\n' >&2; return 1; }
  digest="$(docker image inspect --format '{{json .RepoDigests}}' "$image" | python3 -c '
import json, re, sys
prefix = sys.argv[1].split("@", 1)[0].split(":", 1)[0] + "@sha256:"
for item in json.load(sys.stdin) or []:
    if item.startswith(prefix) and re.fullmatch(r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+@sha256:[0-9a-f]{64}", item):
        print(item)
        break
else:
    raise SystemExit("Verified registry digest missing; installation stopped.")
' "$image")" || return 1
  printf '%s\n' "$digest"
}
set_image_config() {
  python3 - "$1" <<'PY'
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('configure', 'deploy/configure.py')
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)
configure.write_config(Path('.env'), {}, reconfigure=True, image=sys.argv[1])
PY
}
