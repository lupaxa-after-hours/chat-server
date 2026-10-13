"""Tests for time-based one-time passwords and QR terminal rendering."""


def test_a_current_code_matches_and_a_reuse_does_not() -> None:
    """The current 30 second step matches once."""
    import base64
    import hmac
    import struct
    from hashlib import sha1

    from lupaxa.chat_server.totp import new_secret, verify_totp

    secret = new_secret()
    raw = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    step = 1_700_000_000 // 30
    digest = hmac.new(raw, struct.pack(">Q", step), sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    code = f"{binary % 1_000_000:06d}"
    assert verify_totp(secret, code, now=1_700_000_000, used=None) == step
    assert verify_totp(secret, code, now=1_700_000_000, used=step) is None
    assert verify_totp(secret, "000000", now=1_700_000_000, used=None) is None


def test_an_older_timestep_cannot_be_replayed() -> None:
    """After step N+1 is used, codes for N and N+1 both fail."""
    import base64
    import hmac
    import struct
    from hashlib import sha1

    from lupaxa.chat_server.totp import new_secret, verify_totp

    secret = new_secret()
    raw = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    now = 1_700_000_000
    current = now // 30
    used = current + 1

    def code_for(step: int) -> str:
        digest = hmac.new(raw, struct.pack(">Q", step), sha1).digest()
        offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        return f"{binary % 1_000_000:06d}"

    assert verify_totp(secret, code_for(used), now=now, used=None) == used
    assert verify_totp(secret, code_for(current), now=now, used=used) is None
    assert verify_totp(secret, code_for(used), now=now, used=used) is None


def test_qr_rows_are_as_wide_as_the_matrix() -> None:
    """Every QR row has the same width."""
    from lupaxa.chat_server.totp import otpauth_uri, qr_lines

    rows = qr_lines(otpauth_uri("Ada", "JBSWY3DPEHPK3PXP"))
    assert rows
    assert len({len(row) for row in rows}) == 1
