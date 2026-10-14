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


def center_block(text: str, width: int) -> str:
    """Pad every line so the widest line sits in the middle of ``width``."""
    if width <= 0 or text == "":
        return text
    newline = text.endswith("\n")
    lines = text[:-1].split("\n") if newline else text.split("\n")
    widest = max((plain_len(line) for line in lines), default=0)
    pad = max(0, (width - widest) // 2)
    if pad == 0:
        return text
    centered = "\n".join(" " * pad + line for line in lines)
    return centered + "\n" if newline else centered


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
