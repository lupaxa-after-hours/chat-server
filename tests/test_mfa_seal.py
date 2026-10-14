"""Tests for password-derived MFA secret sealing."""

from totp_fixtures import TOTP_TEST_SEED


def test_a_sealed_secret_opens_only_with_the_same_password() -> None:
    """The file value hides the base32 secret."""
    from lupaxa.chat_server.mfa_seal import open_mfa_secret, seal_mfa_secret

    sealed = seal_mfa_secret("pw", TOTP_TEST_SEED)
    assert sealed.startswith("enc1$")
    assert TOTP_TEST_SEED not in sealed
    assert open_mfa_secret("pw", sealed) == TOTP_TEST_SEED
    assert open_mfa_secret("nope", sealed) is None


def test_refresh_seals_legacy_and_leaves_a_bad_seal() -> None:
    """Plaintext is sealed. A seal that will not open is returned unchanged."""
    from lupaxa.chat_server.mfa_seal import refresh_mfa_secret, seal_mfa_secret

    plain, stored, opened = refresh_mfa_secret("pw", TOTP_TEST_SEED)
    assert opened is True
    assert plain == TOTP_TEST_SEED
    assert stored is not None
    assert stored.startswith("enc1$")
    bad = seal_mfa_secret("other", TOTP_TEST_SEED)
    again, kept, opened_bad = refresh_mfa_secret("pw", bad)
    assert opened_bad is False
    assert again is None
    assert kept == bad
