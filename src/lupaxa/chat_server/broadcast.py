"""Send a line to a room, the staff, or everyone connected."""

from __future__ import annotations

from .client import Client
from .colour import can_see_colour, paint_admin, paint_coder, paint_staff, yellow
from .player import is_logged_in
from .ranks import Rank, rank_allows
from .rooms import is_in_room
from .server import Server
from .utilities import get_clients


def broadcast_room(server: Server, room_id: str | None, text: str, *, skip: Client | None) -> None:
    """Send ``text`` to every player in ``room_id`` except ``skip``."""
    if room_id is None or text == "":
        return
    for client in list(get_clients(server)):
        if not is_in_room(client, room_id):
            continue
        if skip is not None and client is skip:
            continue
        server.send(client, text)


def broadcast_room_split(server: Server, room_id: str | None, plain: str, staff: str, *, skip: Client | None) -> None:
    """Send ``staff`` to super users in the room and ``plain`` to everyone else."""
    if room_id is None:
        return
    for client in list(get_clients(server)):
        if not is_in_room(client, room_id):
            continue
        if skip is not None and client is skip:
            continue
        user = client.player
        if user is not None and rank_allows(user.rank, Rank.SUPER_USER):
            server.send(client, staff)
        else:
            server.send(client, plain)


def broadcast_shout(server: Server, line: str, *, skip: Client | None) -> None:
    """Send a shout line to every player in a room except ``skip``."""
    if line == "":
        return
    for client in list(get_clients(server)):
        if not is_logged_in(client):
            continue
        if skip is not None and client is skip:
            continue
        if can_see_colour(client):
            server.send(client, yellow(line) + "\n")
        else:
            server.send(client, line + "\n")


def broadcast_all(server: Server, text: str, *, skip: Client | None) -> None:
    """Send ``text`` to every player who has a name and a room, except ``skip``."""
    if text == "":
        return
    for client in list(get_clients(server)):
        if not is_logged_in(client):
            continue
        if skip is not None and client is skip:
            continue
        server.send(client, text)


def broadcast_staff(server: Server, text: str, *, skip: Client | None = None) -> None:
    """Send ``text`` to SU Channel and above except ``skip``."""
    if text == "":
        return
    for client in list(get_clients(server)):
        user = client.player
        if user is None or not rank_allows(user.rank, Rank.SU_CHANNEL) or user.off_duty:
            continue
        if skip is not None and client is skip:
            continue
        server.send(client, paint_staff(client, text))


def broadcast_admin(server: Server, text: str, *, skip: Client | None = None) -> None:
    """Send ``text`` to every admin except ``skip``."""
    if text == "":
        return
    for client in list(get_clients(server)):
        user = client.player
        if user is None or not rank_allows(user.rank, Rank.ADMIN) or user.off_duty:
            continue
        if skip is not None and client is skip:
            continue
        server.send(client, paint_admin(client, text))


def broadcast_coder(server: Server, text: str, *, skip: Client | None = None) -> None:
    """Send ``text`` to Coder and above except ``skip``. Off duty still hears it."""
    if text == "":
        return
    for client in list(get_clients(server)):
        user = client.player
        if user is None or not rank_allows(user.rank, Rank.CODER):
            continue
        if skip is not None and client is skip:
            continue
        server.send(client, paint_coder(client, text))
