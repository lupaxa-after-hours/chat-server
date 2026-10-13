"""Shared constants. The command list does not live here."""

from __future__ import annotations

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
RESERVED_NAMES = frozenset({"me", "who", "quit"})
