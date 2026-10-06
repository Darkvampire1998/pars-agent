#!/usr/bin/env bash
# Download this file, inspect it, then run it with sudo on the intended VPS.
set -euo pipefail
umask 077

domain=""
repository="Darkvampire1998/pars-agent"
install_dir="/opt/pars-agent"
branch="main"

usage() {
  cat <<'HELP'
Pars Agent Linux installer
Usage: sudo bash bootstrap.sh --domain trade.example.com [--repo owner/repo] [--dir /opt/pars-agent] [--branch main]

Installs Git, Python, curl and (if absent) Docker Engine from Docker's official
repository on Ubuntu/Debian. Clones the project and starts the HTTPS panel.
Existing unrelated directories are refused. MT5/Wine is configured separately.
An existing installation must be updated with deploy/update.sh.
HELP
}
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain|--repo|--dir|--branch)
      [[ $# -ge 2 ]] || fail "Missing value for $1"
      case "$1" in
        --domain) domain="$2";;
        --repo) repository="$2";;
        --dir) install_dir="$2";;
        --branch) branch="$2";;
      esac
      shift 2;;
    --help|-h) usage; exit 0;;
    *) fail "Unknown option: $1";;
  esac
done
[[ "$domain" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ && ${#domain} -le 253 ]] || fail "Use a DNS hostname without https://, port or path."
[[ "$repository" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || fail "Repository must be owner/name."
[[ "$branch" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || fail "Invalid branch."
[[ "$install_dir" == /* && "$install_dir" != / && ! "$install_dir" =~ (^|/)\.\.(/|$) ]] || fail "Installation directory must be an absolute path without parent traversal."
[[ $EUID -eq 0 ]] || fail "Run the installer with sudo."
[[ ! -e "$install_dir" ]] || fail "Directory already exists: $install_dir. Use another --dir or deploy/update.sh."
[[ -r /etc/os-release ]] || fail "Cannot detect the operating system."
# shellcheck source=/dev/null
source /etc/os-release
[[ "${ID:-}" == ubuntu || "${ID:-}" == debian ]] || fail "Automatic dependency installation supports Ubuntu/Debian only."
suite="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
[[ "$suite" =~ ^[a-z]+$ ]] || fail "Cannot identify the OS release codename."
command -v apt-get >/dev/null || fail "apt-get is required."

printf 'Installing panel dependencies for %s; repository %s.\n' "$domain" "$repository"
apt-get update
apt-get install -y ca-certificates curl git python3
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
git clone --branch "$branch" --single-branch "https://github.com/$repository.git" "$install_dir"
chmod 0755 "$install_dir"
cd -- "$install_dir"
bash deploy/install.sh "$domain"
printf '\nPanel: https://%s\nProject: %s\nNext: connect MT5 using the README, then validate a demo account.\n' "$domain" "$install_dir"
