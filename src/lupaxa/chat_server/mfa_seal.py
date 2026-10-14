"""Seal an MFA secret with the player's password."""

from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_MAXMEM = 32 * 1024 * 1024


def legacy_mfa_secret(stored: str) -> bool:
    """Return whether ``stored`` is a base32 secret rather than a seal."""
    if stored.startswith("enc1$"):
        return False
    padded = stored + "=" * ((8 - len(stored) % 8) % 8)
    try:
        base64.b32decode(padded, casefold=True)
    except ValueError:
        return False
    return True


def seal_mfa_secret(password: str, secret: str) -> str:
    """Return ``secret`` sealed with a key derived from ``password``."""
    salt = secrets.token_bytes(_SALT_BYTES)
    token = _fernet(password, salt).encrypt(secret.encode("utf-8"))
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    return f"enc1${salt_text}${token.decode('ascii')}"


def open_mfa_secret(password: str, stored: str) -> str | None:
    """Return the base32 secret, or None when the seal will not open."""
    parts = stored.split("$", 2)
    if len(parts) != 3 or parts[0] != "enc1":
        return None
    try:
        salt = base64.urlsafe_b64decode(parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4))
        raw = _fernet(password, salt).decrypt(parts[2].encode("ascii"))
    except (InvalidToken, ValueError):
        return None
    return raw.decode("utf-8")


def refresh_mfa_secret(password: str, stored: str | None) -> tuple[str | None, str | None, bool]:
    """Return the base32 secret, the file value, and whether it opened."""
    if stored is None:
        return None, None, True
    if legacy_mfa_secret(stored):
        return stored, seal_mfa_secret(password, stored), True
    plain = open_mfa_secret(password, stored)
    if plain is None:
        return None, stored, False
    return plain, seal_mfa_secret(password, plain), True


def _fernet(password: str, salt: bytes) -> Fernet:
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_N,
        r=_R,
        p=_P,
        dklen=32,
        maxmem=_MAXMEM,
    )
    return Fernet(base64.urlsafe_b64encode(derived))
