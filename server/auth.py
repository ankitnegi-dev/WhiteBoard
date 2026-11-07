"""Authentication helpers: salted PBKDF2 password hashing and session tokens.

Only the standard library is used (hashlib, secrets) so there is no extra
dependency and the hashing is easy to explain in the report.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Optional, Tuple


def hash_password(password: str) -> Tuple[str, str]:
    """Return (salt_hex, hash_hex) for a password using PBKDF2-HMAC-SHA256.

    A fresh random salt is generated each time so two rooms with the same
    password never store the same hash. 100k iterations is slow enough to
    deter brute force but fast enough for a LAN app.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """Constant-time comparison of a password against a stored salt/hash."""
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return secrets.compare_digest(digest.hex(), hash_hex)


def new_token() -> str:
    """Generate an opaque session token for reconnects and identity."""
    return secrets.token_hex(16)


def new_room_code(existing: set[str]) -> str:
    """Generate a unique 6-character room code (uppercase letters/digits)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # avoids 0/O and 1/I
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if code not in existing:
            return code
