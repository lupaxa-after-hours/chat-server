"""Time-based one-time passwords and terminal QR rendering."""

from __future__ import annotations

import base64
import hmac
import secrets
import struct
from hashlib import sha1
from urllib.parse import quote

import qrcode
from qrcode.constants import ERROR_CORRECT_L

ISSUER = "Chat Server"
STEP = 30


def new_secret() -> str:
    """Return a base32 secret with no padding, from 20 random bytes."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def otpauth_uri(name: str, secret: str) -> str:
    """Return an ``otpauth://`` URI for ``name`` and ``secret``."""
    label = f"{ISSUER}:{name}"
    return f"otpauth://totp/{quote(label)}?secret={secret}&issuer={quote(ISSUER)}"


def secret_key(secret: str) -> bytes:
    """Return the decoded bytes of a base32 MFA secret."""
    pad = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(secret + pad)


def code_at(secret: str, step: int) -> str:
    """Return the six-digit code for ``secret`` at ``step``."""
    digest = hmac.new(secret_key(secret), struct.pack(">Q", step), sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def verify_totp(secret: str, code: str, *, now: int, used: int | None) -> int | None:
    """Return the matched 30-second timestep, or ``None`` when the code does not apply."""
    if len(code) != 6 or not code.isdigit():
        return None
    current = now // STEP
    for step in (current - 1, current, current + 1):
        if used is not None and step <= used:
            continue
        if code_at(secret, step) == code:
            return step
    return None


def record_mfa_step(steps: dict[str, int], key: str, step: int) -> None:
    """Store ``step`` only when it is higher than the value already stored."""
    prev = steps.get(key)
    if prev is None or step > prev:
        steps[key] = step


def qr_lines(uri: str) -> tuple[str, ...]:
    """Return terminal lines that render ``uri`` as a half-block QR grid."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_L, border=1, box_size=1)
    qr.add_data(uri)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    lines: list[str] = []
    row = 0
    while row < len(matrix):
        upper = matrix[row]
        lower = matrix[row + 1] if row + 1 < len(matrix) else None
        chars: list[str] = []
        for col, top_dark in enumerate(upper):
            bottom_dark = lower[col] if lower is not None else False
            if top_dark and bottom_dark:
                chars.append("█")
            elif top_dark:
                chars.append("▀")
            elif bottom_dark:
                chars.append("▄")
            else:
                chars.append(" ")
        lines.append("".join(chars))
        row += 2
    return tuple(lines)
