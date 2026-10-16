"""Name checks, direction words, and turning a typed line into a command."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .client import Client
from .commands import Command, aliases, by_name, command_allowed, held_roles, shown_syntax
from .globals import DIRECTIONS, NAME_PROMPT, RESERVED_NAMES
from .ranks import Rank, rank_allows
from .server import Server
from .utilities import has_rank


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
    ``me``, ``who``, ``quit``, ``ssh``, ``telnet``, and ``system`` in any case are reserved and
    are refused before a player file is opened. Command parameters such as
    ``help``, ``rooms``, ``on``, and ``off`` are not reserved.
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


BARE_OK = frozenset(
    {
        "command_appoint",
        "command_auto_reply",
        "command_chains",
        "command_colour",
        "command_description",
        "command_dismiss",
        "command_home",
        "command_login",
        "command_plan",
        "command_entermsg",
        "command_loginmsg",
        "command_logoutmsg",
        "command_network",
        "command_reboot",
        "command_retire",
        "command_roles",
        "command_save",
        "command_screenlock",
        "command_status",
        "command_term_width",
        "command_time",
    }
)


def run_subcommand(server: Server, client: Client, parent: Command, text: str) -> None:
    """Match text as one child of parent and run that child."""
    children = parent.subcommands or ()
    parts = text.split(None, 1)
    if not parts:
        server.send(client, f"No such subcommand '{text}'.\n")
        return
    folded = parts[0].casefold()
    child = next((item for item in children if item.name.casefold() == folded), None)
    rest = parts[1] if len(parts) > 1 else None
    if child is None:
        server.send(client, f"No such subcommand '{parts[0]}'.\n")
        return
    if not command_allowed(has_rank(client), held_roles(client), child):
        server.send(client, f"No such subcommand '{child.name}'.\n")
        return
    user = client.player
    if user is not None and user.off_duty and child.name != "on_duty" and child.rank is not Rank.CODER and not rank_allows(Rank.RESIDENT, child.rank):
        server.send(client, "You are off duty.\n")
        return
    if child.syntax is not None and not rest and function_name(child) not in BARE_OK:
        server.send(client, f"Syntax: {shown_syntax(child)}\n")
        return
    child.function(server, client, child, rest)


def match_command(line: str, known: Mapping[str, Command], alias_list: Sequence[Command]) -> tuple[Command | None, str | None]:
    """Return the command and its text.

    An alias matches with no space after it, unless that alias sets
    ``needs_space``. ``x Bea`` is examine. ``xBea`` is not. The word matches
    in any case, the same way a player name does.
    """
    for command in alias_list:
        if command.needs_space:
            continue
        name = command.name
        if line[: len(name)].casefold() == name.casefold():
            rest = line[len(name) :].lstrip()
            return command, rest or None
    parts = line.split(None, 1)
    folded = parts[0].casefold()
    found = next((command for word, command in known.items() if word.casefold() == folded), None)
    if found is None or (found.is_alias and not found.needs_space):
        return None, None
    text = parts[1] if len(parts) > 1 else None
    return found, text


def process_commands(server: Server, client: Client, line: str) -> None:
    """Match ``line`` to one command and call that command's function.

    Typing clears idle before the command runs.
    """
    if line.strip() == "":
        return
    command, text = match_command(line, by_name(), aliases())
    if client.player is not None:
        client.player.idle_seconds = 0
    if command is not None and command.name == "mfa" and not server.mfa_enabled:
        command = None
    if command is not None and command.name == "ipinfo" and not server.country_lookup:
        command = None
    if command is None:
        word = line.split(None, 1)[0]
        server.send(client, f"No such command '{word}'\n")
        return
    if not command_allowed(has_rank(client), held_roles(client), command):
        server.send(client, f"No such command '{command.name}'\n")
        return
    user = client.player
    if user is not None and user.off_duty and command.name != "on_duty" and command.rank is not Rank.CODER and not rank_allows(Rank.RESIDENT, command.rank):
        server.send(client, "You are off duty.\n")
        return
    if command.syntax is not None and not text and function_name(command) not in BARE_OK:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    if command.subcommands is not None:
        run_subcommand(server, client, command, text if text is not None else "")
        return
    command.function(server, client, command, text)


def function_name(command: Command) -> str:
    """Return the command function's name. Aliases share that function."""
    return getattr(command.function, "__name__", "")
