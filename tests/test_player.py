"""How long a player has been connected."""

from datetime import datetime, timedelta, timezone

from lupaxa.chat_server.player import Player
from lupaxa.chat_server.time import connected_phrase
from lupaxa.chat_server.timer import apply_timer_tick

_START = datetime(2026, 10, 12, tzinfo=timezone.utc)


def test_connected_phrase_uses_the_largest_unit() -> None:
    """Seconds, minutes, hours, and days each use singular and plural."""
    assert connected_phrase(_START, _START) == "0 seconds"
    assert connected_phrase(_START, _START + timedelta(seconds=1)) == "1 second"
    assert connected_phrase(_START, _START + timedelta(seconds=59)) == "59 seconds"
    assert connected_phrase(_START, _START + timedelta(seconds=60)) == "1 minute"
    assert connected_phrase(_START, _START + timedelta(minutes=2)) == "2 minutes"
    assert connected_phrase(_START, _START + timedelta(hours=1)) == "1 hour"
    assert connected_phrase(_START, _START + timedelta(hours=1, minutes=5)) == ("1 hour, 5 minutes")
    assert connected_phrase(_START, _START + timedelta(days=1)) == "1 day"
    assert connected_phrase(_START, _START + timedelta(days=2, hours=3)) == "2 days, 3 hours"


def test_timer_tick_counts_idle_and_fires_the_alarm() -> None:
    """Idle grows every tick. The alarm sends once when it reaches zero."""
    user = Player("Ada", "hall", _START, alarm_seconds=3)
    assert apply_timer_tick(user, 2) is None
    assert user.idle_seconds == 2
    assert user.alarm_seconds == 1
    assert apply_timer_tick(user, 2) == "Your alarm has passed.\n"
    assert user.alarm_seconds is None
    assert user.idle_seconds == 4
    assert apply_timer_tick(user, 1) is None


def test_a_player_keeps_a_lowercase_key() -> None:
    """The display spelling and the key are both stored."""
    user = Player("Bob", "hall", datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert user.name == "Bob"
    assert user.lowercase_name == "bob"
