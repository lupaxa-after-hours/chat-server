"""A logged-in player. Further fields can be added here later."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeVar

from .defaults import DEFAULT_ENTERMSG, DEFAULT_LOGINMSG, DEFAULT_LOGOUTMSG, DEFAULT_PROMPT
from .ranks import Rank


class ConnectedClient(Protocol):
    """A connection that has a name, a room, and maybe a player."""

    @property
    def name(self) -> str | None:
        """Return the logged-in name."""
        raise NotImplementedError

    @property
    def room(self) -> str | None:
        """Return the player's room."""
        raise NotImplementedError

    @property
    def player(self) -> Player | None:
        """Return the logged-in player."""
        raise NotImplementedError


ConnectedT = TypeVar("ConnectedT", bound=ConnectedClient)


@dataclass
class Player:
    """Name, room, connect time, and the counters the timer loop updates."""

    name: str
    room: str
    connected_at: datetime
    alarm_seconds: int | None = None
    idle_seconds: int = 0
    prompt: str = DEFAULT_PROMPT
    rank: Rank = Rank.NEWBIE
    connected_seconds: int = 0
    flushed_seconds: int = 0
    lowercase_name: str = ""
    colour: bool = True
    description: str = ""
    status: str = ""
    auto_reply: str = ""
    entermsg: str = DEFAULT_ENTERMSG
    loginmsg: str = DEFAULT_LOGINMSG
    logoutmsg: str = DEFAULT_LOGOUTMSG
    plan: str = ""
    country_name: str = ""
    country_code: str = ""
    off_duty: bool = False
    off_list: bool = False
    screen_locked: bool = False
    screen_lock_reason: str = ""
    roles: int = 0

    def __post_init__(self) -> None:
        """Fill the key from the display spelling when the caller did not pass one."""
        if self.lowercase_name == "":
            self.lowercase_name = self.name.lower()


def is_logged_in(client: ConnectedClient) -> bool:
    """Return whether this connection has a name and a room."""
    return client.name is not None and client.room is not None


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


def match_online_player(clients: Iterable[ConnectedT], name: str) -> tuple[ConnectedT | None, str | None]:
    """Return one playing client, or the multiple-matches line.

    Exact names win. A single prefix match is used. Several prefixes are refused.
    """
    playing: list[ConnectedT] = []
    for client in clients:
        if not is_logged_in(client):
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
