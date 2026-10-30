"""Usernames and passwords for the admin accounts created on master panels."""

from __future__ import annotations

import re
import secrets
import string

_UPPER = string.ascii_uppercase
_LOWER = string.ascii_lowercase
_DIGITS = string.digits
_SPECIAL = "!@#$%^&*()-_=+"
_ALPHABET = _UPPER + _LOWER + _DIGITS + _SPECIAL

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


def generate_password(length: int = 16, *, username: str | None = None) -> str:
    """A password that satisfies the Pasarguard policy (upper, lower, digits, special; no quotes)."""
    length = max(length, 12)

    def build() -> str:
        parts = [
            secrets.choice(_UPPER),
            secrets.choice(_UPPER),
            secrets.choice(_LOWER),
            secrets.choice(_LOWER),
            secrets.choice(_DIGITS),
            secrets.choice(_DIGITS),
            secrets.choice(_SPECIAL),
        ]
        while len(parts) < length:
            parts.append(secrets.choice(_ALPHABET))
        secrets.SystemRandom().shuffle(parts)
        return "".join(parts)

    for _ in range(32):
        password = build()
        if username and username.lower() in password.lower():
            continue
        return password
    return build()  # a username collision twice in a row is not worth failing for


def generate_username(owner_id: int, *, prefix: str = "p") -> str:
    """``p<telegram id>_<4 hex>``: unique per purchase, recognisable on the panel, within 32 chars."""
    prefix = re.sub(r"[^A-Za-z0-9_]", "", prefix)[:6] or "p"
    return f"{prefix}{owner_id}_{secrets.token_hex(2)}"[:32]
