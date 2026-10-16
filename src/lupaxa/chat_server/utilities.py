"""Shared text, frames, addresses, and roster lookups."""

from __future__ import annotations  # noqa: I001

import ipaddress
import socket
from collections.abc import Collection, Sequence
from typing import Literal, overload

from .client import Client
from .colour import plain_len
from .exceptions import ConfigError
from .globals import CANNOT_NOW
from .player import is_logged_in
from .ranks import Rank
from .saved_player import SavedPlayer, load_saved, player_path, savable_name  # fmt: skip  # noqa: E501
from .server import Server
from .time import duration_phrase

DEFAULT_FRAME = 80
MIN_IDLE = 10
GAP = 2
CENTER_HEADERS = frozenset({"Rank", "Room", "Idle"})
CENTER_VALUES = frozenset({"Rank"})


def connected_place(hostname: str, address: str) -> str:
    """Return ``hostname (address)`` for an examine from-line."""
    host = hostname.strip()
    ip = address.strip()
    if host == "" and ip == "":
        return "unknown"
    if host == "":
        host = ip
    if ip == "":
        return host
    return f"{host} ({ip})"


def login_place(hostname: str, address: str, *, ssh: bool) -> str:
    """Return the logged-in from value, including telnet or ssh."""
    kind = "ssh" if ssh else "telnet"
    return f"{connected_place(hostname, address)} [via {kind}]"


def display_address(host: str) -> str:
    """Return ``host`` for display, unwrapping IPv4-mapped IPv6 when applicable."""
    if host == "":
        return "unknown"
    bare = host.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(bare)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    return str(address)


def peer_address(address: object) -> str:
    """Return the IP string from an accept address."""
    host = address[0] if isinstance(address, tuple) and address else address
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    return display_address("" if host is None else str(host))


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


def time_frame(current: str, booted: str, uptime: str, rebooted: str | None = None) -> str:
    """Return the clock box, ending with a newline."""
    label_width = len("Current time")
    fields = [("Current time", current), ("Booted at", booted)]
    if rebooted is not None:
        fields.append(("Rebooted at", rebooted))
    fields.append(("Uptime", uptime))
    rows = [f" {label.ljust(label_width)} : {value}" for label, value in fields]
    inner = max(len(row) for row in rows) + 1
    border = "+" + ("-" * inner) + "+"
    body = ["|" + row.ljust(inner) + "|" for row in rows]
    return "\n".join((border, *body, border)) + "\n"


def frame_width(discovered: int | None, manual: int | None) -> int:
    """Return the manual width, else the discovered width, else 80."""
    if manual is not None and manual > 0:
        return manual
    if discovered is not None and discovered > 0:
        return discovered
    return DEFAULT_FRAME


def terminal_size_value(discovered_columns: int | None, discovered_rows: int | None, manual: int | None) -> str:
    """Return the examine value for this client's terminal."""
    width = frame_width(discovered_columns, manual)
    if discovered_columns is not None and discovered_rows is not None and discovered_columns > 0 and discovered_rows > 0:
        return f"{width}x{discovered_rows} ({discovered_columns}x{discovered_rows} discovered)"
    return f"{width}  (Pending)"


def terminal_size_line(discovered_columns: int | None, discovered_rows: int | None, manual: int | None) -> str:
    """Return the one examine field line for the terminal."""
    label = "Terminal Size"
    label_width = max(len("Logged in from"), len(label))
    value = terminal_size_value(discovered_columns, discovered_rows, manual)
    return f"  {label.ljust(label_width)} : {value}\n"


def width_ceiling(client: Client) -> int:
    """Return the widest manual width this client may set."""
    if client.discovered_columns is not None and client.discovered_columns > 0:
        return client.discovered_columns
    return DEFAULT_FRAME


def examine_idle(seconds: int) -> str:
    """Return the examine idle words. Ten seconds or less is not idle."""
    if seconds <= 10:
        return "Not idle at all"
    return duration_phrase(seconds)


def visible_width(terminal: int, content: int) -> int:
    """Return the wider of the terminal and the plain content."""
    return max(content, terminal)


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


def examine_frame(name: str, fields: Sequence[tuple[str, str]], width: int, description: str = "", plan: str = "") -> str:
    """Return the ruled examine profile. The name is centered.

    The room sentences sit in their own block. A description sits under the
    name when set. A plan sits under the sentences when set.
    """
    label_width = max(len("Logged in from"), *(len(label) for label, value in fields))
    profile: list[str] = []
    messages: list[str] = []
    for label, value in fields:
        line = f"  {label.ljust(label_width)} : {value}"
        if label in ("Enter message", "Login message", "Logout message"):
            messages.append(line)
        else:
            profile.append(line)
    desc_line = f"  {description}" if description else ""
    plan_line = f"  {plan}" if plan else ""
    content = max(len(name), *(plain_len(line) for line in (*profile, *messages)))
    if desc_line:
        content = max(content, plain_len(desc_line))
    if plan_line:
        content = max(content, plain_len(plan_line))
    width = visible_width(width, content)
    rule = "-" * width

    def fit(line: str) -> str:
        gap = width - plain_len(line)
        return line + (" " * gap if gap > 0 else "")

    lines = [rule, name.center(width), rule]
    if desc_line:
        lines.append(fit(desc_line))
        lines.append(rule)
    for line in profile:
        lines.append(fit(line))
    lines.append(rule)
    for line in messages:
        lines.append(fit(line))
    if messages:
        lines.append(rule)
    if plan_line:
        lines.append(fit(plan_line))
        lines.append(rule)
    return "\n".join(lines) + "\n"


def fit_cell(text: str, width: int, center_it: bool) -> str:
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
        col_widths[-1] = max(col_widths[-1], MIN_IDLE)
    for row in rows:
        for index, cell in enumerate(row):
            col_widths[index] = max(col_widths[index], plain_len(cell))
    gap_count = max(len(headers) - 1, 0)
    base = 4 + sum(col_widths) + (GAP * gap_count)
    width = visible_width(width, base)
    extra = width - base
    share, leftover = divmod(extra, gap_count) if gap_count else (0, 0)
    gaps = [GAP + share] * gap_count
    for index in range(leftover):
        gaps[index] += 1

    def content(cells: Sequence[str], kind: str) -> str:
        parts = ["  "]
        for index, cell in enumerate(cells):
            header = headers[index]
            center_it = header in CENTER_HEADERS if kind == "header" else header in CENTER_VALUES
            parts.append(fit_cell(cell, col_widths[index], center_it))
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


def get_clients(server: Server, *, alphabetical: bool = False) -> Collection[Client]:
    """Return the connected clients in login order, or by name when ``alphabetical`` is true.

    A connection with no name sorts last.
    """
    clients = server.clients.values()
    if not alphabetical:
        return clients
    return sorted(clients, key=lambda client: (client.name is None, (client.name or "").lower()))


def find_player(server: Server, client: Client, name: str, *, quiet: bool = False) -> Client | None:
    """Return the playing client whose lowercase key is ``name``.

    A miss tells ``client`` there is no such player, unless ``quiet`` is true.
    """
    key = name.lower()
    for connected in get_clients(server):
        player = connected.player
        if not is_logged_in(connected) or player is None:
            continue
        if player.lowercase_name == key:
            return connected
    if not quiet:
        server.send(client, f"No such player '{name}'.\n")
    return None


def has_rank(client: Client) -> Rank:
    """Return this player's rank, or newbie when they have not entered."""
    user = client.player
    if user is None:
        return Rank.NEWBIE
    return user.rank


def reconnected_line(name: str) -> str:
    """Return the room line for a player who took over their own session."""
    return f"[{name} has reconnected]\n"


def goes_to_line(name: str | None, destination: str) -> str:
    """Return the line a room sees when a player uses main."""
    if not name:
        return ""
    return f"{name} goes to {destination}.\n"


@overload
def get_parts(text: str | None, *, split: Literal[False] = False) -> str:
    pass


@overload
def get_parts(text: str | None, *, split: Literal[True]) -> list[str]:
    pass


def get_parts(text: str | None, *, split: bool = False) -> str | list[str]:
    """Return ``text`` stripped, or its words when ``split`` is true."""
    cleaned = (text or "").strip()
    if split:
        return cleaned.split()
    return cleaned


def get_player_name(client: Client) -> str:
    """Return the name of a player who is in a room."""
    return client.name or ""


def is_admin(server: Server, name: str) -> bool:
    """Return whether ``name`` is a super admin on disk or online."""
    saved = find_saved(server, name)
    if saved is not None and saved.rank == Rank.SUPER_ADMIN:
        return True
    lowered = name.lower()
    return any(client.name is not None and client.name.lower() == lowered and client.player is not None and client.player.rank == Rank.SUPER_ADMIN for client in get_clients(server))  # fmt: skip  # noqa: E501


def get_saved_player(server: Server, client: Client, name: str, *, quiet: bool = False) -> tuple[bool, SavedPlayer | None]:
    """Return the saved player for ``name``.

    A missing file tells ``client`` there is no saved player. A file that cannot
    be read tells them the name cannot be used right now. ``quiet`` leaves both
    unsaid. The bool is false only when the file could not be read.
    """
    try:
        saved = find_saved(server, name)
    except (ConfigError, OSError) as exc:
        print(exc)
        if not quiet:
            server.send(client, CANNOT_NOW)
        return False, None
    if saved is None and not quiet:
        server.send(client, f"No saved player '{name}'.\n")
    return True, saved


def find_saved(server: Server, name: str) -> SavedPlayer | None:
    """Return the saved player whose file ``name`` points at, or ``None``."""
    if not savable_name(name):
        return None
    return load_saved(player_path(server.roster_dir, name))


def refuses_self(server: Server, client: Client, name: str) -> bool:
    """Send the self refusal when ``name`` is this player. Return whether it was."""
    own = client.name
    if own is None or name.lower() != own.lower():
        return False
    server.send(client, "You cannot do that to yourself.\n")
    return True


def saved_players(server: Server) -> list[SavedPlayer]:
    """Return readable roster files, lowercase path then the stored spelling."""
    directory = server.roster_dir
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
            loaded.append(saved)
        except (ConfigError, OSError) as exc:
            print(exc)
            continue
    return loaded


def name_prefixes_argument(argument: str, stored: str) -> bool:
    """Return whether ``stored`` is a space-bounded prefix of ``argument``."""
    if len(argument) < len(stored):
        return False
    if argument[: len(stored)].lower() != stored.lower():
        return False
    return len(argument) == len(stored) or argument[len(stored)].isspace()
