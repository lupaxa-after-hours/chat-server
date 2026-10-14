"""Protocol text with no sockets."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest
from totp_fixtures import TOTP_TEST_SEED

import lupaxa.chat_server.admin as admin_api
import lupaxa.chat_server.commands as command_api
import lupaxa.chat_server.entry as entry_api
import lupaxa.chat_server.login as login_api
import lupaxa.chat_server.mfa as mfa_api
import lupaxa.chat_server.password as password_api
import lupaxa.chat_server.su as su_api
import lupaxa.chat_server.timer as timer_api
from lupaxa.chat_server import channel as channel_api
from lupaxa.chat_server.admin import command_allow, command_demote, command_promote
from lupaxa.chat_server.channel import (
    connections,
    match_name,
    multiple_matches_message,
    paint_admin,
    paint_staff,
)
from lupaxa.chat_server.colour import cyan, green, plain_len, red, yellow
from lupaxa.chat_server.commands import command_remote, command_save, command_tell
from lupaxa.chat_server.commands_list import Command, command_words, wrap_words
from lupaxa.chat_server.config import Config
from lupaxa.chat_server.entry import name_taken_reply
from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.globals import NAME_PROMPT
from lupaxa.chat_server.help import (
    HelpBook,
    HelpEntry,
    HelpStamp,
    help_stamp,
    load_help,
)
from lupaxa.chat_server.login import (
    admin_gate_reply,
    name_prompt,
    process_input,
    process_login,
    welcome_text,
)
from lupaxa.chat_server.net import NetStats
from lupaxa.chat_server.parse import consider_name, parse_direction, process_commands
from lupaxa.chat_server.password import confirm_password_prompt
from lupaxa.chat_server.player import Player
from lupaxa.chat_server.rooms import Layout, Room
from lupaxa.chat_server.saved_player import (
    RANK_ADMIN,
    RANK_NEWBIE,
    RANK_RESIDENT,
    RANK_SUPER,
    RANK_SUPER_ADMIN,
    SavedPlayer,
    delete_player,
    hash_password,
    load_saved,
    password_ok,
    player_path,
    rank_allows,
    record_saved_logout,
    save_player,
)
from lupaxa.chat_server.su import (
    command_disconnect,
    command_nuke,
    command_resident,
    command_warn,
)
from lupaxa.chat_server.time import clock_phrase, duration_phrase, idle_clock
from lupaxa.chat_server.timer import (
    apply_net_tick,
    consume_seconds,
    note_connected_time,
    process_timer,
)
from lupaxa.chat_server.utilities import (
    center_block,
    examine_frame,
    frame_width,
    list_frame,
    shown_rank,
    time_frame,
)
from lupaxa.chat_server.version import get_version

_SHIPPED_HELP = load_help(Path(__file__).resolve().parents[1] / "config" / "help.yml")


class _ClientMap:
    """People on the host, so a scan can find that host from ``values()``."""

    def __init__(self, host: _Host) -> None:
        self.host = host

    def values(self) -> _ClientMap:
        """Return this map. Iteration yields the host's people."""
        return self

    def __iter__(self) -> Iterator[_Player]:
        return iter(self.host.people)


@dataclass
class _Player:
    name: str | None = "Ada"
    room: str | None = "reception"
    player: Player | None = field(
        default_factory=lambda: Player("Ada", "reception", datetime.now(timezone.utc))
    )
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
    ssh: bool = False
    accepted_at: float = 0.0
    discovered_columns: int | None = 72
    discovered_rows: int | None = 24
    frame_columns: int | None = None
    sent: list[str] = field(default_factory=list)
    net: NetStats = field(default_factory=NetStats)

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
class _Host:
    sent: list[str] = field(default_factory=list)
    room_lines: list[str] = field(default_factory=list)
    replaced: list[_Player] = field(default_factory=list)
    dropped: bool = False
    stopped: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _timer_mark: float | None = None
    _timer_remainder: float = 0.0
    _net_count: int = 10
    _telnet_net: NetStats = field(default_factory=NetStats)
    _ssh_net: NetStats = field(default_factory=NetStats)
    _combined_net: NetStats = field(default_factory=NetStats)
    _shutdown_seconds: int | None = None
    _shutdown_reason: str = ""
    _allow_newbies: bool = True
    _allow_residents: bool = True
    _mfa_enabled: bool = False
    _mfa_steps: dict[str, int] = field(default_factory=dict)
    dropped_clients: list[_Player] = field(default_factory=list)
    announced: list[bool] = field(default_factory=list)
    staff_lines: list[str] = field(default_factory=list)
    admin_lines: list[str] = field(default_factory=list)
    occupied: int = 0
    people: list[_Player] = field(default_factory=list)
    all_lines: list[str] = field(default_factory=list)
    newbies: list[tuple[str, str, str, int]] = field(default_factory=list)
    _layout: Layout = field(
        default_factory=lambda: Layout(
            rooms={
                "reception": Room("The desk.", 4, {"nw": "hall"}),
                "hall": Room("A hall.", 4, {}),
            }
        )
    )
    _config: Config = field(
        default_factory=lambda: Config(
            port=1,
            rooms=Path("rooms.yml"),
            start="reception",
            help=Path("help.yml"),
            players=Path("players"),
            mfa_enabled=False,
        )
    )
    _help: HelpBook = field(default_factory=lambda: HelpBook(entries=()))
    _help_stamp: HelpStamp = field(default_factory=lambda: HelpStamp(-1, -1))
    _help_failed: HelpStamp | None = None
    _help_check_seconds: int = 0
    _roster_dir: Path = Path("players")
    _client_map: dict[str, _Player] | None = None

    @property
    def _clients(self) -> dict[str, _Player]:
        if self._client_map is None:
            return _ClientMap(self)  # type: ignore[return-value]
        return self._client_map

    @_clients.setter
    def _clients(self, value: dict[str, _Player]) -> None:
        self._client_map = value

    def send(self, client: _Player, text: str) -> bool:
        self.sent.append(text)
        client.sent.append(text)
        return True

    def _broadcast_room(self, room_id: str, text: str, *, skip: _Player | None) -> None:
        del room_id, skip
        self.room_lines.append(text)

    def _broadcast_room_split(
        self, room_id: str, plain: str, staff: str, *, skip: _Player | None
    ) -> None:
        del room_id, staff, skip
        self.room_lines.append(plain)

    def drop(self, client: _Player, *, announce: bool) -> None:
        self.dropped = True
        self.dropped_clients.append(client)
        self.announced.append(announce)

    def _broadcast_staff(self, text: str, *, skip: _Player | None = None) -> None:
        del skip
        self.staff_lines.append(text)

    def _broadcast_admin(self, text: str, *, skip: _Player | None = None) -> None:
        del skip
        self.admin_lines.append(text)

    def _occupancy(self, room_id: str) -> int:
        del room_id
        return self.occupied

    def _broadcast_all(self, text: str, *, skip: _Player | None) -> None:
        del skip
        self.all_lines.append(text)

    def shutdown(self) -> None:
        self.stopped = True

    def is_stopping(self) -> bool:
        return self.stopped

    def _player_named(self, name: str) -> _Player | None:
        key = name.lower()
        for person in self.people:
            player = person.player
            if player is None or person.room is None:
                continue
            if player.lowercase_name == key:
                return person
        return None

    def _idlers(self) -> list[tuple[str, int]]:
        rows = [
            (person.player.name, person.player.idle_seconds)
            for person in self.people
            if person.player is not None
        ]
        return sorted(rows)

    def _newbies(self) -> list[tuple[str, str, str, int]]:
        return list(self.newbies)

    def _who(self) -> list[tuple[str, str, str, str, int]]:
        rows = [
            (
                person.name,
                person.player.rank,
                person.room,
                person.player.status,
                person.player.idle_seconds,
            )
            for person in self.people
            if person.name is not None and person.room is not None and person.player is not None
        ]
        return sorted(rows)

    def _others(self, room_id: str, client: _Player) -> list[str]:
        del room_id, client
        return []

    def _taken_names(self) -> set[str]:
        return {
            person.name
            for person in self.people
            if person.name is not None and person not in self.dropped_clients
        }

    def _unsaved_names(self, skip: _Player | None = None) -> set[str]:
        claimed: set[str] = set()
        for person in self.people:
            if person is skip or person in self.dropped_clients:
                continue
            user = person.player
            if user is not None and user.rank == RANK_NEWBIE:
                claimed.add(user.name.lower())
            elif (
                user is None and person.auth in ("set", "confirm", "agree") and person.pending_name
            ):
                claimed.add(person.pending_name.lower())
        return claimed

    def _replace_socket(self, live: _Player, newcomer: _Player) -> None:
        self.replaced.append(live)
        live.address = newcomer.address
        live.hostname = newcomer.hostname


def _host_from_clients(clients: object) -> _Host | None:
    host = getattr(clients, "host", None)
    if isinstance(host, _Host):
        return host
    return None


@pytest.fixture(autouse=True)
def _route_scans_to_the_host_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record scans on ``_Host`` and restore the channel functions after each test."""

    def broadcast_room(server: object, room_id: str | None, text: str, *, skip: object) -> None:
        if isinstance(server, _Host):
            server._broadcast_room(room_id, text, skip=skip)  # type: ignore[arg-type]
            return
        channel_api.broadcast_room(server, room_id, text, skip=skip)  # type: ignore[arg-type]

    def broadcast_room_split(
        server: object,
        room_id: str | None,
        plain: str,
        staff: str,
        *,
        skip: object,
    ) -> None:
        if isinstance(server, _Host):
            server._broadcast_room_split(room_id, plain, staff, skip=skip)  # type: ignore[arg-type]
            return
        channel_api.broadcast_room_split(
            server,  # type: ignore[arg-type]
            room_id,
            plain,
            staff,
            skip=skip,  # type: ignore[arg-type]
        )

    def broadcast_all(server: object, text: str, *, skip: object) -> None:
        if isinstance(server, _Host):
            server._broadcast_all(text, skip=skip)  # type: ignore[arg-type]
            return
        channel_api.broadcast_all(server, text, skip=skip)  # type: ignore[arg-type]

    def broadcast_shout(server: object, line: str, *, skip: object) -> None:
        if isinstance(server, _Host):
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
            server._broadcast_all(painted, skip=skip)  # type: ignore[arg-type]
            return
        channel_api.broadcast_shout(server, line, skip=skip)  # type: ignore[arg-type]

    def broadcast_staff(server: object, text: str, *, skip: object = None) -> None:
        if isinstance(server, _Host):
            for person in server.people:
                user = person.player
                if user is None or not rank_allows(user.rank, RANK_SUPER):
                    continue
                if skip is not None and person is skip:
                    continue
                server._broadcast_staff(paint_staff(person, text), skip=skip)  # type: ignore[arg-type]
            return
        channel_api.broadcast_staff(server, text, skip=skip)  # type: ignore[arg-type]

    def broadcast_admin(server: object, text: str, *, skip: object = None) -> None:
        if isinstance(server, _Host):
            for person in server.people:
                user = person.player
                if user is None or not rank_allows(user.rank, RANK_ADMIN):
                    continue
                if skip is not None and person is skip:
                    continue
                server._broadcast_admin(paint_admin(person, text), skip=skip)  # type: ignore[attr-defined]
            return
        channel_api.broadcast_admin(server, text, skip=skip)  # type: ignore[arg-type]

    def occupancy(clients: object, room_id: str) -> int:
        host = _host_from_clients(clients)
        if host is not None:
            return host._occupancy(room_id)
        return channel_api.occupancy(clients, room_id)  # type: ignore[arg-type]

    def others(clients: object, room_id: str, skip: object) -> list[str]:
        host = _host_from_clients(clients)
        if host is not None:
            return host._others(room_id, skip)  # type: ignore[arg-type]
        return channel_api.others(clients, room_id, skip)  # type: ignore[arg-type]

    def who(clients: object) -> list[tuple[str, str, str, str, int]]:
        host = _host_from_clients(clients)
        if host is not None:
            return host._who()
        return channel_api.who(clients)  # type: ignore[arg-type]

    def idlers(clients: object) -> list[tuple[str, int]]:
        host = _host_from_clients(clients)
        if host is not None:
            return host._idlers()
        return channel_api.idlers(clients)  # type: ignore[arg-type]

    def newbies(clients: object) -> list[tuple[str, str, str, int]]:
        host = _host_from_clients(clients)
        if host is not None:
            return host._newbies()
        return channel_api.newbies(clients)  # type: ignore[arg-type]

    def player_named(clients: object, name: str) -> object:
        host = _host_from_clients(clients)
        if host is not None:
            return host._player_named(name)
        return channel_api.player_named(clients, name)  # type: ignore[arg-type]

    replacements = {
        "broadcast_room": broadcast_room,
        "broadcast_room_split": broadcast_room_split,
        "broadcast_all": broadcast_all,
        "broadcast_shout": broadcast_shout,
        "broadcast_staff": broadcast_staff,
        "broadcast_admin": broadcast_admin,
        "occupancy": occupancy,
        "others": others,
        "who": who,
        "idlers": idlers,
        "newbies": newbies,
        "player_named": player_named,
    }
    for module in (command_api, admin_api, su_api, login_api, password_api, entry_api, mfa_api):
        for name, func in replacements.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, func)
    monkeypatch.setattr(timer_api, "broadcast_all", broadcast_all)
    monkeypatch.setattr(timer_api, "broadcast_admin", broadcast_admin)


def _run(line: str, player: _Player | None = None) -> tuple[_Host, _Player]:
    current = player or _Player()
    host = _Host(people=[current], _help=_SHIPPED_HELP)
    process_commands(host, current, line)  # type: ignore[arg-type]
    return host, current


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
        assert decision.reply == (
            f"{typed.strip()} cannot be used. Pick another name.\n\n{name_prompt()}"
        )

    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "ME")  # type: ignore[arg-type]
    assert player.player is None
    assert host.sent == ["ME cannot be used. Pick another name.\n\nPlease enter your name: "]
    assert load_saved(player_path(tmp_path, "me")) is None
    assert not (tmp_path / "me.yml").exists()


def test_who_and_quit_cannot_be_names() -> None:
    """Who and quit are reserved in any case, like me."""
    for typed in ("who", "Who", "WHO", "quit", "Quit", "QUIT"):
        decision = consider_name(typed, set())
        assert decision.name is None
        assert decision.reply == (
            f"{typed} cannot be used. Pick another name.\n\nPlease enter your name: "
        )


def test_who_at_the_name_prompt_lists_players(tmp_path: Path) -> None:
    """Who shows the connected frame and asks for a name again."""
    host = _login_host(tmp_path)
    when = datetime.now(timezone.utc)
    host.people = [
        _Player(
            name="Ada",
            room="reception",
            player=Player("Ada", "reception", when, idle_seconds=10),
        )
    ]
    asker = _Player(player=None)
    process_input(host, asker, "WHO")  # type: ignore[arg-type]
    assert asker.player is None
    assert asker.auth == "name"
    assert host.sent[0].splitlines()[0] == "-" * 72
    assert "Ada" in host.sent[0]
    assert host.sent[-1] == "Please enter your name: "


def test_quit_at_the_name_prompt_disconnects(tmp_path: Path) -> None:
    """Quit says goodbye and drops the connection."""
    host = _login_host(tmp_path)
    asker = _Player(player=None)
    process_input(host, asker, "Quit")  # type: ignore[arg-type]
    assert host.sent == ["Goodbye!\n"]
    assert host.dropped_clients == [asker]
    assert asker.player is None


def test_who_please_is_not_the_login_list(tmp_path: Path) -> None:
    """A space keeps the letters-and-digits refusal."""
    host = _login_host(tmp_path)
    asker = _Player(player=None)
    process_input(host, asker, "who please")  # type: ignore[arg-type]
    assert asker.player is None
    assert host.sent == ["A name can use only letters and digits.\n\nPlease enter your name: "]
    assert host.dropped_clients == []


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
    assert decision.reply == (
        "Ada is already in use. Pick another name.\n\nPlease enter your name: "
    )


def test_say_emote_and_think() -> None:
    """Room lines and the sender's copy use the spec wording."""
    say, _player = _run("say hello there")
    assert say.sent == ["You say 'hello there'\n"]
    assert say.room_lines == ["- Ada says 'hello there'\n"]

    emote, _player = _run(":waves")
    assert emote.sent == ["You emote: waves\n"]
    assert emote.room_lines == ["- Ada waves\n"]

    think, _player = _run("~ lunch")
    assert think.sent == ["You think . o O ( lunch )\n"]
    assert think.room_lines == ["- Ada thinks . o O ( lunch )\n"]


def test_alias_does_not_need_a_space() -> None:
    """An alias is a command even when the text follows immediately."""
    host, _player = _run("'hello there")
    assert host.sent == ["You say 'hello there'\n"]
    assert host.room_lines == ["- Ada says 'hello there'\n"]


def test_missing_message_uses_the_canonical_name() -> None:
    """A command with syntax, and no text, shows that syntax."""
    host, _player = _run("say")
    assert host.sent == ["Syntax: say <message>\n"]
    assert host.room_lines == []

    host, _player = _run("'")
    assert host.sent == ["Syntax: say <message>\n"]
    assert host.dropped is False

    host, _player = _run("emote")
    assert host.sent == ["Syntax: emote <message>\n"]

    host, _player = _run("think")
    assert host.sent == ["Syntax: think <message>\n"]


def test_unknown_command() -> None:
    """An unknown word is quoted back to the sender."""
    host, _player = _run("dance")
    assert host.sent == ["No such command 'dance'\n"]
    assert host.room_lines == []


def test_glued_command_name_is_unknown() -> None:
    """A command name still needs a space before its text."""
    host, _player = _run("sayhello")
    assert host.sent == ["No such command 'sayhello'\n"]


def test_help_version_and_commands() -> None:
    """Local replies do not go to the room."""
    help_result, _player = _run("help")
    assert help_result.sent == ["Syntax: help <topic>\n"]
    assert help_result.room_lines == []

    version_result, _player = _run("version")
    assert version_result.sent == [f"The current version is: {get_version()}\n"]

    resident = _Player(
        player=Player("Ada", "reception", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    )
    listed, _player = _run("commands", resident)
    words = command_words(RANK_RESIDENT).split()
    width = frame_width(resident.discovered_columns, resident.frame_columns)
    wrapped = wrap_words(words, width)
    assert listed.sent == [f"Commands available:\n{wrapped}\n"]
    assert command_words(RANK_RESIDENT) == (
        "! \" ' , . : ; < > ? ` ~ alarm auto_reply color colour commands description emote "
        "entermsg examine go help join look lsu main map password prompt quit recap remote "
        "retire save say "
        "shout "
        "staff status tell term_width think time version w who x"
    )


def test_recap_changes_only_the_capitalisation(tmp_path: Path) -> None:
    """BoB updates the live name and the file. The file stays bob.yml."""
    save_player(tmp_path, SavedPlayer("Bob", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    user = Player("Bob", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    bob = _Player(name="Bob", room="hall", player=user)
    host.people = [bob]
    process_commands(host, bob, "recap BoB")  # type: ignore[arg-type]
    assert host.sent == ["Name set to BoB.\n"]
    assert user.name == "BoB"
    assert user.lowercase_name == "bob"
    assert host.room_lines == []
    saved = load_saved(player_path(tmp_path, "bob"))
    assert saved is not None
    assert saved.name == "BoB"
    assert player_path(tmp_path, "bob").is_file()
    host.sent.clear()
    process_commands(host, bob, "recap Robert")  # type: ignore[arg-type]
    assert host.sent == ["You can only change the capitalisation of your name.\n"]
    assert user.name == "BoB"


def test_recap_cannot_rename_someone_else(tmp_path: Path) -> None:
    """Ada cannot recap Bea. A bare recap shows the syntax."""
    host = _login_host(tmp_path)
    ada = _newbie("Ada")
    bea = _newbie("Bea")
    host.people = [ada, bea]
    process_commands(host, ada, "recap Bea")  # type: ignore[arg-type]
    assert host.sent == ["You can only change the capitalisation of your name.\n"]
    assert bea.player is not None
    assert bea.player.name == "Bea"
    host.sent.clear()
    process_commands(host, ada, "recap")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: recap <name>\n"]


def test_recap_of_the_current_spelling_succeeds() -> None:
    """Typing the spelling already in use is still a success."""
    ada, _player = _run("recap Ada")
    assert ada.sent == ["Name set to Ada.\n"]


def test_recap_keeps_the_live_name_when_the_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed save leaves the display name the player already has."""
    save_player(tmp_path, SavedPlayer("Bob", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    user = Player("Bob", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    bob = _Player(name="Bob", room="hall", player=user)
    host.people = [bob]

    def _fail(directory: object, player: object) -> None:
        del directory, player
        raise ConfigError("cannot write")

    monkeypatch.setattr(command_api, "save_player", _fail)
    process_commands(host, bob, "recap BoB")  # type: ignore[arg-type]
    assert host.sent == ["That name cannot be used right now.\n"]
    assert user.name == "Bob"


def test_help_shows_an_entry_or_says_it_is_missing() -> None:
    """A name and an alias show the same paragraph. An unknown topic does not."""
    book = HelpBook(
        entries=(
            HelpEntry(
                "say",
                ("'", '"', "`"),
                "Speak to the other people in the room you are in.",
            ),
            HelpEntry("examine", ("x",), "Show a player."),
        )
    )
    host = _Host(_help=book)
    player = _Player()
    process_commands(host, player, "help say")  # type: ignore[arg-type]
    page = host.sent[0]
    assert page.startswith("\n\n")
    assert page.endswith("\n\n")
    assert "Command : say" in page
    assert "Syntax  : say <message>" in page
    assert "Aliases : ' \" `" in page
    assert host.room_lines == []

    host = _Host(_help=book)
    process_commands(host, _Player(), "help '")  # type: ignore[arg-type]
    alias_page = host.sent[0]
    assert "Command : say" in alias_page
    assert "Syntax  : say <message>" in alias_page
    assert "Aliases : ' \" `" in alias_page

    host = _Host(_help=book)
    process_commands(host, _Player(), "help nope")  # type: ignore[arg-type]
    assert host.sent == ["help for nope not found\n"]
    assert host.room_lines == []

    host = _Host(_help=book)
    process_commands(host, _Player(), "help say please")  # type: ignore[arg-type]
    assert host.sent == ["help for say please not found\n"]


def test_help_for_a_topic_that_is_not_a_command() -> None:
    """A topic with no command shows the paragraph, and an alias still opens it."""
    book = HelpBook(
        entries=(
            HelpEntry("hours", ("closing",), "The office closes at six."),
            HelpEntry("look", (), "Show this room."),
        )
    )
    host = _Host(_help=book)
    player = _Player()
    process_commands(host, player, "help hours")  # type: ignore[arg-type]
    page = host.sent[0]
    assert page.startswith("\n\n")
    assert page.endswith("\n\n")
    assert "The office closes at six." in page
    assert "Command:" not in page
    assert "Syntax" not in page

    host = _Host(_help=book)
    process_commands(host, _Player(), "help closing")  # type: ignore[arg-type]
    assert "The office closes at six." in host.sent[0]
    assert "Command:" not in host.sent[0]


def test_help_hides_a_see_name_the_player_cannot_open() -> None:
    """See Also drops a topic this rank cannot open."""
    book = HelpBook(
        entries=(
            HelpEntry("hours", (), "The office closes at six.", see=("reload", "look")),
            HelpEntry("look", (), "Show this room."),
            HelpEntry("reload", (), "Read the help file again."),
        )
    )
    host = _Host(_help=book)
    process_commands(host, _Player(), "help hours")  # type: ignore[arg-type]
    assert "See Also: look" in host.sent[0]
    assert "reload" not in host.sent[0]


def test_help_look_shows_the_command_name_as_syntax() -> None:
    """Look has no syntax string, so the page shows the name."""
    host, _player = _run("help look")
    page = host.sent[0]
    assert "Syntax  : look" in page
    assert host.room_lines == []


def test_prompt_follows_a_finished_command() -> None:
    """The prompt returns after success, a syntax error, and an unknown command."""
    host = _Host(_help=_SHIPPED_HELP)
    player = _Player()
    process_input(host, player, "help")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: help <topic>\n", "What? > "]

    process_input(host, player, "say")  # type: ignore[arg-type]
    assert host.sent[-2:] == ["Syntax: say <message>\n", "What? > "]

    process_input(host, player, "nope")  # type: ignore[arg-type]
    assert host.sent[-2:] == ["No such command 'nope'\n", "What? > "]


def test_prompt_command_replaces_the_next_prompt() -> None:
    """``prompt`` stores the text and the following prompt uses it."""
    user = Player("Ada", "reception", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    player = _Player(player=user)
    host = _Host()
    process_input(host, player, "prompt Hello >")  # type: ignore[arg-type]
    assert user.prompt == "Hello > "
    assert host.sent == ["Prompt set.\n", "Hello > "]


def test_a_newbie_entered_shows_the_default_description_on_examine(tmp_path: Path) -> None:
    """A newbie who entered through login shows I am new here between the rules."""
    host = _login_host(tmp_path)
    ada = _Player(player=None)
    process_input(host, ada, "Ada")  # type: ignore[arg-type]
    process_input(host, ada, "yes")  # type: ignore[arg-type]
    assert ada.player is not None
    assert ada.player.description == "I am new here"
    bea = _newbie("Bea")
    host.people = [ada, bea]
    host.sent.clear()
    process_commands(host, bea, "examine Ada")  # type: ignore[arg-type]
    lines = host.sent[0].splitlines()
    assert lines[3].startswith("  I am new here")
    assert lines[2] == lines[4]


def test_description_sets_clears_and_refuses_long_text(tmp_path: Path) -> None:
    """Description stores one line, clears it, and refuses more than 80 characters."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    host.people = [bea]
    process_commands(host, bea, "description Hello there")  # type: ignore[arg-type]
    assert host.sent == ["Description set.\n"]
    path = player_path(tmp_path, "Bea")
    assert "description: Hello there" in path.read_text(encoding="utf-8")
    host.sent.clear()
    process_commands(host, bea, "examine me")  # type: ignore[arg-type]
    assert "  Hello there" in host.sent[0]
    assert "Hello there".center(72) not in host.sent[0]
    host.sent.clear()
    process_commands(host, bea, "description")  # type: ignore[arg-type]
    assert host.sent == ["Description cleared.\n"]
    assert "description" not in path.read_text(encoding="utf-8")
    host.sent.clear()
    process_commands(host, bea, "examine me")  # type: ignore[arg-type]
    assert "  Hello there" not in host.sent[0]
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), description="Saved"),
    )
    before = path.read_text(encoding="utf-8")
    host.sent.clear()
    process_commands(host, bea, f"description {'x' * 81}")  # type: ignore[arg-type]
    assert host.sent == ["A description is at most 80 characters.\n"]
    assert path.read_text(encoding="utf-8") == before


def test_description_preserves_internal_spaces(tmp_path: Path) -> None:
    """Description stores one stripped line without collapsing spaces."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    host.people = [bea]
    process_commands(host, bea, "description hello  world")  # type: ignore[arg-type]
    assert host.sent == ["Description set.\n"]
    assert bea.player.description == "hello  world"
    path = player_path(tmp_path, "Bea")
    assert "description: hello  world" in path.read_text(encoding="utf-8")


def test_description_refuses_81_chars_without_changing_live_value(tmp_path: Path) -> None:
    """An over-long description is refused and the in-memory line stays put."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    host.people = [bea]
    bea.player.description = "Still here"
    host.sent.clear()
    process_commands(host, bea, f"description {'x' * 81}")  # type: ignore[arg-type]
    assert host.sent == ["A description is at most 80 characters.\n"]
    assert bea.player.description == "Still here"


def test_save_writes_the_live_profile(tmp_path: Path) -> None:
    """Save stores the live description and leaves status off the file."""
    _save(tmp_path, "Ada", RANK_RESIDENT)
    host = _Host()
    host._roster_dir = tmp_path
    ada = _staff("Ada", RANK_RESIDENT)
    assert ada.player is not None
    ada.player.description = "Hello"
    ada.player.status = "away"
    ada.player.auto_reply = "later"
    command_save(host, ada, Command("save", command_save, False, "save"), None)
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
    newbie = _Player(player=Player("Ada", "reception", when, rank="newbie"))
    host, _player = _run("save", newbie)
    assert host.sent == ["No such command 'save'\n"]


def test_save_rejects_extra_text(tmp_path: Path) -> None:
    """Save with arguments shows the syntax line."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    host.people = [bea]
    process_commands(host, bea, "save now")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: save\n"]


def test_save_keeps_the_file_when_save_player_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed save sends CANNOT_NOW and leaves the file bytes alone."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    host.people = [bea]
    assert bea.player is not None
    bea.player.description = "Hello"
    path = player_path(tmp_path, "Bea")
    before = path.read_bytes()

    def _fail(directory: object, player: object) -> None:
        del directory, player
        raise ConfigError("cannot write")

    monkeypatch.setattr(command_api, "save_player", _fail)
    process_commands(host, bea, "save")  # type: ignore[arg-type]
    assert host.sent == ["That name cannot be used right now.\n"]
    assert path.read_bytes() == before


def test_a_newbie_cannot_retire() -> None:
    """A newbie is told retire does not exist."""
    host, _player = _run("retire", _newbie("Ada"))
    assert host.sent == ["No such command 'retire'\n"]


def test_retire_refuses_the_admin(tmp_path: Path) -> None:
    """The admin is refused and is not asked to confirm."""
    host = _login_host(tmp_path)
    boss = _staff("admin", RANK_ADMIN)
    host.people = [boss]
    process_input(host, boss, "retire")  # type: ignore[arg-type]
    assert host.sent == ["The admin cannot be removed.\n", "What? > "]
    assert boss.auth == "name"


def test_retire_cancelled_with_no_or_blank(tmp_path: Path) -> None:
    """No or a blank line cancels retire and keeps the file."""
    _save(tmp_path, "Ada", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_RESIDENT)
    host.people = [ada]
    for answer in ("no", ""):
        host.sent.clear()
        process_input(host, ada, "retire")  # type: ignore[arg-type]
        process_input(host, ada, answer)  # type: ignore[arg-type]
        assert host.sent[-1] == "Retire cancelled.\nWhat? > "
        assert ada.auth == "name"
        assert load_saved(player_path(tmp_path, "Ada")) is not None


def test_retire_yes_asks_for_password(tmp_path: Path) -> None:
    """YES moves to the password step with no play prompt appended."""
    _save(tmp_path, "Ada", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_RESIDENT)
    host.people = [ada]
    process_input(host, ada, "retire")  # type: ignore[arg-type]
    host.sent.clear()
    process_input(host, ada, "YES")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]
    assert ada.auth == "retire_password"


def test_retire_wrong_password_returns_to_the_room(tmp_path: Path) -> None:
    """A wrong password cancels retire and leaves the file."""
    _save(tmp_path, "Ada", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_RESIDENT)
    host.people = [ada]
    process_input(host, ada, "retire")  # type: ignore[arg-type]
    process_input(host, ada, "yes")  # type: ignore[arg-type]
    host.sent.clear()
    process_input(host, ada, "wrong")  # type: ignore[arg-type]
    assert host.sent == ["Wrong password.\nWhat? > "]
    assert ada.auth == "name"
    assert host.dropped is False
    assert load_saved(player_path(tmp_path, "Ada")) is not None


def test_retire_deletes_the_file_and_disconnects(tmp_path: Path) -> None:
    """The right password retires the player and tells staff."""
    _save(tmp_path, "Ada", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_RESIDENT)
    watcher = _staff("Bea", RANK_SUPER)
    host.people = [ada, watcher]
    process_input(host, ada, "retire")  # type: ignore[arg-type]
    process_input(host, ada, "yes")  # type: ignore[arg-type]
    host.sent.clear()
    process_input(host, ada, "pw")  # type: ignore[arg-type]
    assert host.sent == ["You have retired.\n"]
    assert load_saved(player_path(tmp_path, "Ada")) is None
    assert host.staff_lines == [paint_staff(watcher, "< SU > Ada retires.\n")]
    assert host.dropped_clients == [ada]
    assert host.announced == [True]


def test_retire_keeps_the_file_when_delete_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed delete sends CANNOT_NOW and does not drop the player."""
    _save(tmp_path, "Ada", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_RESIDENT)
    host.people = [ada]

    def _fail(directory: object, name: object) -> bool:
        del directory, name
        raise ConfigError("cannot delete")

    monkeypatch.setattr(password_api, "delete_player", _fail)
    process_input(host, ada, "retire")  # type: ignore[arg-type]
    process_input(host, ada, "yes")  # type: ignore[arg-type]
    host.sent.clear()
    process_input(host, ada, "pw")  # type: ignore[arg-type]
    assert "cannot delete" in capsys.readouterr().out
    assert host.sent == ["That name cannot be used right now.\nWhat? > "]
    assert ada.auth == "name"
    assert host.dropped is False
    assert load_saved(player_path(tmp_path, "Ada")) is not None


def test_status_under_rank_on_connected_examine(tmp_path: Path) -> None:
    """Connected examine shows Status under User Rank; logged-out examine does not."""
    user = Player("Ada", "hall", datetime.now(timezone.utc), rank=RANK_SUPER)
    ada = _Player(player=user)
    host = _Host(people=[ada])
    process_commands(host, ada, "status busy")  # type: ignore[arg-type]
    host.sent.clear()
    process_commands(host, ada, "examine me")  # type: ignore[arg-type]
    lines = host.sent[0].splitlines()
    rank_index = next(i for i, line in enumerate(lines) if line.startswith("  User Rank"))
    status_index = next(i for i, line in enumerate(lines) if line.startswith("  Status"))
    assert status_index == rank_index + 1
    assert "busy" in lines[status_index]
    rule_lines = [i for i, line in enumerate(lines) if line and set(line) == {"-"}]
    assert rank_index == rule_lines[1] + 1
    host.sent.clear()
    process_commands(host, ada, "status")  # type: ignore[arg-type]
    host.sent.clear()
    process_commands(host, ada, "examine me")  # type: ignore[arg-type]
    assert "  Status" not in host.sent[0]
    _save(tmp_path, "Adam", RANK_RESIDENT)
    host = _login_host(tmp_path, admin="Zed")
    process_commands(host, _Player(), "examine ad")  # type: ignore[arg-type]
    assert "  Status" not in host.sent[0]


def test_status_shows_on_examine_who_and_lsu_and_is_not_saved(tmp_path: Path) -> None:
    """Status is a session field under User Rank on examine, and on both lists."""
    user = Player("Ada", "hall", datetime.now(timezone.utc), rank=RANK_SUPER)
    ada = _Player(player=user)
    host, _player = _run("status in a meeting", ada)
    assert user.status == "in a meeting"
    assert host.sent[-1] == "Status set.\n"
    host.sent.clear()
    process_commands(host, ada, f"status {'x' * 21}")  # type: ignore[arg-type]
    assert host.sent == ["A status is at most 20 characters.\n"]
    assert user.status == "in a meeting"
    host.sent.clear()
    process_commands(host, ada, "status")  # type: ignore[arg-type]
    assert user.status == ""
    assert host.sent == ["Status cleared.\n"]
    host.sent.clear()
    process_commands(host, ada, "status busy")  # type: ignore[arg-type]
    host.sent.clear()
    process_commands(host, ada, "examine me")  # type: ignore[arg-type]
    lines = host.sent[0].splitlines()
    rank_index = next(i for i, line in enumerate(lines) if line.startswith("  User Rank"))
    status_index = next(i for i, line in enumerate(lines) if line.startswith("  Status"))
    assert status_index == rank_index + 1
    assert "busy" in lines[status_index]
    host.sent.clear()
    process_commands(host, ada, "status")  # type: ignore[arg-type]
    host.sent.clear()
    process_commands(host, ada, "examine me")  # type: ignore[arg-type]
    assert "  Status" not in host.sent[0]
    host.sent.clear()
    process_commands(host, ada, "status on call")  # type: ignore[arg-type]
    host.sent.clear()
    process_commands(host, ada, "who")  # type: ignore[arg-type]
    who_header = host.sent[0].splitlines()[1]
    who_cols = ("Name", "Rank", "Room", "Status", "Idle")
    assert [who_header.index(name) for name in who_cols] == sorted(
        who_header.index(name) for name in who_cols
    )
    host.sent.clear()
    process_commands(host, ada, "lsu")  # type: ignore[arg-type]
    lsu_header = host.sent[0].splitlines()[1]
    assert [lsu_header.index(name) for name in who_cols] == sorted(
        lsu_header.index(name) for name in who_cols
    )
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    host.people = [bea]
    path = player_path(tmp_path, "Bea")
    process_commands(host, bea, "status meeting")  # type: ignore[arg-type]
    assert "status" not in path.read_text(encoding="utf-8")


def test_load_profile_copies_a_saved_description(tmp_path: Path) -> None:
    """A saved player enters with the description from the file."""
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), description="Hello"),
    )
    host = _login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    entry_api._load_profile(host, user)  # type: ignore[arg-type]
    assert user.description == "Hello"


def test_load_profile_copies_a_saved_entermsg(tmp_path: Path) -> None:
    """A saved player enters with the enter message from the file."""
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            entermsg="crawls into the room",
        ),
    )
    host = _login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    entry_api._load_profile(host, user)  # type: ignore[arg-type]
    assert user.entermsg == "crawls into the room"


def test_promote_drops_the_default_description_or_keeps_a_custom_one(tmp_path: Path) -> None:
    """Resident promotion clears I am new here but keeps a typed description."""
    host = _login_host(tmp_path)
    default = _newbie("Bea")
    assert default.player is not None
    default.player.description = "I am new here"
    ada = _staff("Ada", RANK_SUPER)
    host.people = [default, ada]
    process_commands(host, ada, "resident Bea")  # type: ignore[arg-type]
    process_input(host, default, "hunter2")  # type: ignore[arg-type]
    process_input(host, default, "hunter2")  # type: ignore[arg-type]
    assert default.player.description == ""
    path = player_path(tmp_path, "Bea")
    assert "description" not in path.read_text(encoding="utf-8")

    custom = _newbie("Cyd")
    assert custom.player is not None
    custom.player.description = "Hello"
    host.people = [custom, ada]
    process_commands(host, ada, "resident Cyd")  # type: ignore[arg-type]
    process_input(host, custom, "hunter2")  # type: ignore[arg-type]
    process_input(host, custom, "hunter2")  # type: ignore[arg-type]
    assert "description: Hello" in player_path(tmp_path, "Cyd").read_text(encoding="utf-8")

    host = _login_host(tmp_path, opened=False)
    admin = _Player(player=None)
    process_input(host, admin, "admin")  # type: ignore[arg-type]
    process_input(host, admin, "secret")  # type: ignore[arg-type]
    process_input(host, admin, "secret")  # type: ignore[arg-type]
    assert "description" not in player_path(tmp_path, "admin").read_text(encoding="utf-8")


def test_colour_reports_and_sets_on_and_off() -> None:
    """Bare colour reports the choice. on and off set it. ON is syntax."""
    newbie, _player = _run("colour")
    assert newbie.sent == ["Colour is on.\n"]
    turned, player = _run("colour off")
    assert turned.sent == ["Colour is off.\n"]
    assert player.player is not None
    assert player.player.colour is False
    back, player = _run("color on", player)
    assert back.sent == ["Colour is on.\n"]
    assert player.player is not None
    assert player.player.colour is True
    bad, _player = _run("colour ON")
    assert bad.sent == ["Syntax: colour on|off\n"]
    extra, _player = _run("colour off please")
    assert extra.sent == ["Syntax: colour on|off\n"]
    glued, _player = _run("coloron")
    assert glued.sent == ["No such command 'coloron'\n"]
    helped, _player = _run("help colour")
    assert "Turn colour on or off" in helped.sent[0]
    helped, _player = _run("help color")
    assert "Turn colour on or off" in helped.sent[0]


def test_colour_off_is_written_and_on_removes_the_key(tmp_path: Path) -> None:
    """A saved player keeps off in the file, and on removes that key."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    host.people = [bea]
    process_commands(host, bea, "colour off")  # type: ignore[arg-type]
    path = player_path(tmp_path, "bea")
    saved = load_saved(path)
    assert saved is not None
    assert saved.colour is False
    assert "colour: false" in path.read_text(encoding="utf-8")
    host.sent.clear()
    process_commands(host, bea, "colour on")  # type: ignore[arg-type]
    saved = load_saved(path)
    assert saved is not None
    assert saved.colour is True
    assert "colour" not in path.read_text(encoding="utf-8")


def test_a_failed_colour_write_still_changes_the_live_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The player is told the choice even when the file cannot be written."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    host.people = [bea]

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(command_api, "save_player", fail)
    process_commands(host, bea, "colour off")  # type: ignore[arg-type]
    assert host.sent == ["Colour is off.\n"]
    assert bea.player is not None
    assert bea.player.colour is False


def test_a_newbie_colour_off_is_stored_on_resident(tmp_path: Path) -> None:
    """Becoming a resident writes the colour chosen on this visit."""
    host = _login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), colour=False)
    bea = _Player(name="Bea", room="hall", player=user)
    host.people = [bea]
    password_api._promote_to_resident(host, bea, "secret")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "bea"))
    assert saved is not None
    assert saved.rank == RANK_RESIDENT
    assert saved.colour is False
    assert "colour: false" in player_path(tmp_path, "bea").read_text(encoding="utf-8")


def test_quit() -> None:
    """Quit says goodbye and asks the host to drop the player."""
    host, _player = _run("quit")
    assert host.sent == ["Goodbye!\n"]
    assert host.room_lines == []
    assert host.dropped is True


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("n", "n"),
        ("north", "n"),
        ("west", "w"),
        ("w", "w"),
        ("north-west", "nw"),
        ("north west", "nw"),
        ("south east", "se"),
        ("up", "up"),
        ("down", "down"),
    ],
)
def test_parse_direction(text: str, code: str) -> None:
    """Short and long direction words share one code."""
    assert parse_direction(text) == code


def test_parse_direction_rejects_unknown_words() -> None:
    """Case and unknown words do not match."""
    assert parse_direction("West") is None
    assert parse_direction("sideways") is None


def test_go_without_a_direction() -> None:
    """Go alone explains the syntax."""
    host, player = _run("go")
    assert host.sent == ["Syntax: go <direction>\n"]
    assert player.room == "reception"


def test_go_unknown_direction() -> None:
    """An unknown remainder is quoted back."""
    host, player = _run("go sideways")
    assert host.sent == ["No such direction 'sideways'.\n"]
    assert player.room == "reception"


def test_go_collapses_whitespace() -> None:
    """North west is one direction."""
    host, player = _run("go north   west")
    assert player.room == "hall"
    assert "Ada goes north-west to Hall.\n" in host.room_lines


def test_go_uses_the_destination_enter_sentence() -> None:
    """People left behind hear the room being entered."""
    host = _Host()
    host._layout = Layout(
        rooms={
            "reception": Room("The desk.", 4, {"nw": "hall"}),
            "hall": Room("A hall.", 4, {}, "opens the door and enters the hall"),
        }
    )
    ada = _Player()
    process_commands(host, ada, "go nw")  # type: ignore[arg-type]
    assert "Ada opens the door and enters the hall.\n" in host.room_lines
    assert "Ada goes north-west to Hall.\n" not in host.room_lines


def test_go_announces_the_player_enter_message() -> None:
    """The destination hears the player's sentence, not a direction."""
    user = Player("Ada", "reception", datetime.now(timezone.utc), entermsg="crawls into the room")
    ada = _Player(player=user)
    host = _Host(people=[ada])
    process_commands(host, ada, "go nw")  # type: ignore[arg-type]
    assert "Ada crawls into the room.\n" in host.room_lines
    assert "Ada arrives from the south-east.\n" not in host.room_lines


def test_join_says_who_they_went_to_join() -> None:
    """Join names the player, not the room sentence."""
    bea = _Player(name="Bea", room="hall")
    host = _Host(people=[bea])
    host._layout = Layout(
        rooms={
            "reception": Room("The desk.", 4, {"nw": "hall"}),
            "hall": Room("A hall.", 4, {}, "opens the door and enters the hall"),
        }
    )
    ada = _Player()
    process_commands(host, ada, "join Bea")  # type: ignore[arg-type]
    assert host.room_lines[0] == "Ada goes to join Bea.\n"


def test_main_uses_the_start_room_enter_sentence() -> None:
    """Main speaks the start room's sentence to the room being left."""
    ada = _Player(room="hall")
    host = _Host(people=[ada])
    host._layout = Layout(
        rooms={
            "reception": Room("The desk.", 4, {"nw": "hall"}, "walks back to the desk"),
            "hall": Room("A hall.", 4, {}),
        }
    )
    process_commands(host, ada, "main")  # type: ignore[arg-type]
    assert host.room_lines[0] == "Ada walks back to the desk.\n"


def test_map_sends_the_map() -> None:
    """Map with no remainder sends the centered map to the player only."""
    host, player = _run("map")
    assert host.room_lines == []
    width = frame_width(player.discovered_columns, player.frame_columns)
    colour_on = player.player is not None and player.player.colour
    drawn = host._layout.map_text(player.room, width, colour=colour_on)
    assert host.sent[0] == f"\n\n{center_block(drawn, width)}\n"
    assert green("Reception") in host.sent[0]


def test_center_block_uses_the_widest_line() -> None:
    """Each line shifts by the same pad. A wider block is left as it is."""
    assert center_block("a\nbbb\n", 5) == " a\n bbb\n"
    assert center_block("abcdef\n", 4) == "abcdef\n"


def test_map_centers_on_the_manual_width() -> None:
    """A chosen term width is the width the map is centered in."""
    player = _Player(discovered_columns=40, frame_columns=60)
    host, player = _run("map", player)
    width = frame_width(player.discovered_columns, player.frame_columns)
    colour_on = player.player is not None and player.player.colour
    drawn = host._layout.map_text(player.room, width, colour=colour_on)
    assert host.sent[0] == f"\n\n{center_block(drawn, width)}\n"


def test_question_mark_is_help() -> None:
    """Bare help and bare ? reply Syntax: help <topic>. ? say and ?say show the say page."""
    listed, _player = _run("help")
    assert listed.sent == ["Syntax: help <topic>\n"]
    asked, _player = _run("?")
    assert asked.sent == ["Syntax: help <topic>\n"]
    topic, _player = _run("? say")
    topic_page = topic.sent[0]
    assert "Command : say" in topic_page
    assert "Syntax  : say <message>" in topic_page
    stuck, _player = _run("?say")
    stuck_page = stuck.sent[0]
    assert "Command : say" in stuck_page
    assert "Syntax  : say <message>" in stuck_page


def test_map_leaves_the_current_room_plain_when_colour_is_off() -> None:
    """Colour off keeps the room name, without a green code."""
    player = _Player(
        player=Player("Ada", "reception", datetime.now(timezone.utc), colour=False),
    )
    host, _player = _run("map", player)
    assert "Reception" in host.sent[0]
    assert green("Reception") not in host.sent[0]


def test_map_rejects_extra_text() -> None:
    """Map does not take a remainder."""
    host, _player = _run("map please")
    assert host.sent == ["Syntax: map\n"]


def test_newbie_cannot_shout() -> None:
    """A newbie is told shout does not exist. A resident still shouts."""
    when = datetime.now(timezone.utc)
    newbie = _Player(player=Player("Ada", "reception", when, rank="newbie"))
    host, _player = _run("shout hi", newbie)
    assert host.sent == ["No such command 'shout'\n"]
    assert host.all_lines == []

    host, _player = _run("!hi", newbie)
    assert host.sent == ["No such command '!'\n"]
    assert host.all_lines == []

    host, _player = _run("shout", newbie)
    assert host.sent == ["No such command 'shout'\n"]
    host, _player = _run("!", newbie)
    assert host.sent == ["No such command '!'\n"]

    resident = _Player(player=Player("Ada", "reception", when, rank=RANK_RESIDENT))
    host, _player = _run("shout hi", resident)
    assert host.sent == ["You shout 'hi'\n"]
    assert host.all_lines == [yellow("! Ada shouts 'hi'") + "\n"]

    newbie_words = command_words(RANK_NEWBIE).split()
    assert "shout" not in newbie_words
    assert "!" not in newbie_words
    resident_words = command_words(RANK_RESIDENT).split()
    assert "shout" in resident_words
    assert "!" in resident_words


def test_help_hides_shout_from_a_newbie() -> None:
    """Help for a command above the player's rank is the same as a missing topic."""
    book = HelpBook(
        entries=(
            HelpEntry("say", ("'",), "Speak."),
            HelpEntry("shout", ("!",), "Shout to everyone."),
        )
    )
    when = datetime.now(timezone.utc)
    newbie = _Player(player=Player("Ada", "reception", when, rank="newbie"))
    host = _Host(_help=book)
    process_commands(host, newbie, "help shout")  # type: ignore[arg-type]
    assert host.sent == ["help for shout not found\n"]

    host = _Host(_help=book)
    process_commands(host, newbie, "help !")  # type: ignore[arg-type]
    assert host.sent == ["help for ! not found\n"]

    host = _Host(_help=book)
    process_commands(host, newbie, "help")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: help <topic>\n"]

    resident = _Player(player=Player("Ada", "reception", when, rank=RANK_RESIDENT))
    host = _Host(_help=book)
    process_commands(host, resident, "help shout")  # type: ignore[arg-type]
    shout_page = host.sent[0]
    assert "Command : shout" in shout_page
    assert "Syntax  : shout <message>" in shout_page
    assert "Aliases : !" in shout_page

    host = _Host(_help=book)
    process_commands(host, resident, "help")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: help <topic>\n"]


def test_shout_reaches_everyone_and_alias_needs_no_space() -> None:
    """Shout is not limited to the current room. ``!`` needs no space."""
    resident = _Player(
        player=Player("Ada", "reception", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    )
    host, _player = _run("shout hello there", resident)
    assert host.sent == ["You shout 'hello there'\n"]
    assert host.room_lines == []
    assert host.all_lines == [yellow("! Ada shouts 'hello there'") + "\n"]

    host, _player = _run("!hello", resident)
    assert host.sent == ["You shout 'hello'\n"]
    assert host.all_lines == [yellow("! Ada shouts 'hello'") + "\n"]

    host, _player = _run("!", resident)
    assert host.sent == ["Syntax: shout <message>\n"]
    assert host.all_lines == []


def test_tell_reaches_a_named_player_in_any_room() -> None:
    """Tell finds a player by name. A missing or roomless player is not found."""
    bea = _Player(name="Bea", room="hall")
    host = _Host(people=[bea])
    process_commands(host, _Player(), "tell Bea hello there")  # type: ignore[arg-type]
    assert host.sent == [
        "You tell Bea 'hello there'\n",
        green("> Ada tells you 'hello there'") + "\n",
    ]
    assert host.room_lines == []
    assert host.all_lines == []

    missing, _player = _run("tell Bea hello")
    assert missing.sent == ["Player Bea not found.\n"]

    waiting = _Player(name="Bea", room=None)
    host = _Host(people=[waiting])
    process_commands(host, _Player(), "tell Bea hello")  # type: ignore[arg-type]
    assert host.sent == ["Player Bea not found.\n"]

    bare, _player = _run("tell Bea")
    assert bare.sent == ["Syntax: tell <name> <message>\n"]

    host = _Host(people=[bea])
    process_commands(host, _Player(), ".Bea hello")  # type: ignore[arg-type]
    assert host.sent == [
        "You tell Bea 'hello'\n",
        green("> Ada tells you 'hello'") + "\n",
    ]
    host.sent.clear()
    process_commands(host, _Player(), "> Bea hello")  # type: ignore[arg-type]
    assert host.sent == [
        "You tell Bea 'hello'\n",
        green("> Ada tells you 'hello'") + "\n",
    ]
    dotted, _player = _run(".")
    assert dotted.sent == ["Syntax: tell <name> <message>\n"]


def test_remote_emotes_to_one_named_player() -> None:
    """Remote emotes to one player. A comma and < are the same command."""
    bea = _Player(name="Bea", room="hall")
    host = _Host(people=[bea])
    process_commands(host, _Player(), "remote Bea smiles")  # type: ignore[arg-type]
    assert host.sent == [
        "You emote: Ada smiles to Bea\n",
        green("> Ada smiles to you") + "\n",
    ]
    assert host.room_lines == []
    assert host.all_lines == []

    missing, _player = _run("remote Bea smiles")
    assert missing.sent == ["Player Bea not found.\n"]

    bare, _player = _run("remote Bea")
    assert bare.sent == ["Syntax: remote <name> <message>\n"]

    host = _Host(people=[bea])
    process_commands(host, _Player(), ",Bea smiles")  # type: ignore[arg-type]
    assert host.sent == [
        "You emote: Ada smiles to Bea\n",
        green("> Ada smiles to you") + "\n",
    ]
    host.sent.clear()
    process_commands(host, _Player(), "< Bea smiles")  # type: ignore[arg-type]
    assert host.sent == [
        "You emote: Ada smiles to Bea\n",
        green("> Ada smiles to you") + "\n",
    ]
    comma, _player = _run(",")
    assert comma.sent == ["Syntax: remote <name> <message>\n"]


def test_tell_to_yourself_is_refused() -> None:
    """A tell to the speaker is not delivered and has no auto-reply."""
    host = _Host()
    ada = _Player()
    ada.player.auto_reply = "later"
    host.people = [ada]
    command_tell(host, ada, Command("tell", command_tell, False, "tell <name> <message>"), "Ada hi")
    assert ada.sent == ["Talking to yourself is the first sign of madness.\n"]


def test_remote_alias_to_yourself_is_refused() -> None:
    """A remote alias uses the same sentence."""
    host = _Host()
    ada = _Player()
    host.people = [ada]
    command_remote(
        host, ada, Command("<", command_remote, True, "remote <name> <message>"), "ada waves"
    )
    assert ada.sent == ["Talking to yourself is the first sign of madness.\n"]


def test_auto_reply_comes_back_to_the_sender(tmp_path: Path) -> None:
    """Tell and remote still arrive, and the sender gets the reply."""
    bea_user = Player("Bea", "hall", datetime.now(timezone.utc))
    bea_user.auto_reply = "In a meeting"
    bea = _Player(name="Bea", player=bea_user)
    ada = _Player()
    host = _Host(people=[ada, bea])
    process_commands(host, ada, "tell Bea hello")  # type: ignore[arg-type]
    assert any(line == "[ Auto Reply ] In a meeting\n" for line in host.sent)
    assert bea.sent[-1].endswith("tells you 'hello'\n") or "tells you 'hello'" in "".join(bea.sent)

    host.sent.clear()
    ada.sent.clear()
    bea.sent.clear()
    process_commands(host, ada, "remote Bea smiles")  # type: ignore[arg-type]
    assert any(line == "[ Auto Reply ] In a meeting\n" for line in host.sent)
    assert green("> Ada smiles to you") + "\n" in bea.sent

    host.sent.clear()
    ada.sent.clear()
    bea.sent.clear()
    process_commands(host, ada, "auto_reply busy")  # type: ignore[arg-type]
    assert host.sent == ["Auto-reply set.\n"]
    assert ada.player is not None
    assert ada.player.auto_reply == "busy"

    host.sent.clear()
    ada.sent.clear()
    bea.sent.clear()
    bea_user.auto_reply = ""
    process_commands(host, ada, "auto_reply")  # type: ignore[arg-type]
    assert host.sent == ["Auto-reply cleared.\n"]
    assert ada.player is not None
    assert ada.player.auto_reply == ""

    host.sent.clear()
    ada.sent.clear()
    bea.sent.clear()
    bea_user.auto_reply = "Away"
    process_commands(host, bea, "auto_reply")  # type: ignore[arg-type]
    assert bea_user.auto_reply == ""
    process_commands(host, ada, "tell Bea ping")  # type: ignore[arg-type]
    assert not any(line.startswith("[ Auto Reply ]") for line in host.sent)

    _save(tmp_path, "Ada", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_RESIDENT)
    host.people = [ada]
    path = player_path(tmp_path, "Ada")
    process_commands(host, ada, "auto_reply out")  # type: ignore[arg-type]
    assert "auto_reply" not in path.read_text(encoding="utf-8")

    host.sent.clear()
    ada.sent.clear()
    ada.player.auto_reply = "Echo"
    host.people = [ada]
    process_commands(host, ada, "tell Ada hello")  # type: ignore[arg-type]
    assert not any(line.startswith("[ Auto Reply ]") for line in host.sent)


def test_a_received_tell_and_remote_are_green() -> None:
    """The recipient's line is green. The sender's copy is plain."""
    bea = _Player(name="Bea", room="hall")
    host = _Host(people=[bea])
    process_commands(host, _Player(), "tell Bea hello")  # type: ignore[arg-type]
    assert host.sent == [
        "You tell Bea 'hello'\n",
        green("> Ada tells you 'hello'") + "\n",
    ]
    host.sent.clear()
    process_commands(host, _Player(), "remote Bea smiles")  # type: ignore[arg-type]
    assert host.sent == [
        "You emote: Ada smiles to Bea\n",
        green("> Ada smiles to you") + "\n",
    ]
    assert bea.player is not None
    bea.player.colour = False
    host.sent.clear()
    process_commands(host, _Player(), "tell Bea hello")  # type: ignore[arg-type]
    assert host.sent == ["You tell Bea 'hello'\n", "> Ada tells you 'hello'\n"]


def test_room_and_shout_prefixes_respect_colour_off() -> None:
    """Prefixed room and shout lines stay plain when the recipient's colour is off."""
    when = datetime.now(timezone.utc)
    bea = _Player(name="Bea", room="reception")
    bea.player.colour = False
    host = _Host(people=[bea])
    process_commands(host, _Player(), "say hello")  # type: ignore[arg-type]
    assert host.room_lines == ["- Ada says 'hello'\n"]
    host.room_lines.clear()
    process_commands(host, _Player(), ":waves")  # type: ignore[arg-type]
    assert host.room_lines == ["- Ada waves\n"]
    resident = _Player(player=Player("Ada", "reception", when, rank=RANK_RESIDENT))
    host = _Host(people=[bea, resident])
    process_commands(host, resident, "shout hi")  # type: ignore[arg-type]
    assert host.all_lines == ["! Ada shouts 'hi'\n"]


def test_paint_staff_colours_when_colour_is_on() -> None:
    """Staff lines are cyan when colour is on and plain when it is off."""
    when = datetime.now(timezone.utc)
    on = _Player(player=Player("Ada", "hall", when, rank=RANK_SUPER))
    off = _Player(player=Player("Bea", "hall", when, rank=RANK_SUPER))
    off.player.colour = False
    line = "< SU > Ada says 'hello'\n"
    assert paint_staff(on, line) == cyan("< SU > Ada says 'hello'") + "\n"
    assert paint_staff(off, line) == line


def test_paint_admin_is_red_when_colour_is_on() -> None:
    """Admin channel lines are red with colour on and plain with colour off."""
    from lupaxa.chat_server.channel import paint_admin
    from lupaxa.chat_server.colour import red

    line = "[ Admin ] Ada says 'hello'\n"
    on = _staff("Ada", RANK_ADMIN)
    off = _staff("Bea", RANK_ADMIN)
    assert off.player is not None
    off.player.colour = False
    assert paint_admin(on, line) == red(line.rstrip("\n")) + "\n"
    assert paint_admin(off, line) == line


def test_broadcast_admin_reaches_only_admins() -> None:
    """A super user and a resident do not receive the admin channel."""
    from lupaxa.chat_server.channel import broadcast_admin, paint_admin

    ada = _staff("Ada", RANK_ADMIN)
    sue = _staff("Sue", RANK_SUPER)
    bea = _staff("Bea", RANK_RESIDENT)
    host = _Host(people=[ada, sue, bea])
    broadcast_admin(host, "[ Admin ] hello\n")  # type: ignore[arg-type]
    assert ada.sent == [paint_admin(ada, "[ Admin ] hello\n")]
    assert sue.sent == []
    assert bea.sent == []


def test_multiple_matches_uses_ampersand_before_the_last_name() -> None:
    """Two names use &. More names use commas, and & before the last."""
    assert multiple_matches_message(["Admin", "Adam"]) == "multiple Matches: Adam & Admin\n"
    assert (
        multiple_matches_message(["Adept", "Adam", "Admin"])
        == "multiple Matches: Adam, Adept & Admin\n"
    )


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
    adam = _Player(name="Adam", room="hall")
    host = _Host(people=[adam])
    process_commands(host, _Player(), "tell ad hello")  # type: ignore[arg-type]
    assert host.sent == [
        "You tell Adam 'hello'\n",
        green("> Ada tells you 'hello'") + "\n",
    ]

    admin = _Player(name="Admin", room="hall")
    host = _Host(people=[adam, admin])
    process_commands(host, _Player(), "tell a hello")  # type: ignore[arg-type]
    assert host.sent == ["multiple Matches: Adam & Admin\n"]


def test_examine_partial_finds_one_logged_out_player(tmp_path: Path) -> None:
    """A unique prefix of a saved name examines that logged-out player."""
    _save(tmp_path, "Adam", RANK_RESIDENT)
    host = _login_host(tmp_path, admin="Zed")
    process_commands(host, _Player(), "examine ad")  # type: ignore[arg-type]
    assert host.sent[0].splitlines()[1].strip() == "Adam"
    assert "Player ad not found." not in host.sent[0]


def test_examine_refuses_several_partial_names(tmp_path: Path) -> None:
    """Online and logged-out prefixes are one list."""
    _save(tmp_path, "Admin", RANK_RESIDENT)
    adam = _Player(name="Adam", room="hall")
    host = _login_host(tmp_path, admin="Zed")
    host.people = [adam]
    process_commands(host, _Player(), "examine a")  # type: ignore[arg-type]
    assert host.sent == ["multiple Matches: Adam & Admin\n"]


def test_permanent_commands_do_not_accept_a_prefix(tmp_path: Path) -> None:
    """Resident, promote, demote, disconnect, and nuke need the whole name."""
    _save(tmp_path, "Adam", RANK_RESIDENT)
    adam = _newbie("Adam")
    host = _login_host(tmp_path)
    host.people = [adam]
    super_user = _staff("Ada", RANK_SUPER)
    process_commands(host, super_user, "resident Ad")  # type: ignore[arg-type]
    assert host.sent[-1] == "Player Ad not found.\n"
    process_commands(host, super_user, "disconnect Ad rude")  # type: ignore[arg-type]
    assert host.sent[-1] == "Player Ad not found.\n"
    assert host.dropped_clients == []
    process_commands(host, super_user, "nuke Ad gone")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Adam")) is not None
    admin = _staff("Boss", RANK_ADMIN)
    process_commands(host, admin, "promote Ad")  # type: ignore[arg-type]
    assert host.sent[-1] == "Player Ad not found.\n"
    process_commands(host, admin, "demote Ad")  # type: ignore[arg-type]
    assert host.sent[-1] == "Player Ad not found.\n"


def test_idle_clock_is_blank_until_eleven_seconds() -> None:
    """Ten seconds is blank. Eleven seconds is a clock. Hours are not clipped."""
    assert idle_clock(10) == ""
    assert idle_clock(11) == "00:00:11"
    assert idle_clock(3723) == "01:02:03"
    assert idle_clock(100 * 3600) == "100:00:00"


def test_frame_width_uses_manual_then_discovered_then_eighty() -> None:
    """A manual width wins. A narrow discovery is kept. Otherwise the frame is 80."""
    assert frame_width(None, None) == 80
    assert frame_width(40, None) == 40
    assert frame_width(200, None) == 200
    assert frame_width(200, 70) == 70
    assert frame_width(None, 70) == 70


def test_time_frame_is_the_eighty_column_picture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Time prints the clock and the uptime, padded to the frame width."""
    fixed = datetime(2026, 10, 12, 13, 45, 3, tzinfo=timezone.utc)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return fixed

    monkeypatch.setattr("lupaxa.chat_server.commands.datetime", _Clock)
    host = _Host()
    host.started_at = fixed - timedelta(hours=2, minutes=5)
    player = _Player(discovered_columns=80, frame_columns=None)
    process_commands(host, player, "time")  # type: ignore[arg-type]
    expected = """--------------------------------------------------------------------------------
  Current time : Monday 12th October 2026, 13:45:03 UTC                         
  Uptime       : 2 hours, 5 minutes                                             
--------------------------------------------------------------------------------
"""
    assert host.sent == [expected]
    assert all(len(line) == 80 for line in expected.splitlines())
    host.sent.clear()
    process_commands(host, player, "time please")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: time\n"]


def test_time_frame_grows_to_the_longest_line() -> None:
    """A narrow frame widens to the current-time line."""
    text = time_frame(
        "Monday 12th October 2026, 13:45:03 UTC",
        "2 hours, 5 minutes",
        40,
    )
    assert len(text.splitlines()[0]) == 55


def test_term_width_sets_and_clears_a_manual_width() -> None:
    """A manual width is kept, refused outside 60 and the ceiling, and cleared by auto."""
    ada = _Player(discovered_columns=100, discovered_rows=24, frame_columns=None)
    host = _Host(people=[ada])
    process_commands(host, ada, "term_width")  # type: ignore[arg-type]
    assert host.sent == ["  Terminal Size  : 100x24 (100x24 discovered)\n"]
    host.sent.clear()
    process_commands(host, ada, "term_width 70")  # type: ignore[arg-type]
    assert ada.frame_columns == 70
    assert host.sent == ["Width set to 70.\n"]
    host.sent.clear()
    process_commands(host, ada, "term_width 59")  # type: ignore[arg-type]
    assert ada.frame_columns == 70
    assert host.sent == ["Width must be at least 60.\n"]
    host.sent.clear()
    process_commands(host, ada, "term_width 101")  # type: ignore[arg-type]
    assert host.sent == ["Width cannot be wider than 100.\n"]
    host.sent.clear()
    process_commands(host, ada, "term_width auto")  # type: ignore[arg-type]
    assert ada.frame_columns is None
    assert host.sent == ["Width set to automatic.\n"]
    host.sent.clear()
    process_commands(host, ada, "term_width please")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: term_width <width>|auto\n"]
    host.sent.clear()
    process_commands(host, ada, "term_width ²")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: term_width <width>|auto\n"]
    pending_host = _Host()
    bare = _Player(discovered_columns=None, discovered_rows=None, frame_columns=None)
    process_commands(pending_host, bare, "term_width")  # type: ignore[arg-type]
    assert pending_host.sent == ["  Terminal Size  : 80  (Pending)\n"]


def test_term_width_is_not_saved_and_a_narrow_window_rejects_numbers(tmp_path: Path) -> None:
    """The manual width is not a player-file field. Below 60 only auto works."""
    save_player(tmp_path, SavedPlayer("Ada", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    user = Player("Ada", "reception", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    ada = _Player(name="Ada", player=user, discovered_columns=40, discovered_rows=12)
    host.people = [ada]
    process_commands(host, ada, "term_width 60")  # type: ignore[arg-type]
    assert ada.frame_columns is None
    assert host.sent == ["Width cannot be wider than 40.\n"]
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
    bea_user = Player("Bea", "hall", when, idle_seconds=120)
    host = _Host(
        people=[
            _Player(name="Bea", room="hall", player=bea_user),
            _Player(name="Ada", room="reception", player=ada_user),
            _Player(name="Cyd", room=None),
        ]
    )
    asker = _Player()
    assert asker.discovered_columns == 72
    process_commands(host, asker, "who")  # type: ignore[arg-type]
    text = host.sent[0]
    lines = text.splitlines()
    assert lines[0] == "-" * 72
    assert lines[-1] == "-" * 72
    assert "Connected:" not in text
    assert "00:02:00" in text
    assert "00:00:10" not in text

    host = _Host(people=[_Player(name="Cyd", room=None)])
    process_commands(host, _Player(), "who")  # type: ignore[arg-type]
    assert host.sent == [" -=> There is currently no one connected\n"]

    extra, _player = _run("who please")
    assert extra.sent == ["Syntax: who\n"]


def test_who_lists_a_player_who_is_alone() -> None:
    """A player in a room is listed, including when they are the only one."""
    when = datetime.now(timezone.utc)
    ada = _Player(
        name="Ada",
        room="reception",
        player=Player("Ada", "reception", when),
    )
    host = _Host(people=[ada])
    process_commands(host, ada, "who")  # type: ignore[arg-type]
    assert "Ada" in host.sent[0]
    assert host.sent[0] != " -=> There is currently no one connected\n"


def test_w_lists_the_same_players_as_who() -> None:
    """W is who. wplease is not. w please is the who syntax."""
    ada = _Player()
    bea = _Player(name="Bea", room="hall", player=Player("Bea", "hall", datetime.now(timezone.utc)))
    host = _Host(people=[ada, bea])
    process_commands(host, ada, "who")  # type: ignore[arg-type]
    who_text = host.sent[0]
    host.sent.clear()
    process_commands(host, ada, "w")  # type: ignore[arg-type]
    assert host.sent == [who_text]
    host.sent.clear()
    process_commands(host, ada, "wplease")  # type: ignore[arg-type]
    assert host.sent == ["No such command 'wplease'\n"]
    host.sent.clear()
    process_commands(host, ada, "w please")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: who\n"]


def test_enter_at_the_play_prompt_does_not_clear_idle() -> None:
    """An empty line shows the prompt again and leaves idle alone."""
    user = Player("Ada", "hall", datetime.now(timezone.utc), idle_seconds=40)
    ada = _Player(player=user)
    host = _Host(people=[ada])
    process_input(host, ada, "")  # type: ignore[arg-type]
    assert user.idle_seconds == 40
    assert host.sent == ["What? > "]
    assert "No such command" not in "".join(host.sent)


def test_confirm_password_starts_after_a_blank_line(tmp_path: Path) -> None:
    """The confirm prompt is preceded by a blank line."""
    assert confirm_password_prompt() == "\n\nConfirm password: "
    host = _login_host(tmp_path, opened=False)
    local = _Player(player=None, address="127.0.0.1")
    process_input(host, local, "admin")  # type: ignore[arg-type]
    process_input(host, local, "secret")  # type: ignore[arg-type]
    assert host.sent[-1] == "\n\nConfirm password: "


def test_look_repeats_the_room_brief() -> None:
    """Look shows the room brief and does not tell the room anyone arrived."""
    ada = _Player()
    bea = _Player(name="Bea", room="reception")
    host = _Host(people=[ada, bea])
    process_commands(host, ada, "look")  # type: ignore[arg-type]
    assert host.sent == [host._layout.brief("reception", [])]
    assert host.room_lines == []

    extra, _player = _run("look please")
    assert extra.sent == ["Syntax: look\n"]

    newbie_words = command_words(RANK_NEWBIE).split()
    assert "look" in newbie_words


def test_join_moves_to_that_players_room() -> None:
    """Join follows a player when the destination has space."""
    bea = _Player(name="Bea", room="hall")
    host = _Host(people=[bea])
    ada = _Player()
    process_commands(host, ada, "join Bea")  # type: ignore[arg-type]
    assert ada.room == "hall"
    assert host.room_lines == ["Ada goes to join Bea.\n", "Ada arrives.\n"]
    assert host.sent[0] == host._layout.brief("hall", [])

    stay = _Host(people=[_Player(name="Bea", room="reception")])
    ada = _Player()
    process_commands(stay, ada, "join Bea")  # type: ignore[arg-type]
    assert ada.room == "reception"
    assert stay.sent == ["You are already in Reception.\n"]
    assert stay.room_lines == []

    full = _Host(people=[bea], occupied=4)
    stuck = _Player()
    process_commands(full, stuck, "join Bea")  # type: ignore[arg-type]
    assert stuck.room == "reception"
    assert full.sent == ["Hall is full.\n"]
    assert full.room_lines == []

    missing, player = _run("join Nobody")
    assert missing.sent == ["Player Nobody not found.\n"]
    assert player.room == "reception"


def test_join_uses_one_partial_name_and_refuses_several() -> None:
    """Join be follows Bea. Join b names both Bea and Ben."""
    bea = _Player(name="Bea", room="hall")
    host = _Host(people=[bea])
    ada = _Player()
    process_commands(host, ada, "join be")  # type: ignore[arg-type]
    assert ada.room == "hall"

    ben = _Player(name="Ben", room="hall")
    host = _Host(people=[bea, ben])
    ada = _Player()
    process_commands(host, ada, "join b")  # type: ignore[arg-type]
    assert host.sent == ["multiple Matches: Bea & Ben\n"]
    assert ada.room == "reception"


def test_main_moves_to_the_start_room() -> None:
    """Main announces the leave and the arrive, then shows the room."""
    ada = _Player(room="hall")
    host = _Host(people=[ada])
    process_commands(host, ada, "main")  # type: ignore[arg-type]
    assert ada.room == "reception"
    assert host.room_lines == ["Ada goes to Reception.\n", "Ada arrives.\n"]
    assert host.sent[0] == host._layout.brief("reception", [])


def test_main_does_nothing_when_already_there() -> None:
    """Main sends nothing when the player is already in the start room."""
    host, ada = _run("main")
    assert ada.room == "reception"
    assert host.sent == []
    assert host.room_lines == []


def test_main_stays_when_the_start_room_is_full() -> None:
    """A full start room keeps the player where they are."""
    ada = _Player(room="hall")
    host = _Host(people=[ada], occupied=4)
    process_commands(host, ada, "main")  # type: ignore[arg-type]
    assert ada.room == "hall"
    assert host.sent == ["Reception is full.\n"]
    assert host.room_lines == []


def test_main_with_extra_text_shows_the_syntax() -> None:
    """Extra text does not move the player."""
    host, player = _run("main please")
    assert host.sent == ["Syntax: main\n"]
    assert player.room == "reception"


def test_main_uses_the_start_room_display_name() -> None:
    """The leave line uses the display name of config.start."""
    layout = Layout(
        rooms={
            "lounge": Room("A lounge.", 4, {}),
            "the_bar": Room("A bar.", 4, {}),
        }
    )
    ada = _Player(room="lounge")
    host = _Host(people=[ada], _layout=layout)
    host._config = replace(host._config, start="the_bar")
    process_commands(host, ada, "main")  # type: ignore[arg-type]
    assert ada.room == "the_bar"
    assert host.room_lines == ["Ada goes to The Bar.\n", "Ada arrives.\n"]


class _ClosedHost(_Host):
    """A host whose send fails, so the arrive line must not be sent."""

    def send(self, client: _Player, text: str) -> bool:
        self.sent.append(text)
        client.sent.append(text)
        return False


def test_main_skips_the_arrive_line_when_the_brief_fails() -> None:
    """A failed description does not announce the arrival."""
    ada = _Player(room="hall")
    host = _ClosedHost(people=[ada])
    process_commands(host, ada, "main")  # type: ignore[arg-type]
    assert ada.room == "reception"
    assert host.room_lines == ["Ada goes to Reception.\n"]


def test_examine_idle_of_ten_seconds_is_not_idle(tmp_path: Path) -> None:
    """Ten seconds is not idle. Eleven seconds uses the duration words."""
    host = _login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), idle_seconds=10)
    bea = _Player(name="Bea", room="hall", player=user, discovered_columns=72)
    host.people = [bea]
    process_commands(host, bea, "examine me")  # type: ignore[arg-type]
    assert "Not idle at all" in host.sent[0]
    assert "Idle for" in host.sent[0]
    assert host.sent[0].splitlines()[0] == "-" * 72
    user.idle_seconds = 11
    host.sent.clear()
    process_commands(host, bea, "examine me")  # type: ignore[arg-type]
    assert "Not idle at all" not in host.sent[0]
    assert duration_phrase(11) in host.sent[0]


def test_examine_shows_name_room_and_time() -> None:
    """Examine names a player. ``x`` needs a space, and a missing player is not found."""
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    bea = _Player(
        name="Bea",
        room="hall",
        player=Player("Bea", "hall", when, connected_seconds=7200),
    )
    host = _Host(people=[bea])
    process_commands(host, _Player(), "examine Bea")  # type: ignore[arg-type]
    body = examine_frame(
        "Bea",
        (
            ("User Rank", shown_rank(RANK_NEWBIE, True)),
            ("Logged in for", "10 minutes"),
            ("Total Login", "2 hours"),
            ("Idle for", "Not idle at all"),
        ),
        72,
    )
    assert host.sent == [body]
    assert "User Rank" in body
    assert "Logged in for" in body
    assert "10 minutes" in body
    assert "Total Login" in body
    assert "2 hours" in body
    assert "Not idle at all" in body
    assert "Location:" not in body
    assert "Logged in from" not in body

    host = _Host(people=[bea])
    process_commands(host, _Player(), "x Bea")  # type: ignore[arg-type]
    assert host.sent == [body]

    glued, _player = _run("xBea")
    assert glued.sent == ["No such command 'xBea'\n"]

    bare, _player = _run("x")
    assert bare.sent == ["Syntax: examine <name>\n"]

    missing, _player = _run("examine Nobody")
    assert missing.sent == ["Player Nobody not found.\n"]


def test_examine_text_is_the_seventy_two_column_profile() -> None:
    """Examine keeps a coloured rank inside the 72-column terminal."""
    from lupaxa.chat_server.colour import green, plain_len

    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    user = Player("Bea", "hall", when, connected_seconds=7200, idle_seconds=0)
    bea = _Player(name="Bea", room="hall", player=user, discovered_columns=72)
    host = _Host(people=[bea])
    process_commands(host, bea, "examine me")  # type: ignore[arg-type]
    text = host.sent[0]
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
    ada = _Player(discovered_columns=80, discovered_rows=24)
    bea = _Player(name="Bea", room="hall", player=bea_user)
    host = _Host(people=[ada, bea])
    process_commands(host, ada, "examine Bea")  # type: ignore[arg-type]
    assert "Terminal Size" not in host.sent[0]

    left = datetime.now(timezone.utc) - timedelta(hours=2)
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            logged_out_at=left,
        ),
    )
    host = _login_host(tmp_path)
    process_commands(host, _viewer(RANK_RESIDENT), "examine Bea")  # type: ignore[arg-type]
    assert "Terminal Size" not in host.sent[0]

    pending = _Player(
        name="Bea",
        room="hall",
        player=Player("Bea", "hall", when),
        discovered_columns=None,
        discovered_rows=None,
        frame_columns=None,
    )
    host = _Host(people=[pending])
    process_commands(host, pending, "examine me")  # type: ignore[arg-type]
    assert "80  (Pending)" in host.sent[0]


def test_examine_me_is_the_typist() -> None:
    """me, Me, and ME show the typist, and staff see their own address."""
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    user = Player("Ada", "reception", when, connected_seconds=7200)
    ada = _Player(name="Ada", room="reception", player=user, address="10.0.0.5")
    host = _Host(people=[ada])
    body = examine_frame(
        "Ada",
        (
            ("User Rank", shown_rank(RANK_NEWBIE, True)),
            ("Logged in for", "10 minutes"),
            ("Total Login", "2 hours"),
            ("Idle for", "Not idle at all"),
            ("Terminal Size", "72x24 (72x24 discovered)"),
        ),
        72,
    )
    for word in ("me", "Me", "ME"):
        host.sent.clear()
        process_commands(host, ada, f"examine {word}")  # type: ignore[arg-type]
        assert host.sent == [body]
    host.sent.clear()
    process_commands(host, ada, "x me")  # type: ignore[arg-type]
    assert host.sent == [body]
    assert "Ada".center(63) in body
    assert "Not idle at all" in body
    assert "Location:" not in body

    sue = _staff("Sue", RANK_SUPER)
    sue.address = "10.0.0.9"
    host.people.append(sue)
    host.sent.clear()
    process_commands(host, sue, "examine ME")  # type: ignore[arg-type]
    assert host.sent == [
        examine_frame(
            "Sue",
            (
                ("User Rank", shown_rank(RANK_SUPER, True)),
                ("Logged in from", "10.0.0.9"),
                ("Logged in for", "0 seconds"),
                ("Total Login", "0 seconds"),
                ("Idle for", "Not idle at all"),
                ("Terminal Size", "72x24 (72x24 discovered)"),
            ),
            72,
        )
    ]


def test_examine_finds_a_connected_newbie_by_exact_name(tmp_path: Path) -> None:
    """Find a connected newbie by the lowercase key."""
    host = _login_host(tmp_path)
    cyd = _newbie("Cyd")
    host.people = [cyd]
    body = examine_frame(
        "Cyd",
        (
            ("User Rank", shown_rank(RANK_NEWBIE, True)),
            ("Logged in for", "0 seconds"),
            ("Total Login", "0 seconds"),
            ("Idle for", "Not idle at all"),
            ("Terminal Size", "72x24 (72x24 discovered)"),
        ),
        72,
    )
    process_commands(host, cyd, "examine Cyd")  # type: ignore[arg-type]
    assert host.sent == [body]
    assert "Cyd" in body
    assert "User Rank" in body
    assert "Newbie" in body
    assert load_saved(player_path(tmp_path, "Cyd")) is None
    host.sent.clear()
    process_commands(host, cyd, "examine cyd")  # type: ignore[arg-type]
    assert host.sent == [body]
    assert "Player cyd not found." not in host.sent[0]


def test_examine_lowercase_saved_name_uses_the_in_room_block(tmp_path: Path) -> None:
    """A saved player who is here gets the in-room block, not the logout block."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    user = Player("Bea", "hall", when, connected_seconds=7200)
    bea = _Player(name="Bea", room="hall", player=user)
    host.people = [bea, _newbie("Ada")]
    process_commands(host, _newbie("Ada"), "examine bea")  # type: ignore[arg-type]
    body = examine_frame(
        "Bea",
        (
            ("User Rank", shown_rank(RANK_NEWBIE, True)),
            ("Logged in for", "10 minutes"),
            ("Total Login", "2 hours"),
            ("Idle for", "Not idle at all"),
        ),
        72,
    )
    assert host.sent == [body]
    assert "Bea" in body
    assert "10 minutes" in body
    assert "2 hours" in body
    assert "Not idle at all" in body
    assert "Logged out" not in body
    assert "Location:" not in body


def test_alarm_sets_seconds_and_idle_lists_players() -> None:
    """Alarm stores minutes as seconds and resets the asker's idle."""
    when = datetime.now(timezone.utc)
    ada_user = Player("Ada", "reception", when, idle_seconds=40)
    bea_user = Player("Bea", "hall", when, idle_seconds=125)
    ada = _Player(name="Ada", room="reception", player=ada_user)
    host = _Host(people=[ada, _Player(name="Bea", room="hall", player=bea_user)])
    process_commands(host, ada, "alarm 2")  # type: ignore[arg-type]
    assert ada_user.alarm_seconds == 120
    assert ada_user.idle_seconds == 0
    assert host.sent == ["Alarm set for 2 minutes.\n"]

    bad, _player = _run("alarm later")
    assert bad.sent == ["Syntax: alarm <minutes>\n"]
    bad_sup, _player = _run("alarm ²")
    assert bad_sup.sent == ["Syntax: alarm <minutes>\n"]


def test_idle_is_unknown() -> None:
    """Idle is not a command."""
    host, _player = _run("idle")
    assert host.sent == ["No such command 'idle'\n"]


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
    host, _player = _run("commands")
    words = host.sent[0].split()
    assert words.index("`") < words.index("commands")
    assert "go" in words
    assert "map" in words


def _login_host(tmp_path: Path, admin: str = "admin", *, opened: bool = True) -> _Host:
    if opened and load_saved(player_path(tmp_path, admin)) is None:
        save_player(
            tmp_path,
            SavedPlayer(admin, RANK_SUPER_ADMIN, hash_password("pw", rounds=4)),
        )
    config = Config(
        port=1,
        rooms=Path("rooms.yml"),
        start="reception",
        help=Path("help.yml"),
        players=tmp_path,
        admin=admin,
        mfa_enabled=False,
    )
    host = _Host(_config=config, _roster_dir=tmp_path)
    host._help = HelpBook(
        entries=(HelpEntry("disclaimer", (), "Be kind."),),
    )
    return host


def _mfa_login_host(tmp_path: Path, admin: str = "admin", *, opened: bool = True) -> _Host:
    host = _login_host(tmp_path, admin, opened=opened)
    host._mfa_enabled = True
    host._config = replace(host._config, mfa_enabled=True)
    return host


def _register_mfa_command() -> None:
    from lupaxa.chat_server.commands_list import COMMANDS, ensure_commands
    from lupaxa.chat_server.mfa import register_mfa

    ensure_commands()
    register_mfa(COMMANDS)


def _mfa_command_host(tmp_path: Path, admin: str = "admin", *, opened: bool = True) -> _Host:
    host = _mfa_login_host(tmp_path, admin, opened=opened)
    _register_mfa_command()
    return host


_MFA_REMOVED_WARNING = (
    "Your MFA has been removed. Set it up again before you log out. "
    "The next login will make you set it up before you can enter."
)


def _assert_sealed_mfa_secret(saved: SavedPlayer, password: str, plain_secret: str) -> None:
    """Assert the file seal opens with ``password`` to ``plain_secret``."""
    from lupaxa.chat_server.mfa_seal import open_mfa_secret

    assert saved.mfa_secret is not None
    assert saved.mfa_secret.startswith("enc1$")
    assert plain_secret not in saved.mfa_secret
    assert saved.mfa_on is True
    assert open_mfa_secret(password, saved.mfa_secret) == plain_secret


def _totp_code(secret: str, *, now: int = 1_700_000_000) -> str:
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


def test_mfa_login_asks_for_a_code_after_the_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A saved secret sends Code after the password, then enters on a match."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=totp_seed,
        ),
    )
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.player is None
    assert player.auth == "code"
    assert host.sent[-1] == "Code: "
    process_input(host, player, _totp_code(totp_seed))  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.name == "Bea"


def test_a_plaintext_secret_still_logs_in_and_is_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old base32 secret matches a code and is stored sealed."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), mfa_secret=totp_seed),
    )
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is not None
    assert saved.mfa_secret.startswith("enc1$")
    assert totp_seed not in saved.mfa_secret
    assert saved.mfa_on is True
    assert player.auth == "code"
    assert player.player is None
    process_input(host, player, _totp_code(totp_seed))  # type: ignore[arg-type]
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
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), mfa_secret=totp_seed),
    )
    host = _mfa_login_host(tmp_path)
    host._mfa_enabled = False
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert "Code:" not in "".join(host.sent)
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is not None
    assert saved.mfa_secret.startswith("enc1$")
    assert saved.mfa_on is True


def test_login_says_mfa_was_removed_when_the_flag_remains(tmp_path: Path) -> None:
    """A deleted secret is obvious when the file still shows MFA was on."""
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), mfa_on=True),
    )
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" in host.sent
    assert player.player is not None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_on is False


def test_staff_with_the_flag_are_told_and_must_set_mfa_up(tmp_path: Path) -> None:
    """A wiped secret still blocks staff when the file shows MFA was on."""
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_SUPER, hash_password("pw", rounds=4), mfa_on=True),
    )
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" in host.sent
    assert player.auth == "mfa_enroll"
    assert player.player is None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_on is True


def test_staff_unreadable_seal_kept_until_setup_finishes(tmp_path: Path) -> None:
    """Staff keep a seal that will not open until enrolment saves."""
    from lupaxa.chat_server.mfa_seal import seal_mfa_secret

    sealed = seal_mfa_secret("other", TOTP_TEST_SEED)
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_SUPER,
            hash_password("pw", rounds=4),
            mfa_secret=sealed,
            mfa_on=True,
        ),
    )
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" in host.sent
    assert player.auth == "mfa_enroll"
    assert player.player is None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret == sealed
    assert saved.mfa_on is True


def test_staff_without_the_flag_set_up_with_no_removal_line(tmp_path: Path) -> None:
    """The first forced setup does not say MFA was removed."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_SUPER, hash_password("pw", rounds=4)))
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" not in host.sent
    assert player.auth == "mfa_enroll"


def test_mfa_off_leaves_an_unreadable_secret_in_place(tmp_path: Path) -> None:
    """A seal that will not open is kept when MFA is turned off."""
    from lupaxa.chat_server.mfa_seal import seal_mfa_secret

    sealed = seal_mfa_secret("other", TOTP_TEST_SEED)
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=sealed,
            mfa_on=True,
        ),
    )
    host = _mfa_login_host(tmp_path)
    host._mfa_enabled = False
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert "Your MFA has been removed.\n" not in host.sent
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret == sealed


def test_unreadable_secret_removed_when_mfa_flag_is_off(tmp_path: Path) -> None:
    """A seal that will not open is cleared even when mfa_on is false."""
    from lupaxa.chat_server.mfa_seal import seal_mfa_secret

    sealed = seal_mfa_secret("other", TOTP_TEST_SEED)
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=sealed,
            mfa_on=False,
        ),
    )
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" in host.sent
    assert player.player is not None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False


def test_an_unreadable_secret_tells_a_resident_and_is_deleted(tmp_path: Path) -> None:
    """A seal that will not open is removed after the resident is told."""
    from lupaxa.chat_server.mfa_seal import seal_mfa_secret

    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=seal_mfa_secret("other", TOTP_TEST_SEED),
            mfa_on=True,
        ),
    )
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert "Your MFA has been removed.\n" in host.sent
    assert player.player is not None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False


def test_mfa_login_drops_after_three_wrong_codes(tmp_path: Path) -> None:
    """Three wrong codes on one login drop the connection."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=totp_seed,
        ),
    )
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    process_input(host, player, "000000")  # type: ignore[arg-type]
    assert host.sent[-2:] == ["Wrong code.\n", "Code: "]
    process_input(host, player, "000001")  # type: ignore[arg-type]
    process_input(host, player, "000002")  # type: ignore[arg-type]
    assert host.dropped is True
    assert player.player is None


def test_mfa_enrolment_does_not_restore_a_nuked_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code after nuke does not recreate the file or enter the room."""
    save_player(tmp_path, SavedPlayer("Cyd", RANK_SUPER, hash_password("pw", rounds=4)))
    host = _mfa_login_host(tmp_path)
    boss = _staff("Boss", RANK_ADMIN)
    host.people = [boss]
    player = _Player(player=None)
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(host, player, "Cyd")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.auth == "mfa_enroll"
    secret = player.pending_mfa_secret
    delete_player(tmp_path, "Cyd")
    code = _totp_code(secret)
    process_input(host, player, code)  # type: ignore[arg-type]
    assert player.player is None
    assert load_saved(player_path(tmp_path, "Cyd")) is None
    assert host.sent[-1] == NAME_PROMPT


def test_mfa_super_user_enrols_on_first_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A super user with no secret sees the secret, then saves it on a match."""
    save_player(tmp_path, SavedPlayer("Cyd", RANK_SUPER, hash_password("pw", rounds=4)))
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(host, player, "Cyd")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.player is None
    assert player.auth == "mfa_enroll"
    body = "".join(host.sent)
    assert player.pending_mfa_secret in body
    assert "Code:" in body
    secret = player.pending_mfa_secret
    code = _totp_code(secret)
    process_input(host, player, code)  # type: ignore[arg-type]
    assert player.player is not None
    saved = load_saved(player_path(tmp_path, "Cyd"))
    assert saved is not None
    _assert_sealed_mfa_secret(saved, "pw", secret)


def test_mfa_super_admin_first_login_waits_for_enrolment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The config account file appears only after the enrolment code matches."""
    host = _mfa_login_host(tmp_path, opened=False)
    player = _Player(player=None)
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(host, player, "admin")  # type: ignore[arg-type]
    process_input(host, player, "secret")  # type: ignore[arg-type]
    process_input(host, player, "secret")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "admin")) is None
    assert player.auth == "mfa_enroll"
    secret = player.pending_mfa_secret
    code = _totp_code(secret)
    process_input(host, player, code)  # type: ignore[arg-type]
    assert player.player is not None
    saved = load_saved(player_path(tmp_path, "admin"))
    assert saved is not None
    _assert_sealed_mfa_secret(saved, "secret", secret)


def test_mfa_resident_without_secret_enters_on_password_alone(tmp_path: Path) -> None:
    """A resident with no mfa_secret still enters when MFA is on."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _mfa_login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.auth == "name"
    assert "Code:" not in "".join(host.sent)


def test_mfa_closed_talker_skips_the_code_for_residents(tmp_path: Path) -> None:
    """A resident is refused after the password when residents are closed."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=totp_seed,
        ),
    )
    host = _mfa_login_host(tmp_path)
    host._allow_residents = False
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert "Code:" not in "".join(host.sent)
    assert host.dropped is True


def test_mfa_reconnect_waits_for_the_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second socket does not replace the live player until the code matches."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=totp_seed,
        ),
    )
    host = _mfa_login_host(tmp_path)
    live = _Player(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT))
    host.people = [live]
    newcomer = _Player(player=None, address="10.0.0.5")
    monkeypatch.setattr("lupaxa.chat_server.password.time.time", lambda: 1_700_000_000.0)
    process_input(host, newcomer, "Bea")  # type: ignore[arg-type]
    process_input(host, newcomer, "pw")  # type: ignore[arg-type]
    assert host.replaced == []
    assert newcomer.auth == "code"
    process_input(host, newcomer, _totp_code(totp_seed))  # type: ignore[arg-type]
    assert host.replaced == [live]


def test_mfa_command_syntax_when_bare(tmp_path: Path) -> None:
    """Bare mfa prints the syntax line."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    bea = _staff("Bea", RANK_RESIDENT)
    host = _mfa_command_host(tmp_path)
    process_commands(host, bea, "mfa")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: mfa setup|mfa remove|mfa remove <name>\n"]


def test_mfa_setup_asks_password_then_saves_secret_on_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup stores the secret only after password and a matching code."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    bea = _staff("Bea", RANK_RESIDENT)
    host = _mfa_command_host(tmp_path)
    monkeypatch.setattr("lupaxa.chat_server.mfa.time.time", lambda: 1_700_000_000.0)
    process_commands(host, bea, "mfa setup")  # type: ignore[arg-type]
    assert host.sent == ["Current password: "]
    assert bea.auth == "mfa_pw"
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")).mfa_secret is None  # type: ignore[union-attr]
    secret = bea.pending_mfa_secret
    assert secret in "".join(host.sent)
    assert bea.auth == "mfa_show"
    process_input(host, bea, _totp_code(secret))  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    _assert_sealed_mfa_secret(saved, "pw", secret)
    assert bea.auth == "name"


def test_mfa_setup_wrong_password_returns_to_room(tmp_path: Path) -> None:
    """A wrong setup password sends one line and does not ask again."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    bea = _staff("Bea", RANK_RESIDENT)
    host = _mfa_command_host(tmp_path)
    process_commands(host, bea, "mfa setup")  # type: ignore[arg-type]
    process_input(host, bea, "no")  # type: ignore[arg-type]
    assert host.sent[-1] == "Wrong password.\nWhat? > "
    assert bea.auth == "name"


def test_mfa_setup_quit_at_password_returns_to_room(tmp_path: Path) -> None:
    """Quit at the setup password prompt returns to the room without saving."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    bea = _staff("Bea", RANK_RESIDENT)
    host = _mfa_command_host(tmp_path)
    process_commands(host, bea, "mfa setup")  # type: ignore[arg-type]
    process_input(host, bea, "quit")  # type: ignore[arg-type]
    assert host.sent[-1] == "What? > "
    assert bea.auth == "name"
    assert bea.player is not None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None


def test_mfa_setup_when_secret_exists(tmp_path: Path) -> None:
    """Setup is refused when MFA is already on."""
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=TOTP_TEST_SEED,
        ),
    )
    bea = _staff("Bea", RANK_RESIDENT)
    host = _mfa_command_host(tmp_path)
    process_commands(host, bea, "mfa setup")  # type: ignore[arg-type]
    assert host.sent == ["MFA is already on.\n"]


def test_mfa_remove_self_clears_secret_for_resident(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-remove asks for password and code, then clears the file."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=totp_seed,
        ),
    )
    bea = _staff("Bea", RANK_RESIDENT)
    host = _mfa_command_host(tmp_path)
    monkeypatch.setattr("lupaxa.chat_server.mfa.time.time", lambda: 1_700_000_000.0)
    process_commands(host, bea, "mfa remove")  # type: ignore[arg-type]
    assert host.sent == ["Current password: "]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    assert bea.auth == "mfa_remove_code"
    process_input(host, bea, _totp_code(totp_seed))  # type: ignore[arg-type]
    assert host.sent[-1] == "Your MFA has been removed.\nWhat? > "
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False


def test_mfa_remove_self_then_setup_same_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After self-remove, setup accepts the new secret's code in the same timestep."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=totp_seed,
        ),
    )
    bea = _staff("Bea", RANK_RESIDENT)
    host = _mfa_command_host(tmp_path)
    monkeypatch.setattr("lupaxa.chat_server.mfa.time.time", lambda: 1_700_000_000.0)
    process_commands(host, bea, "mfa remove")  # type: ignore[arg-type]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    process_input(host, bea, _totp_code(totp_seed))  # type: ignore[arg-type]
    assert bea.player is not None
    process_commands(host, bea, "mfa setup")  # type: ignore[arg-type]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    new_secret = bea.pending_mfa_secret
    process_input(host, bea, _totp_code(new_secret))  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    _assert_sealed_mfa_secret(saved, "pw", new_secret)
    assert bea.player is not None
    assert bea.auth == "name"


def test_mfa_remove_self_super_user_sees_warning_and_reenrols_on_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A super user who clears MFA is warned and must enrol again on the next login."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer("Cyd", RANK_SUPER, hash_password("pw", rounds=4), mfa_secret=totp_seed),
    )
    cyd = _staff("Cyd", RANK_SUPER)
    host = _mfa_command_host(tmp_path)
    monkeypatch.setattr("lupaxa.chat_server.mfa.time.time", lambda: 1_700_000_000.0)
    process_commands(host, cyd, "mfa remove")  # type: ignore[arg-type]
    process_input(host, cyd, "pw")  # type: ignore[arg-type]
    process_input(host, cyd, _totp_code(totp_seed))  # type: ignore[arg-type]
    assert _MFA_REMOVED_WARNING in host.sent[-1]
    assert cyd.player is not None
    newcomer = _Player(player=None)
    process_input(host, newcomer, "Cyd")  # type: ignore[arg-type]
    process_input(host, newcomer, "pw")  # type: ignore[arg-type]
    assert newcomer.auth == "mfa_enroll"


def test_mfa_remove_other_asks_sure_and_clears_on_yes(tmp_path: Path) -> None:
    """An admin clears another player's MFA after yes and announces on the admin channel."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_SUPER, hash_password("pw", rounds=4), mfa_secret=totp_seed),
    )
    save_player(tmp_path, SavedPlayer("Boss", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(tmp_path, SavedPlayer("Ada", RANK_ADMIN, hash_password("pw", rounds=4)))
    boss = _staff("Boss", RANK_ADMIN)
    ada = _staff("Ada", RANK_ADMIN)
    bea = _staff("Bea", RANK_SUPER)
    host = _mfa_command_host(tmp_path)
    host.people = [boss, ada, bea]
    process_commands(host, boss, "mfa remove Bea")  # type: ignore[arg-type]
    assert host.sent == ["Are you sure you want to remove MFA for Bea?\n"]
    assert boss.auth == "mfa_sure"
    process_input(host, boss, "no")  # type: ignore[arg-type]
    assert host.sent[-1] == "MFA remove cancelled.\nWhat? > "
    process_commands(host, boss, "mfa remove Bea")  # type: ignore[arg-type]
    process_input(host, boss, "yes")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False
    assert bea.player is not None
    line = "[ Admin ] Boss removes MFA for Bea.\n"
    assert host.admin_lines == [paint_admin(ada, line)]


def test_mfa_remove_other_clears_logged_off_player(tmp_path: Path) -> None:
    """Removing MFA for someone who is offline still clears the file after yes."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_SUPER, hash_password("pw", rounds=4), mfa_secret=totp_seed),
    )
    save_player(tmp_path, SavedPlayer("Boss", RANK_ADMIN, hash_password("pw", rounds=4)))
    boss = _staff("Boss", RANK_ADMIN)
    host = _mfa_command_host(tmp_path)
    process_commands(host, boss, "mfa remove Bea")  # type: ignore[arg-type]
    process_input(host, boss, "yes")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False


def test_mfa_remove_other_refuses_equal_rank(tmp_path: Path) -> None:
    """An admin cannot remove MFA for another admin."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_ADMIN, hash_password("pw", rounds=4), mfa_secret=totp_seed),
    )
    save_player(tmp_path, SavedPlayer("Boss", RANK_ADMIN, hash_password("pw", rounds=4)))
    boss = _staff("Boss", RANK_ADMIN)
    host = _mfa_command_host(tmp_path)
    process_commands(host, boss, "mfa remove Bea")  # type: ignore[arg-type]
    assert host.sent == ["You cannot remove MFA for Bea.\n"]


def test_mfa_remove_other_super_admin_clears_admin(tmp_path: Path) -> None:
    """The super admin can clear a promoted admin after yes."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_ADMIN, hash_password("pw", rounds=4), mfa_secret=totp_seed),
    )
    save_player(
        tmp_path,
        SavedPlayer("admin", RANK_SUPER_ADMIN, hash_password("pw", rounds=4), mfa_secret=totp_seed),
    )
    ada = _staff("admin", RANK_SUPER_ADMIN)
    host = _mfa_command_host(tmp_path)
    process_commands(host, ada, "mfa remove Bea")  # type: ignore[arg-type]
    process_input(host, ada, "yes")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is None
    assert saved.mfa_on is False


def test_mfa_remove_other_refuses_super_admin(tmp_path: Path) -> None:
    """Nobody can remove MFA for the super admin through the talker."""
    totp_seed = TOTP_TEST_SEED
    save_player(
        tmp_path,
        SavedPlayer("admin", RANK_SUPER_ADMIN, hash_password("pw", rounds=4), mfa_secret=totp_seed),
    )
    save_player(tmp_path, SavedPlayer("Boss", RANK_ADMIN, hash_password("pw", rounds=4)))
    boss = _staff("Boss", RANK_ADMIN)
    host = _mfa_command_host(tmp_path)
    process_commands(host, boss, "mfa remove admin")  # type: ignore[arg-type]
    assert host.sent == ["You cannot remove MFA for admin.\n"]


def test_mfa_remove_other_without_secret(tmp_path: Path) -> None:
    """Removing MFA for someone with no secret is refused."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_SUPER, hash_password("pw", rounds=4)))
    save_player(tmp_path, SavedPlayer("Boss", RANK_ADMIN, hash_password("pw", rounds=4)))
    boss = _staff("Boss", RANK_ADMIN)
    host = _mfa_command_host(tmp_path)
    process_commands(host, boss, "mfa remove Bea")  # type: ignore[arg-type]
    assert host.sent == ["Bea has no MFA.\n"]


def test_mfa_remove_unknown_player(tmp_path: Path) -> None:
    """An unknown name is reported before rank or secret checks."""
    save_player(tmp_path, SavedPlayer("Boss", RANK_ADMIN, hash_password("pw", rounds=4)))
    boss = _staff("Boss", RANK_ADMIN)
    host = _mfa_command_host(tmp_path)
    process_commands(host, boss, "mfa remove Bea")  # type: ignore[arg-type]
    assert host.sent == ["Player Bea not found.\n"]


def test_mfa_command_missing_when_disabled(tmp_path: Path) -> None:
    """Without the host flag, mfa is not a command."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    bea = _staff("Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    _register_mfa_command()
    process_commands(host, bea, "mfa")  # type: ignore[arg-type]
    assert host.sent == ["No such command 'mfa'\n"]


def test_a_newbie_name_enters_after_yes(tmp_path: Path) -> None:
    """A free name shows the disclaimer and enters after yes."""
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Ada")  # type: ignore[arg-type]
    assert player.player is None
    assert player.sent[-1] == "Do you agree to these rules: "
    page_line = next(line for line in player.sent if "Be kind." in line)
    assert page_line.startswith("\n\n")
    process_input(host, player, "YES")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.rank == "newbie"
    assert player.sent[-1] == "What? > "


def test_a_newbie_must_type_yes(tmp_path: Path) -> None:
    """Any other line repeats the prompt and does not draw the page again."""
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Ada")  # type: ignore[arg-type]
    process_input(host, player, "no")  # type: ignore[arg-type]
    assert player.player is None
    assert player.auth == "agree"
    assert player.sent[-1] == "Do you agree to these rules: "
    assert sum("Be kind." in line for line in player.sent) == 1
    process_input(host, player, "who")  # type: ignore[arg-type]
    assert player.sent[-1] == "Do you agree to these rules: "
    assert player.player is None


def test_quit_during_the_disclaimer_disconnects(tmp_path: Path) -> None:
    """Quit at the disclaimer leaves before the room."""
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Ada")  # type: ignore[arg-type]
    process_input(host, player, "quit")  # type: ignore[arg-type]
    assert player in host.dropped_clients
    assert player.player is None
    assert host.room_lines == []


def test_yes_refuses_a_talker_that_closed(tmp_path: Path) -> None:
    """Closing newbies while the page is up returns to the name prompt."""
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Ada")  # type: ignore[arg-type]
    host._allow_newbies = False
    process_input(host, player, "yes")  # type: ignore[arg-type]
    assert player.player is None
    assert player.auth == "name"
    assert player.sent[-1] == ("The talker is closed to new people.\nPlease enter your name: ")


def test_a_missing_disclaimer_disconnects_the_newbie(tmp_path: Path) -> None:
    """No disclaimer topic tells the newbie, drops them, and tells admins."""
    host = _login_host(tmp_path)
    host._help = HelpBook(entries=())
    admin_user = Player("Ann", "reception", datetime.now(timezone.utc), rank="admin")
    admin_user.colour = False
    admin = _Player(name="Ann", player=admin_user)
    host.people = [admin]
    player = _Player(player=None)
    process_input(host, player, "Ada")  # type: ignore[arg-type]
    assert player.sent == ["The disclaimer is missing.\n"]
    assert player in host.dropped_clients
    assert host.announced == [False]
    assert host.room_lines == []
    assert host.admin_lines == ["[ Admin ] The disclaimer is missing.\n"]
    assert player.player is None


def test_the_admin_name_sets_and_confirms_a_password(tmp_path: Path) -> None:
    """The admin name with no file asks twice, saves rank super admin, then enters."""
    host = _login_host(tmp_path, opened=False)
    player = _Player(player=None)
    process_input(host, player, "admin")  # type: ignore[arg-type]
    assert host.sent == ["Set a password: "]
    process_input(host, player, "secret")  # type: ignore[arg-type]
    assert host.sent[-1] == "\n\nConfirm password: "
    assert player.player is None
    process_input(host, player, "secret")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.rank == "super_admin"
    assert host.sent[-1] == "What? > "
    saved = load_saved(player_path(tmp_path, "admin"))
    assert saved is not None
    assert saved.rank == "super_admin"
    assert password_ok("secret", saved.password_hash)
    assert saved.colour is True
    assert "colour" not in player_path(tmp_path, "admin").read_text(encoding="utf-8")


def test_set_password_errors_return_to_the_first_prompt(tmp_path: Path) -> None:
    """Empty, long, and mismatched passwords each ask for a new password."""
    host = _login_host(tmp_path, opened=False)
    player = _Player(player=None)
    process_input(host, player, "admin")  # type: ignore[arg-type]
    process_login(host, player, "")  # type: ignore[arg-type]
    assert host.sent[-1] == "Password cannot be empty.\nSet a password: "
    process_login(host, player, "x" * 73)  # type: ignore[arg-type]
    assert host.sent[-1] == "Password is too long.\nSet a password: "
    process_login(host, player, "one")  # type: ignore[arg-type]
    process_login(host, player, "two")  # type: ignore[arg-type]
    assert host.sent[-1] == "Those passwords do not match.\nSet a password: "
    assert player.auth == "set"
    assert player.player is None
    assert load_saved(player_path(tmp_path, "admin")) is None


def test_bob_signs_in_as_the_saved_spelling(tmp_path: Path) -> None:
    """Typing bob enters as Bob, and tell and examine find that spelling."""
    save_player(tmp_path, SavedPlayer("Bob", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "bob")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.name == "Bob"
    assert player.player.lowercase_name == "bob"
    host.people = [player]
    host.sent.clear()
    teller = _newbie("Ada")
    host.people.append(teller)
    process_commands(host, teller, "tell bob hello")  # type: ignore[arg-type]
    assert any("Bob" in line for line in host.sent)
    host.sent.clear()
    process_commands(host, teller, "examine bob")  # type: ignore[arg-type]
    assert host.sent
    assert "Bob".center(72) in host.sent[0]
    assert "Player bob not found." not in host.sent[0]


def test_a_saved_name_asks_for_its_password(tmp_path: Path) -> None:
    """A matching saved file asks for the password and enters with its rank."""
    save_player(tmp_path, SavedPlayer("Bea", "resident", hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.name == "Bea"
    assert player.player.rank == "resident"
    assert host.sent[-1] == "What? > "


def test_a_saved_resident_whose_password_is_quit_can_enter(tmp_path: Path) -> None:
    """``quit`` at the password prompt is checked, not treated as disconnect."""
    save_player(tmp_path, SavedPlayer("Bea", "resident", hash_password("quit", rounds=4)))
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]
    process_input(host, player, "quit")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.name == "Bea"
    assert player.player.rank == "resident"
    assert player not in host.dropped_clients
    assert host.sent[-1] == "What? > "


def test_another_spelling_of_a_saved_name_is_refused(tmp_path: Path) -> None:
    """Enter as the saved spelling after typing another case."""
    save_player(tmp_path, SavedPlayer("Bea", "resident", hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "bea")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.name == "Bea"


def test_a_removed_player_does_not_enter_on_a_correct_password(tmp_path: Path) -> None:
    """The file is gone by the time the password arrives, so the name prompt returns."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_SUPER, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]
    assert delete_player(tmp_path, "Bea") is True
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.player is None
    assert player.auth == "name"
    assert host.sent[-1] == name_prompt()


def test_a_changed_rank_is_used_when_the_password_arrives(tmp_path: Path) -> None:
    """The rank comes from the file at the time of the correct password."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_SUPER, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    process_input(host, player, "pw")  # type: ignore[arg-type]
    assert player.player is not None
    assert player.player.rank == RANK_RESIDENT


def test_a_taken_saved_name_still_asks_for_the_password(tmp_path: Path) -> None:
    """A saved name that is in a room is not refused before the password."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    live = _Player(
        player=Player(
            "Bea",
            "hall",
            datetime(2026, 10, 12, tzinfo=timezone.utc),
            alarm_seconds=120,
            idle_seconds=40,
            prompt="Go: ",
            rank=RANK_SUPER,
        )
    )
    host.people = [live]
    newcomer = _Player(player=None, address="10.0.0.5")
    process_input(host, newcomer, "Bea")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]
    assert newcomer.player is None
    assert live.player is not None
    assert live.player.idle_seconds == 40


def test_the_right_password_reconnects_the_live_session(tmp_path: Path) -> None:
    """The same player stays, idle clears, and the room hears reconnected."""
    logged_out = datetime(2020, 1, 1, tzinfo=timezone.utc)
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            logged_out_at=logged_out,
            ip_address="192.168.0.9",
        ),
    )
    host = _login_host(tmp_path)
    connected_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
    user = Player(
        "Bea",
        "hall",
        connected_at,
        connected_seconds=500,
        alarm_seconds=120,
        idle_seconds=40,
        prompt="Go: ",
        rank=RANK_SUPER,
    )
    live = _Player(player=user, address="192.168.0.9")
    host.people = [live]
    host.occupied = 99
    newcomer = _Player(player=None, address="10.0.0.5")
    process_input(host, newcomer, "Bea")  # type: ignore[arg-type]
    host.sent.clear()
    host.room_lines.clear()
    process_input(host, newcomer, "pw")  # type: ignore[arg-type]
    assert host.replaced == [live]
    assert live.player is user
    assert user.room == "hall"
    assert user.connected_at == connected_at
    assert user.connected_seconds == 500
    assert user.alarm_seconds == 120
    assert user.prompt == "Go: "
    assert user.idle_seconds == 0
    assert user.rank == RANK_RESIDENT
    assert live.address == "10.0.0.5"
    assert newcomer.player is None
    assert host.dropped is False
    assert host.room_lines == ["[Bea has reconnected]\n"]
    assert any(text == host._layout.brief("hall", []) for text in host.sent)
    assert "has connected" not in "".join(host.room_lines)
    assert "has disconnected" not in "".join(host.room_lines)
    assert host.sent[-1] == "Go: "
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.ip_address == "10.0.0.5"
    assert saved.hostname == "10.0.0.5"
    assert saved.logged_out_at == logged_out


def test_a_reconnect_drops_an_unfinished_password_change(tmp_path: Path) -> None:
    """Lines after a reconnect are commands, not answers to the old password change."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    before = load_saved(player_path(tmp_path, "Bea"))
    assert before is not None
    host = _login_host(tmp_path)
    live = _Player(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT))
    host.people = [live]
    process_input(host, live, "password")  # type: ignore[arg-type]
    process_input(host, live, "pw")  # type: ignore[arg-type]
    assert live.auth == "renew"
    newcomer = _Player(player=None, address="10.0.0.5")
    process_input(host, newcomer, "Bea")  # type: ignore[arg-type]
    process_input(host, newcomer, "pw")  # type: ignore[arg-type]
    assert host.replaced == [live]
    assert live.auth == "name"
    assert live.pending_password == ""
    host.sent.clear()
    process_input(host, live, "who")  # type: ignore[arg-type]
    process_input(host, live, "who")  # type: ignore[arg-type]
    output = "".join(host.sent)
    assert output.count("Bea") >= 2
    assert "Password changed." not in output
    assert "Set a password" not in output
    assert "Confirm password" not in output
    after = load_saved(player_path(tmp_path, "Bea"))
    assert after is not None
    assert after.password_hash == before.password_hash


def test_a_taken_newbie_name_is_still_refused(tmp_path: Path) -> None:
    """A name with no file does not get a password just because it is in use."""
    host = _login_host(tmp_path)
    live = _Player(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_NEWBIE))
    host.people = [live]
    newcomer = _Player(player=None)
    process_input(host, newcomer, "Bea")  # type: ignore[arg-type]
    assert host.sent == ["Bea is already in use. Pick another name.\n\nPlease enter your name: "]
    assert host.replaced == []
    assert live.player is not None
    assert live.player.idle_seconds == 0


def test_another_case_reconnects_the_live_session(tmp_path: Path) -> None:
    """Reconnect the live session after another case and the right password."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    live = _Player(player=user)
    host.people = [live]
    newcomer = _Player(player=None)
    process_input(host, newcomer, "bea")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]
    process_input(host, newcomer, "pw")  # type: ignore[arg-type]
    assert host.replaced == [live]
    assert user.name == "Bea"


def test_a_wrong_password_leaves_the_live_session(tmp_path: Path) -> None:
    """Three failures drop only the new connection."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    live = _Player(
        player=Player(
            "Bea",
            "hall",
            datetime.now(timezone.utc),
            idle_seconds=40,
            rank=RANK_RESIDENT,
        )
    )
    host.people = [live]
    newcomer = _Player(player=None)
    process_input(host, newcomer, "Bea")  # type: ignore[arg-type]
    for _times in range(3):
        process_input(host, newcomer, "no")  # type: ignore[arg-type]
    assert host.dropped_clients == [newcomer]
    assert live not in host.dropped_clients
    assert live.player is not None
    assert live.player.idle_seconds == 40
    assert host.replaced == []
    assert host.room_lines == []


def test_a_missing_file_does_not_reconnect(tmp_path: Path) -> None:
    """The file is gone when the password arrives, so the live player stays."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    live = _Player(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT))
    host.people = [live]
    newcomer = _Player(player=None)
    process_input(host, newcomer, "Bea")  # type: ignore[arg-type]
    assert delete_player(tmp_path, "Bea") is True
    process_input(host, newcomer, "pw")  # type: ignore[arg-type]
    assert host.replaced == []
    assert newcomer.player is None
    assert newcomer.auth == "name"
    assert live.player is not None
    assert live.player.room == "hall"


def test_a_saved_name_that_has_left_still_connects(tmp_path: Path) -> None:
    """No live player means a normal connect line."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    newcomer = _Player(player=None, address="10.0.0.5")
    process_input(host, newcomer, "Bea")  # type: ignore[arg-type]
    process_input(host, newcomer, "pw")  # type: ignore[arg-type]
    assert host.replaced == []
    assert newcomer.player is not None
    assert newcomer.player.room == "reception"
    assert host.room_lines == ["[Bea has connected]\n"]


def test_the_admin_name_in_another_case_is_not_a_newbie(tmp_path: Path) -> None:
    """Treat another spelling of the admin name as that admin."""
    host = _login_host(tmp_path, opened=False)
    player = _Player(player=None)
    process_input(host, player, "Admin")  # type: ignore[arg-type]
    assert host.sent == ["Set a password: "]
    assert player.pending_name == "Admin"
    assert player.player is None
    assert player.auth == "set"

    remote_host = _login_host(tmp_path, admin="Boss", opened=False)
    for typed in ("boss", "BOSS"):
        remote_host.sent.clear()
        remote = _Player(player=None, address="10.0.0.5")
        process_input(remote_host, remote, typed)  # type: ignore[arg-type]
        assert remote_host.sent == [admin_gate_reply()]
        assert remote.player is None
        assert remote.auth == "name"

    host = _login_host(tmp_path)
    for typed in ("Admin", "ADMIN"):
        host.sent.clear()
        player = _Player(player=None)
        process_input(host, player, typed)  # type: ignore[arg-type]
        assert host.sent == ["Password: "]
        assert player.pending_name == "admin"
        assert player.player is None
        assert player.auth == "password"

    host = _login_host(tmp_path, admin="Boss")
    player = _Player(player=None)
    process_input(host, player, "boss")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]
    assert player.pending_name == "Boss"
    assert player.player is None


def test_remote_login_waits_until_the_admin_password_exists(tmp_path: Path) -> None:
    """Allow only localhost to choose the admin password before the file exists."""
    host = _login_host(tmp_path, opened=False)
    remote = _Player(player=None, address="10.0.0.5")
    process_input(host, remote, "admin")  # type: ignore[arg-type]
    assert host.sent == [admin_gate_reply()]
    assert remote.player is None
    assert remote.auth == "name"
    host.sent.clear()
    local_other = _Player(player=None, address="127.0.0.1")
    process_input(host, local_other, "Bea")  # type: ignore[arg-type]
    assert host.sent == [admin_gate_reply()]
    host.sent.clear()
    other_case = _Player(player=None, address="::1")
    process_input(host, other_case, "Admin")  # type: ignore[arg-type]
    assert host.sent == ["Set a password: "]
    host.sent.clear()
    local = _Player(player=None, address="127.0.0.1")
    process_input(host, local, "admin")  # type: ignore[arg-type]
    assert host.sent == ["Set a password: "]
    assert local.auth == "set"
    host.people.append(local)
    host.sent.clear()
    second = _Player(player=None, address="127.0.0.1")
    process_input(host, second, "ADMIN")  # type: ignore[arg-type]
    assert host.sent == [name_taken_reply("ADMIN")]
    assert load_saved(player_path(tmp_path, "admin")) is None
    host.drop(local, announce=False)
    host.sent.clear()
    retry = _Player(player=None, address="::1")
    process_input(host, retry, "admin")  # type: ignore[arg-type]
    assert host.sent == ["Set a password: "]
    process_input(host, retry, "secret")  # type: ignore[arg-type]
    process_input(host, retry, "secret")  # type: ignore[arg-type]
    assert retry.player is not None
    host.sent.clear()
    remote_name = _Player(player=None, address="10.0.0.5")
    process_input(host, remote_name, "Bea")  # type: ignore[arg-type]
    process_input(host, remote_name, "yes")  # type: ignore[arg-type]
    assert remote_name.player is not None
    assert remote_name.player.name == "Bea"
    host.sent.clear()
    remote_admin = _Player(player=None, address="10.0.0.5")
    process_input(host, remote_admin, "admin")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]


def test_resident_of_the_admin_name_starts_no_pair(tmp_path: Path) -> None:
    """The admin in any case is refused, and nobody is asked for a password."""
    admin = _newbie("admin")
    host = _login_host(tmp_path)
    host.people = [admin]
    ada = _staff("Ada", "super")
    for line in ("resident admin", "resident Admin", "resident ADMIN"):
        host.sent.clear()
        process_commands(host, ada, line)  # type: ignore[arg-type]
        assert host.sent == ["The admin cannot be removed.\n"]
    assert host.staff_lines == []
    assert admin.auth == "name"


def test_three_wrong_passwords_drop_the_player(tmp_path: Path) -> None:
    """Each wrong password is answered. The third one closes the connection."""
    save_player(tmp_path, SavedPlayer("Bea", "resident", hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "Bea")  # type: ignore[arg-type]
    process_input(host, player, "no")  # type: ignore[arg-type]
    assert host.sent[-2:] == ["Wrong password.\n", "Password: "]
    process_input(host, player, "no")  # type: ignore[arg-type]
    assert host.dropped is False
    process_input(host, player, "no")  # type: ignore[arg-type]
    assert host.sent[-1] == "Wrong password.\n"
    assert host.dropped is True
    assert player.player is None


def test_a_name_that_cannot_be_a_file_is_a_newbie(tmp_path: Path) -> None:
    """A name with path characters never reaches the players directory."""
    host = _login_host(tmp_path)
    player = _Player(player=None)
    process_input(host, player, "../evil")  # type: ignore[arg-type]
    assert player.player is None
    assert host.sent[-1] == ("A name can use only letters and digits.\n\nPlease enter your name: ")


def _staff(name: str, rank: str) -> _Player:
    user = Player(name, "reception", datetime.now(timezone.utc), rank=rank)
    return _Player(name=name, player=user)


def test_another_capitalisation_of_a_newbie_is_in_use(tmp_path: Path) -> None:
    """Bea and BEA are the newbie already in the room."""
    host = _login_host(tmp_path)
    bea = _Player(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_NEWBIE))
    host.people = [bea]
    for typed in ("bea", "BEA"):
        host.sent.clear()
        newcomer = _Player(player=None)
        process_input(host, newcomer, typed)  # type: ignore[arg-type]
        assert host.sent == [f"{typed} is already in use. Pick another name.\n\n{name_prompt()}"]
        assert newcomer.player is None
    assert bea.player is not None
    assert bea.player.name == "Bea"


def test_a_newbie_who_quits_frees_the_name(tmp_path: Path) -> None:
    """After quit, the other capitalisation can enter, and no file is written."""
    host = _login_host(tmp_path)
    bea = _Player(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_NEWBIE))
    host.people = [bea]
    process_input(host, bea, "quit")  # type: ignore[arg-type]
    assert bea in host.dropped_clients
    host.sent.clear()
    later = _Player(player=None)
    process_input(host, later, "bea")  # type: ignore[arg-type]
    process_input(host, later, "yes")  # type: ignore[arg-type]
    assert later.player is not None
    assert later.player.name == "bea"
    assert later.player.rank == RANK_NEWBIE
    assert load_saved(player_path(tmp_path, "bea")) is None


def test_a_newbie_setting_a_resident_password_still_holds_the_name(tmp_path: Path) -> None:
    """The name stays taken until confirm writes the file."""
    host = _login_host(tmp_path)
    bea = _Player(player=Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_NEWBIE))
    host.people = [bea]
    issuer = _staff("Ada", RANK_SUPER)
    host.people.append(issuer)
    process_commands(host, issuer, "resident Bea")  # type: ignore[arg-type]
    assert bea.auth == "set"
    newcomer = _Player(player=None)
    process_input(host, newcomer, "bea")  # type: ignore[arg-type]
    assert host.sent[-1] == f"bea is already in use. Pick another name.\n\n{name_prompt()}"
    assert load_saved(player_path(tmp_path, "Bea")) is None
    process_input(host, bea, "quit")  # type: ignore[arg-type]
    host.sent.clear()
    process_input(host, newcomer, "bea")  # type: ignore[arg-type]
    process_input(host, newcomer, "yes")  # type: ignore[arg-type]
    assert newcomer.player is not None
    assert newcomer.player.name == "bea"
    assert load_saved(player_path(tmp_path, "bea")) is None


def test_a_saved_player_at_the_password_prompt_does_not_hold_the_name(
    tmp_path: Path,
) -> None:
    """Ask for the password again, including another capitalisation."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    waiting = _Player(player=None, address="10.0.0.5")
    host.people = [waiting]
    process_input(host, waiting, "Bea")  # type: ignore[arg-type]
    assert waiting.auth == "password"
    host.sent.clear()
    other = _Player(player=None, address="10.0.0.5")
    process_input(host, other, "Bea")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]
    host.sent.clear()
    spelling = _Player(player=None, address="10.0.0.5")
    process_input(host, spelling, "bea")  # type: ignore[arg-type]
    assert host.sent == ["Password: "]


def _newbie(name: str) -> _Player:
    return _Player(
        name=name,
        room="hall",
        player=Player(name, "hall", datetime.now(timezone.utc)),
    )


def _save(tmp_path: Path, name: str, rank: str) -> None:
    save_player(tmp_path, SavedPlayer(name, rank, hash_password("pw", rounds=4)))


def test_resident_saves_a_connected_newbie(tmp_path: Path) -> None:
    """The newbie is asked for a password pair, and is saved only after the confirm."""
    bea = _newbie("Bea")
    ada = _staff("Ada", "super")
    host = _login_host(tmp_path)
    host.people = [bea, ada]
    process_commands(host, ada, "resident Bea")  # type: ignore[arg-type]
    assert host.staff_lines == [paint_staff(ada, "< SU > Ada makes Bea a resident.\n")]
    assert host.sent == ["Set a password: "]
    assert bea.auth == "set"
    assert bea.player is not None
    assert bea.player.rank == "newbie"
    assert load_saved(player_path(tmp_path, "Bea")) is None

    process_input(host, bea, "hunter2")  # type: ignore[arg-type]
    assert host.sent[-1] == "\n\nConfirm password: "
    assert bea.player.rank == "newbie"
    assert load_saved(player_path(tmp_path, "Bea")) is None

    process_input(host, bea, "hunter2")  # type: ignore[arg-type]
    assert bea.player.rank == "resident"
    assert bea.auth == "name"
    assert host.sent[-3:] == ["You are now a resident.\n", "Bea is now a resident.\n", "What? > "]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "resident"
    assert password_ok("hunter2", saved.password_hash)


def test_resident_staff_line_is_sent_at_command_time_only(tmp_path: Path) -> None:
    """Staff hear who made the resident once; promotion does not broadcast again."""
    bea = _newbie("Bea")
    ada = _staff("Ada", "super")
    host = _login_host(tmp_path)
    host.people = [bea, ada]
    process_commands(host, ada, "resident Bea")  # type: ignore[arg-type]
    staff_line = paint_staff(ada, "< SU > Ada makes Bea a resident.\n")
    assert host.staff_lines == [staff_line]
    process_input(host, bea, "hunter2")  # type: ignore[arg-type]
    process_input(host, bea, "hunter2")  # type: ignore[arg-type]
    assert host.staff_lines == [staff_line]
    assert "Bea is now a resident.\n" in ada.sent


def test_resident_pair_refuses_a_bad_password(tmp_path: Path) -> None:
    """A mismatch returns to the first prompt and still writes nothing."""
    bea = _newbie("Bea")
    host = _login_host(tmp_path)
    host.people = [bea]
    process_commands(host, _staff("Ada", "super"), "resident Bea")  # type: ignore[arg-type]
    process_input(host, bea, "")  # type: ignore[arg-type]
    assert host.sent[-1] == "Password cannot be empty.\nSet a password: "
    process_input(host, bea, "one")  # type: ignore[arg-type]
    process_input(host, bea, "two")  # type: ignore[arg-type]
    assert host.sent[-1] == "Those passwords do not match.\nSet a password: "
    assert bea.auth == "set"
    assert bea.player is not None
    assert bea.player.rank == "newbie"
    assert load_saved(player_path(tmp_path, "Bea")) is None


def test_resident_refusals(tmp_path: Path) -> None:
    """Each refusal names the typed name and starts no password pair."""
    _save(tmp_path, "Cyd", RANK_RESIDENT)
    odd = _newbie("a/b")
    host = _login_host(tmp_path)
    host.people = [odd, _newbie("Bea")]
    ada = _staff("Ada", "super")
    for line, reply in (
        ("resident Zed", "Player Zed not found.\n"),
        ("resident Cyd", "Cyd is already saved.\n"),
        ("resident cyd", "cyd is already saved.\n"),
        ("resident a/b", "That name cannot be saved.\n"),
    ):
        host.sent.clear()
        process_commands(host, ada, line)  # type: ignore[arg-type]
        assert host.sent == [reply]
    assert host.staff_lines == []
    assert all(person.auth == "name" for person in host.people)
    assert odd.auth == "name"


def test_resident_needs_super_and_a_name(tmp_path: Path) -> None:
    """A resident does not see the command. Super sees the syntax with no name."""
    host = _login_host(tmp_path)
    resident = _staff("Ada", RANK_RESIDENT)
    process_commands(host, resident, "resident Bea")  # type: ignore[arg-type]
    assert host.sent == ["No such command 'resident'\n"]
    host = _login_host(tmp_path)
    process_commands(host, _staff("Ada", RANK_SUPER), "resident")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: resident <name>\n"]


def test_nuke_deletes_the_file(tmp_path: Path) -> None:
    """Nuking an offline resident deletes the file and says so to staff."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", "super")
    host.people = [ada]
    process_commands(host, ada, "nuke Bea spam")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")) is None
    assert host.staff_lines == [paint_staff(ada, "< SU > Ada nukes Bea: spam\n")]
    assert host.dropped_clients == []


def test_nuke_deletes_a_saved_name_with_a_space(tmp_path: Path) -> None:
    """A saved name with a space is the whole name, and the rest is the reason."""
    save_player(
        tmp_path,
        SavedPlayer("BeaSmith", RANK_RESIDENT, hash_password("pw", rounds=4)),
    )
    host = _login_host(tmp_path)
    ada = _staff("Ada", "super")
    host.people = [ada]
    process_commands(host, ada, "nuke Bea")  # type: ignore[arg-type]
    assert host.sent[-1] == "Syntax: nuke <name> <reason>\n"
    assert load_saved(player_path(tmp_path, "BeaSmith")) is not None
    process_commands(host, ada, "nuke BeaSmith")  # type: ignore[arg-type]
    assert host.sent[-1] == "Syntax: nuke <name> <reason>\n"
    assert load_saved(player_path(tmp_path, "BeaSmith")) is not None
    process_commands(host, ada, "nuke BeaSmith spam")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "BeaSmith")) is None
    assert host.staff_lines == [paint_staff(ada, "< SU > Ada nukes BeaSmith: spam\n")]


def test_nuke_removes_a_connected_player(tmp_path: Path) -> None:
    """A connected target is told, then dropped with the disconnect line."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    bea = _staff("Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", "super")
    host.people = [bea, ada]
    process_commands(host, ada, "nuke bea  being rude")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")) is None
    assert host.sent == ["You have been removed.\n"]
    assert host.dropped_clients == [bea]
    assert host.staff_lines == [paint_staff(ada, "< SU > Ada nukes Bea: being rude\n")]


def test_nuke_refuses_a_connected_newbie(tmp_path: Path) -> None:
    """A connected newbie has no saved file, so nuke leaves them in place."""
    bea = _newbie("Bea")
    host = _login_host(tmp_path)
    host.people = [bea]
    process_commands(host, _staff("Ada", "super"), "nuke Bea spam")  # type: ignore[arg-type]
    assert host.sent == ["Player Bea not found.\n"]
    assert bea in host.people
    assert bea.player is not None
    assert host.dropped_clients == []
    assert host.staff_lines == []


def test_disconnect_refuses_yourself() -> None:
    """Disconnect aimed at the typist does not drop them."""
    host = _Host()
    ada = _staff("Ada", RANK_SUPER)
    host.people = [ada]
    command_disconnect(
        host,
        ada,
        Command("disconnect", command_disconnect, False),
        "ada spam",
    )
    assert ada.sent == ["You cannot do that to yourself.\n"]
    assert host.dropped_clients == []


def test_warn_syntax_without_reason() -> None:
    """Warn needs a name and a reason."""
    host = _Host()
    ada = _staff("Ada", RANK_SUPER)
    host.people = [ada]
    command_warn(
        host,
        ada,
        Command("warn", command_warn, False, "warn <name> <reason>"),
        "Bea",
    )
    assert ada.sent == ["Syntax: warn <name> <reason>\n"]


def test_warn_unknown_player() -> None:
    """Warn misses when the full name is not in a room."""
    host = _Host()
    ada = _staff("Ada", RANK_SUPER)
    host.people = [ada]
    command_warn(
        host,
        ada,
        Command("warn", command_warn, False, "warn <name> <reason>"),
        "Bea stop that",
    )
    assert ada.sent == ["Player Bea not found.\n"]
    assert host.staff_lines == []


def test_warn_refuses_yourself() -> None:
    """Warn aimed at the typist does not message staff."""
    host = _Host()
    ada = _staff("Ada", RANK_SUPER)
    host.people = [ada]
    command_warn(
        host,
        ada,
        Command("warn", command_warn, False, "warn <name> <reason>"),
        "Ada stop that",
    )
    assert ada.sent == ["You cannot do that to yourself.\n"]
    assert host.staff_lines == []


def test_warn_rejects_a_name_prefix() -> None:
    """Warn needs the full name, not a prefix."""
    host = _Host()
    ada = _staff("Ada", RANK_SUPER)
    bea = _staff("Bea", RANK_RESIDENT)
    host.people = [ada, bea]
    command_warn(
        host,
        ada,
        Command("warn", command_warn, False, "warn <name> <reason>"),
        "Be stop that",
    )
    assert ada.sent == ["Player Be not found.\n"]
    assert bea.sent == []
    assert host.staff_lines == []


def test_warn_sends_a_cyan_line_and_a_beep() -> None:
    """The target sees cyan when colour is on, plain text when it is off."""
    host = _Host()
    ada = _staff("Ada", RANK_SUPER)
    bea = _staff("Bea", RANK_RESIDENT)
    host.people = [ada, bea]
    assert ada.player is not None
    ada.player.colour = False
    assert bea.player is not None
    bea.player.colour = True
    command_warn(
        host,
        ada,
        Command("warn", command_warn, False, "warn <name> <reason>"),
        "Bea stop that",
    )
    plain = "-=> Ada warn you: stop that"
    assert bea.sent == [f"\n\n{cyan(plain)}\n\n\n\a"]
    assert ada.sent == []
    assert host.staff_lines == ["< SU > Ada warns Bea : stop that\n"]

    host.staff_lines.clear()
    bea.sent.clear()
    bea.player.colour = False
    command_warn(
        host,
        ada,
        Command("warn", command_warn, False, "warn <name> <reason>"),
        "Bea stop that",
    )
    assert bea.sent == [f"\n\n{plain}\n\n\n\a"]
    assert ada.sent == []
    assert host.staff_lines == ["< SU > Ada warns Bea : stop that\n"]


def test_resident_refuses_yourself() -> None:
    """Resident aimed at the typist does not start a password pair."""
    host = _Host()
    ada = _Player()
    host.people = [ada]
    command_resident(host, ada, Command("resident", command_resident, False), "ada")
    assert ada.sent == ["You cannot do that to yourself.\n"]
    assert host.staff_lines == []
    assert ada.auth == "name"


def test_nuke_refuses_yourself(tmp_path: Path) -> None:
    """Nuke aimed at the typist does not delete their file."""
    _save(tmp_path, "Ada", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    host.people = [ada]
    command_nuke(host, ada, Command("nuke", command_nuke, False), "ada gone")
    assert ada.sent == ["You cannot do that to yourself.\n"]
    assert load_saved(player_path(tmp_path, "Ada")) is not None
    assert host.staff_lines == []


def test_promote_refuses_yourself(tmp_path: Path) -> None:
    """Promote aimed at the typist does not raise their rank."""
    _save(tmp_path, "Ada", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_ADMIN)
    host.people = [ada]
    command_promote(host, ada, Command("promote", command_promote, False), "ada")
    assert ada.sent == ["You cannot do that to yourself.\n"]
    saved = load_saved(player_path(tmp_path, "Ada"))
    assert saved is not None
    assert saved.rank == RANK_RESIDENT
    assert host.staff_lines == []


def test_demote_refuses_yourself(tmp_path: Path) -> None:
    """Demote aimed at the typist does not lower their rank."""
    _save(tmp_path, "Ada", RANK_SUPER)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_ADMIN)
    host.people = [ada]
    command_demote(host, ada, Command("demote", command_demote, False), "ada")
    assert ada.sent == ["You cannot do that to yourself.\n"]
    saved = load_saved(player_path(tmp_path, "Ada"))
    assert saved is not None
    assert saved.rank == RANK_SUPER
    assert host.staff_lines == []


def test_disconnect_drops_a_resident_and_keeps_the_file(tmp_path: Path) -> None:
    """Staff are told, the resident is told, and the file stays."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    bea = _staff("Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    host.people = [bea, ada]
    process_commands(host, ada, "disconnect Bea spam")  # type: ignore[arg-type]
    assert host.sent == ["You have been disconnected.\n"]
    assert host.dropped_clients == [bea]
    assert host.announced == [True]
    assert host.staff_lines == [paint_staff(ada, "< SU > Ada disconnects Bea: spam\n")]
    assert load_saved(player_path(tmp_path, "Bea")) is not None


def test_disconnect_refuses_the_same_rank_or_higher(tmp_path: Path) -> None:
    """A super user cannot drop a super user, the admin, or themselves."""
    host = _login_host(tmp_path)
    cy = _staff("Cyd", RANK_SUPER)
    boss = _staff("Boss", RANK_ADMIN)
    ada = _staff("Ada", RANK_SUPER)
    host.people = [cy, boss, ada]
    process_commands(host, ada, "disconnect Cyd spam")  # type: ignore[arg-type]
    assert host.sent == ["You cannot disconnect Cyd.\n"]
    host.sent.clear()
    process_commands(host, ada, "disconnect Boss spam")  # type: ignore[arg-type]
    assert host.sent == ["You cannot disconnect Boss.\n"]
    host.sent.clear()
    process_commands(host, ada, "disconnect Ada spam")  # type: ignore[arg-type]
    assert host.sent == ["You cannot do that to yourself.\n"]
    assert host.dropped_clients == []
    assert host.staff_lines == []


def test_an_admin_can_disconnect_a_super_user_but_not_themselves(tmp_path: Path) -> None:
    """A super user is below an admin and is dropped. An admin cannot drop themselves."""
    host = _login_host(tmp_path)
    cy = _staff("Cyd", RANK_SUPER)
    boss = _staff("Boss", RANK_ADMIN)
    host.people = [cy, boss]
    process_commands(host, boss, "disconnect Cyd spam")  # type: ignore[arg-type]
    assert host.sent == ["You have been disconnected.\n"]
    assert host.dropped_clients == [cy]
    host.sent.clear()
    process_commands(host, boss, "disconnect Boss spam")  # type: ignore[arg-type]
    assert host.sent == ["You cannot do that to yourself.\n"]
    assert host.dropped_clients == [cy]


def test_nuke_refuses_the_same_rank_or_higher(tmp_path: Path) -> None:
    """A super user or a promoted admin cannot nuke their rank or above."""
    _save(tmp_path, "Bea", RANK_ADMIN)
    _save(tmp_path, "Cyd", RANK_SUPER)
    host = _login_host(tmp_path)
    sue = _staff("Sue", RANK_SUPER)
    boss = _staff("Boss", RANK_ADMIN)
    host.people = [sue, boss]
    process_commands(host, sue, "nuke Bea spam")  # type: ignore[arg-type]
    assert host.sent == ["You cannot nuke Bea.\n"]
    host.sent.clear()
    process_commands(host, sue, "nuke Cyd spam")  # type: ignore[arg-type]
    assert host.sent == ["You cannot nuke Cyd.\n"]
    host.sent.clear()
    process_commands(host, boss, "nuke Bea spam")  # type: ignore[arg-type]
    assert host.sent == ["You cannot nuke Bea.\n"]
    assert load_saved(player_path(tmp_path, "Bea")) is not None
    assert load_saved(player_path(tmp_path, "Cyd")) is not None
    assert host.staff_lines == []


def test_the_config_admin_can_nuke_another_admin(tmp_path: Path) -> None:
    """The account named in the config may nuke another admin."""
    _save(tmp_path, "Bea", RANK_ADMIN)
    host = _login_host(tmp_path)
    boss = _staff("admin", RANK_SUPER_ADMIN)
    host.people = [boss]
    process_commands(host, boss, "nuke Bea spam")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")) is None
    assert host.staff_lines == [paint_staff(boss, "< SU > admin nukes Bea: spam\n")]


def test_a_promoted_admin_cannot_disconnect_another_admin(tmp_path: Path) -> None:
    """Same rank is refused when the caller is not the config admin."""
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_ADMIN)
    boss = _staff("Boss", RANK_ADMIN)
    host.people = [bea, boss]
    process_commands(host, boss, "disconnect Bea spam")  # type: ignore[arg-type]
    assert host.sent == ["You cannot disconnect Bea.\n"]
    assert host.dropped_clients == []


def test_the_config_admin_can_disconnect_another_admin(tmp_path: Path) -> None:
    """The account named in the config may disconnect another admin."""
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_ADMIN)
    boss = _staff("admin", RANK_SUPER_ADMIN)
    host.people = [bea, boss]
    process_commands(host, boss, "disconnect Bea spam")  # type: ignore[arg-type]
    assert host.sent == ["You have been disconnected.\n"]
    assert host.dropped_clients == [bea]


def test_disconnect_of_a_login_name_is_not_found(tmp_path: Path) -> None:
    """A name that is only at a password prompt is not a target."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    waiting = _Player(name=None, room=None, player=None)
    process_input(host, waiting, "Bea")  # type: ignore[arg-type]
    assert waiting.auth == "password"
    host.people = [waiting]
    host.sent.clear()
    process_commands(host, _staff("Ada", RANK_SUPER), "disconnect Bea spam")  # type: ignore[arg-type]
    assert host.sent == ["Player Bea not found.\n"]
    assert host.dropped_clients == []


def test_disconnect_finds_a_name_with_a_space(tmp_path: Path) -> None:
    """The longest in-room name is the player, and the rest is the reason."""
    bea = _Player(player=Player("BeaSmith", "hall", datetime.now(timezone.utc), rank=RANK_NEWBIE))
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_ADMIN)
    host.people = [bea, ada]
    process_commands(
        host,  # type: ignore[arg-type]
        ada,  # type: ignore[arg-type]
        "disconnect BeaSmith being rude",
    )
    assert host.sent == ["You have been disconnected.\n"]
    assert host.staff_lines == [paint_staff(ada, "< SU > Ada disconnects BeaSmith: being rude\n")]
    assert host.dropped_clients == [bea]


def test_a_resident_cannot_disconnect(tmp_path: Path) -> None:
    """The command does not exist below super user."""
    host = _login_host(tmp_path)
    process_commands(host, _staff("Bea", RANK_RESIDENT), "disconnect Ada spam")  # type: ignore[arg-type]
    assert host.sent == ["No such command 'disconnect'\n"]
    assert "disconnect" not in command_words(RANK_RESIDENT).split()
    assert "disconnect" not in command_words(RANK_NEWBIE).split()
    assert "disconnect" in command_words(RANK_SUPER).split()
    assert "disconnect" in command_words(RANK_ADMIN).split()


def test_a_promoted_admin_cannot_nuke_another_admin(tmp_path: Path) -> None:
    """Same rank is refused, and the super admin can nuke a promoted admin."""
    _save(tmp_path, "Bea", RANK_ADMIN)
    host = _login_host(tmp_path)
    boss = _staff("Boss", RANK_ADMIN)
    host.people = [boss]
    process_commands(host, boss, "nuke Bea spam")  # type: ignore[arg-type]
    assert host.sent == ["You cannot nuke Bea.\n"]
    assert load_saved(player_path(tmp_path, "Bea")) is not None
    host.sent.clear()
    chief = _staff("admin", RANK_SUPER_ADMIN)
    host.people.append(chief)
    process_commands(host, chief, "nuke Bea spam")  # type: ignore[arg-type]
    assert load_saved(player_path(tmp_path, "Bea")) is None


def test_nuke_refusals(tmp_path: Path) -> None:
    """Unknown names and the admin are refused, and the admin file stays."""
    _save(tmp_path, "admin", RANK_SUPER_ADMIN)
    host = _login_host(tmp_path)
    ada = _staff("Ada", "super")
    process_commands(host, ada, "nuke Bea spam")  # type: ignore[arg-type]
    process_commands(host, ada, "nuke admin spam")  # type: ignore[arg-type]
    process_commands(host, ada, "nuke ADMIN spam")  # type: ignore[arg-type]
    assert host.sent == [
        "Player Bea not found.\n",
        "You cannot nuke admin.\n",
        "You cannot nuke admin.\n",
    ]
    assert load_saved(player_path(tmp_path, "admin")) is not None
    assert host.staff_lines == []
    process_commands(host, ada, "nuke Bea")  # type: ignore[arg-type]
    assert host.sent[-1] == "Syntax: nuke <name> <reason>\n"


def test_promote_refuses_a_logged_off_player(tmp_path: Path) -> None:
    """Promote runs only when that player is in a room."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_ADMIN)
    host.people = [ada]
    process_commands(host, ada, "promote Bea")  # type: ignore[arg-type]
    assert host.sent == ["Bea is not connected.\n"]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "resident"


def test_promote_refuses_a_connected_player_with_no_mfa(tmp_path: Path) -> None:
    """A resident in the room still needs a secret when MFA is on."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    host._mfa_enabled = True
    bea = _staff("Bea", RANK_RESIDENT)
    ada = _staff("Ada", RANK_ADMIN)
    host.people = [bea, ada]
    process_commands(host, ada, "promote Bea")  # type: ignore[arg-type]
    assert host.sent == ["Bea must turn on MFA first.\n"]


def test_demote_a_logged_off_super_user_still_writes_resident(tmp_path: Path) -> None:
    """Demote still rewrites the file when the target is not in a room."""
    _save(tmp_path, "Bea", RANK_SUPER)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_ADMIN)
    host.people = [ada]
    process_commands(host, ada, "demote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "resident"


def test_promote_makes_a_resident_a_super_user(tmp_path: Path) -> None:
    """Promote rewrites the file and raises the live rank."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    bea = _staff("Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_ADMIN)
    host.people = [bea, ada]
    process_commands(host, ada, "promote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "super"
    assert bea.player is not None
    assert bea.player.rank == "super"
    promoted = "< SU > Ada promotes Bea to Super User.\n"
    assert host.staff_lines == [paint_staff(ada, promoted), paint_staff(bea, promoted)]


def test_promote_makes_a_super_user_an_admin(tmp_path: Path) -> None:
    """The config admin lifts a super user to admin on the admin channel only."""
    from lupaxa.chat_server.channel import paint_admin

    _save(tmp_path, "Bea", RANK_SUPER)
    bea = _staff("Bea", RANK_SUPER)
    sue = _staff("Sue", RANK_SUPER)
    boss = _staff("admin", RANK_SUPER_ADMIN)
    host = _login_host(tmp_path)
    host.people = [bea, sue, boss]
    process_commands(host, boss, "promote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "admin"
    assert bea.player is not None
    assert bea.player.rank == "admin"
    line = "[ Admin ] admin promotes Bea to Admin.\n"
    assert host.admin_lines == [paint_admin(boss, line), paint_admin(bea, line)]
    assert host.staff_lines == []


def test_a_promoted_admin_cannot_promote_to_admin(tmp_path: Path) -> None:
    """A promoted admin may not raise a super user to admin."""
    _save(tmp_path, "Bea", RANK_SUPER)
    bea = _staff("Bea", RANK_SUPER)
    ada = _staff("Ada", RANK_ADMIN)
    host = _login_host(tmp_path)
    host.people = [bea, ada]
    process_commands(host, ada, "promote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "super"
    assert bea.player is not None
    assert bea.player.rank == "super"
    assert host.sent == ["Only the admin can promote Bea to Admin.\n"]
    assert host.admin_lines == []
    assert host.staff_lines == []


def test_promote_refusals(tmp_path: Path) -> None:
    """Promote needs the admin and a saved resident."""
    _save(tmp_path, "Cyd", RANK_ADMIN)
    _save(tmp_path, "admin", RANK_ADMIN)
    host = _login_host(tmp_path)
    host.people = [_newbie("Dee")]
    boss = _staff("Ada", RANK_ADMIN)
    for line, reply in (
        ("promote Zed", "Player Zed not found.\n"),
        ("promote Dee", "Dee is not a resident.\n"),
        ("promote Cyd", "Cyd is already an admin.\n"),
        ("promote admin", "admin is the super admin.\n"),
    ):
        host.sent.clear()
        process_commands(host, boss, line)  # type: ignore[arg-type]
        assert host.sent == [reply]
    assert host.staff_lines == []
    host = _login_host(tmp_path)
    process_commands(host, _staff("Sam", RANK_SUPER), "promote Cyd")  # type: ignore[arg-type]
    assert host.sent == ["No such command 'promote'\n"]


def test_demote_makes_a_super_user_a_resident(tmp_path: Path) -> None:
    """Demote rewrites the file and lowers the live rank."""
    _save(tmp_path, "Bea", RANK_SUPER)
    bea = _staff("Bea", RANK_SUPER)
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_ADMIN)
    host.people = [bea, ada]
    process_commands(host, ada, "demote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "resident"
    assert bea.player is not None
    assert bea.player.rank == "resident"
    demoted = "< SU > Ada demotes Bea from Super User.\n"
    assert host.staff_lines == [paint_staff(ada, demoted)]


def test_demote_makes_an_admin_a_super_user(tmp_path: Path) -> None:
    """The config admin drops an admin to super user on the admin channel only."""
    _save(tmp_path, "Bea", RANK_ADMIN)
    bea = _staff("Bea", RANK_ADMIN)
    sue = _staff("Sue", RANK_SUPER)
    boss = _staff("admin", RANK_SUPER_ADMIN)
    host = _login_host(tmp_path)
    host.people = [bea, sue, boss]
    process_commands(host, boss, "demote Bea")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "super"
    assert bea.player is not None
    assert bea.player.rank == "super"
    line = "[ Admin ] admin demotes Bea from Admin.\n"
    assert host.admin_lines == [paint_admin(boss, line)]
    assert host.staff_lines == []


def test_a_promoted_admin_cannot_demote_an_admin(tmp_path: Path) -> None:
    """A promoted admin may not move an admin down, with or without force."""
    _save(tmp_path, "Bea", RANK_ADMIN)
    bea = _staff("Bea", RANK_ADMIN)
    ada = _staff("Ada", RANK_ADMIN)
    host = _login_host(tmp_path)
    host.people = [bea, ada]
    process_commands(host, ada, "demote Bea")  # type: ignore[arg-type]
    assert host.sent == ["Only the admin can demote Bea from Admin.\n"]
    host.sent.clear()
    process_commands(host, ada, "demote Bea force")  # type: ignore[arg-type]
    assert host.sent == ["Only the admin can demote Bea from Admin.\n"]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "admin"
    assert bea.player is not None
    assert bea.player.rank == "admin"
    assert host.admin_lines == []
    assert host.staff_lines == []


def test_demote_force_from_admin_tells_both_channels(tmp_path: Path) -> None:
    """Force from admin lands on resident and is heard by staff and admins."""
    _save(tmp_path, "Bea", RANK_ADMIN)
    bea = _staff("Bea", RANK_ADMIN)
    sue = _staff("Sue", RANK_SUPER)
    boss = _staff("admin", RANK_SUPER_ADMIN)
    host = _login_host(tmp_path)
    host.people = [bea, sue, boss]
    process_commands(host, boss, "demote Bea FORCE")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "resident"
    assert bea.player is not None
    assert bea.player.rank == "resident"
    staff = "< SU > admin demotes Bea from Admin.\n"
    admin = "[ Admin ] admin demotes Bea from Admin.\n"
    assert host.staff_lines == [paint_staff(sue, staff), paint_staff(boss, staff)]
    assert host.admin_lines == [paint_admin(boss, admin)]


def test_demote_force_from_super_user_stays_on_staff(tmp_path: Path) -> None:
    """Force from super user is the normal super-user demote."""
    _save(tmp_path, "Bea", RANK_SUPER)
    bea = _staff("Bea", RANK_SUPER)
    ada = _staff("Ada", RANK_ADMIN)
    host = _login_host(tmp_path)
    host.people = [bea, ada]
    process_commands(host, ada, "demote Bea force")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.rank == "resident"
    assert bea.player.rank == "resident"
    line = "< SU > Ada demotes Bea from Super User.\n"
    assert host.staff_lines == [paint_staff(ada, line)]
    assert host.admin_lines == []


def test_demote_refusals(tmp_path: Path) -> None:
    """Demote refuses missing names, residents, newbies, the config admin, and bad force syntax."""
    _save(tmp_path, "Cyd", RANK_RESIDENT)
    _save(tmp_path, "admin", RANK_ADMIN)
    host = _login_host(tmp_path)
    boss = _staff("Ada", RANK_ADMIN)
    host.people = [_newbie("Dee")]
    for line, reply in (
        ("demote Zed", "Player Zed not found.\n"),
        ("demote Cyd", "Cyd is a resident.\n"),
        ("demote Dee", "Dee cannot be demoted.\n"),
        ("demote admin", "The super admin cannot be demoted.\n"),
        ("demote admin force", "The super admin cannot be demoted.\n"),
        ("demote force Bea", "Syntax: demote <name>|demote <name> force\n"),
        ("demote Bea force extra", "Syntax: demote <name>|demote <name> force\n"),
    ):
        host.sent.clear()
        process_commands(host, boss, line)  # type: ignore[arg-type]
        assert host.sent == [reply]
    assert host.staff_lines == []
    loaded = load_saved(player_path(tmp_path, "admin"))
    assert loaded is not None
    assert loaded.rank == "super_admin"


def test_an_old_admin_file_for_the_config_account_becomes_super_admin(tmp_path: Path) -> None:
    """The config account stored as admin is rewritten when the file is loaded."""
    from lupaxa.chat_server.utilities import _find_saved

    _save(tmp_path, "admin", RANK_ADMIN)
    host = _login_host(tmp_path)
    found = _find_saved(host, "admin")  # type: ignore[arg-type]
    assert found is not None
    assert found.rank == "super_admin"
    saved = load_saved(player_path(tmp_path, "admin"))
    assert saved is not None
    assert saved.rank == "super_admin"


def test_finger_shows_when_they_logged_out(tmp_path: Path) -> None:
    """Examine gives rank, the UTC logout time with seconds, and how long ago."""
    left = datetime.now(timezone.utc) - timedelta(hours=2)
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), logged_out_at=left),
    )
    host = _login_host(tmp_path)
    process_commands(host, _newbie("Ada"), "examine Bea")  # type: ignore[arg-type]
    stamp = clock_phrase(left)
    body = examine_frame(
        "Bea",
        (
            ("User Rank", "Resident"),
            ("Logged out", stamp),
            ("Ago", "2 hours"),
            ("Total Login", "0 seconds"),
        ),
        72,
    )
    assert host.sent == [body]
    assert "Resident" in body
    assert "Logged out" in body
    assert stamp in body
    assert "Ago" in body
    assert "2 hours" in body
    assert "Total Login" in body
    assert "0 seconds" in body
    assert "Last from" not in body
    assert "Rank:" not in body


def test_finger_with_no_logout_time_says_unknown(tmp_path: Path) -> None:
    """A file with no ``logged_out_at`` says unknown, with no Ago line."""
    _save(tmp_path, "Bea", RANK_SUPER)
    host = _login_host(tmp_path)
    process_commands(host, _newbie("Ada"), "examine Bea")  # type: ignore[arg-type]
    body = examine_frame(
        "Bea",
        (
            ("User Rank", shown_rank(RANK_SUPER, True)),
            ("Logged out", "unknown"),
            ("Total Login", "0 seconds"),
        ),
        72,
    )
    assert host.sent == [body]
    assert "Logged out" in body
    assert "unknown" in body
    assert "Ago" not in body


def test_finger_and_f_are_unknown() -> None:
    """Finger and f are not commands."""
    for line, reply in (
        ("finger Bea", "No such command 'finger'\n"),
        ("f Bea", "No such command 'f'\n"),
        ("fBea", "No such command 'fBea'\n"),
    ):
        host, _player = _run(line)
        assert host.sent == [reply]


def _viewer(rank: str) -> _Player:
    return _staff("Ada", rank)


def _here_at(host: _Host, name: str, address: str) -> _Player:
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    user = Player(name, "hall", when, connected_seconds=7200)
    bea = _Player(name=name, room="hall", player=user, address=address)
    host.people = [bea]
    return bea


def test_examine_shows_the_address_to_staff(tmp_path: Path) -> None:
    """Examine ends with the address for a super user or the admin, and not for others."""
    rank_words = shown_rank(RANK_NEWBIE, True)
    plain = (
        ("User Rank", rank_words),
        ("Logged in for", "10 minutes"),
        ("Total Login", "2 hours"),
        ("Idle for", "Not idle at all"),
    )
    for rank in (RANK_NEWBIE, RANK_RESIDENT, RANK_SUPER, RANK_ADMIN):
        host = _login_host(tmp_path)
        _here_at(host, "Bea", "10.0.0.5")
        process_commands(host, _viewer(rank), "examine Bea")  # type: ignore[arg-type]
        fields: tuple[tuple[str, str], ...] = plain
        if rank in (RANK_SUPER, RANK_ADMIN):
            fields = (
                ("User Rank", rank_words),
                ("Logged in from", "10.0.0.5"),
                ("Logged in for", "10 minutes"),
                ("Total Login", "2 hours"),
                ("Idle for", "Not idle at all"),
            )
        body = examine_frame("Bea", fields, 72)
        assert host.sent == [body]
        if rank in (RANK_SUPER, RANK_ADMIN):
            assert "Logged in from : 10.0.0.5" in body
        else:
            assert "Logged in from" not in body
            assert "Address:" not in body


def test_examine_shows_last_connected_from(tmp_path: Path) -> None:
    """A logged-out player shows the last address to staff, and not to a resident."""
    left = datetime.now(timezone.utc) - timedelta(hours=2)
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            logged_out_at=left,
            ip_address="10.0.0.5",
        ),
    )
    stamp = clock_phrase(left)
    logged_out = (
        ("User Rank", "Resident"),
        ("Logged out", stamp),
        ("Ago", "2 hours"),
        ("Total Login", "0 seconds"),
    )
    host = _login_host(tmp_path)
    process_commands(host, _viewer(RANK_SUPER), "examine Bea")  # type: ignore[arg-type]
    staff = examine_frame("Bea", (*logged_out, ("Last from", "10.0.0.5")), 72)
    assert host.sent == [staff]
    assert "Last from : 10.0.0.5" in " ".join(staff.split())
    assert "Last connected from:" not in staff
    host = _login_host(tmp_path)
    process_commands(host, _viewer(RANK_RESIDENT), "examine Bea")  # type: ignore[arg-type]
    resident = examine_frame("Bea", logged_out, 72)
    assert host.sent == [resident]
    assert "Last from" not in resident


def test_finger_says_unknown_when_no_address_is_saved(tmp_path: Path) -> None:
    """A file with no address reads unknown to staff, and adds no Ago line."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    process_commands(host, _viewer(RANK_SUPER), "examine Bea")  # type: ignore[arg-type]
    body = examine_frame(
        "Bea",
        (
            ("User Rank", "Resident"),
            ("Logged out", "unknown"),
            ("Total Login", "0 seconds"),
            ("Last from", "unknown"),
        ),
        72,
    )
    assert host.sent == [body]
    assert "Last from : unknown" in " ".join(body.split())
    assert "Last connected from:" not in body


def test_entry_remembers_the_address_of_a_saved_player_only(tmp_path: Path) -> None:
    """A resident's file gets the address on entry. A newbie gets no file."""
    _save(tmp_path, "Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    bea = _Player(player=None, address="10.0.0.5")
    process_input(host, bea, "Bea")  # type: ignore[arg-type]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    assert bea.player is not None
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.ip_address == "10.0.0.5"
    assert saved.hostname == "10.0.0.5"

    host = _login_host(tmp_path)
    cyd = _Player(player=None, address="10.0.0.6")
    process_input(host, cyd, "Cyd")  # type: ignore[arg-type]
    process_input(host, cyd, "yes")  # type: ignore[arg-type]
    assert cyd.player is not None
    assert load_saved(player_path(tmp_path, "Cyd")) is None


def test_a_new_resident_is_saved_with_the_address(tmp_path: Path) -> None:
    """Saving a connected newbie as a resident stores where they are connected from."""
    bea = _newbie("Bea")
    bea.address = "10.0.0.5"
    bea.hostname = "bea.example"
    ada = _staff("Ada", RANK_SUPER)
    host = _login_host(tmp_path)
    host.people = [bea, ada]
    process_commands(host, ada, "resident Bea")  # type: ignore[arg-type]
    process_input(host, bea, "hunter2")  # type: ignore[arg-type]
    process_input(host, bea, "hunter2")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.ip_address == "10.0.0.5"
    assert saved.hostname == "bea.example"


def test_su_stays_on_the_staff_channel(tmp_path: Path) -> None:
    """The sender sees You, the channel gets the name line, and a resident is refused."""
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    bob = _staff("Bob", RANK_SUPER)
    host.people = [ada, bob]
    for line, mine, theirs in (
        ("su hello", "< SU > You say 'hello'\n", "< SU > Ada says 'hello'\n"),
        ("se waves", "< SU > You emote: waves\n", "< SU > Ada waves\n"),
        ("st lunch", "< SU > You think . o O ( lunch )\n", "< SU > Ada thinks . o O ( lunch )\n"),
    ):
        host.sent.clear()
        host.staff_lines.clear()
        process_commands(host, ada, line)  # type: ignore[arg-type]
        assert host.sent == [paint_staff(ada, mine)]
        assert host.staff_lines == [paint_staff(bob, theirs)]
    for word in ("su", "se", "st"):
        host.sent.clear()
        process_commands(host, ada, word)  # type: ignore[arg-type]
        assert host.sent == [f"Syntax: {word} <message>\n"]
    host.sent.clear()
    host.staff_lines.clear()
    for word in ("su", "se", "st", "lsn"):
        process_commands(host, _staff("Bea", RANK_RESIDENT), f"{word} hi")  # type: ignore[arg-type]
    assert host.sent == [f"No such command '{word}'\n" for word in ("su", "se", "st", "lsn")]
    assert host.staff_lines == []


def test_admin_channel_say_emote_and_think(tmp_path: Path) -> None:
    """au, ae, and at stay on the admin channel and are hidden from super users."""
    from lupaxa.chat_server.channel import paint_admin

    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_ADMIN)
    bob = _staff("Bob", RANK_ADMIN)
    sue = _staff("Sue", RANK_SUPER)
    host.people = [ada, bob, sue]
    cases = (
        ("au hello", "[ Admin ] You say 'hello'\n", "[ Admin ] Ada says 'hello'\n"),
        ("ae waves", "[ Admin ] You emote: waves\n", "[ Admin ] Ada waves\n"),
        (
            "at lunch",
            "[ Admin ] You think . o O ( lunch )\n",
            "[ Admin ] Ada thinks . o O ( lunch )\n",
        ),
    )
    for line, mine, theirs in cases:
        host.sent.clear()
        host.admin_lines.clear()
        host.staff_lines.clear()
        process_commands(host, ada, line)  # type: ignore[arg-type]
        assert host.sent == [paint_admin(ada, mine)]
        assert host.admin_lines == [paint_admin(bob, theirs)]
        assert host.staff_lines == []
    host.sent.clear()
    host.staff_lines.clear()
    process_commands(host, sue, "su hello")  # type: ignore[arg-type]
    assert any("Sue says 'hello'" in line for line in host.staff_lines)
    for word in ("au", "ae", "at"):
        host.sent.clear()
        process_commands(host, ada, word)  # type: ignore[arg-type]
        assert host.sent == [f"Syntax: {word} <message>\n"]
    ada.player.colour = False  # type: ignore[union-attr]
    bob.player.colour = False  # type: ignore[union-attr]
    host.sent.clear()
    host.admin_lines.clear()
    process_commands(host, ada, "au hello")  # type: ignore[arg-type]
    assert host.sent == ["[ Admin ] You say 'hello'\n"]
    assert host.admin_lines == ["[ Admin ] Ada says 'hello'\n"]
    host.sent.clear()
    process_commands(host, sue, "au hello")  # type: ignore[arg-type]
    process_commands(host, sue, "ae waves")  # type: ignore[arg-type]
    process_commands(host, sue, "at lunch")  # type: ignore[arg-type]
    assert host.sent == [
        "No such command 'au'\n",
        "No such command 'ae'\n",
        "No such command 'at'\n",
    ]


def test_lsu_pads_the_columns_and_lists_only_staff(tmp_path: Path) -> None:
    """Only connected super users and the admin are listed, sorted, with dashes aligned."""
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    assert ada.player is not None
    ada.player.idle_seconds = 0
    boss = _staff("admin", RANK_ADMIN)
    assert boss.player is not None
    boss.player.idle_seconds = 120
    bea = _staff("Bea", RANK_RESIDENT)
    cyd = _newbie("Cyd")
    host.people = [boss, bea, ada, cyd]
    assert ada.discovered_columns == 72
    process_commands(host, ada, "lsu")  # type: ignore[arg-type]
    text = host.sent[0]
    assert "[ Admin ]" in text
    assert "< Super User >" in text
    assert "00:02:00" in text
    assert "Super Users:" not in text
    assert "0 seconds" not in text
    assert "Bea" not in text
    assert "Cyd" not in text
    for asker in (cyd, bea):
        host.sent.clear()
        process_commands(host, asker, "lsu")  # type: ignore[arg-type]
        assert host.sent == [text]


def test_lsu_text_is_the_seventy_two_column_frame(tmp_path: Path) -> None:
    """A coloured staff list stays within the 72-column terminal."""
    from lupaxa.chat_server.colour import cyan, plain_len, red

    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    assert ada.player is not None
    ada.player.idle_seconds = 0
    boss = _staff("admin", RANK_ADMIN)
    assert boss.player is not None
    boss.player.idle_seconds = 3723
    host.people = [boss, ada]
    assert ada.discovered_columns == 72
    process_commands(host, ada, "lsu")  # type: ignore[arg-type]
    super_line = "  Ada         < Super User >       Reception                            "
    super_line = super_line.replace("< Super User >", cyan("< Super User >"), 1)
    admin_line = "  admin         [ Admin ]          Reception                  01:02:03  "
    admin_line = admin_line.replace("[ Admin ]", red("[ Admin ]"), 1)
    assert plain_len(super_line) == 72
    assert plain_len(admin_line) == 72
    lines = [
        "-" * 72,
        "  Name             Rank              Room         Status        Idle    ",
        "-" * 72,
        super_line,
        admin_line,
        "-" * 72,
    ]
    assert host.sent == ["\n".join(lines) + "\n"]
    for line in lines:
        assert plain_len(line) == 72


def test_lsu_with_colour_off_is_the_plain_frame(tmp_path: Path) -> None:
    """With colour off, rank cells have no codes and rules stay 72 columns wide."""
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    assert ada.player is not None
    ada.player.colour = False
    ada.player.idle_seconds = 0
    boss = _staff("admin", RANK_ADMIN)
    assert boss.player is not None
    boss.player.idle_seconds = 3723
    host.people = [boss, ada]
    assert ada.discovered_columns == 72
    process_commands(host, ada, "lsu")  # type: ignore[arg-type]
    super_line = "  Ada         < Super User >       Reception                            "
    admin_line = "  admin         [ Admin ]          Reception                  01:02:03  "
    lines = [
        "-" * 72,
        "  Name             Rank              Room         Status        Idle    ",
        "-" * 72,
        super_line,
        admin_line,
        "-" * 72,
    ]
    assert host.sent == ["\n".join(lines) + "\n"]
    for rule in (lines[0], lines[2], lines[5]):
        assert len(rule) == 72


def test_staff_lists_the_same_frame_as_lsu() -> None:
    """Staff is lsu. staffroom is not. staff please is the lsu syntax."""
    ada = _Player(player=Player("Ada", "reception", datetime.now(timezone.utc), rank=RANK_SUPER))
    host = _Host(people=[ada])
    process_commands(host, ada, "lsu")  # type: ignore[arg-type]
    listed = host.sent[0]
    host.sent.clear()
    process_commands(host, ada, "staff")  # type: ignore[arg-type]
    assert host.sent == [listed]
    host.sent.clear()
    process_commands(host, ada, "staffroom")  # type: ignore[arg-type]
    assert host.sent == ["No such command 'staffroom'\n"]
    host.sent.clear()
    process_commands(host, ada, "staff please")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: lsu\n"]


def test_lsu_with_nobody_to_list(tmp_path: Path) -> None:
    """No connected staff member is one sentence, not a table."""
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    host.people = [_staff("Bea", RANK_RESIDENT)]
    process_commands(host, ada, "lsu")  # type: ignore[arg-type]
    assert host.sent == [" -=> There are no staff members connected currently.\n"]


def test_lsu_lists_a_connected_super_admin(tmp_path: Path) -> None:
    """The config super admin appears on the staff list with the Super Admin label."""
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    boss = _staff("admin", RANK_SUPER_ADMIN)
    host.people = [boss, ada]
    process_commands(host, ada, "lsu")  # type: ignore[arg-type]
    text = host.sent[0]
    assert "[ Super Admin ]" in text
    assert "admin" in text


def test_lsn_lists_connected_newbies(tmp_path: Path) -> None:
    """A super user sees each connected newbie with the address, names padded."""
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    host.people = [ada, _staff("Dee", RANK_RESIDENT)]
    host.newbies = [("Bea", "10.0.0.5", "reception", 0), ("Cora", "192.168.1.20", "hall", 11)]
    process_commands(host, ada, "lsn")  # type: ignore[arg-type]
    text = host.sent[0]
    assert "Newbies:" not in text
    assert "10.0.0.5" in text
    assert "192.168.1.20" in text
    assert "Reception" in text
    assert "Hall" in text
    assert "00:00:11" in text


def test_lsn_shows_hostname_and_room() -> None:
    """Each newbie row shows hostname, room display name, and idle clock."""
    ada = _Player(player=Player("Ada", "reception", datetime.now(timezone.utc), rank=RANK_SUPER))
    host = _Host(people=[ada])
    host.newbies = [("Bea", "bea.example", "hall", 11)]
    process_commands(host, ada, "lsn")  # type: ignore[arg-type]
    text = host.sent[0]
    assert "Address" in text
    assert "Room" in text
    assert "bea.example" in text
    assert "Hall" in text
    assert "00:00:11" in text


def test_lsn_with_no_newbies(tmp_path: Path) -> None:
    """No connected newbie is one sentence, not a table."""
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    process_commands(host, ada, "lsn")  # type: ignore[arg-type]
    assert host.sent == [" -=> There are no newbies connected currently.\n"]


def test_lsn_refuses_extra_text(tmp_path: Path) -> None:
    """Extra text after lsn is a syntax error."""
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    host.newbies = [("Bea", "10.0.0.5", "reception", 0)]
    process_commands(host, ada, "lsn now")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: lsn\n"]


def test_newbie_cannot_lsn(tmp_path: Path) -> None:
    """A newbie and a resident get no such command, and help hides it from a newbie."""
    host = _login_host(tmp_path)
    host.newbies = [("Bea", "10.0.0.5", "reception", 0)]
    process_commands(host, _newbie("Bea"), "lsn")  # type: ignore[arg-type]
    process_commands(host, _staff("Dee", RANK_RESIDENT), "lsn")  # type: ignore[arg-type]
    assert host.sent == ["No such command 'lsn'\n", "No such command 'lsn'\n"]
    host = _Host(
        _help=HelpBook(entries=(HelpEntry("lsn", (), "List connected newbies."),)),
    )
    process_commands(host, _newbie("Bea"), "help lsn")  # type: ignore[arg-type]
    assert host.sent == ["help for lsn not found\n"]


def test_connection_rows_use_the_pending_name_and_sort_by_ip() -> None:
    """No name is <pending>. A typed name is kept. Rows sort by name, then IP."""
    early = _Player(
        player=None,
        name=None,
        room=None,
        address="10.0.0.1",
        hostname="10.0.0.1",
    )
    late = _Player(
        player=None,
        name=None,
        room=None,
        address="9.9.9.9",
        hostname="9.9.9.9",
    )
    held = _Player(
        player=None,
        name=None,
        room=None,
        pending_name="Bea",
        address="203.0.113.4",
        hostname="203.0.113.4",
    )
    ada = _Player(address="198.51.100.2", hostname="ada.example", ssh=True)
    logging_in = _Player(pending_name="NotAda", auth="current", address="198.51.100.8")
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
    viewer = _Player(
        player=Player("Cyd", "reception", when, rank=RANK_SUPER),
        address="192.0.2.1",
        hostname="staff.example",
    )
    unnamed = _Player(
        player=None,
        name=None,
        room=None,
        address="192.0.2.10",
        hostname="192.0.2.10",
    )
    host = _Host(people=[unnamed, viewer])
    process_commands(host, viewer, "connections")  # type: ignore[arg-type]
    frame = host.sent[0]
    assert frame.splitlines()[1].split()[:4] == ["Name", "Hostname", "IP", "Address"]
    assert "Type" in frame.splitlines()[1]
    assert "<pending>" in frame
    assert "Cyd" in frame
    assert "staff.example" in frame
    assert "192.0.2.1" in frame
    assert "telnet" in frame
    assert frame.index("<pending>") < frame.index("Cyd")


def test_conns_is_the_same_list_and_needs_a_space() -> None:
    """Conns lists sockets. connsplease is not the command."""
    viewer = _Player(player=Player("Cyd", "reception", datetime.now(timezone.utc), rank=RANK_SUPER))
    listed, _player = _run("conns", viewer)
    assert "Cyd" in listed.sent[0]
    missing, _player = _run("connsplease", viewer)
    assert missing.sent == ["No such command 'connsplease'\n"]
    extra, _player = _run("connections please", viewer)
    assert extra.sent == ["Syntax: connections\n"]
    alias_extra, _player = _run("conns please", viewer)
    assert alias_extra.sent == ["Syntax: connections\n"]


def test_a_resident_cannot_use_connections() -> None:
    """Residents are told the words are not commands."""
    resident = _Player(
        player=Player("Ada", "reception", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    )
    refused, _player = _run("connections", resident)
    assert refused.sent == ["No such command 'connections'\n"]
    alias, _player = _run("conns", resident)
    assert alias.sent == ["No such command 'conns'\n"]


def test_a_newbie_has_no_password_to_change(tmp_path: Path) -> None:
    """A newbie is told there is no saved password to change."""
    host = _login_host(tmp_path)
    process_commands(host, _newbie("Ada"), "password")  # type: ignore[arg-type]
    assert host.sent == ["You have no saved password.\n"]


def test_password_syntax_rejects_extra_text(tmp_path: Path) -> None:
    """Password does not take a remainder."""
    host = _login_host(tmp_path)
    process_commands(host, _staff("Bea", RANK_RESIDENT), "password extra")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: password\n"]


def test_password_via_process_input_leaves_only_current_prompt(tmp_path: Path) -> None:
    """The live path must not append the play prompt to the current-password line."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    bea = _staff("Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    process_input(host, bea, "password")  # type: ignore[arg-type]
    assert host.sent == ["Current password: "]
    assert bea.auth == "current"


def test_saved_player_can_change_password(tmp_path: Path) -> None:
    """A saved resident replaces their password after the three prompts."""
    left = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            logged_out_at=left,
            ip_address="10.0.0.5",
            prompt="Go: ",
            connected_seconds=90,
        ),
    )
    bea = _staff("Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    process_commands(host, bea, "password")  # type: ignore[arg-type]
    assert host.sent == ["Current password: "]
    assert bea.auth == "current"
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    process_input(host, bea, "next")  # type: ignore[arg-type]
    process_input(host, bea, "next")  # type: ignore[arg-type]
    assert host.sent[-1] == "Password changed.\nWhat? > "
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert password_ok("next", saved.password_hash)
    assert not password_ok("pw", saved.password_hash)
    assert saved.logged_out_at == left
    assert saved.ip_address == "10.0.0.5"
    assert saved.hostname == "10.0.0.5"
    assert saved.prompt == "Go: "
    assert saved.connected_seconds == 90


def test_password_change_reseals_with_the_new_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new password still opens the same MFA secret."""
    from lupaxa.chat_server.mfa_seal import open_mfa_secret, seal_mfa_secret

    totp_seed = TOTP_TEST_SEED
    sealed = seal_mfa_secret("pw", totp_seed)
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=sealed,
            mfa_on=True,
        ),
    )
    host = _login_host(tmp_path)
    host._mfa_enabled = False
    bea = _staff("Bea", RANK_RESIDENT)
    process_commands(host, bea, "password")  # type: ignore[arg-type]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    process_input(host, bea, "next")  # type: ignore[arg-type]
    process_input(host, bea, "next")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is not None
    assert open_mfa_secret("next", saved.mfa_secret) == totp_seed
    assert open_mfa_secret("pw", saved.mfa_secret) is None


def test_password_change_reseals_after_mismatch_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed confirm still reseals with the new password on retry."""
    from lupaxa.chat_server.mfa_seal import open_mfa_secret, seal_mfa_secret

    totp_seed = TOTP_TEST_SEED
    sealed = seal_mfa_secret("pw", totp_seed)
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            mfa_secret=sealed,
            mfa_on=True,
        ),
    )
    host = _login_host(tmp_path)
    host._mfa_enabled = False
    bea = _staff("Bea", RANK_RESIDENT)
    process_commands(host, bea, "password")  # type: ignore[arg-type]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    process_input(host, bea, "next")  # type: ignore[arg-type]
    process_input(host, bea, "nope")  # type: ignore[arg-type]
    process_input(host, bea, "next")  # type: ignore[arg-type]
    process_input(host, bea, "next")  # type: ignore[arg-type]
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.mfa_secret is not None
    assert open_mfa_secret("next", saved.mfa_secret) == totp_seed
    assert open_mfa_secret("pw", saved.mfa_secret) is None


def test_password_wrong_current_keeps_the_old_hash(tmp_path: Path) -> None:
    """A wrong current password does not drop the player or change the file."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    bea = _staff("Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    process_commands(host, bea, "password")  # type: ignore[arg-type]
    process_input(host, bea, "no")  # type: ignore[arg-type]
    assert host.sent[-1] == "Wrong password.\nWhat? > "
    assert host.dropped is False
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert password_ok("pw", saved.password_hash)


def test_password_mismatch_returns_to_set_prompt(tmp_path: Path) -> None:
    """Mismatched new passwords stay on the first new-password prompt."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    bea = _staff("Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    process_commands(host, bea, "password")  # type: ignore[arg-type]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    process_input(host, bea, "one")  # type: ignore[arg-type]
    process_input(host, bea, "two")  # type: ignore[arg-type]
    assert host.sent[-1] == "Those passwords do not match.\nSet a password: "
    assert bea.auth == "renew"
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert password_ok("pw", saved.password_hash)


def test_password_renew_rejects_empty_and_long(tmp_path: Path) -> None:
    """Empty and overlong new passwords keep the old hash."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    bea = _staff("Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    process_commands(host, bea, "password")  # type: ignore[arg-type]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    process_input(host, bea, "")  # type: ignore[arg-type]
    assert host.sent[-1] == "Password cannot be empty.\nSet a password: "
    process_input(host, bea, "x" * 73)  # type: ignore[arg-type]
    assert host.sent[-1] == "Password is too long.\nSet a password: "
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert password_ok("pw", saved.password_hash)


def test_password_without_confirm_keeps_the_old_hash(tmp_path: Path) -> None:
    """Stopping after the first new password leaves the file unchanged."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    bea = _staff("Bea", RANK_RESIDENT)
    host = _login_host(tmp_path)
    process_commands(host, bea, "password")  # type: ignore[arg-type]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    process_input(host, bea, "next")  # type: ignore[arg-type]
    assert bea.auth == "renew_confirm"
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert password_ok("pw", saved.password_hash)


def test_admin_commands_are_coloured_and_say_is_plain() -> None:
    """Super-user names are cyan, admin names are red, and say stays plain."""
    from lupaxa.chat_server.colour import cyan, red

    boss = _staff("admin", RANK_ADMIN)
    host, _player = _run("commands", boss)
    text = host.sent[0]
    assert cyan("lsn") in text
    assert cyan("conns") in text
    assert red("shutdown") in text
    assert cyan("say") not in text
    assert red("network") in text
    assert "say" in text.split()


def test_commands_without_colour_have_no_codes() -> None:
    """Colour off leaves the command list as plain words."""
    boss = _staff("admin", RANK_ADMIN)
    assert boss.player is not None
    boss.player.colour = False
    host, _player = _run("commands", boss)
    assert "\x1b" not in host.sent[0]
    assert "shutdown" in host.sent[0]


def test_examine_ranks_use_the_viewers_colour() -> None:
    """Newbie is green, super user is cyan, admin is red, and resident is plain."""
    from lupaxa.chat_server.colour import cyan, green, red

    when = datetime.now(timezone.utc)
    newbie = _Player(player=Player("Bea", "hall", when))
    host = _Host(people=[newbie])
    process_commands(host, newbie, "examine me")  # type: ignore[arg-type]
    assert green("Newbie") in host.sent[0]
    assert "User Rank" in host.sent[0]
    assert host.sent[0].split("User Rank")[1].startswith("      : ")

    resident = _staff("Bea", RANK_RESIDENT)
    host = _Host(people=[resident])
    process_commands(host, resident, "examine me")  # type: ignore[arg-type]
    assert "Resident" in host.sent[0]
    assert "\x1b" not in host.sent[0]

    super_user = _staff("Ada", RANK_SUPER)
    host = _Host(people=[super_user])
    process_commands(host, super_user, "examine me")  # type: ignore[arg-type]
    assert cyan("Super User") in host.sent[0]

    boss = _staff("admin", RANK_ADMIN)
    assert boss.player is not None
    boss.player.colour = False
    host = _Host(people=[super_user, boss])
    process_commands(host, boss, "examine Ada")  # type: ignore[arg-type]
    assert "Super User" in host.sent[0]
    assert cyan("Super User") not in host.sent[0]

    boss = _staff("admin", RANK_ADMIN)
    host = _Host(people=[boss])
    process_commands(host, boss, "examine me")  # type: ignore[arg-type]
    assert red("Admin") in host.sent[0]
    assert boss.player is not None
    boss.player.colour = False
    host.sent.clear()
    process_commands(host, boss, "examine me")  # type: ignore[arg-type]
    assert "Admin" in host.sent[0]
    assert red("Admin") not in host.sent[0]


def test_logged_out_examine_colours_the_rank(tmp_path: Path) -> None:
    """A logged-out super user is cyan for a viewer whose colour is on."""
    from lupaxa.chat_server.colour import cyan

    save_player(tmp_path, SavedPlayer("Bea", RANK_SUPER, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    host.people = [ada]
    process_commands(host, ada, "examine Bea")  # type: ignore[arg-type]
    assert cyan("Super User") in host.sent[0]


def test_admin_words_include_the_new_commands() -> None:
    """The admin sees every command word."""
    assert command_words(RANK_ADMIN) == (
        "! \" ' , . : ; < > ? ` ~ ae alarm allow at au auto_reply broadcast color "
        "colour commands connections "
        "conns demote "
        "description disconnect emote entermsg examine go help join look lsn lsu main map network "
        "nuke "
        "password promote prompt quit recap reload remote resident retire save say se shout "
        "shutdown st staff status su "
        "tell term_width think time version w warn who x"
    )
    assert "main" in command_words(RANK_NEWBIE).split()
    assert "idle" not in command_words(RANK_NEWBIE).split()
    assert "password" in command_words(RANK_NEWBIE).split()
    assert "finger" not in command_words(RANK_NEWBIE).split()
    assert "f" not in command_words(RANK_NEWBIE).split()
    assert "lsn" in command_words(RANK_SUPER).split()
    assert "lsn" not in command_words(RANK_RESIDENT).split()
    assert "lsn" not in command_words(RANK_NEWBIE).split()
    assert "connections" in command_words(RANK_SUPER).split()
    assert "conns" in command_words(RANK_SUPER).split()
    assert "connections" not in command_words(RANK_RESIDENT).split()
    assert "conns" not in command_words(RANK_RESIDENT).split()
    assert "connections" not in command_words(RANK_NEWBIE).split()
    assert "lsu" in command_words(RANK_NEWBIE).split()
    assert "lsu" in command_words(RANK_RESIDENT).split()
    for word in ("su", "se", "st", "lsn"):
        assert word not in command_words(RANK_RESIDENT).split()
        assert word not in command_words(RANK_NEWBIE).split()


def _seconds_saved(tmp_path: Path, seconds: int) -> None:
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), connected_seconds=seconds),
    )


def _saved_seconds(tmp_path: Path) -> int:
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    return saved.connected_seconds


def _resident_bea(seconds: int, flushed: int) -> Player:
    return Player(
        "Bea",
        "hall",
        datetime.now(timezone.utc),
        rank=RANK_RESIDENT,
        connected_seconds=seconds,
        flushed_seconds=flushed,
    )


def test_note_connected_time_writes_after_300_more_seconds(tmp_path: Path) -> None:
    """Three hundred seconds ahead of the last write saves the total. Fewer does not."""
    _seconds_saved(tmp_path, 10)
    user = _resident_bea(10, 10)
    note_connected_time(tmp_path, user, 299)
    assert user.connected_seconds == 309
    assert user.flushed_seconds == 10
    assert _saved_seconds(tmp_path) == 10
    note_connected_time(tmp_path, user, 1)
    assert user.connected_seconds == 310
    assert user.flushed_seconds == 310
    assert _saved_seconds(tmp_path) == 310


def test_note_connected_time_of_300_writes_310(tmp_path: Path) -> None:
    """A saved resident at 10 seconds who adds 300 has 310 in the file."""
    _seconds_saved(tmp_path, 10)
    user = _resident_bea(10, 10)
    note_connected_time(tmp_path, user, 300)
    assert _saved_seconds(tmp_path) == 310
    assert user.flushed_seconds == 310


def test_note_connected_time_ignores_nothing_to_add(tmp_path: Path) -> None:
    """Zero seconds changes nothing."""
    user = _resident_bea(10, 10)
    note_connected_time(tmp_path, user, 0)
    assert user.connected_seconds == 10


def test_note_connected_time_keeps_a_newbie_off_disk(tmp_path: Path) -> None:
    """A newbie counts the seconds, and no file is written."""
    user = Player("Bea", "hall", datetime.now(timezone.utc))
    note_connected_time(tmp_path, user, 300)
    assert user.connected_seconds == 300
    assert user.flushed_seconds == 0
    assert list(tmp_path.iterdir()) == []


def test_a_failed_write_leaves_flushed_seconds_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad player file is printed, and the next tick tries again."""
    (tmp_path / "bea.yml").write_text("- not a mapping\n", encoding="utf-8")
    user = _resident_bea(0, 0)
    note_connected_time(tmp_path, user, 300)
    assert user.connected_seconds == 300
    assert user.flushed_seconds == 0
    assert "bea.yml" in capsys.readouterr().out


def test_record_saved_logout_writes_the_time_and_the_total_together(tmp_path: Path) -> None:
    """After 40 seconds the file has connected_seconds 40 and a logout time."""
    _seconds_saved(tmp_path, 0)
    user = _resident_bea(40, 0)
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


def _advance(host: _Host, monkeypatch: pytest.MonkeyPatch, now: float) -> None:
    host._timer_mark = 0.0  # type: ignore[attr-defined]
    host._timer_remainder = 0.0  # type: ignore[attr-defined]
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: now)
    process_timer(host)  # type: ignore[arg-type]


def test_allow_changes_the_live_flag_and_not_the_file(tmp_path: Path) -> None:
    """Allow closes newbies in memory and leaves config.yml bytes alone."""
    config_path = tmp_path / "config.yml"
    config_path.write_text("allow_newbies: true\n", encoding="utf-8")
    host = _Host()
    ada = _Player()
    ada.player.rank = RANK_ADMIN
    host.people = [ada]
    assert ada.player is not None
    ada.player.colour = False
    command_allow(host, ada, Command("allow", command_allow, False), "newbies off")
    assert host._allow_newbies is False
    assert config_path.read_text(encoding="utf-8") == "allow_newbies: true\n"
    assert host.staff_lines == ["< SU > Ada closes the talker to newbies.\n"]

    host.staff_lines.clear()
    host._allow_residents = False
    command_allow(host, ada, Command("allow", command_allow, False), "residents on")
    assert host._allow_residents is True
    assert host.staff_lines == ["< SU > Ada opens the talker to residents.\n"]

    host._allow_newbies = True
    host._allow_residents = True
    ada.sent.clear()
    command_allow(host, ada, Command("allow", command_allow, False), "maybe")
    assert ada.sent == ["Syntax: allow newbies|residents on|off\n"]
    assert host._allow_newbies is True
    assert host._allow_residents is True

    newbie_host, newbie = _run("allow newbies off", _newbie("Bea"))
    assert newbie.sent == ["No such command 'allow'\n"]
    assert newbie_host._allow_newbies is True


def test_broadcast_is_centered_with_blank_lines() -> None:
    """An admin broadcast is red when colour is on, with a blank line after it."""
    plain = "-=> Broadcast Messages from Ada: hello <=-"
    ada = _Player(
        player=Player("Ada", "hall", datetime.now(timezone.utc), rank="admin"),
        frame_columns=len(plain),
    )
    host, _player = _run("broadcast hello", ada)
    assert host.sent[0] == f"\n\n\n{red(plain)}\n\n\n\n"
    resident, _player = _run("broadcast hello", _staff("Bea", RANK_RESIDENT))
    assert resident.sent == ["No such command 'broadcast'\n"]
    empty, _player = _run("broadcast", ada)
    assert empty.sent == ["Syntax: broadcast <message>\n"]
    ada_off = _Player(
        player=Player(
            "Ada",
            "hall",
            datetime.now(timezone.utc),
            rank="admin",
            colour=False,
        ),
        frame_columns=len(plain),
    )
    host_off, _player = _run("broadcast hello", ada_off)
    assert host_off.sent[0] == f"\n\n\n{plain}\n\n\n\n"
    ada_wide = _Player(
        player=Player("Ada", "hall", datetime.now(timezone.utc), rank="admin"),
        frame_columns=len(plain) + 4,
    )
    host_wide, _player = _run("broadcast hello", ada_wide)
    assert host_wide.sent[0] == f"\n\n\n  {red(plain)}\n\n\n\n"


def test_shutdown_speaks_the_opening_line_and_the_marks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """90 seconds opens in seconds, then 1 minute, then the pair, then stop."""
    host = _login_host(tmp_path)
    host._clients = {}  # type: ignore[attr-defined]
    admin = _staff("Ada", RANK_ADMIN)
    process_commands(host, admin, "shutdown 90 restart")  # type: ignore[arg-type]
    assert host.all_lines == ["Shutting down in 90 seconds for 'restart'\n"]
    assert host.stopped is False
    now = {"t": 0.0}
    host._timer_mark = 0.0
    host._timer_remainder = 0.0
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: now["t"])
    now["t"] = 30.0
    host.all_lines.clear()
    process_timer(host)  # type: ignore[arg-type]
    assert host.all_lines == ["Shutting down in 1 minute for 'restart'\n"]
    now["t"] = 89.0
    host.all_lines.clear()
    process_timer(host)  # type: ignore[arg-type]
    assert host.all_lines == [
        "Shutting down in 1 second for 'restart'\n",
        "Shutting down now!\n",
    ]
    assert host.stopped is False
    now["t"] = 90.0
    host.all_lines.clear()
    process_timer(host)  # type: ignore[arg-type]
    assert host.all_lines == []
    assert host.stopped is True


def test_shutdown_5_says_each_second_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The opening 5 is not repeated, and 4, 3, and 2 are said once."""
    host = _login_host(tmp_path)
    host._clients = {}  # type: ignore[attr-defined]
    process_commands(host, _staff("Ada", RANK_ADMIN), "shutdown 5 restart")  # type: ignore[arg-type]
    assert host.all_lines == ["Shutting down in 5 seconds for 'restart'\n"]
    now = {"t": 0.0}
    host._timer_mark = 0.0
    host._timer_remainder = 0.0
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: now["t"])
    heard: list[str] = []
    for second in range(1, 5):
        now["t"] = float(second)
        host.all_lines.clear()
        process_timer(host)  # type: ignore[arg-type]
        heard.extend(host.all_lines)
    assert heard == [
        "Shutting down in 4 seconds for 'restart'\n",
        "Shutting down in 3 seconds for 'restart'\n",
        "Shutting down in 2 seconds for 'restart'\n",
        "Shutting down in 1 second for 'restart'\n",
        "Shutting down now!\n",
    ]
    assert host.stopped is False


def test_a_jump_to_zero_still_says_the_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipped marks stay unsaid. Passing 1 still says the pair, then stops."""
    host = _login_host(tmp_path)
    host._clients = {}  # type: ignore[attr-defined]
    process_commands(host, _staff("Ada", RANK_ADMIN), "shutdown 6 restart")  # type: ignore[arg-type]
    host._timer_mark = 0.0
    host._timer_remainder = 0.0
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: 6.0)
    host.all_lines.clear()
    process_timer(host)  # type: ignore[arg-type]
    assert host.all_lines == [
        "Shutting down in 1 second for 'restart'\n",
        "Shutting down now!\n",
    ]
    assert host.stopped is True


def test_shutdown_now_stops_without_the_pair(tmp_path: Path) -> None:
    """Now speaks one line and stops, and it replaces a running countdown."""
    host = _login_host(tmp_path)
    process_commands(host, _staff("Ada", RANK_ADMIN), "shutdown 600 restart")  # type: ignore[arg-type]
    host.all_lines.clear()
    process_commands(host, _staff("Ada", RANK_ADMIN), "shutdown now maintenance")  # type: ignore[arg-type]
    assert host.all_lines == ["Shutting down now for 'maintenance'.\n"]
    assert host.stopped is True
    assert host._shutdown_seconds is None


def test_shutdown_now_skips_login_timeout_for_a_pending_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An overdue login is not told Login timed out after shutdown now."""
    host = _login_host(tmp_path)
    waiting = _Player(
        name=None,
        room=None,
        player=None,
        auth="set",
        pending_name="Bea",
        accepted_at=1_000.0,
    )
    host.people = [waiting]
    host._clients = {"s": waiting}  # type: ignore[attr-defined]
    admin = _staff("Ada", RANK_ADMIN)
    process_commands(host, admin, "shutdown now restart")  # type: ignore[arg-type]
    assert host.all_lines == ["Shutting down now for 'restart'.\n"]
    assert host.stopped is True
    host.sent.clear()
    _advance(host, monkeypatch, 2_000.0)
    assert "Login timed out.\n" not in host.sent
    assert waiting not in host.dropped_clients


def test_shutdown_cancel_clears_the_countdown(tmp_path: Path) -> None:
    """Cancel tells the rooms, and a second cancel tells only the admin."""
    host = _login_host(tmp_path)
    admin = _staff("Ada", RANK_ADMIN)
    process_commands(host, admin, "shutdown 600 restart")  # type: ignore[arg-type]
    host.all_lines.clear()
    host.sent.clear()
    process_commands(host, admin, "shutdown cancel maintenance finished")  # type: ignore[arg-type]
    assert host.all_lines == ["Shutdown cancelled for 'maintenance finished'.\n"]
    assert host.stopped is False
    assert host._shutdown_seconds is None
    host.all_lines.clear()
    host.sent.clear()
    process_commands(host, admin, "shutdown cancel again")  # type: ignore[arg-type]
    assert host.all_lines == []
    assert host.sent == ["No shutdown is running.\n"]


def test_a_second_shutdown_replaces_the_first(tmp_path: Path) -> None:
    """One countdown exists. The new time is spoken at once."""
    host = _login_host(tmp_path)
    admin = _staff("Ada", RANK_ADMIN)
    process_commands(host, admin, "shutdown 600 restart")  # type: ignore[arg-type]
    host.all_lines.clear()
    process_commands(host, admin, "shutdown 30 maintenance")  # type: ignore[arg-type]
    assert host._shutdown_seconds == 30
    assert host._shutdown_reason == "maintenance"
    assert host.all_lines == ["Shutting down in 30 seconds for 'maintenance'\n"]
    assert host.stopped is False


def test_shutdown_1_says_the_pair_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening at 1 second says the pair, and the next second only stops."""
    host = _login_host(tmp_path)
    host._clients = {}  # type: ignore[attr-defined]
    process_commands(host, _staff("Ada", RANK_ADMIN), "shutdown 1 restart")  # type: ignore[arg-type]
    assert host.all_lines == [
        "Shutting down in 1 second for 'restart'\n",
        "Shutting down now!\n",
    ]
    assert host.stopped is False
    host._timer_mark = 0.0
    host._timer_remainder = 0.0
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: 1.0)
    host.all_lines.clear()
    process_timer(host)  # type: ignore[arg-type]
    assert host.all_lines == []
    assert host.stopped is True


def test_shutdown_rejects_a_bad_count_and_hides_from_a_super_user(tmp_path: Path) -> None:
    """Below 1 is syntax. A super user is told the command does not exist."""
    host = _login_host(tmp_path)
    process_commands(host, _staff("Ada", RANK_ADMIN), "shutdown 0 restart")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: shutdown <seconds> <reason>\n"]
    assert host._shutdown_seconds is None
    host.sent.clear()
    process_commands(host, _staff("Ada", RANK_SUPER), "shutdown 10 restart")  # type: ignore[arg-type]
    assert host.sent == ["No such command 'shutdown'\n"]
    assert "shutdown" not in command_words(RANK_SUPER).split()
    assert "shutdown" in command_words(RANK_ADMIN).split()


def test_shutdown_rejects_a_unicode_digit_count(tmp_path: Path) -> None:
    """A superscript digit is not a count, and it does not start a countdown."""
    host = _login_host(tmp_path)
    process_commands(host, _staff("Ada", RANK_ADMIN), "shutdown ² restart")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: shutdown <seconds> <reason>\n"]
    assert host.stopped is False
    assert host._shutdown_seconds is None


def test_reload_help_replaces_the_book(tmp_path: Path) -> None:
    """reload help installs the file and tells the admin."""  # noqa: D403
    path = tmp_path / "help.yml"
    path.write_text("- name: say\n  aliases: []\n  text: Hello.\n", encoding="utf-8")
    host = _Host()
    host._config = replace(host._config, help=path)
    host._help_failed = HelpStamp(1, 2)
    ada = _staff("Ada", RANK_ADMIN)
    assert ada.player is not None
    ada.player.colour = False
    host.people = [ada]
    process_commands(host, ada, "reload help")  # type: ignore[arg-type]
    loaded = host._help.find("say")
    assert loaded is not None
    assert loaded.text == "Hello."
    assert host.sent == ["Help reloaded.\n"]
    assert host.admin_lines == []
    assert host._help_stamp == help_stamp(path)
    assert host._help_failed is None


def test_reload_help_keeps_the_book_when_the_file_is_not_utf8(tmp_path: Path) -> None:
    """A non-UTF-8 file leaves the old book and tells the admin once."""
    path = tmp_path / "help.yml"
    path.write_text(_GOOD_HELP, encoding="utf-8")
    host = _Host()
    host._config = replace(host._config, help=path)
    host._help = load_help(path)
    previous = host._help
    ada = _staff("Ada", RANK_ADMIN)
    assert ada.player is not None
    ada.player.colour = False
    host.people = [ada]
    path.write_bytes(_INVALID_UTF8_HELP)
    process_commands(host, ada, "reload help")  # type: ignore[arg-type]
    assert host._help is previous
    assert len(host.sent) == 1
    assert host.sent[0].startswith("Cannot reload help: Help file ")
    assert "is not valid UTF-8:" in host.sent[0]
    assert host.admin_lines == []
    assert host._help_failed == help_stamp(path)


def test_reload_help_keeps_the_book_when_the_file_is_bad(tmp_path: Path) -> None:
    """A bad file leaves the old book and tells the admin the load error."""
    path = tmp_path / "help.yml"
    path.write_text("- name: say\n  aliases: []\n", encoding="utf-8")
    host = _Host()
    host._config = replace(host._config, help=path)
    previous = host._help
    ada = _staff("Ada", RANK_ADMIN)
    assert ada.player is not None
    ada.player.colour = False
    host.people = [ada]
    process_commands(host, ada, "reload help")  # type: ignore[arg-type]
    assert host._help is previous
    assert host.sent == ["Cannot reload help: Help entry say is missing text.\n"]
    assert host.admin_lines == []
    assert host._help_failed == help_stamp(path)


def test_reload_help_reads_the_file_when_the_stamp_matches(tmp_path: Path) -> None:
    """reload help reads the file even when the stamp already matches."""  # noqa: D403
    path = tmp_path / "help.yml"
    path.write_text("- name: say\n  aliases: []\n  text: Hello.\n", encoding="utf-8")
    host = _Host()
    host._config = replace(host._config, help=path)
    host._help_stamp = help_stamp(path)
    ada = _staff("Ada", RANK_ADMIN)
    host.people = [ada]
    process_commands(host, ada, "reload help")  # type: ignore[arg-type]
    loaded = host._help.find("say")
    assert loaded is not None
    assert loaded.text == "Hello."


def test_reload_of_an_unknown_type_does_not_read_the_file(tmp_path: Path) -> None:
    """reload rooms is refused before the help file is opened."""  # noqa: D403
    path = tmp_path / "help.yml"
    path.write_text("- name: say\n  aliases: []\n  text: Hello.\n", encoding="utf-8")
    host = _Host()
    host._config = replace(host._config, help=path)
    previous = host._help
    ada = _staff("Ada", RANK_ADMIN)
    host.people = [ada]
    process_commands(host, ada, "reload rooms")  # type: ignore[arg-type]
    assert host._help is previous
    assert host.sent == ["reload for rooms not found\n"]


def test_bare_reload_replies_with_the_syntax() -> None:
    """reload with no type uses the normal syntax reply."""  # noqa: D403
    ada = _staff("Ada", RANK_ADMIN)
    host = _Host(people=[ada])
    process_commands(host, ada, "reload")  # type: ignore[arg-type]
    assert host.sent == ["Syntax: reload <type>\n"]


def test_a_super_user_cannot_reload() -> None:
    """Reload is an admin command, so a super user does not see it."""
    ada = _staff("Ada", RANK_SUPER)
    host = _Host(people=[ada])
    process_commands(host, ada, "reload help")  # type: ignore[arg-type]
    assert host.sent == ["No such command 'reload'\n"]


_GOOD_HELP = "- name: say\n  aliases: []\n  text: Hello.\n"
_BAD_HELP = "- name: say\n  aliases: []\n"
_INVALID_UTF8_HELP = b"- name: say\n  aliases: []\n  text: caf\xe9\n"


def _help_host(tmp_path: Path, text: str) -> tuple[_Host, Path]:
    """Return an admin host whose help file is ``text`` and whose stamp matches it."""
    path = tmp_path / "help.yml"
    path.write_text(text, encoding="utf-8")
    host = _Host()
    host._config = replace(host._config, help=path)
    host._help_stamp = help_stamp(path)
    ada = _staff("Ada", RANK_ADMIN)
    assert ada.player is not None
    ada.player.colour = False
    host.people = [ada]
    return host, path


def test_help_is_not_checked_before_five_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """299 seconds leaves the book alone."""
    host, path = _help_host(tmp_path, _GOOD_HELP)
    previous = host._help
    path.write_text(_GOOD_HELP.replace("Hello.", "Updated."), encoding="utf-8")
    _advance(host, monkeypatch, 299)
    assert host._help is previous
    assert host.admin_lines == []


def test_an_unchanged_help_file_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """300 seconds with the same stamp does not replace the book."""
    host, _path = _help_host(tmp_path, _GOOD_HELP)
    previous = host._help
    _advance(host, monkeypatch, 300)
    assert host._help is previous
    assert host.admin_lines == []
    assert host._help_check_seconds == 0


def test_a_changed_help_file_reloads_without_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new good file replaces the book and the admin channel stays quiet."""
    host, path = _help_host(tmp_path, _GOOD_HELP)
    path.write_text(_GOOD_HELP.replace("Hello.", "Updated."), encoding="utf-8")
    _advance(host, monkeypatch, 300)
    loaded = host._help.find("say")
    assert loaded is not None
    assert loaded.text == "Updated."
    assert host.admin_lines == []


def test_a_non_utf8_help_file_is_announced_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-UTF-8 file keeps the book and announces that stamp once."""
    host, path = _help_host(tmp_path, _GOOD_HELP)
    host._help = load_help(path)
    previous = host._help
    path.write_bytes(_INVALID_UTF8_HELP)
    _advance(host, monkeypatch, 300)
    assert host._help is previous
    assert len(host.admin_lines) == 1
    assert host.admin_lines[0].startswith("[ Admin ] Cannot reload help: Help file ")
    assert "is not valid UTF-8:" in host.admin_lines[0]
    _advance(host, monkeypatch, 300)
    assert len(host.admin_lines) == 1


def test_a_bad_change_is_announced_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same failed stamp does not announce again. 700 seconds checks once."""
    host, path = _help_host(tmp_path, _GOOD_HELP)
    previous = host._help
    path.write_text(_BAD_HELP, encoding="utf-8")
    _advance(host, monkeypatch, 700)
    assert host._help is previous
    assert host._help_check_seconds == 100
    assert host.admin_lines == ["[ Admin ] Cannot reload help: Help entry say is missing text.\n"]
    _advance(host, monkeypatch, 300)
    assert host._help is previous
    assert len(host.admin_lines) == 1


def test_a_missing_help_file_is_announced_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting the file keeps the book and announces that version once."""
    host, path = _help_host(tmp_path, _GOOD_HELP)
    previous = host._help
    path.unlink()
    _advance(host, monkeypatch, 300)
    assert host._help is previous
    assert len(host.admin_lines) == 1
    assert host.admin_lines[0].startswith("[ Admin ] Cannot reload help: Cannot read help file ")
    _advance(host, monkeypatch, 300)
    assert len(host.admin_lines) == 1


def test_a_fix_after_a_failed_reload_clears_the_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later good file replaces the book and does not send another admin line."""
    host, path = _help_host(tmp_path, _GOOD_HELP)
    path.write_text(_BAD_HELP, encoding="utf-8")
    _advance(host, monkeypatch, 300)
    path.write_text(_GOOD_HELP.replace("Hello.", "Fixed."), encoding="utf-8")
    _advance(host, monkeypatch, 300)
    loaded = host._help.find("say")
    assert loaded is not None
    assert loaded.text == "Fixed."
    assert len(host.admin_lines) == 1


def test_a_manual_failure_is_not_announced_again_by_the_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reload help records the failed stamp, so the timer stays quiet."""
    host, path = _help_host(tmp_path, _GOOD_HELP)
    ada = host.people[0]
    path.write_text(_BAD_HELP, encoding="utf-8")
    process_commands(host, ada, "reload help")  # type: ignore[arg-type]
    assert host.admin_lines == []
    _advance(host, monkeypatch, 300)
    assert host.admin_lines == []


def test_a_later_bad_save_is_announced_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new failed stamp is announced once, after an earlier failure."""
    host, path = _help_host(tmp_path, _GOOD_HELP)
    path.write_text(_BAD_HELP, encoding="utf-8")
    _advance(host, monkeypatch, 300)
    path.write_text("- name: look\n  aliases: []\n", encoding="utf-8")
    _advance(host, monkeypatch, 300)
    assert host.admin_lines == [
        "[ Admin ] Cannot reload help: Help entry say is missing text.\n",
        "[ Admin ] Cannot reload help: Help entry look is missing text.\n",
    ]


def test_help_is_not_reloaded_while_the_server_is_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stopping server does not read a changed help file."""
    host, path = _help_host(tmp_path, _GOOD_HELP)
    previous = host._help
    path.write_text(_GOOD_HELP.replace("Hello.", "Updated."), encoding="utf-8")
    host.stopped = True
    _advance(host, monkeypatch, 300)
    assert host._help is previous


def test_a_login_that_never_enters_is_timed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three minutes from accept, a connection with no room is told and dropped."""
    host = _login_host(tmp_path)
    waiting = _Player(
        name=None,
        room=None,
        player=None,
        auth="set",
        pending_name="Bea",
        accepted_at=1_000.0,
    )
    host.people = [waiting]
    host._clients = {"s": waiting}  # type: ignore[attr-defined]
    _advance(host, monkeypatch, 1_000.0 + 179)
    assert waiting not in host.dropped_clients
    assert host.sent == []
    _advance(host, monkeypatch, 1_000.0 + 180)
    assert host.sent == ["Login timed out.\n"]
    assert waiting in host.dropped_clients
    assert "bea" not in host._unsaved_names()


def test_a_player_in_a_room_is_not_login_timed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A player who already has a room is not dropped by the login limit."""
    host = _login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    bea = _Player(player=user, accepted_at=0.0)
    host._clients = {"b": bea}  # type: ignore[attr-defined]
    _advance(host, monkeypatch, 10_000.0)
    assert bea not in host.dropped_clients
    assert host.sent == []


def test_process_timer_adds_the_seconds_to_the_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each whole second applied to idle is also added to the connected total."""
    host = _login_host(tmp_path)
    host._timer_mark = 10.0  # type: ignore[attr-defined]
    host._timer_remainder = 0.0  # type: ignore[attr-defined]
    host._clients = {}  # type: ignore[attr-defined]
    user = Player("Ada", "hall", datetime.now(timezone.utc))
    host._clients["a"] = _Player(player=user)  # type: ignore[attr-defined]
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: 13.0)
    process_timer(host)  # type: ignore[arg-type]
    assert user.idle_seconds == 3
    assert user.connected_seconds == 3


def test_login_loads_colour_off(tmp_path: Path) -> None:
    """A saved player starts from the colour stored in the file."""
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), colour=False),
    )
    host = _login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    entry_api._load_profile(host, user)  # type: ignore[arg-type]
    assert user.colour is False


def test_entry_loads_the_saved_total(tmp_path: Path) -> None:
    """A saved player enters with the total from the file, already flushed."""
    _seconds_saved(tmp_path, 7200)
    host = _login_host(tmp_path)
    bea = _Player(player=None)
    process_input(host, bea, "Bea")  # type: ignore[arg-type]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    assert bea.player is not None
    assert bea.player.connected_seconds == 7200
    assert bea.player.flushed_seconds == 7200


def test_a_newbie_enters_with_no_total(tmp_path: Path) -> None:
    """A name with no file starts the total at zero."""
    host = _login_host(tmp_path)
    ada = _Player(player=None)
    process_input(host, ada, "Ada")  # type: ignore[arg-type]
    process_input(host, ada, "yes")  # type: ignore[arg-type]
    assert ada.player is not None
    assert ada.player.connected_seconds == 0
    assert ada.player.flushed_seconds == 0


def test_a_new_resident_is_saved_with_the_total_so_far(tmp_path: Path) -> None:
    """Promotion stores the seconds already counted and marks them flushed."""
    bea = _newbie("Bea")
    assert bea.player is not None
    bea.player.connected_seconds = 120
    ada = _staff("Ada", RANK_SUPER)
    host = _login_host(tmp_path)
    host.people = [bea, ada]
    process_commands(host, ada, "resident Bea")  # type: ignore[arg-type]
    process_input(host, bea, "hunter2")  # type: ignore[arg-type]
    process_input(host, bea, "hunter2")  # type: ignore[arg-type]
    assert _saved_seconds(tmp_path) == 120
    assert bea.player.flushed_seconds == 120


def test_a_newbie_cannot_set_entermsg() -> None:
    """The command does not exist below resident."""
    host, _player = _run("entermsg crawls into the room")
    assert host.sent == ["No such command 'entermsg'\n"]


def test_a_resident_sets_and_clears_entermsg(tmp_path: Path) -> None:
    """The sentence is saved, and a bare command removes it."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    process_commands(host, bea, "entermsg crawls into the room")  # type: ignore[arg-type]
    assert host.sent[-1] == "Enter message set.\n"
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.entermsg == "crawls into the room"
    host.sent.clear()
    process_commands(host, bea, "entermsg")  # type: ignore[arg-type]
    assert "Enter message cleared.\n" in host.sent[-1]
    cleared = load_saved(player_path(tmp_path, "Bea"))
    assert cleared is not None
    assert cleared.entermsg is None


def test_entermsg_rejects_more_than_80_characters(tmp_path: Path) -> None:
    """A long sentence is refused and the file stays unchanged."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    process_commands(host, bea, "entermsg " + ("x" * 81))  # type: ignore[arg-type]
    assert "An enter message is at most 80 characters.\n" in "".join(host.sent)
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    assert saved.entermsg is None


def _entermsg_of(tmp_path: Path) -> str | None:
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    return saved.entermsg


def test_the_default_entermsg_stores_no_key(tmp_path: Path) -> None:
    """Choosing the usual line removes a saved enter message."""
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            entermsg="crawls into the room",
        ),
    )
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    process_commands(host, bea, "entermsg arrives.")  # type: ignore[arg-type]
    assert _entermsg_of(tmp_path) is None
    assert "entermsg" not in (tmp_path / "bea.yml").read_text(encoding="utf-8")
    assert bea.player is not None
    assert bea.player.entermsg == "arrives."
    assert host.sent == ["Enter message set.\n"]


def test_save_after_default_entermsg_stores_no_key(tmp_path: Path) -> None:
    """Save keeps the default enter message out of the player file."""
    save_player(
        tmp_path,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            entermsg="crawls into the room",
        ),
    )
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    process_commands(host, bea, "entermsg arrives.")  # type: ignore[arg-type]
    host.sent.clear()
    process_commands(host, bea, "save")  # type: ignore[arg-type]
    assert _entermsg_of(tmp_path) is None
    assert "entermsg" not in (tmp_path / "bea.yml").read_text(encoding="utf-8")


def test_a_newbie_cannot_set_a_prompt() -> None:
    """A newbie is told prompt does not exist, and help hides it."""
    when = datetime.now(timezone.utc)
    newbie = _Player(player=Player("Ada", "reception", when, rank=RANK_NEWBIE))
    host, _player = _run("prompt Go:", newbie)
    assert host.sent == ["No such command 'prompt'\n"]
    assert newbie.player is not None
    assert newbie.player.prompt == "What? > "

    book = HelpBook(entries=(HelpEntry("say", (), "Speak."), HelpEntry("prompt", (), "Set it.")))
    helped = _Host(_help=book)
    process_commands(helped, newbie, "help prompt")  # type: ignore[arg-type]
    assert helped.sent == ["help for prompt not found\n"]
    listed = _Host(_help=book)
    process_commands(listed, newbie, "help")  # type: ignore[arg-type]
    assert listed.sent == ["Syntax: help <topic>\n"]

    assert "prompt" not in command_words(RANK_NEWBIE).split()
    assert "prompt" in command_words(RANK_RESIDENT).split()
    assert command_words(RANK_SUPER).split().count("prompt") == 1
    assert command_words(RANK_ADMIN).split().count("prompt") == 1


def _prompt_of(tmp_path: Path) -> str | None:
    saved = load_saved(player_path(tmp_path, "Bea"))
    assert saved is not None
    return saved.prompt


def test_a_resident_prompt_is_saved_and_loaded_at_login(tmp_path: Path) -> None:
    """The file keeps ``Go: `` and the next login of that player types at it."""
    _seconds_saved(tmp_path, 0)
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    process_commands(host, bea, "prompt Go:")  # type: ignore[arg-type]
    assert host.sent == ["Prompt set.\n"]
    assert _prompt_of(tmp_path) == "Go: "

    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), prompt="Go: "),
    )
    login_host = _login_host(tmp_path)
    again = _Player(player=None)
    process_input(login_host, again, "Bea")  # type: ignore[arg-type]
    process_input(login_host, again, "pw")  # type: ignore[arg-type]
    assert again.player is not None
    assert again.player.prompt == "Go: "
    assert login_host.sent[-1] == "Go: "


def test_a_file_without_a_prompt_keeps_the_default(tmp_path: Path) -> None:
    """No prompt key leaves ``What? > `` on the new session."""
    _seconds_saved(tmp_path, 0)
    host = _login_host(tmp_path)
    bea = _Player(player=None)
    process_input(host, bea, "Bea")  # type: ignore[arg-type]
    process_input(host, bea, "pw")  # type: ignore[arg-type]
    assert bea.player is not None
    assert bea.player.prompt == "What? > "


def test_the_default_prompt_stores_no_key(tmp_path: Path) -> None:
    """Choosing the usual line removes a saved prompt."""
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), prompt="Go: "),
    )
    host = _login_host(tmp_path)
    bea = _staff("Bea", RANK_RESIDENT)
    process_commands(host, bea, "prompt What? >")  # type: ignore[arg-type]
    assert _prompt_of(tmp_path) is None
    assert "prompt" not in (tmp_path / "bea.yml").read_text(encoding="utf-8")
    assert bea.player is not None
    assert bea.player.prompt == "What? > "
    assert host.sent == ["Prompt set.\n"]


def test_becoming_a_resident_stores_no_prompt(tmp_path: Path) -> None:
    """A newbie's prompt is never written when they are promoted."""
    bea = _newbie("Bea")
    assert bea.player is not None
    ada = _staff("Ada", RANK_SUPER)
    host = _login_host(tmp_path)
    host.people = [bea, ada]
    process_commands(host, ada, "resident Bea")  # type: ignore[arg-type]
    process_input(host, bea, "hunter2")  # type: ignore[arg-type]
    process_input(host, bea, "hunter2")  # type: ignore[arg-type]
    assert _prompt_of(tmp_path) is None
    assert "prompt" not in (tmp_path / "bea.yml").read_text(encoding="utf-8")


def test_custom_prompt_ends_with_a_space() -> None:
    """A custom prompt gains one trailing space and keeps it."""
    user = Player("Ada", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    ada = _Player(player=user)
    host, _player = _run("prompt Hi:", ada)
    assert user.prompt == "Hi: "
    assert host.sent[-1] == "Prompt set.\n"


def test_commands_wrap_on_visible_width() -> None:
    """Colour codes do not make the command list wrap early."""
    on_user = Player("Ada", "hall", datetime.now(timezone.utc), rank=RANK_ADMIN, colour=True)
    off_user = Player("Ada", "hall", datetime.now(timezone.utc), rank=RANK_ADMIN, colour=False)
    on, _player = _run("commands", _Player(player=on_user, frame_columns=60))
    off, _player = _run("commands", _Player(player=off_user, frame_columns=60))
    on_body = on.sent[0].split("Commands available:\n", 1)[1]
    off_body = off.sent[0].split("Commands available:\n", 1)[1]
    on_lines = on_body.splitlines()
    off_lines = off_body.splitlines()
    assert [plain_len(line) for line in on_lines] == [len(line) for line in off_lines]
    for line in on_lines:
        assert plain_len(line) <= 60


def test_player_prompt_adds_a_space_to_a_legacy_saved_value(tmp_path: Path) -> None:
    """A file that kept ``Hi:`` still types at ``Hi: ``."""
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4), prompt="Hi:"),
    )
    login_host = _login_host(tmp_path)
    again = _Player(player=None)
    process_input(login_host, again, "Bea")  # type: ignore[arg-type]
    process_input(login_host, again, "pw")  # type: ignore[arg-type]
    assert again.player is not None
    assert again.player.prompt == "Hi:"
    assert entry_api.player_prompt(again) == "Hi: "
    assert login_host.sent[-1] == "Hi: "


def test_examine_and_lsu_keep_the_same_line_count_with_colour(tmp_path: Path) -> None:
    """Colour codes do not add or remove frame lines."""
    host = _login_host(tmp_path)
    ada = _staff("Ada", RANK_SUPER)
    assert ada.player is not None
    ada.player.idle_seconds = 0
    boss = _staff("admin", RANK_ADMIN)
    assert boss.player is not None
    boss.player.idle_seconds = 3723
    host.people = [boss, ada]

    process_commands(host, ada, "examine me")  # type: ignore[arg-type]
    examine_on = host.sent[0]
    for line in examine_on.splitlines():
        assert plain_len(line) == 72
    ada.player.colour = False
    host.sent.clear()
    process_commands(host, ada, "examine me")  # type: ignore[arg-type]
    assert host.sent[0].count("\n") == examine_on.count("\n")

    ada.player.colour = True
    host.sent.clear()
    process_commands(host, ada, "lsu")  # type: ignore[arg-type]
    lsu_on = host.sent[0]
    for line in lsu_on.splitlines():
        assert plain_len(line) == 72
    ada.player.colour = False
    host.sent.clear()
    process_commands(host, ada, "lsu")  # type: ignore[arg-type]
    assert host.sent[0].count("\n") == lsu_on.count("\n")


def test_coloured_frames_match_the_terminal_width(tmp_path: Path) -> None:
    """A red admin rank and a plain resident rank use the same rule length."""
    from lupaxa.chat_server.colour import plain_len

    host = _login_host(tmp_path)
    resident = _staff("Bea", RANK_RESIDENT)
    boss = _staff("Ada", RANK_ADMIN)
    assert resident.player is not None
    assert boss.player is not None
    resident.player.colour = True
    boss.player.colour = True
    host.people = [resident, boss]

    process_commands(host, resident, "examine me")  # type: ignore[arg-type]
    resident_rules = [line for line in host.sent[0].splitlines() if set(line) == {"-"}]
    process_commands(host, boss, "examine me")  # type: ignore[arg-type]
    admin_text = host.sent[-1]
    admin_rules = [line for line in admin_text.splitlines() if set(line) == {"-"}]
    assert resident_rules[0] == "-" * 72
    assert admin_rules[0] == "-" * 72
    for line in admin_text.splitlines():
        assert plain_len(line) == 72

    host.sent.clear()
    host.newbies = [("Cyd", "10.0.0.8", "reception", 0)]
    process_commands(host, boss, "lsn")  # type: ignore[arg-type]
    process_commands(host, boss, "lsu")  # type: ignore[arg-type]
    lsn_rule = host.sent[0].splitlines()[0]
    lsu_rule = host.sent[1].splitlines()[0]
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


def test_ten_seconds_rolls_port_and_connection_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One 10-second step rolls telnet, combined, and the connection."""
    host = _login_host(tmp_path)
    host._timer_mark = 0.0
    host._timer_remainder = 0.0
    person = _Player(player=Player("Ada", "hall", datetime.now(timezone.utc)))
    person.net.in_current = 25
    host._telnet_net.in_current = 25
    host._combined_net.in_current = 25
    host.people = [person]
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: 10.0)
    process_timer(host)  # type: ignore[arg-type]
    assert host._net_count == 10
    assert host._telnet_net.in_total == 25
    assert host._telnet_net.in_bps == 2
    assert host._telnet_net.in_average == 1
    assert host._telnet_net.in_current == 0
    assert person.net.in_total == 25
    assert person.net.in_current == 0


def test_a_short_window_is_still_divided_by_ten(tmp_path: Path) -> None:
    """Three bytes in a window that closes early have a rate of 0."""
    host = _login_host(tmp_path)
    host._net_count = 1
    person = _Player()
    person.net.in_current = 3
    host.people = [person]
    apply_net_tick(host, 1)  # type: ignore[arg-type]
    assert person.net.in_bps == 0
    assert person.net.in_total == 3


def test_a_fifteen_second_tick_rolls_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A late tick divides by 10 once and sets the countdown back to 10."""
    host = _login_host(tmp_path)
    host._timer_mark = 0.0
    host._timer_remainder = 0.0
    host._telnet_net.in_current = 100
    monkeypatch.setattr("lupaxa.chat_server.timer.time.monotonic", lambda: 15.0)
    process_timer(host)  # type: ignore[arg-type]
    assert host._net_count == 10
    assert host._telnet_net.in_bps == 10
    assert host._telnet_net.in_total == 100


def test_telnet_and_ssh_cannot_be_names() -> None:
    """Those words are reserved in any case, like me."""
    for typed in ("telnet", "SSH", " Telnet "):
        decision = consider_name(typed, set())
        assert decision.name is None
        assert decision.reply == (
            f"{typed.strip()} cannot be used. Pick another name.\n\nPlease enter your name: "
        )


def test_a_resident_cannot_use_network(tmp_path: Path) -> None:
    """Below admin, network is not a command."""
    host = _login_host(tmp_path)
    user = Player("Ada", "reception", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    player = _Player(player=user)
    process_commands(host, player, "network")  # type: ignore[arg-type]
    assert player.sent[-1] == "No such command 'network'\n"


def test_network_with_no_argument_shows_the_combined_box(tmp_path: Path) -> None:
    """Bare network is the combined counter."""
    host = _login_host(tmp_path)
    host._combined_net.in_total = 1500
    host._telnet_net.in_total = 9
    user = Player("Ann", "reception", datetime.now(timezone.utc), rank=RANK_ADMIN)
    player = _Player(player=user)
    host.people = [player]
    process_commands(host, player, "network")  # type: ignore[arg-type]
    assert "1.5 KB" in player.sent[-1]
    assert "9 B" not in player.sent[-1]


def test_network_telnet_and_ssh_select_those_ports(tmp_path: Path) -> None:
    """The port words win in any case."""
    host = _login_host(tmp_path)
    host._telnet_net.in_total = 1500
    host._ssh_net.out_total = 300_000_000
    user = Player("Ann", "reception", datetime.now(timezone.utc), rank=RANK_ADMIN)
    player = _Player(player=user)
    host.people = [player]
    process_commands(host, player, "network TELNET")  # type: ignore[arg-type]
    assert "1.5 KB" in player.sent[-1]
    process_commands(host, player, "network ssh")  # type: ignore[arg-type]
    assert "300.0 MB" in player.sent[-1]


def test_network_shows_one_connection_from_accept(tmp_path: Path) -> None:
    """The player's box is the connection counter, including login bytes."""
    host = _login_host(tmp_path)
    user = Player("Bea", "hall", datetime.now(timezone.utc), rank=RANK_RESIDENT)
    bea = _Player(name="Bea", player=user, room="hall")
    bea.net.in_total = 999
    admin_user = Player("Ann", "reception", datetime.now(timezone.utc), rank=RANK_ADMIN)
    admin = _Player(player=admin_user)
    host.people = [admin, bea]
    process_commands(host, admin, "network bea")  # type: ignore[arg-type]
    assert "999 B" in admin.sent[-1]


def test_network_reports_a_missing_player_and_extra_words(tmp_path: Path) -> None:
    """An unknown name and a second word are refused."""
    host = _login_host(tmp_path)
    user = Player("Ann", "reception", datetime.now(timezone.utc), rank=RANK_ADMIN)
    player = _Player(player=user)
    host.people = [player]
    process_commands(host, player, "network Ada")  # type: ignore[arg-type]
    assert player.sent[-1] == "Player Ada not found.\n"
    process_commands(host, player, "network telnet please")  # type: ignore[arg-type]
    assert player.sent[-1] == "Syntax: network [telnet|ssh|<player>]\n"


def test_a_saved_player_file_has_no_network_fields(tmp_path: Path) -> None:
    """Traffic counters are not written to the player file."""
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    text = player_path(tmp_path, "Bea").read_text(encoding="utf-8")
    assert "in_total" not in text
    assert "in_bps" not in text
