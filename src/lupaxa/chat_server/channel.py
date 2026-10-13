"""Broadcast lines and list who is connected."""

from __future__ import annotations

from collections.abc import Iterable

from .client import Client
from .colour import cyan, red, yellow
from .commands_list import Host
from .saved_player import RANK_ADMIN, RANK_NEWBIE, RANK_SUPER, rank_allows


def paint_staff(client: Client, text: str) -> str:
    """Return a staff-channel line, cyan when the recipient has colour on."""
    if client.player is not None and client.player.colour:
        return cyan(text.rstrip("\n")) + "\n"
    return text


def paint_admin(client: Client, text: str) -> str:
    """Return an admin-channel line, red when the recipient has colour on."""
    if client.player is not None and client.player.colour:
        return red(text.rstrip("\n")) + "\n"
    return text


def playing(client: Client, room_id: str) -> bool:
    """Return whether ``client`` has a name and is in ``room_id``."""
    return client.name is not None and client.room == room_id


def occupancy(clients: Iterable[Client], room_id: str) -> int:
    """Return how many playing clients are in ``room_id``."""
    return sum(1 for client in clients if playing(client, room_id))


def others(clients: Iterable[Client], room_id: str, skip: Client) -> list[str]:
    """Return the names of the other players in ``room_id``."""
    return [
        client.name
        for client in clients
        if playing(client, room_id) and client is not skip and client.name is not None
    ]


def broadcast_room(server: Host, room_id: str | None, text: str, *, skip: Client | None) -> None:
    """Send ``text`` to every player in ``room_id`` except ``skip``."""
    if room_id is None or text == "":
        return
    for client in list(server._clients.values()):
        if not playing(client, room_id):
            continue
        if skip is not None and client is skip:
            continue
        server.send(client, text)


def broadcast_room_split(
    server: Host,
    room_id: str | None,
    plain: str,
    staff: str,
    *,
    skip: Client | None,
) -> None:
    """Send ``staff`` to super users in the room and ``plain`` to everyone else."""
    if room_id is None:
        return
    for client in list(server._clients.values()):
        if not playing(client, room_id):
            continue
        if skip is not None and client is skip:
            continue
        user = client.player
        if user is not None and rank_allows(user.rank, RANK_SUPER):
            server.send(client, staff)
        else:
            server.send(client, plain)


def broadcast_shout(server: Host, line: str, *, skip: Client | None) -> None:
    """Send a shout line to every player in a room except ``skip``."""
    if line == "":
        return
    for client in list(server._clients.values()):
        if client.name is None or client.room is None:
            continue
        if skip is not None and client is skip:
            continue
        if client.player is not None and client.player.colour:
            server.send(client, yellow(line) + "\n")
        else:
            server.send(client, line + "\n")


def broadcast_all(server: Host, text: str, *, skip: Client | None) -> None:
    """Send ``text`` to every player who has a name and a room, except ``skip``."""
    if text == "":
        return
    for client in list(server._clients.values()):
        if client.name is None or client.room is None:
            continue
        if skip is not None and client is skip:
            continue
        server.send(client, text)


def broadcast_staff(server: Host, text: str, *, skip: Client | None = None) -> None:
    """Send ``text`` to every super user except ``skip``."""
    if text == "":
        return
    for client in list(server._clients.values()):
        user = client.player
        if user is None or not rank_allows(user.rank, RANK_SUPER):
            continue
        if skip is not None and client is skip:
            continue
        server.send(client, paint_staff(client, text))


def broadcast_admin(server: Host, text: str, *, skip: Client | None = None) -> None:
    """Send ``text`` to every admin except ``skip``."""
    if text == "":
        return
    for client in list(server._clients.values()):
        user = client.player
        if user is None or not rank_allows(user.rank, RANK_ADMIN):
            continue
        if skip is not None and client is skip:
            continue
        server.send(client, paint_admin(client, text))


def who(clients: Iterable[Client]) -> list[tuple[str, str, str, str, int]]:
    """Return each playing name, rank, room, status, and idle seconds, sorted by name."""
    rows: list[tuple[str, str, str, str, int]] = []
    for client in clients:
        player = client.player
        if player is None or client.room is None:
            continue
        rows.append((player.name, player.rank, client.room, player.status, player.idle_seconds))
    rows.sort()
    return rows


def connections(clients: Iterable[Client]) -> list[tuple[str, str, str, str]]:
    """Return each socket's name, hostname, IP, and telnet or ssh."""
    rows: list[tuple[str, str, str, str]] = []
    for client in clients:
        player = client.player
        if player is not None:
            name = player.name
        elif client.pending_name:
            name = client.pending_name
        else:
            name = "<pending>"
        kind = "ssh" if client.ssh else "telnet"
        rows.append((name, client.hostname, client.address, kind))
    rows.sort(key=lambda row: (row[0], row[2]))
    return rows


def idlers(clients: Iterable[Client]) -> list[tuple[str, int]]:
    """Return each logged-in name and idle count, sorted."""
    rows: list[tuple[str, int]] = []
    for client in clients:
        user = client.player
        if user is None:
            continue
        rows.append((user.name, user.idle_seconds))
    rows.sort()
    return rows


def newbies(clients: Iterable[Client]) -> list[tuple[str, str, str, int]]:
    """Return each connected newbie's name, hostname, room, and idle seconds, sorted."""
    rows: list[tuple[str, str, str, int]] = []
    for client in clients:
        user = client.player
        if user is None or user.rank != RANK_NEWBIE or client.room is None:
            continue
        rows.append((user.name, client.hostname, client.room, user.idle_seconds))
    rows.sort()
    return rows


def player_named(clients: Iterable[Client], name: str) -> Client | None:
    """Return the playing client whose lowercase key is ``name``."""
    key = name.lower()
    for client in clients:
        player = client.player
        if player is None or client.room is None:
            continue
        if player.lowercase_name == key:
            return client
    return None


def multiple_matches_message(names: Iterable[str]) -> str:
    """Return the refusal that lists every partial name.

    Two names use ``&``. More names use commas, and ``&`` before the last.
    """
    ordered = sorted(names, key=str.casefold)
    if len(ordered) < 2:
        listed = ordered[0] if ordered else ""
    elif len(ordered) == 2:
        listed = f"{ordered[0]} & {ordered[1]}"
    else:
        listed = f"{', '.join(ordered[:-1])} & {ordered[-1]}"
    return f"multiple Matches: {listed}\n"


def match_name(query: str, names: Iterable[str]) -> tuple[str | None, str | None]:
    """Return one stored name, or the multiple-matches line.

    An exact case-insensitive name wins. Otherwise one prefix match wins.
    Several prefixes return ``(None, message)``. No match returns ``(None, None)``.
    """
    folded = query.casefold()
    stored = list(names)
    exact = [name for name in stored if name.casefold() == folded]
    if exact:
        return exact[0], None
    partial = [name for name in stored if name.casefold().startswith(folded)]
    if len(partial) == 1:
        return partial[0], None
    if len(partial) > 1:
        return None, multiple_matches_message(partial)
    return None, None


def match_online_player(clients: Iterable[Client], name: str) -> tuple[Client | None, str | None]:
    """Return one playing client, or the multiple-matches line.

    Exact names win. A single prefix match is used. Several prefixes are refused.
    """
    playing: list[Client] = []
    for client in clients:
        player = client.player
        if player is None or client.room is None:
            continue
        playing.append(client)
    chosen, error = match_name(name, (client.player.name for client in playing if client.player))
    if error is not None or chosen is None:
        return None, error
    key = chosen.casefold()
    for client in playing:
        player = client.player
        if player is not None and player.name.casefold() == key:
            return client, None
    return None, None


def announce_reconnect(name: str) -> None:
    """Print that ``name`` reconnected."""
    print(f"{name} reconnected.")
