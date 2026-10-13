"""Calendar phrases."""

from datetime import datetime, timezone

from lupaxa.chat_server.time import clock_phrase


def test_clock_phrase_is_the_english_utc_line() -> None:
    """A known Monday is the full phrase, with seconds and UTC."""
    moment = datetime(2026, 10, 12, 13, 45, 3, tzinfo=timezone.utc)
    assert clock_phrase(moment) == "Monday 12th October 2026, 13:45:03 UTC"


def test_clock_phrase_treats_a_naive_datetime_as_utc() -> None:
    """A datetime with no zone is the same instant as UTC."""
    naive = datetime(2026, 10, 12, 13, 45, 3)
    aware = datetime(2026, 10, 12, 13, 45, 3, tzinfo=timezone.utc)
    assert clock_phrase(naive) == clock_phrase(aware)


def test_ordinals_for_the_first_days_and_the_teens() -> None:
    """1st, 2nd, 3rd, 11th, and 21st use the English endings."""
    cases = (
        (datetime(2026, 10, 1, tzinfo=timezone.utc), "1st"),
        (datetime(2026, 10, 2, tzinfo=timezone.utc), "2nd"),
        (datetime(2026, 10, 3, tzinfo=timezone.utc), "3rd"),
        (datetime(2026, 10, 11, tzinfo=timezone.utc), "11th"),
        (datetime(2026, 10, 21, tzinfo=timezone.utc), "21st"),
    )
    for moment, ordinal in cases:
        assert f" {ordinal} " in clock_phrase(moment)
