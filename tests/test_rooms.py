"""Rooms file loader."""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lupaxa.chat_server.client import Client
from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.player import Player
from lupaxa.chat_server.rooms import Layout, Room, cannot_go_line, full_line, house_room_id, is_full, is_home_id, leave_line, load_house, load_layout, release_empty_houses, spoken_line, system_room_ids, write_default_house

OFFICE = """\
reception:
  description: The front desk.
  max_occupancy: 2
  exits:
    n: hall
hall:
  description: A hall.
  max_occupancy: 2
  exits:
    s: reception
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rooms.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_home_id_displays_as_home_and_is_reserved(tmp_path: Path) -> None:
    """A house home reads Home, and a system file cannot take one."""
    layout = Layout(rooms={"reception": Room("The desk.", 4, {})})
    assert layout.display_name("the_bar") == "The Bar"
    assert is_home_id("home_ada") is True
    assert is_home_id("reception") is False
    assert is_home_id(None) is False
    assert house_room_id("Ada") == "ada_home"
    system = write(tmp_path, "reception:\n  description: The desk.\n  max_occupancy: 4\n  exits: {}\n")
    write_default_house(system, "Ada")
    house = tmp_path / "ada.yml"
    assert house.read_text(encoding="utf-8") == "ada_home:\n  description: You are home.\n  max_occupancy: 1\n  exits: {}\n"
    loaded = load_house(house, "Ada", system_room_ids(layout))
    updated = layout.with_house("Ada", loaded)
    assert updated.rooms["ada_home"].description == "You are home."
    assert updated.rooms["ada_home"].max_occupancy == 1
    assert updated.rooms["ada_home"].exits == {}
    assert updated.display_name("ada_home") == "Home"
    assert "ada_home" not in layout.rooms
    with pytest.raises(ConfigError, match="Room id home_ada is reserved for a home."):
        load_layout(write(tmp_path, "home_ada:\n  description: No.\n  max_occupancy: 1\n  exits: {}\n"))


def test_void_is_reserved_in_a_rooms_file(tmp_path: Path) -> None:
    """A rooms file cannot define the built-in void id."""
    with pytest.raises(ConfigError, match="Room id void is reserved."):
        load_layout(write(tmp_path, "void:\n  description: No.\n  max_occupancy: 1\n  exits: {}\n"))


def test_the_void_is_added_and_has_no_limit(tmp_path: Path) -> None:
    """The Void is built in, unlimited, and has no exits."""
    layout = load_layout(write(tmp_path, "reception:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n")).with_void()
    assert layout.display_name("void") == "The Void"
    assert layout.rooms["void"].description == "You are in The Void."
    assert layout.rooms["void"].exits == {}
    left, right = socket.socketpair()
    other, peer = socket.socketpair()
    try:
        ada = Client(left, player=Player("Ada", "void", datetime.now(timezone.utc)))
        bea = Client(other, player=Player("Bea", "void", datetime.now(timezone.utc)))
        assert is_full(layout, [ada, bea], "void") is False
    finally:
        left.close()
        right.close()
        other.close()
        peer.close()


def test_a_house_file_uses_the_player_prefix(tmp_path: Path) -> None:
    """Bea's kitchen is labelled Kitchen, and a visitor would see Bea's Kitchen."""
    system = load_layout(write(tmp_path, "reception:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n")).with_void()
    house = tmp_path / "bea.yml"
    house.write_text("bea_home:\n  description: You are home.\n  max_occupancy: 1\n  exits:\n    n: bea_kitchen\nbea_kitchen:\n  description: A kettle.\n  max_occupancy: 2\n  exits:\n    s: bea_home\n", encoding="utf-8")
    loaded = load_house(house, "Bea", system_room_ids(system))
    layout = system.with_house("Bea", loaded)
    assert layout.display_name("bea_kitchen") == "Kitchen"
    assert layout.display_name("bea_home") == "Home"
    assert layout.location_name("bea_kitchen", "Bea") == "Kitchen"
    assert layout.location_name("bea_kitchen", "Ada") == "Bea's Kitchen"
    assert layout.brief("bea_home", []).splitlines()[-1] == "  north -> Kitchen"


def test_a_house_exit_must_stay_in_the_file(tmp_path: Path) -> None:
    """An exit into the office, a foreign id, or a system id is rejected."""
    system = load_layout(write(tmp_path, "meeting_room:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n"))
    outside = tmp_path / "bea.yml"
    outside.write_text("bea_home:\n  description: You are home.\n  max_occupancy: 1\n  exits:\n    n: meeting_room\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="points at missing room meeting_room"):
        load_house(outside, "Bea", system_room_ids(system))
    foreign = tmp_path / "ada.yml"
    foreign.write_text("bea_kitchen:\n  description: No.\n  max_occupancy: 1\n  exits: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Room id bea_kitchen must start with ada_."):
        load_house(foreign, "Ada", system_room_ids(system))
    clash = tmp_path / "meeting.yml"
    clash.write_text("meeting_room:\n  description: No.\n  max_occupancy: 1\n  exits: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Room id meeting_room is already a system room."):
        load_house(clash, "Meeting", system_room_ids(system))


def test_release_empty_houses_keeps_an_occupied_house(tmp_path: Path) -> None:
    """An occupied house stays, and an empty house is dropped."""
    base = load_layout(write(tmp_path, "reception:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n")).with_void()
    layout = base.with_house("Ada", {"ada_home": Room("Ada is home.", 1, {})}).with_house("Bea", {"bea_home": Room("Bea is home.", 1, {})})
    left, right = socket.socketpair()
    try:
        ada = Client(left, player=Player("Ada", "ada_home", datetime.now(timezone.utc)))
        released = release_empty_houses(layout, [ada])
    finally:
        left.close()
        right.close()
    assert "ada_home" in released.rooms
    assert released.rooms["ada_home"].description == "Ada is home."
    assert "bea_home" not in released.rooms
    assert "reception" in released.rooms


def test_write_default_house_writes_one_room(tmp_path: Path) -> None:
    """A default house file is one home room beside the system file."""
    system = write(tmp_path, "reception:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n")
    assert house_room_id("Bea") == "bea_home"
    write_default_house(system, "Bea")
    assert (tmp_path / "bea.yml").read_text(encoding="utf-8") == "bea_home:\n  description: You are home.\n  max_occupancy: 1\n  exits: {}\n"


def test_brief_names_exits_and_people(tmp_path: Path) -> None:
    """The brief lists exits in compass order and sorts other names."""
    text = OFFICE + ("closet:\n  description: A closet.\n  max_occupancy: 1\n  exits: {}\n")
    layout = load_layout(write(tmp_path, text))
    text = layout.brief("reception", ["Cal", "Bea"])
    assert text == ("The front desk.\n\nAlso here: Bea, Cal.\n\nExits:\n  north -> Hall\n")
    assert layout.brief("hall", []) == ("A hall.\n\nNobody else is here.\n\nExits:\n  south -> Reception\n")
    alone = layout.brief("closet", [])
    assert alone.endswith("Exits: none.\n")
    assert "Reception" not in alone


def test_brief_lines_up_exit_arrows(tmp_path: Path) -> None:
    """Arrows line up on the longest direction that room actually has."""
    text = """\
lobby:
  description: A lobby.
  max_occupancy: 4
  exits:
    n: hall
    ne: office
    e: court
hall:
  description: A hall.
  max_occupancy: 2
  exits:
    s: lobby
office:
  description: An office.
  max_occupancy: 2
  exits:
    sw: lobby
court:
  description: A court.
  max_occupancy: 2
  exits:
    w: lobby
"""
    layout = load_layout(write(tmp_path, text))
    brief = layout.brief("lobby", [])
    assert "  north      -> Hall\n" in brief
    assert "  north-east -> Office\n" in brief
    assert "  east       -> Court\n" in brief
    assert "south-east" not in brief


def test_spoken_line_adds_a_stop_unless_one_is_there() -> None:
    """The name comes first. Punctuation is added once."""
    assert spoken_line("Ada", "opens the door") == "Ada opens the door.\n"
    assert spoken_line("Ada", "stops!") == "Ada stops!\n"
    assert spoken_line(None, "opens the door") == ""


def test_enter_is_optional_and_rejects_a_number(tmp_path: Path) -> None:
    """A blank enter is absent. A number is not a sentence."""
    path = write(tmp_path, OFFICE.replace("description: A hall.", "description: A hall.\n  enter: '  '\n", 1))
    layout = load_layout(path)
    assert layout.rooms["hall"].enter is None
    path = write(tmp_path, OFFICE.replace("description: A hall.", "description: A hall.\n  enter: 1\n", 1))
    with pytest.raises(ConfigError, match="Room hall enter must be a string"):
        load_layout(path)
    path = write(tmp_path, OFFICE.replace("description: A hall.\n", "description: A hall.\n  enter: |\n    one\n    two\n", 1))
    with pytest.raises(ConfigError, match="Room hall enter must be one line"):
        load_layout(path)


def test_move_lines() -> None:
    """Leave, arrive, full, and blocked lines match the helpers."""
    assert leave_line("Ada", "w", "Kitchen") == "Ada goes west to Kitchen.\n"
    assert spoken_line("Ada", "arrives.") == "Ada arrives.\n"
    assert full_line("Toilet") == "Toilet is full.\n"
    assert cannot_go_line("w") == "You cannot go west.\n"


def test_destination(tmp_path: Path) -> None:
    """Destination returns the room id, or None when that exit is absent."""
    layout = load_layout(write(tmp_path, OFFICE))
    assert layout.destination("reception", "n") == "hall"
    assert layout.destination("reception", "w") is None


def test_layout_has_no_required_room_id(tmp_path: Path) -> None:
    """A rooms file does not need a room named reception."""
    layout = load_layout(write(tmp_path, "hall:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n"))
    assert list(layout.rooms) == ["hall"]


def test_load_layout(tmp_path: Path) -> None:
    """Two linked rooms load, and the display name capitalises the id."""
    layout = load_layout(write(tmp_path, OFFICE))
    assert layout.display_name("reception") == "Reception"
    assert layout.rooms["reception"].exits == {"n": "hall"}
    assert layout.rooms["hall"].max_occupancy == 2


def test_shipped_layout() -> None:
    """config/rooms/system.yml is an example layout. This one is an office."""
    path = Path(__file__).resolve().parents[1] / "config" / "rooms" / "system.yml"
    layout = load_layout(path)
    assert set(layout.rooms) == {
        "reception",
        "kitchen",
        "meeting_room",
        "hallway",
        "mens",
        "womens",
        "open_office",
        "corridor",
        "accounts",
        "human_resources",
        "staff_room",
        "balcony",
        "server_room",
        "plant_room",
    }
    assert layout.rooms["reception"].exits == {
        "n": "open_office",
        "e": "meeting_room",
        "w": "kitchen",
        "s": "hallway",
    }
    assert layout.rooms["hallway"].exits == {"n": "reception", "e": "mens", "w": "womens"}
    assert layout.rooms["mens"].exits == {"w": "hallway"}
    assert layout.rooms["womens"].exits == {"e": "hallway"}
    assert layout.rooms["hallway"].max_occupancy == 4
    assert layout.rooms["mens"].max_occupancy == 1
    assert layout.rooms["womens"].max_occupancy == 1
    assert layout.rooms["server_room"].max_occupancy == 2
    assert layout.rooms["plant_room"].max_occupancy == 2
    assert layout.rooms["kitchen"].max_occupancy == 4
    assert all(room.enter for room in layout.rooms.values())
    assert layout.rooms["server_room"].enter == ("opens the door and enters the server room")
    assert layout.rooms["staff_room"].exits == {
        "s": "kitchen",
        "e": "open_office",
        "w": "balcony",
    }
    assert layout.rooms["kitchen"].exits == {"n": "staff_room", "e": "reception"}
    assert layout.rooms["corridor"].exits == {
        "s": "open_office",
        "e": "accounts",
        "w": "human_resources",
    }
    assert layout.rooms["accounts"].exits == {"w": "corridor"}
    assert layout.rooms["human_resources"].exits == {"e": "corridor"}
    assert layout.rooms["balcony"].exits == {"e": "staff_room"}
    assert layout.rooms["staff_room"].max_occupancy == 4
    assert layout.rooms["meeting_room"].max_occupancy == 6
    assert layout.rooms["reception"].max_occupancy == 8
    assert layout.rooms["open_office"].max_occupancy == 10
    assert layout.display_name("meeting_room") == "Meeting Room"


@pytest.mark.parametrize(("text", "match"), [("- 1\n", "mapping"), ("reception:\n  description: ''\n  max_occupancy: 1\n  exits: {}\n", "description"), ("reception:\n  description: Hi.\n  max_occupancy: true\n  exits: {}\n", "integer"), ("reception:\n  description: Hi.\n  max_occupancy: 0\n  exits: {}\n", "1 or more"), ("reception:\n  description: Hi.\n  max_occupancy: 1\n  exits:\n    up: reception\n", "itself"), ("reception:\n  description: Hi.\n  max_occupancy: 1\n  exits:\n    n: missing\n", "missing"), (("reception:\n  description: Hi.\n  max_occupancy: 1\n  exits:\n    sideways: reception\n"), "sideways"), ("reception:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n  name: Lobby\n", "name"),])  # fmt: skip  # noqa: E501
def test_layout_errors(tmp_path: Path, text: str, match: str) -> None:
    """Bad rooms files name the problem."""
    with pytest.raises(ConfigError, match=match):
        load_layout(write(tmp_path, text))


def test_missing_layout_file(tmp_path: Path) -> None:
    """A missing rooms file raises ConfigError."""
    with pytest.raises(ConfigError, match="Cannot read"):
        load_layout(tmp_path / "missing.yml")


def test_yaml_bool_word_room_id(tmp_path: Path) -> None:
    """Unquoted YAML bool words stay plain room ids."""
    text = """\
reception:
  description: Front desk.
  max_occupancy: 2
  exits:
    n: on
on:
  description: A room named on.
  max_occupancy: 2
  exits:
    s: reception
"""
    layout = load_layout(write(tmp_path, text))
    assert layout.rooms["on"].description == "A room named on."


def test_yaml_bool_word_exit_target(tmp_path: Path) -> None:
    """An unquoted exit target names the room id string."""
    text = """\
reception:
  description: Front desk.
  max_occupancy: 2
  exits:
    n: on
on:
  description: A room named on.
  max_occupancy: 2
  exits:
    s: reception
"""
    layout = load_layout(write(tmp_path, text))
    assert layout.rooms["reception"].exits["n"] == "on"


def test_narrow_office_map_stays_three_by_three() -> None:
    """Eighty columns cannot hold the wide office map."""
    layout = load_layout(Path(__file__).resolve().parents[1] / "config" / "rooms" / "system.yml")
    drawn = layout.map_text("open_office", 80)
    assert "Balcony" not in drawn
    assert "Open Office" in drawn


def test_five_by_five_ne_on_north_row_does_not_raise(tmp_path: Path) -> None:
    """A north-row ne link to the cell south-east does not index past the map."""
    text = """\
hub:
  description: Hub.
  max_occupancy: 2
  exits:
    nw: wing
wing:
  description: Wing.
  max_occupancy: 2
  exits:
    nw: far
    se: hub
far:
  description: Far north west.
  max_occupancy: 2
  exits:
    ne: wing
    se: wing
"""
    layout = load_layout(write(tmp_path, text))
    drawn = layout.map_text("hub", 200)
    assert "Far" in drawn
    assert "Wing" in drawn


def test_wide_office_map_is_five_by_five() -> None:
    """A wide frame shows every room that sits inside the five by five window."""
    layout = load_layout(Path(__file__).resolve().parents[1] / "config" / "rooms" / "system.yml")
    drawn = layout.map_text("open_office", 200)
    assert "Balcony" in drawn
    assert "Hallway" in drawn
    assert "Human Resources" in drawn
    assert "Womens" in drawn
    assert "Mens" in drawn
    reception = layout.map_text("reception", 200)
    assert "Accounts" in reception
    assert "Human Resources" in reception
    assert "Balcony" in reception
    assert "Womens" in reception


def test_map_of_open_office_five_by_five() -> None:
    """At width 200 the shipped office uses the five by five map."""
    layout = load_layout(Path(__file__).resolve().parents[1] / "config" / "rooms" / "system.yml")
    assert layout.map_text("open_office", 200) == ("                      +---------------+     +---------------+     +---------------+\n                      |               |     |               |     |               |\n                      |Human Resources|-----|   Corridor    |-----|   Accounts    |\n                      |               |     |               |     |               |\n                      +---------------+     +---------------+     +---------------+\n                                                    |\n+---------------+     +---------------+     +---------------+     +---------------+\n|               |     |               |     |               |     |               |\n|    Balcony    |-----|  Staff Room   |-----|  Open Office  |-----|  Server Room  |\n|               |     |               |     |               |     |               |\n+---------------+     +---------------+     +---------------+     +---------------+\n                              |                     |\n                      +---------------+     +---------------+     +---------------+\n                      |               |     |               |     |               |\n                      |    Kitchen    |-----|   Reception   |-----| Meeting Room  |\n                      |               |     |               |     |               |\n                      +---------------+     +---------------+     +---------------+\n                                                    |\n                      +---------------+     +---------------+     +---------------+\n                      |               |     |               |     |               |\n                      |    Womens     |-----|    Hallway    |-----|     Mens      |\n                      |               |     |               |     |               |\n                      +---------------+     +---------------+     +---------------+\n")  # fmt: skip  # noqa: E501
    hallway_lines = layout.map_text("hallway", 200).splitlines()
    assert hallway_lines[0] != ""
    assert hallway_lines[-1] != ""


def test_map_of_open_office() -> None:
    """Open Office shows its neighbours and the rooms one corner past Reception."""
    layout = load_layout(Path(__file__).resolve().parents[1] / "config" / "rooms" / "system.yml")
    assert layout.map_text("open_office") == ("+---------------+     +---------------+     +---------------+\n|               |     |               |     |               |\n|Human Resources|-----|   Corridor    |-----|   Accounts    |\n|               |     |               |     |               |\n+---------------+     +---------------+     +---------------+\n                              |\n+---------------+     +---------------+     +---------------+\n|               |     |               |     |               |\n|  Staff Room   |-----|  Open Office  |-----|  Server Room  |\n|               |     |               |     |               |\n+---------------+     +---------------+     +---------------+\n        |                     |\n+---------------+     +---------------+     +---------------+\n|               |     |               |     |               |\n|    Kitchen    |-----|   Reception   |-----| Meeting Room  |\n|               |     |               |     |               |\n+---------------+     +---------------+     +---------------+\n")  # fmt: skip  # noqa: E501


def test_map_of_balcony_three_by_three() -> None:
    """Width 60 stays on the three by three map and shows rooms one cell away."""
    layout = load_layout(Path(__file__).resolve().parents[1] / "config" / "rooms" / "system.yml")
    assert layout.map_text("balcony", 60) == ("                                            +---------------+\n                                            |               |\n                                            |Human Resources|\n                                            |               |\n                                            +---------------+\n\n                      +---------------+     +---------------+\n                      |               |     |               |\n                      |    Balcony    |-----|  Staff Room   |\n                      |               |     |               |\n                      +---------------+     +---------------+\n                                                    |\n                                            +---------------+\n                                            |               |\n                                            |    Kitchen    |\n                                            |               |\n                                            +---------------+\n")  # fmt: skip  # noqa: E501


def test_map_shows_a_corner_reached_through_a_neighbour() -> None:
    """Reception shows the server room east of the open office, with no exit that way."""
    layout = load_layout(Path(__file__).resolve().parents[1] / "config" / "rooms" / "system.yml")
    drawn = layout.map_text("reception")
    assert "|  Staff Room  |-----| Open Office  |-----| Server Room  |" in drawn
    assert "\\" not in drawn
    assert "/" not in drawn


def test_map_draws_up_and_a_diagonal(tmp_path: Path) -> None:
    """Diagonal strokes replace both corners and the middle of the gap."""
    text = """\
reception:
  description: Door.
  max_occupancy: 2
  exits:
    ne: north_east
    nw: north_west
    se: south_east
    sw: south_west
    up: loft
north_east:
  description: NE.
  max_occupancy: 2
  exits: {}
north_west:
  description: NW.
  max_occupancy: 2
  exits: {}
south_east:
  description: SE.
  max_occupancy: 2
  exits: {}
south_west:
  description: SW.
  max_occupancy: 2
  exits: {}
loft:
  description: Loft.
  max_occupancy: 2
  exits:
    down: reception
"""
    layout = load_layout(write(tmp_path, text))
    drawn = layout.map_text("reception", 78)
    lines = drawn.splitlines()
    # Inner width 14. Centre box columns are 21..36. East neighbour starts at 42.
    assert lines[8] == " " * 21 + "|  Reception   |"
    assert lines[6] == " " * 21 + "\\--------------/"
    assert lines[10] == " " * 21 + "/--------------\\"
    assert lines[4][15] == "\\"
    assert lines[4][42] == "/"
    assert lines[5][18] == "\\"
    assert lines[5][39] == "/"
    assert lines[5][29] == " "
    assert lines[11][18] == "/"
    assert lines[11][39] == "\\"
    assert lines[11][29] == " "
    assert lines[12][15] == "/"
    assert lines[12][42] == "\\"
    # South-east: centre corner, gap middle, neighbour corner.
    assert lines[10][36] == "\\"
    assert lines[11][39] == "\\"
    assert lines[12][42] == "\\"
    assert "|-----|" not in drawn
    assert "South East" in drawn
    assert drawn.endswith("Up: Loft\n")
    assert "Down:" not in drawn


def test_a_room_without_max_occupancy_has_no_limit(tmp_path: Path) -> None:
    """Omitting max_occupancy means the room never fills."""
    text = "reception:\n  description: Hi.\n  exits: {}\n"
    layout = load_layout(write(tmp_path, text))
    assert layout.rooms["reception"].max_occupancy is None
    assert is_full(layout, [], "reception") is False


def test_max_occupancy_zero_is_still_an_error(tmp_path: Path) -> None:
    """A present capacity of zero is rejected."""
    text = "kitchen:\n  description: Hi.\n  max_occupancy: 0\n  exits: {}\n"
    with pytest.raises(ConfigError, match="Room kitchen max_occupancy must be 1 or more."):
        load_layout(write(tmp_path, text))


def test_max_occupancy_yaml_true_still_integer_error(tmp_path: Path) -> None:
    """max_occupancy: true is not accepted as a bool or integer."""
    text = """\
reception:
  description: Hi.
  max_occupancy: true
  exits: {}
"""
    with pytest.raises(ConfigError, match="integer"):
        load_layout(write(tmp_path, text))
