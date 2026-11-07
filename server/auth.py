"""Authentication helpers: salted PBKDF2 password hashing and session tokens.

Day 1 stub. Password hashing and token issuance are implemented in Day 4.
Only the standard library is used (hashlib, secrets).
"""

from __future__ import annotations

import secrets


def new_token() -> str:
    """Generate an opaque session token. Implemented fully in Day 4."""
    return secrets.token_hex(16)
