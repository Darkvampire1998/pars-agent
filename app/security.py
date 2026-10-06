import hashlib
import hmac
import os
import secrets
from cryptography.fernet import Fernet

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def hash_password(value):
    salt = secrets.token_hex(16)
    out = hashlib.pbkdf2_hmac("sha256", value.encode(), salt.encode(), 600_000).hex()
    return salt + ":" + out

def verify_password(value, stored):
    salt, expected = stored.split(":")
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", value.encode(), salt.encode(), 600_000).hex(), expected)

def cipher():
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is required; run deploy/configure.py")
    return Fernet(key.encode())

def encrypt(value):
    return cipher().encrypt(value.encode()).decode()

def decrypt(value):
    return cipher().decrypt(value.encode()).decode()
