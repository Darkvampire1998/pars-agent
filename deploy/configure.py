#!/usr/bin/env python3
"""Configure domain/IP HTTPS without replacing an existing encryption key."""
import argparse
import base64
import ipaddress
import os
import re
import tempfile
from pathlib import Path

def endpoint(domain=None, ip=None, local=False, port=443):
    if not 1 <= port <= 65535 or port == 80:
        raise ValueError("HTTPS port must be 1..65535, excluding port 80 (ACME HTTP challenge).")
    if local:
        return {"DOMAIN": "localhost", "PUBLIC_URL": "http://127.0.0.1:8000", "COOKIE_SECURE": "false"}
    if ip:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            raise ValueError("Pass a public IP address, without scheme, brackets, port or path.") from None
        if not address.is_global or address.is_multicast:
            raise ValueError("IP must be a publicly routable unicast address.")
        domain = str(address)
        host = f"[{domain}]" if address.version == 6 else domain
        config = "./deploy/Caddyfile.ip"
    else:
        if not domain or not re.fullmatch(r"(?=.{4,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", domain):
            raise ValueError("Pass a public DNS hostname, without scheme, port or path.")
        domain = domain.lower()
        host = domain
        config = "./deploy/Caddyfile"
    url = "https://" + host + (f":{port}" if port != 443 else "")
    return {"DOMAIN": domain, "PANEL_HOST": host, "PANEL_PORT": str(port),
            "PUBLIC_URL": url, "COOKIE_SECURE": "true", "CADDY_CONFIG": config}


def write_config(path, values, reconfigure=False, host_build=False, reuse=False):
    existed = path.exists()
    if existed:
        if path.is_symlink() or not path.is_file():
            raise ValueError(".env must be a regular file, not a symlink.")
        original = path.read_text()
        current = {}
        for line in original.splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key in current:
                    raise ValueError("Duplicate .env setting: " + key)
                current[key] = value
        try:
            key = base64.b64decode(current.get("ENCRYPTION_KEY", ""), altchars=b"-_", validate=True)
            if len(key) != 32:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Existing ENCRYPTION_KEY is invalid; restore the original .env backup.") from None
        if not reconfigure:
            if not reuse:
                raise ValueError(".env already exists; use --reconfigure to change the address while preserving its encryption key.")
            if current.get("PUBLIC_URL", "").rstrip("/") != values["PUBLIC_URL"]:
                raise ValueError("Existing configuration uses a different address. Use --reconfigure explicitly.")
    else:
        original = ""
        current = {"ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
                   "REGISTRATION_ENABLED": "true", "LIVE_TRADING_ALLOWED": "false"}
    replacements = dict(values)
    if host_build:
        previous = current.get("COMPOSE_FILE", "compose.yaml")
        if previous not in ("compose.yaml", "compose.yaml:deploy/compose.host-build.yaml"):
            raise ValueError("Custom COMPOSE_FILE exists; review it before enabling host build networking.")
        replacements["COMPOSE_FILE"] = "compose.yaml:deploy/compose.host-build.yaml"
    lines = []
    for line in original.splitlines():
        name = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else None
        if name in replacements:
            lines.append(name + "=" + replacements.pop(name))
        else:
            lines.append(line)
    if not existed:
        lines.extend(k + "=" + v for k, v in current.items())
    lines.extend(k + "=" + v for k, v in replacements.items())
    text = "\n".join(lines) + "\n"
    if not existed:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
    else:
        fd, temp_name = tempfile.mkstemp(prefix=".env-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(text)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--domain")
    group.add_argument("--ip")
    group.add_argument("--local", action="store_true")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--reconfigure", action="store_true")
    parser.add_argument("--reuse", action="store_true", help="Reuse only an identical existing public URL.")
    parser.add_argument("--host-build-network", action="store_true")
    args = parser.parse_args()
    try:
        values = endpoint(args.domain, args.ip, args.local, args.port)
        write_config(Path(__file__).resolve().parent.parent / ".env", values,
                     args.reconfigure, args.host_build_network, args.reuse)
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")
    print("Configuration ready. Encryption key preserved on reconfiguration. Panel URL: " + values["PUBLIC_URL"])


if __name__ == "__main__":
    main()
