"""Rooms loaded from YAML. This module does not touch sockets."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .client import Client
from .colour import green, plain_len
from .exceptions import ConfigError
from .player import is_logged_in

if TYPE_CHECKING:
    from .server import Server

ROOM_ID = re.compile(r"^[a-z0-9_]+$")
HOME_ID = re.compile(r"^home_[a-z][a-z0-9]{2,19}$")
VOID_ID = "void"
EXIT_CODES = frozenset({"n", "s", "e", "w", "ne", "nw", "se", "sw", "up", "down"})
ROOM_KEYS = frozenset({"description", "max_occupancy", "exits", "enter"})
NO_IMPLICIT_SCALAR = frozenset({"tag:yaml.org,2002:bool", "tag:yaml.org,2002:null"})
DIRECTION_NAMES = {
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "ne": "north-east",
    "nw": "north-west",
    "se": "south-east",
    "sw": "south-west",
    "up": "up",
    "down": "down",
}
EXIT_ORDER = ("n", "s", "e", "w", "ne", "nw", "se", "sw", "up", "down")
MIN_INNER = 14
GAP = 5
VECTORS = {
    "n": (0, -1),
    "s": (0, 1),
    "e": (1, 0),
    "w": (-1, 0),
    "ne": (1, -1),
    "nw": (-1, -1),
    "se": (1, 1),
    "sw": (-1, 1),
}


class LayoutYamlLoader(yaml.SafeLoader):
    """SafeLoader that keeps YAML 1.1 bool/null words as plain strings."""


LayoutYamlLoader.yaml_implicit_resolvers = {char: [(tag, pattern) for tag, pattern in patterns if tag not in NO_IMPLICIT_SCALAR] for char, patterns in yaml.SafeLoader.yaml_implicit_resolvers.items()}
LayoutYamlLoader.yaml_implicit_resolvers = {char: patterns for char, patterns in LayoutYamlLoader.yaml_implicit_resolvers.items() if patterns}


@dataclass(frozen=True)
class Room:
    """One room in a layout."""

    description: str
    max_occupancy: int | None
    exits: Mapping[str, str]
    enter: str | None = None


@dataclass(frozen=True)
class Layout:
    """Every room, keyed by id."""

    rooms: Mapping[str, Room]
    houses: Mapping[str, str] = field(default_factory=dict)

    def display_name(self, room_id: str) -> str:
        """Return the words players see for ``room_id``."""
        if room_id == VOID_ID:
            return "The Void"
        owner = self.houses.get(room_id)
        if owner is not None and room_id.startswith(f"{owner}_"):
            rest = room_id[len(owner) + 1 :]
            return " ".join(part.capitalize() for part in rest.split("_") if part)
        return " ".join(part.capitalize() for part in room_id.split("_"))

    def location_name(self, room_id: str, occupant: str) -> str:
        """Return the place label for ``occupant``."""
        shown = self.display_name(room_id)
        owner = self.houses.get(room_id)
        if owner is None or occupant.casefold() == owner:
            return shown
        return f"{owner.capitalize()}'s {shown}"

    def with_void(self) -> Layout:
        """Return a layout that contains The Void."""
        if VOID_ID in self.rooms:
            return self
        rooms = dict(self.rooms)
        rooms[VOID_ID] = Room("You are in The Void.", 1, {})
        return Layout(rooms=rooms, houses=dict(self.houses))

    def with_house(self, name: str, rooms: Mapping[str, Room]) -> Layout:
        """Replace ``name``'s house rooms with ``rooms``."""
        owner = name.lower()
        kept = {room_id: room for room_id, room in self.rooms.items() if self.houses.get(room_id) != owner}
        kept.update(rooms)
        houses = {room_id: who for room_id, who in self.houses.items() if who != owner}
        for room_id in rooms:
            houses[room_id] = owner
        return Layout(rooms=kept, houses=houses)

    def drop_house(self, name: str) -> Layout:
        """Remove ``name``'s house rooms."""
        return self.with_house(name, {})

    def destination(self, room_id: str | None, code: str) -> str | None:
        """Return the room id reached by ``code``, or None."""
        if room_id is None:
            return None
        return self.rooms[room_id].exits.get(code)

    def brief(self, room_id: str, others: Sequence[str]) -> str:
        """Return the description, exits, and other people in the room."""
        room = self.rooms[room_id]
        company = "Also here: " + ", ".join(sorted(others)) + "." if others else "Nobody else is here."
        present = [code for code in EXIT_ORDER if code in room.exits]
        longest = max((len(DIRECTION_NAMES[code]) for code in present), default=0)
        exit_lines = []
        for code in present:
            direction = DIRECTION_NAMES[code].ljust(longest)
            room_name = self.display_name(room.exits[code])
            exit_lines.append(f"  {direction} -> {room_name}")
        exits = "Exits: none." if not exit_lines else "Exits:\n" + "\n".join(exit_lines)
        return f"{room.description}\n\n{company}\n\n{exits}\n"

    def map_text(self, room_id: str | None, width: int = 80, *, colour: bool = False) -> str:
        """Return a map for someone standing in ``room_id``."""
        if room_id is None:
            return ""
        wide = self.map_five(room_id, colour=colour)
        longest = max((plain_len(line) for line in wide.splitlines()), default=0)
        if width >= longest:
            return wide
        return self.map_three(room_id, colour=colour)

    def map_three(self, room_id: str, *, colour: bool = False) -> str:
        """Return the 3 by 3 map for someone standing in ``room_id``."""
        exits = self.rooms[room_id].exits
        cells = self.grid_cells(room_id, 1)
        labels: list[list[str]] = []
        visible: list[str] = []
        for id_row in cells:
            label_row: list[str] = []
            for found in id_row:
                name = "" if found is None else self.display_name(found)
                label_row.append(name)
                if name:
                    visible.append(name)
            labels.append(label_row)
        width = max([MIN_INNER, *[len(name) for name in visible]])
        box_width = width + 2
        stride = box_width + GAP
        lines: list[str] = []
        row_at: list[int] = []
        for row_index, label_row in enumerate(labels):
            boxes = [box_lines(name, width, here=colour and cells[row_index][column] == room_id) for column, name in enumerate(label_row)]
            row_at.append(len(lines))
            for line_index in range(5):
                parts: list[str] = []
                for column, box in enumerate(boxes):
                    parts.append(box[line_index])
                    if column < 2:
                        linked = line_index == 2 and self.rooms_join(cells[row_index][column], cells[row_index][column + 1], "e", "w")
                        parts.append("-" * GAP if linked else " " * GAP)
                lines.append("".join(parts).rstrip())
            if row_index < 2:
                connector = [" "] * (3 * box_width + 2 * GAP)
                for column in range(3):
                    if self.rooms_join(cells[row_index][column], cells[row_index + 1][column], "s", "n"):
                        connector[column * stride + box_width // 2] = "|"
                lines.append("".join(connector).rstrip())
        self.draw_diagonal(lines, row_at, stride, box_width, "se", "\\", exits)
        self.draw_diagonal(lines, row_at, stride, box_width, "nw", "\\", exits)
        self.draw_diagonal(lines, row_at, stride, box_width, "ne", "/", exits)
        self.draw_diagonal(lines, row_at, stride, box_width, "sw", "/", exits)
        text = "\n".join(lines) + "\n"
        if "up" in exits:
            text += f"Up: {self.display_name(exits['up'])}\n"
        if "down" in exits:
            text += f"Down: {self.display_name(exits['down'])}\n"
        return text

    def map_five(self, room_id: str, *, colour: bool = False) -> str:
        """Return the 5 by 5 map for someone standing in ``room_id``."""
        exits = self.rooms[room_id].exits
        span = 2
        cells = self.grid_cells(room_id, span)
        labels: list[list[str]] = []
        visible: list[str] = []
        for id_row in cells:
            label_row: list[str] = []
            for found in id_row:
                name = "" if found is None else self.display_name(found)
                label_row.append(name)
                if name:
                    visible.append(name)
            labels.append(label_row)
        width = max([MIN_INNER, *[len(name) for name in visible]])
        box_width = width + 2
        stride = box_width + GAP
        side = 2 * span + 1
        lines: list[str] = []
        row_at: list[int] = []
        for row_index, label_row in enumerate(labels):
            boxes = [box_lines(name, width, here=colour and cells[row_index][column] == room_id) for column, name in enumerate(label_row)]
            row_at.append(len(lines))
            for line_index in range(5):
                parts: list[str] = []
                for column, box in enumerate(boxes):
                    parts.append(box[line_index])
                    if column < side - 1:
                        linked = line_index == 2 and self.rooms_join(cells[row_index][column], cells[row_index][column + 1], "e", "w")
                        parts.append("-" * GAP if linked else " " * GAP)
                lines.append("".join(parts).rstrip())
            if row_index < side - 1:
                connector = [" "] * (side * box_width + (side - 1) * GAP)
                for column in range(side):
                    if self.rooms_join(cells[row_index][column], cells[row_index + 1][column], "s", "n"):
                        connector[column * stride + box_width // 2] = "|"
                lines.append("".join(connector).rstrip())
        for row_index in range(side - 1):
            for column in range(side - 1):
                if self.rooms_join(cells[row_index][column], cells[row_index + 1][column + 1], "se", "nw"):
                    self.draw_diagonal_at(lines, row_at, stride, box_width, row_index, column, "se", "\\")
                if self.rooms_join(cells[row_index][column + 1], cells[row_index + 1][column], "sw", "ne"):
                    self.draw_diagonal_at(lines, row_at, stride, box_width, row_index, column + 1, "sw", "/")
        for row_index in range(1, side):
            for column in range(1, side):
                if self.rooms_join(cells[row_index][column], cells[row_index - 1][column - 1], "nw", "se"):
                    self.draw_diagonal_at(lines, row_at, stride, box_width, row_index, column, "nw", "\\")
        drop_empty_edges(lines)
        text = "\n".join(lines) + "\n"
        if "up" in exits:
            text += f"Up: {self.display_name(exits['up'])}\n"
        if "down" in exits:
            text += f"Down: {self.display_name(exits['down'])}\n"
        return text

    def grid_cells(self, origin: str, span: int) -> list[list[str | None]]:
        """Return every room whose grid cell lies within ``span`` of ``origin``.

        Compass exits place each room. A walk may be longer than ``span``. Up
        and down are not grid steps. A room already placed is not moved.
        """
        positions: dict[str, tuple[int, int]] = {origin: (0, 0)}
        cells: dict[tuple[int, int], str] = {(0, 0): origin}
        queue = [origin]
        while queue:
            current = queue.pop(0)
            x, y = positions[current]
            for code, (step_x, step_y) in VECTORS.items():
                dest = self.rooms[current].exits.get(code)
                if dest is None or dest not in self.rooms or dest in positions:
                    continue
                next_cell = (x + step_x, y + step_y)
                if next_cell in cells:
                    continue
                positions[dest] = next_cell
                cells[next_cell] = dest
                queue.append(dest)
        grid: list[list[str | None]] = []
        for dy in range(-span, span + 1):
            row = [cells.get((dx, dy)) for dx in range(-span, span + 1)]
            grid.append(row)
        return grid

    def draw_diagonal_at(self, lines: list[str], row_at: list[int], stride: int, box_width: int, grid_row: int, grid_col: int, code: str, mark: str) -> None:  # fmt: skip  # noqa: E501
        """Draw one diagonal exit on the map."""
        left = grid_col * stride
        right = grid_col * stride + box_width - 1
        step = GAP // 2 + 1
        span = GAP + 1
        if code == "se":
            points = ((row_at[grid_row] + 4, right), (row_at[grid_row] + 5, right + step), (row_at[grid_row + 1], right + span))
        elif code == "nw":
            points = ((row_at[grid_row], left), (row_at[grid_row - 1] + 5, left - step), (row_at[grid_row - 1] + 4, left - span))
        elif code == "ne":
            points = ((row_at[grid_row], right), (row_at[grid_row - 1] + 5, right + step), (row_at[grid_row - 1] + 4, right + span))
        else:
            points = ((row_at[grid_row] + 4, left), (row_at[grid_row] + 5, left - step), (row_at[grid_row + 1], left - span))
        for line_index, column in points:
            padded = list(lines[line_index].ljust(column + 1))
            padded[column] = mark
            lines[line_index] = "".join(padded).rstrip()

    def draw_diagonal(self, lines: list[str], row_at: list[int], stride: int, box_width: int, code: str, mark: str, exits: Mapping[str, str]) -> None:  # fmt: skip  # noqa: E501
        """Draw the diagonal exits that leave the current room."""
        if code not in exits:
            return
        # The stroke replaces both box corners and the middle of the five-column gap.
        left = stride
        right = stride + box_width - 1
        step = GAP // 2 + 1
        span = GAP + 1
        if code == "se":
            points = ((row_at[1] + 4, right), (row_at[1] + 5, right + step), (row_at[2], right + span))
        elif code == "nw":
            points = ((row_at[1], left), (row_at[0] + 5, left - step), (row_at[0] + 4, left - span))
        elif code == "ne":
            points = ((row_at[1], right), (row_at[0] + 5, right + step), (row_at[0] + 4, right + span))
        else:
            points = ((row_at[1] + 4, left), (row_at[1] + 5, left - step), (row_at[2], left - span))
        for line_index, column in points:
            padded = list(lines[line_index].ljust(column + 1))
            padded[column] = mark
            lines[line_index] = "".join(padded).rstrip()

    def rooms_join(self, left: str | None, right: str | None, forward: str, back: str) -> bool:
        """Return whether ``left`` and ``right`` have an exit between them."""
        if left is None or right is None:
            return False
        forward_exit = self.rooms[left].exits.get(forward) == right
        back_exit = self.rooms[right].exits.get(back) == left
        return forward_exit or back_exit


def is_home_id(room_id: str | None) -> bool:
    """Return whether ``room_id`` is reserved for a resident's home."""
    return room_id is not None and HOME_ID.fullmatch(room_id) is not None


def drop_empty_edges(lines: list[str]) -> None:
    """Remove blank rows left where the grid has no rooms."""
    while lines and lines[0] == "":
        del lines[0]
    while lines and lines[-1] == "":
        del lines[-1]


def center_label(text: str, width: int) -> str:
    """Return ``text`` centered in ``width``."""
    pad = width - len(text)
    left = pad // 2
    return " " * left + text + " " * (pad - left)


def box_lines(name: str, width: int, *, here: bool = False) -> list[str]:
    """Return the five lines of one map room."""
    span = width + 2
    if name == "":
        return [" " * span for _ in range(5)]
    label = center_label(name, width)
    if here:
        label = label.replace(name, green(name), 1)
    return [
        "+" + "-" * width + "+",
        "|" + " " * width + "|",
        "|" + label + "|",
        "|" + " " * width + "|",
        "+" + "-" * width + "+",
    ]


def spoken_line(name: str | None, sentence: str) -> str:
    """Return ``name`` plus ``sentence``, with one closing mark."""
    if not name:
        return ""
    text = sentence.strip()
    if text[-1:] not in ".!?":
        text += "."
    return f"{name} {text}\n"


def room_enter(room_id: str, value: object) -> str | None:
    """Return a one-line enter sentence, or None when it was left out."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"Room {room_id} enter must be a string.")
    if "\n" in value:
        raise ConfigError(f"Room {room_id} enter must be one line.")
    text = value.strip()
    if text == "":
        return None
    return text


def leave_line(name: str | None, code: str, destination: str) -> str:
    """Return the line the room being left sees."""
    if name is None:
        return ""
    return f"{name} goes {DIRECTION_NAMES[code]} to {destination}.\n"


def full_line(display_name: str) -> str:
    """Return the line a player sees when a room is full."""
    return f"{display_name} is full.\n"


def cannot_go_line(code: str) -> str:
    """Return the line a player sees when that direction has no exit."""
    return f"You cannot go {DIRECTION_NAMES[code]}.\n"


def house_path(system_file: Path, name: str) -> Path:
    """Return the house file beside the system rooms file."""
    return system_file.parent / f"{name.lower()}.yml"


def house_path_blocked(rooms_file: Path, help_file: Path, name: str) -> bool:
    """Return whether ``name``'s house path is the system file or the help file."""
    path = house_path(rooms_file, name)
    if path in (rooms_file, help_file):
        return True
    try:
        resolved = path.resolve()
        return resolved in (rooms_file.resolve(), help_file.resolve())
    except OSError:
        return True


def unlink_house(server: Server, name: str) -> None:
    """Delete ``name``'s house file when it is really theirs."""
    if house_path_blocked(server.config.rooms, server.config.help, name):
        return
    path = house_path(server.config.rooms, name)
    try:
        if not path.is_file():
            return
        load_house(path, name, system_room_ids(server.layout))
    except (ConfigError, OSError):
        return
    try:
        path.unlink()
    except OSError as exc:
        print(exc)


def house_room_id(name: str) -> str:
    """Return the home room id for ``name``."""
    return f"{name.lower()}_home"


def system_room_ids(layout: Layout) -> set[str]:
    """Return the office room ids, without houses or The Void."""
    return {room_id for room_id in layout.rooms if room_id not in layout.houses and room_id != VOID_ID}


def load_house(path: Path, name: str, system_ids: Collection[str]) -> dict[str, Room]:
    """Return the rooms in ``name``'s house file."""
    owner = name.lower()
    prefix = f"{owner}_"
    rooms = read_room_file(path, system=False)
    for room_id in rooms:
        if not room_id.startswith(prefix) or room_id == prefix:
            raise ConfigError(f"Room id {room_id} must start with {prefix}.")
        if room_id in system_ids:
            raise ConfigError(f"Room id {room_id} is already a system room.")
    return rooms


def write_default_house(system_file: Path, name: str) -> None:
    """Write a one-room house file for ``name``."""
    path = house_path(system_file, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    room_id = house_room_id(name)
    path.write_text(f"{room_id}:\n  description: You are home.\n  max_occupancy: 1\n  exits: {{}}\n", encoding="utf-8")


def restore_saved_room(server: Server, name: str, room_id: str | None) -> str:
    """Return the room id to put ``name`` back in."""
    if room_id is None:
        return VOID_ID
    if room_id == f"home_{name.lower()}":
        room_id = house_room_id(name)
    if room_id in server.layout.rooms and room_id not in server.layout.houses:
        return room_id
    if room_id in server.layout.houses and server.layout.houses[room_id] == name.lower():
        return room_id
    owner = name.lower()
    if room_id.startswith(f"{owner}_"):
        if house_path_blocked(server.config.rooms, server.config.help, name):
            return VOID_ID
        path = house_path(server.config.rooms, name)
        try:
            if not path.is_file() and room_id == house_room_id(name):
                write_default_house(server.config.rooms, name)
            rooms = load_house(path, name, system_room_ids(server.layout))
        except (ConfigError, OSError) as exc:
            print(exc)
            return VOID_ID
        if room_id in rooms:
            server.layout = server.layout.with_house(name, rooms)
            return room_id
    if room_id in server.layout.rooms:
        return room_id
    return VOID_ID


def is_full(layout: Layout, clients: Iterable[Client], room_id: str) -> bool:
    """Return whether room_id cannot take another player."""
    if room_id == VOID_ID:
        return False
    limit = layout.rooms[room_id].max_occupancy
    if limit is None:
        return False
    return occupancy(clients, room_id) >= limit


def release_empty_houses(layout: Layout, clients: Iterable[Client]) -> Layout:
    """Drop house rooms that nobody logged in is standing in."""
    occupied = {client.room for client in clients if is_logged_in(client) and client.room is not None}
    current = layout
    for owner in set(layout.houses.values()):
        ids = [room_id for room_id, who in layout.houses.items() if who == owner]
        if any(room_id in occupied for room_id in ids):
            continue
        current = current.drop_house(owner)
    return current


def load_layout(path: Path) -> Layout:
    """Return the rooms stored at ``path``.

    Raises
    ------
    ConfigError
        The file cannot be read or does not describe a valid layout.
    """
    return Layout(rooms=read_room_file(path, system=True))


def read_room_file(path: Path, *, system: bool) -> dict[str, Room]:
    """Return the rooms stored at ``path``."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read rooms file {path}: {exc.strerror}") from exc
    try:
        payload = yaml.load(raw_text, Loader=LayoutYamlLoader)  # noqa: S506
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"Rooms file {path} must be a YAML mapping.")
    rooms: dict[str, Room] = {}
    for room_id, body in payload.items():
        rooms[str(room_id)] = parse_room(str(room_id), body, system=system)
    for room_id, room in rooms.items():
        for code, target in room.exits.items():
            if target == room_id:
                raise ConfigError(f"Room {room_id} exit {code} points at itself.")
            if target not in rooms:
                raise ConfigError(f"Room {room_id} exit {code} points at missing room {target}.")
    return rooms


def parse_room(room_id: str, body: object, *, system: bool = True) -> Room:
    """Return one room parsed from ``body``."""
    if not ROOM_ID.fullmatch(room_id):
        raise ConfigError(f"Room id {room_id} must be lowercase letters, digits, and underscores.")
    if room_id == VOID_ID:
        raise ConfigError(f"Room id {room_id} is reserved.")
    if system and is_home_id(room_id):
        raise ConfigError(f"Room id {room_id} is reserved for a home.")
    if not isinstance(body, dict):
        raise ConfigError(f"Room {room_id} must be a mapping.")
    extra = sorted(str(key) for key in body if key not in ROOM_KEYS)
    if extra:
        raise ConfigError(f"Unknown room key on {room_id}: {', '.join(extra)}")
    if "description" not in body:
        raise ConfigError(f"Room {room_id} is missing description.")
    description = body["description"]
    if not isinstance(description, str) or description == "":
        raise ConfigError(f"Room {room_id} description must be a non-empty string.")
    if "max_occupancy" not in body:
        occupancy = None
    else:
        occupancy = body["max_occupancy"]
        if isinstance(occupancy, bool) or not isinstance(occupancy, int):
            raise ConfigError(f"Room {room_id} max_occupancy must be an integer.")
        if occupancy < 1:
            raise ConfigError(f"Room {room_id} max_occupancy must be 1 or more.")
    if "exits" not in body:
        raise ConfigError(f"Room {room_id} is missing exits.")
    exits = body["exits"]
    if not isinstance(exits, dict):
        raise ConfigError(f"Room {room_id} exits must be a mapping.")
    cleaned: dict[str, str] = {}
    for code, target in exits.items():
        direction = str(code)
        if direction not in EXIT_CODES:
            raise ConfigError(f"Room {room_id} has unknown exit direction: {direction}.")
        if not isinstance(target, str):
            raise ConfigError(f"Room {room_id} exit {direction} must name a room.")
        cleaned[direction] = target
    return Room(description=description, max_occupancy=occupancy, exits=cleaned, enter=room_enter(room_id, body.get("enter")))


def is_in_room(client: Client, room_id: str) -> bool:
    """Return whether ``client`` is logged in and in ``room_id``."""
    return is_logged_in(client) and client.room == room_id


def occupancy(clients: Iterable[Client], room_id: str) -> int:
    """Return how many logged-in clients are in ``room_id``."""
    return sum(1 for client in clients if is_in_room(client, room_id))


def others(clients: Iterable[Client], room_id: str, skip: Client) -> list[str]:
    """Return the names of the other players in ``room_id``."""
    return [client.name for client in clients if is_in_room(client, room_id) and client is not skip and client.name is not None]
