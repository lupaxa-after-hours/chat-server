"""Shared constants and the command list."""

from __future__ import annotations

from typing import Any

DIRECTIONS = {
    "n": "n",
    "north": "n",
    "s": "s",
    "south": "s",
    "e": "e",
    "east": "e",
    "w": "w",
    "west": "w",
    "ne": "ne",
    "north-east": "ne",
    "north east": "ne",
    "nw": "nw",
    "north-west": "nw",
    "north west": "nw",
    "se": "se",
    "south-east": "se",
    "south east": "se",
    "sw": "sw",
    "south-west": "sw",
    "south west": "sw",
    "up": "up",
    "down": "down",
}

NAME_PROMPT = "Please enter your name: "
MAX_PASSWORD_FAILURES = 3
CANNOT_NOW = "That name cannot be used right now.\n"
LOCALHOST = frozenset({"127.0.0.1", "::1"})
# Refused as player names. telnet and ssh share network's player argument.
# system is the office rooms file. Other command parameters, such as help,
# rooms, on, off, login, and force, stay available as names.
RESERVED_NAMES = frozenset({"me", "who", "quit", "ssh", "telnet", "system"})

COMMANDS: list[Any] = []
commands_by_name: dict[str, Any] = {}
command_aliases: tuple[Any, ...] = ()
LOADED = [False]
# Casefolded parent name to (first spelling, children in call order).
registered_subcommands: dict[str, tuple[str, list[Any]]] = {}
