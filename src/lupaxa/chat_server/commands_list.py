"""The command type and the full list of commands."""

from __future__ import annotations

import importlib
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .client import Client
from .colour import cyan, plain_len, red
from .config import Config
from .help import HelpBook, HelpStamp
from .rooms import Layout
from .saved_player import RANK_ADMIN, RANK_NEWBIE, RANK_SUPER, rank_allows

if TYPE_CHECKING:
    from .net import NetStats


class Host(Protocol):
    """What command, login, and timer code needs from the running server."""

    started_at: datetime
    _config: Config
    _layout: Layout
    _help: HelpBook
    _help_stamp: HelpStamp
    _help_failed: HelpStamp | None
    _help_check_seconds: int
    _roster_dir: Path
    _shutdown_seconds: int | None
    _shutdown_reason: str
    _allow_newbies: bool
    _allow_residents: bool
    _mfa_enabled: bool
    _mfa_steps: dict[str, int]
    _timer_mark: float | None
    _timer_remainder: float
    _net_count: int
    _telnet_net: NetStats
    _ssh_net: NetStats
    _combined_net: NetStats

    @property
    def _clients(self) -> Mapping[socket.socket, Client]:
        """Return the connected clients, keyed by socket."""
        raise NotImplementedError

    def send(self, client: Client, text: str) -> bool:
        """Queue ``text`` for ``client``. Return False when the socket is gone."""
        raise NotImplementedError

    def drop(self, client: Client, *, announce: bool) -> None:
        """Close ``client`` and tell the room when ``announce`` is true."""
        raise NotImplementedError

    def shutdown(self) -> None:
        """Ask the accept loop to stop."""
        raise NotImplementedError

    def is_stopping(self) -> bool:
        """Return whether ``shutdown`` has been asked for."""
        raise NotImplementedError

    def _replace_socket(self, live: Client, newcomer: Client) -> None:
        """Attach ``newcomer``'s socket to the player already in ``live``."""
        raise NotImplementedError

    def _unsaved_names(self, skip: Client | None = None) -> set[str]:
        """Return lowercase names held by unsaved connections, except ``skip``."""
        raise NotImplementedError

    def _taken_names(self) -> set[str]:
        """Return the names of players who are already in a room."""
        raise NotImplementedError


@dataclass(frozen=True)
class Command:
    """One command or alias the player can type."""

    name: str
    function: Callable[[Host, Client, Command, str | None], None]
    is_alias: bool
    syntax: str | None = None
    needs_space: bool = False
    rank: str = RANK_NEWBIE


COMMANDS: list[Command] = []
_by_name: dict[str, Command] = {}
_aliases: tuple[Command, ...] = ()
_LOADED = [False]


def ensure_commands() -> None:
    """Import every command module once so ``COMMANDS`` is complete."""
    global _aliases, _by_name
    if _LOADED[0]:
        return
    _LOADED[0] = True
    for name in ("commands", "password", "admin", "su"):
        importlib.import_module(f"{__package__}.{name}")
    _by_name = {command.name: command for command in COMMANDS}
    _aliases = tuple(
        sorted(
            (command for command in COMMANDS if command.is_alias),
            key=lambda command: len(command.name),
            reverse=True,
        )
    )


def by_name() -> dict[str, Command]:
    """Return every command and alias, keyed by the word the player types."""
    ensure_commands()
    return _by_name


def aliases() -> tuple[Command, ...]:
    """Return aliases, longest name first, so ``!`` does not swallow a longer mark."""
    ensure_commands()
    return _aliases


def _shown_name(command: Command, colour: bool) -> str:
    """Return the command word, coloured when ``colour`` is true."""
    if not colour:
        return command.name
    if command.rank == RANK_SUPER:
        return cyan(command.name)
    if command.rank == RANK_ADMIN:
        return red(command.name)
    return command.name


def command_words(rank: str, colour: bool = False, *, mfa_enabled: bool = False) -> str:
    """Return punctuation aliases first, then letter names this rank may use."""
    ensure_commands()
    visible = [
        command
        for command in COMMANDS
        if rank_allows(rank, command.rank) and (mfa_enabled or command.name != "mfa")
    ]
    marks = sorted(
        (command for command in visible if command.is_alias and not command.name.isalpha()),
        key=lambda command: command.name,
    )
    names = sorted(
        (command for command in visible if not command.is_alias or command.name.isalpha()),
        key=lambda command: command.name,
    )
    mark_words = [_shown_name(command, colour) for command in marks]
    name_words = [_shown_name(command, colour) for command in names]
    return " ".join([*mark_words, *name_words])


def wrap_words(words: Sequence[str], width: int) -> str:
    """Join ``words`` into lines no wider than ``width`` visible characters."""
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = word if not current else " ".join((*current, word))
        if current and plain_len(trial) > width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)
