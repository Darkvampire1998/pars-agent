#!/usr/bin/env python3
"""Detect the VPS IPv4, without trusting a single external response."""
import ipaddress
import json
import subprocess
import sys
from collections import Counter

SERVICES = ('https://api.ipify.org', 'https://checkip.amazonaws.com', 'https://ipv4.icanhazip.com')

def public_ipv4(value):
    try:
        address = ipaddress.ip_address(value.strip())
        return str(address) if address.version == 4 and address.is_global and not address.is_multicast else None
    except ValueError:
        return None

def local_addresses():
    try:
        result = subprocess.run(['ip', '-j', '-4', 'address', 'show', 'scope', 'global'], capture_output=True, text=True, timeout=3, check=True)
        return {valid for link in json.loads(result.stdout) for entry in link.get('addr_info', []) if (valid := public_ipv4(entry.get('local', '')))}
    except (OSError, ValueError, subprocess.SubprocessError):
        return set()

def query(url):
    # curl bounds DNS as well as connection/read time. HTTPS verification stays on.
    try:
        r = subprocess.run(['curl', '-4', '--proto', '=https', '--tlsv1.2', '-fsS', '--connect-timeout', '3', '--max-time', '5', url], capture_output=True, text=True, timeout=6, check=True)
        return public_ipv4(r.stdout[:100]) if len(r.stdout) < 100 else None
    except (OSError, subprocess.SubprocessError):
        return None

def detect():
    local = local_addresses()
    votes = Counter()
    for url in SERVICES:
        address = query(url)
        if address:
            votes[address] += 1
            if votes[address] >= 2:
                return address
    if len(local) == 1 and (not votes or set(votes) == local):
        return next(iter(local))
    raise ValueError('Cannot determine one reliable public IPv4. Retry or specify --ip YOUR_PUBLIC_IP (or --domain HOST).')

if __name__ == '__main__':
    try:
        print(detect())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
