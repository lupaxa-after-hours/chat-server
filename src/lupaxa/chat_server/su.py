"""Commands for super users and the admin."""

from __future__ import annotations  # noqa: I001

from collections.abc import Iterable

from .broadcast import broadcast_room, broadcast_staff
from .client import Client
from .commands import Command, shown_syntax
from .colour import can_see_colour, cyan, paint_idle, paint_staff
from .entry import ask
from .exceptions import ConfigError
from .globals import CANNOT_NOW, COMMANDS
from .password import password_prompt, set_password_prompt
from .player import find_player, is_logged_in, match_online_player
from .rooms import full_line, is_full, others, release_empty_houses, spoken_line, system_room_ids, unlink_house
from .saved_player import SavedPlayer, delete_player, savable_name
from .server import Server
from .utilities import find_saved, frame_width, get_clients, get_parts, get_player_name, get_saved_player, has_rank, is_admin, list_frame, name_prefixes_argument, refuses_self, saved_players  # fmt: skip  # noqa: E501
from .ranks import Rank, rank_allows


def command_resident(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Ask a connected newbie for a password so they can be saved as a resident.

    The name must match in full. A prefix is not enough.
    """
    name = get_parts(text)
    if refuses_self(server, client, name):
        return
    try:
        if is_admin(server, name):
            server.send(client, "The super admin cannot be removed.\n")
            return
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, CANNOT_NOW)
        return
    found, saved = get_saved_player(server, client, name, quiet=True)
    if not found:
        server.send(client, CANNOT_NOW)
        return
    if saved is not None:
        server.send(client, f"{name} is already saved.\n")
        return
    target = find_player(server, client, name)
    if target is None or target.player is None:
        return
    if target.player.rank != Rank.NEWBIE:
        server.send(client, f"{name} is already saved.\n")
        return
    if not savable_name(target.player.name):
        server.send(client, "That name cannot be saved.\n")
        return
    ask(target, "set", target.player.name, None)
    target.pending_issuer = get_player_name(client)
    broadcast_staff(server, f"< SU > {get_player_name(client)} makes {target.player.name} a resident.\n")
    server.send(target, set_password_prompt())


def command_nuke(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Delete a saved player and drop them when they are connected.

    The name must match in full. A prefix is not enough.
    """
    resolved = resolve_nuke(server, text)
    if resolved is None:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    if isinstance(resolved, str):
        server.send(client, f"Player {resolved} not found.\n")
        return
    name, reason = resolved
    if refuses_self(server, client, name):
        return
    try:
        saved = find_saved(server, name)
    except (ConfigError, OSError) as exc:
        print(exc)
        saved = None
    target = find_player(server, client, name if saved is None else saved.name, quiet=True)
    ranks = []
    if saved is not None:
        ranks.append(saved.rank)
    if target is not None and target.player is not None:
        ranks.append(target.player.rank)
    if any(not may_remove(server, client, rank) for rank in ranks):
        shown = saved.name if saved is not None else name
        server.send(client, f"You cannot nuke {shown}.\n")
        return
    try:
        deleted = delete_player(server.roster_dir, name)
    except OSError as exc:
        print(exc)
        server.send(client, CANNOT_NOW)
        return
    if not deleted:
        server.send(client, f"Player {name} not found.\n")
        return
    unlink_house(server, name)
    shown = name if target is None or target.name is None else target.name
    if target is not None:
        server.send(target, "You have been removed.\n")
        server.drop(target, announce=True)
    broadcast_staff(server, f"< SU > {get_player_name(client)} nukes {shown}: {reason}\n")


def command_warn(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Warn one named player and tell the staff channel."""
    parts = get_parts(text).split(None, 1)
    if len(parts) < 2:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    name, reason = parts
    if refuses_self(server, client, name):
        return
    target = find_player(server, client, name)
    if target is None or target.player is None:
        return
    if not may_remove(server, client, target.player.rank):
        server.send(client, f"You cannot warn {target.name}.\n")
        return
    plain = f"-=> {get_player_name(client)} warn you: {reason}"
    shown = cyan(plain) if can_see_colour(target) else plain
    server.send(target, f"\n\n{shown}\n\n\n\a")
    staff = f"< SU > {get_player_name(client)} warns {target.player.name} : {reason}\n"
    broadcast_staff(server, staff)


def command_disconnect(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Drop a player of lower rank who is already in a room.

    The name must match in full. A prefix is not enough.
    """
    parts = get_parts(text).split(None, 1)
    if len(parts) < 2:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    name, reason = parts
    if refuses_self(server, client, name):
        return
    target = find_player(server, client, name)
    if target is None or target.player is None:
        return
    if not may_remove(server, client, target.player.rank):
        server.send(client, f"You cannot disconnect {target.name}.\n")
        return
    shown = target.name or name
    server.send(target, "You have been disconnected.\n")
    server.drop(target, announce=True)
    broadcast_staff(server, f"< SU > {get_player_name(client)} disconnects {shown}: {reason}\n")


def command_grab(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Pull a lower-rank player into this super user's office room."""
    from .newbie import enter_sentence

    del command
    name = get_parts(text)
    target, error = match_online_player(get_clients(server), name)
    if error is not None:
        server.send(client, error)
        return
    if target is None or target.room is None or target.player is None:
        server.send(client, f"Player {name} not found.\n")
        return
    if target is client or (target.name or "").casefold() == (client.name or "").casefold():
        server.send(client, "You cannot do that to yourself.\n")
        return
    if not may_remove(server, client, target.player.rank):
        server.send(client, f"You cannot grab {target.name}.\n")
        return
    if target.room == client.room:
        server.send(client, f"But {target.name} is already in the same room as you!\n")
        return
    destination = client.room
    if destination is None or destination not in system_room_ids(server.layout):
        server.send(client, "You can only grab someone into a system room.\n")
        return
    if is_full(server.layout, get_clients(server), destination):
        server.send(client, full_line(server.layout.display_name(destination)))
        return
    origin = target.room
    broadcast_room(server, origin, spoken_line(target.name, f"is grabbed by {client.name}"), skip=target)
    target.room = destination
    server.layout = release_empty_houses(server.layout, get_clients(server))
    grabbed = f" -=> {client.name} stretches out and grabs you !!!!!!\n"
    if not server.send(target, grabbed):
        return
    brief = server.layout.brief(destination, others(get_clients(server), destination, target))
    if not server.send(target, brief):
        return
    broadcast_room(server, destination, spoken_line(target.name, enter_sentence(target)), skip=target)


def may_remove(server: Server, client: Client, target_rank: Rank) -> bool:
    """Return whether this caller outranks ``target_rank``."""
    return not rank_allows(target_rank, has_rank(client))


def command_su(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Speak on the super-user channel."""
    staff_line(server, client, f"You say '{text}'", f"{get_player_name(client)} says '{text}'")


def command_se(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Emote on the super-user channel."""
    staff_line(server, client, f"You emote: {text}", f"{get_player_name(client)} {text}")


def command_st(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Think on the super-user channel."""
    staff_line(server, client, f"You think . o O ( {text} )", f"{get_player_name(client)} thinks . o O ( {text} )")


def command_off_duty(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Step away from the staff role until on_duty."""
    user = client.player
    reason = (text or "").strip()
    if user is None or reason == "":
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    if user.off_duty:
        server.send(client, "You are already off duty.\n")
        return
    broadcast_staff(server, f"< SU > {user.name} goes off duty because '{reason}'.\n")
    user.off_duty = True


def command_on_duty(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Ask for the saved password before returning to the staff role."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    user = client.player
    if user is None:
        return
    if not user.off_duty:
        server.send(client, "You are already on duty.\n")
        return
    client.auth = "on_duty_password"
    server.send(client, password_prompt())


def newbies(clients: Iterable[Client]) -> list[tuple[str, str, str, int]]:
    """Return each connected newbie's name, hostname, room, and idle seconds, sorted."""
    rows: list[tuple[str, str, str, int]] = []
    for client in clients:
        user = client.player
        if not is_logged_in(client) or user is None or user.rank != Rank.NEWBIE:
            continue
        rows.append((user.name, client.hostname, user.room, user.idle_seconds))
    rows.sort()
    return rows


def command_lsn(server: Server, client: Client, command: Command, text: str | None) -> None:
    """List connected newbies and the address each one came from."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    rows = [(name, hostname, server.layout.location_name(room_id, name), paint_idle(idle, can_see_colour(client))) for name, hostname, room_id, idle in newbies(get_clients(server))]
    if not rows:
        server.send(client, " -=> There are no newbies connected currently.\n")
        return
    server.send(client, list_frame(("Name", "Address", "Room", "Idle"), rows, frame_width(client.discovered_columns, client.frame_columns)))


def connections(clients: Iterable[Client], *, country: bool = False) -> list[tuple[str, ...]]:
    """Return each socket's name, hostname, IP, optional country, and telnet or ssh."""
    rows: list[tuple[str, ...]] = []
    for client in clients:
        player = client.player
        if player is not None:
            name = player.name
        elif client.pending_name:
            name = client.pending_name
        else:
            name = "<pending>"
        kind = "ssh" if client.ssh else "telnet"
        if country:
            rows.append((name, client.hostname, client.address, client.country_name, kind))
        else:
            rows.append((name, client.hostname, client.address, kind))
    rows.sort(key=lambda row: (row[0], row[2]))
    return rows


def command_connections(server: Server, client: Client, command: Command, text: str | None) -> None:
    """List every open socket, including people still logging in."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    headers = ("Name", "Hostname", "IP Address", "Country", "Type") if server.country_lookup else ("Name", "Hostname", "IP Address", "Type")
    server.send(client, list_frame(headers, connections(get_clients(server), country=server.country_lookup), frame_width(client.discovered_columns, client.frame_columns)))  # fmt: skip  # noqa: E501


def staff_line(server: Server, client: Client, mine: str, theirs: str) -> None:
    """Send the sender ``mine`` and the rest of the staff channel ``theirs``."""
    if not server.send(client, paint_staff(client, f"< SU > {mine}\n")):
        return
    broadcast_staff(server, f"< SU > {theirs}\n", skip=client)


def resolve_nuke(server: Server, text: str | None) -> tuple[str, str] | str | None:
    """Return a stored name and reason, a shorter missing name, or ``None``.

    ``None`` means the reason is missing. A bare string is a typed name that
    is not a saved player and is only the start of a longer saved name.
    """
    argument = get_parts(text)
    found = saved_players(server)
    longest: SavedPlayer | None = None
    for saved in found:
        if not name_prefixes_argument(argument, saved.name):
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
        for saved in found:
            if saved.name.lower().startswith(f"{lowered} "):
                return argument
    parts = argument.split(None, 1)
    if len(parts) < 2:
        return None
    return parts[0], parts[1].strip()


COMMANDS.extend(
    (
        Command("resident", command_resident, False, "<name>", rank=Rank.SUPER_USER),
        Command("nuke", command_nuke, False, "<name> <reason>", rank=Rank.SUPER_USER),
        Command("disconnect", command_disconnect, False, "<name> <reason>", rank=Rank.SUPER_USER),
        Command("grab", command_grab, False, "<name>", rank=Rank.SUPER_USER),
        Command("warn", command_warn, False, "<name> <reason>", rank=Rank.SUPER_USER),
        Command("su", command_su, False, "<message>", rank=Rank.SU_CHANNEL),
        Command("se", command_se, False, "<message>", rank=Rank.SU_CHANNEL),
        Command("st", command_st, False, "<message>", rank=Rank.SU_CHANNEL),
        Command("off_duty", command_off_duty, False, "<reason>", rank=Rank.SU_CHANNEL),
        Command("on_duty", command_on_duty, False, None, rank=Rank.SU_CHANNEL),
        Command("lsn", command_lsn, False, None, rank=Rank.SU_CHANNEL),
        Command("connections", command_connections, False, None, rank=Rank.SU_CHANNEL),
        Command("conns", command_connections, True, None, needs_space=True, rank=Rank.SU_CHANNEL),
    )
)
