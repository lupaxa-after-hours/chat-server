"""Commands for super users and the admin."""

from __future__ import annotations

from .channel import (
    broadcast_staff,
    connections,
    newbies,
    paint_staff,
    player_named,
    who,
)
from .client import Client
from .colour import cyan
from .commands_list import COMMANDS, Command, Host
from .entry import _ask
from .exceptions import ConfigError
from .globals import CANNOT_NOW
from .password import set_password_prompt
from .saved_player import (
    RANK_NEWBIE,
    RANK_SUPER,
    SavedPlayer,
    delete_player,
    rank_allows,
    savable_name,
)
from .time import idle_clock
from .utilities import (
    _find_saved,
    _is_admin,
    _lookup,
    _name_of,
    _name_prefixes_argument,
    _rank_of,
    _saved_players,
    frame_width,
    list_frame,
    refuses_self,
)


def command_resident(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Ask a connected newbie for a password so they can be saved as a resident.

    The name must match in full. A prefix is not enough.
    """
    del command
    name = (text or "").strip()
    if refuses_self(host, client, name):
        return
    if _is_admin(host, name):
        host.send(client, "The admin cannot be removed.\n")
        return
    found, saved = _lookup(host, client, name)
    if not found:
        return
    if saved is not None:
        host.send(client, f"{name} is already saved.\n")
        return
    target = player_named(host._clients.values(), name)
    if target is None or target.player is None:
        host.send(client, f"Player {name} not found.\n")
        return
    if target.player.rank != RANK_NEWBIE:
        host.send(client, f"{name} is already saved.\n")
        return
    if not savable_name(target.player.name):
        host.send(client, "That name cannot be saved.\n")
        return
    _ask(target, "set", target.player.name, None)
    target.pending_issuer = _name_of(client)
    broadcast_staff(
        host,
        f"< SU > {_name_of(client)} makes {target.player.name} a resident.\n",
    )
    host.send(target, set_password_prompt())


def command_nuke(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Delete a saved player and drop them when they are connected.

    The name must match in full. A prefix is not enough.
    """
    resolved = _resolve_nuke(host, text)
    if resolved is None:
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    if isinstance(resolved, str):
        host.send(client, f"Player {resolved} not found.\n")
        return
    name, reason = resolved
    if refuses_self(host, client, name):
        return
    try:
        saved = _find_saved(host, name)
    except (ConfigError, OSError) as exc:
        print(exc)
        saved = None
    target = player_named(host._clients.values(), name if saved is None else saved.name)
    ranks = []
    if saved is not None:
        ranks.append(saved.rank)
    if target is not None and target.player is not None:
        ranks.append(target.player.rank)
    if any(not _may_remove(host, client, rank) for rank in ranks):
        shown = saved.name if saved is not None else name
        host.send(client, f"You cannot nuke {shown}.\n")
        return
    try:
        deleted = delete_player(host._roster_dir, name)
    except OSError as exc:
        print(exc)
        host.send(client, CANNOT_NOW)
        return
    if not deleted:
        host.send(client, f"Player {name} not found.\n")
        return
    shown = name if target is None or target.name is None else target.name
    if target is not None:
        host.send(target, "You have been removed.\n")
        host.drop(target, announce=True)
    broadcast_staff(host, f"< SU > {_name_of(client)} nukes {shown}: {reason}\n")


def command_warn(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Warn one named player and tell the staff channel."""
    parts = (text or "").strip().split(None, 1)
    if len(parts) < 2:
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    name, reason = parts
    if refuses_self(host, client, name):
        return
    target = player_named(host._clients.values(), name)
    if target is None or target.player is None:
        host.send(client, f"Player {name} not found.\n")
        return
    plain = f"-=> {_name_of(client)} warn you: {reason}"
    shown = cyan(plain) if target.player.colour else plain
    host.send(target, f"\n\n{shown}\n\n\n\a")
    staff = f"< SU > {_name_of(client)} warns {target.player.name} : {reason}\n"
    broadcast_staff(host, staff)


def command_disconnect(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Drop a player of lower rank who is already in a room.

    The name must match in full. A prefix is not enough.
    """
    resolved = _resolve_disconnect(host, text)
    if resolved is None:
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    if isinstance(resolved, str):
        host.send(client, f"Player {resolved} not found.\n")
        return
    name, reason = resolved
    if refuses_self(host, client, name):
        return
    target = player_named(host._clients.values(), name)
    if target is None or target.player is None:
        host.send(client, f"Player {name} not found.\n")
        return
    if not _may_remove(host, client, target.player.rank):
        host.send(client, f"You cannot disconnect {target.name}.\n")
        return
    shown = target.name or name
    host.send(target, "You have been disconnected.\n")
    host.drop(target, announce=True)
    broadcast_staff(host, f"< SU > {_name_of(client)} disconnects {shown}: {reason}\n")


def _may_remove(host: Host, client: Client, target_rank: str) -> bool:
    """Return whether this caller outranks ``target_rank``."""
    del host
    return not rank_allows(target_rank, _rank_of(client))


def command_su(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Speak on the super-user channel."""
    del command
    _staff_line(host, client, f"You say '{text}'", f"{_name_of(client)} says '{text}'")


def command_se(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Emote on the super-user channel."""
    del command
    _staff_line(host, client, f"You emote: {text}", f"{_name_of(client)} {text}")


def command_st(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Think on the super-user channel."""
    del command
    _staff_line(
        host,
        client,
        f"You think . o O ( {text} )",
        f"{_name_of(client)} thinks . o O ( {text} )",
    )


def command_lsn(host: Host, client: Client, command: Command, text: str | None) -> None:
    """List connected newbies and the address each one came from."""
    del command
    if text:
        host.send(client, "Syntax: lsn\n")
        return
    rows = [
        (name, hostname, host._layout.display_name(room_id), idle_clock(idle))
        for name, hostname, room_id, idle in newbies(host._clients.values())
    ]
    if not rows:
        host.send(client, " -=> There are no newbies connected currently.\n")
        return
    host.send(
        client,
        list_frame(
            ("Name", "Address", "Room", "Idle"),
            rows,
            frame_width(client.discovered_columns, client.frame_columns),
        ),
    )


def command_connections(host: Host, client: Client, command: Command, text: str | None) -> None:
    """List every open socket, including people still logging in."""
    del command
    if text:
        host.send(client, "Syntax: connections\n")
        return
    host.send(
        client,
        list_frame(
            ("Name", "Hostname", "IP Address", "Type"),
            connections(host._clients.values()),
            frame_width(client.discovered_columns, client.frame_columns),
        ),
    )


def _staff_line(host: Host, client: Client, mine: str, theirs: str) -> None:
    """Send the sender ``mine`` and the rest of the staff channel ``theirs``."""
    if not host.send(client, paint_staff(client, f"< SU > {mine}\n")):
        return
    broadcast_staff(host, f"< SU > {theirs}\n", skip=client)


def _resolve_nuke(host: Host, text: str | None) -> tuple[str, str] | str | None:
    """Return a stored name and reason, a shorter missing name, or ``None``.

    ``None`` means the reason is missing. A bare string is a typed name that
    is not a saved player and is only the start of a longer saved name.
    """
    argument = (text or "").strip()
    if argument == "":
        return None
    saved_players = _saved_players(host)
    longest: SavedPlayer | None = None
    for saved in saved_players:
        if not _name_prefixes_argument(argument, saved.name):
            continue
        if longest is None or len(saved.name) > len(longest.name):
            longest = saved
    if longest is not None:
        reason = argument[len(longest.name) :].strip()
        if reason == "":
            return None
        return longest.name, reason
    if " " not in argument:
        lowered = argument.lower()
        for saved in saved_players:
            if saved.name.lower().startswith(f"{lowered} "):
                return argument
    parts = argument.split(None, 1)
    if len(parts) < 2:
        return None
    return parts[0], parts[1].strip()


def _resolve_disconnect(host: Host, text: str | None) -> tuple[str, str] | str | None:
    """Return the live name and reason, a missing name, or ``None``."""
    argument = (text or "").strip()
    if argument == "":
        return None
    names = [room_name for room_name, _rank, _room, _status, _idle in who(host._clients.values())]
    longest: str | None = None
    for room_name in names:
        if not _name_prefixes_argument(argument, room_name):
            continue
        if longest is None or len(room_name) > len(longest):
            longest = room_name
    if longest is not None:
        reason = argument[len(longest) :].strip()
        if reason == "":
            return None
        return longest, reason
    parts = argument.split(None, 1)
    if len(parts) < 2 or parts[1].strip() == "":
        return None
    return parts[0]


COMMANDS.extend(
    (
        Command("resident", command_resident, False, "resident <name>", rank=RANK_SUPER),
        Command("nuke", command_nuke, False, "nuke <name> <reason>", rank=RANK_SUPER),
        Command(
            "disconnect",
            command_disconnect,
            False,
            "disconnect <name> <reason>",
            rank=RANK_SUPER,
        ),
        Command("warn", command_warn, False, "warn <name> <reason>", rank=RANK_SUPER),
        Command("su", command_su, False, "su <message>", rank=RANK_SUPER),
        Command("se", command_se, False, "se <message>", rank=RANK_SUPER),
        Command("st", command_st, False, "st <message>", rank=RANK_SUPER),
        Command("lsn", command_lsn, False, None, rank=RANK_SUPER),
        Command("connections", command_connections, False, None, rank=RANK_SUPER),
        Command(
            "conns",
            command_connections,
            True,
            None,
            needs_space=True,
            rank=RANK_SUPER,
        ),
    )
)
