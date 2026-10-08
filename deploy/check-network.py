#!/usr/bin/env python3
"""Deadline-bounded preflight. Telegram failures never block panel installation."""
import argparse
from pathlib import Path
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

PYPI = ("https://pypi.org/simple/", "https://files.pythonhosted.org/")
ACME = "https://acme-v02.api.letsencrypt.org/directory"
TELEGRAM = "https://api.telegram.org/"
TARGETS = (*PYPI, ACME, TELEGRAM)


def probe(url):
    socket.setdefaulttimeout(6)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=6):
            pass
    except urllib.error.HTTPError:
        pass  # An HTTP error still proves TCP and verified TLS worked.
    except (OSError, urllib.error.URLError) as exc:
        print(str(exc))
        return 1
    return 0


def check(url):
    # A socket timeout alone does not bound getaddrinfo(). Kill the entire probe
    # on deadline so a stalled resolver cannot leave installation hanging.
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--probe", url],
                                capture_output=True, text=True, timeout=10)
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, "DNS/TLS check timed out after 10 seconds"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("image", "source", "all"), default="all")
    parser.add_argument("--probe", choices=TARGETS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.probe:
        return probe(args.probe)
    required = (ACME,) if args.mode == "image" else (*PYPI, ACME)
    failed = False
    for url in (*required, TELEGRAM):
        host = urllib.parse.urlsplit(url).hostname
        ok, reason = check(url)
        if ok:
            print("DNS/TLS OK: " + host, flush=True)
        elif url == TELEGRAM:
            print("WARNING: Telegram reports unavailable — " + reason +
                  ". Panel installation can continue.", file=sys.stderr, flush=True)
        else:
            failed = True
            print("DNS/TLS FAILED: " + host + " — " + reason, file=sys.stderr, flush=True)
    if failed:
        print("A required service is unreachable. Review VPS DNS/egress; system resolver settings were not changed.", file=sys.stderr)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
