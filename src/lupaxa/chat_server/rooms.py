"""Rooms loaded from YAML. This module does not touch sockets."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from .colour import green, plain_len
from .exceptions import ConfigError

_ROOM_ID = re.compile(r"^[a-z0-9_]+$")
_EXIT_CODES = frozenset({"n", "s", "e", "w", "ne", "nw", "se", "sw", "up", "down"})
_ROOM_KEYS = frozenset({"description", "max_occupancy", "exits", "enter"})
_NO_IMPLICIT_SCALAR = frozenset(
    {"tag:yaml.org,2002:bool", "tag:yaml.org,2002:null"},
)
_DIRECTION_NAMES = {
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
_EXIT_ORDER = ("n", "s", "e", "w", "ne", "nw", "se", "sw", "up", "down")
_MIN_INNER = 14
_GAP = 5
_VECTORS = {
    "n": (0, -1),
    "s": (0, 1),
    "e": (1, 0),
    "w": (-1, 0),
    "ne": (1, -1),
    "nw": (-1, -1),
    "se": (1, 1),
    "sw": (-1, 1),
}


class _LayoutYamlLoader(yaml.SafeLoader):
    """SafeLoader that keeps YAML 1.1 bool/null words as plain strings."""


_LayoutYamlLoader.yaml_implicit_resolvers = {
    char: [(tag, pattern) for tag, pattern in patterns if tag not in _NO_IMPLICIT_SCALAR]
    for char, patterns in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_LayoutYamlLoader.yaml_implicit_resolvers = {
    char: patterns
    for char, patterns in _LayoutYamlLoader.yaml_implicit_resolvers.items()
    if patterns
}


@dataclass(frozen=True)
class Room:
    """One room in a layout."""

    description: str
    max_occupancy: int
    exits: Mapping[str, str]
    enter: str | None = None


@dataclass(frozen=True)
class Layout:
    """Every room, keyed by id."""

    rooms: Mapping[str, Room]

    def display_name(self, room_id: str) -> str:
        """Return the words players see for ``room_id``."""
        return " ".join(part.capitalize() for part in room_id.split("_"))

    def destination(self, room_id: str | None, code: str) -> str | None:
        """Return the room id reached by ``code``, or None."""
        if room_id is None:
            return None
        return self.rooms[room_id].exits.get(code)

    def brief(self, room_id: str, others: Sequence[str]) -> str:
        """Return the description, exits, and other people in the room."""
        room = self.rooms[room_id]
        if others:
            company = "Also here: " + ", ".join(sorted(others)) + "."
        else:
            company = "Nobody else is here."
        present = [code for code in _EXIT_ORDER if code in room.exits]
        longest = max((len(_DIRECTION_NAMES[code]) for code in present), default=0)
        exit_lines = []
        for code in present:
            direction = _DIRECTION_NAMES[code].ljust(longest)
            room_name = self.display_name(room.exits[code])
            exit_lines.append(f"  {direction} -> {room_name}")
        exits = "Exits: none." if not exit_lines else "Exits:\n" + "\n".join(exit_lines)
        return f"{room.description}\n\n{company}\n\n{exits}\n"

    def map_text(self, room_id: str | None, width: int = 80, *, colour: bool = False) -> str:
        """Return a map for someone standing in ``room_id``."""
        if room_id is None:
            return ""
        wide = self._map_five(room_id, colour=colour)
        longest = max((plain_len(line) for line in wide.splitlines()), default=0)
        if width >= longest:
            return wide
        return self._map_three(room_id, colour=colour)

    def _map_three(self, room_id: str, *, colour: bool = False) -> str:
        """Return the 3 by 3 map for someone standing in ``room_id``."""
        exits = self.rooms[room_id].exits
        cells = self._grid_cells(room_id, 1)
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
        width = max([_MIN_INNER, *[len(name) for name in visible]])
        box_width = width + 2
        stride = box_width + _GAP
        lines: list[str] = []
        row_at: list[int] = []
        for row_index, label_row in enumerate(labels):
            boxes = [
                _box_lines(
                    name,
                    width,
                    here=colour and cells[row_index][column] == room_id,
                )
                for column, name in enumerate(label_row)
            ]
            row_at.append(len(lines))
            for line_index in range(5):
                parts: list[str] = []
                for column, box in enumerate(boxes):
                    parts.append(box[line_index])
                    if column < 2:
                        linked = line_index == 2 and self._rooms_join(
                            cells[row_index][column],
                            cells[row_index][column + 1],
                            "e",
                            "w",
                        )
                        parts.append("-" * _GAP if linked else " " * _GAP)
                lines.append("".join(parts).rstrip())
            if row_index < 2:
                connector = [" "] * (3 * box_width + 2 * _GAP)
                for column in range(3):
                    if self._rooms_join(
                        cells[row_index][column],
                        cells[row_index + 1][column],
                        "s",
                        "n",
                    ):
                        connector[column * stride + box_width // 2] = "|"
                lines.append("".join(connector).rstrip())
        self._draw_diagonal(lines, row_at, stride, box_width, "se", "\\", exits)
        self._draw_diagonal(lines, row_at, stride, box_width, "nw", "\\", exits)
        self._draw_diagonal(lines, row_at, stride, box_width, "ne", "/", exits)
        self._draw_diagonal(lines, row_at, stride, box_width, "sw", "/", exits)
        text = "\n".join(lines) + "\n"
        if "up" in exits:
            text += f"Up: {self.display_name(exits['up'])}\n"
        if "down" in exits:
            text += f"Down: {self.display_name(exits['down'])}\n"
        return text

    def _map_five(self, room_id: str, *, colour: bool = False) -> str:
        """Return the 5 by 5 map for someone standing in ``room_id``."""
        exits = self.rooms[room_id].exits
        span = 2
        cells = self._grid_cells(room_id, span)
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
        width = max([_MIN_INNER, *[len(name) for name in visible]])
        box_width = width + 2
        stride = box_width + _GAP
        side = 2 * span + 1
        lines: list[str] = []
        row_at: list[int] = []
        for row_index, label_row in enumerate(labels):
            boxes = [
                _box_lines(
                    name,
                    width,
                    here=colour and cells[row_index][column] == room_id,
                )
                for column, name in enumerate(label_row)
            ]
            row_at.append(len(lines))
            for line_index in range(5):
                parts: list[str] = []
                for column, box in enumerate(boxes):
                    parts.append(box[line_index])
                    if column < side - 1:
                        linked = line_index == 2 and self._rooms_join(
                            cells[row_index][column],
                            cells[row_index][column + 1],
                            "e",
                            "w",
                        )
                        parts.append("-" * _GAP if linked else " " * _GAP)
                lines.append("".join(parts).rstrip())
            if row_index < side - 1:
                connector = [" "] * (side * box_width + (side - 1) * _GAP)
                for column in range(side):
                    if self._rooms_join(
                        cells[row_index][column],
                        cells[row_index + 1][column],
                        "s",
                        "n",
                    ):
                        connector[column * stride + box_width // 2] = "|"
                lines.append("".join(connector).rstrip())
        for row_index in range(side - 1):
            for column in range(side - 1):
                if self._rooms_join(
                    cells[row_index][column],
                    cells[row_index + 1][column + 1],
                    "se",
                    "nw",
                ):
                    self._draw_diagonal_at(
                        lines,
                        row_at,
                        stride,
                        box_width,
                        row_index,
                        column,
                        "se",
                        "\\",
                    )
                if self._rooms_join(
                    cells[row_index][column + 1],
                    cells[row_index + 1][column],
                    "sw",
                    "ne",
                ):
                    self._draw_diagonal_at(
                        lines,
                        row_at,
                        stride,
                        box_width,
                        row_index,
                        column + 1,
                        "sw",
                        "/",
                    )
        for row_index in range(1, side):
            for column in range(1, side):
                if self._rooms_join(
                    cells[row_index][column],
                    cells[row_index - 1][column - 1],
                    "nw",
                    "se",
                ):
                    self._draw_diagonal_at(
                        lines,
                        row_at,
                        stride,
                        box_width,
                        row_index,
                        column,
                        "nw",
                        "\\",
                    )
        _drop_empty_edges(lines)
        text = "\n".join(lines) + "\n"
        if "up" in exits:
            text += f"Up: {self.display_name(exits['up'])}\n"
        if "down" in exits:
            text += f"Down: {self.display_name(exits['down'])}\n"
        return text

    def _grid_cells(self, origin: str, span: int) -> list[list[str | None]]:
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
            for code, (step_x, step_y) in _VECTORS.items():
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

    def _draw_diagonal_at(
        self,
        lines: list[str],
        row_at: list[int],
        stride: int,
        box_width: int,
        grid_row: int,
        grid_col: int,
        code: str,
        mark: str,
    ) -> None:
        left = grid_col * stride
        right = grid_col * stride + box_width - 1
        step = _GAP // 2 + 1
        span = _GAP + 1
        if code == "se":
            points = (
                (row_at[grid_row] + 4, right),
                (row_at[grid_row] + 5, right + step),
                (row_at[grid_row + 1], right + span),
            )
        elif code == "nw":
            points = (
                (row_at[grid_row], left),
                (row_at[grid_row - 1] + 5, left - step),
                (row_at[grid_row - 1] + 4, left - span),
            )
        elif code == "ne":
            points = (
                (row_at[grid_row], right),
                (row_at[grid_row - 1] + 5, right + step),
                (row_at[grid_row - 1] + 4, right + span),
            )
        else:
            points = (
                (row_at[grid_row] + 4, left),
                (row_at[grid_row] + 5, left - step),
                (row_at[grid_row + 1], left - span),
            )
        for line_index, column in points:
            padded = list(lines[line_index].ljust(column + 1))
            padded[column] = mark
            lines[line_index] = "".join(padded).rstrip()

    def _draw_diagonal(
        self,
        lines: list[str],
        row_at: list[int],
        stride: int,
        box_width: int,
        code: str,
        mark: str,
        exits: Mapping[str, str],
    ) -> None:
        if code not in exits:
            return
        # The stroke replaces both box corners and the middle of the five-column gap.
        left = stride
        right = stride + box_width - 1
        step = _GAP // 2 + 1
        span = _GAP + 1
        if code == "se":
            points = (
                (row_at[1] + 4, right),
                (row_at[1] + 5, right + step),
                (row_at[2], right + span),
            )
        elif code == "nw":
            points = (
                (row_at[1], left),
                (row_at[0] + 5, left - step),
                (row_at[0] + 4, left - span),
            )
        elif code == "ne":
            points = (
                (row_at[1], right),
                (row_at[0] + 5, right + step),
                (row_at[0] + 4, right + span),
            )
        else:
            points = (
                (row_at[1] + 4, left),
                (row_at[1] + 5, left - step),
                (row_at[2], left - span),
            )
        for line_index, column in points:
            padded = list(lines[line_index].ljust(column + 1))
            padded[column] = mark
            lines[line_index] = "".join(padded).rstrip()

    def _rooms_join(self, left: str | None, right: str | None, forward: str, back: str) -> bool:
        """Return whether ``left`` and ``right`` have an exit between them."""
        if left is None or right is None:
            return False
        forward_exit = self.rooms[left].exits.get(forward) == right
        back_exit = self.rooms[right].exits.get(back) == left
        return forward_exit or back_exit


def _drop_empty_edges(lines: list[str]) -> None:
    """Remove blank rows left where the grid has no rooms."""
    while lines and lines[0] == "":
        del lines[0]
    while lines and lines[-1] == "":
        del lines[-1]


def _center_label(text: str, width: int) -> str:
    pad = width - len(text)
    left = pad // 2
    return " " * left + text + " " * (pad - left)


def _box_lines(name: str, width: int, *, here: bool = False) -> list[str]:
    span = width + 2
    if name == "":
        return [" " * span for _ in range(5)]
    label = _center_label(name, width)
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


def _room_enter(room_id: str, value: object) -> str | None:
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
    return f"{name} goes {_DIRECTION_NAMES[code]} to {destination}.\n"


def full_line(display_name: str) -> str:
    """Return the line a player sees when a room is full."""
    return f"{display_name} is full.\n"


def cannot_go_line(code: str) -> str:
    """Return the line a player sees when that direction has no exit."""
    return f"You cannot go {_DIRECTION_NAMES[code]}.\n"


def load_layout(path: Path) -> Layout:
    """Return the rooms stored at ``path``.

    Raises
    ------
    ConfigError
        The file cannot be read or does not describe a valid layout.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read rooms file {path}: {exc.strerror}") from exc
    try:
        payload = yaml.load(raw_text, Loader=_LayoutYamlLoader)  # noqa: S506
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"Rooms file {path} must be a YAML mapping.")
    rooms: dict[str, Room] = {}
    for room_id, body in payload.items():
        rooms[str(room_id)] = _room(str(room_id), body)
    for room_id, room in rooms.items():
        for code, target in room.exits.items():
            if target == room_id:
                raise ConfigError(f"Room {room_id} exit {code} points at itself.")
            if target not in rooms:
                raise ConfigError(f"Room {room_id} exit {code} points at missing room {target}.")
    return Layout(rooms=rooms)


def _room(room_id: str, body: object) -> Room:
    if not _ROOM_ID.fullmatch(room_id):
        raise ConfigError(f"Room id {room_id} must be lowercase letters, digits, and underscores.")
    if not isinstance(body, dict):
        raise ConfigError(f"Room {room_id} must be a mapping.")
    extra = sorted(str(key) for key in body if key not in _ROOM_KEYS)
    if extra:
        raise ConfigError(f"Unknown room key on {room_id}: {', '.join(extra)}")
    if "description" not in body:
        raise ConfigError(f"Room {room_id} is missing description.")
    description = body["description"]
    if not isinstance(description, str) or description == "":
        raise ConfigError(f"Room {room_id} description must be a non-empty string.")
    if "max_occupancy" not in body:
        raise ConfigError(f"Room {room_id} is missing max_occupancy.")
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
        if direction not in _EXIT_CODES:
            raise ConfigError(f"Room {room_id} has unknown exit direction: {direction}.")
        if not isinstance(target, str):
            raise ConfigError(f"Room {room_id} exit {direction} must name a room.")
        cleaned[direction] = target
    return Room(
        description=description,
        max_occupancy=occupancy,
        exits=cleaned,
        enter=_room_enter(room_id, body.get("enter")),
    )
