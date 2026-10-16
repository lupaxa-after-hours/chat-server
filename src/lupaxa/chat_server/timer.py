"""Idle, alarm, login timeout, shutdown, and help reload ticks."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .broadcast import broadcast_all, broadcast_coder
from .chain import close_idle_chains
from .config import HotConfig, load_banner, load_gate_file, load_hot_file
from .country import close_country_database, database_readable
from .exceptions import ConfigError
from .globals import COMMANDS
from .help import HelpStamp, help_stamp, load_help_into
from .netstats import roll
from .player import Player
from .ranks import Rank, rank_allows
from .roles import Roles
from .rooms import VOID_ID, Layout, house_path, load_house, load_layout, release_empty_houses, system_room_ids
from .saved_player import load_saved, player_path, save_player
from .server import Server
from .utilities import get_clients

FLUSH_SECONDS = 300
HELP_CHECK_SECONDS = 300
SHUTDOWN_MARKS = frozenset({60, 30, 15, 10, 5, 4, 3, 2})


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


def announce_shutdown_pair(server: Server, reason: str) -> None:
    """Tell people in a room that one second remains, then that shutdown is now."""
    broadcast_all(server, shutdown_phrase(1, reason), skip=None)
    broadcast_all(server, "Shutting down now!\n", skip=None)


def apply_shutdown_tick(server: Server, whole: int) -> bool:
    """Decrease the one countdown. Return True when the server is stopping."""
    remaining = server.shutdown_seconds
    if remaining is None or whole <= 0:
        return False
    reason = server.shutdown_reason
    previous = remaining
    remaining -= whole
    if remaining <= 0:
        server.shutdown_seconds = None
        server.shutdown_reason = ""
        if previous > 1:
            announce_shutdown_pair(server, reason)
        server.shutdown()
        return True
    server.shutdown_seconds = remaining
    if remaining == 1:
        announce_shutdown_pair(server, reason)
    elif remaining in SHUTDOWN_MARKS:
        broadcast_all(server, shutdown_phrase(remaining, reason), skip=None)
    return False


def apply_net_tick(server: Server, whole: int) -> None:
    """Subtract ``whole`` from the countdown and roll every counter at 0."""
    if whole <= 0:
        return
    server.net_count -= whole
    if server.net_count > 0:
        return
    server.net_count = 10
    roll(server.telnet_net)
    roll(server.ssh_net)
    roll(server.combined_net)
    for client in get_clients(server):
        roll(client.net)


def process_timer(server: Server) -> None:
    """Advance idle and alarm counters by the seconds elapsed since the last loop."""
    if not server.is_stopping():
        close_idle_chains(server, datetime.now(timezone.utc))
    now = time.monotonic()
    mark, remainder, whole = consume_seconds(server.timer_mark, server.timer_remainder, now)
    server.timer_mark = mark
    server.timer_remainder = remainder
    if whole <= 0:
        return
    apply_net_tick(server, whole)
    for client in list(get_clients(server)):
        user = client.player
        if user is None:
            continue
        message = apply_timer_tick(user, whole)
        note_connected_time(server.roster_dir, user, whole)
        if message is not None:
            server.send(client, message)
    if apply_shutdown_tick(server, whole):
        return
    if server.is_stopping():
        return
    apply_login_timeouts(server, now)
    apply_help_check(server, whole)


def message_file_stamps(messages_path: Path) -> dict[str, HelpStamp]:
    """Return the current stamp of each message file in the messages directory."""
    return {
        "banner": help_stamp(messages_path / "banner.msg"),
        "full": help_stamp(messages_path / "full.msg"),
        "no-more-newbies": help_stamp(messages_path / "no-more-newbies.msg"),
    }


def reload_messages(server: Server, *, force: bool) -> list[str]:
    """Re-read the three message files. Return one error line per bad file."""
    files = (
        ("banner", "banner.msg", "banner", "Banner"),
        ("full", "full.msg", "full", "Full"),
        ("no-more-newbies", "no-more-newbies.msg", "no-more-newbies", "No-more-newbies"),
    )
    directory = server.config.messages_path
    errors: list[str] = []
    for key, filename, missing_name, utf8_name in files:
        path = directory / filename
        stamp = help_stamp(path)
        if not force and (stamp == server.message_stamps.get(key) or stamp == server.message_failed.get(key)):
            continue
        try:
            text = load_banner(directory) if key == "banner" else load_gate_file(directory, filename, missing_name, utf8_name)
        except ConfigError as exc:
            server.message_failed[key] = stamp
            errors.append(f"Cannot reload {key}: {exc}")
            continue
        if key == "banner":
            server.banner = text
        elif key == "full":
            server.full_message = text
        else:
            server.newbie_message = text
        server.message_stamps[key] = stamp
        server.message_failed.pop(key, None)
    return errors


def announce_reload_failure(server: Server, text: str) -> None:
    """Tell the coder channel, and site admins below coder, about a failed automatic reload."""
    line = f"{{ Coder }} {text}\n"
    broadcast_coder(server, line)
    for person in list(get_clients(server)):
        user = person.player
        if user is None or (user.roles & int(Roles.SITE_ADMIN)) == 0:
            continue
        if rank_allows(user.rank, Rank.CODER):
            continue
        server.send(person, line)


def reload_hot_if_changed(server: Server) -> None:
    """Load a new hot stamp. Announce a failure once for that stamp."""
    stamp = help_stamp(server.config.hot_path)
    if stamp == server.hot_stamp or stamp == server.hot_failed:
        return
    error, _notice = reload_hot_config(server)
    if error is None:
        return
    announce_reload_failure(server, f"Cannot reload config: {error}")


def apply_help_check(server: Server, whole: int) -> None:
    """Reload hot, help, rooms, and message files after each further 300 seconds when a file stamp is new."""
    if whole <= 0 or server.is_stopping():
        return
    server.help_check_seconds += whole
    if server.help_check_seconds < HELP_CHECK_SECONDS:
        return
    server.help_check_seconds %= HELP_CHECK_SECONDS
    reload_hot_if_changed(server)
    reload_help_if_changed(server)
    for error in reload_rooms(server, force=False):
        announce_reload_failure(server, error)
    for error in reload_messages(server, force=False):
        announce_reload_failure(server, error)


def reload_hot_config(server: Server) -> tuple[str | None, str | None]:
    """Read the hot file and install it. Return the error and the country sentence."""
    path = server.config.hot_path
    stamp = help_stamp(path)
    try:
        hot = load_hot_file(path)
        system_rooms = system_room_ids(server.layout)
        if hot.start not in system_rooms:
            raise ConfigError(f"Start room {hot.start} is not in {server.config.rooms}.")
        if hot.newbie_start and hot.newbie_start not in system_rooms:
            raise ConfigError(f"Newbie start room {hot.newbie_start} is not in {server.config.rooms}.")
    except ConfigError as exc:
        server.hot_failed = stamp
        return str(exc), None
    notice = install_hot(server, hot)
    server.hot_stamp = stamp
    server.hot_failed = None
    return None, notice


def install_hot(server: Server, hot: HotConfig) -> str | None:
    """Replace the hot values. Return the country sentence when lookup turns off."""
    server.config = replace(
        server.config,
        start=hot.start,
        newbie_start=hot.newbie_start,
        login_timeout=hot.login_timeout,
        allow_newbies=hot.allow_newbies,
        allow_residents=hot.allow_residents,
        mfa_enabled=hot.mfa_enabled,
        country_lookup=hot.country_lookup,
        country_database=hot.country_database,
        max_connections=hot.max_connections,
        max_newbies=hot.max_newbies,
        obsolete_hot=hot.obsolete_keys,
    )
    server.allow_newbies = hot.allow_newbies
    server.allow_residents = hot.allow_residents
    server.mfa_enabled = hot.mfa_enabled
    if hot.mfa_enabled:
        from .mfa import register_mfa

        register_mfa(COMMANDS)
    with server.country_lock:
        close_country_database(server.country_db)
        server.country_db = None
        server.country_lookup = hot.country_lookup
        database = hot.country_database
        if server.country_lookup and (database is None or not database_readable(database)):
            shown = "the country database" if database is None else str(database)
            server.country_lookup = False
            return f"Country lookup is off. Cannot read {shown}."
    return None


def reload_help_if_changed(server: Server) -> None:
    """Load a new stamp. Announce a failure once for that stamp."""
    stamp = help_stamp(server.config.help)
    if stamp == server.help_stamp or stamp == server.help_failed:
        return
    error = load_help_into(server)
    if error is None:
        return
    announce_reload_failure(server, f"Cannot reload help: {error}")


def reload_rooms(server: Server, *, force: bool) -> list[str]:
    """Re-read system rooms and loaded houses. Return the error lines."""
    from .newbie import land_in_void

    system_stamp = help_stamp(server.config.rooms)
    read_system = force or (system_stamp != server.rooms_stamp and system_stamp != server.rooms_failed)
    if read_system:
        try:
            loaded = load_layout(server.config.rooms)
        except ConfigError as exc:
            server.rooms_failed = system_stamp
            return [f"Cannot reload rooms: {exc}"]
        if server.config.start not in loaded.rooms:
            server.rooms_failed = system_stamp
            return [f"Cannot reload rooms: Start room {server.config.start} is not in {server.config.rooms}."]
        newbie_start = server.config.newbie_start
        if newbie_start and newbie_start not in loaded.rooms:
            server.rooms_failed = system_stamp
            return [f"Cannot reload rooms: Newbie start room {newbie_start} is not in {server.config.rooms}."]
        base = loaded.with_void()
        server.rooms_stamp = system_stamp
        server.rooms_failed = None
    else:
        base = Layout(rooms={room_id: room for room_id, room in server.layout.rooms.items() if room_id not in server.layout.houses}, houses={})
        if VOID_ID not in base.rooms:
            base = base.with_void()
    errors: list[str] = []
    owners = sorted(set(server.layout.houses.values()))
    current = base
    for owner in owners:
        path = house_path(server.config.rooms, owner)
        stamp = help_stamp(path)
        known = server.house_failed.get(owner)
        if not force and (stamp == server.house_stamps.get(owner) or stamp == known):
            current = current.with_house(owner, {room_id: server.layout.rooms[room_id] for room_id, who in server.layout.houses.items() if who == owner})
            continue
        try:
            rooms = load_house(path, owner, system_room_ids(base))
        except ConfigError as exc:
            server.house_failed[owner] = stamp
            errors.append(f"Cannot reload {owner}'s rooms: {exc}")
            current = current.with_house(owner, {room_id: server.layout.rooms[room_id] for room_id, who in server.layout.houses.items() if who == owner})
            continue
        server.house_stamps[owner] = stamp
        server.house_failed.pop(owner, None)
        current = current.with_house(owner, rooms)
    server.layout = current
    for client in list(get_clients(server)):
        if client.room is not None and client.room not in server.layout.rooms:
            land_in_void(server, client)
    server.layout = release_empty_houses(server.layout, get_clients(server))
    return errors


def apply_login_timeouts(server: Server, now: float) -> None:
    """Drop a connection that has no room and has used up its login minutes."""
    limit = server.config.login_timeout * 60
    for client in list(get_clients(server)):
        if client.room is not None:
            continue
        if now < client.accepted_at + limit:
            continue
        server.send(client, "Login timed out.\n")
        server.drop(client, announce=False)


def note_connected_time(directory: Path, player: Player, whole: int) -> None:
    """Add ``whole`` seconds and write the total after each further 300."""
    if whole <= 0:
        return
    player.connected_seconds += whole
    if player.rank == Rank.NEWBIE:
        return
    if player.connected_seconds - player.flushed_seconds < FLUSH_SECONDS:
        return
    write_connected_seconds(directory, player)


def consume_seconds(previous: float | None, remainder: float, now: float) -> tuple[float, float, int]:
    """Return the new mark, the leftover fraction, and the whole seconds to apply."""
    if previous is None or now < previous:
        return now, remainder, 0
    remainder += now - previous
    whole = int(remainder)
    return now, remainder - whole, whole


def write_connected_seconds(directory: Path, player: Player) -> None:
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
