"""Hold a name during a password prompt, and put a player in the start room."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from .channel import broadcast_room_split, occupancy, others, player_named
from .client import Client
from .commands_list import Host
from .defaults import DEFAULT_PROMPT
from .exceptions import ConfigError
from .globals import NAME_PROMPT
from .player import Player
from .rooms import full_line
from .saved_player import RANK_NEWBIE, RANK_SUPER, SavedPlayer, rank_allows, save_player
from .utilities import (
    _find_saved,
    connected_from_line,
    connected_line,
    reconnected_from_line,
    reconnected_line,
)


def name_taken_reply(name: str) -> str:
    """Return the line that sends a duplicate name back to the name prompt."""
    return f"{name} is already in use. Pick another name.\n\n{NAME_PROMPT}"


def player_prompt(client: Client) -> str:
    """Return the prompt this player types at, with no trailing newline."""
    if client.player is None:
        return DEFAULT_PROMPT
    prompt = client.player.prompt
    if prompt.endswith(" "):
        return prompt
    return prompt + " "


def _ask(client: Client, stage: str, name: str, saved: SavedPlayer | None) -> None:
    """Hold ``name`` while the player answers a password prompt."""
    client.auth = stage
    client.pending_name = name
    client.pending_saved = saved
    client.pending_password = ""
    client.pending_current_password = ""
    client.pending_mfa_secret = ""
    client.pending_issuer = ""
    client.password_failures = 0


def _restart_login(client: Client) -> None:
    """Forget the held name and go back to asking for one."""
    _ask(client, "name", "", None)


def _closed_status(host: Host) -> str | None:
    """Return the staff line for a closed talker, or None when it is open."""
    if not host._allow_residents:
        return "[ Status ] The talker is closed to newbies and residents.\n"
    if not host._allow_newbies:
        return "[ Status ] The talker is closed to newbies.\n"
    return None


def _enter(host: Host, client: Client, name: str, rank: str) -> None:
    """Put the player in the start room, or refuse them when it is full."""
    taken = {item.lower() for item in host._taken_names()}
    if name.lower() in host._unsaved_names(skip=client) or name.lower() in taken:
        _restart_login(client)
        host.send(client, name_taken_reply(name))
        return
    start = host._config.start
    if occupancy(host._clients.values(), start) >= host._layout.rooms[start].max_occupancy:
        host.send(client, full_line(host._layout.display_name(start)))
        host.drop(client, announce=False)
        return
    _restart_login(client)
    newbie_text = "I am new here"
    description = newbie_text if rank == RANK_NEWBIE else ""
    client.player = Player(
        name,
        start,
        datetime.now(timezone.utc),
        rank=rank,
        description=description,
    )
    _load_profile(host, client.player)
    _remember_address(host, client)
    broadcast_room_split(
        host,
        start,
        connected_line(name),
        connected_from_line(name, client.address),
        skip=client,
    )
    brief = host._layout.brief(start, others(host._clients.values(), start, client))
    if host.send(client, brief):
        user = client.player
        if user is not None and rank_allows(user.rank, RANK_SUPER):
            status = _closed_status(host)
            if status is not None:
                host.send(client, status)
        host.send(client, player_prompt(client))


def _enter_fresh(host: Host, client: Client, held: SavedPlayer) -> None:
    """Enter with the rank in the file now, or go back to the name prompt when it is gone."""
    try:
        fresh = _find_saved(host, held.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
        return
    if fresh is None:
        _restart_login(client)
        host.send(client, NAME_PROMPT)
        return
    live = player_named(host._clients.values(), fresh.name)
    if live is not None:
        _reconnect(host, live, client, fresh)
        return
    _enter(host, client, fresh.name, fresh.rank)


def _reconnect(host: Host, live: Client, newcomer: Client, fresh: SavedPlayer) -> None:
    """Keep ``live`` and attach ``newcomer``'s socket to it."""
    user = live.player
    if user is None:
        _enter(host, newcomer, fresh.name, fresh.rank)
        return
    user.idle_seconds = 0
    user.rank = fresh.rank
    host._replace_socket(live, newcomer)
    if live.auth in ("current", "renew", "renew_confirm"):
        _restart_login(live)
    _remember_address(host, live)
    broadcast_room_split(
        host,
        user.room,
        reconnected_line(user.name),
        reconnected_from_line(user.name, live.address),
        skip=live,
    )
    brief = host._layout.brief(user.room, others(host._clients.values(), user.room, live))
    if host.send(live, brief):
        user = live.player
        if user is not None and rank_allows(user.rank, RANK_SUPER):
            status = _closed_status(host)
            if status is not None:
                host.send(live, status)
        host.send(live, player_prompt(live))


def _load_profile(host: Host, user: Player) -> None:
    """Start a saved player's total and prompt from the file. A newbie keeps the defaults."""
    if user.rank == RANK_NEWBIE:
        return
    try:
        saved = _find_saved(host, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        return
    if saved is None:
        return
    user.connected_seconds = saved.connected_seconds
    user.flushed_seconds = saved.connected_seconds
    if saved.prompt is not None:
        user.prompt = saved.prompt
    if saved.entermsg is not None:
        user.entermsg = saved.entermsg
    user.colour = saved.colour
    user.description = saved.description


def _remember_address(host: Host, client: Client) -> None:
    """Save where a saved player connected from, when it differs from the file."""
    user = client.player
    if user is None or user.rank == RANK_NEWBIE:
        return
    try:
        saved = _find_saved(host, user.name)
        if saved is not None and (
            saved.ip_address != client.address or saved.hostname != client.hostname
        ):
            save_player(
                host._roster_dir,
                replace(saved, ip_address=client.address, hostname=client.hostname),
            )
    except (ConfigError, OSError) as exc:
        print(exc)
