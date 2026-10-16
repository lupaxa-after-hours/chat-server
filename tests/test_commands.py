"""Protocol text with no sockets."""

from __future__ import annotations  # noqa: I001

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from types import SimpleNamespace
import threading

import psutil

import pytest
from totp_fixtures import TOTP_TEST_SEED

import lupaxa.chat_server.admin as admin_api
import lupaxa.chat_server.reboot as reboot_api
import lupaxa.chat_server.broadcast as broadcast_api
import lupaxa.chat_server.commands as command_api
import lupaxa.chat_server.entry as entry_api
import lupaxa.chat_server.login as login_api
import lupaxa.chat_server.mfa as mfa_api
import lupaxa.chat_server.newbie as newbie_api
import lupaxa.chat_server.password as password_api
import lupaxa.chat_server.utilities as utilities_api
import lupaxa.chat_server.resident as resident_api
import lupaxa.chat_server.rooms as rooms_api
import lupaxa.chat_server.coder as coder_api
import lupaxa.chat_server.su as su_api
import lupaxa.chat_server.timer as timer_api
from lupaxa.chat_server.admin import command_allow, command_demote, command_promote
from lupaxa.chat_server.client import output_hidden
from lupaxa.chat_server.colour import center_block, cls, cyan, green, paint_admin, paint_coder, paint_staff, plain_len, red, yellow
from lupaxa.chat_server.commands import Command, by_name, command_words, subcommand_words
from lupaxa.chat_server.config import Config
from lupaxa.chat_server.entry import enter, enter_fresh
from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.globals import NAME_PROMPT, RESERVED_NAMES
from lupaxa.chat_server.help import HelpBook, HelpEntry, HelpStamp, help_stamp, load_help
from lupaxa.chat_server.login import name_prompt, process_input, process_login, refusal_text, welcome_text
from lupaxa.chat_server.netstats import NetStats
from lupaxa.chat_server.newbie import command_remote, command_tell
from lupaxa.chat_server.parse import consider_name, parse_direction, process_commands
from lupaxa.chat_server.password import confirm_password_prompt
from lupaxa.chat_server.player import Player, match_name, multiple_matches_message
from lupaxa.chat_server.resident import command_save
from lupaxa.chat_server.roles import Roles
from lupaxa.chat_server.rooms import Layout, Room, house_room_id, load_house, load_layout, write_default_house
from lupaxa.chat_server.saved_player import SavedPlayer, delete_player, hash_password, load_saved, password_ok, player_path, record_saved_logout, save_player  # fmt: skip  # noqa: E501
from lupaxa.chat_server.su import command_disconnect, command_nuke, command_resident, command_warn, connections
from lupaxa.chat_server.time import clock_phrase, cpu_phrase, duration_phrase, idle_clock
from lupaxa.chat_server.chatserver import ChatServer
from lupaxa.chat_server.timer import apply_net_tick, consume_seconds, message_file_stamps, note_connected_time, process_timer, reload_messages
from lupaxa.chat_server.utilities import examine_frame, frame_width, list_frame, time_frame, wrap_words
from lupaxa.chat_server.version import get_version
from lupaxa.chat_server.ranks import Rank, rank_allows, shown_rank

SHIPPED_HELP = load_help(Path(__file__).resolve().parents[1] / "config" / "help.yml")

ROOM_SENTENCES = (("Enter message", "arrives."), ("Login message", "has connected."), ("Logout message", "has disconnected."))
LOCAL_FROM = "127.0.0.1 (127.0.0.1) [via telnet]"


class FakeClientMap:
    """People on the server, so a scan can find that server from ``values()``."""

    def __init__(self, server: FakeServer) -> None:
        self.server = server

    def values(self) -> FakeClientMap:
        """Return this map. Iteration yields the server's people."""
        return self

    def __iter__(self) -> Iterator[FakePlayer]:
        return iter(self.server.people)


@dataclass
class FakePlayer:
    name: str | None = "Ada"
    room: str | None = "reception"
    player: Player | None = field(default_factory=lambda: Player("Ada", "reception", datetime.now(timezone.utc)))
    auth: str = "name"
    pending_name: str = ""
    pending_saved: SavedPlayer | None = None
    pending_password: str = ""
    pending_current_password: str = ""
    pending_mfa_secret: str = ""
    pending_issuer: str = ""
    password_failures: int = 0
    address: str = "127.0.0.1"
    hostname: str = ""
    country_name: str = ""
    country_code: str = ""
    ssh: bool = False
    accepted_at: float = 0.0
    discovered_columns: int | None = 72
    discovered_rows: int | None = 24
    frame_columns: int | None = None
    sent: list[str] = field(default_factory=list)
    net: NetStats = field(default_factory=NetStats)
    show_lock: bool = False

    def __post_init__(self) -> None:
        if self.hostname == "":
            self.hostname = self.address
        if self.player is None:
            return
        if self.name == "Ada" and self.room == "reception":
            self.name = self.player.name
            self.room = self.player.room
            return
        if self.name is not None:
            self.player.name = self.name
            self.player.lowercase_name = self.name.lower()
        if self.room is not None:
            self.player.room = self.room


@dataclass
class FakeServer:
    sent: list[str] = field(default_factory=list)
    room_lines: list[str] = field(default_factory=list)
    replaced: list[FakePlayer] = field(default_factory=list)
    dropped: bool = False
    stopped: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rebooted_at: datetime | None = None
    process: object | None = None
    timer_mark: float | None = None
    timer_remainder: float = 0.0
    net_count: int = 10
    telnet_net: NetStats = field(default_factory=NetStats)
    ssh_net: NetStats = field(default_factory=NetStats)
    combined_net: NetStats = field(default_factory=NetStats)
    shutdown_seconds: int | None = None
    shutdown_reason: str = ""
    allow_newbies: bool = True
    allow_residents: bool = True
    full_message: str = ""
    newbie_message: str = ""
    mfa_enabled: bool = False
    country_lookup: bool = False
    mfa_steps: dict[str, int] = field(default_factory=dict)
    dropped_clients: list[FakePlayer] = field(default_factory=list)
    flushed: list[FakePlayer] = field(default_factory=list)
    announced: list[bool] = field(default_factory=list)
    staff_lines: list[str] = field(default_factory=list)
    admin_lines: list[str] = field(default_factory=list)
    coder_lines: list[str] = field(default_factory=list)
    occupied: int = 0
    chains: list = field(default_factory=list)
    people: list[FakePlayer] = field(default_factory=list)
    all_lines: list[str] = field(default_factory=list)
    newbies: list[tuple[str, str, str, int]] = field(default_factory=list)
    layout: Layout = field(default_factory=lambda: Layout(rooms={"reception": Room("The desk.", 4, {"nw": "hall"}), "hall": Room("A hall.", 4, {}),}))  # fmt: skip  # noqa: E501
    config: Config = field(default_factory=lambda: Config(port=1, rooms=Path("rooms.yml"), start="reception", help=Path("help.yml"), players=Path("players"), mfa_enabled=False))  # fmt: skip  # noqa: E501
    help: HelpBook = field(default_factory=lambda: HelpBook(entries=()))
    help_stamp: HelpStamp = field(default_factory=lambda: HelpStamp(-1, -1))
    help_failed: HelpStamp | None = None
    rooms_stamp: HelpStamp = field(default_factory=lambda: HelpStamp(-1, -1))
    rooms_failed: HelpStamp | None = None
    house_stamps: dict[str, HelpStamp] = field(default_factory=dict)
    house_failed: dict[str, HelpStamp] = field(default_factory=dict)
    banner: str = ""
    message_stamps: dict[str, HelpStamp] = field(default_factory=lambda: {"banner": HelpStamp(-1, -1), "full": HelpStamp(-1, -1), "no-more-newbies": HelpStamp(-1, -1)})
    message_failed: dict[str, HelpStamp] = field(default_factory=dict)
    help_check_seconds: int = 0
    roster_dir: Path = Path("players")
    client_map: dict[str, FakePlayer] | None = None
    hot_stamp: HelpStamp = field(default_factory=lambda: HelpStamp(-1, -1))
    hot_failed: HelpStamp | None = None
    country_db: object | None = None
    country_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def clients(self) -> dict[str, FakePlayer]:
        if self.client_map is None:
            return FakeClientMap(self)  # type: ignore[return-value]
        return self.client_map

    @clients.setter
    def clients(self, value: dict[str, FakePlayer]) -> None:
        self.client_map = value

    def send(self, client: FakePlayer, text: str) -> bool:
        if output_hidden(client):
            return True
        self.sent.append(text)
        client.sent.append(text)
        return True

    def broadcast_room(self, room_id: str, text: str, *, skip: FakePlayer | None) -> None:
        self.room_lines.append(text)
        for person in self.people:
            if person.room != room_id or person.name is None:
                continue
            if skip is not None and person is skip:
                continue
            self.send(person, text)

    def broadcast_room_split(self, room_id: str, plain: str, staff: str, *, skip: FakePlayer | None) -> None:
        del room_id, staff, skip
        self.room_lines.append(plain)

    def drop(self, client: FakePlayer, *, announce: bool, skip_chain_departure: bool = False) -> None:
        del skip_chain_departure
        self.dropped = True
        self.dropped_clients.append(client)
        self.announced.append(announce)

    def flush(self, client: FakePlayer) -> bool:
        self.flushed.append(client)
        return True

    def broadcast_staff(self, text: str, *, skip: FakePlayer | None = None) -> None:
        del skip
        self.staff_lines.append(text)

    def broadcast_admin(self, text: str, *, skip: FakePlayer | None = None) -> None:
        del skip
        self.admin_lines.append(text)

    def broadcast_coder(self, text: str, *, skip: FakePlayer | None = None) -> None:
        del skip
        self.coder_lines.append(text)

    def occupancy(self, room_id: str) -> int:
        del room_id
        return self.occupied

    def broadcast_all(self, text: str, *, skip: FakePlayer | None) -> None:
        del skip
        self.all_lines.append(text)

    def shutdown(self) -> None:
        self.stopped = True

    def is_stopping(self) -> bool:
        return self.stopped

    def find_player(self, name: str) -> FakePlayer | None:
        key = name.lower()
        for person in self.people:
            player = person.player
            if player is None or person.room is None:
                continue
            if player.lowercase_name == key:
                return person
        return None

    def idlers(self) -> list[tuple[str, int]]:
        rows = [(person.player.name, person.player.idle_seconds) for person in self.people if person.player is not None]
        return sorted(rows)

    def who(self) -> list[tuple[str, str, str, str, int]]:
        rows = [(person.name, person.player.rank, person.room, person.player.status, person.player.idle_seconds) for person in self.people if person.name is not None and person.room is not None and person.player is not None]
        return sorted(rows)

    def others(self, room_id: str, client: FakePlayer) -> list[str]:
        del room_id, client
        return []

    def taken_names(self) -> set[str]:
        return {person.name for person in self.people if person.name is not None and person not in self.dropped_clients}

    def unsaved_names(self, skip: FakePlayer | None = None) -> set[str]:
        claimed: set[str] = set()
        for person in self.people:
            if person is skip or person in self.dropped_clients:
                continue
            user = person.player
            if user is not None and user.rank == Rank.NEWBIE:
                claimed.add(user.name.lower())
            elif user is None and person.auth in ("set", "confirm", "agree") and person.pending_name:
                claimed.add(person.pending_name.lower())
        return claimed

    def replace_socket(self, live: FakePlayer, newcomer: FakePlayer) -> None:
        self.replaced.append(live)
        live.address = newcomer.address
        live.hostname = newcomer.hostname


def server_from_clients(clients: object) -> FakeServer | None:
    server = getattr(clients, "server", None)
    if isinstance(server, FakeServer):
        return server
    return None


@pytest.fixture(autouse=True)
def route_scans_to_the_host_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record scans on ``FakeServer`` and restore the channel functions after each test."""

    def broadcast_room(server: object, room_id: str | None, text: str, *, skip: object) -> None:
        if isinstance(server, FakeServer):
            server.broadcast_room(room_id, text, skip=skip)  # type: ignore[arg-type]
            return
        broadcast_api.broadcast_room(server, room_id, text, skip=skip)  # type: ignore[arg-type]

    def broadcast_room_split(server: object, room_id: str | None, plain: str, staff: str, *, skip: object) -> None:
        if isinstance(server, FakeServer):
            server.broadcast_room_split(room_id, plain, staff, skip=skip)  # type: ignore[arg-type]
            return
        broadcast_api.broadcast_room_split(server, room_id, plain, staff, skip=skip)  # type: ignore[arg-type]

    def broadcast_all(server: object, text: str, *, skip: object) -> None:
        if isinstance(server, FakeServer):
            server.broadcast_all(text, skip=skip)  # type: ignore[arg-type]
            return
        broadcast_api.broadcast_all(server, text, skip=skip)  # type: ignore[arg-type]

    def broadcast_shout(server: object, line: str, *, skip: object) -> None:
        if isinstance(server, FakeServer):
            painted = yellow(line) + "\n"
            for person in server.people:
                if skip is not None and person is skip:
                    continue
                if person.name is None or person.room is None:
                    continue
                user = person.player
                if user is None or not user.colour:
                    painted = line + "\n"
                break
            server.broadcast_all(painted, skip=skip)  # type: ignore[arg-type]
            for person in server.people:
                if skip is not None and person is skip:
                    continue
                if person.name is None or person.room is None:
                    continue
                user = person.player
                if user is not None and user.colour:
                    server.send(person, yellow(line) + "\n")
                else:
                    server.send(person, line + "\n")
            return
        broadcast_api.broadcast_shout(server, line, skip=skip)  # type: ignore[arg-type]

    def broadcast_staff(server: object, text: str, *, skip: object = None) -> None:
        if isinstance(server, FakeServer):
            for person in server.people:
                user = person.player
                if user is None or not rank_allows(user.rank, Rank.SU_CHANNEL) or user.off_duty:
                    continue
                if skip is not None and person is skip:
                    continue
                painted = paint_staff(person, text)
                server.broadcast_staff(painted, skip=skip)  # type: ignore[arg-type]
                if not output_hidden(person):
                    person.sent.append(painted)
            return
        broadcast_api.broadcast_staff(server, text, skip=skip)  # type: ignore[arg-type]

    def broadcast_admin(server: object, text: str, *, skip: object = None) -> None:
        if isinstance(server, FakeServer):
            for person in server.people:
                user = person.player
                if user is None or not rank_allows(user.rank, Rank.ADMIN) or user.off_duty:
                    continue
                if skip is not None and person is skip:
                    continue
                server.broadcast_admin(paint_admin(person, text), skip=skip)  # type: ignore[attr-defined]
            return
        broadcast_api.broadcast_admin(server, text, skip=skip)  # type: ignore[arg-type]

    def broadcast_coder(server: object, text: str, *, skip: object = None) -> None:
        if isinstance(server, FakeServer):
            for person in server.people:
                user = person.player
                if user is None or not rank_allows(user.rank, Rank.CODER):
                    continue
                if skip is not None and person is skip:
                    continue
                painted = paint_coder(person, text)
                server.broadcast_coder(painted, skip=skip)  # type: ignore[attr-defined]
                if not output_hidden(person):
                    person.sent.append(painted)
            return
        broadcast_api.broadcast_coder(server, text, skip=skip)  # type: ignore[attr-defined]

    real_occupancy = rooms_api.occupancy

    def occupancy(clients: object, room_id: str) -> int:
        server = server_from_clients(clients)
        if server is not None:
            return server.occupancy(room_id)
        return real_occupancy(clients, room_id)  # type: ignore[arg-type]

    def others(clients: object, room_id: str, skip: object) -> list[str]:
        server = server_from_clients(clients)
        if server is not None:
            return server.others(room_id, skip)  # type: ignore[arg-type]
        return rooms_api.others(clients, room_id, skip)  # type: ignore[arg-type]

    listed_who = newbie_api.who
    listed_idlers = newbie_api.idlers
    listed_newbies = su_api.newbies

    def who(clients: object) -> list[tuple[str, str, str, str, int]]:
        server = server_from_clients(clients)
        if server is not None:
            return server.who()
        return listed_who(clients)  # type: ignore[arg-type]

    def idlers(clients: object) -> list[tuple[str, int]]:
        server = server_from_clients(clients)
        if server is not None:
            return server.idlers()
        return listed_idlers(clients)  # type: ignore[arg-type]

    def newbies(clients: object) -> list[tuple[str, str, str, int]]:
        server = server_from_clients(clients)
        if server is not None:
            return list(server.newbies)
        return listed_newbies(clients)  # type: ignore[arg-type]

    def find_player(server: object, client: object, name: str, *, quiet: bool = False) -> object:
        if isinstance(server, FakeServer):
            found = server.find_player(name)
            if found is None and not quiet:
                server.send(client, f"No such player '{name}'.\n")  # type: ignore[arg-type]
            return found
        return utilities_api.find_player(server, client, name, quiet=quiet)  # type: ignore[arg-type]

    replacements = {
        "broadcast_room": broadcast_room,
        "broadcast_room_split": broadcast_room_split,
        "broadcast_all": broadcast_all,
        "broadcast_shout": broadcast_shout,
        "broadcast_staff": broadcast_staff,
        "broadcast_admin": broadcast_admin,
        "broadcast_coder": broadcast_coder,
        "occupancy": occupancy,
        "others": others,
        "who": who,
        "idlers": idlers,
        "newbies": newbies,
        "find_player": find_player,
    }
    for module in (newbie_api, resident_api, command_api, admin_api, reboot_api, su_api, coder_api, login_api, password_api, entry_api, mfa_api):
        for name, func in replacements.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, func)
    monkeypatch.setattr(rooms_api, "occupancy", occupancy)
    monkeypatch.setattr(timer_api, "broadcast_all", broadcast_all)
    monkeypatch.setattr(timer_api, "broadcast_coder", broadcast_coder)


def run(line: str, player: FakePlayer | None = None) -> tuple[FakeServer, FakePlayer]:
    current = player or FakePlayer()
    server = FakeServer(people=[current], help=SHIPPED_HELP)
    process_commands(server, current, line)  # type: ignore[arg-type]
    return server, current


def test_welcome_centers_the_banner() -> None:
    """Connect shows the banner, two blank lines, and no version."""
    banner = "+---+\n| Hi |\n+---+\n"
    text = welcome_text(banner, 11)
    assert "Version" not in text
    assert "Chat Server" not in text
    assert text.startswith("\n\n")
    assert text.endswith("\n\nPlease enter your name: ")
    assert "  | Hi |" in text
    assert not text.endswith("\n")


def test_blank_name_is_ignored() -> None:
    """Whitespace does not repeat the prompt."""
    decision = consider_name("   ", set())
    assert decision.name is None
    assert decision.reply == ""


def test_me_cannot_be_a_name(tmp_path: Path) -> None:
    """Me in any case is refused, and no player file is opened."""
    for typed in ("me", "Me", "ME", " me "):
        decision = consider_name(typed, {"me"})
        assert decision.name is None
        assert decision.reply == (f"{typed.strip()} cannot be used. Pick another name.\n\n{name_prompt()}")

    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "ME")  # type: ignore[arg-type]
    assert player.player is None
    assert server.sent == ["ME cannot be used. Pick another name.\n\nPlease enter your name: "]
    assert load_saved(player_path(tmp_path, "me")) is None
    assert not (tmp_path / "me.yml").exists()


def test_who_and_quit_cannot_be_names() -> None:
    """Who and quit are reserved in any case, like me."""
    for typed in ("who", "Who", "WHO", "quit", "Quit", "QUIT"):
        decision = consider_name(typed, set())
        assert decision.name is None
        assert decision.reply == (f"{typed} cannot be used. Pick another name.\n\nPlease enter your name: ")


def test_who_at_the_name_prompt_lists_players(tmp_path: Path) -> None:
    """Who shows the connected frame and asks for a name again."""
    server = login_host(tmp_path)
    when = datetime.now(timezone.utc)
    server.people = [FakePlayer(name="Ada", room="reception", player=Player("Ada", "reception", when, idle_seconds=10))]
    asker = FakePlayer(player=None)
    process_input(server, asker, "WHO")  # type: ignore[arg-type]
    assert asker.player is None
    assert asker.auth == "name"
    assert server.sent[0].splitlines()[0] == "-" * 72
    assert "Ada" in server.sent[0]
    assert server.sent[-1] == "Please enter your name: "


def test_quit_at_the_name_prompt_disconnects(tmp_path: Path) -> None:
    """Quit says goodbye and drops the connection."""
    server = login_host(tmp_path)
    asker = FakePlayer(player=None)
    process_input(server, asker, "Quit")  # type: ignore[arg-type]
    assert server.sent == ["Goodbye!\n"]
    assert server.dropped_clients == [asker]
    assert asker.player is None


def test_who_please_is_not_the_login_list(tmp_path: Path) -> None:
    """A space keeps the letters-and-digits refusal."""
    server = login_host(tmp_path)
    asker = FakePlayer(player=None)
    process_input(server, asker, "who please")  # type: ignore[arg-type]
    assert asker.player is None
    assert server.sent == ["A name can use only letters and digits.\n\nPlease enter your name: "]
    assert server.dropped_clients == []


def test_a_name_must_be_three_to_twenty_letters_or_digits() -> None:
    """Illegal names are refused in order, and the prompt comes back."""
    letters = consider_name("Bea Smith", set())
    assert letters.reply == ("A name can use only letters and digits.\n\nPlease enter your name: ")
    leading = consider_name("1Ada", set())
    assert leading.reply == ("A name cannot start with a number.\n\nPlease enter your name: ")
    short = consider_name("Ab", set())
    assert short.reply == ("A name must be at least 3 characters.\n\nPlease enter a longer name: ")
    teen = consider_name("12", set())
    assert teen.reply == ("A name cannot start with a number.\n\nPlease enter your name: ")
    long = consider_name("A" * 21, set())
    assert long.reply == ("A name can be at most 20 characters.\n\nPlease enter a shorter name: ")
    assert consider_name("Ada", set()).name == "Ada"
    assert consider_name("A" * 20, set()).name == "A" * 20
    assert consider_name("Ada1", set()).name == "Ada1"


def test_duplicate_name() -> None:
    """A taken name keeps the player unnamed and asks again."""
    decision = consider_name("Ada", {"Ada"})
    assert decision.name is None
    assert decision.reply == ("Ada is already in use. Pick another name.\n\nPlease enter your name: ")


def test_say_emote_and_think() -> None:
    """Room lines and the sender's copy use the spec wording."""
    say, _ = run("say hello there")
    assert say.sent == ["You say 'hello there'\n"]
    assert say.room_lines == ["- Ada says 'hello there'\n"]

    emote, _ = run(":waves")
    assert emote.sent == ["You emote: waves\n"]
    assert emote.room_lines == ["- Ada waves\n"]

    think, _ = run("~ lunch")
    assert think.sent == ["You think . o O ( lunch )\n"]
    assert think.room_lines == ["- Ada thinks . o O ( lunch )\n"]


def test_alias_does_not_need_a_space() -> None:
    """An alias is a command even when the text follows immediately."""
    server, _ = run("'hello there")
    assert server.sent == ["You say 'hello there'\n"]
    assert server.room_lines == ["- Ada says 'hello there'\n"]


def test_missing_message_uses_the_canonical_name() -> None:
    """A command with syntax, and no text, shows that syntax."""
    server, _ = run("say")
    assert server.sent == ["Syntax: say <message>\n"]
    assert server.room_lines == []

    server, _ = run("'")
    assert server.sent == ["Syntax: '<message>\n"]
    assert server.dropped is False

    server, _ = run("emote")
    assert server.sent == ["Syntax: emote <message>\n"]

    server, _ = run("think")
    assert server.sent == ["Syntax: think <message>\n"]


def test_unknown_command() -> None:
    """An unknown word is quoted back to the sender."""
    server, _ = run("dance")
    assert server.sent == ["No such command 'dance'\n"]
    assert server.room_lines == []


def test_glued_command_name_is_unknown() -> None:
    """A command name still needs a space before its text."""
    server, _ = run("sayhello")
    assert server.sent == ["No such command 'sayhello'\n"]


def test_a_command_word_matches_in_any_case() -> None:
    """VERSION and L are the same commands as version and look."""
    version_result, _ = run("VERSION")
    assert version_result.sent == [f"The current version is: {get_version()}\n"]
    looked, _ = run("L")
    assert "The desk." in looked.sent[0]
    glued, _ = run("SAYhello")
    assert glued.sent == ["No such command 'SAYhello'\n"]


def test_help_version_and_commands() -> None:
    """Local replies do not go to the room."""
    help_result, _ = run("help")
    assert help_result.sent == ["Syntax: help <topic>\n"]
    assert help_result.room_lines == []

    version_result, _ = run("version")
    assert version_result.sent == [f"The current version is: {get_version()}\n"]

    resident = FakePlayer(player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    listed, _ = run("commands", resident)
    words = command_words(Rank.RESIDENT).split()
    width = frame_width(resident.discovered_columns, resident.frame_columns)
    wrapped = wrap_words(words, width)
    assert listed.sent == [f"Commands available:\n{wrapped}\n"]
    assert command_words(Rank.RESIDENT) == ("! \" ' , . : ; < > ? ` ~ alarm chains cls color colour commands emote examine go help home join l look lsu main map password quit recap remote retire roles rthink save say screenlock set shout staff tell term_width testcard think time version w who x")  # fmt: skip  # noqa: E501


def test_commands_a_filters_the_visible_list() -> None:
    """A letter keeps the visible names that start with it, in either case."""
    resident = staff("Ada", Rank.RESIDENT)
    resident.frame_columns = 200
    lower, _ = run("commands a", resident)
    upper, _ = run("commands A", resident)
    assert lower.sent == upper.sent
    body = lower.sent[0]
    assert body.startswith("Commands available:\n")
    assert "alarm" in body
    assert "!" not in body


def test_commands_z_says_nothing_starts_with_that_letter() -> None:
    """A letter with no names replaces the header."""
    resident = staff("Ada", Rank.RESIDENT)
    upper, _ = run("commands Z", resident)
    assert upper.sent == ["No commands found beginning with 'Z'.\n"]
    lower, _ = run("commands z", resident)
    assert lower.sent == ["No commands found beginning with 'z'.\n"]


def test_commands_aliases_lists_the_visible_aliases() -> None:
    """Aliases keeps punctuation marks first and leaves real names out."""
    resident = staff("Ada", Rank.RESIDENT)
    assert resident.player is not None
    resident.player.colour = False
    resident.frame_columns = 200
    server, _ = run("commands Aliases", resident)
    words = server.sent[0].split("Commands available:\n", 1)[1].split()
    assert "!" in words
    assert "x" in words
    assert "say" not in words


def test_commands_su_lists_channel_commands_for_su_channel() -> None:
    """SU Channel sees the channel commands and not warn. A super user sees both."""
    ada = staff("Ada", Rank.SU_CHANNEL)
    assert ada.player is not None
    ada.player.colour = False
    ada.frame_columns = 200
    server, _ = run("commands su", ada)
    words = server.sent[0].split("Commands available:\n", 1)[1].split()
    assert "su" in words
    assert "lsn" in words
    assert "connections" in words
    assert "conns" in words
    assert "off_duty" in words
    assert "warn" not in words
    assert "grab" not in words
    boss = staff("Bea", Rank.SUPER_USER)
    assert boss.player is not None
    boss.player.colour = False
    boss.frame_columns = 200
    listed, _ = run("commands su", boss)
    staff_words = listed.sent[0].split("Commands available:\n", 1)[1].split()
    assert "su" in staff_words
    assert "warn" in staff_words
    assert "shutdown" not in staff_words


def test_commands_su_lists_super_user_commands_while_off_duty() -> None:
    """An off-duty super user still gets the super-user names."""
    boss = staff("Ada", Rank.SUPER_USER)
    assert boss.player is not None
    boss.player.off_duty = True
    boss.player.colour = False
    boss.frame_columns = 200
    server, _ = run("commands SU", boss)
    assert len(server.sent) == 1
    words = server.sent[0].split("Commands available:\n", 1)[1].split()
    assert "grab" in words
    assert "conns" in words
    assert "say" not in words
    assert "mfa" not in words
    assert "You are off duty." not in server.sent[0]


def test_commands_admin_lists_admin_commands() -> None:
    """Admin names stay in the admin list, and ipinfo stays out while lookup is off."""
    boss = staff("Ada", Rank.ADMIN)
    assert boss.player is not None
    boss.player.colour = False
    boss.frame_columns = 200
    server, _ = run("commands admin", boss)
    words = server.sent[0].split("Commands available:\n", 1)[1].split()
    assert "allow" in words
    assert "shutdown" not in words
    assert "grab" not in words
    assert "ipinfo" not in words


def test_commands_syntax_follows_the_player_rank() -> None:
    """The syntax line names only the filters that rank may use."""
    resident = staff("Ada", Rank.RESIDENT)
    for line in ("commands su", "commands su a", "commands ab", "commands a please", "commands 1", "commands as"):
        server, _ = run(line, resident)
        assert server.sent == ["Syntax: commands [<letter>|aliases]\n"]
    channel = staff("Ada", Rank.SU_CHANNEL)
    server, _ = run("commands admin", channel)
    assert server.sent == ["Syntax: commands [<letter>|aliases|su]\n"]
    super_user = staff("Ada", Rank.SUPER_USER)
    server, _ = run("commands admin", super_user)
    assert server.sent == ["Syntax: commands [<letter>|aliases|su]\n"]
    coder = staff("Ada", Rank.CODER)
    server, _ = run("commands foo", coder)
    assert server.sent == ["Syntax: commands [<letter>|aliases|su|admin|coder]\n"]
    chief = staff("Ada", Rank.SUPER_ADMIN)
    server, _ = run("commands foo", chief)
    assert server.sent == ["Syntax: commands [<letter>|aliases|su|admin|coder]\n"]
    admin = staff("Ada", Rank.ADMIN)
    server, _ = run("commands coder", admin)
    assert server.sent == ["Syntax: commands [<letter>|aliases|su|admin]\n"]
    server, _ = run("commands foo", admin)
    assert server.sent == ["Syntax: commands [<letter>|aliases|su|admin]\n"]


def test_commands_coder_lists_the_channel_and_the_coder_commands() -> None:
    """Commands coder lists the channel and the coder commands, and leaves warn and promote out."""
    ada = staff("Ada", Rank.CODER)
    ada.frame_columns = 240
    server, _ = run("commands coder", ada)
    text = server.sent[0]
    for name in ("cu", "ce", "ct", "broadcast", "network", "reboot", "reload", "shutdown", "system"):
        assert name in text
    assert "warn" not in text
    assert "promote" not in text


def test_commands_gap_names_an_empty_coder_list() -> None:
    """An empty coder filter says there are no Coder commands."""
    from lupaxa.chat_server.newbie import commands_gap

    assert commands_gap("coder") == "There are no Coder commands."


def test_commands_gap_names_an_empty_role_list() -> None:
    """An empty role filter uses the display label."""
    from lupaxa.chat_server.newbie import commands_gap

    assert commands_gap("site_admin") == "There are no Site Admin commands."
    assert commands_gap("SITE_ADMIN") == "There are no Site Admin commands."


def test_commands_site_admin_lists_the_granted_commands() -> None:
    """A holder sees every command that role grants, coloured by that command's rank."""
    from lupaxa.chat_server.colour import magenta
    from lupaxa.chat_server.roles import Roles

    ada = staff("Ada", Rank.RESIDENT)
    assert ada.player is not None
    ada.player.roles = int(Roles.SITE_ADMIN)
    ada.frame_columns = 240
    server, _ = run("commands site_admin", ada)
    text = server.sent[0]
    assert text.startswith("Commands available:\n")
    for name in ("broadcast", "network", "reboot", "reload", "shutdown", "system"):
        assert name in text
    assert magenta("cu") not in text
    assert "warn" not in text
    assert magenta("shutdown") in text
    plain = staff("Bea", Rank.RESIDENT)
    assert plain.player is not None
    plain.player.roles = int(Roles.SITE_ADMIN)
    plain.player.colour = False
    plain.frame_columns = 240
    off, _ = run("commands SITE_ADMIN", plain)
    assert "shutdown" in off.sent[0]
    assert "cu" not in off.sent[0].split()
    assert magenta("shutdown") not in off.sent[0]


def test_commands_site_admin_works_while_off_duty() -> None:
    """Off duty does not close the role filter."""
    from lupaxa.chat_server.roles import Roles

    ada = staff("Ada", Rank.RESIDENT)
    assert ada.player is not None
    ada.player.roles = int(Roles.SITE_ADMIN)
    ada.player.off_duty = True
    ada.frame_columns = 240
    server, _ = run("commands site_admin", ada)
    assert server.sent[0].startswith("Commands available:\n")
    assert "shutdown" in server.sent[0]
    assert "You are off duty." not in server.sent[0]


def test_commands_site_admin_is_refused_without_the_role() -> None:
    """A player who does not hold the role gets the syntax line, without the token."""
    from lupaxa.chat_server.roles import Roles

    resident = staff("Ada", Rank.RESIDENT)
    server, _ = run("commands site_admin", resident)
    assert server.sent == ["Syntax: commands [<letter>|aliases]\n"]
    holder = staff("Bea", Rank.RESIDENT)
    assert holder.player is not None
    holder.player.roles = int(Roles.SITE_ADMIN)
    held, _ = run("commands foo", holder)
    assert held.sent == ["Syntax: commands [<letter>|aliases|site_admin]\n"]
    coder = staff("Cyd", Rank.CODER)
    assert coder.player is not None
    coder.player.roles = int(Roles.SITE_ADMIN)
    both, _ = run("commands foo", coder)
    assert both.sent == ["Syntax: commands [<letter>|aliases|su|admin|coder|site_admin]\n"]


def test_coder_command_names_are_magenta() -> None:
    """A coder command name is magenta. A super-user command name stays cyan."""
    from lupaxa.chat_server.colour import cyan, magenta

    boss = staff("Ada", Rank.SUPER_ADMIN)
    boss.frame_columns = 240
    server, _ = run("commands", boss)
    assert magenta("cu") in server.sent[0]
    assert cyan("su") in server.sent[0]


def test_commands_su_keeps_rank_colour() -> None:
    """A filtered super-user name stays cyan, an admin name stays red, and a coder name is magenta."""
    from lupaxa.chat_server.colour import cyan, magenta, red

    boss = staff("Ada", Rank.ADMIN)
    boss.frame_columns = 200
    server, _ = run("commands su", boss)
    assert cyan("grab") in server.sent[0]
    assert cyan("su") in server.sent[0]
    listed, _ = run("commands admin", boss)
    assert red("allow") in listed.sent[0]
    coder = staff("Bea", Rank.CODER)
    coder.frame_columns = 240
    coloured, _ = run("commands coder", coder)
    assert magenta("shutdown") in coloured.sent[0]


def test_commands_gap_names_the_empty_filter() -> None:
    """Each empty filter has its own sentence, and a letter keeps its case."""
    from lupaxa.chat_server.newbie import commands_gap

    assert commands_gap("Z") == "No commands found beginning with 'Z'."
    assert commands_gap("z") == "No commands found beginning with 'z'."
    assert commands_gap("aliases") == "There are no aliases."
    assert commands_gap("su") == "There are no Super User commands."
    assert commands_gap("SU") == "There are no Super User commands."
    assert commands_gap("admin") == "There are no Admin commands."
    assert commands_gap("ADMIN") == "There are no Admin commands."


def test_recap_changes_only_the_capitalisation(tmp_path: Path) -> None:
    """BoB updates the live name and the file. The file stays bob.yml."""
    save_player(tmp_path, SavedPlayer("Bob", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    user = Player("Bob", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    bob = FakePlayer(name="Bob", room="hall", player=user)
    server.people = [bob]
    process_commands(server, bob, "recap BoB")  # type: ignore[arg-type]
    assert server.sent == ["Name set to BoB.\n"]
    assert user.name == "BoB"
    assert user.lowercase_name == "bob"
    assert server.room_lines == []
    saved = load_saved(player_path(tmp_path, "bob"))
    assert saved is not None
    assert saved.name == "BoB"
    assert player_path(tmp_path, "bob").is_file()
    server.sent.clear()
    process_commands(server, bob, "recap Robert")  # type: ignore[arg-type]
    assert server.sent == ["You can only change the capitalisation of your name.\n"]
    assert user.name == "BoB"


def test_recap_cannot_rename_someone_else(tmp_path: Path) -> None:
    """Ada cannot recap Bea. A bare recap shows the syntax."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.RESIDENT)
    bea = newbie("Bea")
    server.people = [ada, bea]
    process_commands(server, ada, "recap Bea")  # type: ignore[arg-type]
    assert server.sent == ["You can only change the capitalisation of your name.\n"]
    assert bea.player is not None
    assert bea.player.name == "Bea"
    server.sent.clear()
    process_commands(server, ada, "recap")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: recap <name>\n"]


def test_recap_of_the_current_spelling_succeeds() -> None:
    """Typing the spelling already in use is still a success."""
    resident = FakePlayer(player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    ada, _ = run("recap Ada", resident)
    assert ada.sent == ["Name set to Ada.\n"]


def test_recap_keeps_the_live_name_when_the_save_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed save leaves the display name the player already has."""
    save_player(tmp_path, SavedPlayer("Bob", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    user = Player("Bob", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    bob = FakePlayer(name="Bob", room="hall", player=user)
    server.people = [bob]

    def fail(directory: object, player: object) -> None:
        del directory, player
        raise ConfigError("cannot write")

    monkeypatch.setattr(resident_api, "save_player", fail)
    process_commands(server, bob, "recap BoB")  # type: ignore[arg-type]
    assert server.sent == ["That name cannot be used right now.\n"]
    assert user.name == "Bob"


def test_help_shows_an_entry_or_says_it_is_missing() -> None:
    """A name and an alias show the same paragraph. An unknown topic does not."""
    book = HelpBook(entries=(HelpEntry("say", ("'", '"', "`"), "Speak to the other people in the room you are in."), HelpEntry("examine", ("x",), "Show a player.")))  # fmt: skip  # noqa: E501
    server = FakeServer(help=book)
    player = FakePlayer()
    process_commands(server, player, "help say")  # type: ignore[arg-type]
    page = server.sent[0]
    assert page.startswith("\n\n")
    assert page.endswith("\n\n")
    assert "Command    : say" in page
    assert "Syntax     : say <message>" in page
    assert "Aliases    : ' \" `" in page
    assert server.room_lines == []

    server = FakeServer(help=book)
    process_commands(server, FakePlayer(), "help '")  # type: ignore[arg-type]
    alias_page = server.sent[0]
    assert "Command    : say" in alias_page
    assert "Syntax     : say <message>" in alias_page
    assert "Aliases    : ' \" `" in alias_page

    server = FakeServer(help=book)
    process_commands(server, FakePlayer(), "help nope")  # type: ignore[arg-type]
    assert server.sent == ["help for nope not found\n"]
    assert server.room_lines == []

    server = FakeServer(help=book)
    process_commands(server, FakePlayer(), "help say please")  # type: ignore[arg-type]
    assert server.sent == ["help for say please not found\n"]


def test_help_for_a_topic_that_is_not_a_command() -> None:
    """A topic with no command shows the paragraph, and an alias still opens it."""
    book = HelpBook(entries=(HelpEntry("hours", ("closing",), "The office closes at six."), HelpEntry("look", (), "Show this room.")))
    server = FakeServer(help=book)
    player = FakePlayer()
    process_commands(server, player, "help hours")  # type: ignore[arg-type]
    page = server.sent[0]
    assert page.startswith("\n\n")
    assert page.endswith("\n\n")
    assert "The office closes at six." in page
    assert "Command:" not in page
    assert "Syntax" not in page

    server = FakeServer(help=book)
    process_commands(server, FakePlayer(), "help closing")  # type: ignore[arg-type]
    assert "The office closes at six." in server.sent[0]
    assert "Command:" not in server.sent[0]


def search_book() -> HelpBook:
    """Return two visible pages and one admin page."""
    return HelpBook(
        entries=(
            HelpEntry("say", (), "Speak in the room."),
            HelpEntry("map", (), "Draw the house."),
            HelpEntry("shutdown", (), "Stop the server."),
        )
    )


def test_help_search_shows_the_one_page() -> None:
    """One match shows that help page."""
    resident = staff("Ada", Rank.RESIDENT)
    assert resident.player is not None
    resident.player.colour = False
    server = FakeServer(help=search_book(), people=[resident])
    process_commands(server, resident, "help search room")  # type: ignore[arg-type]
    assert "Speak in the room." in server.sent[0]
    assert server.sent[0].startswith("\n\n")


def test_help_search_lists_several_pages() -> None:
    """Several matches list one page name on each line."""
    resident = staff("Ada", Rank.RESIDENT)
    server = FakeServer(help=search_book(), people=[resident])
    process_commands(server, resident, "help SEARCH the")  # type: ignore[arg-type]
    assert server.sent == ["Help pages:\n  map\n  say\n"]


def test_help_search_keeps_a_spaced_name_on_one_line() -> None:
    """A page name that contains spaces stays on one line."""
    resident = staff("Ada", Rank.RESIDENT)
    book = HelpBook(
        entries=(
            HelpEntry("set plan", (), "A plan."),
            HelpEntry("examine", (), "A plan of the house."),
            HelpEntry("quit", (), "Leave."),
        )
    )
    server = FakeServer(help=book, people=[resident])
    process_commands(server, resident, "help search plan")  # type: ignore[arg-type]
    assert server.sent == ["Help pages:\n  examine\n  set plan\n"]


def test_help_search_says_when_nothing_matches() -> None:
    """No match quotes the typed text."""
    resident = staff("Ada", Rank.RESIDENT)
    server = FakeServer(help=search_book(), people=[resident])
    process_commands(server, resident, "help search Missing Thing")  # type: ignore[arg-type]
    assert server.sent == ["No help found for 'Missing Thing'.\n"]


def test_help_search_needs_text() -> None:
    """Help search with no text asks for the text."""
    resident = staff("Ada", Rank.RESIDENT)
    server = FakeServer(help=search_book(), people=[resident])
    process_commands(server, resident, "help search")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: help search <text>\n"]


def test_help_search_skips_a_hidden_page() -> None:
    """A resident does not find an admin page."""
    resident = staff("Ada", Rank.RESIDENT)
    server = FakeServer(help=search_book(), people=[resident])
    process_commands(server, resident, "help search Stop")  # type: ignore[arg-type]
    assert server.sent == ["No help found for 'Stop'.\n"]


def test_help_hides_a_see_name_the_player_cannot_open() -> None:
    """See Also drops a topic this rank cannot open."""
    book = HelpBook(entries=(HelpEntry("hours", (), "The office closes at six.", see=("reload", "look")), HelpEntry("look", (), "Show this room."), HelpEntry("reload", (), "Read the help file again.")))  # fmt: skip  # noqa: E501
    server = FakeServer(help=book)
    process_commands(server, FakePlayer(), "help hours")  # type: ignore[arg-type]
    assert "See Also   : look" in server.sent[0]
    assert "reload" not in server.sent[0]


def test_help_look_shows_the_command_name_as_syntax() -> None:
    """Look has no syntax string, so the page shows the name."""
    server, _ = run("help look")
    page = server.sent[0]
    assert "Syntax     : look" in page
    assert server.room_lines == []


def test_help_through_process_commands_uses_the_shipped_book() -> None:
    """Real help through process_commands shows rank, syntax, and colour from the shipped book."""
    when = datetime.now(timezone.utc)
    resident = FakePlayer(player=Player("Ada", "reception", when, rank=Rank.RESIDENT, colour=False))
    server = FakeServer(help=SHIPPED_HELP, people=[resident])
    process_commands(server, resident, "help shout")  # type: ignore[arg-type]
    shout_page = server.sent[0]
    assert "Permissions: Resident" in shout_page
    assert "Syntax     : shout <message>" in shout_page

    server = FakeServer(help=SHIPPED_HELP, people=[resident])
    process_commands(server, resident, "help !")  # type: ignore[arg-type]
    assert "Syntax     : shout <message>" in server.sent[0]

    server = FakeServer(help=SHIPPED_HELP, people=[resident])
    process_commands(server, resident, "help set plan")  # type: ignore[arg-type]
    plan_page = server.sent[0]
    assert "Syntax     : set plan <plan>" in plan_page
    assert "Permissions: Resident" in plan_page

    server = FakeServer(help=SHIPPED_HELP, people=[resident])
    process_commands(server, resident, "help disclaimer")  # type: ignore[arg-type]
    assert "Permissions" not in server.sent[0]

    server = FakeServer(help=SHIPPED_HELP, people=[resident])
    process_commands(server, resident, "help plan")  # type: ignore[arg-type]
    assert server.sent == ["help for plan not found\n"]

    coder = staff("Ada", Rank.CODER)
    assert coder.player is not None
    coder.player.colour = False
    server = FakeServer(help=SHIPPED_HELP, people=[coder])
    process_commands(server, coder, "help shutdown")  # type: ignore[arg-type]
    assert "Permissions: Coder or Site Admin" in server.sent[0]
    server = FakeServer(help=SHIPPED_HELP, people=[coder])
    process_commands(server, coder, "help reload config")  # type: ignore[arg-type]
    assert "Permissions: Coder or Site Admin" in server.sent[0]

    super_user = staff("Ada", Rank.SUPER_USER)
    assert super_user.player is not None
    super_user.player.colour = True
    server = FakeServer(help=SHIPPED_HELP, people=[super_user])
    process_commands(server, super_user, "help grab")  # type: ignore[arg-type]
    assert cyan("Super User") in server.sent[0]


def test_prompt_follows_a_finished_command() -> None:
    """The prompt returns after success, a syntax error, and an unknown command."""
    server = FakeServer(help=SHIPPED_HELP)
    player = FakePlayer()
    process_input(server, player, "help")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: help <topic>\n", "What? > "]

    process_input(server, player, "say")  # type: ignore[arg-type]
    assert server.sent[-2:] == ["Syntax: say <message>\n", "What? > "]

    process_input(server, player, "nope")  # type: ignore[arg-type]
    assert server.sent[-2:] == ["No such command 'nope'\n", "What? > "]


def test_prompt_command_replaces_the_next_prompt() -> None:
    """``prompt`` stores the text and the following prompt uses it."""
    user = Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    player = FakePlayer(player=user)
    server = FakeServer()
    process_input(server, player, "set prompt Hello >")  # type: ignore[arg-type]
    assert user.prompt == "Hello > "
    assert server.sent == ["Prompt set.\n", "Hello > "]


def test_a_newbie_entered_shows_the_default_description_on_examine(tmp_path: Path) -> None:
    """A newbie who entered through login shows I am new here between the rules."""
    server = login_host(tmp_path)
    ada = FakePlayer(player=None)
    process_input(server, ada, "Ada")  # type: ignore[arg-type]
    process_input(server, ada, "yes")  # type: ignore[arg-type]
    assert ada.player is not None
    assert ada.player.description == "I am new here"
    bea = newbie("Bea")
    server.people = [ada, bea]
    server.sent.clear()
    process_commands(server, bea, "examine Ada")  # type: ignore[arg-type]
    lines = server.sent[0].splitlines()
    assert lines[3].startswith("  I am new here")
    assert lines[2] == lines[4]


def test_who_and_examine_mask_su_channel(tmp_path: Path) -> None:
    """A resident sees Resident. SU Channel sees Super User Channel."""
    save(tmp_path, "Cyd", Rank.SU_CHANNEL)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.RESIDENT)
    bea = staff("Bea", Rank.SU_CHANNEL)
    assert ada.player is not None
    assert bea.player is not None
    ada.player.colour = True
    bea.player.colour = True
    server.people = [ada, bea]
    process_commands(server, ada, "who")  # type: ignore[arg-type]
    assert "Super User Channel" not in server.sent[0]
    bea_row = next(line for line in server.sent[0].splitlines() if "Bea" in line)
    assert "Resident" in bea_row
    server.sent.clear()
    process_commands(server, ada, "examine Bea")  # type: ignore[arg-type]
    assert "Resident" in server.sent[0]
    assert "Super User Channel" not in server.sent[0]
    server.sent.clear()
    process_commands(server, bea, "examine me")  # type: ignore[arg-type]
    assert cyan("Super User Channel") in server.sent[0]
    server.sent.clear()
    process_commands(server, ada, "examine Cyd")  # type: ignore[arg-type]
    assert "Resident" in server.sent[0]
    assert "Super User Channel" not in server.sent[0]
    server.sent.clear()
    process_commands(server, bea, "examine Cyd")  # type: ignore[arg-type]
    assert cyan("Super User Channel") in server.sent[0]


def test_description_sets_clears_and_refuses_long_text(tmp_path: Path) -> None:
    """Description stores one line, clears it, and refuses more than 80 characters."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [bea]
    process_commands(server, bea, "set description Hello there")  # type: ignore[arg-type]
    assert server.sent == ["Description set.\n"]
    path = player_path(tmp_path, "Bea")
    assert "description: Hello there" in path.read_text(encoding="utf-8")
    server.sent.clear()
    process_commands(server, bea, "examine me")  # type: ignore[arg-type]
    assert "  Hello there" in server.sent[0]
    assert "Hello there".center(72) not in server.sent[0]
    server.sent.clear()
    process_commands(server, bea, "set description")  # type: ignore[arg-type]
    assert server.sent == ["Description cleared.\n"]
    assert "description" not in path.read_text(encoding="utf-8")
    server.sent.clear()
    process_commands(server, bea, "examine me")  # type: ignore[arg-type]
    assert "  Hello there" not in server.sent[0]
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), description="Saved"))
    before = path.read_text(encoding="utf-8")
    server.sent.clear()
    process_commands(server, bea, f"set description {'x' * 81}")  # type: ignore[arg-type]
    assert server.sent == ["A description is at most 80 characters.\n"]
    assert path.read_text(encoding="utf-8") == before


def test_description_preserves_internal_spaces(tmp_path: Path) -> None:
    """Description stores one stripped line without collapsing spaces."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [bea]
    process_commands(server, bea, "set description hello  world")  # type: ignore[arg-type]
    assert server.sent == ["Description set.\n"]
    assert bea.player.description == "hello  world"
    path = player_path(tmp_path, "Bea")
    assert "description: hello  world" in path.read_text(encoding="utf-8")


def test_description_refuses_81_chars_without_changing_live_value(tmp_path: Path) -> None:
    """An over-long description is refused and the in-memory line stays put."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [bea]
    bea.player.description = "Still here"
    server.sent.clear()
    process_commands(server, bea, f"set description {'x' * 81}")  # type: ignore[arg-type]
    assert server.sent == ["A description is at most 80 characters.\n"]
    assert bea.player.description == "Still here"


def test_save_writes_the_live_profile(tmp_path: Path) -> None:
    """Save stores the live description and leaves status off the file."""
    save(tmp_path, "Ada", Rank.RESIDENT)
    server = FakeServer()
    server.roster_dir = tmp_path
    ada = staff("Ada", Rank.RESIDENT)
    assert ada.player is not None
    ada.player.description = "Hello"
    ada.player.status = "away"
    ada.player.auto_reply = "later"
    command_save(server, ada, Command("save", command_save, False, "save"), None)
    saved = load_saved(player_path(tmp_path, "Ada"))
    assert saved is not None
    assert saved.description == "Hello"
    text = player_path(tmp_path, "Ada").read_text(encoding="utf-8")
    assert "status" not in text
    assert "auto_reply" not in text
    assert ada.sent == ["Player Saved.\n"]


def test_newbie_cannot_save() -> None:
    """A newbie is told save does not exist."""
    when = datetime.now(timezone.utc)
    newbie = FakePlayer(player=Player("Ada", "reception", when, rank=Rank.NEWBIE))
    server, _ = run("save", newbie)
    assert server.sent == ["No such command 'save'\n"]


def test_save_rejects_extra_text(tmp_path: Path) -> None:
    """Save with arguments shows the syntax line."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [bea]
    process_commands(server, bea, "save now")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: save\n"]


def test_save_keeps_the_file_when_save_player_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed save sends CANNOT_NOW and leaves the file bytes alone."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [bea]
    assert bea.player is not None
    bea.player.description = "Hello"
    path = player_path(tmp_path, "Bea")
    before = path.read_bytes()

    def fail(directory: object, player: object) -> None:
        del directory, player
        raise ConfigError("cannot write")

    monkeypatch.setattr(resident_api, "save_player", fail)
    process_commands(server, bea, "save")  # type: ignore[arg-type]
    assert server.sent == ["That name cannot be used right now.\n"]
    assert path.read_bytes() == before


def test_a_newbie_cannot_retire() -> None:
    """A newbie is told retire does not exist."""
    server, _ = run("retire", newbie("Ada"))
    assert server.sent == ["No such command 'retire'\n"]


def test_retire_refuses_the_admin(tmp_path: Path) -> None:
    """The super admin is refused and is not asked to confirm."""
    server = login_host(tmp_path)
    boss = staff("admin", Rank.SUPER_ADMIN)
    server.people = [boss]
    process_input(server, boss, "retire")  # type: ignore[arg-type]
    assert server.sent == ["The super admin cannot be removed.\n", "What? > "]
    assert boss.auth == "name"


def test_retire_with_a_bad_super_admin_file_sends_cannot_now(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Retire does not crash when the super admin file is invalid YAML."""
    server = login_host(tmp_path)
    boss = staff("admin", Rank.SUPER_ADMIN)
    server.people = [boss]
    player_path(tmp_path, "admin").write_text(":\n  bad yaml: [\n", encoding="utf-8")
    process_input(server, boss, "retire")  # type: ignore[arg-type]
    assert "Invalid YAML" in capsys.readouterr().out
    assert any("That name cannot be used right now." in line for line in server.sent)
    assert boss.auth == "name"


def test_retire_cancelled_with_no_or_blank(tmp_path: Path) -> None:
    """No or a blank line cancels retire and keeps the file."""
    save(tmp_path, "Ada", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.RESIDENT)
    server.people = [ada]
    for answer in ("no", ""):
        server.sent.clear()
        process_input(server, ada, "retire")  # type: ignore[arg-type]
        process_input(server, ada, answer)  # type: ignore[arg-type]
        assert server.sent[-1] == "Retire cancelled.\nWhat? > "
        assert ada.auth == "name"
        assert load_saved(player_path(tmp_path, "Ada")) is not None


def test_retire_yes_asks_for_password(tmp_path: Path) -> None:
    """YES moves to the password step with no play prompt appended."""
    save(tmp_path, "Ada", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.RESIDENT)
    server.people = [ada]
    process_input(server, ada, "retire")  # type: ignore[arg-type]
    server.sent.clear()
    process_input(server, ada, "YES")  # type: ignore[arg-type]
    assert server.sent == ["Password: "]
    assert ada.auth == "retire_password"


def test_retire_wrong_password_returns_to_the_room(tmp_path: Path) -> None:
    """A wrong password cancels retire and leaves the file."""
    save(tmp_path, "Ada", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.RESIDENT)
    server.people = [ada]
    process_input(server, ada, "retire")  # type: ignore[arg-type]
    process_input(server, ada, "yes")  # type: ignore[arg-type]
    server.sent.clear()
    process_input(server, ada, "wrong")  # type: ignore[arg-type]
    assert server.sent == ["Wrong password.\nWhat? > "]
    assert ada.auth == "name"
    assert server.dropped is False
    assert load_saved(player_path(tmp_path, "Ada")) is not None


def test_retire_deletes_the_file_and_disconnects(tmp_path: Path) -> None:
    """The right password retires the player and tells staff."""
    save(tmp_path, "Ada", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.RESIDENT)
    watcher = staff("Bea", Rank.SUPER_USER)
    server.people = [ada, watcher]
    process_input(server, ada, "retire")  # type: ignore[arg-type]
    process_input(server, ada, "yes")  # type: ignore[arg-type]
    server.sent.clear()
    process_input(server, ada, "pw")  # type: ignore[arg-type]
    assert server.sent == ["You have retired.\n"]
    assert load_saved(player_path(tmp_path, "Ada")) is None
    assert server.staff_lines == [paint_staff(watcher, "< SU > Ada retires.\n")]
    assert server.dropped_clients == [ada]
    assert server.announced == [True]


def test_retire_keeps_the_file_when_delete_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip  # noqa: E501
    """A failed delete sends CANNOT_NOW and does not drop the player."""
    save(tmp_path, "Ada", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.RESIDENT)
    server.people = [ada]

    def fail(directory: object, name: object) -> bool:
        del directory, name
        raise ConfigError("cannot delete")

    monkeypatch.setattr(password_api, "delete_player", fail)
    process_input(server, ada, "retire")  # type: ignore[arg-type]
    process_input(server, ada, "yes")  # type: ignore[arg-type]
    server.sent.clear()
    process_input(server, ada, "pw")  # type: ignore[arg-type]
    assert "cannot delete" in capsys.readouterr().out
    assert server.sent == ["That name cannot be used right now.\nWhat? > "]
    assert ada.auth == "name"
    assert server.dropped is False
    assert load_saved(player_path(tmp_path, "Ada")) is not None


def test_status_under_rank_on_connected_examine(tmp_path: Path) -> None:
    """Connected examine shows Status under User Rank; logged-out examine does not."""
    user = Player("Ada", "hall", datetime.now(timezone.utc), rank=Rank.SUPER_USER)
    ada = FakePlayer(player=user)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "set status busy")  # type: ignore[arg-type]
    server.sent.clear()
    process_commands(server, ada, "examine me")  # type: ignore[arg-type]
    lines = server.sent[0].splitlines()
    rank_index = next(i for i, line in enumerate(lines) if line.startswith("  User Rank"))
    status_index = next(i for i, line in enumerate(lines) if line.startswith("  Status"))
    assert status_index == rank_index + 1
    assert "busy" in lines[status_index]
    rule_lines = [i for i, line in enumerate(lines) if line and set(line) == {"-"}]
    assert rank_index == rule_lines[1] + 1
    server.sent.clear()
    process_commands(server, ada, "set status")  # type: ignore[arg-type]
    server.sent.clear()
    process_commands(server, ada, "examine me")  # type: ignore[arg-type]
    assert "  Status" not in server.sent[0]
    save(tmp_path, "Adam", Rank.RESIDENT)
    server = login_host(tmp_path, super_admin="Zed")
    process_commands(server, FakePlayer(), "examine ad")  # type: ignore[arg-type]
    assert "  Status" not in server.sent[0]


def test_examine_shows_roles_under_rank_when_one_is_set(tmp_path: Path) -> None:
    """Connected and logged-out examine list named roles under User Rank."""
    user = Player("Ada", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT, roles=int(Roles.SITE_ADMIN))
    user.status = "busy"
    ada = FakePlayer(player=user)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "examine me")  # type: ignore[arg-type]
    lines = server.sent[0].splitlines()
    rank_index = next(i for i, line in enumerate(lines) if line.startswith("  User Rank"))
    roles_index = next(i for i, line in enumerate(lines) if line.startswith("  Roles"))
    status_index = next(i for i, line in enumerate(lines) if line.startswith("  Status"))
    assert roles_index == rank_index + 1
    assert status_index == roles_index + 1
    assert "Site Admin" in lines[roles_index]
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), roles=int(Roles.SITE_ADMIN)))
    logged_out = login_host(tmp_path)
    process_commands(logged_out, FakePlayer(), "examine Bea")  # type: ignore[arg-type]
    body = logged_out.sent[0].splitlines()
    rank_index = next(i for i, line in enumerate(body) if line.startswith("  User Rank"))
    roles_index = next(i for i, line in enumerate(body) if line.startswith("  Roles"))
    last_index = next(i for i, line in enumerate(body) if line.startswith("  Last from"))
    assert roles_index == rank_index + 1
    assert last_index == roles_index + 1
    assert "Site Admin" in body[roles_index]


def test_examine_omits_roles_when_none_are_set(tmp_path: Path) -> None:
    """A player with no named role has no Roles line."""
    user = Player("Ada", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    ada = FakePlayer(player=user)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "examine me")  # type: ignore[arg-type]
    assert "  Roles" not in server.sent[0]
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    logged_out = login_host(tmp_path)
    process_commands(logged_out, FakePlayer(), "examine Bea")  # type: ignore[arg-type]
    assert "  Roles" not in logged_out.sent[0]


def test_status_shows_on_examine_who_and_lsu_and_is_not_saved(tmp_path: Path) -> None:
    """Status is a session field under User Rank on examine, and on both lists."""
    user = Player("Ada", "hall", datetime.now(timezone.utc), rank=Rank.SUPER_USER)
    ada = FakePlayer(player=user)
    server, _ = run("set status in a meeting", ada)
    assert user.status == "in a meeting"
    assert server.sent[-1] == "Status set.\n"
    server.sent.clear()
    process_commands(server, ada, f"set status {'x' * 21}")  # type: ignore[arg-type]
    assert server.sent == ["A status is at most 20 characters.\n"]
    assert user.status == "in a meeting"
    server.sent.clear()
    process_commands(server, ada, "set status")  # type: ignore[arg-type]
    assert user.status == ""
    assert server.sent == ["Status cleared.\n"]
    server.sent.clear()
    process_commands(server, ada, "set status busy")  # type: ignore[arg-type]
    server.sent.clear()
    process_commands(server, ada, "examine me")  # type: ignore[arg-type]
    lines = server.sent[0].splitlines()
    rank_index = next(i for i, line in enumerate(lines) if line.startswith("  User Rank"))
    status_index = next(i for i, line in enumerate(lines) if line.startswith("  Status"))
    assert status_index == rank_index + 1
    assert "busy" in lines[status_index]
    server.sent.clear()
    process_commands(server, ada, "set status")  # type: ignore[arg-type]
    server.sent.clear()
    process_commands(server, ada, "examine me")  # type: ignore[arg-type]
    assert "  Status" not in server.sent[0]
    server.sent.clear()
    process_commands(server, ada, "set status on call")  # type: ignore[arg-type]
    server.sent.clear()
    process_commands(server, ada, "who")  # type: ignore[arg-type]
    who_header = server.sent[0].splitlines()[1]
    who_cols = ("Name", "Rank", "Room", "Status", "Idle")
    assert [who_header.index(name) for name in who_cols] == sorted(who_header.index(name) for name in who_cols)
    server.sent.clear()
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    lsu_header = server.sent[0].splitlines()[1]
    assert [lsu_header.index(name) for name in who_cols] == sorted(lsu_header.index(name) for name in who_cols)
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [bea]
    path = player_path(tmp_path, "Bea")
    process_commands(server, bea, "set status meeting")  # type: ignore[arg-type]
    assert "status" not in path.read_text(encoding="utf-8")


def test_load_profile_copies_a_saved_description(tmp_path: Path) -> None:
    """A saved player enters with the description from the file."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), description="Hello"))
    server = login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    entry_api.load_profile(server, user)  # type: ignore[arg-type]
    assert user.description == "Hello"


def test_load_profile_copies_a_saved_entermsg(tmp_path: Path) -> None:
    """A saved player enters with the enter message from the file."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), entermsg="crawls into the room"))
    server = login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    entry_api.load_profile(server, user)  # type: ignore[arg-type]
    assert user.entermsg == "crawls into the room"


def test_promote_drops_the_default_description_or_keeps_a_custom_one(tmp_path: Path) -> None:
    """Resident promotion clears I am new here but keeps a typed description."""
    server = login_host(tmp_path)
    default = newbie("Bea")
    assert default.player is not None
    default.player.description = "I am new here"
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [default, ada]
    process_commands(server, ada, "resident Bea")  # type: ignore[arg-type]
    process_input(server, default, "hunter2")  # type: ignore[arg-type]
    process_input(server, default, "hunter2")  # type: ignore[arg-type]
    assert default.player.description == ""
    path = player_path(tmp_path, "Bea")
    assert "description" not in path.read_text(encoding="utf-8")

    custom = newbie("Cyd")
    assert custom.player is not None
    custom.player.description = "Hello"
    server.people = [custom, ada]
    process_commands(server, ada, "resident Cyd")  # type: ignore[arg-type]
    process_input(server, custom, "hunter2")  # type: ignore[arg-type]
    process_input(server, custom, "hunter2")  # type: ignore[arg-type]
    assert "description: Hello" in player_path(tmp_path, "Cyd").read_text(encoding="utf-8")


def test_colour_reports_and_sets_on_and_off() -> None:
    """Bare colour reports the choice. on and off set it. ON is syntax."""
    newbie, _ = run("colour")
    assert newbie.sent == ["Colour is on.\n"]
    turned, player = run("colour off")
    assert turned.sent == ["Colour is off.\n"]
    assert player.player is not None
    assert player.player.colour is False
    back, player = run("color on", player)
    assert back.sent == ["Colour is on.\n"]
    assert player.player is not None
    assert player.player.colour is True
    bad, _ = run("colour ON")
    assert bad.sent == ["Syntax: colour on|off\n"]
    extra, _ = run("colour off please")
    assert extra.sent == ["Syntax: colour on|off\n"]
    glued, _ = run("coloron")
    assert glued.sent == ["No such command 'coloron'\n"]
    helped, _ = run("help colour")
    assert "Turn colour on or off" in helped.sent[0]
    helped, _ = run("help color")
    assert "Turn colour on or off" in helped.sent[0]


def test_colour_off_is_written_and_on_removes_the_key(tmp_path: Path) -> None:
    """A saved player keeps off in the file, and on removes that key."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [bea]
    process_commands(server, bea, "colour off")  # type: ignore[arg-type]
    path = player_path(tmp_path, "bea")
    saved = load_saved(path)
    assert saved is not None
    assert saved.colour is False
    assert "colour: false" in path.read_text(encoding="utf-8")
    server.sent.clear()
    process_commands(server, bea, "colour on")  # type: ignore[arg-type]
    saved = load_saved(path)
    assert saved is not None
    assert saved.colour is True
    assert "colour" not in path.read_text(encoding="utf-8")


def test_a_failed_colour_write_still_changes_the_live_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The player is told the choice even when the file cannot be written."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [bea]

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(newbie_api, "save_player", fail)
    process_commands(server, bea, "colour off")  # type: ignore[arg-type]
    assert server.sent == ["Colour is off.\n"]
    assert bea.player is not None
    assert bea.player.colour is False


def test_a_newbie_colour_off_is_stored_on_resident(tmp_path: Path) -> None:
    """Becoming a resident writes the colour chosen on this visit."""
    server = login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), colour=False)
    bea = FakePlayer(name="Bea", room="hall", player=user)
    server.people = [bea]
    password_api.promote_to_resident(server, bea, "secret")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "bea"))
    assert saved is not None
    assert saved.rank == Rank.RESIDENT
    assert saved.colour is False
    assert "colour: false" in player_path(tmp_path, "bea").read_text(encoding="utf-8")


def test_quit() -> None:
    """Quit says goodbye and asks the server to drop the player."""
    server, _ = run("quit")
    assert server.sent == ["Goodbye!\n"]
    assert server.room_lines == []
    assert server.dropped is True


@pytest.mark.parametrize(("text", "code"), [("n", "n"), ("north", "n"), ("west", "w"), ("w", "w"), ("north-west", "nw"), ("north west", "nw"), ("south east", "se"), ("up", "up"), ("down", "down"),])  # fmt: skip  # noqa: E501
def test_parse_direction(text: str, code: str) -> None:
    """Short and long direction words share one code."""
    assert parse_direction(text) == code


def test_parse_direction_rejects_unknown_words() -> None:
    """Case and unknown words do not match."""
    assert parse_direction("West") is None
    assert parse_direction("sideways") is None


def test_go_without_a_direction() -> None:
    """Go alone explains the syntax."""
    server, player = run("go")
    assert server.sent == ["Syntax: go <direction>\n"]
    assert player.room == "reception"


def test_go_unknown_direction() -> None:
    """An unknown remainder is quoted back."""
    server, player = run("go sideways")
    assert server.sent == ["No such direction 'sideways'.\n"]
    assert player.room == "reception"


def test_go_collapses_whitespace() -> None:
    """North west is one direction."""
    server, player = run("go north   west")
    assert player.room == "hall"
    assert "Ada goes north-west to Hall.\n" in server.room_lines


def test_go_uses_the_destination_enter_sentence() -> None:
    """People left behind hear the room being entered."""
    server = FakeServer()
    server.layout = Layout(rooms={"reception": Room("The desk.", 4, {"nw": "hall"}), "hall": Room("A hall.", 4, {}, "opens the door and enters the hall"),})  # fmt: skip  # noqa: E501
    ada = FakePlayer()
    process_commands(server, ada, "go nw")  # type: ignore[arg-type]
    assert "Ada opens the door and enters the hall.\n" in server.room_lines
    assert "Ada goes north-west to Hall.\n" not in server.room_lines


def test_go_announces_the_player_enter_message() -> None:
    """The destination hears the player's sentence, not a direction."""
    user = Player("Ada", "reception", datetime.now(timezone.utc), entermsg="crawls into the room")
    ada = FakePlayer(player=user)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "go nw")  # type: ignore[arg-type]
    assert "Ada crawls into the room.\n" in server.room_lines
    assert "Ada arrives from the south-east.\n" not in server.room_lines


def test_join_says_who_they_went_to_join() -> None:
    """Join names the player, not the room sentence."""
    bea = FakePlayer(name="Bea", room="hall")
    server = FakeServer(people=[bea])
    server.layout = Layout(rooms={"reception": Room("The desk.", 4, {"nw": "hall"}), "hall": Room("A hall.", 4, {}, "opens the door and enters the hall"),})  # fmt: skip  # noqa: E501
    ada = FakePlayer()
    process_commands(server, ada, "join Bea")  # type: ignore[arg-type]
    assert server.room_lines[0] == "Ada goes to join Bea.\n"


def test_main_uses_the_start_room_enter_sentence() -> None:
    """Main speaks the start room's sentence to the room being left."""
    ada = FakePlayer(room="hall")
    server = FakeServer(people=[ada])
    server.layout = Layout(rooms={"reception": Room("The desk.", 4, {"nw": "hall"}, "walks back to the desk"), "hall": Room("A hall.", 4, {}),})  # fmt: skip  # noqa: E501
    process_commands(server, ada, "main")  # type: ignore[arg-type]
    assert server.room_lines[0] == "Ada walks back to the desk.\n"


def test_map_sends_the_map() -> None:
    """Map with no remainder sends the centered map to the player only."""
    server, player = run("map")
    assert server.room_lines == []
    width = frame_width(player.discovered_columns, player.frame_columns)
    colour_on = player.player is not None and player.player.colour
    drawn = server.layout.map_text(player.room, width, colour=colour_on)
    assert server.sent[0] == f"\n\n{center_block(drawn, width)}\n"
    assert green("Reception") in server.sent[0]


def test_home_creates_the_room_and_tells_only_the_room_they_left(tmp_path: Path) -> None:
    """The first home is saved, and only the room being left hears the leave line."""
    players = tmp_path / "players"
    players.mkdir()
    save(players, "Ada", Rank.RESIDENT)
    save(players, "Bea", Rank.RESIDENT)
    save(players, "Cyd", Rank.RESIDENT)
    ada = staff("Ada", Rank.RESIDENT)
    bea = FakePlayer(name="Bea", room="reception", player=Player("Bea", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    cyd = FakePlayer(name="Cyd", room="hall", player=Player("Cyd", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server = login_host(tmp_path)
    server.roster_dir = players
    server.config = replace(server.config, rooms=tmp_path / "system.yml", players=players)
    server.people = [ada, bea, cyd]
    process_commands(server, ada, "home")  # type: ignore[arg-type]
    assert ada.room == "ada_home"
    assert "ada_home" in server.layout.rooms
    assert bea.sent == ["Ada goes home.\n"]
    assert cyd.sent == []
    assert "Ada goes home.\n" not in ada.sent
    assert server.sent[0] == "Your home has been created.\n"
    assert server.sent[-1] == "You are home.\n\nNobody else is here.\n\nExits: none.\n"
    stored = load_saved(player_path(players, "Ada"))
    assert stored is not None
    assert stored.home is True
    assert stored.home_login is False

    server.sent.clear()
    process_commands(server, ada, "home")  # type: ignore[arg-type]
    assert server.sent == ["You are already home.\n"]
    assert bea.sent == ["Ada goes home.\n"]


def test_home_writes_the_house_file_and_map_draws_it(tmp_path: Path) -> None:
    """The first home writes ada.yml, and map names the room Home."""
    players = tmp_path / "players"
    players.mkdir()
    save(players, "Ada", Rank.RESIDENT)
    ada = staff("Ada", Rank.RESIDENT)
    server = login_host(tmp_path)
    server.roster_dir = players
    server.config = replace(server.config, rooms=tmp_path / "system.yml", players=players)
    server.people = [ada]
    process_commands(server, ada, "home")  # type: ignore[arg-type]
    written = (tmp_path / "ada.yml").read_text(encoding="utf-8")
    assert "ada_home:" in written
    assert ada.room == "ada_home"
    assert server.sent[0] == "Your home has been created.\n"
    server.sent.clear()
    process_commands(server, ada, "map")  # type: ignore[arg-type]
    assert "Home" in server.sent[-1]
    assert "Surely you know the layout?" not in server.sent[-1]


def test_home_login_on_writes_a_missing_house_when_home_is_set(tmp_path: Path) -> None:
    """A missing house file is written when home is already true, and the player stays put."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("Ada", Rank.RESIDENT, hash_password("pw", rounds=4), home=True))
    ada = staff("Ada", Rank.RESIDENT)
    server = login_host(players)
    server.config = replace(server.config, rooms=tmp_path / "system.yml", players=players)
    server.people = [ada]
    process_commands(server, ada, "set login home")  # type: ignore[arg-type]
    assert (tmp_path / "ada.yml").is_file()
    assert ada.room == "reception"
    assert server.sent == ["You will login to your home room.\n"]
    stored = load_saved(player_path(players, "Ada"))
    assert stored is not None
    assert stored.home is True
    assert stored.home_login is True


def test_set_login_reports_and_chooses_home_or_main(tmp_path: Path) -> None:
    """Set login reports the choice. home and main are exact. Old plan is unknown."""
    players = tmp_path / "players"
    players.mkdir()
    save(players, "Ada", Rank.RESIDENT)
    ada = staff("Ada", Rank.RESIDENT)
    ada.frame_columns = 120
    server = login_host(tmp_path)
    server.roster_dir = players
    server.config = replace(server.config, rooms=tmp_path / "system.yml", players=players)
    server.people = [ada]
    process_commands(server, ada, "set login")  # type: ignore[arg-type]
    assert server.sent == ["You will login to the main room.\n"]
    server.sent.clear()
    process_commands(server, ada, "set Login")  # type: ignore[arg-type]
    assert server.sent == ["You will login to the main room.\n"]
    server.sent.clear()
    process_commands(server, ada, "set login home")  # type: ignore[arg-type]
    assert server.sent == ["Your home has been created. You will login to your home room.\n"]
    assert ada.room == "reception"
    server.sent.clear()
    process_commands(server, ada, "set login")  # type: ignore[arg-type]
    assert server.sent == ["You will login to your home room.\n"]
    server.sent.clear()
    process_commands(server, ada, "set login main")  # type: ignore[arg-type]
    assert server.sent == ["You will login to the main room.\n"]
    server.sent.clear()
    process_commands(server, ada, "set login Home")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: set login home|main\n"]
    server.sent.clear()
    process_commands(server, ada, "set login home now")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: set login home|main\n"]
    server.sent.clear()
    process_commands(server, ada, "plan later")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'plan'\n"]
    server.sent.clear()
    process_commands(server, ada, "set plan")  # type: ignore[arg-type]
    assert server.sent == ["Plan cleared.\n"]
    server.sent.clear()
    process_commands(server, ada, "set Prompt")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: set prompt <prompt>\n"]
    server.sent.clear()
    process_commands(server, ada, "set commands")  # type: ignore[arg-type]
    assert server.sent == ["Subcommands available:\nauto_reply commands description entermsg login loginmsg logoutmsg plan prompt status\n"]
    server.sent.clear()
    process_commands(server, ada, "home login")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: home\n"]


def test_home_refuses_a_house_path_that_is_the_office(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Home refuses a house path that is the office file and leaves the player where they are."""
    players = tmp_path / "players"
    players.mkdir()
    save(players, "System", Rank.RESIDENT)
    office = tmp_path / "rooms" / "system.yml"
    office.parent.mkdir()
    text = "reception:\n  description: The desk.\n  max_occupancy: 2\n  exits: {}\n"
    office.write_text(text, encoding="utf-8")
    system = staff("System", Rank.RESIDENT)
    server = login_host(players)
    server.config = replace(server.config, rooms=office, help=tmp_path / "help.yml", players=players)
    server.layout = load_layout(office).with_void()
    server.people = [system]
    process_commands(server, system, "home")  # type: ignore[arg-type]
    assert capsys.readouterr().out == ""
    assert server.sent == ["Cannot read your home.\n"]
    assert system.room == "reception"
    assert office.read_text(encoding="utf-8") == text
    stored = load_saved(player_path(players, "System"))
    assert stored is not None
    assert stored.home is False


def test_home_login_reports_and_sets_the_next_fresh_login(tmp_path: Path) -> None:
    """Login on creates a missing home and does not move the player."""
    players = tmp_path / "players"
    players.mkdir()
    save(players, "Ada", Rank.RESIDENT)
    ada = staff("Ada", Rank.RESIDENT)
    server = login_host(tmp_path)
    server.roster_dir = players
    server.config = replace(server.config, rooms=tmp_path / "system.yml", players=players)
    server.people = [ada]
    process_commands(server, ada, "set login")  # type: ignore[arg-type]
    assert server.sent == ["You will login to the main room.\n"]
    assert ada.room == "reception"

    server.sent.clear()
    process_commands(server, ada, "set login home")  # type: ignore[arg-type]
    assert server.sent == ["Your home has been created. You will login to your home room.\n"]
    assert ada.room == "reception"
    stored = load_saved(player_path(players, "Ada"))
    assert stored is not None
    assert stored.home is True
    assert stored.home_login is True

    server.sent.clear()
    process_commands(server, ada, "set login home")  # type: ignore[arg-type]
    assert server.sent == ["You will login to your home room.\n"]

    server.sent.clear()
    process_commands(server, ada, "set login main")  # type: ignore[arg-type]
    assert server.sent == ["You will login to the main room.\n"]
    stored = load_saved(player_path(players, "Ada"))
    assert stored is not None
    assert stored.home is True
    assert stored.home_login is False

    server.sent.clear()
    process_commands(server, ada, "home sideways")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: home\n"]


def test_a_fresh_login_starts_at_home_when_the_flag_is_on(tmp_path: Path) -> None:
    """home_login puts a new session in the home. Reconnect stays where it was."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("Ada", Rank.RESIDENT, hash_password("pw", rounds=4), home=True, home_login=True))
    server = login_host(tmp_path)
    server.roster_dir = players
    server.config = replace(server.config, rooms=tmp_path / "system.yml", players=players)
    write_default_house(server.config.rooms, "Ada")
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [bea]
    ada = FakePlayer(player=None)
    enter(server, ada, "Ada", Rank.RESIDENT)  # type: ignore[arg-type]
    assert ada.player is not None
    assert ada.player.room == "ada_home"
    assert "ada_home" in server.layout.rooms
    assert any(line.startswith("You are home.") for line in ada.sent)
    assert "Ada has connected.\n" not in bea.sent

    live = staff("Bea", Rank.RESIDENT)
    live.room = "hall"
    assert live.player is not None
    live.player.room = "hall"
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), home=True, home_login=True))
    server.people = [live]
    newcomer = FakePlayer(player=None)
    held = load_saved(player_path(players, "Bea"))
    assert held is not None
    enter_fresh(server, newcomer, held)  # type: ignore[arg-type]
    assert live.player.room == "hall"


def test_login_demotes_an_unknown_rank_and_tells_site_admins(tmp_path: Path) -> None:
    """A file word of super becomes resident, and both notices go out."""
    players = tmp_path / "players"
    players.mkdir()
    path = player_path(players, "Bea")
    path.write_text("name: Bea\nlowercase_name: bea\nrank: super\npassword: x\nroles: 1\n", encoding="utf-8")
    save_player(players, SavedPlayer("Ada", Rank.RESIDENT, hash_password("pw", rounds=4), roles=int(Roles.SITE_ADMIN)))
    server = login_host(tmp_path, super_admin=None)
    server.roster_dir = players
    server.config = replace(server.config, players=players)
    ada = staff("Ada", Rank.RESIDENT)
    ada.player.roles = int(Roles.SITE_ADMIN)
    cyd = staff("Cyd", Rank.RESIDENT)
    bea = FakePlayer(player=None)
    server.people = [ada, cyd, bea]
    enter(server, bea, "Bea", Rank.RESIDENT)  # type: ignore[arg-type]
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.rank is Rank.RESIDENT
    assert loaded.unknown_rank is None
    assert loaded.roles == 1
    assert "rank: resident\n" in path.read_text(encoding="utf-8")
    personal = "You logged in with an unknown rank 'super' and have been auto-demoted to 'resident'.\n"
    issue = "[ Issue Located ] Bea logged in with an unknown rank 'super' and has been auto-demoted to 'resident'.\n"
    assert personal in bea.sent
    assert issue in bea.sent
    assert bea.sent.index(personal) < bea.sent.index(issue)
    assert issue in ada.sent
    assert personal not in ada.sent
    assert issue not in cyd.sent
    assert personal not in cyd.sent


def test_reconnect_demotes_an_unknown_rank(tmp_path: Path) -> None:
    """Reconnect rewrites an unknown rank and does not repeat the startup warnings."""
    players = tmp_path / "players"
    players.mkdir()
    path = player_path(players, "Bea")
    path.write_text("name: Bea\nlowercase_name: bea\nrank: super\npassword: x\nroles: 1\n", encoding="utf-8")
    save_player(players, SavedPlayer("Ada", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    held = load_saved(path)
    assert held is not None
    assert held.rank is Rank.RESIDENT
    assert held.unknown_rank == "super"
    server = login_host(tmp_path, super_admin=None)
    server.roster_dir = players
    server.config = replace(server.config, players=players, obsolete_hot=("admin",))
    live = staff("Bea", Rank.SUPER_USER)
    live.room = "hall"
    assert live.player is not None
    live.player.room = "hall"
    live.player.roles = int(Roles.SITE_ADMIN)
    dee = staff("Dee", Rank.RESIDENT)
    dee.room = "hall"
    assert dee.player is not None
    dee.player.room = "hall"
    server.people = [live, dee]
    newcomer = FakePlayer(player=None)
    enter_fresh(server, newcomer, held)  # type: ignore[arg-type]
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.rank is Rank.RESIDENT
    assert loaded.unknown_rank is None
    assert loaded.roles == 1
    assert "rank: resident\n" in path.read_text(encoding="utf-8")
    assert server.replaced == [live]
    assert live.player.rank is Rank.RESIDENT
    assert live.player.room == "hall"
    brief = "A hall.\n\nNobody else is here.\n\nExits: none.\n"
    personal = "You logged in with an unknown rank 'super' and have been auto-demoted to 'resident'.\n"
    issue = "[ Issue Located ] Bea logged in with an unknown rank 'super' and has been auto-demoted to 'resident'.\n"
    prompt = "What? > "
    assert live.sent.index(brief) < live.sent.index(personal) < live.sent.index(issue) < live.sent.index(prompt)
    output = "".join([*server.sent, *server.room_lines, *server.staff_lines, *server.admin_lines])
    assert "There are multi super-admins" not in output
    assert "Obsolete keys found" not in output
    assert personal not in dee.sent
    assert issue not in dee.sent


def test_a_failed_demotion_write_sends_no_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The file stays unknown, and nobody is told, when the rewrite fails."""
    players = tmp_path / "players"
    players.mkdir()
    path = player_path(players, "Bea")
    path.write_text("name: Bea\nlowercase_name: bea\nrank: super\npassword: x\n", encoding="utf-8")
    server = login_host(tmp_path, super_admin=None)
    server.roster_dir = players
    server.config = replace(server.config, players=players)

    def fail_save(directory: Path, player: SavedPlayer) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("lupaxa.chat_server.entry.save_player", fail_save)
    bea = FakePlayer(player=None)
    enter(server, bea, "Bea", Rank.RESIDENT)  # type: ignore[arg-type]
    assert "rank: super\n" in path.read_text(encoding="utf-8")
    assert not any("auto-demoted" in line for line in bea.sent)
    assert bea.player is not None
    assert bea.player.rank is Rank.RESIDENT


def test_login_issues_reach_only_the_site_admin_who_entered(tmp_path: Path) -> None:
    """A resident and a site admin who is already inside do not hear the entering site admin's copy."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("Ada", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", Rank.RESIDENT, hash_password("pw", rounds=4), roles=int(Roles.SITE_ADMIN)))
    server = login_host(tmp_path, super_admin=None)
    server.roster_dir = players
    server.config = replace(server.config, players=players, obsolete_hot=("admin", "colour"))
    dee = staff("Dee", Rank.RESIDENT)
    assert dee.player is not None
    dee.player.roles = int(Roles.SITE_ADMIN)
    resident = staff("Eve", Rank.RESIDENT)
    server.people = [dee, resident]
    cyd = FakePlayer(player=None)
    enter(server, cyd, "Cyd", Rank.RESIDENT)  # type: ignore[arg-type]
    keys = "[ Issue Located ] Obsolete keys found in hot.yml: admin & colour. Please remove them.\n"
    admins = "[ Issue Located ] There are multi super-admins 'Ada & Bea' - this needs to be resolved before the next restart.\n"
    assert keys in cyd.sent
    assert admins in cyd.sent
    assert keys not in dee.sent
    assert admins not in dee.sent
    assert keys not in resident.sent
    assert admins not in resident.sent


def test_a_site_admin_hears_issues_when_login_places_them(tmp_path: Path) -> None:
    """The site-admin role hears each startup problem once, on the first room placement."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("Ada", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", Rank.RESIDENT, hash_password("pw", rounds=4), roles=int(Roles.SITE_ADMIN)))
    server = login_host(tmp_path, super_admin=None)
    server.roster_dir = players
    server.config = replace(server.config, players=players, obsolete_hot=("admin",))
    cyd = FakePlayer(player=None)
    enter(server, cyd, "Cyd", Rank.RESIDENT)  # type: ignore[arg-type]
    issues = [line for line in cyd.sent if line.startswith("[ Issue Located ]")]
    assert issues == [
        "[ Issue Located ] There are multi super-admins 'Ada & Bea' - this needs to be resolved before the next restart.\n",
        "[ Issue Located ] Obsolete keys found in hot.yml: admin. Please remove them.\n",
    ]
    assert cyd.player is not None
    assert cyd.player.roles == int(Roles.SITE_ADMIN)
    assert cyd.player.room == "reception"
    cyd.name = cyd.player.name
    cyd.room = cyd.player.room
    server.people = [cyd]
    cyd.sent.clear()
    process_commands(server, cyd, "go nw")  # type: ignore[arg-type]
    assert cyd.room == "hall"
    assert [line for line in cyd.sent if line.startswith("[ Issue Located ]")] == []


def test_a_site_admin_login_survives_a_malformed_roster_file(tmp_path: Path) -> None:
    """A bad player file does not stop login; the obsolete-key line is still sent."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("Ada", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", Rank.RESIDENT, hash_password("pw", rounds=4), roles=int(Roles.SITE_ADMIN)))
    (players / "cora.yml").write_text("- not a mapping\n", encoding="utf-8")
    server = login_host(tmp_path, super_admin=None)
    server.roster_dir = players
    server.config = replace(server.config, players=players, obsolete_hot=("admin",))
    cyd = FakePlayer(player=None)
    enter(server, cyd, "Cyd", Rank.RESIDENT)  # type: ignore[arg-type]
    assert cyd.player is not None
    assert cyd.player.roles == int(Roles.SITE_ADMIN)
    issues = [line for line in cyd.sent if line.startswith("[ Issue Located ]")]
    assert issues == [
        "[ Issue Located ] Obsolete keys found in hot.yml: admin. Please remove them.\n",
    ]


def test_a_rank_admin_without_the_site_admin_role_hears_no_issues(tmp_path: Path) -> None:
    """Rank Admin with roles omitted hears neither startup problem."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("Ada", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", Rank.ADMIN, hash_password("pw", rounds=4)))
    server = login_host(tmp_path, super_admin=None)
    server.roster_dir = players
    server.config = replace(server.config, players=players, obsolete_hot=("admin",))
    cyd = FakePlayer(player=None)
    enter(server, cyd, "Cyd", Rank.ADMIN)  # type: ignore[arg-type]
    assert cyd.player is not None
    assert cyd.player.roles == 0
    assert [line for line in cyd.sent if line.startswith("[ Issue Located ]")] == []


def test_an_obsolete_hot_key_uses_the_hot_file_name(tmp_path: Path) -> None:
    """The issue line names hot.yml for a leftover key stored on the hot side."""
    from lupaxa.chat_server.entry import issue_lines

    server = FakeServer()
    server.roster_dir = tmp_path
    server.config = replace(server.config, hot_path=Path("hot.yml"), obsolete_hot=("admin",), obsolete_cold=())
    assert issue_lines(server) == ("[ Issue Located ] Obsolete keys found in hot.yml: admin. Please remove them.\n",)  # type: ignore[arg-type]


def test_map_at_home_draws_the_house() -> None:
    """Map in a home draws the room named Home."""
    ada = staff("Ada", Rank.RESIDENT)
    ada.room = "ada_home"
    server = FakeServer(people=[ada])
    server.layout = server.layout.with_house("Ada", {house_room_id("Ada"): Room("You are home.", 1, {})})
    process_commands(server, ada, "map")  # type: ignore[arg-type]
    assert "Home" in server.sent[-1]
    process_commands(server, ada, "map please")  # type: ignore[arg-type]
    assert server.sent[-1] == "Syntax: map\n"


def test_a_newbie_has_no_home_command() -> None:
    """Home is resident and above."""
    server, _ = run("home")
    assert server.sent == ["No such command 'home'\n"]


def test_main_from_home_uses_the_start_room_line() -> None:
    """Main from home is the same move it is from any other room."""
    ada = staff("Ada", Rank.RESIDENT)
    ada.room = "ada_home"
    assert ada.player is not None
    ada.player.room = "ada_home"
    bea = FakePlayer(name="Bea", room="ada_home", player=Player("Bea", "ada_home", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server = FakeServer(people=[ada, bea])
    server.layout = server.layout.with_house("Ada", {house_room_id("Ada"): Room("You are home.", 1, {})})
    process_commands(server, ada, "main")  # type: ignore[arg-type]
    assert ada.room == "reception"
    assert any("The desk." in line for line in ada.sent)
    assert "Ada goes to Reception.\n" in bea.sent


def test_who_shows_home() -> None:
    """A resident at home is listed as Home."""
    ada = staff("Ada", Rank.RESIDENT)
    ada.room = "ada_home"
    assert ada.player is not None
    ada.player.room = "ada_home"
    server = FakeServer(people=[ada])
    server.layout = server.layout.with_house("Ada", {house_room_id("Ada"): Room("You are home.", 1, {})})
    process_commands(server, ada, "who")  # type: ignore[arg-type]
    assert any("Home" in line and "Reception" not in line for line in server.sent)


def test_go_from_home_has_no_exit() -> None:
    """A home has no exits."""
    ada = staff("Ada", Rank.RESIDENT)
    ada.room = "ada_home"
    server = FakeServer(people=[ada])
    server.layout = server.layout.with_house("Ada", {house_room_id("Ada"): Room("You are home.", 1, {})})
    process_commands(server, ada, "go n")  # type: ignore[arg-type]
    assert server.sent == ["You cannot go north.\n"]
    assert ada.room == "ada_home"


def test_shout_reaches_a_home_and_say_stays_in_the_office() -> None:
    """Room speech stays put. A shout still arrives."""
    ada = staff("Ada", Rank.RESIDENT)
    ada.room = "ada_home"
    assert ada.player is not None
    ada.player.room = "ada_home"
    ada.player.colour = False
    bea = FakePlayer(name="Bea", room="reception", player=Player("Bea", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT, colour=False))
    server = FakeServer(people=[ada, bea])
    server.layout = server.layout.with_house("Ada", {house_room_id("Ada"): Room("You are home.", 1, {})})
    process_commands(server, bea, "say hello")  # type: ignore[arg-type]
    assert not any("hello" in line for line in ada.sent)
    ada.sent.clear()
    process_commands(server, bea, "shout hello")  # type: ignore[arg-type]
    assert "! Bea shouts 'hello'\n" in ada.sent


def test_center_block_uses_the_widest_line() -> None:
    """Each line shifts by the same pad. A wider block is left as it is."""
    assert center_block("a\nbbb\n", 5) == " a\n bbb\n"
    assert center_block("abcdef\n", 4) == "abcdef\n"


def test_map_centers_on_the_manual_width() -> None:
    """A chosen term width is the width the map is centered in."""
    player = FakePlayer(discovered_columns=40, frame_columns=60)
    server, player = run("map", player)
    width = frame_width(player.discovered_columns, player.frame_columns)
    colour_on = player.player is not None and player.player.colour
    drawn = server.layout.map_text(player.room, width, colour=colour_on)
    assert server.sent[0] == f"\n\n{center_block(drawn, width)}\n"


def test_question_mark_is_help() -> None:
    """Bare help replies Syntax: help <topic>. Bare ? replies Syntax: ?<topic>. ? say and ?say show the say page."""
    listed, _ = run("help")
    assert listed.sent == ["Syntax: help <topic>\n"]
    asked, _ = run("?")
    assert asked.sent == ["Syntax: ?<topic>\n"]
    topic, _ = run("? say")
    topic_page = topic.sent[0]
    assert "Command    : say" in topic_page
    assert "Syntax     : say <message>" in topic_page
    stuck, _ = run("?say")
    stuck_page = stuck.sent[0]
    assert "Command    : say" in stuck_page
    assert "Syntax     : say <message>" in stuck_page


def test_map_leaves_the_current_room_plain_when_colour_is_off() -> None:
    """Colour off keeps the room name, without a green code."""
    player = FakePlayer(player=Player("Ada", "reception", datetime.now(timezone.utc), colour=False))
    server, _ = run("map", player)
    assert "Reception" in server.sent[0]
    assert green("Reception") not in server.sent[0]


def test_map_rejects_extra_text() -> None:
    """Map does not take a remainder."""
    server, _ = run("map please")
    assert server.sent == ["Syntax: map\n"]


def test_newbie_cannot_shout() -> None:
    """A newbie is told shout does not exist. A resident still shouts."""
    when = datetime.now(timezone.utc)
    newbie = FakePlayer(player=Player("Ada", "reception", when, rank=Rank.NEWBIE))
    server, _ = run("shout hi", newbie)
    assert server.sent == ["No such command 'shout'\n"]
    assert server.all_lines == []

    server, _ = run("!hi", newbie)
    assert server.sent == ["No such command '!'\n"]
    assert server.all_lines == []

    server, _ = run("shout", newbie)
    assert server.sent == ["No such command 'shout'\n"]
    server, _ = run("!", newbie)
    assert server.sent == ["No such command '!'\n"]

    resident = FakePlayer(player=Player("Ada", "reception", when, rank=Rank.RESIDENT))
    server, _ = run("shout hi", resident)
    assert server.sent == ["You shout 'hi'\n"]
    assert server.all_lines == [yellow("! Ada shouts 'hi'") + "\n"]

    newbie_words = command_words(Rank.NEWBIE).split()
    assert "shout" not in newbie_words
    assert "!" not in newbie_words
    resident_words = command_words(Rank.RESIDENT).split()
    assert "shout" in resident_words
    assert "!" in resident_words


def test_help_hides_shout_from_a_newbie() -> None:
    """Help for a command above the player's rank is the same as a missing topic."""
    book = HelpBook(entries=(HelpEntry("say", ("'",), "Speak."), HelpEntry("shout", ("!",), "Shout to everyone.")))
    when = datetime.now(timezone.utc)
    newbie = FakePlayer(player=Player("Ada", "reception", when, rank=Rank.NEWBIE))
    server = FakeServer(help=book)
    process_commands(server, newbie, "help shout")  # type: ignore[arg-type]
    assert server.sent == ["help for shout not found\n"]

    server = FakeServer(help=book)
    process_commands(server, newbie, "help !")  # type: ignore[arg-type]
    assert server.sent == ["help for ! not found\n"]

    server = FakeServer(help=book)
    process_commands(server, newbie, "help")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: help <topic>\n"]

    resident = FakePlayer(player=Player("Ada", "reception", when, rank=Rank.RESIDENT))
    server = FakeServer(help=book)
    process_commands(server, resident, "help shout")  # type: ignore[arg-type]
    shout_page = server.sent[0]
    assert "Command    : shout" in shout_page
    assert "Syntax     : shout <message>" in shout_page
    assert "Aliases    : !" in shout_page

    server = FakeServer(help=book)
    process_commands(server, resident, "help")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: help <topic>\n"]


def test_shout_reaches_everyone_and_alias_needs_no_space() -> None:
    """Shout is not limited to the current room. ``!`` needs no space."""
    resident = FakePlayer(player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server, _ = run("shout hello there", resident)
    assert server.sent == ["You shout 'hello there'\n"]
    assert server.room_lines == []
    assert server.all_lines == [yellow("! Ada shouts 'hello there'") + "\n"]

    server, _ = run("!hello", resident)
    assert server.sent == ["You shout 'hello'\n"]
    assert server.all_lines == [yellow("! Ada shouts 'hello'") + "\n"]

    server, _ = run("!", resident)
    assert server.sent == ["Syntax: !<message>\n"]
    assert server.all_lines == []


def test_tell_to_two_people_uses_a_chain() -> None:
    """Two recipients get a chain prefix, and you replaces the reader's name."""
    ada = FakePlayer()
    bea = FakePlayer(name="Bea", room="hall")
    cyd = FakePlayer(name="Cyd", room="hall")
    server = FakeServer(people=[ada, bea, cyd])
    process_commands(server, ada, "tell Bea, Cyd hello")  # type: ignore[arg-type]
    assert ada.sent == ["(1) You tell Bea & Cyd 'hello'\n"]
    assert bea.sent == [green("(1) > Ada tells you & Cyd 'hello'") + "\n"]
    assert cyd.sent == [green("(1) > Ada tells Bea & you 'hello'") + "\n"]
    assert server.room_lines == []
    assert len(server.chains) == 1


def test_chains_lists_only_your_chains_with_aligned_idle() -> None:
    """Idle clocks share a column, and a dash rule sits above and below."""
    ada = FakePlayer()
    bea = FakePlayer(name="Bea", room="hall")
    cyd = FakePlayer(name="Cyd", room="hall")
    dee = FakePlayer(name="Dee", room="hall")
    server = FakeServer(people=[ada, bea, cyd, dee])
    process_commands(server, ada, "tell Bea, Cyd hello")  # type: ignore[arg-type]
    process_commands(server, ada, "tell Bea, Cyd, Dee hello")  # type: ignore[arg-type]
    server.chains[1].last_message_at = datetime.now(timezone.utc) - timedelta(seconds=724)
    ada.sent.clear()
    process_commands(server, ada, "chains")  # type: ignore[arg-type]
    text = ada.sent[0]
    lines = text.split("\n")
    assert lines[0] == "-" * 72
    assert lines[1].startswith("Chain 1: ")
    assert lines[1].rstrip().endswith(green("[ Active ]"))
    assert "Ada, Bea & Cyd" in lines[1]
    assert lines[2].startswith("Chain 2: ")
    assert lines[2].rstrip().endswith(yellow("[00:12:04]"))
    assert lines[3] == "-" * 72
    assert plain_len(lines[1]) == plain_len(lines[2])
    bea.sent.clear()
    process_commands(server, bea, "chains")  # type: ignore[arg-type]
    assert "Chain 1:" in bea.sent[0]
    loner = FakePlayer(name="Zoe", room="hall")
    server.people.append(loner)
    process_commands(server, loner, "chains")  # type: ignore[arg-type]
    assert loner.sent == ["You are not on a chain.\n"]


def test_chains_with_extra_text_is_syntax() -> None:
    """A word that is not leave, remove, or delete is a syntax error."""
    server, ada = run("chains please")
    assert server.sent == ["Syntax: chains [leave <id> [reason]|remove <id> <name> [reason]|delete <id> [reason]]\n"]
    assert ada is not None


def test_chains_leave_tells_the_others() -> None:
    """Leaving a chain of four tells the people who remain."""
    ada = FakePlayer()
    bea = FakePlayer(name="Bea", room="hall")
    cyd = FakePlayer(name="Cyd", room="hall")
    dee = FakePlayer(name="Dee", room="hall")
    server = FakeServer(people=[ada, bea, cyd, dee])
    process_commands(server, ada, "tell Bea, Cyd, Dee hello")  # type: ignore[arg-type]
    for person in (ada, bea, cyd, dee):
        person.sent.clear()
    process_commands(server, dee, "chains leave 1 dinner")  # type: ignore[arg-type]
    assert dee.sent == ["You have left chain 1.\n"]
    assert ada.sent == ["(1) Dee has left the chain. dinner\n"]
    assert len(server.chains) == 1


def test_chains_remove_closes_when_two_remain() -> None:
    """Removing someone from a chain of three closes it."""
    ada = FakePlayer()
    bea = FakePlayer(name="Bea", room="hall")
    cyd = FakePlayer(name="Cyd", room="hall")
    server = FakeServer(people=[ada, bea, cyd])
    process_commands(server, ada, "tell Bea, Cyd hello")  # type: ignore[arg-type]
    for person in (ada, bea, cyd):
        person.sent.clear()
    process_commands(server, ada, "chains remove 1 Bea")  # type: ignore[arg-type]
    line = "(1) Chain closed because Ada removed Bea from the chain.\n"
    assert ada.sent == [line]
    assert bea.sent == [line]
    assert cyd.sent == [line]
    assert server.chains == []


def test_chains_delete_tells_everyone() -> None:
    """Delete removes the chain and tells every member."""
    ada = FakePlayer()
    bea = FakePlayer(name="Bea", room="hall")
    cyd = FakePlayer(name="Cyd", room="hall")
    server = FakeServer(people=[ada, bea, cyd])
    process_commands(server, ada, "tell Bea, Cyd hello")  # type: ignore[arg-type]
    for person in (ada, bea, cyd):
        person.sent.clear()
    process_commands(server, ada, "chains delete 1 done")  # type: ignore[arg-type]
    line = "(1) Ada deleted the chain. done\n"
    assert ada.sent == [line]
    assert bea.sent == [line]
    assert server.chains == []


def test_tell_chain_reaches_the_same_people() -> None:
    """A later tell to the chain id reaches the other members."""
    ada = FakePlayer()
    bea = FakePlayer(name="Bea", room="hall")
    cyd = FakePlayer(name="Cyd", room="hall")
    server = FakeServer(people=[ada, bea, cyd])
    process_commands(server, ada, "tell Bea,Cyd hello")  # type: ignore[arg-type]
    ada.sent.clear()
    bea.sent.clear()
    cyd.sent.clear()
    process_commands(server, bea, "tell 1 yes")  # type: ignore[arg-type]
    assert bea.sent == ["(1) You tell Ada & Cyd 'yes'\n"]
    assert ada.sent == [green("(1) > Bea tells you & Cyd 'yes'") + "\n"]
    assert cyd.sent == [green("(1) > Bea tells Ada & you 'yes'") + "\n"]


def test_remote_and_rthink_use_the_same_chain_names() -> None:
    """Remote and rthink keep their words and add the chain prefix."""
    ada = FakePlayer()
    bea = FakePlayer(name="Bea", room="hall")
    cyd = FakePlayer(name="Cyd", room="hall")
    bea.player.auto_reply = "later"
    server = FakeServer(people=[ada, bea, cyd])
    process_commands(server, ada, "remote Bea, Cyd smiles")  # type: ignore[arg-type]
    assert ada.sent[0] == "(1) You emote: Ada smiles to Bea & Cyd\n"
    assert bea.sent[0] == green("(1) > Ada smiles to you & Cyd") + "\n"
    assert "[ Auto Reply from Bea ] later\n" in ada.sent
    assert cyd.sent == [green("(1) > Ada smiles to Bea & you") + "\n"]
    ada.sent.clear()
    bea.sent.clear()
    cyd.sent.clear()
    process_commands(server, ada, "rthink 1 hmm")  # type: ignore[arg-type]
    assert ada.sent == [
        "(1) You think . o O ( hmm ) to Bea & Cyd\n",
        "[ Auto Reply from Bea ] later\n",
    ]
    assert bea.sent[0] == green("(1) > Ada thinks . o O ( hmm ) to you & Cyd") + "\n"
    assert cyd.sent == [green("(1) > Ada thinks . o O ( hmm ) to Bea & you") + "\n"]


def test_tell_reaches_a_named_player_in_any_room() -> None:
    """Tell finds a player by name. A missing or roomless player is not found."""
    bea = FakePlayer(name="Bea", room="hall")
    server = FakeServer(people=[bea])
    process_commands(server, FakePlayer(), "tell Bea hello there")  # type: ignore[arg-type]
    assert server.sent == [
        "You tell Bea 'hello there'\n",
        green("> Ada tells you 'hello there'") + "\n",
    ]
    assert server.room_lines == []
    assert server.all_lines == []

    missing, _ = run("tell Bea hello")
    assert missing.sent == ["Player Bea not found.\n"]

    waiting = FakePlayer(name="Bea", room=None)
    server = FakeServer(people=[waiting])
    process_commands(server, FakePlayer(), "tell Bea hello")  # type: ignore[arg-type]
    assert server.sent == ["Player Bea not found.\n"]

    bare, _ = run("tell Bea")
    assert bare.sent == ["Syntax: tell <name> <message>\n"]

    server = FakeServer(people=[bea])
    process_commands(server, FakePlayer(), ".Bea hello")  # type: ignore[arg-type]
    assert server.sent == [
        "You tell Bea 'hello'\n",
        green("> Ada tells you 'hello'") + "\n",
    ]
    server.sent.clear()
    process_commands(server, FakePlayer(), "> Bea hello")  # type: ignore[arg-type]
    assert server.sent == [
        "You tell Bea 'hello'\n",
        green("> Ada tells you 'hello'") + "\n",
    ]
    dotted, _ = run(".")
    assert dotted.sent == ["Syntax: .<name> <message>\n"]


def test_rthink_thinks_to_one_named_player() -> None:
    """Rthink uses the think words and reaches one player, the way remote does."""
    bea = FakePlayer(name="Bea", room="hall")
    bea.player.auto_reply = "later"
    server = FakeServer(people=[bea])
    process_commands(server, FakePlayer(), "rthink Bea hmm")  # type: ignore[arg-type]
    assert server.sent == [
        "You think . o O ( hmm ) to Bea\n",
        green("> Ada thinks . o O ( hmm ) to you") + "\n",
        "[ Auto Reply from Bea ] later\n",
    ]
    assert server.room_lines == []

    missing, _ = run("rthink Bea hmm")
    assert missing.sent == ["Player Bea not found.\n"]

    bare, _ = run("rthink Bea")
    assert bare.sent == ["Syntax: rthink <name> <message>\n"]

    server = FakeServer()
    ada = FakePlayer()
    server.people = [ada]
    process_commands(server, ada, "rthink Ada hmm")  # type: ignore[arg-type]
    assert ada.sent == ["Talking to yourself is the first sign of madness.\n"]


def test_remote_emotes_to_one_named_player() -> None:
    """Remote emotes to one player. A comma and < are the same command."""
    bea = FakePlayer(name="Bea", room="hall")
    server = FakeServer(people=[bea])
    process_commands(server, FakePlayer(), "remote Bea smiles")  # type: ignore[arg-type]
    assert server.sent == [
        "You emote: Ada smiles to Bea\n",
        green("> Ada smiles to you") + "\n",
    ]
    assert server.room_lines == []
    assert server.all_lines == []

    missing, _ = run("remote Bea smiles")
    assert missing.sent == ["Player Bea not found.\n"]

    bare, _ = run("remote Bea")
    assert bare.sent == ["Syntax: remote <name> <message>\n"]

    server = FakeServer(people=[bea])
    process_commands(server, FakePlayer(), ",Bea smiles")  # type: ignore[arg-type]
    assert server.sent == [
        "You emote: Ada smiles to Bea\n",
        green("> Ada smiles to you") + "\n",
    ]
    server.sent.clear()
    process_commands(server, FakePlayer(), "< Bea smiles")  # type: ignore[arg-type]
    assert server.sent == [
        "You emote: Ada smiles to Bea\n",
        green("> Ada smiles to you") + "\n",
    ]
    comma, _ = run(",")
    assert comma.sent == ["Syntax: ,<name> <message>\n"]


def test_tell_to_yourself_is_refused() -> None:
    """A tell to the speaker is not delivered and has no auto-reply."""
    server = FakeServer()
    ada = FakePlayer()
    ada.player.auto_reply = "later"
    server.people = [ada]
    command_tell(server, ada, Command("tell", command_tell, False, "tell <name> <message>"), "Ada hi")
    assert ada.sent == ["Talking to yourself is the first sign of madness.\n"]


def test_remote_alias_to_yourself_is_refused() -> None:
    """A remote alias uses the same sentence."""
    server = FakeServer()
    ada = FakePlayer()
    server.people = [ada]
    command_remote(server, ada, Command("<", command_remote, True, "remote <name> <message>"), "ada waves")
    assert ada.sent == ["Talking to yourself is the first sign of madness.\n"]


def test_auto_reply_comes_back_to_the_sender(tmp_path: Path) -> None:
    """Tell and remote still arrive, and the sender gets the reply."""
    bea_user = Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    bea_user.auto_reply = "In a meeting"
    bea = FakePlayer(name="Bea", player=bea_user)
    ada = FakePlayer(player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server = FakeServer(people=[ada, bea])
    process_commands(server, ada, "tell Bea hello")  # type: ignore[arg-type]
    assert any(line == "[ Auto Reply from Bea ] In a meeting\n" for line in server.sent)
    assert bea.sent[-1].endswith("tells you 'hello'\n") or "tells you 'hello'" in "".join(bea.sent)

    server.sent.clear()
    ada.sent.clear()
    bea.sent.clear()
    process_commands(server, ada, "remote Bea smiles")  # type: ignore[arg-type]
    assert any(line == "[ Auto Reply from Bea ] In a meeting\n" for line in server.sent)
    assert green("> Ada smiles to you") + "\n" in bea.sent

    server.sent.clear()
    ada.sent.clear()
    bea.sent.clear()
    process_commands(server, ada, "set auto_reply busy")  # type: ignore[arg-type]
    assert server.sent == ["Auto-reply set.\n"]
    assert ada.player is not None
    assert ada.player.auto_reply == "busy"

    server.sent.clear()
    ada.sent.clear()
    bea.sent.clear()
    bea_user.auto_reply = ""
    process_commands(server, ada, "set auto_reply")  # type: ignore[arg-type]
    assert server.sent == ["Auto-reply cleared.\n"]
    assert ada.player is not None
    assert ada.player.auto_reply == ""

    server.sent.clear()
    ada.sent.clear()
    bea.sent.clear()
    bea_user.auto_reply = "Away"
    process_commands(server, bea, "set auto_reply")  # type: ignore[arg-type]
    assert bea_user.auto_reply == ""
    process_commands(server, ada, "tell Bea ping")  # type: ignore[arg-type]
    assert not any(line.startswith("[ Auto Reply ]") for line in server.sent)

    save(tmp_path, "Ada", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.RESIDENT)
    server.people = [ada]
    path = player_path(tmp_path, "Ada")
    process_commands(server, ada, "set auto_reply out")  # type: ignore[arg-type]
    assert "auto_reply" not in path.read_text(encoding="utf-8")

    server.sent.clear()
    ada.sent.clear()
    ada.player.auto_reply = "Echo"
    server.people = [ada]
    process_commands(server, ada, "tell Ada hello")  # type: ignore[arg-type]
    assert not any(line.startswith("[ Auto Reply ]") for line in server.sent)


def test_a_received_tell_and_remote_are_green() -> None:
    """The recipient's line is green. The sender's copy is plain."""
    bea = FakePlayer(name="Bea", room="hall")
    server = FakeServer(people=[bea])
    process_commands(server, FakePlayer(), "tell Bea hello")  # type: ignore[arg-type]
    assert server.sent == [
        "You tell Bea 'hello'\n",
        green("> Ada tells you 'hello'") + "\n",
    ]
    server.sent.clear()
    process_commands(server, FakePlayer(), "remote Bea smiles")  # type: ignore[arg-type]
    assert server.sent == [
        "You emote: Ada smiles to Bea\n",
        green("> Ada smiles to you") + "\n",
    ]
    assert bea.player is not None
    bea.player.colour = False
    server.sent.clear()
    process_commands(server, FakePlayer(), "tell Bea hello")  # type: ignore[arg-type]
    assert server.sent == ["You tell Bea 'hello'\n", "> Ada tells you 'hello'\n"]


def test_room_and_shout_prefixes_respect_colour_off() -> None:
    """Prefixed room and shout lines stay plain when the recipient's colour is off."""
    when = datetime.now(timezone.utc)
    bea = FakePlayer(name="Bea", room="reception")
    bea.player.colour = False
    server = FakeServer(people=[bea])
    process_commands(server, FakePlayer(), "say hello")  # type: ignore[arg-type]
    assert server.room_lines == ["- Ada says 'hello'\n"]
    server.room_lines.clear()
    process_commands(server, FakePlayer(), ":waves")  # type: ignore[arg-type]
    assert server.room_lines == ["- Ada waves\n"]
    resident = FakePlayer(player=Player("Ada", "reception", when, rank=Rank.RESIDENT))
    server = FakeServer(people=[bea, resident])
    process_commands(server, resident, "shout hi")  # type: ignore[arg-type]
    assert server.all_lines == ["! Ada shouts 'hi'\n"]


def test_paint_staff_colours_when_colour_is_on() -> None:
    """Staff lines are cyan when colour is on and plain when it is off."""
    when = datetime.now(timezone.utc)
    on = FakePlayer(player=Player("Ada", "hall", when, rank=Rank.SUPER_USER))
    off = FakePlayer(player=Player("Bea", "hall", when, rank=Rank.SUPER_USER))
    off.player.colour = False
    line = "< SU > Ada says 'hello'\n"
    assert paint_staff(on, line) == cyan("< SU > Ada says 'hello'") + "\n"
    assert paint_staff(off, line) == line


def test_testcard_shows_every_foreground_and_background() -> None:
    """Testcard paints each colour and stays on this screen."""
    from lupaxa.chat_server import colour

    foreground = (colour.black, colour.red, colour.green, colour.yellow, colour.blue, colour.magenta, colour.cyan, colour.white, colour.light_black, colour.light_red, colour.light_green, colour.light_yellow, colour.light_blue, colour.light_magenta, colour.light_cyan, colour.light_white)  # fmt: skip  # noqa: E501
    background = (colour.on_black, colour.on_red, colour.on_green, colour.on_yellow, colour.on_blue, colour.on_magenta, colour.on_cyan, colour.on_white, colour.on_light_black, colour.on_light_red, colour.on_light_green, colour.on_light_yellow, colour.on_light_blue, colour.on_light_magenta, colour.on_light_cyan, colour.on_light_white)  # fmt: skip  # noqa: E501
    server, _ = run("testcard")
    page = colour.testcard()
    lines = page.splitlines()
    assert server.sent == [page]
    assert server.room_lines == []
    assert lines[0].startswith("+")
    assert lines[-1] == lines[0]
    assert len({plain_len(line) for line in lines}) == 1
    inner = lines[1:-1]
    assert inner[0].startswith("|")
    assert "Foreground" in inner[0]
    assert "Background" in inner[0]
    assert len(inner) == len(foreground) + 1
    for ink, paper, line in zip(foreground, background, inner[1:], strict=True):
        assert ink("sample") in line
        assert paper(" sample ") in line
        assert line.index(ink("sample")) < line.index(paper(" sample "))
    extra, _ = run("testcard please")
    assert extra.sent == ["Syntax: testcard\n"]


def test_cls_clears_the_screen() -> None:
    """Cls clears only this screen and leaves the cursor on this row."""
    from colorama.ansi import clear_screen

    server, _ = run("cls")
    assert server.sent == [clear_screen() + "\r"]
    assert server.room_lines == []
    extra, _ = run("cls please")
    assert extra.sent == ["Syntax: cls\n"]


def test_colour_helpers_cover_the_colorama_palette() -> None:
    """Every foreground, background, and effect wraps with its colorama code."""
    from colorama import Back, Fore, Style
    from colorama.ansi import BEL, code_to_chars

    from lupaxa.chat_server import colour

    painted = (
        (colour.black, Fore.BLACK),
        (colour.red, Fore.RED),
        (colour.green, Fore.GREEN),
        (colour.yellow, Fore.YELLOW),
        (colour.blue, Fore.BLUE),
        (colour.magenta, Fore.MAGENTA),
        (colour.cyan, Fore.CYAN),
        (colour.white, Fore.WHITE),
        (colour.light_black, Fore.LIGHTBLACK_EX),
        (colour.light_red, Fore.LIGHTRED_EX),
        (colour.light_green, Fore.LIGHTGREEN_EX),
        (colour.light_yellow, Fore.LIGHTYELLOW_EX),
        (colour.light_blue, Fore.LIGHTBLUE_EX),
        (colour.light_magenta, Fore.LIGHTMAGENTA_EX),
        (colour.light_cyan, Fore.LIGHTCYAN_EX),
        (colour.light_white, Fore.LIGHTWHITE_EX),
        (colour.on_black, Back.BLACK),
        (colour.on_red, Back.RED),
        (colour.on_green, Back.GREEN),
        (colour.on_yellow, Back.YELLOW),
        (colour.on_blue, Back.BLUE),
        (colour.on_magenta, Back.MAGENTA),
        (colour.on_cyan, Back.CYAN),
        (colour.on_white, Back.WHITE),
        (colour.on_light_black, Back.LIGHTBLACK_EX),
        (colour.on_light_red, Back.LIGHTRED_EX),
        (colour.on_light_green, Back.LIGHTGREEN_EX),
        (colour.on_light_yellow, Back.LIGHTYELLOW_EX),
        (colour.on_light_blue, Back.LIGHTBLUE_EX),
        (colour.on_light_magenta, Back.LIGHTMAGENTA_EX),
        (colour.on_light_cyan, Back.LIGHTCYAN_EX),
        (colour.on_light_white, Back.LIGHTWHITE_EX),
        (colour.dim, Style.DIM),
        (colour.normal, Style.NORMAL),
        (colour.flash, code_to_chars(5)),
    )
    for paint_text, code in painted:
        assert paint_text("Hi") == f"{code}Hi{Style.RESET_ALL}"
        assert plain_len(paint_text("Hi")) == 2
    assert colour.bright("Hi") == f"{Style.BRIGHT}{Fore.WHITE}Hi{Style.RESET_ALL}"
    assert colour.bell("Hi") == f"Hi{BEL}"


def test_paint_admin_is_red_when_colour_is_on() -> None:
    """Admin channel lines are red with colour on and plain with colour off."""
    line = "[ Admin ] Ada says 'hello'\n"
    on = staff("Ada", Rank.ADMIN)
    off = staff("Bea", Rank.ADMIN)
    assert off.player is not None
    off.player.colour = False
    assert paint_admin(on, line) == red(line.rstrip("\n")) + "\n"
    assert paint_admin(off, line) == line


def test_broadcast_admin_reaches_only_admins() -> None:
    """A super user and a resident do not receive the admin channel."""
    from lupaxa.chat_server.broadcast import broadcast_admin

    ada = staff("Ada", Rank.ADMIN)
    sue = staff("Sue", Rank.SUPER_USER)
    bea = staff("Bea", Rank.RESIDENT)
    server = FakeServer(people=[ada, sue, bea])
    broadcast_admin(server, "[ Admin ] hello\n")  # type: ignore[arg-type]
    assert ada.sent == [paint_admin(ada, "[ Admin ] hello\n")]
    assert sue.sent == []
    assert bea.sent == []


def test_multiple_matches_uses_ampersand_before_the_last_name() -> None:
    """Two names use &. More names use commas, and & before the last."""
    assert multiple_matches_message(["Admin", "Adam"]) == "multiple Matches: Adam & Admin\n"
    assert multiple_matches_message(["Adept", "Adam", "Admin"]) == "multiple Matches: Adam, Adept & Admin\n"


def test_match_name_prefers_an_exact_name_over_a_prefix() -> None:
    """Adam is exact. Ad is the one prefix of Adam. Ad matches both Adam and Admin."""
    assert match_name("Adam", ["Adam", "Adamant"]) == ("Adam", None)
    assert match_name("Ad", ["Adam"]) == ("Adam", None)
    chosen, error = match_name("Ad", ["Adam", "Admin"])
    assert chosen is None
    assert error == "multiple Matches: Adam & Admin\n"
    assert match_name("Zed", ["Adam"]) == (None, None)


def test_tell_uses_one_partial_name_and_refuses_several() -> None:
    """Tell ad reaches Adam. Tell a names both Adam and Admin."""
    adam = FakePlayer(name="Adam", room="hall")
    server = FakeServer(people=[adam])
    process_commands(server, FakePlayer(), "tell ad hello")  # type: ignore[arg-type]
    assert server.sent == [
        "You tell Adam 'hello'\n",
        green("> Ada tells you 'hello'") + "\n",
    ]

    admin = FakePlayer(name="Admin", room="hall")
    server = FakeServer(people=[adam, admin])
    process_commands(server, FakePlayer(), "tell a hello")  # type: ignore[arg-type]
    assert server.sent == ["multiple Matches: Adam & Admin\n"]


def test_examine_partial_finds_one_logged_out_player(tmp_path: Path) -> None:
    """A unique prefix of a saved name examines that logged-out player."""
    save(tmp_path, "Adam", Rank.RESIDENT)
    server = login_host(tmp_path, super_admin="Zed")
    process_commands(server, FakePlayer(), "examine ad")  # type: ignore[arg-type]
    assert server.sent[0].splitlines()[1].strip() == "Adam"
    assert "Player ad not found." not in server.sent[0]


def test_examine_refuses_several_partial_names(tmp_path: Path) -> None:
    """Online and logged-out prefixes are one list."""
    save(tmp_path, "Admin", Rank.RESIDENT)
    adam = FakePlayer(name="Adam", room="hall")
    server = login_host(tmp_path, super_admin="Zed")
    server.people = [adam]
    process_commands(server, FakePlayer(), "examine a")  # type: ignore[arg-type]
    assert server.sent == ["multiple Matches: Adam & Admin\n"]


def test_permanent_commands_do_not_accept_a_prefix(tmp_path: Path) -> None:
    """Resident, promote, demote, disconnect, and nuke need the whole name."""
    save(tmp_path, "Adam", Rank.RESIDENT)
    adam = newbie("Adam")
    server = login_host(tmp_path)
    server.people = [adam]
    super_user = staff("Ada", Rank.SUPER_USER)
    process_commands(server, super_user, "resident Ad")  # type: ignore[arg-type]
    assert server.sent[-1] == "No such player 'Ad'.\n"
    process_commands(server, super_user, "disconnect Ad rude")  # type: ignore[arg-type]
    assert server.sent[-1] == "No such player 'Ad'.\n"
    assert server.dropped_clients == []
    process_commands(server, super_user, "nuke Ad gone")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Adam")) is not None
    admin = staff("Boss", Rank.ADMIN)
    process_commands(server, admin, "promote Ad")  # type: ignore[arg-type]
    assert server.sent[-1] == "No such player 'Ad'.\n"
    process_commands(server, admin, "demote Ad")  # type: ignore[arg-type]
    assert server.sent[-1] == "Player Ad not found.\n"


def test_idle_clock_is_active_until_five_minutes() -> None:
    """Under five minutes is Active. The clock starts at five minutes."""
    assert idle_clock(0) == "[ Active ]"
    assert idle_clock(299) == "[ Active ]"
    assert idle_clock(300) == "[00:05:00]"
    assert idle_clock(1799) == "[00:29:59]"
    assert idle_clock(1800) == "[00:30:00]"
    assert idle_clock(3599) == "[00:59:59]"
    assert idle_clock(3600) == "[--:--:--]"
    assert idle_clock(100 * 3600) == "[--:--:--]"


def test_idle_cell_uses_green_yellow_and_red() -> None:
    """Active is green, five to thirty minutes is yellow, then red, then light black."""
    from lupaxa.chat_server.colour import green, light_black, paint_idle, red, yellow

    assert paint_idle(299, True) == green("[ Active ]")
    assert paint_idle(300, True) == yellow("[00:05:00]")
    assert paint_idle(1799, True) == yellow("[00:29:59]")
    assert paint_idle(1800, True) == red("[00:30:00]")
    assert paint_idle(3599, True) == red("[00:59:59]")
    assert paint_idle(3600, True) == light_black("[--:--:--]")
    assert paint_idle(300, False) == "[00:05:00]"
    assert paint_idle(3600, False) == "[--:--:--]"


def test_super_admin_rank_is_light_blue() -> None:
    """A promoted admin stays red. The config Super Admin is light blue."""
    from lupaxa.chat_server.colour import light_blue
    from lupaxa.chat_server.ranks import shown_rank_cell

    assert shown_rank(Rank.ADMIN, True) == red("Admin")
    assert shown_rank(Rank.SUPER_ADMIN, True) == light_blue("Super Admin")
    assert shown_rank(Rank.SUPER_ADMIN, False) == "Super Admin"
    assert shown_rank_cell(Rank.ADMIN, True) == red("[ Admin ]")
    assert shown_rank_cell(Rank.SUPER_ADMIN, True) == light_blue("( Super Admin )")


def test_frame_width_uses_manual_then_discovered_then_eighty() -> None:
    """A manual width wins. A narrow discovery is kept. Otherwise the frame is 80."""
    assert frame_width(None, None) == 80
    assert frame_width(40, None) == 40
    assert frame_width(200, None) == 200
    assert frame_width(200, 70) == 70
    assert frame_width(None, 70) == 70


def test_cpu_phrase_keeps_the_fractional_second() -> None:
    """CPU time uses words and keeps a fractional second."""
    assert cpu_phrase(0) == "0 seconds"
    assert cpu_phrase(-1) == "0 seconds"
    assert cpu_phrase(1) == "1 second"
    assert cpu_phrase(34) == "34 seconds"
    assert cpu_phrase(34.1) == "34.1 seconds"
    assert cpu_phrase(59.96) == "1 minute"
    assert cpu_phrase(180) == "3 minutes"
    assert cpu_phrase(182.4) == "3 minutes, 2.4 seconds"
    assert cpu_phrase(3600) == "1 hour"
    assert cpu_phrase(3602.4) == "1 hour, 2.4 seconds"
    assert cpu_phrase(3661.5) == "1 hour, 1 minute, 1.5 seconds"
    assert cpu_phrase(86400) == "1 day"
    assert cpu_phrase(90061.5) == "1 day, 1 hour, 1 minute, 1.5 seconds"


def test_time_frame_is_the_boot_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """Time prints the clock, the boot time, and the uptime in a snug box."""
    fixed = datetime(2026, 10, 12, 13, 45, 3, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return fixed

    monkeypatch.setattr("lupaxa.chat_server.newbie.datetime", Clock)
    server = FakeServer()
    server.started_at = fixed - timedelta(hours=2, minutes=5)
    player = FakePlayer(discovered_columns=80, frame_columns=None)
    process_commands(server, player, "time")  # type: ignore[arg-type]
    expected = """\
+-------------------------------------------------------+
| Current time : Monday 12th October 2026, 13:45:03 UTC |
| Booted at    : Monday 12th October 2026, 11:40:03 UTC |
| Uptime       : 2 hours, 5 minutes                     |
+-------------------------------------------------------+
"""
    assert server.sent == [expected]
    wide = FakePlayer(discovered_columns=200, frame_columns=120)
    server.sent.clear()
    process_commands(server, wide, "time")  # type: ignore[arg-type]
    assert server.sent == [expected]
    server.sent.clear()
    process_commands(server, player, "time please")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: time\n"]


def test_time_frame_shows_rebooted_at_only_after_a_reboot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rebooted at appears under Booted at, and only when a reboot has happened."""
    fixed = datetime(2026, 10, 12, 13, 45, 3, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return fixed

    monkeypatch.setattr("lupaxa.chat_server.newbie.datetime", Clock)
    server = FakeServer()
    server.started_at = fixed - timedelta(hours=2, minutes=5)
    server.rebooted_at = datetime(2026, 10, 12, 12, 10, 3, tzinfo=timezone.utc)
    player = FakePlayer()
    process_commands(server, player, "time")  # type: ignore[arg-type]
    expected = """\
+-------------------------------------------------------+
| Current time : Monday 12th October 2026, 13:45:03 UTC |
| Booted at    : Monday 12th October 2026, 11:40:03 UTC |
| Rebooted at  : Monday 12th October 2026, 12:10:03 UTC |
| Uptime       : 2 hours, 5 minutes                     |
+-------------------------------------------------------+
"""
    assert server.sent == [expected]


def test_time_frame_widens_for_a_longer_value() -> None:
    """The box follows the longest row and does not wrap the clock."""
    clock = "Monday 12th October 2026, 13:45:03 UTC"
    short = time_frame(clock, "Monday 12th October 2026, 11:40:03 UTC", "2 hours, 5 minutes")
    long = time_frame(clock, "Monday 12th October 2026, 11:40:03 UTC", "2 hours, 5 minutes, and more words here")
    assert len(long.splitlines()[0]) > len(short.splitlines()[0])
    assert clock in long.splitlines()[1]


def test_term_width_sets_and_clears_a_manual_width() -> None:
    """A manual width is kept, refused outside 60 and the ceiling, and cleared by auto."""
    ada = FakePlayer(discovered_columns=100, discovered_rows=24, frame_columns=None)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "term_width")  # type: ignore[arg-type]
    assert server.sent == ["  Terminal Size  : 100x24 (100x24 discovered)\n"]
    server.sent.clear()
    process_commands(server, ada, "term_width 70")  # type: ignore[arg-type]
    assert ada.frame_columns == 70
    assert server.sent == ["Width set to 70.\n"]
    server.sent.clear()
    process_commands(server, ada, "term_width 59")  # type: ignore[arg-type]
    assert ada.frame_columns == 70
    assert server.sent == ["Width must be at least 60.\n"]
    server.sent.clear()
    process_commands(server, ada, "term_width 101")  # type: ignore[arg-type]
    assert server.sent == ["Width cannot be wider than 100.\n"]
    server.sent.clear()
    process_commands(server, ada, "term_width auto")  # type: ignore[arg-type]
    assert ada.frame_columns is None
    assert server.sent == ["Width set to automatic.\n"]
    server.sent.clear()
    process_commands(server, ada, "term_width please")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: term_width <width>|auto\n"]
    server.sent.clear()
    process_commands(server, ada, "term_width ²")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: term_width <width>|auto\n"]
    pending_server = FakeServer()
    bare = FakePlayer(discovered_columns=None, discovered_rows=None, frame_columns=None)
    process_commands(pending_server, bare, "term_width")  # type: ignore[arg-type]
    assert pending_server.sent == ["  Terminal Size  : 80  (Pending)\n"]


def test_term_width_is_not_saved_and_a_narrow_window_rejects_numbers(tmp_path: Path) -> None:
    """The manual width is not a player-file field. Below 60 only auto works."""
    save_player(tmp_path, SavedPlayer("Ada", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    user = Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    ada = FakePlayer(name="Ada", player=user, discovered_columns=40, discovered_rows=12)
    server.people = [ada]
    process_commands(server, ada, "term_width 60")  # type: ignore[arg-type]
    assert ada.frame_columns is None
    assert server.sent == ["Width cannot be wider than 40.\n"]
    text = (tmp_path / "ada.yml").read_text(encoding="utf-8")
    assert "frame_columns" not in text
    assert "term_width" not in text


def test_a_long_name_grows_the_frame() -> None:
    """A name that does not fit widens the rule instead of being cut."""
    text = list_frame(("Name", "Room", "Idle"), [("N" * 80, "Hall", "")], 72)
    assert len(text.splitlines()[0]) > 72
    assert "N" * 80 in text


def test_who_lists_players_rooms_and_idle() -> None:
    """Who lists name, room, and idle, and skips someone who is not in a room."""
    when = datetime.now(timezone.utc)
    ada_user = Player("Ada", "reception", when, idle_seconds=10)
    bea_user = Player("Bea", "hall", when, idle_seconds=300)
    server = FakeServer(people=[FakePlayer(name="Bea", room="hall", player=bea_user), FakePlayer(name="Ada", room="reception", player=ada_user), FakePlayer(name="Cyd", room=None),])  # fmt: skip  # noqa: E501
    asker = FakePlayer()
    assert asker.discovered_columns == 72
    process_commands(server, asker, "who")  # type: ignore[arg-type]
    text = server.sent[0]
    lines = text.splitlines()
    assert lines[0] == "-" * 72
    assert lines[-1] == "-" * 72
    assert "Connected:" not in text
    assert "[00:05:00]" in text
    assert "00:00:10" not in text
    assert "[ Active ]" in text

    server = FakeServer(people=[FakePlayer(name="Cyd", room=None)])
    process_commands(server, FakePlayer(), "who")  # type: ignore[arg-type]
    assert server.sent == [" -=> There is currently no one connected\n"]

    extra, _ = run("who please")
    assert extra.sent == ["Syntax: who\n"]


def test_who_lists_a_player_who_is_alone() -> None:
    """A player in a room is listed, including when they are the only one."""
    when = datetime.now(timezone.utc)
    ada = FakePlayer(name="Ada", room="reception", player=Player("Ada", "reception", when))
    server = FakeServer(people=[ada])
    process_commands(server, ada, "who")  # type: ignore[arg-type]
    assert "Ada" in server.sent[0]
    assert server.sent[0] != " -=> There is currently no one connected\n"


def test_w_lists_the_same_players_as_who() -> None:
    """W is who. wplease is not. w please is the who syntax."""
    ada = FakePlayer()
    bea = FakePlayer(name="Bea", room="hall", player=Player("Bea", "hall", datetime.now(timezone.utc)))
    server = FakeServer(people=[ada, bea])
    process_commands(server, ada, "who")  # type: ignore[arg-type]
    who_text = server.sent[0]
    server.sent.clear()
    process_commands(server, ada, "w")  # type: ignore[arg-type]
    assert server.sent == [who_text]
    server.sent.clear()
    process_commands(server, ada, "wplease")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'wplease'\n"]
    server.sent.clear()
    process_commands(server, ada, "w please")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: w\n"]


def test_enter_at_the_play_prompt_does_not_clear_idle() -> None:
    """An empty line shows the prompt again and leaves idle alone."""
    user = Player("Ada", "hall", datetime.now(timezone.utc), idle_seconds=40)
    ada = FakePlayer(player=user)
    server = FakeServer(people=[ada])
    process_input(server, ada, "")  # type: ignore[arg-type]
    assert user.idle_seconds == 40
    assert server.sent == ["What? > "]
    assert "No such command" not in "".join(server.sent)


def test_confirm_password_starts_after_a_blank_line(tmp_path: Path) -> None:
    """The confirm prompt is preceded by a blank line."""
    assert confirm_password_prompt() == "\n\nConfirm password: "


def test_look_repeats_the_room_brief() -> None:
    """Look shows the room brief and does not tell the room anyone arrived."""
    ada = FakePlayer()
    bea = FakePlayer(name="Bea", room="reception")
    server = FakeServer(people=[ada, bea])
    process_commands(server, ada, "look")  # type: ignore[arg-type]
    assert server.sent == [server.layout.brief("reception", [])]
    assert server.room_lines == []

    extra, _ = run("look please")
    assert extra.sent == ["Syntax: look\n"]
    server.sent.clear()
    process_commands(server, ada, "l")  # type: ignore[arg-type]
    assert server.sent == [server.layout.brief("reception", [])]
    glued, _ = run("lplease")
    assert glued.sent == ["No such command 'lplease'\n"]

    newbie_words = command_words(Rank.NEWBIE).split()
    assert "look" in newbie_words
    assert "l" in newbie_words


def test_join_refuses_someone_elses_home() -> None:
    """A joiner stays put and is told they are not permitted."""
    ada = staff("Ada", Rank.RESIDENT)
    ada.room = "ada_home"
    assert ada.player is not None
    ada.player.room = "ada_home"
    bea = FakePlayer(name="Bea", room="hall", player=Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server = FakeServer(people=[ada, bea])
    server.layout = server.layout.with_house("Ada", {house_room_id("Ada"): Room("You are home.", 1, {})})
    process_commands(server, bea, "join Ada")  # type: ignore[arg-type]
    assert bea.room == "hall"
    assert server.sent == ["You are not permitted to Ada's home.\n"]
    assert ada.sent == []

    server.sent.clear()
    process_commands(server, ada, "join Ada")  # type: ignore[arg-type]
    assert ada.room == "ada_home"
    assert server.sent == ["You are already in Home.\n"]


def test_join_moves_to_that_players_room() -> None:
    """Join follows a player when the destination has space."""
    bea = FakePlayer(name="Bea", room="hall")
    server = FakeServer(people=[bea])
    ada = FakePlayer()
    process_commands(server, ada, "join Bea")  # type: ignore[arg-type]
    assert ada.room == "hall"
    assert server.room_lines == ["Ada goes to join Bea.\n", "Ada arrives.\n"]
    assert server.sent[0] == server.layout.brief("hall", [])

    stay = FakeServer(people=[FakePlayer(name="Bea", room="reception")])
    ada = FakePlayer()
    process_commands(stay, ada, "join Bea")  # type: ignore[arg-type]
    assert ada.room == "reception"
    assert stay.sent == ["You are already in Reception.\n"]
    assert stay.room_lines == []

    full = FakeServer(people=[bea], occupied=4)
    stuck = FakePlayer()
    process_commands(full, stuck, "join Bea")  # type: ignore[arg-type]
    assert stuck.room == "reception"
    assert full.sent == ["Hall is full.\n"]
    assert full.room_lines == []

    missing, player = run("join Nobody")
    assert missing.sent == ["Player Nobody not found.\n"]
    assert player.room == "reception"


def test_join_uses_one_partial_name_and_refuses_several() -> None:
    """Join be follows Bea. Join b names both Bea and Ben."""
    bea = FakePlayer(name="Bea", room="hall")
    server = FakeServer(people=[bea])
    ada = FakePlayer()
    process_commands(server, ada, "join be")  # type: ignore[arg-type]
    assert ada.room == "hall"

    ben = FakePlayer(name="Ben", room="hall")
    server = FakeServer(people=[bea, ben])
    ada = FakePlayer()
    process_commands(server, ada, "join b")  # type: ignore[arg-type]
    assert server.sent == ["multiple Matches: Bea & Ben\n"]
    assert ada.room == "reception"


def test_main_moves_to_the_start_room() -> None:
    """Main announces the leave and the arrive, then shows the room."""
    ada = FakePlayer(room="hall")
    server = FakeServer(people=[ada])
    process_commands(server, ada, "main")  # type: ignore[arg-type]
    assert ada.room == "reception"
    assert server.room_lines == ["Ada goes to Reception.\n", "Ada arrives.\n"]
    assert server.sent[0] == server.layout.brief("reception", [])


def test_main_does_nothing_when_already_there() -> None:
    """Main sends nothing when the player is already in the start room."""
    server, ada = run("main")
    assert ada.room == "reception"
    assert server.sent == []
    assert server.room_lines == []


def test_main_stays_when_the_start_room_is_full() -> None:
    """A full start room keeps the player where they are."""
    ada = FakePlayer(room="hall")
    server = FakeServer(people=[ada], occupied=4)
    process_commands(server, ada, "main")  # type: ignore[arg-type]
    assert ada.room == "hall"
    assert server.sent == ["Reception is full.\n"]
    assert server.room_lines == []


def test_main_with_extra_text_shows_the_syntax() -> None:
    """Extra text does not move the player."""
    server, player = run("main please")
    assert server.sent == ["Syntax: main\n"]
    assert player.room == "reception"


def test_main_uses_the_start_room_display_name() -> None:
    """The leave line uses the display name of config.start."""
    layout = Layout(rooms={"lounge": Room("A lounge.", 4, {}), "the_bar": Room("A bar.", 4, {})})
    ada = FakePlayer(room="lounge")
    server = FakeServer(people=[ada], layout=layout)
    server.config = replace(server.config, start="the_bar")
    process_commands(server, ada, "main")  # type: ignore[arg-type]
    assert ada.room == "the_bar"
    assert server.room_lines == ["Ada goes to The Bar.\n", "Ada arrives.\n"]


class ClosedServer(FakeServer):
    """A server whose send fails, so the arrive line must not be sent."""

    def send(self, client: FakePlayer, text: str) -> bool:
        if output_hidden(client):
            return True
        self.sent.append(text)
        client.sent.append(text)
        return False


def test_main_skips_the_arrive_line_when_the_brief_fails() -> None:
    """A failed description does not announce the arrival."""
    ada = FakePlayer(room="hall")
    server = ClosedServer(people=[ada])
    process_commands(server, ada, "main")  # type: ignore[arg-type]
    assert ada.room == "reception"
    assert server.room_lines == ["Ada goes to Reception.\n"]


def test_examine_idle_of_ten_seconds_is_not_idle(tmp_path: Path) -> None:
    """Ten seconds is not idle. Eleven seconds uses the duration words.

    Examining yourself is already zero, because typing cleared idle first.
    """
    server = login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), idle_seconds=10)
    bea = FakePlayer(name="Bea", room="hall", player=user, discovered_columns=72)
    ada_user = Player("Ada", "hall", datetime.now(timezone.utc), idle_seconds=40)
    ada = FakePlayer(name="Ada", room="hall", player=ada_user, discovered_columns=72)
    server.people = [ada, bea]
    process_commands(server, ada, "examine Bea")  # type: ignore[arg-type]
    assert "Not idle at all" in server.sent[0]
    assert "Idle for" in server.sent[0]
    assert server.sent[0].splitlines()[0] == "-" * 72
    assert user.idle_seconds == 10
    assert ada_user.idle_seconds == 0
    user.idle_seconds = 11
    server.sent.clear()
    process_commands(server, ada, "examine Bea")  # type: ignore[arg-type]
    assert "Not idle at all" not in server.sent[0]
    assert duration_phrase(11) in server.sent[0]
    ada_user.idle_seconds = 40
    server.sent.clear()
    process_commands(server, ada, "examine me")  # type: ignore[arg-type]
    assert "Not idle at all" in server.sent[0]
    assert ada_user.idle_seconds == 0


def test_examine_shows_ssh_when_the_socket_is_ssh() -> None:
    """Connected via is ssh on an SSH socket and telnet otherwise."""
    when = datetime.now(timezone.utc)
    user = Player("Bea", "hall", when)
    bea = FakePlayer(name="Bea", room="hall", player=user, ssh=True)
    server = FakeServer(people=[bea])
    process_commands(server, FakePlayer(), "examine Bea")  # type: ignore[arg-type]
    assert "Connected via" not in server.sent[0]
    assert "[via ssh]" in server.sent[0]
    bea.ssh = False
    server.sent.clear()
    process_commands(server, FakePlayer(), "examine Bea")  # type: ignore[arg-type]
    assert "[via telnet]" in server.sent[0]
    """Examine names a player. ``x`` needs a space, and a missing player is not found."""
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    bea = FakePlayer(name="Bea", room="hall", player=Player("Bea", "hall", when, connected_seconds=7200))
    server = FakeServer(people=[bea])
    process_commands(server, FakePlayer(), "examine Bea")  # type: ignore[arg-type]
    body = examine_frame("Bea", (("User Rank", shown_rank(Rank.NEWBIE, True)), ("Logged in from", LOCAL_FROM), ("Logged in for", "10 minutes"), ("Total Login", "2 hours"), ("Idle for", "Not idle at all"), *ROOM_SENTENCES), 72)  # fmt: skip  # noqa: E501
    assert server.sent == [body]
    assert "User Rank" in body
    assert "Logged in for" in body
    assert "10 minutes" in body
    assert "Total Login" in body
    assert "2 hours" in body
    assert "Not idle at all" in body
    assert "Location:" not in body
    assert "[via telnet]" in body

    server = FakeServer(people=[bea])
    process_commands(server, FakePlayer(), "x Bea")  # type: ignore[arg-type]
    assert server.sent == [body]

    glued, _ = run("xBea")
    assert glued.sent == ["No such command 'xBea'\n"]

    bare, _ = run("x")
    assert bare.sent == ["Syntax: x <name>\n"]

    missing, _ = run("examine Nobody")
    assert missing.sent == ["Player Nobody not found.\n"]


def test_examine_text_is_the_seventy_two_column_profile() -> None:
    """Examine keeps a coloured rank inside the 72-column terminal."""
    from lupaxa.chat_server.colour import green, plain_len

    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    user = Player("Bea", "hall", when, connected_seconds=7200, idle_seconds=0)
    bea = FakePlayer(name="Bea", room="hall", player=user, discovered_columns=72)
    server = FakeServer(people=[bea])
    process_commands(server, bea, "examine me")  # type: ignore[arg-type]
    text = server.sent[0]
    assert text.splitlines()[0] == "-" * 72
    assert text.splitlines()[-1] == "-" * 72
    assert green("Newbie") in text
    assert "Logged in for" in text
    assert "Total Login" in text
    assert "Idle for" in text
    assert "Terminal Size" in text
    for line in text.splitlines():
        assert plain_len(line) == 72


def test_examine_hides_the_terminal_line_from_other_players(tmp_path: Path) -> None:
    """Another player, and a logged-out examine, do not show the terminal line."""
    when = datetime.now(timezone.utc)
    bea_user = Player("Bea", "hall", when)
    ada = FakePlayer(discovered_columns=80, discovered_rows=24)
    bea = FakePlayer(name="Bea", room="hall", player=bea_user)
    server = FakeServer(people=[ada, bea])
    process_commands(server, ada, "examine Bea")  # type: ignore[arg-type]
    assert "Terminal Size" not in server.sent[0]

    left = datetime.now(timezone.utc) - timedelta(hours=2)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), logged_out_at=left))
    server = login_host(tmp_path)
    process_commands(server, viewer(Rank.RESIDENT), "examine Bea")  # type: ignore[arg-type]
    assert "Terminal Size" not in server.sent[0]

    pending = FakePlayer(name="Bea", room="hall", player=Player("Bea", "hall", when), discovered_columns=None, discovered_rows=None, frame_columns=None)  # fmt: skip  # noqa: E501
    server = FakeServer(people=[pending])
    process_commands(server, pending, "examine me")  # type: ignore[arg-type]
    assert "80  (Pending)" in server.sent[0]


def test_examine_me_is_the_typist() -> None:
    """me, Me, and ME show the typist, and staff see their own address."""
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    user = Player("Ada", "reception", when, connected_seconds=7200)
    ada = FakePlayer(name="Ada", room="reception", player=user, address="10.0.0.5")
    server = FakeServer(people=[ada])
    body = examine_frame("Ada", (("User Rank", shown_rank(Rank.NEWBIE, True)), ("Logged in from", "10.0.0.5 (10.0.0.5) [via telnet]"), ("Logged in for", "10 minutes"), ("Total Login", "2 hours"), ("Idle for", "Not idle at all"), ("Terminal Size", "72x24 (72x24 discovered)"), *ROOM_SENTENCES), 72)  # fmt: skip  # noqa: E501
    for word in ("me", "Me", "ME"):
        server.sent.clear()
        process_commands(server, ada, f"examine {word}")  # type: ignore[arg-type]
        assert server.sent == [body]
    server.sent.clear()
    process_commands(server, ada, "x me")  # type: ignore[arg-type]
    assert server.sent == [body]
    assert "Ada".center(63) in body
    assert "Not idle at all" in body
    assert "Location:" not in body

    sue = staff("Sue", Rank.SUPER_USER)
    sue.address = "10.0.0.9"
    sue.hostname = "sue.example"
    server.people.append(sue)
    server.sent.clear()
    process_commands(server, sue, "examine ME")  # type: ignore[arg-type]
    assert server.sent == [examine_frame("Sue", (("User Rank", shown_rank(Rank.SUPER_USER, True)), ("Logged in from", "sue.example (10.0.0.9) [via telnet]"), ("Logged in for", "0 seconds"), ("Total Login", "0 seconds"), ("Idle for", "Not idle at all"), ("Terminal Size", "72x24 (72x24 discovered)"), *ROOM_SENTENCES), 72)]  # fmt: skip  # noqa: E501


def test_examine_finds_a_connected_newbie_by_exact_name(tmp_path: Path) -> None:
    """Find a connected newbie by the lowercase key."""
    server = login_host(tmp_path)
    cyd = newbie("Cyd")
    server.people = [cyd]
    body = examine_frame("Cyd", (("User Rank", shown_rank(Rank.NEWBIE, True)), ("Logged in from", LOCAL_FROM), ("Logged in for", "0 seconds"), ("Total Login", "0 seconds"), ("Idle for", "Not idle at all"), ("Terminal Size", "72x24 (72x24 discovered)"), *ROOM_SENTENCES), 72)  # fmt: skip  # noqa: E501
    process_commands(server, cyd, "examine Cyd")  # type: ignore[arg-type]
    assert server.sent == [body]
    assert "Cyd" in body
    assert "User Rank" in body
    assert "Newbie" in body
    assert load_saved(player_path(tmp_path, "Cyd")) is None
    server.sent.clear()
    process_commands(server, cyd, "examine cyd")  # type: ignore[arg-type]
    assert server.sent == [body]
    assert "Player cyd not found." not in server.sent[0]


def test_examine_lowercase_saved_name_uses_the_in_room_block(tmp_path: Path) -> None:
    """A saved player who is here gets the in-room block, not the logout block."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    user = Player("Bea", "hall", when, connected_seconds=7200)
    bea = FakePlayer(name="Bea", room="hall", player=user)
    server.people = [bea, newbie("Ada")]
    process_commands(server, newbie("Ada"), "examine bea")  # type: ignore[arg-type]
    body = examine_frame("Bea", (("User Rank", shown_rank(Rank.NEWBIE, True)), ("Logged in from", LOCAL_FROM), ("Logged in for", "10 minutes"), ("Total Login", "2 hours"), ("Idle for", "Not idle at all"), *ROOM_SENTENCES), 72)  # fmt: skip  # noqa: E501
    assert server.sent == [body]
    assert "Bea" in body
    assert "10 minutes" in body
    assert "2 hours" in body
    assert "Not idle at all" in body
    assert "Logged out" not in body
    assert "Location:" not in body


def test_alarm_sets_seconds_and_idle_lists_players() -> None:
    """Alarm stores minutes as seconds and resets the asker's idle."""
    when = datetime.now(timezone.utc)
    ada_user = Player("Ada", "reception", when, idle_seconds=40, rank=Rank.RESIDENT)
    bea_user = Player("Bea", "hall", when, idle_seconds=125)
    ada = FakePlayer(name="Ada", room="reception", player=ada_user)
    server = FakeServer(people=[ada, FakePlayer(name="Bea", room="hall", player=bea_user)])
    process_commands(server, ada, "alarm 2")  # type: ignore[arg-type]
    assert ada_user.alarm_seconds == 120
    assert ada_user.idle_seconds == 0
    assert server.sent == ["Alarm set for 2 minutes.\n"]

    resident = FakePlayer(player=Player("Ada", "reception", when, rank=Rank.RESIDENT))
    bad, _ = run("alarm later", resident)
    assert bad.sent == ["Syntax: alarm <minutes>\n"]
    bad_sup, _ = run("alarm ²", resident)
    assert bad_sup.sent == ["Syntax: alarm <minutes>\n"]


def test_idle_is_unknown() -> None:
    """Idle is not a command."""
    server, _ = run("idle")
    assert server.sent == ["No such command 'idle'\n"]


def test_consume_seconds_keeps_the_fraction() -> None:
    """Whole seconds are applied. The leftover fraction waits for the next loop."""
    mark, remainder, whole = consume_seconds(None, 0.0, 10.0)
    assert (mark, remainder, whole) == (10.0, 0.0, 0)
    mark, remainder, whole = consume_seconds(10.0, 0.0, 10.5)
    assert (mark, remainder, whole) == (10.5, 0.5, 0)
    mark, remainder, whole = consume_seconds(10.5, 0.5, 12.0)
    assert (mark, remainder, whole) == (12.0, 0.0, 2)


def test_commands_include_go_and_map() -> None:
    """The command list names the new words, with aliases before names."""
    server, _ = run("commands")
    words = server.sent[0].split()
    assert words.index("`") < words.index("commands")
    assert "go" in words
    assert "map" in words


def login_host(tmp_path: Path, super_admin: str | None = "admin", *, opened: bool = True) -> FakeServer:
    """Build a fake server with an optional saved super admin."""
    if not opened:
        super_admin = None
    if super_admin is not None and load_saved(player_path(tmp_path, super_admin)) is None:
        save_player(tmp_path, SavedPlayer(super_admin, Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    config = Config(port=1, rooms=Path("rooms.yml"), start="reception", help=Path("help.yml"), players=tmp_path, mfa_enabled=False)  # fmt: skip  # noqa: E501
    server = FakeServer(config=config, roster_dir=tmp_path)
    server.help = HelpBook(entries=(HelpEntry("disclaimer", (), "Be kind."),))
    return server


def mfa_login_host(tmp_path: Path, super_admin: str | None = "admin", *, opened: bool = True) -> FakeServer:
    """Build a fake server with MFA enabled and an optional saved super admin."""
    server = login_host(tmp_path, super_admin, opened=opened)
    server.mfa_enabled = True
    server.config = replace(server.config, mfa_enabled=True)
    return server


def register_mfa_command() -> None:
    from lupaxa.chat_server.commands import ensure_commands
    from lupaxa.chat_server.globals import COMMANDS
    from lupaxa.chat_server.mfa import register_mfa

    ensure_commands()
    register_mfa(COMMANDS)


def mfa_command_host(tmp_path: Path, super_admin: str | None = "admin", *, opened: bool = True) -> FakeServer:
    server = mfa_login_host(tmp_path, super_admin, opened=opened)
    register_mfa_command()
    return server


MFA_REMOVED_WARNING = ("Your MFA has been removed. Set it up again before you log out. The next login will make you set it up before you can enter.")  # fmt: skip  # noqa: E501


def assert_sealed_mfa_secret(saved: SavedPlayer, password: str, plain_secret: str) -> None:
    """Assert the file seal opens with ``password`` to ``plain_secret``."""
    from lupaxa.chat_server.mfa_seal import open_mfa_secret

    assert saved.mfa_secret is not None
    assert saved.mfa_secret.startswith("enc1$")
    assert plain_secret not in saved.mfa_secret
    assert saved.mfa_on is True
    assert open_mfa_secret(password, saved.mfa_secret) == plain_secret


def totp_code(secret: str, *, now: int = 1_700_000_000) -> str:
    import base64
    import hmac
    import struct
    from hashlib import sha1

    raw = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    step = now // 30
    digest = hmac.new(raw, struct.pack(">Q", step), sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def test_mfa_login_asks_for_a_code_after_the_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A saved secret sends Code after the password, then enters on a match."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.player is None
    assert player.auth == "code"
    assert server.sent[-1] == "Code: "
    process_input(server, player, totp_code(totp_seed))  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.name == "Bea"


def test_a_plaintext_secret_still_logs_in_and_is_sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An old base32 secret matches a code and is stored sealed."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is not None
    assert saved.mfa_secret.startswith("enc1$")
    assert totp_seed not in saved.mfa_secret
    assert saved.mfa_on is True
    assert player.auth == "code"
    assert player.player is None
    process_input(server, player, totp_code(totp_seed))  # type: ignore[arg-type]
    assert player.player is not None
    assert player.pending_password == ""
    assert player.pending_mfa_secret == ""
    record_saved_logout(tmp_path, player.player)
    stayed = load_saved(player_path(tmp_path, "Bea"))
    assert stayed is not None
    assert stayed.mfa_secret is not None
    assert stayed.mfa_secret.startswith("enc1$")
    assert totp_seed not in stayed.mfa_secret


def test_mfa_off_still_seals_a_plaintext_secret(tmp_path: Path) -> None:
    """A turned-off server seals the secret and does not ask for a code."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    server = mfa_login_host(tmp_path)
    server.mfa_enabled = False
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert "Code:" not in "".join(server.sent)
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is not None
    assert saved.mfa_secret.startswith("enc1$")
    assert saved.mfa_on is True


def test_login_says_mfa_was_removed_when_the_flag_remains(tmp_path: Path) -> None:
    """A deleted secret is obvious when the file still shows MFA was on."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_on=True))
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" in server.sent
    assert player.player is not None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_on is False


def test_staff_with_the_flag_are_told_and_must_set_mfa_up(tmp_path: Path) -> None:
    """A wiped secret still blocks staff when the file shows MFA was on."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4), mfa_on=True))
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" in server.sent
    assert player.auth == "mfa_enroll"
    assert player.player is None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_on is True


def test_su_channel_must_enrol_when_mfa_is_on(tmp_path: Path) -> None:
    """MFA on and no secret sends SU Channel to enrolment. A resident enters."""
    save_player(tmp_path, SavedPlayer("Ada", Rank.SU_CHANNEL, hash_password("pw", rounds=4)))
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = mfa_login_host(tmp_path)
    ada = FakePlayer(player=None)
    process_input(server, ada, "Ada")  # type: ignore[arg-type]
    process_input(server, ada, "pw")  # type: ignore[arg-type]
    assert ada.auth == "mfa_enroll"
    assert ada.player is None
    bea = FakePlayer(player=None)
    process_input(server, bea, "Bea")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    assert bea.player is not None
    assert bea.auth != "mfa_enroll"


def test_su_channel_sees_the_closed_talker_line(tmp_path: Path) -> None:
    """Login and reconnect show the closed line to SU Channel, not to a resident."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("Ada", Rank.SU_CHANNEL, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path, super_admin=None)
    server.roster_dir = players
    server.config = replace(server.config, players=players)
    server.allow_residents = False
    ada = FakePlayer(player=None)
    enter(server, ada, "Ada", Rank.SU_CHANNEL)  # type: ignore[arg-type]
    assert "[ Status ] The talker is closed to newbies and residents.\n" in ada.sent
    bea = FakePlayer(player=None)
    enter(server, bea, "Bea", Rank.RESIDENT)  # type: ignore[arg-type]
    assert "[ Status ] The talker is closed to newbies and residents.\n" not in bea.sent
    live = staff("Ada", Rank.SU_CHANNEL)
    live.room = "hall"
    assert live.player is not None
    live.player.room = "hall"
    server.people = [live]
    held = load_saved(player_path(players, "Ada"))
    assert held is not None
    newcomer = FakePlayer(player=None)
    enter_fresh(server, newcomer, held)  # type: ignore[arg-type]
    assert "[ Status ] The talker is closed to newbies and residents.\n" in live.sent


def test_staff_unreadable_seal_kept_until_setup_finishes(tmp_path: Path) -> None:
    """Staff keep a seal that will not open until enrolment saves."""
    from lupaxa.chat_server.mfa_seal import seal_mfa_secret

    sealed = seal_mfa_secret("other", TOTP_TEST_SEED)
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4), mfa_secret=sealed, mfa_on=True))
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" in server.sent
    assert player.auth == "mfa_enroll"
    assert player.player is None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret == sealed
    assert saved.mfa_on is True


def test_staff_without_the_flag_set_up_with_no_removal_line(tmp_path: Path) -> None:
    """The first forced setup does not say MFA was removed."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" not in server.sent
    assert player.auth == "mfa_enroll"


def test_mfa_off_leaves_an_unreadable_secret_in_place(tmp_path: Path) -> None:
    """A seal that will not open is kept when MFA is turned off."""
    from lupaxa.chat_server.mfa_seal import seal_mfa_secret

    sealed = seal_mfa_secret("other", TOTP_TEST_SEED)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=sealed, mfa_on=True))
    server = mfa_login_host(tmp_path)
    server.mfa_enabled = False
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert "Your MFA has been removed.\n" not in server.sent
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret == sealed


def test_unreadable_secret_removed_when_mfa_flag_is_off(tmp_path: Path) -> None:
    """A seal that will not open is cleared even when mfa_on is false."""
    from lupaxa.chat_server.mfa_seal import seal_mfa_secret

    sealed = seal_mfa_secret("other", TOTP_TEST_SEED)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=sealed, mfa_on=False))
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" in server.sent
    assert player.player is not None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False


def test_an_unreadable_secret_tells_a_resident_and_is_deleted(tmp_path: Path) -> None:
    """A seal that will not open is removed after the resident is told."""
    from lupaxa.chat_server.mfa_seal import seal_mfa_secret

    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=seal_mfa_secret("other", TOTP_TEST_SEED), mfa_on=True))  # fmt: skip  # noqa: E501
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" in server.sent
    assert player.player is not None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False


def test_mfa_login_drops_after_three_wrong_codes(tmp_path: Path) -> None:
    """Three wrong codes on one login drop the connection."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    process_input(server, player, "000000")  # type: ignore[arg-type]
    assert server.sent[-2:] == ["Wrong code.\n", "Code: "]
    process_input(server, player, "000001")  # type: ignore[arg-type]
    process_input(server, player, "000002")  # type: ignore[arg-type]
    assert server.dropped is True
    assert player.player is None


def test_mfa_enrolment_does_not_restore_a_nuked_player(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A code after nuke does not recreate the file or enter the room."""
    save_player(tmp_path, SavedPlayer("Cyd", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    server = mfa_login_host(tmp_path)
    boss = staff("Boss", Rank.ADMIN)
    server.people = [boss]
    player = FakePlayer(player=None)
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(server, player, "Cyd")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.auth == "mfa_enroll"
    secret = player.pending_mfa_secret
    delete_player(tmp_path, "Cyd")
    code = totp_code(secret)
    process_input(server, player, code)  # type: ignore[arg-type]
    assert player.player is None
    assert load_saved(player_path(tmp_path, "Cyd")) is None
    assert server.sent[-1] == NAME_PROMPT


def test_mfa_super_user_enrols_on_first_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A super user with no secret sees the secret, then saves it on a match."""
    save_player(tmp_path, SavedPlayer("Cyd", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(server, player, "Cyd")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.player is None
    assert player.auth == "mfa_enroll"
    body = "".join(server.sent)
    assert player.pending_mfa_secret in body
    assert "Code:" in body
    secret = player.pending_mfa_secret
    code = totp_code(secret)
    process_input(server, player, code)  # type: ignore[arg-type]
    assert player.player is not None
    saved = load_saved(player_path(tmp_path, "Cyd"))
    assert saved is not None
    assert_sealed_mfa_secret(saved, "pw", secret)


def test_mfa_super_admin_first_login_waits_for_enrolment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MFA is sealed on the saved file only after the enrolment code matches."""
    save_player(tmp_path, SavedPlayer("Zed", Rank.SUPER_ADMIN, hash_password("secret", rounds=4)))
    server = mfa_login_host(tmp_path, super_admin=None)
    player = FakePlayer(player=None)
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(server, player, "Zed")  # type: ignore[arg-type]
    process_input(server, player, "secret")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Zed"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert player.auth == "mfa_enroll"
    secret = player.pending_mfa_secret
    code = totp_code(secret)
    process_input(server, player, code)  # type: ignore[arg-type]
    assert player.player is not None
    saved = load_saved(player_path(tmp_path, "Zed"))
    assert saved is not None
    assert_sealed_mfa_secret(saved, "secret", secret)


def test_mfa_resident_without_secret_enters_on_password_alone(tmp_path: Path) -> None:
    """A resident with no mfa_secret still enters when MFA is on."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = mfa_login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.auth == "name"
    assert "Code:" not in "".join(server.sent)


def test_mfa_closed_talker_skips_the_code_for_residents(tmp_path: Path) -> None:
    """A resident is refused after the password when residents are closed."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    server = mfa_login_host(tmp_path)
    server.allow_residents = False
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert "Code:" not in "".join(server.sent)
    assert server.dropped is True


def test_mfa_reconnect_waits_for_the_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second socket does not replace the live player until the code matches."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    server = mfa_login_host(tmp_path)
    live = FakePlayer(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server.people = [live]
    newcomer = FakePlayer(player=None, address="10.0.0.5")
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    process_input(server, newcomer, "pw")  # type: ignore[arg-type]
    assert server.replaced == []
    assert newcomer.auth == "code"
    process_input(server, newcomer, totp_code(totp_seed))  # type: ignore[arg-type]
    assert server.replaced == [live]


def test_mfa_command_syntax_when_bare(tmp_path: Path) -> None:
    """Bare mfa prints the syntax line."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = staff("Bea", Rank.RESIDENT)
    server = mfa_command_host(tmp_path)
    process_commands(server, bea, "mfa")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: mfa setup|mfa remove|mfa remove <name>\n"]


def test_mfa_setup_asks_password_then_saves_secret_on_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup stores the secret only after password and a matching code."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = staff("Bea", Rank.RESIDENT)
    server = mfa_command_host(tmp_path)
    monkeypatch.setattr("lupaxa.chat_server.mfa.time.time", lambda: 1_700_000_000.0)
    process_commands(server, bea, "mfa setup")  # type: ignore[arg-type]
    assert server.sent == ["Current password: "]
    assert bea.auth == "mfa_pw"
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")).mfa_secret is None  # type: ignore[union-attr]
    secret = bea.pending_mfa_secret
    assert secret in "".join(server.sent)
    assert bea.auth == "mfa_show"
    process_input(server, bea, totp_code(secret))  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert_sealed_mfa_secret(saved, "pw", secret)
    assert bea.auth == "name"


def test_mfa_setup_wrong_password_returns_to_room(tmp_path: Path) -> None:
    """A wrong setup password sends one line and does not ask again."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = staff("Bea", Rank.RESIDENT)
    server = mfa_command_host(tmp_path)
    process_commands(server, bea, "mfa setup")  # type: ignore[arg-type]
    process_input(server, bea, "no")  # type: ignore[arg-type]
    assert server.sent[-1] == "Wrong password.\nWhat? > "
    assert bea.auth == "name"


def test_mfa_setup_quit_at_password_returns_to_room(tmp_path: Path) -> None:
    """Quit at the setup password prompt returns to the room without saving."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = staff("Bea", Rank.RESIDENT)
    server = mfa_command_host(tmp_path)
    process_commands(server, bea, "mfa setup")  # type: ignore[arg-type]
    process_input(server, bea, "quit")  # type: ignore[arg-type]
    assert server.sent[-1] == "What? > "
    assert bea.auth == "name"
    assert bea.player is not None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None


def test_mfa_setup_when_secret_exists(tmp_path: Path) -> None:
    """Setup is refused when MFA is already on."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=TOTP_TEST_SEED))
    bea = staff("Bea", Rank.RESIDENT)
    server = mfa_command_host(tmp_path)
    process_commands(server, bea, "mfa setup")  # type: ignore[arg-type]
    assert server.sent == ["MFA is already on.\n"]


def test_mfa_remove_self_clears_secret_for_resident(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Self-remove asks for password and code, then clears the file."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    bea = staff("Bea", Rank.RESIDENT)
    server = mfa_command_host(tmp_path)
    monkeypatch.setattr("lupaxa.chat_server.mfa.time.time", lambda: 1_700_000_000.0)
    process_commands(server, bea, "mfa remove")  # type: ignore[arg-type]
    assert server.sent == ["Current password: "]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    assert bea.auth == "mfa_remove_code"
    process_input(server, bea, totp_code(totp_seed))  # type: ignore[arg-type]
    assert server.sent[-1] == "Your MFA has been removed.\nWhat? > "
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False


def test_mfa_remove_self_then_setup_same_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After self-remove, setup accepts the new secret's code in the same timestep."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    bea = staff("Bea", Rank.RESIDENT)
    server = mfa_command_host(tmp_path)
    monkeypatch.setattr("lupaxa.chat_server.mfa.time.time", lambda: 1_700_000_000.0)
    process_commands(server, bea, "mfa remove")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    process_input(server, bea, totp_code(totp_seed))  # type: ignore[arg-type]
    assert bea.player is not None
    process_commands(server, bea, "mfa setup")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    new_secret = bea.pending_mfa_secret
    process_input(server, bea, totp_code(new_secret))  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert_sealed_mfa_secret(saved, "pw", new_secret)
    assert bea.player is not None
    assert bea.auth == "name"


def test_mfa_remove_self_super_user_sees_warning_and_reenrols_on_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A super user who clears MFA is warned and must enrol again on the next login."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Cyd", Rank.SUPER_USER, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    cyd = staff("Cyd", Rank.SUPER_USER)
    server = mfa_command_host(tmp_path)
    monkeypatch.setattr("lupaxa.chat_server.mfa.time.time", lambda: 1_700_000_000.0)
    process_commands(server, cyd, "mfa remove")  # type: ignore[arg-type]
    process_input(server, cyd, "pw")  # type: ignore[arg-type]
    process_input(server, cyd, totp_code(totp_seed))  # type: ignore[arg-type]
    assert MFA_REMOVED_WARNING in server.sent[-1]
    assert cyd.player is not None
    newcomer = FakePlayer(player=None)
    process_input(server, newcomer, "Cyd")  # type: ignore[arg-type]
    process_input(server, newcomer, "pw")  # type: ignore[arg-type]
    assert newcomer.auth == "mfa_enroll"


def test_mfa_remove_other_asks_sure_and_clears_on_yes(tmp_path: Path) -> None:
    """An admin clears another player's MFA after yes and announces on the admin channel."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    save_player(tmp_path, SavedPlayer("Boss", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(tmp_path, SavedPlayer("Ada", Rank.ADMIN, hash_password("pw", rounds=4)))
    boss = staff("Boss", Rank.ADMIN)
    ada = staff("Ada", Rank.ADMIN)
    bea = staff("Bea", Rank.SUPER_USER)
    server = mfa_command_host(tmp_path)
    server.people = [boss, ada, bea]
    process_commands(server, boss, "mfa remove Bea")  # type: ignore[arg-type]
    assert server.sent == ["Are you sure you want to remove MFA for Bea?\n"]
    assert boss.auth == "mfa_sure"
    process_input(server, boss, "no")  # type: ignore[arg-type]
    assert server.sent[-1] == "MFA remove cancelled.\nWhat? > "
    process_commands(server, boss, "mfa remove Bea")  # type: ignore[arg-type]
    process_input(server, boss, "yes")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False
    assert bea.player is not None
    line = "[ Admin ] Boss removes MFA for Bea.\n"
    assert server.admin_lines == [paint_admin(ada, line)]


def test_mfa_remove_other_clears_logged_off_player(tmp_path: Path) -> None:
    """Removing MFA for someone who is offline still clears the file after yes."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    save_player(tmp_path, SavedPlayer("Boss", Rank.ADMIN, hash_password("pw", rounds=4)))
    boss = staff("Boss", Rank.ADMIN)
    server = mfa_command_host(tmp_path)
    process_commands(server, boss, "mfa remove Bea")  # type: ignore[arg-type]
    process_input(server, boss, "yes")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False


def test_mfa_remove_other_refuses_equal_rank(tmp_path: Path) -> None:
    """An admin cannot remove MFA for another admin."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.ADMIN, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    save_player(tmp_path, SavedPlayer("Boss", Rank.ADMIN, hash_password("pw", rounds=4)))
    boss = staff("Boss", Rank.ADMIN)
    server = mfa_command_host(tmp_path)
    process_commands(server, boss, "mfa remove Bea")  # type: ignore[arg-type]
    assert server.sent == ["You cannot remove MFA for Bea.\n"]


def test_mfa_remove_other_super_admin_clears_admin(tmp_path: Path) -> None:
    """The super admin can clear a promoted admin after yes."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("Bea", Rank.ADMIN, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    save_player(tmp_path, SavedPlayer("admin", Rank.SUPER_ADMIN, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    ada = staff("admin", Rank.SUPER_ADMIN)
    server = mfa_command_host(tmp_path)
    process_commands(server, ada, "mfa remove Bea")  # type: ignore[arg-type]
    process_input(server, ada, "yes")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False


def test_mfa_remove_other_refuses_super_admin(tmp_path: Path) -> None:
    """Nobody can remove MFA for the super admin through the talker."""
    totp_seed = TOTP_TEST_SEED
    save_player(tmp_path, SavedPlayer("admin", Rank.SUPER_ADMIN, hash_password("pw", rounds=4), mfa_secret=totp_seed))
    save_player(tmp_path, SavedPlayer("Boss", Rank.ADMIN, hash_password("pw", rounds=4)))
    boss = staff("Boss", Rank.ADMIN)
    server = mfa_command_host(tmp_path)
    process_commands(server, boss, "mfa remove admin")  # type: ignore[arg-type]
    assert server.sent == ["You cannot remove MFA for admin.\n"]


def test_mfa_remove_other_without_secret(tmp_path: Path) -> None:
    """Removing MFA for someone with no secret is refused."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    save_player(tmp_path, SavedPlayer("Boss", Rank.ADMIN, hash_password("pw", rounds=4)))
    boss = staff("Boss", Rank.ADMIN)
    server = mfa_command_host(tmp_path)
    process_commands(server, boss, "mfa remove Bea")  # type: ignore[arg-type]
    assert server.sent == ["Bea has no MFA.\n"]


def test_mfa_remove_unknown_player(tmp_path: Path) -> None:
    """An unknown name is reported before rank or secret checks."""
    save_player(tmp_path, SavedPlayer("Boss", Rank.ADMIN, hash_password("pw", rounds=4)))
    boss = staff("Boss", Rank.ADMIN)
    server = mfa_command_host(tmp_path)
    process_commands(server, boss, "mfa remove Bea")  # type: ignore[arg-type]
    assert server.sent == ["No saved player 'Bea'.\n"]


def test_mfa_command_missing_when_disabled(tmp_path: Path) -> None:
    """Without the server flag, mfa is not a command."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    register_mfa_command()
    process_commands(server, bea, "mfa")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'mfa'\n"]


def test_a_newbie_name_enters_after_yes(tmp_path: Path) -> None:
    """A free name shows the disclaimer and enters after yes."""
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Ada")  # type: ignore[arg-type]
    assert player.player is None
    assert player.sent[-1] == "Do you agree to these rules: "
    page_line = next(line for line in player.sent if "Be kind." in line)
    assert page_line.startswith("\n\n")
    process_input(server, player, "YES")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.rank is Rank.NEWBIE
    assert player.sent[-1] == "What? > "


def test_a_server_at_the_cap_still_accepts_the_last_name(tmp_path: Path) -> None:
    """A connection count equal to the cap is allowed."""
    server = login_host(tmp_path)
    server.config = replace(server.config, max_connections=1)
    newcomer = FakePlayer(player=None, name=None)
    server.people = [newcomer]
    process_input(server, newcomer, "Ada")  # type: ignore[arg-type]
    assert newcomer.sent[-1] == "Do you agree to these rules: "
    assert newcomer not in server.dropped_clients


def test_the_first_admin_is_not_exempt_when_the_server_is_full(tmp_path: Path) -> None:
    """A new name with no saved file is dropped when the server is full."""
    server = login_host(tmp_path, opened=False)
    server.config = replace(server.config, max_connections=1)
    server.full_message = "The server is full.\n"
    filler = FakePlayer(player=None, name=None, auth="name")
    newcomer = FakePlayer(player=None, name=None, address="127.0.0.1")
    server.people = [filler, newcomer]
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    assert newcomer in server.dropped_clients
    assert newcomer.sent[-1] != "Password: "
    assert newcomer.sent[-1] != "Set a password: "


def test_a_disclaimer_already_shown_is_not_checked_again(tmp_path: Path) -> None:
    """Saying yes still enters after the cap filled behind this disclaimer."""
    server = login_host(tmp_path)
    server.config = replace(server.config, max_newbies=1)
    first = FakePlayer(player=None, name=None)
    server.people = [first]
    process_input(server, first, "Ada")  # type: ignore[arg-type]
    assert first.auth == "agree"
    second = FakePlayer(player=None, name=None)
    server.people.append(second)
    process_input(server, second, "Bea")  # type: ignore[arg-type]
    assert second in server.dropped_clients
    process_input(server, first, "yes")  # type: ignore[arg-type]
    assert first.player is not None
    assert first.player.rank == Rank.NEWBIE


def test_a_full_server_drops_a_new_name(tmp_path: Path) -> None:
    """One connection over the cap sees the full message and is dropped."""
    server = login_host(tmp_path)
    server.config = replace(server.config, max_connections=1)
    server.full_message = "The server is full.\nPlease try again later.\n"
    filler = FakePlayer(player=None, name=None, auth="name")
    newcomer = FakePlayer(player=None, name=None)
    server.people = [filler, newcomer]
    process_input(server, newcomer, "Ada")  # type: ignore[arg-type]
    width = frame_width(newcomer.discovered_columns, newcomer.frame_columns)
    assert newcomer.sent[-1] == refusal_text(server.full_message, width)
    assert newcomer in server.dropped_clients
    assert server.announced[-1] is False
    assert newcomer.auth != "agree"


def test_an_admin_reaches_the_password_when_the_server_is_full(tmp_path: Path) -> None:
    """A saved admin skips the connection cap."""
    save_player(tmp_path, SavedPlayer("Boss", Rank.ADMIN, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    server.config = replace(server.config, max_connections=1)
    server.full_message = "The server is full.\n"
    filler = FakePlayer(player=None, name=None, auth="name")
    newcomer = FakePlayer(player=None, name=None)
    server.people = [filler, newcomer]
    process_input(server, newcomer, "Boss")  # type: ignore[arg-type]
    assert newcomer.sent[-1] == "Password: "
    assert newcomer not in server.dropped_clients


def test_a_super_admin_reaches_the_password_when_the_server_is_full(tmp_path: Path) -> None:
    """A saved super admin skips the connection cap."""
    save_player(tmp_path, SavedPlayer("Ada", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    server = login_host(tmp_path, super_admin="Ada")
    server.config = replace(server.config, max_connections=1)
    filler = FakePlayer(player=None, name=None, auth="name")
    newcomer = FakePlayer(player=None, name=None)
    server.people = [filler, newcomer]
    process_input(server, newcomer, "Ada")  # type: ignore[arg-type]
    assert newcomer.sent[-1] == "Password: "
    assert newcomer not in server.dropped_clients


def test_a_super_user_is_dropped_when_the_server_is_full(tmp_path: Path) -> None:
    """A super user is not exempt from the connection cap."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    server.config = replace(server.config, max_connections=1)
    server.full_message = "The server is full.\n"
    filler = FakePlayer(player=None, name=None, auth="name")
    newcomer = FakePlayer(player=None, name=None)
    server.people = [filler, newcomer]
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    assert newcomer in server.dropped_clients
    assert newcomer.sent[-1] != "Password: "


def test_a_coder_reaches_the_password_when_the_server_is_full(tmp_path: Path) -> None:
    """A saved coder skips the connection cap."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.CODER, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    server.config = replace(server.config, max_connections=1)
    server.full_message = "The server is full.\n"
    filler = FakePlayer(player=None, name=None, auth="name")
    newcomer = FakePlayer(player=None, name=None)
    server.people = [filler, newcomer]
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    assert newcomer.sent[-1] == "Password: "
    assert newcomer not in server.dropped_clients


def test_an_su_channel_player_is_dropped_when_the_server_is_full(tmp_path: Path) -> None:
    """An SU Channel player is not exempt from the connection cap."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.SU_CHANNEL, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    server.config = replace(server.config, max_connections=1)
    server.full_message = "The server is full.\n"
    filler = FakePlayer(player=None, name=None, auth="name")
    newcomer = FakePlayer(player=None, name=None)
    server.people = [filler, newcomer]
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    assert newcomer in server.dropped_clients
    assert newcomer.sent[-1] != "Password: "


def test_lsu_lists_a_coder_and_a_channel_only_player(tmp_path: Path) -> None:
    """An admin sees a coder and an SU Channel player."""
    from lupaxa.chat_server.colour import cyan

    server = login_host(tmp_path)
    ada = staff("Ada", Rank.ADMIN)
    assert ada.player is not None
    ada.player.colour = True
    bea = staff("Bea", Rank.CODER)
    cyd = staff("Cyd", Rank.SU_CHANNEL)
    server.people = [ada, bea, cyd]
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    text = server.sent[0]
    assert "Bea" in text
    assert "Cyd" in text
    assert cyan("< Channel Only >") in text


def test_lsu_hides_channel_only_from_residents_and_shows_off_duty_staff(tmp_path: Path) -> None:
    """Residents miss SU Channel and off-duty staff. SU Channel sees both."""
    from lupaxa.chat_server.colour import cyan

    server = login_host(tmp_path)
    resident = staff("Ada", Rank.RESIDENT)
    assert resident.player is not None
    resident.player.colour = True
    channel = staff("Bea", Rank.SU_CHANNEL)
    assert channel.player is not None
    channel.player.colour = True
    boss = staff("Cyd", Rank.SUPER_USER)
    assert boss.player is not None
    boss.player.off_duty = True
    dee = staff("Dee", Rank.SUPER_USER)
    server.people = [resident, channel, boss, dee]
    process_commands(server, resident, "lsu")  # type: ignore[arg-type]
    assert "Bea" not in server.sent[0]
    assert "Cyd" not in server.sent[0]
    assert "Dee" in server.sent[0]
    server.sent.clear()
    process_commands(server, channel, "lsu")  # type: ignore[arg-type]
    assert cyan("< Channel Only >") in server.sent[0]
    assert "Cyd" in server.sent[0]
    assert cyan("< Off Duty >") in server.sent[0]


def test_an_online_resident_reconnects_when_the_server_is_full(tmp_path: Path) -> None:
    """A saved name that is already in a room skips the cap and takes over."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    server.config = replace(server.config, max_connections=1)
    live = staff("Bea", Rank.RESIDENT)
    newcomer = FakePlayer(player=None, name=None)
    server.people = [live, newcomer]
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    assert newcomer.sent[-1] == "Password: "
    process_input(server, newcomer, "pw")  # type: ignore[arg-type]
    assert server.replaced == [live]
    assert newcomer not in server.dropped_clients


def test_a_newbie_cap_drops_a_new_name_before_the_disclaimer(tmp_path: Path) -> None:
    """Someone already reading the disclaimer fills the newbie cap."""
    server = login_host(tmp_path)
    server.config = replace(server.config, max_newbies=1)
    server.newbie_message = "No more new players can enter right now.\nPlease try again later.\n"
    waiting = FakePlayer(player=None, name=None, auth="agree", pending_name="Cyd")
    newcomer = FakePlayer(player=None, name=None)
    server.people = [waiting, newcomer]
    process_input(server, newcomer, "Ada")  # type: ignore[arg-type]
    width = frame_width(newcomer.discovered_columns, newcomer.frame_columns)
    assert newcomer.sent[-1] == refusal_text(server.newbie_message, width)
    assert newcomer in server.dropped_clients
    assert server.announced[-1] is False
    assert newcomer.auth != "agree"


def test_a_logged_in_newbie_in_a_room_fills_the_newbie_cap(tmp_path: Path) -> None:
    """A logged-in newbie in a room fills the newbie cap."""
    server = login_host(tmp_path)
    server.config = replace(server.config, max_newbies=1)
    server.newbie_message = "No more new players can enter right now.\n"
    in_room = staff("Cyd", Rank.NEWBIE)
    in_room.room = "reception"
    assert in_room.player is not None
    in_room.player.room = "reception"
    newcomer = FakePlayer(player=None, name=None)
    server.people = [in_room, newcomer]
    process_input(server, newcomer, "Ada")  # type: ignore[arg-type]
    width = frame_width(newcomer.discovered_columns, newcomer.frame_columns)
    assert newcomer in server.dropped_clients
    assert server.announced[-1] is False
    assert newcomer.sent[-1] == refusal_text(server.newbie_message, width)


def test_a_full_server_wins_over_the_newbie_cap(tmp_path: Path) -> None:
    """Both caps refuse the name with the full message."""
    server = login_host(tmp_path)
    server.config = replace(server.config, max_connections=1, max_newbies=1)
    server.full_message = "The server is full.\n"
    server.newbie_message = "No more new players can enter right now.\n"
    waiting = FakePlayer(player=None, name=None, auth="agree", pending_name="Cyd")
    newcomer = FakePlayer(player=None, name=None)
    server.people = [waiting, newcomer]
    process_input(server, newcomer, "Ada")  # type: ignore[arg-type]
    width = frame_width(newcomer.discovered_columns, newcomer.frame_columns)
    assert newcomer.sent[-1] == refusal_text(server.full_message, width)
    assert "No more new players" not in newcomer.sent[-1]


def test_a_closed_talker_still_uses_the_closed_line_when_the_caps_have_room(tmp_path: Path) -> None:
    """Under both caps, a closed talker stays at the name prompt."""
    server = login_host(tmp_path)
    server.allow_newbies = False
    newcomer = FakePlayer(player=None, name=None)
    server.people = [newcomer]
    process_input(server, newcomer, "Ada")  # type: ignore[arg-type]
    assert newcomer.sent[-1] == f"The talker is closed to new people.\n{NAME_PROMPT}"
    assert newcomer not in server.dropped_clients


def test_a_newbie_must_type_yes(tmp_path: Path) -> None:
    """Any other line repeats the prompt and does not draw the page again."""
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Ada")  # type: ignore[arg-type]
    process_input(server, player, "maybe")  # type: ignore[arg-type]
    assert player.player is None
    assert player.auth == "agree"
    assert player.sent[-1] == "Do you agree to these rules: "
    assert sum("Be kind." in line for line in player.sent) == 1
    process_input(server, player, "who")  # type: ignore[arg-type]
    assert player.sent[-1] == "Do you agree to these rules: "
    assert player.player is None


def test_quit_during_the_disclaimer_disconnects(tmp_path: Path) -> None:
    """Quit at the disclaimer leaves before the room."""
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Ada")  # type: ignore[arg-type]
    process_input(server, player, "quit")  # type: ignore[arg-type]
    assert player in server.dropped_clients
    assert player.player is None
    assert server.room_lines == []


def test_yes_refuses_a_talker_that_closed(tmp_path: Path) -> None:
    """Closing newbies while the page is up returns to the name prompt."""
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Ada")  # type: ignore[arg-type]
    server.allow_newbies = False
    process_input(server, player, "yes")  # type: ignore[arg-type]
    assert player.player is None
    assert player.auth == "name"
    assert player.sent[-1] == ("The talker is closed to new people.\nPlease enter your name: ")


def test_a_missing_disclaimer_disconnects_the_newbie(tmp_path: Path) -> None:
    """No disclaimer topic tells the newbie, drops them, and tells admins."""
    server = login_host(tmp_path)
    server.help = HelpBook(entries=())
    admin_user = Player("Ann", "reception", datetime.now(timezone.utc), rank=Rank.ADMIN)
    admin_user.colour = False
    admin = FakePlayer(name="Ann", player=admin_user)
    server.people = [admin]
    player = FakePlayer(player=None)
    process_input(server, player, "Ada")  # type: ignore[arg-type]
    assert player.sent == ["The disclaimer is missing.\n"]
    assert player in server.dropped_clients
    assert server.announced == [False]
    assert server.room_lines == []
    assert server.admin_lines == ["[ Admin ] The disclaimer is missing.\n"]
    assert player.player is None


def test_the_admin_name_sets_and_confirms_a_password(tmp_path: Path) -> None:
    """The name admin with no file follows the newbie disclaimer like any other name."""
    server = login_host(tmp_path, super_admin=None, opened=False)
    player = FakePlayer(player=None)
    process_input(server, player, "admin")  # type: ignore[arg-type]
    assert player.auth == "agree"
    assert "Set a password:" not in "".join(server.sent)
    assert load_saved(player_path(tmp_path, "admin")) is None


def test_set_password_errors_return_to_the_first_prompt(tmp_path: Path) -> None:
    """Empty, long, and mismatched passwords each ask for a new password."""
    server = login_host(tmp_path)
    bea = newbie("Bea")
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [bea, ada]
    process_commands(server, ada, "resident Bea")  # type: ignore[arg-type]
    process_login(server, bea, "")  # type: ignore[arg-type]
    assert server.sent[-1] == "Password cannot be empty.\nSet a password: "
    process_login(server, bea, "x" * 73)  # type: ignore[arg-type]
    assert server.sent[-1] == "Password is too long.\nSet a password: "
    process_login(server, bea, "one")  # type: ignore[arg-type]
    process_login(server, bea, "two")  # type: ignore[arg-type]
    assert server.sent[-1] == "Those passwords do not match.\nSet a password: "
    assert bea.auth == "set"
    assert bea.player is not None
    assert load_saved(player_path(tmp_path, "Bea")) is None


def test_bob_signs_in_as_the_saved_spelling(tmp_path: Path) -> None:
    """Typing bob enters as Bob, and tell and examine find that spelling."""
    save_player(tmp_path, SavedPlayer("Bob", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "bob")  # type: ignore[arg-type]
    assert server.sent == ["Password: "]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.name == "Bob"
    assert player.player.lowercase_name == "bob"
    server.people = [player]
    server.sent.clear()
    teller = newbie("Ada")
    server.people.append(teller)
    process_commands(server, teller, "tell bob hello")  # type: ignore[arg-type]
    assert any("Bob" in line for line in server.sent)
    server.sent.clear()
    process_commands(server, teller, "examine bob")  # type: ignore[arg-type]
    assert server.sent
    assert "Bob".center(72) in server.sent[0]
    assert "Player bob not found." not in server.sent[0]


def test_a_saved_name_asks_for_its_password(tmp_path: Path) -> None:
    """A matching saved file asks for the password and enters with its rank."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    assert server.sent == ["Password: "]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.name == "Bea"
    assert player.player.rank is Rank.RESIDENT
    assert server.sent[-1] == "What? > "


def test_a_saved_resident_whose_password_is_quit_can_enter(tmp_path: Path) -> None:
    """``quit`` at the password prompt is checked, not treated as disconnect."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("quit", rounds=4)))
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    assert server.sent == ["Password: "]
    process_input(server, player, "quit")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.name == "Bea"
    assert player.player.rank is Rank.RESIDENT
    assert player not in server.dropped_clients
    assert server.sent[-1] == "What? > "


def test_another_spelling_of_a_saved_name_is_refused(tmp_path: Path) -> None:
    """Enter as the saved spelling after typing another case."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "bea")  # type: ignore[arg-type]
    assert server.sent == ["Password: "]
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.name == "Bea"


def test_a_removed_player_does_not_enter_on_a_correct_password(tmp_path: Path) -> None:
    """The file is gone by the time the password arrives, so the name prompt returns."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    assert server.sent == ["Password: "]
    assert delete_player(tmp_path, "Bea") is True
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.player is None
    assert player.auth == "name"
    assert server.sent[-1] == name_prompt()


def test_a_changed_rank_is_used_when_the_password_arrives(tmp_path: Path) -> None:
    """The rank comes from the file at the time of the correct password."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    process_input(server, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.rank == Rank.RESIDENT


def test_a_taken_saved_name_still_asks_for_the_password(tmp_path: Path) -> None:
    """A saved name that is in a room is not refused before the password."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    live = FakePlayer(player=Player("Bea", "hall", datetime(2026, 10, 12, tzinfo=timezone.utc), alarm_seconds=120, idle_seconds=40, prompt="Go: ", rank=Rank.SUPER_USER))  # fmt: skip  # noqa: E501
    server.people = [live]
    newcomer = FakePlayer(player=None, address="10.0.0.5")
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    assert server.sent == ["Password: "]
    assert newcomer.player is None
    assert live.player is not None
    assert live.player.idle_seconds == 40


def test_the_right_password_reconnects_the_live_session(tmp_path: Path) -> None:
    """The same player stays, idle clears, and the room hears reconnected."""
    logged_out = datetime(2020, 1, 1, tzinfo=timezone.utc)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), logged_out_at=logged_out, ip_address="192.168.0.9"))  # fmt: skip  # noqa: E501
    server = login_host(tmp_path)
    connected_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
    user = Player("Bea", "hall", connected_at, connected_seconds=500, alarm_seconds=120, idle_seconds=40, prompt="Go: ", rank=Rank.SUPER_USER)
    live = FakePlayer(player=user, address="192.168.0.9")
    server.people = [live]
    server.occupied = 99
    newcomer = FakePlayer(player=None, address="10.0.0.5")
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    server.sent.clear()
    server.room_lines.clear()
    process_input(server, newcomer, "pw")  # type: ignore[arg-type]
    assert server.replaced == [live]
    assert live.player is user
    assert user.room == "hall"
    assert user.connected_at == connected_at
    assert user.connected_seconds == 500
    assert user.alarm_seconds == 120
    assert user.prompt == "Go: "
    assert user.idle_seconds == 0
    assert user.rank == Rank.RESIDENT
    assert live.address == "10.0.0.5"
    assert newcomer.player is None
    assert server.dropped is False
    assert server.room_lines == ["[Bea has reconnected]\n"]
    assert any(text == server.layout.brief("hall", []) for text in server.sent)
    assert "has connected" not in "".join(server.room_lines)
    assert "has disconnected" not in "".join(server.room_lines)
    assert server.sent[-1] == "Go: "
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.ip_address == "10.0.0.5"
    assert saved.hostname == "10.0.0.5"
    assert saved.logged_out_at == logged_out


def test_a_reconnect_drops_an_unfinished_password_change(tmp_path: Path) -> None:
    """Lines after a reconnect are commands, not answers to the old password change."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    before = load_saved(player_path(tmp_path, "Bea"))
    assert before is not None
    server = login_host(tmp_path)
    live = FakePlayer(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server.people = [live]
    process_input(server, live, "password")  # type: ignore[arg-type]
    process_input(server, live, "pw")  # type: ignore[arg-type]
    assert live.auth == "renew"
    newcomer = FakePlayer(player=None, address="10.0.0.5")
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    process_input(server, newcomer, "pw")  # type: ignore[arg-type]
    assert server.replaced == [live]
    assert live.auth == "name"
    assert live.pending_password == ""
    server.sent.clear()
    process_input(server, live, "who")  # type: ignore[arg-type]
    process_input(server, live, "who")  # type: ignore[arg-type]
    output = "".join(server.sent)
    assert output.count("Bea") >= 2
    assert "Password changed." not in output
    assert "Set a password" not in output
    assert "Confirm password" not in output
    after = load_saved(player_path(tmp_path, "Bea"))
    assert after is not None
    assert after.password_hash == before.password_hash


def test_a_taken_newbie_name_is_still_refused(tmp_path: Path) -> None:
    """A name with no file does not get a password just because it is in use."""
    server = login_host(tmp_path)
    live = FakePlayer(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.NEWBIE))
    server.people = [live]
    newcomer = FakePlayer(player=None)
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    assert server.sent == ["Bea is already in use. Pick another name.\n\nPlease enter your name: "]
    assert server.replaced == []
    assert live.player is not None
    assert live.player.idle_seconds == 0


def test_another_case_reconnects_the_live_session(tmp_path: Path) -> None:
    """Reconnect the live session after another case and the right password."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    live = FakePlayer(player=user)
    server.people = [live]
    newcomer = FakePlayer(player=None)
    process_input(server, newcomer, "bea")  # type: ignore[arg-type]
    assert server.sent == ["Password: "]
    process_input(server, newcomer, "pw")  # type: ignore[arg-type]
    assert server.replaced == [live]
    assert user.name == "Bea"


def test_a_wrong_password_leaves_the_live_session(tmp_path: Path) -> None:
    """Three failures drop only the new connection."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    live = FakePlayer(player=Player("Bea", "hall", datetime.now(timezone.utc), idle_seconds=40, rank=Rank.RESIDENT))
    server.people = [live]
    newcomer = FakePlayer(player=None)
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    for _ in range(3):
        process_input(server, newcomer, "no")  # type: ignore[arg-type]
    assert server.dropped_clients == [newcomer]
    assert live not in server.dropped_clients
    assert live.player is not None
    assert live.player.idle_seconds == 40
    assert server.replaced == []
    assert server.room_lines == []


def test_a_missing_file_does_not_reconnect(tmp_path: Path) -> None:
    """The file is gone when the password arrives, so the live player stays."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    live = FakePlayer(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server.people = [live]
    newcomer = FakePlayer(player=None)
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    assert delete_player(tmp_path, "Bea") is True
    process_input(server, newcomer, "pw")  # type: ignore[arg-type]
    assert server.replaced == []
    assert newcomer.player is None
    assert newcomer.auth == "name"
    assert live.player is not None
    assert live.player.room == "hall"


def test_a_saved_name_that_has_left_still_connects(tmp_path: Path) -> None:
    """No live player means a normal connect line."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    newcomer = FakePlayer(player=None, address="10.0.0.5")
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    process_input(server, newcomer, "pw")  # type: ignore[arg-type]
    assert server.replaced == []
    assert newcomer.player is not None
    assert newcomer.player.room == "reception"
    assert server.room_lines == ["Bea has connected.\n"]


def test_a_remote_new_name_is_a_newbie(tmp_path: Path) -> None:
    """A new name from another machine reaches the disclaimer."""
    server = login_host(tmp_path, super_admin=None)
    remote = FakePlayer(player=None, address="10.0.0.5")
    process_input(server, remote, "Bea")  # type: ignore[arg-type]
    assert remote.auth == "agree"
    assert "The admin must set a password" not in "".join(server.sent)


def test_resident_of_the_admin_name_starts_no_pair(tmp_path: Path) -> None:
    """The admin in any case is refused, and nobody is asked for a password."""
    admin = newbie("admin")
    server = login_host(tmp_path)
    server.people = [admin]
    ada = staff("Ada", Rank.SUPER_USER)
    for line in ("resident admin", "resident Admin", "resident ADMIN"):
        server.sent.clear()
        process_commands(server, ada, line)  # type: ignore[arg-type]
        assert server.sent == ["The super admin cannot be removed.\n"]
    assert server.staff_lines == []
    assert admin.auth == "name"


def test_three_wrong_passwords_drop_the_player(tmp_path: Path) -> None:
    """Each wrong password is answered. The third one closes the connection."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "Bea")  # type: ignore[arg-type]
    process_input(server, player, "no")  # type: ignore[arg-type]
    assert server.sent[-2:] == ["Wrong password.\n", "Password: "]
    process_input(server, player, "no")  # type: ignore[arg-type]
    assert server.dropped is False
    process_input(server, player, "no")  # type: ignore[arg-type]
    assert server.sent[-1] == "Wrong password.\n"
    assert server.dropped is True
    assert player.player is None


def test_a_name_that_cannot_be_a_file_is_a_newbie(tmp_path: Path) -> None:
    """A name with path characters never reaches the players directory."""
    server = login_host(tmp_path)
    player = FakePlayer(player=None)
    process_input(server, player, "../evil")  # type: ignore[arg-type]
    assert player.player is None
    assert server.sent[-1] == ("A name can use only letters and digits.\n\nPlease enter your name: ")


def staff(name: str, rank: Rank) -> FakePlayer:
    user = Player(name, "reception", datetime.now(timezone.utc), rank=rank)
    return FakePlayer(name=name, player=user)


def test_another_capitalisation_of_a_newbie_is_in_use(tmp_path: Path) -> None:
    """Bea and BEA are the newbie already in the room."""
    server = login_host(tmp_path)
    bea = FakePlayer(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.NEWBIE))
    server.people = [bea]
    for typed in ("bea", "BEA"):
        server.sent.clear()
        newcomer = FakePlayer(player=None)
        process_input(server, newcomer, typed)  # type: ignore[arg-type]
        assert server.sent == [f"{typed} is already in use. Pick another name.\n\n{name_prompt()}"]
        assert newcomer.player is None
    assert bea.player is not None
    assert bea.player.name == "Bea"


def test_a_newbie_who_quits_frees_the_name(tmp_path: Path) -> None:
    """After quit, the other capitalisation can enter, and no file is written."""
    server = login_host(tmp_path)
    bea = FakePlayer(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.NEWBIE))
    server.people = [bea]
    process_input(server, bea, "quit")  # type: ignore[arg-type]
    assert bea in server.dropped_clients
    server.sent.clear()
    later = FakePlayer(player=None)
    process_input(server, later, "bea")  # type: ignore[arg-type]
    process_input(server, later, "yes")  # type: ignore[arg-type]
    assert later.player is not None
    assert later.player.name == "bea"
    assert later.player.rank == Rank.NEWBIE
    assert load_saved(player_path(tmp_path, "bea")) is None


def test_a_newbie_setting_a_resident_password_still_holds_the_name(tmp_path: Path) -> None:
    """The name stays taken until confirm writes the file."""
    server = login_host(tmp_path)
    bea = FakePlayer(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.NEWBIE))
    server.people = [bea]
    issuer = staff("Ada", Rank.SUPER_USER)
    server.people.append(issuer)
    process_commands(server, issuer, "resident Bea")  # type: ignore[arg-type]
    assert bea.auth == "set"
    newcomer = FakePlayer(player=None)
    process_input(server, newcomer, "bea")  # type: ignore[arg-type]
    assert server.sent[-1] == f"bea is already in use. Pick another name.\n\n{name_prompt()}"
    assert load_saved(player_path(tmp_path, "Bea")) is None
    process_input(server, bea, "quit")  # type: ignore[arg-type]
    server.sent.clear()
    process_input(server, newcomer, "bea")  # type: ignore[arg-type]
    process_input(server, newcomer, "yes")  # type: ignore[arg-type]
    assert newcomer.player is not None
    assert newcomer.player.name == "bea"
    assert load_saved(player_path(tmp_path, "bea")) is None


def test_a_saved_player_at_the_password_prompt_does_not_hold_the_name(tmp_path: Path) -> None:
    """Ask for the password again, including another capitalisation."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    waiting = FakePlayer(player=None, address="10.0.0.5")
    server.people = [waiting]
    process_input(server, waiting, "Bea")  # type: ignore[arg-type]
    assert waiting.auth == "password"
    server.sent.clear()
    other = FakePlayer(player=None, address="10.0.0.5")
    process_input(server, other, "Bea")  # type: ignore[arg-type]
    assert server.sent == ["Password: "]
    server.sent.clear()
    spelling = FakePlayer(player=None, address="10.0.0.5")
    process_input(server, spelling, "bea")  # type: ignore[arg-type]
    assert server.sent == ["Password: "]


def newbie(name: str) -> FakePlayer:
    return FakePlayer(name=name, room="hall", player=Player(name, "hall", datetime.now(timezone.utc)))


def save(tmp_path: Path, name: str, rank: Rank) -> None:
    save_player(tmp_path, SavedPlayer(name, rank, hash_password("pw", rounds=4)))


def test_resident_saves_a_connected_newbie(tmp_path: Path) -> None:
    """The newbie is asked for a password pair, and is saved only after the confirm."""
    bea = newbie("Bea")
    ada = staff("Ada", Rank.SUPER_USER)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "resident Bea")  # type: ignore[arg-type]
    assert server.staff_lines == [paint_staff(ada, "< SU > Ada makes Bea a resident.\n")]
    assert server.sent == ["Set a password: "]
    assert bea.auth == "set"
    assert bea.player is not None
    assert bea.player.rank is Rank.NEWBIE
    assert load_saved(player_path(tmp_path, "Bea")) is None

    process_input(server, bea, "hunter2")  # type: ignore[arg-type]
    assert server.sent[-1] == "\n\nConfirm password: "
    assert bea.player.rank is Rank.NEWBIE
    assert load_saved(player_path(tmp_path, "Bea")) is None

    process_input(server, bea, "hunter2")  # type: ignore[arg-type]
    assert bea.player.rank is Rank.RESIDENT
    assert bea.auth == "name"
    assert server.sent[-3:] == ["You are now a resident.\n", "Bea is now a resident.\n", "What? > "]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.RESIDENT
    assert password_ok("hunter2", saved.password_hash)


def test_resident_staff_line_is_sent_at_command_time_only(tmp_path: Path) -> None:
    """Staff hear who made the resident once; promotion does not broadcast again."""
    bea = newbie("Bea")
    ada = staff("Ada", Rank.SUPER_USER)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "resident Bea")  # type: ignore[arg-type]
    staff_line = paint_staff(ada, "< SU > Ada makes Bea a resident.\n")
    assert server.staff_lines == [staff_line]
    process_input(server, bea, "hunter2")  # type: ignore[arg-type]
    process_input(server, bea, "hunter2")  # type: ignore[arg-type]
    assert server.staff_lines == [staff_line]
    assert "Bea is now a resident.\n" in ada.sent


def test_resident_pair_refuses_a_bad_password(tmp_path: Path) -> None:
    """A mismatch returns to the first prompt and still writes nothing."""
    bea = newbie("Bea")
    server = login_host(tmp_path)
    server.people = [bea]
    process_commands(server, staff("Ada", Rank.SUPER_USER), "resident Bea")  # type: ignore[arg-type]
    process_input(server, bea, "")  # type: ignore[arg-type]
    assert server.sent[-1] == "Password cannot be empty.\nSet a password: "
    process_input(server, bea, "one")  # type: ignore[arg-type]
    process_input(server, bea, "two")  # type: ignore[arg-type]
    assert server.sent[-1] == "Those passwords do not match.\nSet a password: "
    assert bea.auth == "set"
    assert bea.player is not None
    assert bea.player.rank is Rank.NEWBIE
    assert load_saved(player_path(tmp_path, "Bea")) is None


def test_resident_refusals(tmp_path: Path) -> None:
    """Each refusal names the typed name and starts no password pair."""
    save(tmp_path, "Cyd", Rank.RESIDENT)
    odd = newbie("a/b")
    server = login_host(tmp_path)
    server.people = [odd, newbie("Bea")]
    ada = staff("Ada", Rank.SUPER_USER)
    for line, reply in (("resident Zed", "No such player 'Zed'.\n"), ("resident Cyd", "Cyd is already saved.\n"), ("resident cyd", "cyd is already saved.\n"), ("resident a/b", "That name cannot be saved.\n")):  # fmt: skip  # noqa: E501
        server.sent.clear()
        process_commands(server, ada, line)  # type: ignore[arg-type]
        assert server.sent == [reply]
    assert server.staff_lines == []
    assert all(person.auth == "name" for person in server.people)
    assert odd.auth == "name"


def test_resident_needs_super_and_a_name(tmp_path: Path) -> None:
    """A resident does not see the command. Super sees the syntax with no name."""
    server = login_host(tmp_path)
    resident = staff("Ada", Rank.RESIDENT)
    process_commands(server, resident, "resident Bea")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'resident'\n"]
    server = login_host(tmp_path)
    process_commands(server, staff("Ada", Rank.SUPER_USER), "resident")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: resident <name>\n"]


def test_nuke_deletes_the_file(tmp_path: Path) -> None:
    """Nuking an offline resident deletes the file and says so to staff."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    process_commands(server, ada, "nuke Bea spam")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")) is None
    assert server.staff_lines == [paint_staff(ada, "< SU > Ada nukes Bea: spam\n")]
    assert server.dropped_clients == []


def test_nuke_deletes_a_saved_name_with_a_space(tmp_path: Path) -> None:
    """A saved name with a space is the whole name, and the rest is the reason."""
    save_player(tmp_path, SavedPlayer("BeaSmith", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    process_commands(server, ada, "nuke Bea")  # type: ignore[arg-type]
    assert server.sent[-1] == "Syntax: nuke <name> <reason>\n"
    assert load_saved(player_path(tmp_path, "BeaSmith")) is not None
    process_commands(server, ada, "nuke BeaSmith")  # type: ignore[arg-type]
    assert server.sent[-1] == "Syntax: nuke <name> <reason>\n"
    assert load_saved(player_path(tmp_path, "BeaSmith")) is not None
    process_commands(server, ada, "nuke BeaSmith spam")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "BeaSmith")) is None
    assert server.staff_lines == [paint_staff(ada, "< SU > Ada nukes BeaSmith: spam\n")]


def test_nuke_removes_a_connected_player(tmp_path: Path) -> None:
    """A connected target is told, then dropped with the disconnect line."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [bea, ada]
    process_commands(server, ada, "nuke bea  being rude")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")) is None
    assert server.sent == ["You have been removed.\n"]
    assert server.dropped_clients == [bea]
    assert server.staff_lines == [paint_staff(ada, "< SU > Ada nukes Bea: being rude\n")]


def test_nuke_refuses_a_connected_newbie(tmp_path: Path) -> None:
    """A connected newbie has no saved file, so nuke leaves them in place."""
    bea = newbie("Bea")
    server = login_host(tmp_path)
    server.people = [bea]
    process_commands(server, staff("Ada", Rank.SUPER_USER), "nuke Bea spam")  # type: ignore[arg-type]
    assert server.sent == ["Player Bea not found.\n"]
    assert bea in server.people
    assert bea.player is not None
    assert server.dropped_clients == []
    assert server.staff_lines == []


def test_disconnect_refuses_yourself() -> None:
    """Disconnect aimed at the typist does not drop them."""
    server = FakeServer()
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    command_disconnect(server, ada, Command("disconnect", command_disconnect, False), "ada spam")
    assert ada.sent == ["You cannot do that to yourself.\n"]
    assert server.dropped_clients == []


def test_warn_syntax_without_reason() -> None:
    """Warn needs a name and a reason."""
    server = FakeServer()
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    command_warn(server, ada, Command("warn", command_warn, False, "<name> <reason>"), "Bea")
    assert ada.sent == ["Syntax: warn <name> <reason>\n"]


def test_warn_unknown_player() -> None:
    """Warn misses when the full name is not in a room."""
    server = FakeServer()
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    command_warn(server, ada, Command("warn", command_warn, False, "<name> <reason>"), "Bea stop that")
    assert ada.sent == ["No such player 'Bea'.\n"]
    assert server.staff_lines == []


def test_warn_refuses_yourself() -> None:
    """Warn aimed at the typist does not message staff."""
    server = FakeServer()
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    command_warn(server, ada, Command("warn", command_warn, False, "<name> <reason>"), "Ada stop that")
    assert ada.sent == ["You cannot do that to yourself.\n"]
    assert server.staff_lines == []


def test_warn_rejects_a_name_prefix() -> None:
    """Warn needs the full name, not a prefix."""
    server = FakeServer()
    ada = staff("Ada", Rank.SUPER_USER)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [ada, bea]
    command_warn(server, ada, Command("warn", command_warn, False, "<name> <reason>"), "Be stop that")
    assert ada.sent == ["No such player 'Be'.\n"]
    assert bea.sent == []
    assert server.staff_lines == []


def test_warn_sends_a_cyan_line_and_a_beep() -> None:
    """The target sees cyan when colour is on, plain text when it is off."""
    server = FakeServer()
    ada = staff("Ada", Rank.SUPER_USER)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [ada, bea]
    assert ada.player is not None
    ada.player.colour = False
    assert bea.player is not None
    bea.player.colour = True
    command_warn(server, ada, Command("warn", command_warn, False, "<name> <reason>"), "Bea stop that")
    plain = "-=> Ada warn you: stop that"
    assert bea.sent == [f"\n\n{cyan(plain)}\n\n\n\a"]
    assert ada.sent == ["< SU > Ada warns Bea : stop that\n"]
    assert server.staff_lines == ["< SU > Ada warns Bea : stop that\n"]

    server.staff_lines.clear()
    bea.sent.clear()
    bea.player.colour = False
    command_warn(server, ada, Command("warn", command_warn, False, "<name> <reason>"), "Bea stop that")
    assert bea.sent == [f"\n\n{plain}\n\n\n\a"]
    assert ada.sent == ["< SU > Ada warns Bea : stop that\n", "< SU > Ada warns Bea : stop that\n"]
    assert server.staff_lines == ["< SU > Ada warns Bea : stop that\n"]


def test_resident_refuses_yourself() -> None:
    """Resident aimed at the typist does not start a password pair."""
    server = FakeServer()
    ada = FakePlayer()
    server.people = [ada]
    command_resident(server, ada, Command("resident", command_resident, False), "ada")
    assert ada.sent == ["You cannot do that to yourself.\n"]
    assert server.staff_lines == []
    assert ada.auth == "name"


def test_nuke_of_system_leaves_the_office_file(tmp_path: Path) -> None:
    """Nuke of a player named System leaves the office file in place."""
    players = tmp_path / "players"
    players.mkdir()
    save(players, "System", Rank.RESIDENT)
    office = tmp_path / "rooms" / "system.yml"
    office.parent.mkdir()
    text = "reception:\n  description: The desk.\n  max_occupancy: 2\n  exits: {}\n"
    office.write_text(text, encoding="utf-8")
    server = login_host(players)
    server.config = replace(server.config, rooms=office, help=tmp_path / "help.yml", players=players)
    server.layout = load_layout(office).with_void()
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    command_nuke(server, ada, Command("nuke", command_nuke, False, "nuke <name> <reason>"), "System gone")
    assert office.read_text(encoding="utf-8") == text
    assert load_saved(player_path(players, "System")) is None


def test_nuke_removes_the_house_and_leaves_the_office(tmp_path: Path) -> None:
    """Nuke removes ada.yml and leaves the office file."""
    players = tmp_path / "players"
    players.mkdir()
    save(players, "Ada", Rank.RESIDENT)
    office = tmp_path / "system.yml"
    office.write_text("reception:\n  description: The desk.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    server = login_host(players)
    server.config = replace(server.config, rooms=office, players=players)
    server.layout = load_layout(office).with_void()
    write_default_house(office, "Ada")
    assert (tmp_path / "ada.yml").is_file()
    boss = staff("Bea", Rank.SUPER_USER)
    server.people = [boss]
    command_nuke(server, boss, Command("nuke", command_nuke, False, "nuke <name> <reason>"), "Ada gone")
    assert not (tmp_path / "ada.yml").exists()
    assert office.is_file()
    assert load_saved(player_path(players, "Ada")) is None


def test_retire_removes_the_house_and_leaves_the_office(tmp_path: Path) -> None:
    """Retire removes ada.yml and leaves the office file."""
    players = tmp_path / "players"
    players.mkdir()
    save(players, "Ada", Rank.RESIDENT)
    office = tmp_path / "system.yml"
    office.write_text("reception:\n  description: The desk.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    server = login_host(players)
    server.config = replace(server.config, rooms=office, players=players)
    server.layout = load_layout(office).with_void()
    write_default_house(office, "Ada")
    ada = staff("Ada", Rank.RESIDENT)
    server.people = [ada]
    process_input(server, ada, "retire")  # type: ignore[arg-type]
    process_input(server, ada, "yes")  # type: ignore[arg-type]
    process_input(server, ada, "pw")  # type: ignore[arg-type]
    assert not (tmp_path / "ada.yml").exists()
    assert office.is_file()
    assert load_saved(player_path(players, "Ada")) is None


def test_nuke_refuses_yourself(tmp_path: Path) -> None:
    """Nuke aimed at the typist does not delete their file."""
    save(tmp_path, "Ada", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    command_nuke(server, ada, Command("nuke", command_nuke, False), "ada gone")
    assert ada.sent == ["You cannot do that to yourself.\n"]
    assert load_saved(player_path(tmp_path, "Ada")) is not None
    assert server.staff_lines == []


def test_promote_refuses_yourself(tmp_path: Path) -> None:
    """Promote aimed at the typist does not raise their rank."""
    save(tmp_path, "Ada", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.ADMIN)
    server.people = [ada]
    command_promote(server, ada, Command("promote", command_promote, False), "ada")
    assert ada.sent == ["You cannot do that to yourself.\n"]
    saved = load_saved(player_path(tmp_path, "Ada"))
    assert saved is not None
    assert saved.rank == Rank.RESIDENT
    assert server.staff_lines == []


def test_demote_refuses_yourself(tmp_path: Path) -> None:
    """Demote aimed at the typist does not lower their rank."""
    save(tmp_path, "Ada", Rank.SUPER_USER)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.ADMIN)
    server.people = [ada]
    command_demote(server, ada, Command("demote", command_demote, False), "ada")
    assert ada.sent == ["You cannot do that to yourself.\n"]
    saved = load_saved(player_path(tmp_path, "Ada"))
    assert saved is not None
    assert saved.rank == Rank.SUPER_USER
    assert server.staff_lines == []


def test_disconnect_drops_a_resident_and_keeps_the_file(tmp_path: Path) -> None:
    """Staff are told, the resident is told, and the file stays."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [bea, ada]
    process_commands(server, ada, "disconnect Bea spam")  # type: ignore[arg-type]
    assert server.sent == ["You have been disconnected.\n"]
    assert server.dropped_clients == [bea]
    assert server.announced == [True]
    assert server.staff_lines == [paint_staff(ada, "< SU > Ada disconnects Bea: spam\n")]
    assert load_saved(player_path(tmp_path, "Bea")) is not None


def test_disconnect_refuses_the_same_rank_or_higher(tmp_path: Path) -> None:
    """A super user cannot drop a super user, the admin, or themselves."""
    server = login_host(tmp_path)
    cy = staff("Cyd", Rank.SUPER_USER)
    boss = staff("Boss", Rank.ADMIN)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [cy, boss, ada]
    process_commands(server, ada, "disconnect Cyd spam")  # type: ignore[arg-type]
    assert server.sent == ["You cannot disconnect Cyd.\n"]
    server.sent.clear()
    process_commands(server, ada, "disconnect Boss spam")  # type: ignore[arg-type]
    assert server.sent == ["You cannot disconnect Boss.\n"]
    server.sent.clear()
    process_commands(server, ada, "disconnect Ada spam")  # type: ignore[arg-type]
    assert server.sent == ["You cannot do that to yourself.\n"]
    assert server.dropped_clients == []
    assert server.staff_lines == []


def test_an_admin_can_disconnect_a_super_user_but_not_themselves(tmp_path: Path) -> None:
    """A super user is below an admin and is dropped. An admin cannot drop themselves."""
    server = login_host(tmp_path)
    cy = staff("Cyd", Rank.SUPER_USER)
    boss = staff("Boss", Rank.ADMIN)
    server.people = [cy, boss]
    process_commands(server, boss, "disconnect Cyd spam")  # type: ignore[arg-type]
    assert server.sent == ["You have been disconnected.\n"]
    assert server.dropped_clients == [cy]
    server.sent.clear()
    process_commands(server, boss, "disconnect Boss spam")  # type: ignore[arg-type]
    assert server.sent == ["You cannot do that to yourself.\n"]
    assert server.dropped_clients == [cy]


def test_nuke_refuses_the_same_rank_or_higher(tmp_path: Path) -> None:
    """A super user or a promoted admin cannot nuke their rank or above."""
    save(tmp_path, "Bea", Rank.ADMIN)
    save(tmp_path, "Cyd", Rank.SUPER_USER)
    server = login_host(tmp_path)
    sue = staff("Sue", Rank.SUPER_USER)
    boss = staff("Boss", Rank.ADMIN)
    server.people = [sue, boss]
    process_commands(server, sue, "nuke Bea spam")  # type: ignore[arg-type]
    assert server.sent == ["You cannot nuke Bea.\n"]
    server.sent.clear()
    process_commands(server, sue, "nuke Cyd spam")  # type: ignore[arg-type]
    assert server.sent == ["You cannot nuke Cyd.\n"]
    server.sent.clear()
    process_commands(server, boss, "nuke Bea spam")  # type: ignore[arg-type]
    assert server.sent == ["You cannot nuke Bea.\n"]
    assert load_saved(player_path(tmp_path, "Bea")) is not None
    assert load_saved(player_path(tmp_path, "Cyd")) is not None
    assert server.staff_lines == []


def test_the_config_admin_can_nuke_another_admin(tmp_path: Path) -> None:
    """The super admin may nuke another admin."""
    save(tmp_path, "Bea", Rank.ADMIN)
    server = login_host(tmp_path)
    boss = staff("admin", Rank.SUPER_ADMIN)
    server.people = [boss]
    process_commands(server, boss, "nuke Bea spam")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")) is None
    assert server.staff_lines == [paint_staff(boss, "< SU > admin nukes Bea: spam\n")]


def test_a_promoted_admin_cannot_disconnect_another_admin(tmp_path: Path) -> None:
    """Same rank is refused when the caller is not the config admin."""
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.ADMIN)
    boss = staff("Boss", Rank.ADMIN)
    server.people = [bea, boss]
    process_commands(server, boss, "disconnect Bea spam")  # type: ignore[arg-type]
    assert server.sent == ["You cannot disconnect Bea.\n"]
    assert server.dropped_clients == []


def test_the_config_admin_can_disconnect_another_admin(tmp_path: Path) -> None:
    """The super admin may disconnect another admin."""
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.ADMIN)
    boss = staff("admin", Rank.SUPER_ADMIN)
    server.people = [bea, boss]
    process_commands(server, boss, "disconnect Bea spam")  # type: ignore[arg-type]
    assert server.sent == ["You have been disconnected.\n"]
    assert server.dropped_clients == [bea]


def test_disconnect_of_a_login_name_is_not_found(tmp_path: Path) -> None:
    """A name that is only at a password prompt is not a target."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    waiting = FakePlayer(name=None, room=None, player=None)
    process_input(server, waiting, "Bea")  # type: ignore[arg-type]
    assert waiting.auth == "password"
    server.people = [waiting]
    server.sent.clear()
    process_commands(server, staff("Ada", Rank.SUPER_USER), "disconnect Bea spam")  # type: ignore[arg-type]
    assert server.sent == ["No such player 'Bea'.\n"]
    assert server.dropped_clients == []


def test_disconnect_finds_a_name_with_a_space(tmp_path: Path) -> None:
    """The first word is the name, and the rest is the reason."""
    bea = FakePlayer(player=Player("BeaSmith", "hall", datetime.now(timezone.utc), rank=Rank.NEWBIE))
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.ADMIN)
    server.people = [bea, ada]
    process_commands(server, ada, "disconnect BeaSmith being rude")  # type: ignore[arg-type]
    assert server.sent == ["You have been disconnected.\n"]
    assert server.staff_lines == [paint_staff(ada, "< SU > Ada disconnects BeaSmith: being rude\n")]
    assert server.dropped_clients == [bea]


def test_a_resident_cannot_disconnect(tmp_path: Path) -> None:
    """The command does not exist below super user."""
    server = login_host(tmp_path)
    process_commands(server, staff("Bea", Rank.RESIDENT), "disconnect Ada spam")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'disconnect'\n"]
    assert "disconnect" not in command_words(Rank.RESIDENT).split()
    assert "disconnect" not in command_words(Rank.NEWBIE).split()
    assert "disconnect" in command_words(Rank.SUPER_USER).split()
    assert "disconnect" in command_words(Rank.ADMIN).split()


def test_a_promoted_admin_cannot_nuke_another_admin(tmp_path: Path) -> None:
    """Same rank is refused, and the super admin can nuke a promoted admin."""
    save(tmp_path, "Bea", Rank.ADMIN)
    server = login_host(tmp_path)
    boss = staff("Boss", Rank.ADMIN)
    server.people = [boss]
    process_commands(server, boss, "nuke Bea spam")  # type: ignore[arg-type]
    assert server.sent == ["You cannot nuke Bea.\n"]
    assert load_saved(player_path(tmp_path, "Bea")) is not None
    server.sent.clear()
    chief = staff("admin", Rank.SUPER_ADMIN)
    server.people.append(chief)
    process_commands(server, chief, "nuke Bea spam")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")) is None


def test_nuke_refusals(tmp_path: Path) -> None:
    """Unknown names and the admin are refused, and the admin file stays."""
    save(tmp_path, "admin", Rank.SUPER_ADMIN)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    process_commands(server, ada, "nuke Bea spam")  # type: ignore[arg-type]
    process_commands(server, ada, "nuke admin spam")  # type: ignore[arg-type]
    process_commands(server, ada, "nuke ADMIN spam")  # type: ignore[arg-type]
    assert server.sent == [
        "Player Bea not found.\n",
        "You cannot nuke admin.\n",
        "You cannot nuke admin.\n",
    ]
    assert load_saved(player_path(tmp_path, "admin")) is not None
    assert server.staff_lines == []
    process_commands(server, ada, "nuke Bea")  # type: ignore[arg-type]
    assert server.sent[-1] == "Syntax: nuke <name> <reason>\n"


def test_promote_refuses_a_logged_off_player(tmp_path: Path) -> None:
    """Promote runs only when that player is in a room."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    save(tmp_path, "Cyd", Rank.ADMIN)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.ADMIN)
    server.people = [ada]
    process_commands(server, ada, "promote Bea")  # type: ignore[arg-type]
    assert server.sent == ["No such player 'Bea'.\n"]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.RESIDENT
    server.sent.clear()
    process_commands(server, ada, "promote Cyd")  # type: ignore[arg-type]
    assert server.sent == ["No such player 'Cyd'.\n"]
    saved = load_saved(player_path(tmp_path, "Cyd"))
    assert saved is not None
    assert saved.rank is Rank.ADMIN


def test_promote_refuses_a_resident_with_no_file(tmp_path: Path) -> None:
    """A resident in the room still needs a file before the rank can be written."""
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    ada = staff("Ada", Rank.ADMIN)
    server.people = [bea, ada]
    process_commands(server, ada, "promote Bea")  # type: ignore[arg-type]
    assert server.sent == ["No saved player 'Bea'.\n"]
    assert bea.player is not None
    assert bea.player.rank is Rank.RESIDENT


def test_promote_refuses_a_connected_player_with_no_mfa(tmp_path: Path) -> None:
    """A resident in the room still needs a secret when MFA is on."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    server.mfa_enabled = True
    bea = staff("Bea", Rank.RESIDENT)
    ada = staff("Ada", Rank.ADMIN)
    server.people = [bea, ada]
    process_commands(server, ada, "promote Bea")  # type: ignore[arg-type]
    assert server.sent == ["Bea must turn on MFA first.\n"]


def test_demote_a_logged_off_super_user_writes_su_channel(tmp_path: Path) -> None:
    """Demote still rewrites the file when the target is not in a room."""
    save(tmp_path, "Bea", Rank.SUPER_USER)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.ADMIN)
    server.people = [ada]
    process_commands(server, ada, "demote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.SU_CHANNEL


def test_promote_makes_a_resident_an_su_channel(tmp_path: Path) -> None:
    """Promote rewrites the file and raises the live rank one step."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.ADMIN)
    server.people = [bea, ada]
    process_commands(server, ada, "promote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.SU_CHANNEL
    assert bea.player is not None
    assert bea.player.rank is Rank.SU_CHANNEL
    promoted = "< SU > Ada promotes Bea to SU Channel.\n"
    assert server.staff_lines == [paint_staff(bea, promoted), paint_staff(ada, promoted)]


def test_a_coder_can_promote_a_super_user_to_admin(tmp_path: Path) -> None:
    """A coder lifts a super user to admin on the staff channel only."""
    save(tmp_path, "Bea", Rank.SUPER_USER)
    bea = staff("Bea", Rank.SUPER_USER)
    ada = staff("Ada", Rank.CODER)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "promote Bea")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")).rank is Rank.ADMIN
    line = "< SU > Ada promotes Bea to Admin.\n"
    assert server.staff_lines == [paint_staff(bea, line), paint_staff(ada, line)]
    assert server.admin_lines == []


def test_a_coder_cannot_promote_an_admin_to_coder(tmp_path: Path) -> None:
    """Only the super admin can put someone on Coder."""
    save(tmp_path, "Bea", Rank.ADMIN)
    bea = staff("Bea", Rank.ADMIN)
    ada = staff("Ada", Rank.CODER)
    boss = staff("Dee", Rank.SUPER_ADMIN)
    assert bea.player is not None
    assert ada.player is not None
    assert boss.player is not None
    bea.player.colour = False
    ada.player.colour = False
    boss.player.colour = False
    server = login_host(tmp_path)
    server.people = [bea, ada, boss]
    process_commands(server, ada, "promote Bea")  # type: ignore[arg-type]
    assert server.sent == ["Only the super admin can promote Bea to Coder.\n"]
    assert load_saved(player_path(tmp_path, "Bea")).rank is Rank.ADMIN
    server.sent.clear()
    process_commands(server, boss, "promote Bea")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")).rank is Rank.CODER
    assert server.admin_lines == []
    assert any(line.endswith("promotes Bea to Coder.\n") for line in server.staff_lines)


def test_a_coder_cannot_demote_a_coder(tmp_path: Path) -> None:
    """A coder who demotes a coder is told only the super admin can."""
    save(tmp_path, "Bea", Rank.CODER)
    bea = staff("Bea", Rank.CODER)
    ada = staff("Ada", Rank.CODER)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "demote Bea")  # type: ignore[arg-type]
    assert server.sent == ["Only the super admin can demote Bea from Coder.\n"]
    server.sent.clear()
    process_commands(server, ada, "demote Bea force")  # type: ignore[arg-type]
    assert server.sent == ["Only the super admin can demote Bea from Coder.\n"]
    assert load_saved(player_path(tmp_path, "Bea")).rank is Rank.CODER


def test_a_coder_can_force_an_admin_to_resident(tmp_path: Path) -> None:
    """A coder can force an admin down to resident on the staff channel only."""
    save(tmp_path, "Bea", Rank.ADMIN)
    bea = staff("Bea", Rank.ADMIN)
    ada = staff("Ada", Rank.CODER)
    assert ada.player is not None
    ada.player.colour = False
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "demote Bea force")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")).rank is Rank.RESIDENT
    assert server.admin_lines == []
    assert any(line.endswith("demotes Bea from Admin.\n") for line in server.staff_lines)


def test_an_off_duty_coder_misses_the_promote_line(tmp_path: Path) -> None:
    """The staff announcement skips a coder who is off duty."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    bea = staff("Bea", Rank.RESIDENT)
    ada = staff("Ada", Rank.CODER)
    boss = staff("Dee", Rank.SUPER_ADMIN)
    assert ada.player is not None
    ada.player.off_duty = True
    server = login_host(tmp_path)
    server.people = [bea, ada, boss]
    process_commands(server, boss, "promote Bea")  # type: ignore[arg-type]
    assert not any("promotes" in line for line in ada.sent)
    assert any("promotes Bea to SU Channel." in line for line in boss.sent)


def test_an_admin_cannot_warn_a_coder(tmp_path: Path) -> None:
    """Warn uses may_remove, so an admin cannot warn a coder."""
    bea = staff("Bea", Rank.CODER)
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "warn Bea stop")  # type: ignore[arg-type]
    assert server.sent == ["You cannot warn Bea.\n"]
    assert bea.sent == []


def test_a_coder_can_warn_an_admin(tmp_path: Path) -> None:
    """A coder outranks an admin, so the warning is delivered."""
    bea = staff("Bea", Rank.ADMIN)
    ada = staff("Ada", Rank.CODER)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "warn Bea stop")  # type: ignore[arg-type]
    assert any("warn you: stop" in line for line in bea.sent)


def test_promote_makes_a_super_user_an_admin(tmp_path: Path) -> None:
    """The config admin lifts a super user to admin on the staff channel."""
    save(tmp_path, "Bea", Rank.SUPER_USER)
    bea = staff("Bea", Rank.SUPER_USER)
    sue = staff("Sue", Rank.SUPER_USER)
    boss = staff("admin", Rank.SUPER_ADMIN)
    server = login_host(tmp_path)
    server.people = [bea, sue, boss]
    process_commands(server, boss, "promote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.ADMIN
    assert bea.player is not None
    assert bea.player.rank is Rank.ADMIN
    line = "< SU > admin promotes Bea to Admin.\n"
    assert server.staff_lines == [paint_staff(bea, line), paint_staff(sue, line), paint_staff(boss, line)]
    assert server.admin_lines == []


def test_a_promoted_admin_cannot_promote_to_admin(tmp_path: Path) -> None:
    """A promoted admin is told only a coder or the super admin can raise someone to admin."""
    save(tmp_path, "Bea", Rank.SUPER_USER)
    bea = staff("Bea", Rank.SUPER_USER)
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "promote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.SUPER_USER
    assert bea.player is not None
    assert bea.player.rank is Rank.SUPER_USER
    assert server.sent == ["Only a coder or the super admin can promote Bea to Admin.\n"]
    assert server.admin_lines == []
    assert server.staff_lines == []


def test_promote_refusals(tmp_path: Path) -> None:
    """Promote needs the admin and a saved resident."""
    save(tmp_path, "Cyd", Rank.ADMIN)
    save(tmp_path, "admin", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [newbie("Dee"), staff("Cyd", Rank.ADMIN), staff("admin", Rank.SUPER_ADMIN)]
    boss = staff("Ada", Rank.ADMIN)
    for line, reply in (("promote Zed", "No such player 'Zed'.\n"), ("promote Dee", "Dee is not a resident.\n"), ("promote Cyd", "Only the super admin can promote Cyd to Coder.\n"), ("promote admin", "admin is the super admin.\n")):  # fmt: skip  # noqa: E501
        server.sent.clear()
        process_commands(server, boss, line)  # type: ignore[arg-type]
        assert server.sent == [reply]
    assert server.staff_lines == []
    server = login_host(tmp_path)
    process_commands(server, staff("Sam", Rank.SUPER_USER), "promote Cyd")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'promote'\n"]


def test_demote_makes_a_super_user_an_su_channel(tmp_path: Path) -> None:
    """Demote rewrites the file and lowers the live rank."""
    save(tmp_path, "Bea", Rank.SUPER_USER)
    bea = staff("Bea", Rank.SUPER_USER)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.ADMIN)
    server.people = [bea, ada]
    process_commands(server, ada, "demote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.SU_CHANNEL
    assert bea.player is not None
    assert bea.player.rank is Rank.SU_CHANNEL
    demoted = "< SU > Ada demotes Bea from Super User.\n"
    assert server.staff_lines == [paint_staff(bea, demoted), paint_staff(ada, demoted)]


def test_demote_makes_an_admin_a_super_user(tmp_path: Path) -> None:
    """The config admin drops an admin to super user on the staff channel."""
    save(tmp_path, "Bea", Rank.ADMIN)
    bea = staff("Bea", Rank.ADMIN)
    sue = staff("Sue", Rank.SUPER_USER)
    boss = staff("admin", Rank.SUPER_ADMIN)
    server = login_host(tmp_path)
    server.people = [bea, sue, boss]
    process_commands(server, boss, "demote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.SUPER_USER
    assert bea.player is not None
    assert bea.player.rank is Rank.SUPER_USER
    line = "< SU > admin demotes Bea from Admin.\n"
    assert server.staff_lines == [paint_staff(bea, line), paint_staff(sue, line), paint_staff(boss, line)]
    assert server.admin_lines == []


def test_a_promoted_admin_cannot_demote_an_admin(tmp_path: Path) -> None:
    """A promoted admin is told only a coder or the super admin can demote an admin."""
    save(tmp_path, "Bea", Rank.ADMIN)
    bea = staff("Bea", Rank.ADMIN)
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "demote Bea")  # type: ignore[arg-type]
    assert server.sent == ["Only a coder or the super admin can demote Bea from Admin.\n"]
    server.sent.clear()
    process_commands(server, ada, "demote Bea force")  # type: ignore[arg-type]
    assert server.sent == ["Only a coder or the super admin can demote Bea from Admin.\n"]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.ADMIN
    assert bea.player is not None
    assert bea.player.rank is Rank.ADMIN
    assert server.admin_lines == []
    assert server.staff_lines == []


def test_demote_force_from_admin_stays_on_staff(tmp_path: Path) -> None:
    """Force from admin lands on resident and the line is staff only."""
    save(tmp_path, "Bea", Rank.ADMIN)
    bea = staff("Bea", Rank.ADMIN)
    sue = staff("Sue", Rank.SUPER_USER)
    boss = staff("admin", Rank.SUPER_ADMIN)
    server = login_host(tmp_path)
    server.people = [bea, sue, boss]
    process_commands(server, boss, "demote Bea FORCE")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.RESIDENT
    assert bea.player is not None
    assert bea.player.rank is Rank.RESIDENT
    line = "< SU > admin demotes Bea from Admin.\n"
    assert server.staff_lines == [paint_staff(sue, line), paint_staff(boss, line)]
    assert server.admin_lines == []


def test_demote_force_from_super_user_stays_on_staff(tmp_path: Path) -> None:
    """Force from super user is the normal super-user demote."""
    save(tmp_path, "Bea", Rank.SUPER_USER)
    bea = staff("Bea", Rank.SUPER_USER)
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "demote Bea force")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.RESIDENT
    assert bea.player.rank is Rank.RESIDENT
    line = "< SU > Ada demotes Bea from Super User.\n"
    assert server.staff_lines == [paint_staff(ada, line)]
    assert server.admin_lines == []


def test_promote_walks_each_saved_step(tmp_path: Path) -> None:
    """Each promote writes the next file word on the staff channel. A coder does not go up."""
    save(tmp_path, "Bea", Rank.SU_CHANNEL)
    bea = staff("Bea", Rank.SU_CHANNEL)
    boss = staff("Ada", Rank.SUPER_ADMIN)
    assert boss.player is not None
    boss.player.colour = False
    server = login_host(tmp_path)
    server.people = [bea, boss]
    process_commands(server, boss, "promote Bea")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")).rank is Rank.SUPER_USER
    save(tmp_path, "Bea", Rank.ADMIN)
    bea.player.rank = Rank.ADMIN
    server.sent.clear()
    server.admin_lines.clear()
    server.staff_lines.clear()
    process_commands(server, boss, "promote Bea")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")).rank is Rank.CODER
    assert server.staff_lines[-1].endswith("promotes Bea to Coder.\n")
    assert server.admin_lines == []
    bea.player.rank = Rank.CODER
    server.sent.clear()
    process_commands(server, boss, "promote Bea")  # type: ignore[arg-type]
    assert server.sent == ["Bea is already a coder.\n"]


def test_force_from_coder_stays_on_staff(tmp_path: Path) -> None:
    """Force from coder lands on resident and the line is staff only."""
    save(tmp_path, "Bea", Rank.CODER)
    bea = staff("Bea", Rank.CODER)
    sue = staff("Sue", Rank.SUPER_USER)
    boss = staff("admin", Rank.SUPER_ADMIN)
    assert sue.player is not None
    assert boss.player is not None
    sue.player.colour = False
    boss.player.colour = False
    server = login_host(tmp_path)
    server.people = [bea, sue, boss]
    process_commands(server, boss, "demote Bea force")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")).rank is Rank.RESIDENT
    assert any(line.endswith("demotes Bea from Coder.\n") for line in server.staff_lines)
    assert server.admin_lines == []


def test_promote_clears_an_unknown_rank_word(tmp_path: Path) -> None:
    """An explicit promote writes the new word and skips the login notice."""
    path = player_path(tmp_path, "Bea")
    path.write_text("name: Bea\nlowercase_name: bea\nrank: super\npassword: x\n", encoding="utf-8")
    bea = staff("Bea", Rank.RESIDENT)
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "promote Bea")  # type: ignore[arg-type]
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.rank is Rank.SU_CHANNEL
    assert loaded.unknown_rank is None
    assert "rank: su_channel\n" in path.read_text(encoding="utf-8")


def test_pstats_counts_live_newbies_and_saved_ranks(tmp_path: Path) -> None:
    """Newbies are logged in. Each saved player is one rank. Roles count holders."""
    password = hash_password("pw", rounds=4)
    site = int(Roles.SITE_ADMIN)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, password))
    save_player(tmp_path, SavedPlayer("Cam", Rank.RESIDENT, password, roles=site))
    save_player(tmp_path, SavedPlayer("Dee", Rank.SU_CHANNEL, password))
    save_player(tmp_path, SavedPlayer("Eve", Rank.SUPER_USER, password))
    save_player(tmp_path, SavedPlayer("Fay", Rank.ADMIN, password, roles=site))
    save_player(tmp_path, SavedPlayer("Gus", Rank.CODER, password))
    save_player(tmp_path, SavedPlayer("Hal", Rank.SUPER_ADMIN, password))
    ada = staff("Ada", Rank.ADMIN)
    bea = FakePlayer(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.NEWBIE))
    hal = staff("Hal", Rank.SUPER_ADMIN)
    waiting = FakePlayer(player=None)
    server = FakeServer(people=[ada, bea, hal, waiting], roster_dir=tmp_path)
    process_commands(server, ada, "pstats")  # type: ignore[arg-type]
    expected = """\
+---------------------------------------+
| Newbies     : 1   Total Residents : 7 |
|---------------------------------------|
| Residents   : 2   Channel Only    : 1 |
| Super Users : 1   Admins          : 1 |
| Coders      : 1   Super Admin     : 1 |
|---------------------------------------|
| Site Admin  : 2                       |
+---------------------------------------+
"""
    assert server.sent == [expected]
    assert len({len(line) for line in expected.splitlines()}) == 1
    server.sent.clear()
    process_commands(server, ada, "pstats please")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: pstats\n"]
    resident = staff("Ida", Rank.RESIDENT)
    server.people.append(resident)
    server.sent.clear()
    process_commands(server, resident, "pstats")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'pstats'\n"]
    assert ada.player is not None
    ada.player.off_duty = True
    server.sent.clear()
    process_commands(server, ada, "pstats")  # type: ignore[arg-type]
    assert server.sent == ["You are off duty.\n"]


def test_pstats_widens_for_a_larger_count_and_shows_zero(tmp_path: Path) -> None:
    """A wider count grows the box. An empty rank is shown as zero."""
    password = hash_password("pw", rounds=4)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, password))
    ada = staff("Ada", Rank.ADMIN)
    server = FakeServer(people=[ada], roster_dir=tmp_path)
    process_commands(server, ada, "pstats")  # type: ignore[arg-type]
    short = server.sent[0]
    assert "Channel Only" in short
    assert ": 0" in short
    assert "Site Admin" in short
    for number in range(9):
        save_player(tmp_path, SavedPlayer(f"R{number:02d}", Rank.RESIDENT, password))
    server.sent.clear()
    process_commands(server, ada, "pstats")  # type: ignore[arg-type]
    long = server.sent[0]
    assert len(long.splitlines()[0]) > len(short.splitlines()[0])
    assert len({len(line) for line in long.splitlines()}) == 1
    assert "Residents   : 10" in long


def test_an_admin_cannot_force_a_coder(tmp_path: Path) -> None:
    """Only the super admin may force a coder down to resident."""
    save(tmp_path, "Bea", Rank.CODER)
    bea = staff("Bea", Rank.CODER)
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "demote Bea force")  # type: ignore[arg-type]
    assert server.sent == ["Only the super admin can demote Bea from Coder.\n"]
    assert load_saved(player_path(tmp_path, "Bea")).rank is Rank.CODER


def test_demote_refusals(tmp_path: Path) -> None:
    """Demote refuses missing names, residents, newbies, the super admin, and bad force syntax."""
    save(tmp_path, "Cyd", Rank.RESIDENT)
    server = login_host(tmp_path)
    boss = staff("Ada", Rank.ADMIN)
    server.people = [newbie("Dee")]
    for line, reply in (("demote Zed", "Player Zed not found.\n"), ("demote Cyd", "Cyd is a resident.\n"), ("demote Dee", "Dee cannot be demoted.\n"), ("demote admin", "The super admin cannot be demoted.\n"), ("demote admin force", "The super admin cannot be demoted.\n"), ("demote force Bea", "Syntax: demote <name> [force]\n"), ("demote Bea force extra", "Syntax: demote <name> [force]\n")):  # fmt: skip  # noqa: E501
        server.sent.clear()
        process_commands(server, boss, line)  # type: ignore[arg-type]
        assert server.sent == [reply]
    assert server.staff_lines == []
    loaded = load_saved(player_path(tmp_path, "admin"))
    assert loaded is not None
    assert loaded.rank is Rank.SUPER_ADMIN


def test_an_old_admin_file_for_the_config_account_becomes_super_admin(tmp_path: Path) -> None:
    """A saved admin rank is returned unchanged when the file is loaded."""
    from lupaxa.chat_server.utilities import find_saved

    save(tmp_path, "admin", Rank.ADMIN)
    server = login_host(tmp_path, super_admin=None)
    found = find_saved(server, "admin")  # type: ignore[arg-type]
    assert found is not None
    assert found.rank == Rank.ADMIN
    saved = load_saved(player_path(tmp_path, "admin"))
    assert saved is not None
    assert saved.rank == Rank.ADMIN


def test_finger_shows_when_they_logged_out(tmp_path: Path) -> None:
    """Examine gives rank, the UTC logout time with seconds, and how long ago."""
    left = datetime.now(timezone.utc) - timedelta(hours=2)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), logged_out_at=left))
    server = login_host(tmp_path)
    process_commands(server, newbie("Ada"), "examine Bea")  # type: ignore[arg-type]
    stamp = clock_phrase(left)
    body = examine_frame("Bea", (("User Rank", "Resident"), ("Last from", "unknown"), ("Logged out", stamp), ("Ago", "2 hours"), ("Total Login", "0 seconds"), *ROOM_SENTENCES), 72)
    assert server.sent == [body]
    assert "Resident" in body
    assert "Logged out" in body
    assert stamp in body
    assert "Ago" in body
    assert "2 hours" in body
    assert "Total Login" in body
    assert "0 seconds" in body
    assert "Rank:" not in body


def test_finger_with_no_logout_time_says_unknown(tmp_path: Path) -> None:
    """A file with no ``logged_out_at`` says unknown, with no Ago line."""
    save(tmp_path, "Bea", Rank.SUPER_USER)
    server = login_host(tmp_path)
    process_commands(server, newbie("Ada"), "examine Bea")  # type: ignore[arg-type]
    body = examine_frame("Bea", (("User Rank", shown_rank(Rank.SUPER_USER, True)), ("Last from", "unknown"), ("Logged out", "unknown"), ("Total Login", "0 seconds"), *ROOM_SENTENCES), 72)
    assert server.sent == [body]
    assert "Logged out" in body
    assert "unknown" in body
    assert "Ago" not in body


def test_finger_and_f_are_unknown() -> None:
    """Finger and f are not commands."""
    for line, reply in (("finger Bea", "No such command 'finger'\n"), ("f Bea", "No such command 'f'\n"), ("fBea", "No such command 'fBea'\n")):  # fmt: skip  # noqa: E501
        server, _ = run(line)
        assert server.sent == [reply]


def viewer(rank: Rank) -> FakePlayer:
    return staff("Ada", rank)


def here_at(server: FakeServer, name: str, address: str) -> FakePlayer:
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    user = Player(name, "hall", when, connected_seconds=7200)
    bea = FakePlayer(name=name, room="hall", player=user, address=address)
    server.people = [bea]
    return bea


def test_examine_shows_where_they_connected(tmp_path: Path) -> None:
    """Examine shows the hostname, the IP, and telnet or ssh."""
    rank_words = shown_rank(Rank.NEWBIE, True)
    fields = (("User Rank", rank_words), ("Logged in from", "10.0.0.5 (10.0.0.5) [via telnet]"), ("Logged in for", "10 minutes"), ("Total Login", "2 hours"), ("Idle for", "Not idle at all"), *ROOM_SENTENCES)  # fmt: skip  # noqa: E501
    for rank in (Rank.NEWBIE, Rank.RESIDENT, Rank.SUPER_USER, Rank.ADMIN):
        server = login_host(tmp_path)
        here_at(server, "Bea", "10.0.0.5")
        process_commands(server, viewer(rank), "examine Bea")  # type: ignore[arg-type]
        body = examine_frame("Bea", fields, 72)
        assert server.sent == [body]
        assert "Logged in from : 10.0.0.5 (10.0.0.5) [via telnet]" in body


def test_examine_shows_last_connected_from(tmp_path: Path) -> None:
    """A logged-out player shows the last hostname and IP."""
    left = datetime.now(timezone.utc) - timedelta(hours=2)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), logged_out_at=left, ip_address="10.0.0.5", hostname="bea.example"))
    stamp = clock_phrase(left)
    logged_out = (("User Rank", "Resident"), ("Last from", "bea.example (10.0.0.5)"), ("Logged out", stamp), ("Ago", "2 hours"), ("Total Login", "0 seconds"), *ROOM_SENTENCES)
    body = examine_frame("Bea", logged_out, 72)
    for rank in (Rank.RESIDENT, Rank.SUPER_USER):
        server = login_host(tmp_path)
        process_commands(server, viewer(rank), "examine Bea")  # type: ignore[arg-type]
        assert server.sent == [body]
    assert "Last from : bea.example (10.0.0.5)" in " ".join(body.split())
    assert "Last connected from:" not in body


def test_finger_says_unknown_when_no_address_is_saved(tmp_path: Path) -> None:
    """A file with no address reads unknown to staff, and adds no Ago line."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    process_commands(server, viewer(Rank.SUPER_USER), "examine Bea")  # type: ignore[arg-type]
    body = examine_frame("Bea", (("User Rank", "Resident"), ("Last from", "unknown"), ("Logged out", "unknown"), ("Total Login", "0 seconds"), *ROOM_SENTENCES), 72)  # fmt: skip  # noqa: E501
    assert server.sent == [body]
    assert "Last from : unknown" in " ".join(body.split())
    assert "Last connected from:" not in body


def test_entry_remembers_the_address_of_a_saved_player_only(tmp_path: Path) -> None:
    """A resident's file gets the address on entry. A newbie gets no file."""
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    bea = FakePlayer(player=None, address="10.0.0.5")
    process_input(server, bea, "Bea")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    assert bea.player is not None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.ip_address == "10.0.0.5"
    assert saved.hostname == "10.0.0.5"

    server = login_host(tmp_path)
    cyd = FakePlayer(player=None, address="10.0.0.6")
    process_input(server, cyd, "Cyd")  # type: ignore[arg-type]
    process_input(server, cyd, "yes")  # type: ignore[arg-type]
    assert cyd.player is not None
    assert load_saved(player_path(tmp_path, "Cyd")) is None


def test_a_new_resident_is_saved_with_the_address(tmp_path: Path) -> None:
    """Saving a connected newbie as a resident stores where they are connected from."""
    bea = newbie("Bea")
    bea.address = "10.0.0.5"
    bea.hostname = "bea.example"
    ada = staff("Ada", Rank.SUPER_USER)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "resident Bea")  # type: ignore[arg-type]
    process_input(server, bea, "hunter2")  # type: ignore[arg-type]
    process_input(server, bea, "hunter2")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.ip_address == "10.0.0.5"
    assert saved.hostname == "bea.example"


def test_su_channel_hears_the_staff_channel_and_can_speak(tmp_path: Path) -> None:
    """SU Channel hears and speaks on the staff channel. A resident does not."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SU_CHANNEL)
    bea = staff("Bea", Rank.RESIDENT)
    cyd = staff("Cyd", Rank.SUPER_USER)
    server.people = [ada, bea, cyd]
    process_commands(server, ada, "su hello")  # type: ignore[arg-type]
    assert any("hello" in line for line in ada.sent)
    assert any("hello" in line for line in cyd.sent)
    assert not any("hello" in line for line in bea.sent)
    server.sent.clear()
    process_commands(server, bea, "su hello")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'su'\n"]
    process_commands(server, ada, "lsn")  # type: ignore[arg-type]
    assert server.sent[-1] == " -=> There are no newbies connected currently.\n"


def test_su_stays_on_the_staff_channel(tmp_path: Path) -> None:
    """The sender sees You, the channel gets the name line, and a resident is refused."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    bob = staff("Bob", Rank.SUPER_USER)
    server.people = [ada, bob]
    for line, mine, theirs in (("su hello", "< SU > You say 'hello'\n", "< SU > Ada says 'hello'\n"), ("se waves", "< SU > You emote: waves\n", "< SU > Ada waves\n"), ("st lunch", "< SU > You think . o O ( lunch )\n", "< SU > Ada thinks . o O ( lunch )\n")):  # fmt: skip  # noqa: E501
        server.sent.clear()
        server.staff_lines.clear()
        process_commands(server, ada, line)  # type: ignore[arg-type]
        assert server.sent == [paint_staff(ada, mine)]
        assert server.staff_lines == [paint_staff(bob, theirs)]
    for word in ("su", "se", "st"):
        server.sent.clear()
        process_commands(server, ada, word)  # type: ignore[arg-type]
        assert server.sent == [f"Syntax: {word} <message>\n"]
    server.sent.clear()
    server.staff_lines.clear()
    for word in ("su", "se", "st", "lsn"):
        process_commands(server, staff("Bea", Rank.RESIDENT), f"{word} hi")  # type: ignore[arg-type]
    assert server.sent == [f"No such command '{word}'\n" for word in ("su", "se", "st", "lsn")]
    assert server.staff_lines == []


def test_admin_channel_say_emote_and_think(tmp_path: Path) -> None:
    """au, ae, and at stay on the admin channel and are hidden from super users."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.ADMIN)
    bob = staff("Bob", Rank.ADMIN)
    sue = staff("Sue", Rank.SUPER_USER)
    server.people = [ada, bob, sue]
    cases = (("au hello", "[ Admin ] You say 'hello'\n", "[ Admin ] Ada says 'hello'\n"), ("ae waves", "[ Admin ] You emote: waves\n", "[ Admin ] Ada waves\n"), ("at lunch", "[ Admin ] You think . o O ( lunch )\n", "[ Admin ] Ada thinks . o O ( lunch )\n"))  # fmt: skip  # noqa: E501
    for line, mine, theirs in cases:
        server.sent.clear()
        server.admin_lines.clear()
        server.staff_lines.clear()
        process_commands(server, ada, line)  # type: ignore[arg-type]
        assert server.sent == [paint_admin(ada, mine)]
        assert server.admin_lines == [paint_admin(bob, theirs)]
        assert server.staff_lines == []
    server.sent.clear()
    server.staff_lines.clear()
    process_commands(server, sue, "su hello")  # type: ignore[arg-type]
    assert any("Sue says 'hello'" in line for line in server.staff_lines)
    for word in ("au", "ae", "at"):
        server.sent.clear()
        process_commands(server, ada, word)  # type: ignore[arg-type]
        assert server.sent == [f"Syntax: {word} <message>\n"]
    ada.player.colour = False  # type: ignore[union-attr]
    bob.player.colour = False  # type: ignore[union-attr]
    server.sent.clear()
    server.admin_lines.clear()
    process_commands(server, ada, "au hello")  # type: ignore[arg-type]
    assert server.sent == ["[ Admin ] You say 'hello'\n"]
    assert server.admin_lines == ["[ Admin ] Ada says 'hello'\n"]
    server.sent.clear()
    process_commands(server, sue, "au hello")  # type: ignore[arg-type]
    process_commands(server, sue, "ae waves")  # type: ignore[arg-type]
    process_commands(server, sue, "at lunch")  # type: ignore[arg-type]
    assert server.sent == [
        "No such command 'au'\n",
        "No such command 'ae'\n",
        "No such command 'at'\n",
    ]


def test_coder_channel_lines(tmp_path: Path) -> None:
    """Coder and the Super Admin hear the coder channel. An admin does not."""
    from lupaxa.chat_server.colour import paint_coder

    ada = staff("Ada", Rank.CODER)
    bea = staff("Bea", Rank.CODER)
    cyd = staff("Cyd", Rank.ADMIN)
    dee = staff("Dee", Rank.SUPER_ADMIN)
    for person in (ada, bea, cyd, dee):
        assert person.player is not None
        person.player.colour = True
    server = login_host(tmp_path)
    server.people = [ada, bea, cyd, dee]
    cases = (
        ("cu hello", "{ Coder } You say 'hello'\n", "{ Coder } Ada says 'hello'\n"),
        ("ce waves", "{ Coder } You emote: waves\n", "{ Coder } Ada waves\n"),
        ("ct lunch", "{ Coder } You think . o O ( lunch )\n", "{ Coder } Ada thinks . o O ( lunch )\n"),
    )
    for line, mine, theirs in cases:
        ada.sent.clear()
        bea.sent.clear()
        cyd.sent.clear()
        dee.sent.clear()
        process_commands(server, ada, line)  # type: ignore[arg-type]
        assert ada.sent == [paint_coder(ada, mine)]
        assert bea.sent == [paint_coder(bea, theirs)]
        assert dee.sent == [paint_coder(dee, theirs)]
        assert cyd.sent == []


def test_off_duty_coder_keeps_the_coder_channel(tmp_path: Path) -> None:
    """Off duty still blocks su and au, and cu still reaches another coder."""
    ada = staff("Ada", Rank.CODER)
    bea = staff("Bea", Rank.CODER)
    assert ada.player is not None
    ada.player.off_duty = True
    ada.player.colour = False
    assert bea.player is not None
    bea.player.colour = False
    server = login_host(tmp_path)
    server.people = [ada, bea]
    process_commands(server, ada, "cu hello")  # type: ignore[arg-type]
    assert ada.sent == ["{ Coder } You say 'hello'\n"]
    assert bea.sent == ["{ Coder } Ada says 'hello'\n"]
    server.sent.clear()
    process_commands(server, ada, "su hidden")  # type: ignore[arg-type]
    assert server.sent == ["You are off duty.\n"]
    server.sent.clear()
    process_commands(server, ada, "au hidden")  # type: ignore[arg-type]
    assert server.sent == ["You are off duty.\n"]


def test_screenlock_still_waits_for_a_coder(tmp_path: Path) -> None:
    """A locked coder does not deliver cu until the password unlocks the screen."""
    ada = staff("Ada", Rank.CODER)
    bea = staff("Bea", Rank.CODER)
    assert ada.player is not None
    ada.player.screen_locked = True
    ada.auth = "screenlock"
    server = login_host(tmp_path)
    server.people = [ada, bea]
    process_input(server, ada, "cu hello")  # type: ignore[arg-type]
    assert not any("hello" in line for line in bea.sent)
    assert ada.player.screen_locked is True


def test_lsu_pads_the_columns_and_lists_only_staff(tmp_path: Path) -> None:
    """Only connected super users and the admin are listed, sorted, with dashes aligned."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    assert ada.player is not None
    ada.player.idle_seconds = 0
    boss = staff("admin", Rank.ADMIN)
    assert boss.player is not None
    boss.player.idle_seconds = 300
    bea = staff("Bea", Rank.RESIDENT)
    cyd = newbie("Cyd")
    server.people = [boss, bea, ada, cyd]
    assert ada.discovered_columns == 72
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    text = server.sent[0]
    assert "[ Admin ]" in text
    assert "< Super User >" in text
    assert "[00:05:00]" in text
    assert "Super Users:" not in text
    assert "0 seconds" not in text
    assert "Bea" not in text
    assert "Cyd" not in text
    for asker in (cyd, bea):
        server.sent.clear()
        process_commands(server, asker, "lsu")  # type: ignore[arg-type]
        assert server.sent == [text]


def test_lsu_text_is_the_seventy_two_column_frame(tmp_path: Path) -> None:
    """A coloured staff list stays within the 72-column terminal."""
    from lupaxa.chat_server.colour import cyan, green, light_black, plain_len, red

    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    assert ada.player is not None
    ada.player.idle_seconds = 0
    boss = staff("admin", Rank.ADMIN)
    assert boss.player is not None
    boss.player.idle_seconds = 3723
    server.people = [boss, ada]
    assert ada.discovered_columns == 72
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    super_line = "  Ada        < Super User >      Reception                  [ Active ]  "
    super_line = super_line.replace("< Super User >", cyan("< Super User >"), 1).replace("[ Active ]", green("[ Active ]"), 1)
    admin_line = "  admin        [ Admin ]         Reception                  [--:--:--]  "
    admin_line = admin_line.replace("[ Admin ]", red("[ Admin ]"), 1).replace("[--:--:--]", light_black("[--:--:--]"), 1)
    assert plain_len(super_line) == 72
    assert plain_len(admin_line) == 72
    lines = [
        "-" * 72,
        "  Name            Rank             Room         Status         Idle     ",
        "-" * 72,
        super_line,
        admin_line,
        "-" * 72,
    ]
    assert server.sent == ["\n".join(lines) + "\n"]
    for line in lines:
        assert plain_len(line) == 72


def test_lsu_with_colour_off_is_the_plain_frame(tmp_path: Path) -> None:
    """With colour off, rank cells have no codes and rules stay 72 columns wide."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    assert ada.player is not None
    ada.player.colour = False
    ada.player.idle_seconds = 0
    boss = staff("admin", Rank.ADMIN)
    assert boss.player is not None
    boss.player.idle_seconds = 3723
    server.people = [boss, ada]
    assert ada.discovered_columns == 72
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    super_line = "  Ada        < Super User >      Reception                  [ Active ]  "
    admin_line = "  admin        [ Admin ]         Reception                  [--:--:--]  "
    lines = [
        "-" * 72,
        "  Name            Rank             Room         Status         Idle     ",
        "-" * 72,
        super_line,
        admin_line,
        "-" * 72,
    ]
    assert server.sent == ["\n".join(lines) + "\n"]
    for rule in (lines[0], lines[2], lines[5]):
        assert len(rule) == 72


def test_staff_lists_the_same_frame_as_lsu() -> None:
    """Staff is lsu. staffroom is not. staff please is the lsu syntax."""
    ada = FakePlayer(player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.SUPER_USER))
    server = FakeServer(people=[ada])
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    listed = server.sent[0]
    server.sent.clear()
    process_commands(server, ada, "staff")  # type: ignore[arg-type]
    assert server.sent == [listed]
    server.sent.clear()
    process_commands(server, ada, "staffroom")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'staffroom'\n"]
    server.sent.clear()
    process_commands(server, ada, "staff please")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: staff\n"]


def test_lsu_with_nobody_to_list(tmp_path: Path) -> None:
    """No connected staff member is one sentence, not a table."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [staff("Bea", Rank.RESIDENT)]
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    assert server.sent == [" -=> There are no staff members connected currently.\n"]


def test_lsu_lists_a_connected_super_admin(tmp_path: Path) -> None:
    """The config super admin appears on the staff list with the Super Admin label."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    boss = staff("admin", Rank.SUPER_ADMIN)
    server.people = [boss, ada]
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    text = server.sent[0]
    assert "( Super Admin )" in text
    assert "admin" in text


def test_examine_shows_coder_and_super_admin_without_brackets(tmp_path: Path) -> None:
    """Examine uses the coloured words and no brackets."""
    from lupaxa.chat_server.colour import light_blue, magenta

    ada = staff("Ada", Rank.CODER)
    boss = staff("Dee", Rank.SUPER_ADMIN)
    assert ada.player is not None
    assert boss.player is not None
    ada.player.colour = True
    boss.player.colour = True
    server = login_host(tmp_path)
    server.people = [ada, boss]
    process_commands(server, ada, "examine Dee")  # type: ignore[arg-type]
    text = "".join(server.sent)
    assert light_blue("Super Admin") in text
    assert "( Super Admin )" not in text
    assert "[ Super Admin ]" not in text
    server.sent.clear()
    process_commands(server, boss, "examine Ada")  # type: ignore[arg-type]
    text = "".join(server.sent)
    assert magenta("Coder") in text
    assert "{ Coder }" not in text
    server.sent.clear()
    ada.player.colour = False
    process_commands(server, ada, "examine me")  # type: ignore[arg-type]
    plain = "".join(server.sent)
    assert "Coder" in plain
    assert magenta("Coder") not in plain


def test_who_shows_coder_braces_and_super_admin_parentheses(tmp_path: Path) -> None:
    """Who uses braces for Coder and parentheses for Super Admin."""
    from lupaxa.chat_server.colour import light_blue, magenta

    ada = staff("Ada", Rank.CODER)
    boss = staff("Dee", Rank.SUPER_ADMIN)
    viewer = staff("Bea", Rank.RESIDENT)
    assert viewer.player is not None
    viewer.player.colour = True
    server = login_host(tmp_path)
    server.people = [ada, boss, viewer]
    process_commands(server, viewer, "who")  # type: ignore[arg-type]
    text = "".join(server.sent)
    assert magenta("{ Coder }") in text
    assert light_blue("( Super Admin )") in text
    assert "[ Super Admin ]" not in text


def test_lsu_shows_coder_and_an_off_duty_coder(tmp_path: Path) -> None:
    """Lsu uses magenta braces, including Off Duty."""
    from lupaxa.chat_server.colour import magenta

    ada = staff("Ada", Rank.CODER)
    bea = staff("Bea", Rank.CODER)
    viewer = staff("Cyd", Rank.SU_CHANNEL)
    resident = staff("Dee", Rank.RESIDENT)
    assert ada.player is not None
    assert bea.player is not None
    assert viewer.player is not None
    ada.player.off_duty = True
    viewer.player.colour = True
    server = login_host(tmp_path)
    server.people = [ada, bea, viewer, resident]
    process_commands(server, viewer, "lsu")  # type: ignore[arg-type]
    text = "".join(server.sent)
    assert magenta("{ Coder }") in text
    assert magenta("{ Off Duty }") in text
    server.sent.clear()
    process_commands(server, resident, "lsu")  # type: ignore[arg-type]
    hidden = "".join(server.sent)
    assert "Ada" not in hidden
    assert "Bea" in hidden


def test_off_duty_hides_the_role_until_on_duty(tmp_path: Path) -> None:
    """Off duty leaves the channels and staff commands, and hides the row from residents."""
    from lupaxa.chat_server.colour import cyan, red

    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    bob = staff("Bob", Rank.SUPER_USER)
    boss = staff("Ann", Rank.ADMIN)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [ada, bob, boss, bea]
    process_commands(server, ada, "off_duty")  # type: ignore[arg-type]
    assert server.sent[-1] == "Syntax: off_duty <reason>\n"
    server.sent.clear()
    server.staff_lines.clear()
    process_commands(server, ada, "off_duty lunch")  # type: ignore[arg-type]
    assert ada.player is not None
    assert ada.player.off_duty is True
    note = "< SU > Ada goes off duty because 'lunch'.\n"
    assert server.staff_lines == [paint_staff(ada, note), paint_staff(bob, note), paint_staff(boss, note)]
    server.sent.clear()
    process_commands(server, ada, "su hello")  # type: ignore[arg-type]
    assert server.sent == ["You are off duty.\n"]
    server.sent.clear()
    server.staff_lines.clear()
    process_commands(server, bob, "su hello")  # type: ignore[arg-type]
    assert server.staff_lines == [paint_staff(boss, "< SU > Bob says 'hello'\n")]
    assert server.sent == [paint_staff(bob, "< SU > You say 'hello'\n")]
    process_commands(server, bea, "lsu")  # type: ignore[arg-type]
    assert "Ada" not in server.sent[-1]
    assert "Bob" in server.sent[-1]
    server.sent.clear()
    process_commands(server, bob, "lsu")  # type: ignore[arg-type]
    assert cyan("< Off Duty >") in server.sent[0]
    assert red("[ Admin ]") in server.sent[0]
    server.sent.clear()
    process_commands(server, ada, "say hello")  # type: ignore[arg-type]
    assert server.sent[0] == "You say 'hello'\n"
    save(tmp_path, "Ada", Rank.SUPER_USER)
    server.sent.clear()
    server.staff_lines.clear()
    process_input(server, ada, "on_duty")  # type: ignore[arg-type]
    assert ada.auth == "on_duty_password"
    assert ada.player.off_duty is True
    assert server.sent == ["Password: "]
    server.sent.clear()
    process_input(server, ada, "pw")  # type: ignore[arg-type]
    assert ada.player.off_duty is False
    assert ada.auth == "name"
    back = "< SU > Ada has returned to duty.\n"
    assert paint_staff(ada, back) in server.staff_lines
    assert server.sent[-1] == "What? > "
    process_commands(server, bea, "lsu")  # type: ignore[arg-type]
    assert "Ada" in server.sent[-1]


def test_on_duty_wrong_password_stays_off_duty(tmp_path: Path) -> None:
    """A wrong password leaves them off duty and tells the staff channel."""
    save(tmp_path, "Ada", Rank.SUPER_USER)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    bob = staff("Bob", Rank.SUPER_USER)
    server.people = [ada, bob]
    process_input(server, ada, "off_duty lunch")  # type: ignore[arg-type]
    process_input(server, ada, "on_duty")  # type: ignore[arg-type]
    server.sent.clear()
    server.staff_lines.clear()
    process_input(server, ada, "nope")  # type: ignore[arg-type]
    assert ada.player is not None
    assert ada.player.off_duty is True
    assert ada.auth == "name"
    note = "< SU > Ada got their password wrong and failed to return to duty.\n"
    assert server.staff_lines == [paint_staff(bob, note)]
    assert server.sent == ["Wrong password.\nWhat? > "]


def test_screenlock_takes_su_channel_off_duty(tmp_path: Path) -> None:
    """Locking sets SU Channel off duty, and the right password clears it."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("Ada", Rank.SU_CHANNEL, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    server.roster_dir = players
    server.config = replace(server.config, players=players)
    ada = staff("Ada", Rank.SU_CHANNEL)
    bea = staff("Bea", Rank.SU_CHANNEL)
    server.people = [ada, bea]
    process_commands(server, ada, "screenlock lunch")  # type: ignore[arg-type]
    assert ada.player is not None
    assert ada.player.off_duty is True
    assert any("locked their screen because 'lunch'" in line for line in bea.sent)
    ada.auth = "name"
    server.sent.clear()
    ada.show_lock = True
    process_commands(server, ada, "su hidden")  # type: ignore[arg-type]
    assert server.sent == ["You are off duty.\n"]
    ada.show_lock = False
    ada.auth = "screenlock"
    process_input(server, ada, "pw")  # type: ignore[arg-type]
    assert ada.player.off_duty is False
    assert any("unlocked their screen" in line for line in bea.sent)
    process_commands(server, ada, "su later")  # type: ignore[arg-type]
    assert any("later" in line for line in bea.sent)


def test_screenlock_hides_the_screen_until_the_password(tmp_path: Path) -> None:
    """A locked screen shows only the unlock prompt, and staff step off duty."""
    save(tmp_path, "Ada", Rank.SUPER_USER)
    save(tmp_path, "Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    bob = staff("Bob", Rank.SUPER_USER)
    bea = staff("Bea", Rank.RESIDENT)
    neo = staff("Neo", Rank.NEWBIE)
    server.people = [ada, bob, bea, neo]
    process_input(server, neo, "screenlock")  # type: ignore[arg-type]
    assert server.sent[-2:] == ["No such command 'screenlock'\n", "What? > "]
    server.sent.clear()
    process_input(server, bea, "screenlock")  # type: ignore[arg-type]
    assert bea.player is not None
    assert bea.player.screen_locked is True
    assert bea.player.off_duty is False
    assert bea.auth == "screenlock"
    assert bea.sent == [cls(), "Enter password to unlock: "]
    assert server.staff_lines == []
    server.sent.clear()
    bea.sent.clear()
    assert bea.player is not None
    bea.player.auto_reply = "later"
    process_input(server, ada, "tell Bea hello")  # type: ignore[arg-type]
    assert bea.sent == []
    assert "[ Auto Reply from Bea ] later\n" not in ada.sent
    assert bea.player.auto_reply == "later"
    assert "[ Screen Locked ] Bea's screen is currently locked\n" in ada.sent
    server.sent.clear()
    ada.sent.clear()
    process_input(server, bea, "say hello")  # type: ignore[arg-type]
    assert bea.player.screen_locked is True
    assert bea.sent == ["Wrong password.\nEnter password to unlock: "]
    assert server.room_lines == []
    server.sent.clear()
    bea.sent.clear()
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    assert bea.player.screen_locked is False
    assert bea.auth == "name"
    assert bea.sent == ["Screen is now unlocked.\n", "What? > "]
    server.sent.clear()
    server.staff_lines.clear()
    ada.sent.clear()
    process_input(server, ada, "screenlock lunch")  # type: ignore[arg-type]
    assert ada.player is not None
    assert ada.player.screen_locked is True
    assert ada.player.off_duty is True
    note = "< SU > Ada has locked their screen because 'lunch'.\n"
    assert server.staff_lines == [paint_staff(bob, note)]
    assert ada.sent == [cls(), "Enter password to unlock: "]
    server.sent.clear()
    ada.sent.clear()
    server.staff_lines.clear()
    process_input(server, bob, "tell Ada hi")  # type: ignore[arg-type]
    assert ada.sent == []
    assert "[ Screen Locked ] Ada's screen is currently locked lunch\n" in bob.sent
    server.sent.clear()
    ada.sent.clear()
    server.staff_lines.clear()
    process_input(server, ada, "pw")  # type: ignore[arg-type]
    assert ada.player.screen_locked is False
    assert ada.player.off_duty is False
    assert ada.auth == "name"
    unlocked = "< SU > Ada has unlocked their screen.\n"
    assert paint_staff(ada, unlocked) in server.staff_lines
    assert paint_staff(bob, unlocked) in server.staff_lines
    assert server.sent[-1] == "What? > "
    assert "Screen is now unlocked.\n" in ada.sent


def test_off_list_hides_an_admin_from_the_public_staff_list(tmp_path: Path) -> None:
    """Off list changes lsu only. The admin still hears the channel and can use commands."""
    from lupaxa.chat_server.colour import red

    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    boss = staff("Ann", Rank.ADMIN)
    bea = staff("Bea", Rank.RESIDENT)
    server.people = [ada, boss, bea]
    process_commands(server, ada, "off_list training")  # type: ignore[arg-type]
    assert server.sent[-1] == "No such command 'off_list'\n"
    server.sent.clear()
    server.staff_lines.clear()
    process_commands(server, boss, "off_list training")  # type: ignore[arg-type]
    assert boss.player is not None
    assert boss.player.off_list is True
    note = "< SU > Ann has gone off list because 'training'.\n"
    assert paint_staff(boss, note) in server.staff_lines
    process_commands(server, bea, "lsu")  # type: ignore[arg-type]
    assert "Ann" not in server.sent[-1]
    assert "Ada" in server.sent[-1]
    server.sent.clear()
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    assert red("[ Off List ]") in server.sent[0]
    server.sent.clear()
    server.staff_lines.clear()
    process_commands(server, boss, "au hello")  # type: ignore[arg-type]
    assert server.sent == [paint_admin(boss, "[ Admin ] You say 'hello'\n")]
    server.sent.clear()
    server.staff_lines.clear()
    process_commands(server, boss, "on_list")  # type: ignore[arg-type]
    assert boss.player.off_list is False
    assert paint_staff(boss, "< SU > Ann has gone back on list.\n") in server.staff_lines
    process_commands(server, bea, "lsu")  # type: ignore[arg-type]
    assert "Ann" in server.sent[-1]


def test_lsn_lists_connected_newbies(tmp_path: Path) -> None:
    """A super user sees each connected newbie with the address, names padded."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada, staff("Dee", Rank.RESIDENT)]
    server.newbies = [("Bea", "10.0.0.5", "reception", 0), ("Cora", "192.168.1.20", "hall", 300)]
    process_commands(server, ada, "lsn")  # type: ignore[arg-type]
    text = server.sent[0]
    assert "Newbies:" not in text
    assert "10.0.0.5" in text
    assert "192.168.1.20" in text
    assert "Reception" in text
    assert "Hall" in text
    assert "[ Active ]" in text
    assert "[00:05:00]" in text


def test_lsn_shows_hostname_and_room() -> None:
    """Each newbie row shows hostname, room display name, and idle clock."""
    ada = FakePlayer(player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.SUPER_USER))
    server = FakeServer(people=[ada])
    server.newbies = [("Bea", "bea.example", "hall", 300)]
    process_commands(server, ada, "lsn")  # type: ignore[arg-type]
    text = server.sent[0]
    assert "Address" in text
    assert "Room" in text
    assert "bea.example" in text
    assert "Hall" in text
    assert "[00:05:00]" in text


def test_lsn_with_no_newbies(tmp_path: Path) -> None:
    """No connected newbie is one sentence, not a table."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    process_commands(server, ada, "lsn")  # type: ignore[arg-type]
    assert server.sent == [" -=> There are no newbies connected currently.\n"]


def test_lsn_refuses_extra_text(tmp_path: Path) -> None:
    """Extra text after lsn is a syntax error."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.newbies = [("Bea", "10.0.0.5", "reception", 0)]
    process_commands(server, ada, "lsn now")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: lsn\n"]


def test_newbie_cannot_lsn(tmp_path: Path) -> None:
    """A newbie and a resident get no such command, and help hides it from a newbie."""
    server = login_host(tmp_path)
    server.newbies = [("Bea", "10.0.0.5", "reception", 0)]
    process_commands(server, newbie("Bea"), "lsn")  # type: ignore[arg-type]
    process_commands(server, staff("Dee", Rank.RESIDENT), "lsn")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'lsn'\n", "No such command 'lsn'\n"]
    server = FakeServer(help=HelpBook(entries=(HelpEntry("lsn", (), "List connected newbies."),)))
    process_commands(server, newbie("Bea"), "help lsn")  # type: ignore[arg-type]
    assert server.sent == ["help for lsn not found\n"]


def test_connection_rows_use_the_pending_name_and_sort_by_ip() -> None:
    """No name is <pending>. A typed name is kept. Rows sort by name, then IP."""
    early = FakePlayer(player=None, name=None, room=None, address="10.0.0.1", hostname="10.0.0.1")
    late = FakePlayer(player=None, name=None, room=None, address="9.9.9.9", hostname="9.9.9.9")
    held = FakePlayer(player=None, name=None, room=None, pending_name="Bea", address="203.0.113.4", hostname="203.0.113.4")
    ada = FakePlayer(address="198.51.100.2", hostname="ada.example", ssh=True)
    logging_in = FakePlayer(pending_name="NotAda", auth="current", address="198.51.100.8")
    rows = connections([late, ada, held, early, logging_in])  # type: ignore[arg-type]
    assert [row[0] for row in rows] == ["<pending>", "<pending>", "Ada", "Ada", "Bea"]
    assert rows[0][2] == "10.0.0.1"
    assert rows[1][2] == "9.9.9.9"
    assert rows[2] == ("Ada", "ada.example", "198.51.100.2", "ssh")
    assert rows[3][0] == "Ada"
    assert rows[3][3] == "telnet"
    assert "NotAda" not in [row[0] for row in rows]
    assert rows[4] == ("Bea", "203.0.113.4", "203.0.113.4", "telnet")


def test_connections_frames_every_socket_for_a_super_user() -> None:
    """The frame names the columns and includes the person who asked."""
    when = datetime.now(timezone.utc)
    viewer = FakePlayer(player=Player("Cyd", "reception", when, rank=Rank.SUPER_USER), address="192.0.2.1", hostname="staff.example")
    unnamed = FakePlayer(player=None, name=None, room=None, address="192.0.2.10", hostname="192.0.2.10")
    server = FakeServer(people=[unnamed, viewer])
    process_commands(server, viewer, "connections")  # type: ignore[arg-type]
    frame = server.sent[0]
    assert frame.splitlines()[1].split()[:4] == ["Name", "Hostname", "IP", "Address"]
    assert "Type" in frame.splitlines()[1]
    assert "<pending>" in frame
    assert "Cyd" in frame
    assert "staff.example" in frame
    assert "192.0.2.1" in frame
    assert "telnet" in frame
    assert frame.index("<pending>") < frame.index("Cyd")
    assert "Country" not in frame.splitlines()[1]


def test_connections_adds_a_country_column_when_lookup_is_on() -> None:
    """Country sits between the IP and the connection type."""
    when = datetime.now(timezone.utc)
    viewer = FakePlayer(player=Player("Cyd", "reception", when, rank=Rank.SUPER_USER), address="8.8.8.8", hostname="dns.example", country_name="United States", country_code="US")
    server = FakeServer(people=[viewer], country_lookup=True)
    process_commands(server, viewer, "connections")  # type: ignore[arg-type]
    frame = server.sent[0]
    assert "Country" in frame.splitlines()[1]
    assert "United States" in frame
    assert frame.index("8.8.8.8") < frame.index("United States") < frame.index("telnet")


def test_conns_is_the_same_list_and_needs_a_space() -> None:
    """Conns lists sockets. connsplease is not the command."""
    viewer = FakePlayer(player=Player("Cyd", "reception", datetime.now(timezone.utc), rank=Rank.SUPER_USER))
    listed, _ = run("conns", viewer)
    assert "Cyd" in listed.sent[0]
    missing, _ = run("connsplease", viewer)
    assert missing.sent == ["No such command 'connsplease'\n"]
    extra, _ = run("connections please", viewer)
    assert extra.sent == ["Syntax: connections\n"]
    alias_extra, _ = run("conns please", viewer)
    assert alias_extra.sent == ["Syntax: conns\n"]


def test_a_resident_cannot_use_connections() -> None:
    """Residents are told the words are not commands."""
    resident = FakePlayer(player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    refused, _ = run("connections", resident)
    assert refused.sent == ["No such command 'connections'\n"]
    alias, _ = run("conns", resident)
    assert alias.sent == ["No such command 'conns'\n"]


def test_a_newbie_has_no_password_to_change(tmp_path: Path) -> None:
    """A newbie is told there is no saved password to change."""
    server = login_host(tmp_path)
    process_commands(server, newbie("Ada"), "password")  # type: ignore[arg-type]
    assert server.sent == ["You have no saved password.\n"]


def test_password_syntax_rejects_extra_text(tmp_path: Path) -> None:
    """Password does not take a remainder."""
    server = login_host(tmp_path)
    process_commands(server, staff("Bea", Rank.RESIDENT), "password extra")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: password\n"]


def test_password_via_process_input_leaves_only_current_prompt(tmp_path: Path) -> None:
    """The live path must not append the play prompt to the current-password line."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    process_input(server, bea, "password")  # type: ignore[arg-type]
    assert server.sent == ["Current password: "]
    assert bea.auth == "current"


def test_saved_player_can_change_password(tmp_path: Path) -> None:
    """A saved resident replaces their password after the three prompts."""
    left = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), logged_out_at=left, ip_address="10.0.0.5", prompt="Go: ", connected_seconds=90))  # fmt: skip  # noqa: E501
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    process_commands(server, bea, "password")  # type: ignore[arg-type]
    assert server.sent == ["Current password: "]
    assert bea.auth == "current"
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    process_input(server, bea, "next")  # type: ignore[arg-type]
    process_input(server, bea, "next")  # type: ignore[arg-type]
    assert server.sent[-1] == "Password changed.\nWhat? > "
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert password_ok("next", saved.password_hash)
    assert not password_ok("pw", saved.password_hash)
    assert saved.logged_out_at == left
    assert saved.ip_address == "10.0.0.5"
    assert saved.hostname == "10.0.0.5"
    assert saved.prompt == "Go: "
    assert saved.connected_seconds == 90


def test_password_change_reseals_with_the_new_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A new password still opens the same MFA secret."""
    from lupaxa.chat_server.mfa_seal import open_mfa_secret, seal_mfa_secret

    totp_seed = TOTP_TEST_SEED
    sealed = seal_mfa_secret("pw", totp_seed)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=sealed, mfa_on=True))
    server = login_host(tmp_path)
    server.mfa_enabled = False
    bea = staff("Bea", Rank.RESIDENT)
    process_commands(server, bea, "password")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    process_input(server, bea, "next")  # type: ignore[arg-type]
    process_input(server, bea, "next")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is not None
    assert open_mfa_secret("next", saved.mfa_secret) == totp_seed
    assert open_mfa_secret("pw", saved.mfa_secret) is None


def test_password_change_reseals_after_mismatch_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed confirm still reseals with the new password on retry."""
    from lupaxa.chat_server.mfa_seal import open_mfa_secret, seal_mfa_secret

    totp_seed = TOTP_TEST_SEED
    sealed = seal_mfa_secret("pw", totp_seed)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), mfa_secret=sealed, mfa_on=True))
    server = login_host(tmp_path)
    server.mfa_enabled = False
    bea = staff("Bea", Rank.RESIDENT)
    process_commands(server, bea, "password")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    process_input(server, bea, "next")  # type: ignore[arg-type]
    process_input(server, bea, "nope")  # type: ignore[arg-type]
    process_input(server, bea, "next")  # type: ignore[arg-type]
    process_input(server, bea, "next")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is not None
    assert open_mfa_secret("next", saved.mfa_secret) == totp_seed
    assert open_mfa_secret("pw", saved.mfa_secret) is None


def test_password_wrong_current_keeps_the_old_hash(tmp_path: Path) -> None:
    """A wrong current password does not drop the player or change the file."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    process_commands(server, bea, "password")  # type: ignore[arg-type]
    process_input(server, bea, "no")  # type: ignore[arg-type]
    assert server.sent[-1] == "Wrong password.\nWhat? > "
    assert server.dropped is False
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert password_ok("pw", saved.password_hash)


def test_password_mismatch_returns_to_set_prompt(tmp_path: Path) -> None:
    """Mismatched new passwords stay on the first new-password prompt."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    process_commands(server, bea, "password")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    process_input(server, bea, "one")  # type: ignore[arg-type]
    process_input(server, bea, "two")  # type: ignore[arg-type]
    assert server.sent[-1] == "Those passwords do not match.\nSet a password: "
    assert bea.auth == "renew"
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert password_ok("pw", saved.password_hash)


def test_password_renew_rejects_empty_and_long(tmp_path: Path) -> None:
    """Empty and overlong new passwords keep the old hash."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    process_commands(server, bea, "password")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    process_input(server, bea, "")  # type: ignore[arg-type]
    assert server.sent[-1] == "Password cannot be empty.\nSet a password: "
    process_input(server, bea, "x" * 73)  # type: ignore[arg-type]
    assert server.sent[-1] == "Password is too long.\nSet a password: "
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert password_ok("pw", saved.password_hash)


def test_password_without_confirm_keeps_the_old_hash(tmp_path: Path) -> None:
    """Stopping after the first new password leaves the file unchanged."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    process_commands(server, bea, "password")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    process_input(server, bea, "next")  # type: ignore[arg-type]
    assert bea.auth == "renew_confirm"
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert password_ok("pw", saved.password_hash)


def test_admin_commands_are_coloured_and_say_is_plain() -> None:
    """Super-user names are cyan, admin names are red, coder names are magenta, and say stays plain."""
    from lupaxa.chat_server.colour import cyan, magenta, red

    boss = staff("admin", Rank.SUPER_ADMIN)
    server, _ = run("commands", boss)
    text = server.sent[0]
    assert cyan("lsn") in text
    assert cyan("conns") in text
    assert red("allow") in text
    assert magenta("shutdown") in text
    assert cyan("say") not in text
    assert magenta("network") in text
    assert "say" in text.split()


def test_commands_without_colour_have_no_codes() -> None:
    """Colour off leaves the command list as plain words."""
    boss = staff("admin", Rank.CODER)
    assert boss.player is not None
    boss.player.colour = False
    server, _ = run("commands", boss)
    assert "\x1b" not in server.sent[0]
    assert "shutdown" in server.sent[0]


def test_examine_ranks_use_the_viewers_colour() -> None:
    """Newbie is green, super user is cyan, admin is red, and resident is plain."""
    from lupaxa.chat_server.colour import cyan, green, red

    when = datetime.now(timezone.utc)
    newbie = FakePlayer(player=Player("Bea", "hall", when))
    server = FakeServer(people=[newbie])
    process_commands(server, newbie, "examine me")  # type: ignore[arg-type]
    assert green("Newbie") in server.sent[0]
    assert "User Rank" in server.sent[0]
    assert server.sent[0].split("User Rank")[1].startswith("      : ")

    resident = staff("Bea", Rank.RESIDENT)
    server = FakeServer(people=[resident])
    process_commands(server, resident, "examine me")  # type: ignore[arg-type]
    assert "Resident" in server.sent[0]
    assert "\x1b" not in server.sent[0]

    super_user = staff("Ada", Rank.SUPER_USER)
    server = FakeServer(people=[super_user])
    process_commands(server, super_user, "examine me")  # type: ignore[arg-type]
    assert cyan("Super User") in server.sent[0]

    boss = staff("admin", Rank.ADMIN)
    assert boss.player is not None
    boss.player.colour = False
    server = FakeServer(people=[super_user, boss])
    process_commands(server, boss, "examine Ada")  # type: ignore[arg-type]
    assert "Super User" in server.sent[0]
    assert cyan("Super User") not in server.sent[0]

    boss = staff("admin", Rank.ADMIN)
    server = FakeServer(people=[boss])
    process_commands(server, boss, "examine me")  # type: ignore[arg-type]
    assert red("Admin") in server.sent[0]
    assert boss.player is not None
    boss.player.colour = False
    server.sent.clear()
    process_commands(server, boss, "examine me")  # type: ignore[arg-type]
    assert "Admin" in server.sent[0]
    assert red("Admin") not in server.sent[0]


def test_logged_out_examine_colours_the_rank(tmp_path: Path) -> None:
    """A logged-out super user is cyan for a viewer whose colour is on."""
    from lupaxa.chat_server.colour import cyan

    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    process_commands(server, ada, "examine Bea")  # type: ignore[arg-type]
    assert cyan("Super User") in server.sent[0]


def test_admin_words_include_the_new_commands() -> None:
    """The admin sees every command word."""
    assert command_words(Rank.ADMIN) == ("! \" ' , . : ; < > ? ` ~ ae alarm allow appoint at au chains cls color colour commands connections conns demote disconnect dismiss emote examine go grab help home join l look lsn lsu main map nuke off_duty off_list on_duty on_list password promote pstats quit recap remote resident retire roles rthink save say screenlock se set shout st staff su tell term_width testcard think time version w warn who x")  # fmt: skip  # noqa: E501
    assert "main" in command_words(Rank.NEWBIE).split()
    assert "idle" not in command_words(Rank.NEWBIE).split()
    assert "password" in command_words(Rank.NEWBIE).split()
    assert "finger" not in command_words(Rank.NEWBIE).split()
    assert "f" not in command_words(Rank.NEWBIE).split()
    assert "lsn" in command_words(Rank.SUPER_USER).split()
    assert "lsn" not in command_words(Rank.RESIDENT).split()
    assert "lsn" not in command_words(Rank.NEWBIE).split()
    assert "connections" in command_words(Rank.SUPER_USER).split()
    assert "conns" in command_words(Rank.SUPER_USER).split()
    assert "connections" not in command_words(Rank.RESIDENT).split()
    assert "conns" not in command_words(Rank.RESIDENT).split()
    assert "connections" not in command_words(Rank.NEWBIE).split()
    assert "lsu" in command_words(Rank.NEWBIE).split()
    assert "lsu" in command_words(Rank.RESIDENT).split()
    for word in ("su", "se", "st", "lsn"):
        assert word not in command_words(Rank.RESIDENT).split()
        assert word not in command_words(Rank.NEWBIE).split()


def seconds_saved(tmp_path: Path, seconds: int) -> None:
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), connected_seconds=seconds))


def saved_seconds(tmp_path: Path) -> int:
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    return saved.connected_seconds


def resident_bea(seconds: int, flushed: int) -> Player:
    return Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT, connected_seconds=seconds, flushed_seconds=flushed)


def test_note_connected_time_writes_after_300_more_seconds(tmp_path: Path) -> None:
    """Three hundred seconds ahead of the last write saves the total. Fewer does not."""
    seconds_saved(tmp_path, 10)
    user = resident_bea(10, 10)
    note_connected_time(tmp_path, user, 299)
    assert user.connected_seconds == 309
    assert user.flushed_seconds == 10
    assert saved_seconds(tmp_path) == 10
    note_connected_time(tmp_path, user, 1)
    assert user.connected_seconds == 310
    assert user.flushed_seconds == 310
    assert saved_seconds(tmp_path) == 310


def test_note_connected_time_of_300_writes_310(tmp_path: Path) -> None:
    """A saved resident at 10 seconds who adds 300 has 310 in the file."""
    seconds_saved(tmp_path, 10)
    user = resident_bea(10, 10)
    note_connected_time(tmp_path, user, 300)
    assert saved_seconds(tmp_path) == 310
    assert user.flushed_seconds == 310


def test_note_connected_time_ignores_nothing_to_add(tmp_path: Path) -> None:
    """Zero seconds changes nothing."""
    user = resident_bea(10, 10)
    note_connected_time(tmp_path, user, 0)
    assert user.connected_seconds == 10


def test_note_connected_time_keeps_a_newbie_off_disk(tmp_path: Path) -> None:
    """A newbie counts the seconds, and no file is written."""
    user = Player("Bea", "hall", datetime.now(timezone.utc))
    note_connected_time(tmp_path, user, 300)
    assert user.connected_seconds == 300
    assert user.flushed_seconds == 0
    assert list(tmp_path.iterdir()) == []


def test_a_failed_write_leaves_flushed_seconds_alone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A bad player file is printed, and the next tick tries again."""
    (tmp_path / "bea.yml").write_text("- not a mapping\n", encoding="utf-8")
    user = resident_bea(0, 0)
    note_connected_time(tmp_path, user, 300)
    assert user.connected_seconds == 300
    assert user.flushed_seconds == 0
    assert "bea.yml" in capsys.readouterr().out


def test_record_saved_logout_writes_the_time_and_the_total_together(tmp_path: Path) -> None:
    """After 40 seconds the file has connected_seconds 40 and a logout time."""
    seconds_saved(tmp_path, 0)
    user = resident_bea(40, 0)
    record_saved_logout(tmp_path, user)
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.connected_seconds == 40
    assert saved.logged_out_at is not None


def test_record_saved_logout_skips_a_newbie(tmp_path: Path) -> None:
    """A newbie has no file, so none is written."""
    user = Player("Bea", "hall", datetime.now(timezone.utc), connected_seconds=40)
    record_saved_logout(tmp_path, user)
    assert list(tmp_path.iterdir()) == []


def advance(server: FakeServer, monkeypatch: pytest.MonkeyPatch, now: float) -> None:
    server.timer_mark = 0.0  # type: ignore[attr-defined]
    server.timer_remainder = 0.0  # type: ignore[attr-defined]
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: now)
    process_timer(server)  # type: ignore[arg-type]


def test_allow_changes_the_live_flag_and_not_the_file(tmp_path: Path) -> None:
    """Allow closes newbies in memory and leaves config.yml bytes alone."""
    config_path = tmp_path / "config.yml"
    config_path.write_text("allow_newbies: true\n", encoding="utf-8")
    server = FakeServer()
    ada = FakePlayer()
    ada.player.rank = Rank.ADMIN
    server.people = [ada]
    assert ada.player is not None
    ada.player.colour = False
    allow = Command("allow", command_allow, False, "newbies|residents on|off")
    command_allow(server, ada, allow, "newbies off")
    assert server.allow_newbies is False
    assert config_path.read_text(encoding="utf-8") == "allow_newbies: true\n"
    assert server.staff_lines == ["< SU > Ada closes the talker to newbies.\n"]

    server.staff_lines.clear()
    server.allow_residents = False
    command_allow(server, ada, allow, "residents on")
    assert server.allow_residents is True
    assert server.staff_lines == ["< SU > Ada opens the talker to residents.\n"]

    server.allow_newbies = True
    server.allow_residents = True
    ada.sent.clear()
    command_allow(server, ada, allow, "maybe")
    assert ada.sent == ["Syntax: allow newbies|residents on|off\n"]
    assert server.allow_newbies is True
    assert server.allow_residents is True

    newbie_host, connected = run("allow newbies off", newbie("Bea"))
    assert connected.sent == ["No such command 'allow'\n"]
    assert newbie_host.allow_newbies is True


def test_broadcast_is_centered_with_blank_lines() -> None:
    """An admin broadcast is red when colour is on, with a blank line after it."""
    plain = "-=> Broadcast Messages from Ada: hello <=-"
    ada = FakePlayer(player=Player("Ada", "hall", datetime.now(timezone.utc), rank=Rank.CODER), frame_columns=len(plain))
    server, _ = run("broadcast hello", ada)
    assert server.sent[0] == f"\n\n\n{red(plain)}\n\n\n\n"
    resident, _ = run("broadcast hello", staff("Bea", Rank.RESIDENT))
    assert resident.sent == ["No such command 'broadcast'\n"]
    empty, _ = run("broadcast", ada)
    assert empty.sent == ["Syntax: broadcast <message>\n"]
    ada_off = FakePlayer(player=Player("Ada", "hall", datetime.now(timezone.utc), rank=Rank.CODER, colour=False), frame_columns=len(plain))
    host_off, _ = run("broadcast hello", ada_off)
    assert host_off.sent[0] == f"\n\n\n{plain}\n\n\n\n"
    ada_wide = FakePlayer(player=Player("Ada", "hall", datetime.now(timezone.utc), rank=Rank.CODER), frame_columns=len(plain) + 4)
    host_wide, _ = run("broadcast hello", ada_wide)
    assert host_wide.sent[0] == f"\n\n\n  {red(plain)}\n\n\n\n"


def test_shutdown_speaks_the_opening_line_and_the_marks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """90 seconds opens in seconds, then 1 minute, then the pair, then stop."""
    server = login_host(tmp_path)
    server.clients = {}  # type: ignore[attr-defined]
    admin = staff("Ada", Rank.CODER)
    process_commands(server, admin, "shutdown 90 restart")  # type: ignore[arg-type]
    assert server.all_lines == ["Shutting down in 90 seconds for 'restart'\n"]
    assert server.stopped is False
    now = {"t": 0.0}
    server.timer_mark = 0.0
    server.timer_remainder = 0.0
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: now["t"])
    now["t"] = 30.0
    server.all_lines.clear()
    process_timer(server)  # type: ignore[arg-type]
    assert server.all_lines == ["Shutting down in 1 minute for 'restart'\n"]
    now["t"] = 89.0
    server.all_lines.clear()
    process_timer(server)  # type: ignore[arg-type]
    assert server.all_lines == [
        "Shutting down in 1 second for 'restart'\n",
        "Shutting down now!\n",
    ]
    assert server.stopped is False
    now["t"] = 90.0
    server.all_lines.clear()
    process_timer(server)  # type: ignore[arg-type]
    assert server.all_lines == []
    assert server.stopped is True


def test_shutdown_5_says_each_second_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The opening 5 is not repeated, and 4, 3, and 2 are said once."""
    server = login_host(tmp_path)
    server.clients = {}  # type: ignore[attr-defined]
    process_commands(server, staff("Ada", Rank.CODER), "shutdown 5 restart")  # type: ignore[arg-type]
    assert server.all_lines == ["Shutting down in 5 seconds for 'restart'\n"]
    now = {"t": 0.0}
    server.timer_mark = 0.0
    server.timer_remainder = 0.0
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: now["t"])
    heard: list[str] = []
    for second in range(1, 5):
        now["t"] = float(second)
        server.all_lines.clear()
        process_timer(server)  # type: ignore[arg-type]
        heard.extend(server.all_lines)
    assert heard == [
        "Shutting down in 4 seconds for 'restart'\n",
        "Shutting down in 3 seconds for 'restart'\n",
        "Shutting down in 2 seconds for 'restart'\n",
        "Shutting down in 1 second for 'restart'\n",
        "Shutting down now!\n",
    ]
    assert server.stopped is False


def test_a_jump_to_zero_still_says_the_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipped marks stay unsaid. Passing 1 still says the pair, then stops."""
    server = login_host(tmp_path)
    server.clients = {}  # type: ignore[attr-defined]
    process_commands(server, staff("Ada", Rank.CODER), "shutdown 6 restart")  # type: ignore[arg-type]
    server.timer_mark = 0.0
    server.timer_remainder = 0.0
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: 6.0)
    server.all_lines.clear()
    process_timer(server)  # type: ignore[arg-type]
    assert server.all_lines == [
        "Shutting down in 1 second for 'restart'\n",
        "Shutting down now!\n",
    ]
    assert server.stopped is True


def test_shutdown_now_stops_without_the_pair(tmp_path: Path) -> None:
    """Now speaks one line and stops, and it replaces a running countdown."""
    server = login_host(tmp_path)
    process_commands(server, staff("Ada", Rank.CODER), "shutdown 600 restart")  # type: ignore[arg-type]
    server.all_lines.clear()
    process_commands(server, staff("Ada", Rank.CODER), "shutdown now maintenance")  # type: ignore[arg-type]
    assert server.all_lines == ["Shutting down now for 'maintenance'.\n"]
    assert server.stopped is True
    assert server.shutdown_seconds is None


def test_shutdown_now_skips_login_timeout_for_a_pending_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An overdue login is not told Login timed out after shutdown now."""
    server = login_host(tmp_path)
    waiting = FakePlayer(name=None, room=None, player=None, auth="set", pending_name="Bea", accepted_at=1_000.0)
    server.people = [waiting]
    server.clients = {"s": waiting}  # type: ignore[attr-defined]
    admin = staff("Ada", Rank.CODER)
    process_commands(server, admin, "shutdown now restart")  # type: ignore[arg-type]
    assert server.all_lines == ["Shutting down now for 'restart'.\n"]
    assert server.stopped is True
    server.sent.clear()
    advance(server, monkeypatch, 2_000.0)
    assert "Login timed out.\n" not in server.sent
    assert waiting not in server.dropped_clients


def test_shutdown_cancel_clears_the_countdown(tmp_path: Path) -> None:
    """Cancel tells the rooms, and a second cancel tells only the admin."""
    server = login_host(tmp_path)
    admin = staff("Ada", Rank.CODER)
    process_commands(server, admin, "shutdown 600 restart")  # type: ignore[arg-type]
    server.all_lines.clear()
    server.sent.clear()
    process_commands(server, admin, "shutdown cancel maintenance finished")  # type: ignore[arg-type]
    assert server.all_lines == ["Shutdown cancelled for 'maintenance finished'.\n"]
    assert server.stopped is False
    assert server.shutdown_seconds is None
    server.all_lines.clear()
    server.sent.clear()
    process_commands(server, admin, "shutdown cancel again")  # type: ignore[arg-type]
    assert server.all_lines == []
    assert server.sent == ["No shutdown is running.\n"]


def test_a_second_shutdown_replaces_the_first(tmp_path: Path) -> None:
    """One countdown exists. The new time is spoken at once."""
    server = login_host(tmp_path)
    admin = staff("Ada", Rank.CODER)
    process_commands(server, admin, "shutdown 600 restart")  # type: ignore[arg-type]
    server.all_lines.clear()
    process_commands(server, admin, "shutdown 30 maintenance")  # type: ignore[arg-type]
    assert server.shutdown_seconds == 30
    assert server.shutdown_reason == "maintenance"
    assert server.all_lines == ["Shutting down in 30 seconds for 'maintenance'\n"]
    assert server.stopped is False


def test_shutdown_1_says_the_pair_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening at 1 second says the pair, and the next second only stops."""
    server = login_host(tmp_path)
    server.clients = {}  # type: ignore[attr-defined]
    process_commands(server, staff("Ada", Rank.CODER), "shutdown 1 restart")  # type: ignore[arg-type]
    assert server.all_lines == [
        "Shutting down in 1 second for 'restart'\n",
        "Shutting down now!\n",
    ]
    assert server.stopped is False
    server.timer_mark = 0.0
    server.timer_remainder = 0.0
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: 1.0)
    server.all_lines.clear()
    process_timer(server)  # type: ignore[arg-type]
    assert server.all_lines == []
    assert server.stopped is True


def test_shutdown_rejects_a_bad_count_and_hides_from_a_super_user(tmp_path: Path) -> None:
    """Below 1 is syntax. A super user is told the command does not exist."""
    server = login_host(tmp_path)
    process_commands(server, staff("Ada", Rank.CODER), "shutdown 0 restart")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: shutdown <seconds> <reason>\n"]
    assert server.shutdown_seconds is None
    server.sent.clear()
    process_commands(server, staff("Ada", Rank.SUPER_USER), "shutdown 10 restart")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'shutdown'\n"]
    assert "shutdown" not in command_words(Rank.SUPER_USER).split()
    assert "shutdown" not in command_words(Rank.ADMIN).split()
    assert "shutdown" in command_words(Rank.CODER).split()


def test_shutdown_rejects_a_unicode_digit_count(tmp_path: Path) -> None:
    """A superscript digit is not a count, and it does not start a countdown."""
    server = login_host(tmp_path)
    process_commands(server, staff("Ada", Rank.CODER), "shutdown ² restart")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: shutdown <seconds> <reason>\n"]
    assert server.stopped is False
    assert server.shutdown_seconds is None


def test_a_site_admin_resident_can_run_shutdown(tmp_path: Path) -> None:
    """The site-admin role runs shutdown. Resident rank alone does not."""
    server = login_host(tmp_path)
    plain = staff("Ada", Rank.RESIDENT)
    process_commands(server, plain, "shutdown now restart")  # type: ignore[arg-type]
    assert server.sent[-1] == "No such command 'shutdown'\n"
    assert server.stopped is False
    resident = staff("Bea", Rank.RESIDENT)
    assert resident.player is not None
    resident.player.roles = int(Roles.SITE_ADMIN)
    process_commands(server, resident, "shutdown now restart")  # type: ignore[arg-type]
    assert server.stopped is True


def test_reload_help_replaces_the_book(tmp_path: Path) -> None:
    """reload help installs the file and tells the admin."""  # noqa: D403
    path = tmp_path / "help.yml"
    path.write_text("- name: say\n  aliases: []\n  text: Hello.\n", encoding="utf-8")
    server = FakeServer()
    server.config = replace(server.config, help=path)
    server.help_failed = HelpStamp(1, 2)
    ada = staff("Ada", Rank.CODER)
    assert ada.player is not None
    ada.player.colour = False
    server.people = [ada]
    process_commands(server, ada, "reload help")  # type: ignore[arg-type]
    loaded = server.help.find("say")
    assert loaded is not None
    assert loaded.text == "Hello."
    assert server.sent == ["Help reloaded.\n"]
    assert server.admin_lines == []
    assert server.help_stamp == help_stamp(path)
    assert server.help_failed is None


def test_reload_help_keeps_the_book_when_the_file_is_not_utf8(tmp_path: Path) -> None:
    """A non-UTF-8 file leaves the old book and tells the admin once."""
    path = tmp_path / "help.yml"
    path.write_text(GOOD_HELP, encoding="utf-8")
    server = FakeServer()
    server.config = replace(server.config, help=path)
    server.help = load_help(path)
    previous = server.help
    ada = staff("Ada", Rank.CODER)
    assert ada.player is not None
    ada.player.colour = False
    server.people = [ada]
    path.write_bytes(INVALID_UTF8_HELP)
    process_commands(server, ada, "reload help")  # type: ignore[arg-type]
    assert server.help is previous
    assert len(server.sent) == 1
    assert server.sent[0].startswith("Cannot reload help: Help file ")
    assert "is not valid UTF-8:" in server.sent[0]
    assert server.admin_lines == []
    assert server.help_failed == help_stamp(path)


def test_reload_help_keeps_the_book_when_the_file_is_bad(tmp_path: Path) -> None:
    """A bad file leaves the old book and tells the admin the load error."""
    path = tmp_path / "help.yml"
    path.write_text("- name: say\n  aliases: []\n", encoding="utf-8")
    server = FakeServer()
    server.config = replace(server.config, help=path)
    previous = server.help
    ada = staff("Ada", Rank.CODER)
    assert ada.player is not None
    ada.player.colour = False
    server.people = [ada]
    process_commands(server, ada, "reload help")  # type: ignore[arg-type]
    assert server.help is previous
    assert server.sent == ["Cannot reload help: Help entry say is missing text.\n"]
    assert server.admin_lines == []
    assert server.help_failed == help_stamp(path)


def test_reload_help_reads_the_file_when_the_stamp_matches(tmp_path: Path) -> None:
    """reload help reads the file even when the stamp already matches."""  # noqa: D403
    path = tmp_path / "help.yml"
    path.write_text("- name: say\n  aliases: []\n  text: Hello.\n", encoding="utf-8")
    server = FakeServer()
    server.config = replace(server.config, help=path)
    server.help_stamp = help_stamp(path)
    ada = staff("Ada", Rank.CODER)
    server.people = [ada]
    process_commands(server, ada, "reload help")  # type: ignore[arg-type]
    loaded = server.help.find("say")
    assert loaded is not None
    assert loaded.text == "Hello."


def test_reload_rooms_keeps_a_bad_file(tmp_path: Path) -> None:
    """A bad system file keeps the office and does not move anyone."""
    server = FakeServer()
    path = tmp_path / "system.yml"
    path.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    server.config = replace(server.config, rooms=path, start="reception")
    server.layout = load_layout(path).with_void()
    server.rooms_stamp = help_stamp(path)
    server.rooms_failed = None
    server.house_stamps = {}
    server.house_failed = {}
    ada = staff("Ada", Rank.CODER)
    server.people = [ada]
    path.write_text(":\n", encoding="utf-8")
    process_commands(server, ada, "reload rooms")  # type: ignore[arg-type]
    assert ada.room == "reception"
    assert server.sent[-1].startswith("Cannot reload rooms: ")


def test_reload_rooms_moves_a_removed_room_to_the_void(tmp_path: Path) -> None:
    """A player whose room was removed is told and moved to The Void."""
    server = FakeServer()
    path = tmp_path / "system.yml"
    path.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits:\n    n: kitchen\nkitchen:\n  description: Mugs.\n  max_occupancy: 2\n  exits:\n    s: reception\n", encoding="utf-8")
    server.config = replace(server.config, rooms=path, start="reception")
    server.layout = load_layout(path).with_void()
    server.rooms_stamp = help_stamp(path)
    server.rooms_failed = None
    server.house_stamps = {}
    server.house_failed = {}
    ada = staff("Ada", Rank.CODER)
    ada.room = "kitchen"
    assert ada.player is not None
    ada.player.room = "kitchen"
    server.people = [ada]
    path.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    process_commands(server, ada, "reload rooms")  # type: ignore[arg-type]
    assert ada.room == "void"
    assert "The room you were in has gone.\n" in server.sent
    assert "Rooms reloaded.\n" in server.sent


def test_reload_rooms_refuses_to_drop_the_start_room(tmp_path: Path) -> None:
    """Removing the start room keeps the old office."""
    server = FakeServer()
    path = tmp_path / "system.yml"
    path.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    server.config = replace(server.config, rooms=path, start="reception")
    server.layout = load_layout(path).with_void()
    server.rooms_stamp = help_stamp(path)
    server.rooms_failed = None
    server.house_stamps = {}
    server.house_failed = {}
    ada = staff("Ada", Rank.CODER)
    server.people = [ada]
    path.write_text("kitchen:\n  description: Mugs.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    process_commands(server, ada, "reload rooms")  # type: ignore[arg-type]
    assert ada.room == "reception"
    assert "reception" in server.layout.rooms
    assert server.sent[-1].startswith("Cannot reload rooms: Start room reception is not in ")


def test_reload_rooms_refuses_to_drop_newbie_start(tmp_path: Path) -> None:
    """Removing the newbie start room keeps the old office."""
    server = FakeServer()
    path = tmp_path / "system.yml"
    path.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits: {}\nwaiting_room:\n  description: Chairs.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    server.config = replace(server.config, rooms=path, start="reception", newbie_start="waiting_room")
    server.layout = load_layout(path).with_void()
    server.rooms_stamp = help_stamp(path)
    server.rooms_failed = None
    server.house_stamps = {}
    server.house_failed = {}
    ada = staff("Ada", Rank.CODER)
    server.people = [ada]
    path.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    process_commands(server, ada, "reload rooms")  # type: ignore[arg-type]
    assert "waiting_room" in server.layout.rooms
    assert server.sent[-1].startswith("Cannot reload rooms: Newbie start room waiting_room is not in ")


def test_reload_rooms_reports_the_start_room_when_newbie_start_matches(tmp_path: Path) -> None:
    """When newbie_start matches start, reload still reports the start-room error."""
    server = FakeServer()
    path = tmp_path / "system.yml"
    path.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    server.config = replace(server.config, rooms=path, start="reception", newbie_start="reception")
    server.layout = load_layout(path).with_void()
    server.rooms_stamp = help_stamp(path)
    server.rooms_failed = None
    server.house_stamps = {}
    server.house_failed = {}
    ada = staff("Ada", Rank.CODER)
    server.people = [ada]
    path.write_text("kitchen:\n  description: Mugs.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    process_commands(server, ada, "reload rooms")  # type: ignore[arg-type]
    assert ada.room == "reception"
    assert "reception" in server.layout.rooms
    assert server.sent[-1].startswith("Cannot reload rooms: Start room reception is not in ")


def test_reload_rooms_keeps_a_bad_house_and_updates_the_office(tmp_path: Path) -> None:
    """A bad house file stays, and the office still reloads."""
    server = FakeServer()
    system = tmp_path / "system.yml"
    system.write_text("reception:\n  description: Old.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    house = tmp_path / "ada.yml"
    house.write_text("ada_home:\n  description: You are home.\n  max_occupancy: 1\n  exits: {}\n", encoding="utf-8")
    server.config = replace(server.config, rooms=system, start="reception")
    server.layout = load_layout(system).with_void().with_house("Ada", load_house(house, "Ada", {"reception"}))
    server.rooms_stamp = help_stamp(system)
    server.rooms_failed = None
    server.house_stamps = {}
    server.house_failed = {}
    ada = staff("Ada", Rank.CODER)
    ada.room = "ada_home"
    assert ada.player is not None
    ada.player.room = "ada_home"
    server.people = [ada]
    system.write_text("reception:\n  description: New.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    house.write_text(":\n", encoding="utf-8")
    process_commands(server, ada, "reload rooms")  # type: ignore[arg-type]
    assert ada.room == "ada_home"
    assert server.layout.rooms["reception"].description == "New."
    assert server.layout.rooms["ada_home"].description == "You are home."
    assert any(line.startswith("Cannot reload ada's rooms: ") for line in server.sent)


def test_reload_rooms_matches_any_case(tmp_path: Path) -> None:
    """Reload ROOMS reloads rooms, and an unknown word keeps the typed case."""
    server = FakeServer()
    path = tmp_path / "system.yml"
    path.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    server.config = replace(server.config, rooms=path, start="reception")
    server.layout = load_layout(path).with_void()
    server.rooms_stamp = help_stamp(path)
    server.rooms_failed = None
    server.house_stamps = {}
    server.house_failed = {}
    ada = staff("Ada", Rank.CODER)
    server.people = [ada]
    process_commands(server, ada, "reload ROOMS")  # type: ignore[arg-type]
    assert server.sent == ["Rooms reloaded.\n"]
    server.sent.clear()
    process_commands(server, ada, "reload Banners")  # type: ignore[arg-type]
    assert server.sent == ["No such subcommand 'Banners'.\n"]


def test_a_bad_rooms_file_is_announced_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad system file announces once for that stamp."""
    server, _path = help_host(tmp_path, GOOD_HELP)
    rooms = tmp_path / "system.yml"
    rooms.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    server.config = replace(server.config, rooms=rooms, start="reception")
    server.layout = load_layout(rooms).with_void()
    server.rooms_stamp = help_stamp(rooms)
    server.rooms_failed = None
    server.house_stamps = {}
    server.house_failed = {}
    rooms.write_text(":\n", encoding="utf-8")
    advance(server, monkeypatch, 300)
    assert len(server.coder_lines) == 1
    assert server.coder_lines[0].startswith("{ Coder } Cannot reload rooms: ")
    advance(server, monkeypatch, 300)
    assert len(server.coder_lines) == 1


def test_reload_of_an_unknown_type_does_not_read_the_file(tmp_path: Path) -> None:
    """reload banners is refused before the help file is opened."""  # noqa: D403
    path = tmp_path / "help.yml"
    path.write_text("- name: say\n  aliases: []\n  text: Hello.\n", encoding="utf-8")
    server = FakeServer()
    server.config = replace(server.config, help=path)
    previous = server.help
    ada = staff("Ada", Rank.CODER)
    server.people = [ada]
    process_commands(server, ada, "reload banners")  # type: ignore[arg-type]
    assert server.help is previous
    assert server.sent == ["No such subcommand 'banners'.\n"]


def test_bare_reload_replies_with_the_syntax() -> None:
    """reload with no type uses the normal syntax reply."""  # noqa: D403
    ada = staff("Ada", Rank.CODER)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "reload")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: reload <command>\n"]


def write_hot(directory: Path, text: str) -> Path:
    """Write hot.yml and return its path."""
    path = directory / "hot.yml"
    path.write_text(text, encoding="utf-8")
    return path


def reload_host(tmp_path: Path) -> FakeServer:
    """Return an admin server with a rooms file, a help file, and the three message files."""
    server, _help_path = help_host(tmp_path, GOOD_HELP)
    rooms = tmp_path / "system.yml"
    rooms.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    hot = write_hot(tmp_path, "start: reception\nmax_connections: 128\nmax_newbies: 20\nallow_newbies: true\nallow_residents: true\n")
    server.config = replace(server.config, rooms=rooms, start="reception", newbie_start="reception", hot_path=hot, max_connections=128, max_newbies=20)
    server.hot_stamp = help_stamp(hot)
    server.hot_failed = None
    server.layout = load_layout(rooms).with_void()
    server.rooms_stamp = help_stamp(rooms)
    write_messages(server.config.messages_path)
    return server


def test_reload_messages_tells_the_admin(tmp_path: Path) -> None:
    """Reload messages replaces the three strings and says they reloaded."""
    server = reload_host(tmp_path)
    server.banner = "Old banner\n"
    ada = server.people[0]
    process_commands(server, ada, "reload messages")  # type: ignore[arg-type]
    assert server.banner == "Banner\n"
    assert server.full_message == "Full\n"
    assert server.newbie_message == "Newbie\n"
    assert server.sent == ["Messages reloaded.\n"]


def test_reload_messages_reports_only_the_bad_file(tmp_path: Path) -> None:
    """A bad banner leaves the old banner and does not add a success line."""
    server = reload_host(tmp_path)
    server.banner = "Old banner\n"
    server.full_message = "Old full\n"
    ada = server.people[0]
    (tmp_path / "files" / "banner.msg").write_bytes(b"caf\xe9\n")
    (tmp_path / "files" / "full.msg").write_text("Newer full\n", encoding="utf-8")
    process_commands(server, ada, "reload MESSAGES")  # type: ignore[arg-type]
    assert server.banner == "Old banner\n"
    assert server.full_message == "Newer full\n"
    assert server.newbie_message == "Newbie\n"
    assert len(server.sent) == 1
    assert server.sent[0].startswith("Cannot reload banner: ")
    assert "Messages reloaded." not in server.sent[0]


def test_reload_help_please_does_not_read_the_file(tmp_path: Path) -> None:
    """An extra word is syntax and leaves the book alone."""
    server = reload_host(tmp_path)
    previous = server.help
    ada = server.people[0]
    process_commands(server, ada, "reload help please")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: reload help\n"]
    assert server.help is previous


def test_reload_commands_lists_the_children() -> None:
    """Reload commands lists the six names in alphabetical order."""
    ada = staff("Ada", Rank.CODER)
    assert ada.player is not None
    ada.player.colour = False
    ada.frame_columns = 200
    server, _ada = run("reload commands", ada)
    words = server.sent[0].split("Subcommands available:\n", 1)[1].split()
    assert words == ["all", "commands", "config", "help", "messages", "rooms"]


def test_reload_all_sends_rooms_then_help_then_messages(tmp_path: Path) -> None:
    """Reload all sends the rooms line, then help, then messages."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    process_commands(server, ada, "reload all")  # type: ignore[arg-type]
    assert server.sent == ["Config reloaded.\n", "Rooms reloaded.\n", "Help reloaded.\n", "Messages reloaded.\n"]


def test_reload_all_continues_after_a_bad_help_file(tmp_path: Path) -> None:
    """A bad help file still reloads rooms and messages."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    (tmp_path / "help.yml").write_text(BAD_HELP, encoding="utf-8")
    process_commands(server, ada, "reload all")  # type: ignore[arg-type]
    assert server.sent[0] == "Config reloaded.\n"
    assert server.sent[1] == "Rooms reloaded.\n"
    assert server.sent[2].startswith("Cannot reload help: ")
    assert server.sent[3] == "Messages reloaded.\n"
    assert server.banner == "Banner\n"


def test_reload_config_replaces_the_cap_and_the_gates(tmp_path: Path) -> None:
    """A good hot file replaces the newbie cap and the live gates."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    server.allow_newbies = False
    write_hot(tmp_path, "start: reception\nmax_connections: 128\nmax_newbies: 4\nallow_newbies: true\nallow_residents: false\n")
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    assert server.sent == ["Config reloaded.\n"]
    assert server.config.max_newbies == 4
    assert server.allow_newbies is True
    assert server.allow_residents is False
    assert server.config.allow_residents is False


def test_a_bad_hot_file_keeps_the_old_cap(tmp_path: Path) -> None:
    """A bad hot file leaves the running cap."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    write_hot(tmp_path, "start: reception\nmax_connections: 128\nmax_newbies: no\n")
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    assert server.sent[0].startswith("Cannot reload config: ")
    assert server.config.max_newbies == 20
    assert "Config reloaded." not in "".join(server.sent)


def test_a_cold_key_in_the_hot_file_does_not_change_the_cap(tmp_path: Path) -> None:
    """Port in the hot file fails the reload and leaves the cap."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    write_hot(tmp_path, "port: 8000\nstart: reception\nmax_connections: 128\nmax_newbies: 4\n")
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    assert server.sent == ["Cannot reload config: port belongs in the cold config.\n"]
    assert server.config.max_newbies == 20
    assert server.config.port == 1


def test_a_missing_start_room_fails_the_reload(tmp_path: Path) -> None:
    """A start room that is not loaded fails and leaves the old start."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    write_hot(tmp_path, "start: attic\nmax_connections: 128\nmax_newbies: 20\n")
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    rooms = tmp_path / "system.yml"
    assert server.sent == [f"Cannot reload config: Start room attic is not in {rooms}.\n"]
    assert server.config.start == "reception"


def test_a_loaded_house_cannot_become_the_start_room(tmp_path: Path) -> None:
    """A loaded house cannot become the start room."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    server.layout = server.layout.with_house("Ada", {house_room_id("Ada"): Room("You are home.", 1, {})})
    write_hot(tmp_path, "start: ada_home\nmax_connections: 128\nmax_newbies: 20\n")
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    rooms = tmp_path / "system.yml"
    assert server.sent == [f"Cannot reload config: Start room ada_home is not in {rooms}.\n"]
    assert server.config.start == "reception"


def test_reload_config_does_not_read_the_cold_file(tmp_path: Path) -> None:
    """A broken cold file does not stop a hot reload."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    cold = tmp_path / "cold.yml"
    cold.write_text(":\n", encoding="utf-8")
    server.config = replace(server.config, cold_path=cold)
    write_hot(tmp_path, "start: reception\nmax_connections: 64\nmax_newbies: 20\n")
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    assert server.sent == ["Config reloaded.\n"]
    assert server.config.max_connections == 64
    assert server.config.port == 1


def test_an_unreadable_country_database_turns_lookup_off(tmp_path: Path) -> None:
    """The other hot values apply, and the reply says lookup is off."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    database = tmp_path / "missing.mmdb"
    write_hot(tmp_path, f"start: reception\nmax_connections: 128\nmax_newbies: 20\ncountry_lookup: true\ncountry_database: {database}\n")
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    assert server.sent == ["Config reloaded.\n", f"Country lookup is off. Cannot read {database.resolve()}.\n"]
    assert server.country_lookup is False
    assert server.config.max_newbies == 20


def test_reload_config_closes_the_open_country_database(tmp_path: Path) -> None:
    """A hot reload closes the reader that was already open."""
    server = reload_host(tmp_path)
    ada = server.people[0]

    class Reader:
        """A stand-in country database."""

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            """Record that the reader was closed."""
            self.closed = True

    reader = Reader()
    server.country_db = reader
    write_hot(tmp_path, "start: reception\nmax_connections: 128\nmax_newbies: 20\ncountry_lookup: false\n")
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    assert reader.closed is True
    assert server.country_db is None


def test_turning_mfa_on_registers_the_command(tmp_path: Path) -> None:
    """A hot file with mfa_enabled true registers mfa once."""
    from lupaxa.chat_server.commands import by_name
    from lupaxa.chat_server.globals import COMMANDS

    server = reload_host(tmp_path)
    ada = server.people[0]
    server.mfa_enabled = False
    added = "mfa" not in by_name()
    write_hot(tmp_path, "start: reception\nmax_connections: 128\nmax_newbies: 20\nmfa_enabled: true\n")
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    assert server.mfa_enabled is True
    assert "mfa" in by_name()
    count = len(COMMANDS)
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    assert len(COMMANDS) == count
    assert server.sent == ["Config reloaded.\n", "Config reloaded.\n"]
    if added:
        COMMANDS.remove(by_name().pop("mfa"))


def test_reload_all_continues_after_a_bad_hot_file(tmp_path: Path) -> None:
    """A bad hot file still reloads rooms, help, and messages."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    write_hot(tmp_path, "start: reception\nmax_newbies: 4\n")
    process_commands(server, ada, "reload all")  # type: ignore[arg-type]
    assert server.sent[0].startswith("Cannot reload config: ")
    assert "Rooms reloaded.\n" in server.sent
    assert "Help reloaded.\n" in server.sent
    assert "Messages reloaded.\n" in server.sent
    assert server.sent.index("Rooms reloaded.\n") < server.sent.index("Help reloaded.\n")
    assert server.config.max_newbies == 20


def test_an_off_duty_coder_can_reload(tmp_path: Path) -> None:
    """A coder command still runs while off duty. An admin is told it does not exist."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    assert ada.player is not None
    ada.player.off_duty = True
    process_commands(server, ada, "reload messages")  # type: ignore[arg-type]
    assert server.sent == ["Messages reloaded.\n"]
    boss = staff("Bea", Rank.ADMIN)
    assert boss.player is not None
    boss.player.off_duty = True
    refused = FakeServer(people=[boss])
    process_commands(refused, boss, "reload messages")  # type: ignore[arg-type]
    assert refused.sent == ["No such command 'reload'\n"]


def test_a_super_user_cannot_reload() -> None:
    """Reload is a coder command, so a super user does not see it."""
    ada = staff("Ada", Rank.SUPER_USER)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "reload help")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'reload'\n"]


GOOD_HELP = "- name: say\n  aliases: []\n  text: Hello.\n"
BAD_HELP = "- name: say\n  aliases: []\n"
INVALID_UTF8_HELP = b"- name: say\n  aliases: []\n  text: caf\xe9\n"


def help_host(tmp_path: Path, text: str) -> tuple[FakeServer, Path]:
    """Return an admin server whose help file is ``text`` and whose stamp matches it."""
    path = tmp_path / "help.yml"
    path.write_text(text, encoding="utf-8")
    messages = tmp_path / "files"
    server = FakeServer()
    server.config = replace(server.config, help=path, config_dir=tmp_path, messages_path=messages)
    server.help_stamp = help_stamp(path)
    ada = staff("Ada", Rank.CODER)
    assert ada.player is not None
    ada.player.colour = False
    server.people = [ada]
    return server, path


def write_messages(directory: Path, banner: str = "Banner\n", full: str = "Full\n", newbie: str = "Newbie\n") -> Path:
    """Write the three message files into the messages directory and return that directory."""
    directory.mkdir(exist_ok=True)
    (directory / "banner.msg").write_text(banner, encoding="utf-8")
    (directory / "full.msg").write_text(full, encoding="utf-8")
    (directory / "no-more-newbies.msg").write_text(newbie, encoding="utf-8")
    return directory


def test_reload_messages_reads_messages_path(tmp_path: Path) -> None:
    """Reload messages and the stamps read the directory stored on messages_path."""
    help_path = tmp_path / "docs" / "help.yml"
    help_path.parent.mkdir(parents=True)
    help_path.write_text(GOOD_HELP, encoding="utf-8")
    messages = tmp_path / "messages"
    server = FakeServer()
    server.config = replace(server.config, help=help_path, config_dir=tmp_path, messages_path=messages)
    write_messages(messages, banner="From messages\n", full="Full\n", newbie="Newbie\n")
    decoy = tmp_path / "files"
    decoy.mkdir()
    (decoy / "banner.msg").write_text("From files\n", encoding="utf-8")
    (decoy / "full.msg").write_text("Decoy full\n", encoding="utf-8")
    (decoy / "no-more-newbies.msg").write_text("Decoy newbie\n", encoding="utf-8")
    server.banner = "Old\n"
    assert message_file_stamps(messages)["banner"] == help_stamp(messages / "banner.msg")
    assert reload_messages(server, force=True) == []  # type: ignore[arg-type]
    assert server.banner == "From messages\n"


def test_reload_messages_replaces_the_three_files(tmp_path: Path) -> None:
    """Reload messages replaces the banner, the full message, and the newbie message."""
    server, _path = help_host(tmp_path, GOOD_HELP)
    server.banner = "Old banner\n"
    server.full_message = "Old full\n"
    server.newbie_message = "Old newbie\n"
    write_messages(server.config.messages_path)
    assert reload_messages(server, force=True) == []  # type: ignore[arg-type]
    assert server.banner == "Banner\n"
    assert server.full_message == "Full\n"
    assert server.newbie_message == "Newbie\n"


def test_a_bad_banner_leaves_the_old_banner(tmp_path: Path) -> None:
    """A banner that is not UTF-8 stays, and the other two files still replace."""
    server, _path = help_host(tmp_path, GOOD_HELP)
    server.banner = "Old banner\n"
    files = write_messages(server.config.messages_path)
    assert reload_messages(server, force=True) == []  # type: ignore[arg-type]
    (files / "banner.msg").write_bytes(b"caf\xe9\n")
    (files / "full.msg").write_text("Newer full\n", encoding="utf-8")
    (files / "no-more-newbies.msg").write_text("Newer newbie\n", encoding="utf-8")
    errors = reload_messages(server, force=True)  # type: ignore[arg-type]
    assert len(errors) == 1
    assert errors[0].startswith("Cannot reload banner: ")
    assert "is not valid UTF-8:" in errors[0]
    assert server.banner == "Banner\n"
    assert server.full_message == "Newer full\n"
    assert server.newbie_message == "Newer newbie\n"


def test_a_changed_banner_reloads_without_a_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A changed banner replaces the text after five minutes, and admins are not told."""
    server, _path = help_host(tmp_path, GOOD_HELP)
    files = write_messages(server.config.messages_path, banner="Old banner\n")
    assert reload_messages(server, force=True) == []  # type: ignore[arg-type]
    (files / "banner.msg").write_text("New banner\n", encoding="utf-8")
    advance(server, monkeypatch, 300)
    assert server.banner == "New banner\n"
    assert server.admin_lines == []


def test_a_bad_banner_is_announced_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A banner that is not UTF-8 keeps the old text and announces that stamp once."""
    server, _path = help_host(tmp_path, GOOD_HELP)
    files = write_messages(server.config.messages_path)
    assert reload_messages(server, force=True) == []  # type: ignore[arg-type]
    previous = server.banner
    (files / "banner.msg").write_bytes(b"caf\xe9\n")
    advance(server, monkeypatch, 300)
    assert server.banner == previous
    assert len(server.coder_lines) == 1
    assert server.coder_lines[0].startswith("{ Coder } Cannot reload banner: ")
    assert "is not valid UTF-8:" in server.coder_lines[0]
    advance(server, monkeypatch, 300)
    assert len(server.coder_lines) == 1


def test_startup_records_missing_message_stamps(tmp_path: Path) -> None:
    """Startup records the missing stamp for a message file that is not there."""
    help_path = tmp_path / "help.yml"
    help_path.write_text(GOOD_HELP, encoding="utf-8")
    rooms = tmp_path / "rooms.yml"
    rooms.write_text("reception:\n  description: Hi.\n  max_occupancy: 2\n  exits: {}\n", encoding="utf-8")
    config = replace(FakeServer().config, help=help_path, rooms=rooms, start="reception", players=tmp_path, config_dir=tmp_path, messages_path=tmp_path / "files")
    server = ChatServer(config, load_layout(rooms).with_void(), load_help(help_path))
    assert set(server.message_stamps) == {"banner", "full", "no-more-newbies"}
    assert server.message_stamps["banner"] == HelpStamp(-1, -1)
    assert server.message_failed == {}


def test_help_is_not_checked_before_five_minutes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """299 seconds leaves the book alone."""
    server, path = help_host(tmp_path, GOOD_HELP)
    previous = server.help
    path.write_text(GOOD_HELP.replace("Hello.", "Updated."), encoding="utf-8")
    advance(server, monkeypatch, 299)
    assert server.help is previous
    assert server.admin_lines == []


def test_an_unchanged_help_file_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """300 seconds with the same stamp does not replace the book."""
    server, path = help_host(tmp_path, GOOD_HELP)
    previous = server.help
    advance(server, monkeypatch, 300)
    assert server.help is previous
    assert server.admin_lines == []
    assert server.help_check_seconds == 0


def test_a_changed_help_file_reloads_without_a_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A new good file replaces the book and the admin channel stays quiet."""
    server, path = help_host(tmp_path, GOOD_HELP)
    path.write_text(GOOD_HELP.replace("Hello.", "Updated."), encoding="utf-8")
    advance(server, monkeypatch, 300)
    loaded = server.help.find("say")
    assert loaded is not None
    assert loaded.text == "Updated."
    assert server.admin_lines == []


def test_a_non_utf8_help_file_is_announced_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-UTF-8 file keeps the book and announces that stamp once."""
    server, path = help_host(tmp_path, GOOD_HELP)
    server.help = load_help(path)
    previous = server.help
    path.write_bytes(INVALID_UTF8_HELP)
    advance(server, monkeypatch, 300)
    assert server.help is previous
    assert len(server.coder_lines) == 1
    assert server.coder_lines[0].startswith("{ Coder } Cannot reload help: Help file ")
    assert "is not valid UTF-8:" in server.coder_lines[0]
    advance(server, monkeypatch, 300)
    assert len(server.coder_lines) == 1


def test_a_bad_change_is_announced_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same failed stamp does not announce again. 700 seconds checks once."""
    server, path = help_host(tmp_path, GOOD_HELP)
    previous = server.help
    path.write_text(BAD_HELP, encoding="utf-8")
    advance(server, monkeypatch, 700)
    assert server.help is previous
    assert server.help_check_seconds == 100
    assert server.coder_lines == ["{ Coder } Cannot reload help: Help entry say is missing text.\n"]
    assert server.admin_lines == []
    advance(server, monkeypatch, 300)
    assert server.help is previous
    assert len(server.coder_lines) == 1


def test_a_missing_help_file_is_announced_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting the file keeps the book and announces that version once."""
    server, path = help_host(tmp_path, GOOD_HELP)
    previous = server.help
    path.unlink()
    advance(server, monkeypatch, 300)
    assert server.help is previous
    assert len(server.coder_lines) == 1
    assert server.coder_lines[0].startswith("{ Coder } Cannot reload help: Cannot read help file ")
    advance(server, monkeypatch, 300)
    assert len(server.coder_lines) == 1


def test_a_fix_after_a_failed_reload_clears_the_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A later good file replaces the book and does not send another admin line."""
    server, path = help_host(tmp_path, GOOD_HELP)
    path.write_text(BAD_HELP, encoding="utf-8")
    advance(server, monkeypatch, 300)
    path.write_text(GOOD_HELP.replace("Hello.", "Fixed."), encoding="utf-8")
    advance(server, monkeypatch, 300)
    loaded = server.help.find("say")
    assert loaded is not None
    assert loaded.text == "Fixed."
    assert len(server.coder_lines) == 1


def test_a_manual_failure_is_not_announced_again_by_the_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reload help records the failed stamp, so the timer stays quiet."""
    server, path = help_host(tmp_path, GOOD_HELP)
    ada = server.people[0]
    path.write_text(BAD_HELP, encoding="utf-8")
    process_commands(server, ada, "reload help")  # type: ignore[arg-type]
    assert server.admin_lines == []
    advance(server, monkeypatch, 300)
    assert server.admin_lines == []


def test_a_later_bad_save_is_announced_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A new failed stamp is announced once, after an earlier failure."""
    server, path = help_host(tmp_path, GOOD_HELP)
    path.write_text(BAD_HELP, encoding="utf-8")
    advance(server, monkeypatch, 300)
    path.write_text("- name: look\n  aliases: []\n", encoding="utf-8")
    advance(server, monkeypatch, 300)
    assert server.coder_lines == [
        "{ Coder } Cannot reload help: Help entry say is missing text.\n",
        "{ Coder } Cannot reload help: Help entry look is missing text.\n",
    ]


def test_an_unchanged_hot_file_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """300 seconds with the same hot stamp does not replace the cap."""
    server = reload_host(tmp_path)
    advance(server, monkeypatch, 300)
    assert server.config.max_newbies == 20
    assert server.coder_lines == []
    assert server.admin_lines == []


def test_a_changed_hot_file_reloads_without_a_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A new good hot file replaces the cap and stays quiet."""
    server = reload_host(tmp_path)
    write_hot(tmp_path, "start: reception\nmax_connections: 128\nmax_newbies: 4\nallow_newbies: false\n")
    advance(server, monkeypatch, 300)
    assert server.config.max_newbies == 4
    assert server.allow_newbies is False
    assert server.coder_lines == []


def test_a_bad_hot_stamp_is_announced_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same broken hot stamp announces once. Saving again tries once more."""
    server = reload_host(tmp_path)
    write_hot(tmp_path, "start: reception\nmax_newbies: no\n")
    advance(server, monkeypatch, 300)
    assert server.config.max_newbies == 20
    assert server.coder_lines == ["{ Coder } Cannot reload config: Config is missing max_connections.\n"]
    assert server.admin_lines == []
    advance(server, monkeypatch, 300)
    assert server.coder_lines == ["{ Coder } Cannot reload config: Config is missing max_connections.\n"]
    write_hot(tmp_path, "start: attic\nmax_connections: 128\nmax_newbies: 20\n")
    advance(server, monkeypatch, 300)
    assert len(server.coder_lines) == 2
    assert server.config.max_newbies == 20


def test_a_manual_hot_failure_is_not_announced_again_by_the_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reload config records the failed stamp, so the timer stays quiet."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    write_hot(tmp_path, "start: reception\nmax_newbies: no\n")
    process_commands(server, ada, "reload config")  # type: ignore[arg-type]
    assert server.coder_lines == []
    advance(server, monkeypatch, 300)
    assert server.coder_lines == []


def test_a_timer_failure_reaches_coders_and_a_site_admin_below_coder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A coder who is also a site admin gets one copy. A lower site admin gets the direct line. A locked screen stays quiet."""
    server = reload_host(tmp_path)
    ada = server.people[0]
    assert ada.player is not None
    ada.player.roles = int(Roles.SITE_ADMIN)
    ada.player.colour = False
    bea = staff("Bea", Rank.ADMIN)
    assert bea.player is not None
    bea.player.roles = int(Roles.SITE_ADMIN)
    bea.player.colour = False
    bea.player.off_duty = True
    cyd = staff("Cyd", Rank.ADMIN)
    assert cyd.player is not None
    cyd.player.colour = False
    cyd.player.screen_locked = True
    cyd.player.roles = int(Roles.SITE_ADMIN)
    dee = staff("Dee", Rank.ADMIN)
    assert dee.player is not None
    dee.player.colour = False
    server.people = [ada, bea, cyd, dee]
    write_hot(tmp_path, "start: reception\nmax_newbies: no\n")
    advance(server, monkeypatch, 300)
    line = "{ Coder } Cannot reload config: Config is missing max_connections.\n"
    assert server.coder_lines == [line]
    assert server.admin_lines == []
    assert ada.sent.count(line) == 1
    assert bea.sent == [line]
    assert cyd.sent == []
    assert dee.sent == []


def test_help_is_not_reloaded_while_the_server_is_stopping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stopping server does not read a changed help file."""
    server, path = help_host(tmp_path, GOOD_HELP)
    previous = server.help
    path.write_text(GOOD_HELP.replace("Hello.", "Updated."), encoding="utf-8")
    server.stopped = True
    advance(server, monkeypatch, 300)
    assert server.help is previous


def test_a_login_that_never_enters_is_timed_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three minutes from accept, a connection with no room is told and dropped."""
    server = login_host(tmp_path)
    waiting = FakePlayer(name=None, room=None, player=None, auth="set", pending_name="Bea", accepted_at=1_000.0)
    server.people = [waiting]
    server.clients = {"s": waiting}  # type: ignore[attr-defined]
    advance(server, monkeypatch, 1_000.0 + 179)
    assert waiting not in server.dropped_clients
    assert server.sent == []
    advance(server, monkeypatch, 1_000.0 + 180)
    assert server.sent == ["Login timed out.\n"]
    assert waiting in server.dropped_clients
    assert "bea" not in server.unsaved_names()


def test_a_player_in_a_room_is_not_login_timed_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A player who already has a room is not dropped by the login limit."""
    server = login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    bea = FakePlayer(player=user, accepted_at=0.0)
    server.clients = {"b": bea}  # type: ignore[attr-defined]
    advance(server, monkeypatch, 10_000.0)
    assert bea not in server.dropped_clients
    assert server.sent == []


def test_process_timer_adds_the_seconds_to_the_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each whole second applied to idle is also added to the connected total."""
    server = login_host(tmp_path)
    server.timer_mark = 10.0  # type: ignore[attr-defined]
    server.timer_remainder = 0.0  # type: ignore[attr-defined]
    server.clients = {}  # type: ignore[attr-defined]
    user = Player("Ada", "hall", datetime.now(timezone.utc))
    server.clients["a"] = FakePlayer(player=user)  # type: ignore[attr-defined]
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: 13.0)
    process_timer(server)  # type: ignore[arg-type]
    assert user.idle_seconds == 3
    assert user.connected_seconds == 3


def test_login_loads_colour_off(tmp_path: Path) -> None:
    """A saved player starts from the colour stored in the file."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), colour=False))
    server = login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    entry_api.load_profile(server, user)  # type: ignore[arg-type]
    assert user.colour is False


def test_entry_loads_the_saved_total(tmp_path: Path) -> None:
    """A saved player enters with the total from the file, already flushed."""
    seconds_saved(tmp_path, 7200)
    server = login_host(tmp_path)
    bea = FakePlayer(player=None)
    process_input(server, bea, "Bea")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    assert bea.player is not None
    assert bea.player.connected_seconds == 7200
    assert bea.player.flushed_seconds == 7200


def test_a_newbie_enters_with_no_total(tmp_path: Path) -> None:
    """A name with no file starts the total at zero."""
    server = login_host(tmp_path)
    ada = FakePlayer(player=None)
    process_input(server, ada, "Ada")  # type: ignore[arg-type]
    process_input(server, ada, "yes")  # type: ignore[arg-type]
    assert ada.player is not None
    assert ada.player.connected_seconds == 0
    assert ada.player.flushed_seconds == 0


def test_a_new_resident_is_saved_with_the_total_so_far(tmp_path: Path) -> None:
    """Promotion stores the seconds already counted and marks them flushed."""
    bea = newbie("Bea")
    assert bea.player is not None
    bea.player.connected_seconds = 120
    ada = staff("Ada", Rank.SUPER_USER)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "resident Bea")  # type: ignore[arg-type]
    process_input(server, bea, "hunter2")  # type: ignore[arg-type]
    process_input(server, bea, "hunter2")  # type: ignore[arg-type]
    assert saved_seconds(tmp_path) == 120
    assert bea.player.flushed_seconds == 120


def test_a_newbie_cannot_set_entermsg() -> None:
    """The command does not exist below resident."""
    server, _ = run("set entermsg crawls into the room")
    assert server.sent == ["No such command 'set'\n"]


def test_a_resident_sets_and_clears_entermsg(tmp_path: Path) -> None:
    """The sentence is saved, and a bare command removes it."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    process_commands(server, bea, "set entermsg crawls into the room")  # type: ignore[arg-type]
    assert server.sent[-1] == "Enter message set.\n"
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.entermsg == "crawls into the room"
    server.sent.clear()
    process_commands(server, bea, "set entermsg")  # type: ignore[arg-type]
    assert "Enter message cleared.\n" in server.sent[-1]
    cleared = load_saved(player_path(tmp_path, "Bea"))
    assert cleared is not None
    assert cleared.entermsg is None


def test_entermsg_rejects_more_than_80_characters(tmp_path: Path) -> None:
    """A long sentence is refused and the file stays unchanged."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    process_commands(server, bea, "set entermsg " + ("x" * 81))  # type: ignore[arg-type]
    assert "An enter message is at most 80 characters.\n" in "".join(server.sent)
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.entermsg is None


def entermsg_of(tmp_path: Path) -> str | None:
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    return saved.entermsg


def test_the_default_entermsg_stores_no_key(tmp_path: Path) -> None:
    """Choosing the usual line removes a saved enter message."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), entermsg="crawls into the room"))
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    process_commands(server, bea, "set entermsg arrives.")  # type: ignore[arg-type]
    assert entermsg_of(tmp_path) is None
    assert "entermsg" not in (tmp_path / "bea.yml").read_text(encoding="utf-8")
    assert bea.player is not None
    assert bea.player.entermsg == "arrives."
    assert server.sent == ["Enter message set.\n"]


def test_save_after_default_entermsg_stores_no_key(tmp_path: Path) -> None:
    """Save keeps the default enter message out of the player file."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), entermsg="crawls into the room"))
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    process_commands(server, bea, "set entermsg arrives.")  # type: ignore[arg-type]
    server.sent.clear()
    process_commands(server, bea, "save")  # type: ignore[arg-type]
    assert entermsg_of(tmp_path) is None
    assert "entermsg" not in (tmp_path / "bea.yml").read_text(encoding="utf-8")


def test_a_resident_sets_loginmsg_and_logoutmsg(tmp_path: Path) -> None:
    """Login and logout sentences are saved, and the defaults omit the keys."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    process_commands(server, bea, "set loginmsg wanders in")  # type: ignore[arg-type]
    process_commands(server, bea, "set logoutmsg slips away")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.loginmsg == "wanders in"
    assert saved.logoutmsg == "slips away"
    assert bea.player is not None
    assert bea.player.loginmsg == "wanders in"
    assert bea.player.logoutmsg == "slips away"
    server.sent.clear()
    process_commands(server, bea, "set loginmsg")  # type: ignore[arg-type]
    process_commands(server, bea, "set logoutmsg")  # type: ignore[arg-type]
    cleared = load_saved(player_path(tmp_path, "Bea"))
    assert cleared is not None
    assert cleared.loginmsg is None
    assert cleared.logoutmsg is None
    assert "Login message cleared.\n" in server.sent
    assert "Logout message cleared.\n" in server.sent


def test_a_custom_loginmsg_is_the_room_line(tmp_path: Path) -> None:
    """The room hears the saved login sentence. Staff are not told."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), loginmsg="wanders in"))
    server = login_host(tmp_path)
    watcher = staff("Ada", Rank.SUPER_USER)
    server.people = [watcher]
    newcomer = FakePlayer(player=None, address="10.0.0.5")
    process_input(server, newcomer, "Bea")  # type: ignore[arg-type]
    process_input(server, newcomer, "pw")  # type: ignore[arg-type]
    assert "Bea wanders in.\n" in server.room_lines
    assert server.staff_lines == []


def test_a_newbie_who_reaches_a_room_alerts_staff(tmp_path: Path) -> None:
    """Staff hear the name, then the room the newbie entered."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    bea = FakePlayer(player=None, name=None, hostname="bea.example")
    process_input(server, bea, "Bea")  # type: ignore[arg-type]
    assert any("AWOOGA AWOOGA!!" in line and "Bea is connecting from bea.example" in line for line in server.staff_lines)
    process_input(server, bea, "yes")  # type: ignore[arg-type]
    assert bea.player is not None
    assert bea.player.rank == Rank.NEWBIE
    assert any("[ Newbie Alert ] Bea is in Reception." in line for line in server.staff_lines)
    assert all("< SU >" not in line for line in server.staff_lines)


def test_a_newbie_lands_in_newbie_start(tmp_path: Path) -> None:
    """A newbie enters the configured newbie room and staff hear its name."""
    server = login_host(tmp_path)
    server.layout = Layout(rooms={"reception": Room("The desk.", 4, {}), "waiting_room": Room("A row of chairs.", 4, {}),})  # fmt: skip  # noqa: E501
    server.config = replace(server.config, newbie_start="waiting_room")
    ada = staff("Ada", Rank.SUPER_USER)
    bea = FakePlayer(player=None, name=None)
    server.people = [ada, bea]
    process_input(server, bea, "Bea")  # type: ignore[arg-type]
    process_input(server, bea, "yes")  # type: ignore[arg-type]
    assert bea.player is not None
    assert bea.player.room == "waiting_room"
    assert any("[ Newbie Alert ] Bea is in Waiting Room." in line for line in server.staff_lines)


def test_a_full_newbie_room_drops_the_connection(tmp_path: Path) -> None:
    """A capped newbie room still uses the existing full line."""
    server = login_host(tmp_path)
    server.config = replace(server.config, newbie_start="waiting_room")
    server.layout = Layout(rooms={"reception": Room("The desk.", 4, {}), "waiting_room": Room("A row of chairs.", 1, {}),})  # fmt: skip  # noqa: E501
    server.occupied = 1
    ada = staff("Ada", Rank.SUPER_USER)
    bea = FakePlayer(player=None, name=None)
    server.people = [ada, bea]
    process_input(server, bea, "Bea")  # type: ignore[arg-type]
    process_input(server, bea, "yes")  # type: ignore[arg-type]
    assert "Waiting Room is full.\n" in bea.sent
    assert bea in server.dropped_clients
    assert bea.player is None
    assert all("is in Waiting Room" not in line for line in server.staff_lines)


def test_no_at_the_disclaimer_tells_staff(tmp_path: Path) -> None:
    """No at the disclaimer drops the newbie and tells staff."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    server.people = [ada]
    bea = FakePlayer(player=None, name=None)
    process_input(server, bea, "Bea")  # type: ignore[arg-type]
    process_input(server, bea, "no")  # type: ignore[arg-type]
    assert bea in server.dropped_clients
    assert bea.player is None
    assert any("[ Newbie Alert ] Bea quit at the disclaimer" in line for line in server.staff_lines)


def test_a_plan_sits_under_the_sentences_online_and_offline(tmp_path: Path) -> None:
    """Plan is saved like a description and sits under the sentences either way."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), plan="Out for lunch"))
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    process_commands(server, bea, "set plan Back later")  # type: ignore[arg-type]
    assert server.sent == ["Plan set.\n"]
    assert bea.player is not None
    assert bea.player.plan == "Back later"
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.plan == "Back later"
    ada = staff("Ada", Rank.RESIDENT)
    server.people = [bea, ada]
    server.sent.clear()
    process_commands(server, ada, "examine Bea")  # type: ignore[arg-type]
    lines = server.sent[0].splitlines()
    plan_index = next(i for i, line in enumerate(lines) if line.startswith("  Back later"))
    message_index = next(i for i, line in enumerate(lines) if "Logout message" in line)
    assert lines[plan_index - 1] == lines[0]
    assert lines[plan_index + 1] == lines[0]
    assert plan_index > message_index
    server.sent.clear()
    process_commands(server, bea, "set plan")  # type: ignore[arg-type]
    assert server.sent == ["Plan cleared.\n"]
    server.sent.clear()
    process_commands(server, bea, f"set plan {'x' * 81}")  # type: ignore[arg-type]
    assert server.sent == ["A plan is at most 80 characters.\n"]
    left = datetime.now(timezone.utc) - timedelta(hours=1)
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), logged_out_at=left, plan="Out for lunch", description="Hello"))
    server = login_host(tmp_path)
    process_commands(server, viewer(Rank.RESIDENT), "examine Bea")  # type: ignore[arg-type]
    lines = server.sent[0].splitlines()
    assert "  Hello" not in server.sent[0]
    plan_index = next(i for i, line in enumerate(lines) if line.startswith("  Out for lunch"))
    message_index = next(i for i, line in enumerate(lines) if "Logout message" in line)
    assert lines[plan_index - 1] == lines[0]
    assert lines[plan_index + 1] == lines[0]
    assert plan_index > message_index
    assert "plan" not in command_words(Rank.NEWBIE).split()
    assert "plan" not in subcommand_words(by_name()["set"], Rank.NEWBIE).split()
    assert "plan" in subcommand_words(by_name()["set"], Rank.RESIDENT).split()


def test_a_newbie_cannot_set_a_prompt() -> None:
    """A newbie is told prompt does not exist, and help hides it."""
    when = datetime.now(timezone.utc)
    newbie = FakePlayer(player=Player("Ada", "reception", when, rank=Rank.NEWBIE))
    server, _ = run("set prompt Go:", newbie)
    assert server.sent == ["No such command 'set'\n"]
    assert newbie.player is not None
    assert newbie.player.prompt == "What? > "

    book = HelpBook(entries=(HelpEntry("say", (), "Speak."), HelpEntry("set prompt", (), "Set it.")))
    helped = FakeServer(help=book)
    process_commands(helped, newbie, "help set prompt")  # type: ignore[arg-type]
    assert helped.sent == ["help for set prompt not found\n"]
    listed = FakeServer(help=book)
    process_commands(listed, newbie, "help")  # type: ignore[arg-type]
    assert listed.sent == ["Syntax: help <topic>\n"]

    assert "prompt" not in command_words(Rank.NEWBIE).split()
    set_children = subcommand_words(by_name()["set"], Rank.RESIDENT).split()
    assert "prompt" in set_children
    assert subcommand_words(by_name()["set"], Rank.SUPER_USER).split().count("prompt") == 1
    assert subcommand_words(by_name()["set"], Rank.ADMIN).split().count("prompt") == 1


def prompt_of(tmp_path: Path) -> str | None:
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    return saved.prompt


def test_a_resident_prompt_is_saved_and_loaded_at_login(tmp_path: Path) -> None:
    """The file keeps ``Go: `` and the next login of that player types at it."""
    seconds_saved(tmp_path, 0)
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    process_commands(server, bea, "set prompt Go:")  # type: ignore[arg-type]
    assert server.sent == ["Prompt set.\n"]
    assert prompt_of(tmp_path) == "Go: "

    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), prompt="Go: "))
    server = login_host(tmp_path)
    again = FakePlayer(player=None)
    process_input(server, again, "Bea")  # type: ignore[arg-type]
    process_input(server, again, "pw")  # type: ignore[arg-type]
    assert again.player is not None
    assert again.player.prompt == "Go: "
    assert server.sent[-1] == "Go: "


def test_a_file_without_a_prompt_keeps_the_default(tmp_path: Path) -> None:
    """No prompt key leaves ``What? > `` on the new session."""
    seconds_saved(tmp_path, 0)
    server = login_host(tmp_path)
    bea = FakePlayer(player=None)
    process_input(server, bea, "Bea")  # type: ignore[arg-type]
    process_input(server, bea, "pw")  # type: ignore[arg-type]
    assert bea.player is not None
    assert bea.player.prompt == "What? > "


def test_the_default_prompt_stores_no_key(tmp_path: Path) -> None:
    """Choosing the usual line removes a saved prompt."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), prompt="Go: "))
    server = login_host(tmp_path)
    bea = staff("Bea", Rank.RESIDENT)
    process_commands(server, bea, "set prompt What? >")  # type: ignore[arg-type]
    assert prompt_of(tmp_path) is None
    assert "prompt" not in (tmp_path / "bea.yml").read_text(encoding="utf-8")
    assert bea.player is not None
    assert bea.player.prompt == "What? > "
    assert server.sent == ["Prompt set.\n"]


def test_becoming_a_resident_stores_no_prompt(tmp_path: Path) -> None:
    """A newbie's prompt is never written when they are promoted."""
    bea = newbie("Bea")
    assert bea.player is not None
    ada = staff("Ada", Rank.SUPER_USER)
    server = login_host(tmp_path)
    server.people = [bea, ada]
    process_commands(server, ada, "resident Bea")  # type: ignore[arg-type]
    process_input(server, bea, "hunter2")  # type: ignore[arg-type]
    process_input(server, bea, "hunter2")  # type: ignore[arg-type]
    assert prompt_of(tmp_path) is None
    assert "prompt" not in (tmp_path / "bea.yml").read_text(encoding="utf-8")


def test_custom_prompt_ends_with_a_space() -> None:
    """A custom prompt gains one trailing space and keeps it."""
    user = Player("Ada", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    ada = FakePlayer(player=user)
    server, _ = run("set prompt Hi:", ada)
    assert user.prompt == "Hi: "
    assert server.sent[-1] == "Prompt set.\n"


def test_commands_wrap_on_visible_width() -> None:
    """Colour codes do not make the command list wrap early."""
    on_user = Player("Ada", "hall", datetime.now(timezone.utc), rank=Rank.ADMIN, colour=True)
    off_user = Player("Ada", "hall", datetime.now(timezone.utc), rank=Rank.ADMIN, colour=False)
    on, _ = run("commands", FakePlayer(player=on_user, frame_columns=60))
    off, _ = run("commands", FakePlayer(player=off_user, frame_columns=60))
    on_body = on.sent[0].split("Commands available:\n", 1)[1]
    off_body = off.sent[0].split("Commands available:\n", 1)[1]
    on_lines = on_body.splitlines()
    off_lines = off_body.splitlines()
    assert [plain_len(line) for line in on_lines] == [len(line) for line in off_lines]
    for line in on_lines:
        assert plain_len(line) <= 60


def test_player_prompt_adds_a_space_to_a_legacy_saved_value(tmp_path: Path) -> None:
    """A file that kept ``Hi:`` still types at ``Hi: ``."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), prompt="Hi:"))
    server = login_host(tmp_path)
    again = FakePlayer(player=None)
    process_input(server, again, "Bea")  # type: ignore[arg-type]
    process_input(server, again, "pw")  # type: ignore[arg-type]
    assert again.player is not None
    assert again.player.prompt == "Hi:"
    assert entry_api.player_prompt(again) == "Hi: "
    assert server.sent[-1] == "Hi: "


def test_examine_and_lsu_keep_the_same_line_count_with_colour(tmp_path: Path) -> None:
    """Colour codes do not add or remove frame lines."""
    server = login_host(tmp_path)
    ada = staff("Ada", Rank.SUPER_USER)
    assert ada.player is not None
    ada.player.idle_seconds = 0
    boss = staff("admin", Rank.ADMIN)
    assert boss.player is not None
    boss.player.idle_seconds = 3723
    server.people = [boss, ada]

    process_commands(server, ada, "examine me")  # type: ignore[arg-type]
    examine_on = server.sent[0]
    for line in examine_on.splitlines():
        assert plain_len(line) == 72
    ada.player.colour = False
    server.sent.clear()
    process_commands(server, ada, "examine me")  # type: ignore[arg-type]
    assert server.sent[0].count("\n") == examine_on.count("\n")

    ada.player.colour = True
    server.sent.clear()
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    lsu_on = server.sent[0]
    for line in lsu_on.splitlines():
        assert plain_len(line) == 72
    ada.player.colour = False
    server.sent.clear()
    process_commands(server, ada, "lsu")  # type: ignore[arg-type]
    assert server.sent[0].count("\n") == lsu_on.count("\n")


def test_coloured_frames_match_the_terminal_width(tmp_path: Path) -> None:
    """A red admin rank and a plain resident rank use the same rule length."""
    from lupaxa.chat_server.colour import plain_len

    server = login_host(tmp_path)
    resident = staff("Bea", Rank.RESIDENT)
    boss = staff("Ada", Rank.ADMIN)
    assert resident.player is not None
    assert boss.player is not None
    resident.player.colour = True
    boss.player.colour = True
    server.people = [resident, boss]

    process_commands(server, resident, "examine me")  # type: ignore[arg-type]
    resident_rules = [line for line in server.sent[0].splitlines() if set(line) == {"-"}]
    process_commands(server, boss, "examine me")  # type: ignore[arg-type]
    admin_text = server.sent[-1]
    admin_rules = [line for line in admin_text.splitlines() if set(line) == {"-"}]
    assert resident_rules[0] == "-" * 72
    assert admin_rules[0] == "-" * 72
    for line in admin_text.splitlines():
        assert plain_len(line) == 72

    server.sent.clear()
    server.newbies = [("Cyd", "10.0.0.8", "reception", 0)]
    process_commands(server, boss, "lsn")  # type: ignore[arg-type]
    process_commands(server, boss, "lsu")  # type: ignore[arg-type]
    lsn_rule = server.sent[0].splitlines()[0]
    lsu_rule = server.sent[1].splitlines()[0]
    assert lsn_rule == lsu_rule == "-" * 72
    assert plain_len(lsu_rule) == 72


def test_a_wide_plain_cell_grows_the_rule() -> None:
    """Plain text wider than the terminal widens the rule. Colour bytes do not."""
    from lupaxa.chat_server.colour import red
    from lupaxa.chat_server.utilities import list_frame

    wide = list_frame(("Name",), [("A" * 40,)], 10)
    assert wide.splitlines()[0] == "-" * 44
    coloured = list_frame(("Name",), [(red("Ada"),)], 72)
    assert coloured.splitlines()[0] == "-" * 72


def test_ten_seconds_rolls_port_and_connection_counters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One 10-second step rolls telnet, combined, and the connection."""
    server = login_host(tmp_path)
    server.timer_mark = 0.0
    server.timer_remainder = 0.0
    person = FakePlayer(player=Player("Ada", "hall", datetime.now(timezone.utc)))
    person.net.in_current = 25
    server.telnet_net.in_current = 25
    server.combined_net.in_current = 25
    server.people = [person]
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: 10.0)
    process_timer(server)  # type: ignore[arg-type]
    assert server.net_count == 10
    assert server.telnet_net.in_total == 25
    assert server.telnet_net.in_bps == 2
    assert server.telnet_net.in_average == 1
    assert server.telnet_net.in_current == 0
    assert person.net.in_total == 25
    assert person.net.in_current == 0


def test_a_short_window_is_still_divided_by_ten(tmp_path: Path) -> None:
    """Three bytes in a window that closes early have a rate of 0."""
    server = login_host(tmp_path)
    server.net_count = 1
    person = FakePlayer()
    person.net.in_current = 3
    server.people = [person]
    apply_net_tick(server, 1)  # type: ignore[arg-type]
    assert person.net.in_bps == 0
    assert person.net.in_total == 3


def test_a_fifteen_second_tick_rolls_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A late tick divides by 10 once and sets the countdown back to 10."""
    server = login_host(tmp_path)
    server.timer_mark = 0.0
    server.timer_remainder = 0.0
    server.telnet_net.in_current = 100
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: 15.0)
    process_timer(server)  # type: ignore[arg-type]
    assert server.net_count == 10
    assert server.telnet_net.in_bps == 10
    assert server.telnet_net.in_total == 100


def test_telnet_and_ssh_cannot_be_names() -> None:
    """Those words are reserved in any case, like me."""
    for typed in ("telnet", "SSH", " Telnet "):
        decision = consider_name(typed, set())
        assert decision.name is None
        assert decision.reply == (f"{typed.strip()} cannot be used. Pick another name.\n\nPlease enter your name: ")


def test_system_cannot_be_a_name() -> None:
    """System is reserved because that house file is the office rooms file."""
    for typed in ("system", "System", " SYSTEM "):
        decision = consider_name(typed, set())
        assert decision.name is None
        assert decision.reply == (f"{typed.strip()} cannot be used. Pick another name.\n\nPlease enter your name: ")


def test_command_parameters_can_still_be_names() -> None:
    """A command parameter is not reserved unless it shares a player argument."""
    parameters = {
        "newbies",
        "residents",
        "on",
        "off",
        "login",
        "force",
        "help",
        "rooms",
        "setup",
        "remove",
        "auto",
        "now",
        "cancel",
        "leave",
        "delete",
        "north",
        "south",
        "east",
        "west",
        "down",
        "yes",
    }
    assert parameters.isdisjoint(RESERVED_NAMES)
    assert consider_name("Help", set()).name == "Help"
    assert consider_name("Rooms", set()).name == "Rooms"
    assert consider_name("Off", set()).name == "Off"
    assert {"me", "who", "quit", "ssh", "telnet", "system"} <= RESERVED_NAMES


def test_a_house_file_name_cannot_be_the_rooms_or_help_file(tmp_path: Path) -> None:
    """Help stays a legal name, and is refused when that file is the help file."""
    server = login_host(tmp_path)
    server.config = replace(server.config, rooms=tmp_path / "office.yml", help=tmp_path / "help.yml")
    player = FakePlayer(player=None)
    process_input(server, player, "Help")  # type: ignore[arg-type]
    assert player.player is None
    assert server.sent == ["Help cannot be used. Pick another name.\n\nPlease enter your name: "]
    assert not (tmp_path / "help.yml").exists()


def test_a_resident_cannot_use_network(tmp_path: Path) -> None:
    """Below coder, network is not a command."""
    server = login_host(tmp_path)
    user = Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    player = FakePlayer(player=user)
    process_commands(server, player, "network")  # type: ignore[arg-type]
    assert player.sent[-1] == "No such command 'network'\n"


def test_network_with_no_argument_shows_the_combined_box(tmp_path: Path) -> None:
    """Bare network is the combined counter."""
    server = login_host(tmp_path)
    server.combined_net.in_total = 1500
    server.telnet_net.in_total = 9
    user = Player("Ann", "reception", datetime.now(timezone.utc), rank=Rank.CODER)
    player = FakePlayer(player=user)
    server.people = [player]
    process_commands(server, player, "network")  # type: ignore[arg-type]
    assert "1.5 KB" in player.sent[-1]
    assert "9 B" not in player.sent[-1]


def test_network_telnet_and_ssh_select_those_ports(tmp_path: Path) -> None:
    """The port words win in any case."""
    server = login_host(tmp_path)
    server.telnet_net.in_total = 1500
    server.ssh_net.out_total = 300_000_000
    user = Player("Ann", "reception", datetime.now(timezone.utc), rank=Rank.CODER)
    player = FakePlayer(player=user)
    server.people = [player]
    process_commands(server, player, "network TELNET")  # type: ignore[arg-type]
    assert "1.5 KB" in player.sent[-1]
    process_commands(server, player, "network ssh")  # type: ignore[arg-type]
    assert "300.0 MB" in player.sent[-1]


def test_network_me_shows_the_callers_connection(tmp_path: Path) -> None:
    """Network me is the caller's own counters, the way examine me is the caller."""
    server = login_host(tmp_path)
    user = Player("Ann", "reception", datetime.now(timezone.utc), rank=Rank.CODER)
    player = FakePlayer(player=user)
    player.net.in_total = 42
    server.people = [player]
    process_commands(server, player, "network me")  # type: ignore[arg-type]
    assert "42 B" in player.sent[-1]


def test_ipinfo_frames_a_player_or_an_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ipinfo shows the database record, and does not exist while lookup is off."""
    monkeypatch.setattr("lupaxa.chat_server.admin.lookup_hostname", lambda ip: "dns.google")
    server = login_host(tmp_path)
    admin = staff("Ann", Rank.ADMIN)
    server.people = [admin]
    process_commands(server, admin, "ipinfo 8.8.8.8")  # type: ignore[arg-type]
    assert server.sent[-1] == "No such command 'ipinfo'\n"
    assert "ipinfo" not in command_words(Rank.ADMIN).split()
    server.country_lookup = True
    sue = staff("Sue", Rank.SUPER_USER)
    server.people.append(sue)
    server.sent.clear()
    process_commands(server, sue, "ipinfo 8.8.8.8")  # type: ignore[arg-type]
    assert server.sent[-1] == "No such command 'ipinfo'\n"
    assert "ipinfo" in command_words(Rank.ADMIN, country_lookup=True).split()

    class Reader:
        def get_with_prefix_len(self, ip: str) -> tuple[dict[str, str], int]:
            assert ip == "8.8.8.8"
            return {"country": "United States", "country_code": "US", "continent": "North America", "continent_code": "NA", "asn": "AS15169", "as_name": "Google LLC", "as_domain": "google.com"}, 24

    server.ensure_country_reader = lambda: Reader()  # type: ignore[attr-defined]
    server.sent.clear()
    process_commands(server, admin, "ipinfo 8.8.8.8")  # type: ignore[arg-type]
    text = server.sent[-1]
    assert "8.8.8.8" in text
    assert "dns.google" in text
    assert "United States (US)" in text
    assert "North America (NA)" in text
    assert "AS15169" in text
    assert "Google LLC" in text
    domain_row = text.split("AS domain", 1)[1].split("\n", 1)[0]
    assert domain_row.strip(" :|") == "google.com"
    assert "8.8.8.0/24" in text
    server.sent.clear()
    process_commands(server, admin, "ipinfo me")  # type: ignore[arg-type]
    mine = server.sent[-1]
    assert "Ann" in mine
    assert "Localhost (XL)" in mine
    assert "127.0.0.0/8" in mine
    bea = staff("Bea", Rank.RESIDENT)
    bea.address = "192.168.1.9"
    bea.hostname = "bea.home"
    server.people.append(bea)
    server.sent.clear()
    process_commands(server, admin, "ipinfo Bea")  # type: ignore[arg-type]
    shown = server.sent[-1]
    assert "Bea" in shown
    assert "bea.home" in shown
    assert "Private Network (XP)" in shown
    assert "192.168.0.0/16" in shown
    assert "ASN" not in shown


def test_network_shows_one_connection_from_accept(tmp_path: Path) -> None:
    """The player's box is the connection counter, including login bytes."""
    server = login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    bea = FakePlayer(name="Bea", player=user, room="hall")
    bea.net.in_total = 999
    admin_user = Player("Ann", "reception", datetime.now(timezone.utc), rank=Rank.CODER)
    admin = FakePlayer(player=admin_user)
    server.people = [admin, bea]
    process_commands(server, admin, "network bea")  # type: ignore[arg-type]
    assert "999 B" in admin.sent[-1]


def test_network_reports_a_missing_player_and_extra_words(tmp_path: Path) -> None:
    """An unknown name and a second word are refused."""
    server = login_host(tmp_path)
    user = Player("Ann", "reception", datetime.now(timezone.utc), rank=Rank.CODER)
    player = FakePlayer(player=user)
    server.people = [player]
    process_commands(server, player, "network Ada")  # type: ignore[arg-type]
    assert player.sent[-1] == "Player Ada not found.\n"
    process_commands(server, player, "network telnet please")  # type: ignore[arg-type]
    assert player.sent[-1] == "Syntax: network [telnet|ssh|<player>]\n"


def test_a_saved_player_file_has_no_network_fields(tmp_path: Path) -> None:
    """Traffic counters are not written to the player file."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    text = player_path(tmp_path, "Bea").read_text(encoding="utf-8")
    assert "in_total" not in text
    assert "in_bps" not in text


class SampleProcess:
    """One fixed process snapshot."""

    pid = 18432

    def oneshot(self) -> object:
        return contextlib.nullcontext()

    def cpu_percent(self, interval: None = None) -> float:
        del interval
        return 2.4

    def cpu_times(self) -> object:
        return SimpleNamespace(user=182.4, system=34.1)

    def memory_info(self) -> object:
        return SimpleNamespace(rss=50960794, vms=432852173)

    def memory_full_info(self) -> object:
        return SimpleNamespace(uss=41104179)

    def num_threads(self) -> int:
        return 4

    def num_fds(self) -> int:
        return 87

    def net_connections(self, kind: str = "inet") -> list[None]:
        del kind
        return [None] * 42

    def io_counters(self) -> object:
        return SimpleNamespace(read_bytes=13002342, write_bytes=193147699)

    def num_ctx_switches(self) -> object:
        return SimpleNamespace(voluntary=124821, involuntary=2143)


def test_system_shows_uptime_cpu_memory_and_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """System shows the process box with uptime and resource figures."""
    fixed = datetime(2026, 10, 12, 13, 45, 3, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return fixed

    monkeypatch.setattr("lupaxa.chat_server.coder.datetime", Clock)
    ada = staff("Ada", Rank.CODER)
    assert ada.player is not None
    ada.player.colour = False
    server = FakeServer(people=[ada, FakePlayer(player=None), FakePlayer(player=None)])
    server.started_at = fixed - timedelta(hours=2, minutes=5)
    server.process = SampleProcess()
    process_commands(server, ada, "system")  # type: ignore[arg-type]
    narrow = """\
+---------------------------------------------------------------+
| PID                  : 18432                                  |
| Uptime               : 2 hours, 5 minutes                     |
| Booted at            : Monday 12th October 2026, 11:40:03 UTC |
| CPU usage            : 2.4%                                   |
| CPU user time        : 3 minutes, 2.4 seconds                 |
| CPU system time      : 34.1 seconds                           |
|---------------------------------------------------------------|
| Memory RSS           : 48.6 MiB                               |
| Memory USS           : 39.2 MiB                               |
| Memory VMS           : 412.8 MiB                              |
|---------------------------------------------------------------|
| Threads              : 4                                      |
| File descriptors     : 87                                     |
| Total sockets        : 42                                     |
| Sessions             : 3                                      |
|---------------------------------------------------------------|
| Disk read            : 12.4 MiB                               |
| Disk written         : 184.2 MiB                              |
|---------------------------------------------------------------|
| Voluntary switches   : 124821                                 |
| Involuntary switches : 2143                                   |
+---------------------------------------------------------------+
"""
    assert server.sent == [narrow]
    lines = narrow.splitlines()
    width = len(lines[0])
    assert all(len(line) == width for line in lines)
    server.rebooted_at = datetime(2026, 10, 12, 12, 10, 3, tzinfo=timezone.utc)
    server.sent.clear()
    process_commands(server, ada, "system")  # type: ignore[arg-type]
    wide = """\
+---------------------------------------------------------------+
| PID                  : 18432                                  |
| Uptime               : 2 hours, 5 minutes                     |
| Booted at            : Monday 12th October 2026, 11:40:03 UTC |
| Rebooted at          : Monday 12th October 2026, 12:10:03 UTC |
| CPU usage            : 2.4%                                   |
| CPU user time        : 3 minutes, 2.4 seconds                 |
| CPU system time      : 34.1 seconds                           |
|---------------------------------------------------------------|
| Memory RSS           : 48.6 MiB                               |
| Memory USS           : 39.2 MiB                               |
| Memory VMS           : 412.8 MiB                              |
|---------------------------------------------------------------|
| Threads              : 4                                      |
| File descriptors     : 87                                     |
| Total sockets        : 42                                     |
| Sessions             : 3                                      |
|---------------------------------------------------------------|
| Disk read            : 12.4 MiB                               |
| Disk written         : 184.2 MiB                              |
|---------------------------------------------------------------|
| Voluntary switches   : 124821                                 |
| Involuntary switches : 2143                                   |
+---------------------------------------------------------------+
"""
    assert server.sent == [wide]
    wide_lines = wide.splitlines()
    assert all(len(line) == len(wide_lines[0]) for line in wide_lines)
    assert len(wide_lines[0]) == width


def test_system_shows_unavailable_for_denied_process_stats() -> None:
    """AccessDenied on some counters still shows the rest of the box."""

    class DeniedProcess(SampleProcess):
        def memory_full_info(self) -> object:
            raise psutil.AccessDenied(pid=18432)

        def io_counters(self) -> object:
            raise psutil.AccessDenied(pid=18432)

    ada = staff("Ada", Rank.CODER)
    server = FakeServer(people=[ada])
    server.process = DeniedProcess()
    process_commands(server, ada, "system")  # type: ignore[arg-type]
    body = server.sent[0]
    assert "Memory USS           : unavailable" in body
    assert "Disk read            : unavailable" in body
    assert "Disk written         : unavailable" in body
    assert "Memory RSS           : 48.6 MiB" in body


def test_system_box_survives_a_missing_disk_counter() -> None:
    """AttributeError on io_counters still shows the rest of the box."""

    class NoDiskIoProcess(SampleProcess):
        @property
        def io_counters(self) -> object:
            raise AttributeError("io_counters")

    ada = staff("Ada", Rank.CODER)
    server = FakeServer(people=[ada])
    server.process = NoDiskIoProcess()
    process_commands(server, ada, "system")  # type: ignore[arg-type]
    body = server.sent[0]
    assert "Disk read            : unavailable" in body
    assert "Disk written         : unavailable" in body
    assert "Memory RSS           : 48.6 MiB" in body


def test_system_box_survives_a_real_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """System against the live process returns a box without raising."""
    fixed = datetime(2026, 10, 12, 13, 45, 3, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return fixed

    monkeypatch.setattr("lupaxa.chat_server.coder.datetime", Clock)
    ada = staff("Ada", Rank.CODER)
    server = FakeServer(people=[ada])
    server.started_at = fixed - timedelta(hours=2, minutes=5)
    server.process = psutil.Process()
    process_commands(server, ada, "system")  # type: ignore[arg-type]
    body = server.sent[0]
    assert body.startswith("+")
    assert "Disk read" in body
    assert "Memory RSS" in body


def test_system_rejects_an_extra_word() -> None:
    """System with a word asks for the bare command."""
    ada = staff("Ada", Rank.CODER)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "system please")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: system\n"]


def test_a_super_user_cannot_use_system() -> None:
    """System is a coder command."""
    ada = staff("Ada", Rank.SUPER_USER)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "system")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'system'\n"]


def test_an_off_duty_coder_can_use_system() -> None:
    """A coder command still runs while off duty. An admin is told it does not exist."""
    ada = staff("Ada", Rank.CODER)
    assert ada.player is not None
    ada.player.off_duty = True
    server = FakeServer(people=[ada])
    server.process = SampleProcess()
    process_commands(server, ada, "system")  # type: ignore[arg-type]
    assert server.sent[0] != "You are off duty.\n"
    assert "No such command 'system'\n" not in server.sent
    boss = staff("Bea", Rank.ADMIN)
    assert boss.player is not None
    boss.player.off_duty = True
    refused = FakeServer(people=[boss])
    process_commands(refused, boss, "system")  # type: ignore[arg-type]
    assert refused.sent == ["No such command 'system'\n"]


def test_appoint_dismiss_and_roles_use_syntax_or_list_roles(tmp_path: Path) -> None:
    """Bare dismiss lists roles; short lines and extra words get syntax help."""
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [ada]
    process_commands(server, ada, "dismiss")  # type: ignore[arg-type]
    assert server.sent == ["Roles:\n  site_admin\n"]
    server.sent.clear()
    process_commands(server, ada, "appoint Bea")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: appoint <name> <role>\n"]
    server.sent.clear()
    process_commands(server, ada, "dismiss Bea")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: dismiss <name> <role>\n"]
    server.sent.clear()
    process_commands(server, ada, "roles Ada extra")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: roles [<name>]\n"]


def test_appoint_and_dismiss_write_the_flag_and_tell_staff(tmp_path: Path) -> None:
    """Appoint and dismiss update the file and the live player, and tell the staff channel."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    ada = staff("Ada", Rank.ADMIN)
    bea = staff("Bea", Rank.RESIDENT)
    server = login_host(tmp_path)
    server.people = [ada, bea]
    process_commands(server, ada, "appoint")  # type: ignore[arg-type]
    assert server.sent == ["Roles:\n  site_admin\n"]
    server.sent.clear()
    process_commands(server, ada, "appoint Bea site_admin")  # type: ignore[arg-type]
    stored = load_saved(player_path(tmp_path, "Bea"))
    assert stored is not None
    assert stored.roles == int(Roles.SITE_ADMIN)
    assert bea.player is not None
    assert bea.player.roles == int(Roles.SITE_ADMIN)
    assign = "< SU > Ada assigns Bea to the role of 'Site Admin'\n"
    assert server.admin_lines == []
    assert server.staff_lines == [paint_staff(ada, assign)]
    assert server.sent == [paint_staff(bea, assign)]
    assert bea.sent == [paint_staff(bea, assign)]
    server.staff_lines.clear()
    server.sent.clear()
    process_commands(server, ada, "appoint Bea site_admin")  # type: ignore[arg-type]
    assert server.sent == ["Bea already has the Site Admin role.\n"]
    server.sent.clear()
    bea.sent.clear()
    process_commands(server, ada, "dismiss Bea site_admin")  # type: ignore[arg-type]
    stored = load_saved(player_path(tmp_path, "Bea"))
    assert stored is not None
    assert stored.roles == 0
    assert bea.player.roles == 0
    dismiss = "< SU > Ada dismisses Bea from the role of 'Site Admin'.\n"
    assert server.admin_lines == []
    assert server.staff_lines == [paint_staff(ada, dismiss)]
    assert server.sent == [paint_staff(bea, dismiss)]


def test_an_on_duty_super_user_hears_a_role_change_once(tmp_path: Path) -> None:
    """An on-duty super user gets the staff line and not a second copy."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    ada = staff("Ada", Rank.ADMIN)
    bea = staff("Bea", Rank.SUPER_USER)
    server = login_host(tmp_path)
    server.people = [ada, bea]
    process_commands(server, ada, "appoint Bea site_admin")  # type: ignore[arg-type]
    line = "< SU > Ada assigns Bea to the role of 'Site Admin'\n"
    assert server.staff_lines == [paint_staff(ada, line), paint_staff(bea, line)]
    assert bea.sent == [paint_staff(bea, line)]
    assert server.sent == []


def test_an_off_duty_super_user_is_told_a_role_change_directly(tmp_path: Path) -> None:
    """Off duty skips the staff channel, so the player is told directly."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    ada = staff("Ada", Rank.ADMIN)
    bea = staff("Bea", Rank.SUPER_USER)
    assert bea.player is not None
    bea.player.off_duty = True
    server = login_host(tmp_path)
    server.people = [ada, bea]
    process_commands(server, ada, "appoint Bea site_admin")  # type: ignore[arg-type]
    line = "< SU > Ada assigns Bea to the role of 'Site Admin'\n"
    assert server.staff_lines == [paint_staff(ada, line)]
    assert server.sent == [paint_staff(bea, line)]
    assert bea.sent == [paint_staff(bea, line)]


def test_a_locked_screen_swallows_the_direct_role_line(tmp_path: Path) -> None:
    """A locked screen does not show the direct sentence. The staff channel still hears it."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    ada = staff("Ada", Rank.ADMIN)
    bea = staff("Bea", Rank.RESIDENT)
    assert bea.player is not None
    bea.player.screen_locked = True
    server = login_host(tmp_path)
    server.people = [ada, bea]
    process_commands(server, ada, "appoint Bea site_admin")  # type: ignore[arg-type]
    line = "< SU > Ada assigns Bea to the role of 'Site Admin'\n"
    assert server.staff_lines == [paint_staff(ada, line)]
    assert server.sent == []
    assert bea.sent == []


def test_a_logged_out_player_is_not_sent_the_role_line(tmp_path: Path) -> None:
    """A missing connection still announces on the staff channel."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [ada]
    process_commands(server, ada, "dismiss Bea site_admin")  # type: ignore[arg-type]
    assert server.sent == ["Bea does not have the Site Admin role.\n"]
    server.sent.clear()
    process_commands(server, ada, "appoint Bea site_admin")  # type: ignore[arg-type]
    line = "< SU > Ada assigns Bea to the role of 'Site Admin'\n"
    assert server.staff_lines == [paint_staff(ada, line)]
    assert server.sent == []


def test_an_on_duty_admin_who_appoints_themselves_hears_the_staff_line_once(tmp_path: Path) -> None:
    """Changing your own role while on duty does not add a direct copy."""
    save_player(tmp_path, SavedPlayer("Ada", Rank.ADMIN, hash_password("pw", rounds=4)))
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [ada]
    process_commands(server, ada, "appoint Ada site_admin")  # type: ignore[arg-type]
    line = "< SU > Ada assigns Ada to the role of 'Site Admin'\n"
    assert server.staff_lines == [paint_staff(ada, line)]
    assert ada.sent == [paint_staff(ada, line)]
    assert server.sent == []


def test_appoint_refuses_another_super_admin_and_allows_yourself(tmp_path: Path) -> None:
    """The caller may take a role. A different super admin may not be changed."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(tmp_path, SavedPlayer("Ada", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    ada = staff("Ada", Rank.SUPER_ADMIN)
    server = login_host(tmp_path)
    server.people = [ada]
    process_commands(server, ada, "appoint Bea site_admin")  # type: ignore[arg-type]
    assert server.sent == ["Roles cannot be changed for the super admin.\n"]
    server.sent.clear()
    process_commands(server, ada, "appoint Ada site_admin")  # type: ignore[arg-type]
    stored = load_saved(player_path(tmp_path, "Ada"))
    assert stored is not None
    assert stored.roles == int(Roles.SITE_ADMIN)
    server.sent.clear()
    ada.name = "ADA"
    process_commands(server, ada, "appoint ada site_admin")  # type: ignore[arg-type]
    assert server.sent == ["Ada already has the Site Admin role.\n"]


def test_appoint_names_the_unknown_role_and_the_unknown_player(tmp_path: Path) -> None:
    """A bad role and a missing player use the spec sentences."""
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [ada]
    process_commands(server, ada, "appoint Bea widget")  # type: ignore[arg-type]
    assert server.sent == ["There is no role 'widget'.\n"]
    server.sent.clear()
    process_commands(server, ada, "appoint Bea site_admin")  # type: ignore[arg-type]
    assert server.sent == ["No such player 'Bea'.\n"]
    server.sent.clear()
    process_commands(server, ada, "dismiss Bea site_admin")  # type: ignore[arg-type]
    assert server.sent == ["No such player 'Bea'.\n"]


def test_appoint_refuses_the_spaced_name_and_the_label(tmp_path: Path) -> None:
    """The display words are not a role token."""
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [ada]
    process_commands(server, ada, "appoint Bea site admin")  # type: ignore[arg-type]
    assert server.sent == ["There is no role 'site admin'.\n"]
    server.sent.clear()
    process_commands(server, ada, "appoint Bea Site Admin")  # type: ignore[arg-type]
    assert server.sent == ["There is no role 'Site Admin'.\n"]


def test_roles_lists_your_own_and_an_admin_may_look_up_a_name(tmp_path: Path) -> None:
    """Residents see their own roles. Only an admin may name someone else."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), roles=int(Roles.SITE_ADMIN)))
    ada = staff("Ada", Rank.ADMIN)
    bea = staff("Bea", Rank.RESIDENT)
    assert bea.player is not None
    bea.player.roles = int(Roles.SITE_ADMIN)
    server = login_host(tmp_path)
    server.people = [ada, bea]
    process_commands(server, bea, "roles")  # type: ignore[arg-type]
    assert server.sent == ["Roles:\n  Site Admin\n"]
    server.sent.clear()
    process_commands(server, ada, "roles")  # type: ignore[arg-type]
    assert server.sent == ["You have no roles.\n"]
    server.sent.clear()
    process_commands(server, bea, "roles Ada")  # type: ignore[arg-type]
    assert server.sent == ["Only an admin can look up someone else's roles.\n"]
    server.sent.clear()
    process_commands(server, ada, "roles Bea")  # type: ignore[arg-type]
    assert server.sent == ["Bea:\n  Site Admin\n"]
    server.sent.clear()
    process_commands(server, ada, "dismiss Bea site_admin")  # type: ignore[arg-type]
    server.sent.clear()
    process_commands(server, ada, "roles Bea")  # type: ignore[arg-type]
    assert server.sent == ["Bea has no roles.\n"]


def test_a_newbie_has_no_roles_command() -> None:
    """Roles is resident and above."""
    newbie_player, _ = run("roles")
    assert newbie_player.sent == ["No such command 'roles'\n"]


def test_appoint_writes_a_logged_out_file(tmp_path: Path) -> None:
    """Appoint updates a saved player who is not connected."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [ada]
    process_commands(server, ada, "appoint Bea site_admin")  # type: ignore[arg-type]
    stored = load_saved(player_path(tmp_path, "Bea"))
    assert stored is not None
    assert stored.roles == int(Roles.SITE_ADMIN)
    assert server.sent == []


def test_dismiss_refuses_a_role_that_is_not_held(tmp_path: Path) -> None:
    """Dismiss of a role that is not held names the player."""
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    ada = staff("Ada", Rank.ADMIN)
    server = login_host(tmp_path)
    server.people = [ada]
    process_commands(server, ada, "dismiss Bea site_admin")  # type: ignore[arg-type]
    assert server.sent == ["Bea does not have the Site Admin role.\n"]


def test_appoint_does_not_write_a_role_without_a_file(tmp_path: Path) -> None:
    """A missing file is No such player, and the live player is left unchanged."""
    ada = staff("Ada", Rank.ADMIN)
    bea = staff("Bea", Rank.RESIDENT)
    assert bea.player is not None
    server = login_host(tmp_path)
    server.people = [ada, bea]
    process_commands(server, ada, "appoint Bea site_admin")  # type: ignore[arg-type]
    assert server.sent == ["No such player 'Bea'.\n"]
    assert bea.player.roles == 0
    assert load_saved(player_path(tmp_path, "Bea")) is None


def test_a_resident_site_admin_cannot_appoint_or_dismiss(tmp_path: Path) -> None:
    """A resident with the site admin role cannot appoint or dismiss."""
    bea = staff("Bea", Rank.RESIDENT)
    assert bea.player is not None
    bea.player.roles = int(Roles.SITE_ADMIN)
    server = login_host(tmp_path)
    server.people = [bea]
    process_commands(server, bea, "appoint Bea site_admin")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'appoint'\n"]
    server.sent.clear()
    process_commands(server, bea, "dismiss Bea site_admin")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'dismiss'\n"]
