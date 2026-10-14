"""Shared text, frames, addresses, and roster lookups."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Sequence

from .client import Client
from .colour import cyan, green, plain_len, red
from .commands_list import Host
from .exceptions import ConfigError
from .globals import CANNOT_NOW
from .saved_player import (
    RANK_ADMIN,
    RANK_NEWBIE,
    RANK_SUPER,
    RANK_SUPER_ADMIN,
    SavedPlayer,
    load_saved,
    normalize_super_admin,
    player_path,
    rank_label,
    savable_name,
    save_player,
)
from .time import duration_phrase

_DEFAULT_FRAME = 80
_MIN_IDLE = 8
_GAP = 2
_CENTER_HEADERS = frozenset({"Rank", "Room", "Idle"})
_CENTER_VALUES = frozenset({"Rank"})


def display_address(host: str) -> str:
    """Return ``host`` for display, unwrapping IPv4-mapped IPv6 when applicable."""
    if host == "":
        return "unknown"
    if host.lower().startswith("::ffff:"):
        remainder = host[len("::ffff:") :]
        try:
            return str(ipaddress.IPv4Address(remainder))
        except ValueError:
            return host
    return host


def lookup_hostname(ip: str) -> str:
    """Return the reverse name for ``ip``, or ``ip`` when none is found."""
    if ip in ("", "unknown"):
        return ip
    try:
        name = socket.gethostbyaddr(ip)[0].strip().rstrip(".")
    except OSError:
        return ip
    if name == "":
        return ip
    return name


def time_frame(current: str, uptime: str, width: int) -> str:
    """Return the ruled clock. There is no title."""
    label_width = len("Current time")
    fields = (("Current time", current), ("Uptime", uptime))
    body = [f"  {label.ljust(label_width)} : {value}" for label, value in fields]
    width = max(width, *(len(line) for line in body))
    rule = "-" * width
    lines = [rule, *(line.ljust(width) for line in body), rule]
    return "\n".join(lines) + "\n"


def frame_width(discovered: int | None, manual: int | None) -> int:
    """Return the manual width, else the discovered width, else 80."""
    if manual is not None and manual > 0:
        return manual
    if discovered is not None and discovered > 0:
        return discovered
    return _DEFAULT_FRAME


def terminal_size_value(
    discovered_columns: int | None,
    discovered_rows: int | None,
    manual: int | None,
) -> str:
    """Return the examine value for this client's terminal."""
    width = frame_width(discovered_columns, manual)
    if (
        discovered_columns is not None
        and discovered_rows is not None
        and discovered_columns > 0
        and discovered_rows > 0
    ):
        return f"{width}x{discovered_rows} ({discovered_columns}x{discovered_rows} discovered)"
    return f"{width}  (Pending)"


def terminal_size_line(
    discovered_columns: int | None,
    discovered_rows: int | None,
    manual: int | None,
) -> str:
    """Return the one examine field line for the terminal."""
    label = "Terminal Size"
    label_width = max(len("Logged in from"), len(label))
    value = terminal_size_value(discovered_columns, discovered_rows, manual)
    return f"  {label.ljust(label_width)} : {value}\n"


def _width_ceiling(client: Client) -> int:
    """Return the widest manual width this client may set."""
    if client.discovered_columns is not None and client.discovered_columns > 0:
        return client.discovered_columns
    return _DEFAULT_FRAME


def rank_words(rank: str) -> str:
    """Return the examine rank words, with no brackets."""
    if rank == RANK_NEWBIE:
        return "Newbie"
    return rank_label(rank)


def examine_idle(seconds: int) -> str:
    """Return the examine idle words. Ten seconds or less is not idle."""
    if seconds <= 10:
        return "Not idle at all"
    return duration_phrase(seconds)


def shown_rank(rank: str, enabled: bool) -> str:
    """Return the examine rank words, coloured when ``enabled`` is true."""
    words = rank_words(rank)
    if not enabled:
        return words
    if rank == RANK_NEWBIE:
        return green(words)
    if rank == RANK_SUPER:
        return cyan(words)
    if rank in (RANK_ADMIN, RANK_SUPER_ADMIN):
        return red(words)
    return words


def shown_rank_cell(rank: str, enabled: bool) -> str:
    """Return the list rank cell, coloured when ``enabled`` is true."""
    cell = rank_cell(rank)
    if not enabled:
        return cell
    if rank == RANK_SUPER:
        return cyan(cell)
    if rank in (RANK_ADMIN, RANK_SUPER_ADMIN):
        return red(cell)
    return cell


def _visible_width(terminal: int, content: int) -> int:
    """Return the wider of the terminal and the plain content."""
    return max(content, terminal)


def examine_frame(
    name: str,
    fields: Sequence[tuple[str, str]],
    width: int,
    description: str = "",
) -> str:
    """Return the ruled examine profile. The name is centered."""
    label_width = max(len("Logged in from"), *(len(label) for label, _value in fields))
    body = [f"  {label.ljust(label_width)} : {value}" for label, value in fields]
    desc_line = f"  {description}" if description else ""
    content = max(len(name), *(plain_len(line) for line in body))
    if desc_line:
        content = max(content, plain_len(desc_line))
    width = _visible_width(width, content)
    rule = "-" * width
    lines = [rule, name.center(width), rule]
    if desc_line:
        gap = width - plain_len(desc_line)
        lines.append(desc_line + (" " * gap if gap > 0 else ""))
        lines.append(rule)
    for line in body:
        gap = width - plain_len(line)
        lines.append(line + (" " * gap if gap > 0 else ""))
    lines.append(rule)
    return "\n".join(lines) + "\n"


def rank_cell(rank: str) -> str:
    """Return the list rank cell. Admin uses square brackets. Super user uses angles."""
    label = rank_words(rank)
    if rank in (RANK_ADMIN, RANK_SUPER_ADMIN):
        return f"[ {label} ]"
    if rank == RANK_SUPER:
        return f"< {label} >"
    return label


def _fit_cell(text: str, width: int, center_it: bool) -> str:
    """Return ``text`` in a column of ``width``. Odd leftover space goes on the right."""
    if not center_it:
        gap = width - plain_len(text)
        return text + (" " * gap if gap > 0 else "")
    pad = width - plain_len(text)
    left = pad // 2
    return (" " * left) + text + (" " * (pad - left))


def list_frame(headers: Sequence[str], rows: Sequence[Sequence[str]], width: int) -> str:
    """Return a ruled table. The idle column ends two spaces before the rule."""
    headers = list(headers)
    col_widths = [len(header) for header in headers]
    if col_widths:
        col_widths[-1] = max(col_widths[-1], _MIN_IDLE)
    for row in rows:
        for index, cell in enumerate(row):
            col_widths[index] = max(col_widths[index], plain_len(cell))
    gap_count = max(len(headers) - 1, 0)
    base = 4 + sum(col_widths) + (_GAP * gap_count)
    width = _visible_width(width, base)
    extra = width - base
    share, leftover = divmod(extra, gap_count) if gap_count else (0, 0)
    gaps = [_GAP + share] * gap_count
    for index in range(leftover):
        gaps[index] += 1

    def content(cells: Sequence[str], kind: str) -> str:
        parts = ["  "]
        for index, cell in enumerate(cells):
            header = headers[index]
            center_it = header in _CENTER_HEADERS if kind == "header" else header in _CENTER_VALUES
            parts.append(_fit_cell(cell, col_widths[index], center_it))
            if index < gap_count:
                parts.append(" " * gaps[index])
        text = "".join(parts)
        gap = width - 2 - plain_len(text)
        return text + (" " * gap if gap > 0 else "") + "  "

    rule = "-" * width
    lines = [rule, content(headers, "header"), rule]
    for row in rows:
        lines.append(content(row, "row"))
    if rows:
        lines.append(rule)
    return "\n".join(lines) + "\n"


def _rank_of(client: Client) -> str:
    """Return this player's rank, or newbie when they have not entered."""
    user = client.player
    if user is None:
        return RANK_NEWBIE
    return user.rank


def connected_line(name: str) -> str:
    """Return the room line for a player who just chose a name."""
    return f"[{name} has connected]\n"


def disconnected_line(name: str) -> str:
    """Return the room line for a named player who left."""
    return f"[{name} has disconnected]\n"


def connected_from_line(name: str, address: str) -> str:
    """Return the staff room line for a player who connected from ``address``."""
    return f"[{name} has connected from {address}]\n"


def reconnected_line(name: str) -> str:
    """Return the room line for a player who took over their own session."""
    return f"[{name} has reconnected]\n"


def reconnected_from_line(name: str, address: str) -> str:
    """Return the staff room line for a player who reconnected from ``address``."""
    return f"[{name} has reconnected from {address}]\n"


def disconnected_from_line(name: str, address: str) -> str:
    """Return the staff room line for a player who left from ``address``."""
    return f"[{name} has disconnected from {address}]\n"


def goes_to_line(name: str | None, destination: str) -> str:
    """Return the line a room sees when a player uses main."""
    if not name:
        return ""
    return f"{name} goes to {destination}.\n"


def _name_of(client: Client) -> str:
    """Return the name of a player who is in a room."""
    return client.name or ""


def _is_admin(host: Host, name: str) -> bool:
    """Return whether ``name`` is the admin, in any case."""
    return name.lower() == host._config.admin.lower()


def _lookup(host: Host, client: Client, name: str) -> tuple[bool, SavedPlayer | None]:
    """Return ``(True, saved)`` for ``name``, or tell ``player`` and return ``(False, None)``."""
    try:
        return True, _find_saved(host, name)
    except (ConfigError, OSError) as exc:
        print(exc)
        host.send(client, CANNOT_NOW)
        return False, None


def _normalize_saved_rank(host: Host, saved: SavedPlayer) -> SavedPlayer:
    """Return ``saved`` after config-account rank normalization, persisting when needed."""
    normalized = normalize_super_admin(saved, host._config.admin)
    if normalized.rank != saved.rank:
        save_player(host._roster_dir, normalized)
    return normalized


def _find_saved(host: Host, name: str) -> SavedPlayer | None:
    """Return the saved player whose file ``name`` points at, or ``None``."""
    if not savable_name(name):
        return None
    saved = load_saved(player_path(host._roster_dir, name))
    if saved is None:
        return None
    return _normalize_saved_rank(host, saved)


def refuses_self(host: Host, client: Client, name: str) -> bool:
    """Send the self refusal when ``name`` is this player. Return whether it was."""
    own = client.name
    if own is None or name.lower() != own.lower():
        return False
    host.send(client, "You cannot do that to yourself.\n")
    return True


def _saved_players(host: Host) -> list[SavedPlayer]:
    """Return readable roster files, lowercase path then the stored spelling."""
    directory = host._roster_dir
    try:
        paths = directory.glob("*.yml")
    except OSError as exc:
        print(exc)
        return []
    loaded: list[SavedPlayer] = []
    for path in paths:
        try:
            saved = load_saved(path)
            if saved is None:
                continue
            loaded.append(_normalize_saved_rank(host, saved))
        except (ConfigError, OSError) as exc:
            print(exc)
            continue
    return loaded


def _name_prefixes_argument(argument: str, stored: str) -> bool:
    """Return whether ``stored`` is a space-bounded prefix of ``argument``."""
    if len(argument) < len(stored):
        return False
    if argument[: len(stored)].lower() != stored.lower():
        return False
    return len(argument) == len(stored) or argument[len(stored)].isspace()
