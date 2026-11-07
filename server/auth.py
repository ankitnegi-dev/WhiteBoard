"""Authentication helpers: salted PBKDF2 password hashing, tokens, lockout.

Only the standard library is used. The lockout tracker is in-memory and
per-IP; it resets when the server restarts, which is acceptable for a
LAN tool.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from typing import Any, Optional, Tuple


def hash_password(password: str) -> Tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return secrets.compare_digest(digest.hex(), hash_hex)


def new_token() -> str:
    return secrets.token_hex(16)


def new_room_code(existing: set[str]) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if code not in existing:
            return code


class LoginLimiter:
    """Track failed password attempts per IP with a temporary lockout."""

    def __init__(self, max_failures: int = 5, lockout_seconds: float = 60.0) -> None:
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def is_locked(self, ip: str) -> bool:
        with self._lock:
            entry = self._data.get(ip)
            if not entry:
                return False
            if entry.get("locked_until", 0) > time.time():
                return True
            return False

    def record_failure(self, ip: str) -> bool:
        """Record a failure. Return True if the IP is now locked out."""
        with self._lock:
            entry = self._data.setdefault(ip, {"failures": 0, "locked_until": 0.0})
            entry["failures"] += 1
            if entry["failures"] >= self.max_failures:
                entry["locked_until"] = time.time() + self.lockout_seconds
                entry["failures"] = 0
                return True
            return False

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._data.pop(ip, None)