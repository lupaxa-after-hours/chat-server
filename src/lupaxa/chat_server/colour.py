"""ANSI colours for text the server sends to a player."""

from __future__ import annotations

import re

from colorama import Back, Fore, Style
from colorama.ansi import BEL, clear_screen, code_to_chars

from .client import Client
from .time import IDLE_ACTIVE_SECONDS, IDLE_BLANK_SECONDS, IDLE_RED_SECONDS, idle_clock

SGR = re.compile(r"\x1b\[[0-9;]*m")
FLASH = code_to_chars(5)


def can_see_colour(client: Client) -> bool:
    """Return whether this connection's player has colour turned on."""
    return client.player is not None and client.player.colour


def paint(text: str, *codes: str) -> str:
    """Return ``text`` wrapped in ``codes`` and a reset."""
    return "".join(codes) + text + Style.RESET_ALL


def plain_len(text: str) -> int:
    """Return the length of ``text`` after colour codes are removed."""
    return len(SGR.sub("", text))


def paint_idle(seconds: int, colour: bool) -> str:
    """Return the idle cell. Active is green, then yellow, red, and light black from one hour."""
    text = idle_clock(seconds)
    if not colour:
        return text
    if seconds < IDLE_ACTIVE_SECONDS:
        return green(text)
    if seconds < IDLE_RED_SECONDS:
        return yellow(text)
    if seconds < IDLE_BLANK_SECONDS:
        return red(text)
    return light_black(text)


def paint_staff(client: Client, text: str) -> str:
    """Return a staff-channel line, cyan when the recipient has colour on."""
    if can_see_colour(client):
        return cyan(text.rstrip("\n")) + "\n"
    return text


def paint_admin(client: Client, text: str) -> str:
    """Return an admin-channel line, red when the recipient has colour on."""
    if can_see_colour(client):
        return red(text.rstrip("\n")) + "\n"
    return text


def paint_coder(client: Client, text: str) -> str:
    """Return a coder-channel line, magenta when the recipient has colour on."""
    if can_see_colour(client):
        return magenta(text.rstrip("\n")) + "\n"
    return text


def black(text: str) -> str:
    """Return ``text`` in black."""
    return paint(text, Fore.BLACK)


def red(text: str) -> str:
    """Return ``text`` in red."""
    return paint(text, Fore.RED)


def green(text: str) -> str:
    """Return ``text`` in green."""
    return paint(text, Fore.GREEN)


def yellow(text: str) -> str:
    """Return ``text`` in yellow."""
    return paint(text, Fore.YELLOW)


def blue(text: str) -> str:
    """Return ``text`` in blue."""
    return paint(text, Fore.BLUE)


def magenta(text: str) -> str:
    """Return ``text`` in magenta."""
    return paint(text, Fore.MAGENTA)


def cyan(text: str) -> str:
    """Return ``text`` in cyan."""
    return paint(text, Fore.CYAN)


def white(text: str) -> str:
    """Return ``text`` in white."""
    return paint(text, Fore.WHITE)


def light_black(text: str) -> str:
    """Return ``text`` in light black."""
    return paint(text, Fore.LIGHTBLACK_EX)


def light_red(text: str) -> str:
    """Return ``text`` in light red."""
    return paint(text, Fore.LIGHTRED_EX)


def light_green(text: str) -> str:
    """Return ``text`` in light green."""
    return paint(text, Fore.LIGHTGREEN_EX)


def light_yellow(text: str) -> str:
    """Return ``text`` in light yellow."""
    return paint(text, Fore.LIGHTYELLOW_EX)


def light_blue(text: str) -> str:
    """Return ``text`` in light blue."""
    return paint(text, Fore.LIGHTBLUE_EX)


def light_magenta(text: str) -> str:
    """Return ``text`` in light magenta."""
    return paint(text, Fore.LIGHTMAGENTA_EX)


def light_cyan(text: str) -> str:
    """Return ``text`` in light cyan."""
    return paint(text, Fore.LIGHTCYAN_EX)


def light_white(text: str) -> str:
    """Return ``text`` in light white."""
    return paint(text, Fore.LIGHTWHITE_EX)


def on_black(text: str) -> str:
    """Return ``text`` on a black background."""
    return paint(text, Back.BLACK)


def on_red(text: str) -> str:
    """Return ``text`` on a red background."""
    return paint(text, Back.RED)


def on_green(text: str) -> str:
    """Return ``text`` on a green background."""
    return paint(text, Back.GREEN)


def on_yellow(text: str) -> str:
    """Return ``text`` on a yellow background."""
    return paint(text, Back.YELLOW)


def on_blue(text: str) -> str:
    """Return ``text`` on a blue background."""
    return paint(text, Back.BLUE)


def on_magenta(text: str) -> str:
    """Return ``text`` on a magenta background."""
    return paint(text, Back.MAGENTA)


def on_cyan(text: str) -> str:
    """Return ``text`` on a cyan background."""
    return paint(text, Back.CYAN)


def on_white(text: str) -> str:
    """Return ``text`` on a white background."""
    return paint(text, Back.WHITE)


def on_light_black(text: str) -> str:
    """Return ``text`` on a light black background."""
    return paint(text, Back.LIGHTBLACK_EX)


def on_light_red(text: str) -> str:
    """Return ``text`` on a light red background."""
    return paint(text, Back.LIGHTRED_EX)


def on_light_green(text: str) -> str:
    """Return ``text`` on a light green background."""
    return paint(text, Back.LIGHTGREEN_EX)


def on_light_yellow(text: str) -> str:
    """Return ``text`` on a light yellow background."""
    return paint(text, Back.LIGHTYELLOW_EX)


def on_light_blue(text: str) -> str:
    """Return ``text`` on a light blue background."""
    return paint(text, Back.LIGHTBLUE_EX)


def on_light_magenta(text: str) -> str:
    """Return ``text`` on a light magenta background."""
    return paint(text, Back.LIGHTMAGENTA_EX)


def on_light_cyan(text: str) -> str:
    """Return ``text`` on a light cyan background."""
    return paint(text, Back.LIGHTCYAN_EX)


def on_light_white(text: str) -> str:
    """Return ``text`` on a light white background."""
    return paint(text, Back.LIGHTWHITE_EX)


def dim(text: str) -> str:
    """Return ``text`` dimmed."""
    return paint(text, Style.DIM)


def normal(text: str) -> str:
    """Return ``text`` at normal intensity."""
    return paint(text, Style.NORMAL)


def bright(text: str) -> str:
    """Return ``text`` in bright white."""
    return paint(text, Style.BRIGHT, Fore.WHITE)


def flash(text: str) -> str:
    """Return ``text`` flashing."""
    return paint(text, FLASH)


def bell(text: str) -> str:
    """Return ``text`` followed by the terminal bell."""
    return text + BEL


def cls() -> str:
    """Return the sequence that clears the screen and stays on this row."""
    return clear_screen() + "\r"


def testcard() -> str:
    """Return a swatch of every foreground and background colour."""
    foreground = (
        ("black", black),
        ("red", red),
        ("green", green),
        ("yellow", yellow),
        ("blue", blue),
        ("magenta", magenta),
        ("cyan", cyan),
        ("white", white),
        ("light black", light_black),
        ("light red", light_red),
        ("light green", light_green),
        ("light yellow", light_yellow),
        ("light blue", light_blue),
        ("light magenta", light_magenta),
        ("light cyan", light_cyan),
        ("light white", light_white),
    )
    background = (
        ("black", on_black),
        ("red", on_red),
        ("green", on_green),
        ("yellow", on_yellow),
        ("blue", on_blue),
        ("magenta", on_magenta),
        ("cyan", on_cyan),
        ("white", on_white),
        ("light black", on_light_black),
        ("light red", on_light_red),
        ("light green", on_light_green),
        ("light yellow", on_light_yellow),
        ("light blue", on_light_blue),
        ("light magenta", on_light_magenta),
        ("light cyan", on_light_cyan),
        ("light white", on_light_white),
    )
    rows = [f"  {'':<14}  {'Foreground':<10}  Background"]
    for (name, ink), (_, paper) in zip(foreground, background, strict=True):
        swatch = ink("sample")
        pad = " " * (10 - plain_len(swatch))
        rows.append(f"  {name:<14}  {swatch}{pad}  {paper(' sample ')}")
    longest = max(plain_len(row) for row in rows)
    inner = longest + 1
    border = "+" + ("-" * inner) + "+"
    body = ["|" + row + (" " * (inner - plain_len(row))) + "|" for row in rows]
    return "\n".join((border, *body, border)) + "\n"
