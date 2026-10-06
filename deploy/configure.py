#!/usr/bin/env python3
"""Generate an installation-specific encryption key, never ship a shared secret."""
import argparse
import base64
import os
import re
from pathlib import Path

p = argparse.ArgumentParser()
group = p.add_mutually_exclusive_group(required=True)
group.add_argument("--domain")
group.add_argument("--local", action="store_true")
args = p.parse_args()
root = Path(__file__).resolve().parent.parent
path = root / ".env"
if path.exists():
    raise SystemExit(".env already exists; preserve its encryption key. Edit PUBLIC_URL/DOMAIN manually if needed.")
if args.domain and not re.fullmatch(r"(?=.{4,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", args.domain):
    raise SystemExit("Pass a public DNS hostname, without scheme or path.")
domain = args.domain or "localhost"
url = "https://" + domain if args.domain else "http://127.0.0.1:8000"
key = base64.urlsafe_b64encode(os.urandom(32)).decode()
text = f"DOMAIN={domain}\nPUBLIC_URL={url}\nCOOKIE_SECURE={'true' if args.domain else 'false'}\nENCRYPTION_KEY={key}\nREGISTRATION_ENABLED=true\nLIVE_TRADING_ALLOWED=false\n"
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(text)
print("Configuration created. Keep .env secure and backed up. Panel URL: " + url)
