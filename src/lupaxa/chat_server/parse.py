"""Name checks, direction words, and turning a typed line into a command."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .client import Client
from .commands_list import Command, Host, aliases, by_name
from .globals import DIRECTIONS, NAME_PROMPT, RESERVED_NAMES
from .saved_player import rank_allows
from .utilities import _rank_of


def parse_direction(text: str) -> str | None:
    """Return the short exit code for ``text``, or None."""
    return DIRECTIONS.get(" ".join(text.split()))


@dataclass(frozen=True)
class NameDecision:
    """Result of offering one line as a name."""

    name: str | None
    reply: str


def consider_name(line: str, taken: set[str]) -> NameDecision:
    """Accept ``line`` as a name, or explain why it was refused.

    A blank line returns an empty reply. The caller must not send that reply.
    ``me``, ``who``, and ``quit`` in any case are reserved and are refused
    before a player file is opened.
    """
    stripped = line.strip()
    if not stripped:
        return NameDecision(name=None, reply="")
    if stripped.lower() in RESERVED_NAMES:
        reply = f"{stripped} cannot be used. Pick another name.\n\n{NAME_PROMPT}"
        return NameDecision(name=None, reply=reply)
    if not stripped.isascii() or not stripped.isalnum():
        reply = f"A name can use only letters and digits.\n\n{NAME_PROMPT}"
        return NameDecision(name=None, reply=reply)
    if stripped[0].isdigit():
        reply = f"A name cannot start with a number.\n\n{NAME_PROMPT}"
        return NameDecision(name=None, reply=reply)
    if len(stripped) < 3:
        reply = "A name must be at least 3 characters.\n\nPlease enter a longer name: "
        return NameDecision(name=None, reply=reply)
    if len(stripped) > 20:
        reply = "A name can be at most 20 characters.\n\nPlease enter a shorter name: "
        return NameDecision(name=None, reply=reply)
    if stripped in taken:
        reply = f"{stripped} is already in use. Pick another name.\n\n{NAME_PROMPT}"
        return NameDecision(name=None, reply=reply)
    return NameDecision(name=stripped, reply="")


def match_command(
    line: str,
    known: Mapping[str, Command],
    alias_list: Sequence[Command],
) -> tuple[Command | None, str | None]:
    """Return the command and its text.

    An alias matches with no space after it, unless that alias sets
    ``needs_space``. ``x Bea`` is examine. ``xBea`` is not.
    """
    for command in alias_list:
        if command.needs_space:
            continue
        if line.startswith(command.name):
            rest = line[len(command.name) :].lstrip()
            return command, rest or None
    parts = line.split(None, 1)
    found = known.get(parts[0])
    if found is None or (found.is_alias and not found.needs_space):
        return None, None
    text = parts[1] if len(parts) > 1 else None
    return found, text


def process_commands(host: Host, client: Client, line: str) -> None:
    """Match ``line`` to one command and call that command's function.

    Typing clears idle. Examine reports the idle from before that clear.
    """
    if line.strip() == "":
        return
    command, text = match_command(line, by_name(), aliases())
    shows_idle = command is not None and _function_name(command) == "command_examine"
    if client.player is not None and not shows_idle:
        client.player.idle_seconds = 0
    try:
        if command is None:
            word = line.split(None, 1)[0]
            host.send(client, f"No such command '{word}'\n")
            return
        if not rank_allows(_rank_of(client), command.rank):
            host.send(client, f"No such command '{command.name}'\n")
            return
        bare_ok = _function_name(command) in {
            "command_auto_reply",
            "command_colour",
            "command_description",
            "command_retire",
            "command_save",
            "command_status",
            "command_term_width",
            "command_time",
        }
        if command.syntax is not None and not text and not bare_ok:
            host.send(client, f"Syntax: {command.syntax}\n")
            return
        command.function(host, client, command, text)
    finally:
        if shows_idle and client.player is not None:
            client.player.idle_seconds = 0


def _function_name(command: Command) -> str:
    """Return the command function's name. Aliases share that function."""
    return getattr(command.function, "__name__", "")
