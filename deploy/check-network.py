#!/usr/bin/env python3
"""Bounded DNS/TLS preflight; never rewrites system or Docker resolver settings."""
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

TARGETS = ("https://pypi.org/simple/", "https://files.pythonhosted.org/",
           "https://acme-v02.api.letsencrypt.org/directory", "https://api.telegram.org/")


def main():
    failed = False
    socket.setdefaulttimeout(6)
    for url in TARGETS:
        host = urllib.parse.urlsplit(url).hostname
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=6):
                    pass
            except urllib.error.HTTPError:
                pass  # HTTP errors still prove DNS, TCP and verified TLS worked.
            print("DNS/TLS OK: " + host)
        except (OSError, urllib.error.URLError) as exc:
            failed = True
            print("DNS/TLS FAILED: " + host + " — " + str(exc), file=sys.stderr)
    if failed:
        print("Fix the VPS resolver/egress before installing. Host build networking cannot fix broken host DNS.", file=sys.stderr)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
