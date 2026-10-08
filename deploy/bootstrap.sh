#!/usr/bin/env bash
# Download this file, inspect it, then run it with sudo on the intended VPS.
set -euo pipefail
umask 077

domain=""
ip=""
port="443"
resume=auto
install_options=()
repository="Darkvampire1998/pars-agent"
install_dir="/opt/pars-agent"
branch="main"

usage() {
  cat <<'HELP'
Pars Agent Linux installer
Usage: sudo bash bootstrap.sh (--domain HOST | --ip PUBLIC_IP) [--port 443]
       [--resume] [--source-build|--host-build-network] [--repo owner/repo] [--dir /opt/pars-agent] [--branch main]

Installs missing dependencies only. Downloads the ready container from GHCR;
Python packages are already installed in the image. Starts the HTTPS panel.
Existing unrelated directories are refused. MT5/Wine is configured separately.
Failed installations in clean matching checkouts resume automatically, preserving .env.
Running installations must be updated with deploy/update.sh.
--source-build builds locally instead; this needs working PyPI connectivity.
--host-build-network uses the Linux host network for the build only, if bridge
DNS is broken. It does not change system DNS or runtime service networking.
Public IP HTTPS uses a short-lived Let's Encrypt certificate; TCP 80 must be open.
HELP
}
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain|--ip|--port|--repo|--dir|--branch)
      [[ $# -ge 2 ]] || fail "Missing value for $1"
      case "$1" in
        --domain) domain="$2";;
        --ip) ip="$2";;
        --port) port="$2";;
        --repo) repository="$2";;
        --dir) install_dir="$2";;
        --branch) branch="$2";;
      esac
      shift 2;;
    --resume) resume=true; shift;;
    --source-build|--host-build-network) install_options+=("$1"); shift;;
    --help|-h) usage; exit 0;;
    *) fail "Unknown option: $1";;
  esac
done
[[ -z "$domain" || -z "$ip" ]] || fail "Choose --domain or --ip, not both."
[[ -n "$domain" || -n "$ip" ]] || fail "Use a DNS hostname with --domain or public IP with --ip."
if [[ -n "$domain" ]]; then
  [[ "$domain" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ && ${#domain} -le 253 ]] || fail "Use a DNS hostname without https://, port or path."
  install_options+=(--domain "$domain")
else
  [[ "$ip" =~ ^[0-9a-fA-F:.]+$ ]] || fail "Use a public IP address without scheme, port or path."
  if [[ "$ip" != *:* ]]; then
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Use a public IP address."
    IFS=. read -r -a octets <<< "$ip"
    for octet in "${octets[@]}"; do
      [[ ${#octet} -le 3 && ( "$octet" == 0 || "$octet" != 0* ) ]] || fail "Invalid public IP address."
      (( 10#$octet <= 255 )) || fail "Invalid public IP address."
    done
  fi
  if command -v python3 >/dev/null; then
    python3 - "$ip" <<'PY'
import ipaddress, sys
try:
    address = ipaddress.ip_address(sys.argv[1])
    assert address.is_global and not address.is_multicast
except (ValueError, AssertionError):
    raise SystemExit('Error: Use a public IP address, without scheme, brackets, port or path.')
PY
  fi
  install_options+=(--ip "$ip")
fi
[[ "$port" =~ ^[1-9][0-9]{0,4}$ && "$port" -le 65535 && "$port" -ne 80 ]] || fail "Invalid HTTPS port; use 1..65535 except 80."
install_options+=(--port "$port")
[[ "$repository" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || fail "Repository must be owner/name."
[[ "$branch" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || fail "Invalid branch."
[[ "$install_dir" == /* && "$install_dir" != / && ! "$install_dir" =~ (^|/)\.\.(/|$) ]] || fail "Installation directory must be an absolute path without parent traversal."
if [[ -e "$install_dir" ]]; then
  [[ ! -L "$install_dir" && -d "$install_dir/.git" ]] || fail "Directory already exists: $install_dir. Only a matching Git installation can resume."
  command -v git >/dev/null || fail "Git is required to verify the existing checkout before resuming."
  [[ "$(git -C "$install_dir" remote get-url origin)" == "https://github.com/$repository.git" ]] || fail "Existing checkout has a different origin; resume stopped."
  [[ "$(git -C "$install_dir" branch --show-current)" == "$branch" ]] || fail "Existing checkout uses a different branch."
  [[ -z "$(git -C "$install_dir" status --porcelain --untracked-files=no)" ]] || fail "Tracked files have local changes; resume stopped."
  resume=true
elif [[ "$resume" == true ]]; then
  fail "Cannot resume: installation directory does not exist."
fi
[[ $EUID -eq 0 ]] || fail "Run the installer with sudo."
[[ -r /etc/os-release ]] || fail "Cannot detect the operating system."
# shellcheck source=/dev/null
source /etc/os-release
[[ "${ID:-}" == ubuntu || "${ID:-}" == debian ]] || fail "Automatic dependency installation supports Ubuntu/Debian only."
suite="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
[[ "$suite" =~ ^[a-z]+$ ]] || fail "Cannot identify the OS release codename."
command -v apt-get >/dev/null || fail "apt-get is required."

printf 'Installing panel dependencies for %s; repository %s.\n' "${domain:-$ip}" "$repository"
missing=()
for dependency in curl git python3; do
  command -v "$dependency" >/dev/null || missing+=("$dependency")
done
[[ -s /etc/ssl/certs/ca-certificates.crt ]] || missing+=(ca-certificates)
if [[ ${#missing[@]} -gt 0 ]]; then
  apt-get update
  apt-get install -y "${missing[@]}"
else
  printf 'Dependencies already installed; skipping apt update/install.\n'
fi
if ! command -v docker >/dev/null; then
  install -d -m 0755 /etc/apt/keyrings
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/pars-agent-docker.asc
  chmod 0644 /etc/apt/keyrings/pars-agent-docker.asc
  arch="$(dpkg --print-architecture)"
  # Do not replace existing Docker apt configuration with different signing keys.
  if ! grep -Rqs "https://download.docker.com/linux/$ID" /etc/apt/sources.list /etc/apt/sources.list.d; then
    cat > /etc/apt/sources.list.d/pars-agent-docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: $suite
Components: stable
Architectures: $arch
Signed-By: /etc/apt/keyrings/pars-agent-docker.asc
EOF
  fi
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  if command -v systemctl >/dev/null; then
    systemctl enable --now docker
  fi
fi
docker compose version >/dev/null || fail "Docker Compose v2 is required. Existing Docker packages were left in place."
docker info >/dev/null || fail "Docker daemon is not available; start it and retry."

mkdir -p -- "$(dirname -- "$install_dir")"
git check-ref-format --branch "$branch" >/dev/null || fail "Invalid Git branch."
if [[ "$resume" == true ]]; then
  cd -- "$install_dir"
  if [[ -f .env ]]; then
    active="$(docker compose ps --status running -q app)" || fail "Cannot check existing services; review them before resuming."
    if [[ -n "$active" ]]; then
      existing_url="$(python3 - <<'PY'
from pathlib import Path
print(next((line.split('=', 1)[1] for line in Path('.env').read_text().splitlines() if line.startswith('PUBLIC_URL=')), ''))
PY
)"
      host="${domain,,}"
      if [[ -n "$ip" ]]; then
        host="$(python3 -c 'import ipaddress,sys; a=ipaddress.ip_address(sys.argv[1]); print("["+str(a)+"]" if a.version == 6 else str(a))' "$ip")"
      fi
      requested_url="https://$host"
      [[ "$port" == 443 ]] || requested_url+=":$port"
      if [[ "$existing_url" == "$requested_url" ]] && curl --proto '=https' --tlsv1.2 --fail --silent --show-error --noproxy '*' --connect-timeout 5 --max-time 10 "$existing_url/api/health" >/dev/null; then
        printf '\nPanel already responds over verified HTTPS: %s\nManagement: bash %s/deploy/manage.sh\n' "$existing_url" "$PWD"
        exit 0
      fi
      fail 'Application is running; use deploy/manage.sh logs for HTTPS errors, or deploy/update.sh for updates. Address migration must be explicit.'
    fi
  fi
  printf 'Resuming the existing clean installation; secrets and data are preserved.\n'
  git fetch origin "$branch"
  git merge --ff-only FETCH_HEAD
  install_options+=(--reconfigure)
else
  git clone --branch "$branch" --single-branch "https://github.com/$repository.git" "$install_dir"
fi
chmod 0755 "$install_dir"
cd -- "$install_dir"
bash deploy/install.sh "${install_options[@]}"
printf '\nProject: %s\nNext: connect MT5 using the README, then validate a demo account.\n' "$install_dir"
