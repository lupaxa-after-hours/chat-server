"""The protocol command, login, and timer code uses for the running server."""

from __future__ import annotations

import socket
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .chain import Chain
from .client import Client
from .config import Config
from .help import HelpBook, HelpStamp
from .rooms import Layout

if TYPE_CHECKING:
    from .netstats import NetStats


class Server(Protocol):
    """What command, login, and timer code needs from the running server."""

    started_at: datetime
    rebooted_at: datetime | None
    process: Any
    config: Config
    layout: Layout
    help: HelpBook
    help_stamp: HelpStamp
    help_failed: HelpStamp | None
    hot_stamp: HelpStamp
    hot_failed: HelpStamp | None
    country_db: object | None
    country_lock: threading.Lock
    rooms_stamp: HelpStamp
    rooms_failed: HelpStamp | None
    house_stamps: dict[str, HelpStamp]
    house_failed: dict[str, HelpStamp]
    banner: str
    message_stamps: dict[str, HelpStamp]
    message_failed: dict[str, HelpStamp]
    help_check_seconds: int
    roster_dir: Path
    shutdown_seconds: int | None
    shutdown_reason: str
    allow_newbies: bool
    allow_residents: bool
    full_message: str
    newbie_message: str
    mfa_enabled: bool
    country_lookup: bool
    mfa_steps: dict[str, int]
    timer_mark: float | None
    timer_remainder: float
    net_count: int
    telnet_net: NetStats
    ssh_net: NetStats
    combined_net: NetStats
    chains: list[Chain]

    @property
    def clients(self) -> Mapping[socket.socket, Client]:
        """Return the connected clients, keyed by socket."""
        raise NotImplementedError

    def send(self, client: Client, text: str) -> bool:
        """Queue ``text`` for ``client``. Return False when the socket is gone."""
        raise NotImplementedError

    def flush(self, client: Client) -> bool:
        """Write queued bytes for ``client``. Return False when the socket is gone."""
        raise NotImplementedError

    def drop(self, client: Client, *, announce: bool, skip_chain_departure: bool = False) -> None:
        """Close ``client`` and tell the room when ``announce`` is true."""
        del skip_chain_departure
        raise NotImplementedError

    def shutdown(self) -> None:
        """Ask the accept loop to stop."""
        raise NotImplementedError

    def is_stopping(self) -> bool:
        """Return whether ``shutdown`` has been asked for."""
        raise NotImplementedError

    def replace_socket(self, live: Client, newcomer: Client) -> None:
        """Attach ``newcomer``'s socket to the player already in ``live``."""
        raise NotImplementedError

    def unsaved_names(self, skip: Client | None = None) -> set[str]:
        """Return lowercase names held by unsaved connections, except ``skip``."""
        raise NotImplementedError

    def taken_names(self) -> set[str]:
        """Return the names of players who are already in a room."""
        raise NotImplementedError
