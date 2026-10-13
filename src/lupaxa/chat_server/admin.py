"""Commands only the admin can use."""

from __future__ import annotations

from dataclasses import replace

from .channel import broadcast_admin, broadcast_all, broadcast_staff, paint_admin, player_named
from .client import Client
from .colour import red
from .commands_list import COMMANDS, Command, Host
from .exceptions import ConfigError
from .globals import CANNOT_NOW
from .saved_player import (
    RANK_ADMIN,
    RANK_RESIDENT,
    RANK_SUPER,
    SavedPlayer,
    rank_label,
    save_player,
)
from .timer import shutdown_phrase
from .utilities import (
    _is_admin,
    _lookup,
    _name_of,
    center_block,
    frame_width,
    refuses_self,
)


def command_au(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Speak on the admin channel."""
    del command
    _admin_line(host, client, f"You say '{text}'", f"{_name_of(client)} says '{text}'")


def command_ae(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Emote on the admin channel."""
    del command
    _admin_line(host, client, f"You emote: {text}", f"{_name_of(client)} {text}")


def command_at(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Think on the admin channel."""
    del command
    _admin_line(
        host,
        client,
        f"You think . o O ( {text} )",
        f"{_name_of(client)} thinks . o O ( {text} )",
    )


def _admin_line(host: Host, client: Client, mine: str, theirs: str) -> None:
    """Send the sender ``mine`` and the rest of the admins ``theirs``."""
    if not host.send(client, paint_admin(client, f"[ Admin ] {mine}\n")):
        return
    broadcast_admin(host, f"[ Admin ] {theirs}\n", skip=client)


def command_allow(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Open or close newbies or residents. The config file stays as it was."""
    del command
    parts = (text or "").strip().split()
    if len(parts) != 2 or parts[0] not in ("newbies", "residents"):
        host.send(client, "Syntax: allow newbies|residents on|off\n")
        return
    if parts[1] not in ("on", "off"):
        host.send(client, "Syntax: allow newbies|residents on|off\n")
        return
    opened = parts[1] == "on"
    if parts[0] == "newbies":
        host._allow_newbies = opened
    else:
        host._allow_residents = opened
    verb = "opens" if opened else "closes"
    who = _name_of(client)
    broadcast_staff(host, f"< SU > {who} {verb} the talker to {parts[0]}.\n")


def command_broadcast(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Send a centred broadcast message to every player who is in a room."""
    del command
    message = (text or "").strip()
    if message == "":
        host.send(client, "Syntax: broadcast <message>\n")
        return
    plain = f"-=> Broadcast Messages from {client.name}: {message} <=-"
    for person in list(host._clients.values()):
        if person.name is None or person.room is None:
            continue
        shown = red(plain) if person.player is not None and person.player.colour else plain
        width = frame_width(person.discovered_columns, person.frame_columns)
        padded = center_block(shown + "\n", width).removesuffix("\n")
        host.send(person, f"\n\n\n{padded}\n\n\n\n")


def command_promote(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Make a saved resident a super user, or a super user an admin.

    Any admin may promote to super user. Only the config admin may promote to admin.
    The name must match in full.
    """
    del command
    name = (text or "").strip()
    if refuses_self(host, client, name):
        return
    if _is_admin(host, name):
        host.send(client, f"{name} is the admin.\n")
        return
    found, saved = _lookup(host, client, name)
    if not found:
        return
    if saved is None:
        refusal = (
            f"Player {name} not found.\n"
            if player_named(host._clients.values(), name) is None
            else f"{name} is not a resident.\n"
        )
        host.send(client, refusal)
        return
    if saved.rank == RANK_SUPER:
        if not _is_admin(host, _name_of(client)):
            host.send(
                client,
                f"Only the admin can promote {saved.name} to {rank_label(RANK_ADMIN)}.\n",
            )
            return
        if _set_rank(host, client, saved, RANK_ADMIN):
            label = rank_label(RANK_ADMIN)
            broadcast_admin(
                host,
                f"[ Admin ] {_name_of(client)} promotes {saved.name} to {label}.\n",
            )
        return
    if saved.rank == RANK_ADMIN:
        host.send(client, f"{saved.name} is already an admin.\n")
        return
    if saved.rank != RANK_RESIDENT:
        host.send(client, f"{name} is not a resident.\n")
        return
    if _set_rank(host, client, saved, RANK_SUPER):
        broadcast_staff(
            host,
            f"< SU > {_name_of(client)} promotes {saved.name} to {rank_label(RANK_SUPER)}.\n",
        )


def command_demote(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Move an admin or super user down one rank. ``force`` drops them to resident.

    Any admin may demote a super user. Only the config admin may demote an admin.
    """
    parsed = _demote_args(text)
    if parsed is None:
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    name, force = parsed
    if refuses_self(host, client, name):
        return
    if _is_admin(host, name):
        host.send(client, "The admin cannot be demoted.\n")
        return
    found, saved = _lookup(host, client, name)
    if not found:
        return
    connected = player_named(host._clients.values(), name)
    if saved is None and connected is None:
        host.send(client, f"Player {name} not found.\n")
        return
    if saved is None or saved.rank not in (RANK_SUPER, RANK_ADMIN):
        if saved is not None and saved.rank == RANK_RESIDENT:
            host.send(client, f"{saved.name} is a resident.\n")
            return
        shown = connected.player.name if connected is not None and connected.player else name
        host.send(client, f"{shown} cannot be demoted.\n")
        return
    if saved.rank == RANK_ADMIN and not _is_admin(host, _name_of(client)):
        host.send(
            client,
            f"Only the admin can demote {saved.name} from {rank_label(RANK_ADMIN)}.\n",
        )
        return
    if saved.rank == RANK_ADMIN and not force:
        if _set_rank(host, client, saved, RANK_SUPER):
            label = rank_label(RANK_ADMIN)
            broadcast_admin(
                host,
                f"[ Admin ] {_name_of(client)} demotes {saved.name} from {label}.\n",
            )
        return
    if not _set_rank(host, client, saved, RANK_RESIDENT):
        return
    if saved.rank == RANK_ADMIN:
        line = f"{_name_of(client)} demotes {saved.name} from {rank_label(RANK_ADMIN)}.\n"
        broadcast_admin(host, f"[ Admin ] {line}")
        broadcast_staff(host, f"< SU > {line}")
        return
    broadcast_staff(
        host,
        f"< SU > {_name_of(client)} demotes {saved.name} from {rank_label(RANK_SUPER)}.\n",
    )


def _demote_args(text: str | None) -> tuple[str, bool] | None:
    """Return the name and whether ``force`` was set, or ``None`` when the words are wrong."""
    parts = (text or "").strip().split()
    if len(parts) == 1:
        return parts[0], False
    if len(parts) == 2 and parts[1].lower() == "force":
        return parts[0], True
    return None


def command_shutdown(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Start, replace, cancel, or finish the one server countdown."""
    parts = (text or "").strip().split(None, 1)
    if len(parts) < 2 or parts[1].strip() == "":
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    word, reason = parts[0], parts[1].strip()
    if word == "now":
        host._shutdown_seconds = None
        host._shutdown_reason = ""
        broadcast_all(host, f"Shutting down now for '{reason}'.\n", skip=None)
        host.shutdown()
        return
    if word == "cancel":
        if host._shutdown_seconds is None:
            host.send(client, "No shutdown is running.\n")
            return
        host._shutdown_seconds = None
        host._shutdown_reason = ""
        broadcast_all(host, f"Shutdown cancelled for '{reason}'.\n", skip=None)
        return
    if not word.isascii() or not word.isdigit() or int(word) < 1:
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    seconds = int(word)
    host._shutdown_seconds = seconds
    host._shutdown_reason = reason
    broadcast_all(host, shutdown_phrase(seconds, reason), skip=None)
    if seconds == 1:
        broadcast_all(host, "Shutting down now!\n", skip=None)


def _set_rank(host: Host, client: Client, saved: SavedPlayer, rank: str) -> bool:
    """Write ``rank`` to the file of ``saved`` and to the live user, when connected."""
    try:
        save_player(host._roster_dir, replace(saved, rank=rank))
    except (ConfigError, OSError) as exc:
        print(exc)
        host.send(client, CANNOT_NOW)
        return False
    target = player_named(host._clients.values(), saved.name)
    if target is not None and target.player is not None:
        target.player.rank = rank
    return True


COMMANDS.extend(
    (
        Command(
            "allow",
            command_allow,
            False,
            "allow newbies|residents on|off",
            rank=RANK_ADMIN,
        ),
        Command("broadcast", command_broadcast, False, "broadcast <message>", rank=RANK_ADMIN),
        Command("promote", command_promote, False, "promote <name>", rank=RANK_ADMIN),
        Command(
            "demote",
            command_demote,
            False,
            "demote <name>|demote <name> force",
            rank=RANK_ADMIN,
        ),
        Command("ae", command_ae, False, "ae <message>", rank=RANK_ADMIN),
        Command("at", command_at, False, "at <message>", rank=RANK_ADMIN),
        Command("au", command_au, False, "au <message>", rank=RANK_ADMIN),
        Command(
            "shutdown",
            command_shutdown,
            False,
            "shutdown <seconds> <reason>",
            rank=RANK_ADMIN,
        ),
    )
)
