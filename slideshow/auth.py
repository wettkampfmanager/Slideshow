"""Passwort-Hashing (PBKDF2, nur Standardbibliothek)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

DEFAULT_PASSWORD = "slideshow"
_ITERATIONS = 120_000  # auf dem Pi 3 ca. 0,5 s pro Login – ausreichend


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return hmac.compare_digest(password, DEFAULT_PASSWORD)
    try:
        _, iterations, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(check.hex(), digest)
