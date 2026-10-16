"""The command catalogue."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, replace

from . import globals as catalogue
from .client import Client
from .colour import colour_of, cyan, magenta, red
from .ranks import Rank, rank_allows
from .server import Server


@dataclass(frozen=True)
class Command:
    """One command or alias the player can type."""

    name: str
    function: Callable[[Server, Client, Command, str | None], None]
    is_alias: bool
    syntax: str | None = None
    needs_space: bool = False
    rank: Rank = Rank.NEWBIE
    subcommands: tuple[Command, ...] | None = None
    parent: str = ""
    role: int = 0
    combination: str = "or"


class CommandError(Exception):
    """A parent command's children cannot be registered."""


def ensure_commands() -> None:
    """Import every command module once so ``COMMANDS`` is complete."""
    if catalogue.LOADED[0]:
        return
    catalogue.LOADED[0] = True
    for name in ("newbie", "resident", "password", "admin", "su", "coder"):
        importlib.import_module(f"{__package__}.{name}")
    attach_subcommands(catalogue.COMMANDS, registered=True)
    catalogue.commands_by_name = {command.name: command for command in catalogue.COMMANDS}
    catalogue.command_aliases = tuple(sorted((command for command in catalogue.COMMANDS if command.is_alias), key=lambda command: len(command.name), reverse=True))  # fmt: skip  # noqa: E501


def by_name() -> dict[str, Command]:
    """Return every command and alias, keyed by the word the player types."""
    ensure_commands()
    return catalogue.commands_by_name


def aliases() -> tuple[Command, ...]:
    """Return aliases, longest name first, so ``!`` does not swallow a longer mark."""
    ensure_commands()
    return catalogue.command_aliases


def shown_name(command: Command, colour: bool) -> str:
    """Return the command word, coloured when ``colour`` is true."""
    if not colour:
        return command.name
    if command.rank in (Rank.SU_CHANNEL, Rank.SUPER_USER):
        return cyan(command.name)
    if command.rank is Rank.ADMIN:
        return red(command.name)
    if command.rank is Rank.CODER:
        return magenta(command.name)
    return command.name


def shown_syntax(command: Command) -> str:
    """Return the syntax line for the command the player matched."""
    name = f"{command.parent} {command.name}" if command.parent else command.name
    tail = command.syntax
    if tail is None or tail == "":
        return name
    if not command.parent and command.is_alias and not command.needs_space:
        return f"{name}{tail}"
    return f"{name} {tail}"


def command_allowed(rank: Rank, roles: int, command: Command) -> bool:
    """Return whether this rank and these role bits may use ``command``."""
    if command.role == 0:
        return rank_allows(rank, command.rank)
    holds = (roles & int(command.role)) != 0
    ranked = rank_allows(rank, command.rank)
    if command.combination == "or":
        return ranked or holds
    return ranked and holds


def held_roles(client: Client) -> int:
    """Return the live player's roles, or 0 when nobody is logged in."""
    user = client.player
    if user is None:
        return 0
    return user.roles


def subcommand_words(parent: Command, rank: Rank, colour: bool = False, *, roles: int = 0) -> str:
    """Return this parent's child names, coloured when colour is true."""
    children = parent.subcommands or ()
    visible = [command for command in children if command_allowed(rank, roles, command)]
    marks = sorted((command for command in visible if command.is_alias and not command.name.isalpha()), key=lambda command: command.name)
    names = sorted((command for command in visible if not command.is_alias or command.name.isalpha()), key=lambda command: command.name)
    mark_words = [shown_name(command, colour) for command in marks]
    name_words = [shown_name(command, colour) for command in names]
    return " ".join([*mark_words, *name_words])


def command_subcommands(server: Server, client: Client, command: Command, text: str | None) -> None:
    """List this parent's children for the player's rank."""
    from .utilities import frame_width, has_rank, wrap_words

    parent = by_name()[command.parent]
    words = subcommand_words(parent, has_rank(client), colour=colour_of(client), roles=held_roles(client)).split()
    wrapped = wrap_words(words, frame_width(client.discovered_columns, client.frame_columns))
    server.send(client, f"Subcommands available:\n{wrapped}\n")


def register_subcommands(parent: str, *children: Command) -> None:
    """Queue children for a parent. ensure_commands attaches them."""
    if not children:
        raise CommandError("register_subcommands needs a subcommand.")
    key = parent.casefold()
    entry = catalogue.registered_subcommands.get(key)
    if entry is None:
        catalogue.registered_subcommands[key] = (parent, list(children))
        return
    entry[1].extend(children)


def attach_subcommands(commands: list[Command], *, registered: bool = False) -> None:
    """Give every parent a commands child, or raise CommandError."""
    queued = catalogue.registered_subcommands if registered else {}
    by_fold = {command.name.casefold(): command for command in commands}
    for key, entry in queued.items():
        spelling = entry[0]
        found = by_fold.get(key)
        if found is None:
            raise CommandError(f"No such command '{spelling}'.")
        if found.subcommands is None:
            raise CommandError(f"Command {found.name} is not a parent.")
    prepared: list[tuple[int, Command]] = []
    for index, command in enumerate(commands):
        if command.subcommands is None:
            continue
        extra = queued.get(command.name.casefold())
        children = (*command.subcommands, *extra[1]) if extra is not None else command.subcommands
        merged = replace(command, subcommands=children)
        check_children(merged)
        stamped = tuple(replace(child, parent=command.name) for child in children)
        lister = Command("commands", command_subcommands, False, None, rank=command.rank, parent=command.name)
        prepared.append((index, replace(command, subcommands=(*stamped, lister))))
    for index, command in prepared:
        commands[index] = command
    if registered:
        catalogue.registered_subcommands.clear()


def check_children(command: Command) -> None:
    """Raise CommandError for the first child that cannot be registered."""
    seen: set[str] = set()
    for child in command.subcommands or ():
        if child.subcommands is not None:
            raise CommandError(f"Subcommand {command.name} {child.name} cannot have subcommands.")
        if child.name.casefold() == "commands":
            raise CommandError(f"Subcommand {command.name} commands is reserved.")
        folded = child.name.casefold()
        if folded in seen:
            raise CommandError(f"Subcommand {command.name} {child.name} is already registered.")
        seen.add(folded)
        if not rank_allows(child.rank, command.rank):
            raise CommandError(f"Subcommand {command.name} {child.name} is below {command.name}'s rank.")


def listed_commands(rank: Rank, *, exact: Rank | None = None, mfa_enabled: bool = False, country_lookup: bool = False, roles: int = 0, role_filter: int = 0) -> list[Command]:
    """Return the commands this list may show, punctuation aliases first."""
    ensure_commands()
    visible: list[Command] = []
    for command in catalogue.COMMANDS:
        stored = (command.role & role_filter) != 0 if role_filter else command.rank == exact if exact is not None else command_allowed(rank, roles, command)
        if not stored:
            continue
        if command.name == "mfa" and not mfa_enabled:
            continue
        if command.name == "ipinfo" and not country_lookup:
            continue
        visible.append(command)
    marks = sorted((command for command in visible if command.is_alias and not command.name.isalpha()), key=lambda command: command.name)
    names = sorted((command for command in visible if not command.is_alias or command.name.isalpha()), key=lambda command: command.name)
    return [*marks, *names]


def command_words(rank: Rank, colour: bool = False, *, mfa_enabled: bool = False, country_lookup: bool = False, roles: int = 0) -> str:
    """Return punctuation aliases first, then letter names this rank may use."""
    return " ".join(shown_name(command, colour) for command in listed_commands(rank, mfa_enabled=mfa_enabled, country_lookup=country_lookup, roles=roles))
