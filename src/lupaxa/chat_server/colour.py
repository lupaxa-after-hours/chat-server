"""ANSI colours for text the server sends to a player."""

from __future__ import annotations

import re

from colorama import Fore, Style

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def paint(text: str, *codes: str) -> str:
    """Return ``text`` wrapped in ``codes`` and a reset."""
    return "".join(codes) + text + Style.RESET_ALL


def plain_len(text: str) -> int:
    """Return the length of ``text`` after colour codes are removed."""
    return len(_SGR.sub("", text))


def cyan(text: str) -> str:
    """Return ``text`` in cyan."""
    return paint(text, Fore.CYAN)


def red(text: str) -> str:
    """Return ``text`` in red."""
    return paint(text, Fore.RED)


def green(text: str) -> str:
    """Return ``text`` in green."""
    return paint(text, Fore.GREEN)


def bright(text: str) -> str:
    """Return ``text`` in bright white."""
    return paint(text, Style.BRIGHT, Fore.WHITE)


def yellow(text: str) -> str:
    """Return ``text`` in yellow."""
    return paint(text, Fore.YELLOW)
