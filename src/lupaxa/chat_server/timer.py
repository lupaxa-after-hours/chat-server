"""Idle, alarm, login timeout, shutdown, and help reload ticks."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from .channel import broadcast_admin, broadcast_all
from .commands_list import Host
from .exceptions import ConfigError
from .help import help_stamp, load_help_into
from .net import roll
from .player import Player
from .saved_player import RANK_NEWBIE, load_saved, player_path, save_player

_FLUSH_SECONDS = 300
_HELP_CHECK_SECONDS = 300
_SHUTDOWN_MARKS = frozenset({60, 30, 15, 10, 5, 4, 3, 2})


def apply_timer_tick(player: Player, seconds: int) -> str | None:
    """Add ``seconds`` of idle and take them off any alarm.

    Return the alarm line when the alarm reaches zero.
    """
    if seconds <= 0:
        return None
    player.idle_seconds += seconds
    if player.alarm_seconds is None:
        return None
    player.alarm_seconds -= seconds
    if player.alarm_seconds > 0:
        return None
    player.alarm_seconds = None
    return "Your alarm has passed.\n"


def shutdown_phrase(seconds: int, reason: str) -> str:
    """Return the countdown line for ``seconds`` and ``reason``."""
    if seconds % 60 == 0:
        count = seconds // 60
        unit = "minute" if count == 1 else "minutes"
        shown = count
    else:
        unit = "second" if seconds == 1 else "seconds"
        shown = seconds
    return f"Shutting down in {shown} {unit} for '{reason}'\n"


def _announce_shutdown_pair(host: Host, reason: str) -> None:
    """Tell people in a room that one second remains, then that shutdown is now."""
    broadcast_all(host, shutdown_phrase(1, reason), skip=None)
    broadcast_all(host, "Shutting down now!\n", skip=None)


def apply_shutdown_tick(host: Host, whole: int) -> bool:
    """Decrease the one countdown. Return True when the server is stopping."""
    remaining = host._shutdown_seconds
    if remaining is None or whole <= 0:
        return False
    reason = host._shutdown_reason
    previous = remaining
    remaining -= whole
    if remaining <= 0:
        host._shutdown_seconds = None
        host._shutdown_reason = ""
        if previous > 1:
            _announce_shutdown_pair(host, reason)
        host.shutdown()
        return True
    host._shutdown_seconds = remaining
    if remaining == 1:
        _announce_shutdown_pair(host, reason)
    elif remaining in _SHUTDOWN_MARKS:
        broadcast_all(host, shutdown_phrase(remaining, reason), skip=None)
    return False


def apply_net_tick(host: Host, whole: int) -> None:
    """Subtract ``whole`` from the countdown and roll every counter at 0."""
    if whole <= 0:
        return
    host._net_count -= whole
    if host._net_count > 0:
        return
    host._net_count = 10
    roll(host._telnet_net)
    roll(host._ssh_net)
    roll(host._combined_net)
    for client in host._clients.values():
        roll(client.net)


def process_timer(host: Host) -> None:
    """Advance idle and alarm counters by the seconds elapsed since the last loop."""
    now = time.monotonic()
    mark, remainder, whole = consume_seconds(host._timer_mark, host._timer_remainder, now)
    host._timer_mark = mark
    host._timer_remainder = remainder
    if whole <= 0:
        return
    apply_net_tick(host, whole)
    for client in list(host._clients.values()):
        user = client.player
        if user is None:
            continue
        message = apply_timer_tick(user, whole)
        note_connected_time(host._roster_dir, user, whole)
        if message is not None:
            host.send(client, message)
    if apply_shutdown_tick(host, whole):
        return
    if host.is_stopping():
        return
    apply_login_timeouts(host, now)
    apply_help_check(host, whole)


def apply_help_check(host: Host, whole: int) -> None:
    """Reload help after each further 300 seconds when the file stamp is new."""
    if whole <= 0 or host.is_stopping():
        return
    host._help_check_seconds += whole
    if host._help_check_seconds < _HELP_CHECK_SECONDS:
        return
    host._help_check_seconds %= _HELP_CHECK_SECONDS
    _reload_help_if_changed(host)


def _reload_help_if_changed(host: Host) -> None:
    """Load a new stamp. Announce a failure once for that stamp."""
    stamp = help_stamp(host._config.help)
    if stamp == host._help_stamp or stamp == host._help_failed:
        return
    error = load_help_into(host)
    if error is None:
        return
    broadcast_admin(host, f"[ Admin ] Cannot reload help: {error}\n")


def apply_login_timeouts(host: Host, now: float) -> None:
    """Drop a connection that has no room and has used up its login minutes."""
    limit = host._config.login_timeout * 60
    for client in list(host._clients.values()):
        if client.room is not None:
            continue
        if now < client.accepted_at + limit:
            continue
        host.send(client, "Login timed out.\n")
        host.drop(client, announce=False)


def note_connected_time(directory: Path, player: Player, whole: int) -> None:
    """Add ``whole`` seconds and write the total after each further 300."""
    if whole <= 0:
        return
    player.connected_seconds += whole
    if player.rank == RANK_NEWBIE:
        return
    if player.connected_seconds - player.flushed_seconds < _FLUSH_SECONDS:
        return
    _write_connected_seconds(directory, player)


def consume_seconds(
    previous: float | None, remainder: float, now: float
) -> tuple[float, float, int]:
    """Return the new mark, the leftover fraction, and the whole seconds to apply."""
    if previous is None or now < previous:
        return now, remainder, 0
    remainder += now - previous
    whole = int(remainder)
    return now, remainder - whole, whole


def _write_connected_seconds(directory: Path, player: Player) -> None:
    """Write ``player``'s total to the saved file, and remember what was written."""
    try:
        saved = load_saved(player_path(directory, player.name))
        if saved is None:
            return
        save_player(directory, replace(saved, connected_seconds=player.connected_seconds))
    except (ConfigError, OSError) as exc:
        print(exc)
        return
    player.flushed_seconds = player.connected_seconds
