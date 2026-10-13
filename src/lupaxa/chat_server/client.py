"""A connected client and the bytes still waiting on its socket."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

from .player import Player
from .saved_player import SavedPlayer


@dataclass
class Client:
    """One accepted connection and its unread and unsent bytes."""

    sock: socket.socket
    buffer: bytearray = field(default_factory=bytearray)
    outgoing: bytearray = field(default_factory=bytearray)
    player: Player | None = None
    auth: str = "name"
    pending_name: str = ""
    pending_saved: SavedPlayer | None = None
    pending_password: str = ""
    pending_issuer: str = ""
    password_failures: int = 0
    address: str = "unknown"
    hostname: str = ""
    accepted_at: float = 0.0
    ssh: bool = False
    terminal: bool = False
    hide_input: bool = False
    iac: bytearray = field(default_factory=bytearray)
    saw_cr: bool = False
    discovered_columns: int | None = None
    discovered_rows: int | None = None
    frame_columns: int | None = None

    def __post_init__(self) -> None:
        """Use the IP as the hostname until a lookup replaces it."""
        if self.hostname == "":
            self.hostname = self.address

    @property
    def name(self) -> str | None:
        """Return the logged-in name, or None before this connection has one."""
        if self.player is None:
            return None
        return self.player.name

    @property
    def room(self) -> str | None:
        """Return the player's room, or None before they are logged in."""
        if self.player is None:
            return None
        return self.player.room

    @room.setter
    def room(self, room_id: str | None) -> None:
        """Move the logged-in player to ``room_id``."""
        if self.player is None or room_id is None:
            return
        self.player.room = room_id
