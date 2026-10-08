#!/usr/bin/env bash
set -euo pipefail
[[ $# == 2 && -n "${WINEPREFIX:-}" ]] || { printf 'Usage: WINEPREFIX=/isolated/prefix bash run-wine.sh /windows-python.exe /connector.json\n' >&2; exit 2; }
[[ -f "$1" && -f "$2" ]] || { printf 'Windows Python and connector configuration must exist.\n' >&2; exit 1; }
command -v wine >/dev/null
command -v winepath >/dev/null
command -v xvfb-run >/dev/null
connector="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/connector.py"
config="$(winepath -w "$2")"
script="$(winepath -w "$connector")"
exec xvfb-run -a wine "$1" "$script" --config "$config"
