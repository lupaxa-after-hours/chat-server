"""Two clients on a real listening socket."""

from __future__ import annotations

import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lupaxa.chat_server import server as server_module
from lupaxa.chat_server.channel import player_named
from lupaxa.chat_server.client import Client
from lupaxa.chat_server.config import Config
from lupaxa.chat_server.defaults import DEFAULT_HOST
from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.help import HelpBook
from lupaxa.chat_server.player import Player
from lupaxa.chat_server.rooms import Layout, load_layout
from lupaxa.chat_server.saved_player import (
    RANK_ADMIN,
    RANK_RESIDENT,
    RANK_SUPER,
    SavedPlayer,
    hash_password,
    load_saved,
    player_path,
    save_player,
)
from lupaxa.chat_server.server import ChatServer, _strip_telnet


@pytest.fixture(autouse=True)
def stub_reverse_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub reverse DNS so live server tests do not hit the network."""
    monkeypatch.setattr(server_module, "lookup_hostname", lambda ip: ip)


def _free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind((DEFAULT_HOST, 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def _connect(port: int | None, *, recv_buffer: int | None = None) -> socket.socket:
    if port is None:
        raise AssertionError("this server has no telnet port")
    deadline = time.monotonic() + 2
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if recv_buffer is not None:
            # Shrink the window before connect so a stopped reader fills the
            # server's user-space queue without waiting on a large kernel buffer.
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, recv_buffer)
        client.settimeout(0.5)
        try:
            client.connect((DEFAULT_HOST, port))
        except OSError as exc:
            last_error = exc
            client.close()
            time.sleep(0.05)
            continue
        client.settimeout(2)
        return client
    raise AssertionError(f"could not connect: {last_error}")


def _read_until(client: socket.socket, needle: bytes) -> bytes:
    data = b""
    while needle not in data:
        chunk = client.recv(4096)
        assert chunk, data
        data += chunk
    return data


_TINY = """\
reception:
  description: The front desk faces the street. A bell sits on the counter.
  max_occupancy: 4
  exits:
    w: kitchen
    s: toilet
kitchen:
  description: The kitchen smells of coffee. Mugs crowd the sink.
  max_occupancy: 2
  exits:
    e: reception
toilet:
  description: A single cubicle and a sink. The lock sticks.
  max_occupancy: 1
  exits:
    n: reception
"""


def _layout(tmp_path: Path, text: str = _TINY) -> Layout:
    path = tmp_path / "rooms.yml"
    path.write_text(text, encoding="utf-8")
    return load_layout(path)


def _config(
    start: str = "reception", *, players: Path | None = None, admin: str = "admin"
) -> Config:
    return Config(
        port=_free_port(),
        rooms=Path("rooms.yml"),
        start=start,
        help=Path("help.yml"),
        players=players if players is not None else Path("players"),
        admin=admin,
        mfa_enabled=False,
    )


def _players(tmp_path: Path, admin: str = "admin") -> Path:
    directory = tmp_path / "players"
    directory.mkdir()
    save_player(directory, SavedPlayer(admin, RANK_ADMIN, hash_password("pw", rounds=4)))
    return directory


def _read_to_close(client: socket.socket) -> bytes:
    data = b""
    while True:
        chunk = client.recv(4096)
        if chunk == b"":
            return data
        data += chunk


def _hears_nothing(client: socket.socket) -> bool:
    client.settimeout(0.3)
    try:
        return client.recv(4096) == b""
    except TimeoutError:
        return True
    finally:
        client.settimeout(2)


def test_server_copies_allow_flags(tmp_path: Path) -> None:
    """Startup copies the file. It does not keep a second default."""
    config = Config(
        port=_free_port(),
        rooms=Path("rooms.yml"),
        start="reception",
        help=Path("help.yml"),
        players=tmp_path,
        allow_newbies=False,
        allow_residents=True,
        mfa_enabled=False,
    )
    server = ChatServer(config, _layout(tmp_path), HelpBook(entries=()))
    assert server._allow_newbies is False
    assert server._allow_residents is True


def test_the_server_records_when_it_was_created(tmp_path: Path) -> None:
    """started_at is UTC and is the moment the object is created."""
    before = datetime.now(timezone.utc)
    server = ChatServer(
        _config(players=_players(tmp_path)),
        _layout(tmp_path),
        HelpBook(entries=()),
    )
    after = datetime.now(timezone.utc)
    assert server.started_at.tzinfo is timezone.utc
    assert before <= server.started_at <= after


def test_say_reaches_the_other_client(tmp_path: Path) -> None:
    """A named player's say line is delivered to the other player."""
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    left: socket.socket | None = None
    right: socket.socket | None = None
    try:
        left = _connect(server._config.port)
        right = _connect(server._config.port)
        assert b"Please enter your name: " in _read_until(left, b"Please enter your name: ")
        assert b"Please enter your name: " in _read_until(right, b"Please enter your name: ")
        left.sendall(b"Ada\n")
        right.sendall(b"Bea\n")
        assert b"[Bea has connected]\n" in _read_until(left, b"[Bea has connected]\n")
        left.sendall(b"say hello\n")
        heard = _read_until(right, b"- Ada says 'hello'\n")
        assert b"- Ada says 'hello'\n" in heard
    finally:
        if left is not None:
            left.close()
        if right is not None:
            right.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_tell_and_shout_cross_rooms(tmp_path: Path) -> None:
    """Tell and shout reach another room. A nameless connection hears neither."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Ada", RANK_RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ada: socket.socket | None = None
    bea: socket.socket | None = None
    waiting: socket.socket | None = None
    try:
        ada = _connect(server._config.port)
        bea = _connect(server._config.port)
        waiting = _connect(server._config.port)
        for client in (ada, bea, waiting):
            assert b"Please enter your name: " in _read_until(client, b"Please enter your name: ")
        ada.sendall(b"Ada\n")
        assert b"Password: " in _read_until(ada, b"Password: ")
        ada.sendall(b"pw\n")
        assert b"What? > " in _read_until(ada, b"What? > ")
        bea.sendall(b"Bea\n")
        assert b"[Bea has connected]\n" in _read_until(ada, b"[Bea has connected]\n")
        bea.sendall(b"go west\n")
        assert b"Bea goes west to Kitchen.\n" in _read_until(ada, b"Bea goes west to Kitchen.\n")
        from lupaxa.chat_server.colour import green, yellow

        ada.sendall(b"tell Bea hello\n")
        tell = (green("> Ada tells you 'hello'") + "\n").encode()
        heard = _read_until(bea, tell)
        assert tell in heard
        ada.sendall(b"!hey\n")
        shout = (yellow("! Ada shouts 'hey'") + "\n").encode()
        heard = _read_until(bea, shout)
        assert shout in heard
        waiting.settimeout(0.3)
        try:
            extra = waiting.recv(4096)
        except TimeoutError:
            extra = b""
        assert b"tells you" not in extra
        assert b"shouts" not in extra
    finally:
        if ada is not None:
            ada.close()
        if bea is not None:
            bea.close()
        if waiting is not None:
            waiting.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_overlong_line_disconnects(tmp_path: Path) -> None:
    """More than 1024 characters after stripping drops the client."""
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = _connect(server._config.port)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"a" * 1025 + b"\n")
        leftover = b""
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            leftover += chunk
        assert leftover == b""
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_idle_reader_is_dropped_when_outgoing_exceeds_cap(tmp_path: Path) -> None:
    """A client that stops reading is dropped once queued output exceeds 64 KiB."""
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sender: socket.socket | None = None
    idle: socket.socket | None = None
    try:
        sender = _connect(server._config.port)
        idle = _connect(server._config.port, recv_buffer=4096)
        assert b"Please enter your name: " in _read_until(sender, b"Please enter your name: ")
        assert b"Please enter your name: " in _read_until(idle, b"Please enter your name: ")
        sender.sendall(b"Ada\n")
        idle.sendall(b"Bea\n")
        assert b"[Bea has connected]\n" in _read_until(sender, b"[Bea has connected]\n")
        sender.setblocking(False)
        needle = b"[Bea has disconnected]\n"
        line = b"say " + (b"x" * 1000) + b"\n"
        offset = 0
        heard = b""
        deadline = time.monotonic() + 10
        while needle not in heard:
            if time.monotonic() >= deadline:
                raise AssertionError("idle client was not disconnected before the deadline")
            if offset >= len(line):
                offset = 0
            try:
                sent = sender.send(line[offset:])
            except BlockingIOError:
                sent = 0
            else:
                offset += sent
            try:
                chunk = sender.recv(65536)
            except BlockingIOError:
                # A tight spin here starves the server thread on a small CI runner.
                time.sleep(0.01)
                continue
            assert chunk, heard
            heard += chunk
        assert needle in heard
    finally:
        if sender is not None:
            sender.close()
        if idle is not None:
            idle.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_say_stays_in_the_room(tmp_path: Path) -> None:
    """A say in Kitchen is not delivered to Reception."""
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    left: socket.socket | None = None
    right: socket.socket | None = None
    try:
        left = _connect(server._config.port)
        right = _connect(server._config.port)
        _read_until(left, b"Please enter your name: ")
        _read_until(right, b"Please enter your name: ")
        left.sendall(b"Ada\n")
        right.sendall(b"Bea\n")
        _read_until(left, b"[Bea has connected]\n")
        left.sendall(b"go west\n")
        heard = _read_until(left, b"  east -> Reception\n")
        assert b"The kitchen smells of coffee." in heard
        assert b"Ada goes west to Kitchen.\n" in _read_until(right, b"Ada goes west to Kitchen.\n")
        left.sendall(b"say hello\n")
        right.settimeout(0.3)
        with pytest.raises(TimeoutError):
            right.recv(4096)
    finally:
        if left is not None:
            left.close()
        if right is not None:
            right.close()
        server.shutdown()
        thread.join(timeout=2)


def test_full_reception_disconnects_the_new_name(tmp_path: Path) -> None:
    """A full Reception closes the connection and does not keep the name."""
    text = _TINY.replace("max_occupancy: 4", "max_occupancy: 1", 1)
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path, text), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first: socket.socket | None = None
    second: socket.socket | None = None
    try:
        first = _connect(server._config.port)
        _read_until(first, b"Please enter your name: ")
        first.sendall(b"Ada\n")
        _read_until(first, b"  west  -> Kitchen\n")
        second = _connect(server._config.port)
        _read_until(second, b"Please enter your name: ")
        second.sendall(b"Bea\n")
        assert b"Reception is full.\n" in _read_until(second, b"Reception is full.\n")
        assert second.recv(64) == b""
        second = _connect(server._config.port)
        _read_until(second, b"Please enter your name: ")
        second.sendall(b"Bea\n")
        assert b"Reception is full.\n" in _read_until(second, b"Reception is full.\n")
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
        server.shutdown()
        thread.join(timeout=2)


def test_full_room_refuses_the_move(tmp_path: Path) -> None:
    """Toilet holds one person. The second player stays in Reception."""
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    left: socket.socket | None = None
    right: socket.socket | None = None
    try:
        left = _connect(server._config.port)
        right = _connect(server._config.port)
        _read_until(left, b"Please enter your name: ")
        _read_until(right, b"Please enter your name: ")
        left.sendall(b"Ada\n")
        right.sendall(b"Bea\n")
        _read_until(left, b"[Bea has connected]\n")
        left.sendall(b"go south\n")
        _read_until(left, b"What? > ")
        right.sendall(b"go south\n")
        assert b"Toilet is full.\n" in _read_until(right, b"Toilet is full.\n")
        right.sendall(b"say still here\n")
        left.settimeout(0.3)
        with pytest.raises(TimeoutError):
            left.recv(4096)
    finally:
        if left is not None:
            left.close()
        if right is not None:
            right.close()
        server.shutdown()
        thread.join(timeout=2)


def test_missing_exit_is_told_to_the_mover(tmp_path: Path) -> None:
    """A real direction with no exit does not move the player."""
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = _connect(server._config.port)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"Ada\n")
        _read_until(client, b"  west  -> Kitchen\n")
        client.sendall(b"go north\n")
        assert b"You cannot go north.\n" in _read_until(client, b"You cannot go north.\n")
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_arrival_is_told_to_the_new_room(tmp_path: Path) -> None:
    """Someone already in Kitchen hears Ada arrive from the east."""
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first: socket.socket | None = None
    second: socket.socket | None = None
    try:
        first = _connect(server._config.port)
        second = _connect(server._config.port)
        _read_until(first, b"Please enter your name: ")
        _read_until(second, b"Please enter your name: ")
        first.sendall(b"Bea\n")
        second.sendall(b"Ada\n")
        _read_until(first, b"[Ada has connected]\n")
        first.sendall(b"go west\n")
        _read_until(first, b"  east -> Reception\n")
        second.sendall(b"go west\n")
        assert b"Ada arrives from the east.\n" in _read_until(
            first, b"Ada arrives from the east.\n"
        )
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
        server.shutdown()
        thread.join(timeout=2)


def test_start_room_comes_from_config(tmp_path: Path) -> None:
    """A layout with no reception still starts in the configured room."""
    text = """\
arena:
  description: A court.
  max_occupancy: 1
  exits: {}
"""
    server = ChatServer(
        _config("arena", players=_players(tmp_path)), _layout(tmp_path, text), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first: socket.socket | None = None
    second: socket.socket | None = None
    try:
        first = _connect(server._config.port)
        _read_until(first, b"Please enter your name: ")
        first.sendall(b"Ada\n")
        assert b"A court.\n" in _read_until(first, b"Exits: none.\n")
        second = _connect(server._config.port)
        _read_until(second, b"Please enter your name: ")
        second.sendall(b"Bea\n")
        assert b"Arena is full.\n" in _read_until(second, b"Arena is full.\n")
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
        server.shutdown()
        thread.join(timeout=2)


def test_unknown_start_room_is_rejected(tmp_path: Path) -> None:
    """The configured start room must exist in the rooms file."""
    with pytest.raises(ConfigError, match="arena"):
        ChatServer(_config("arena"), _layout(tmp_path), HelpBook(entries=()))


def test_empty_return_shows_the_prompt_again(tmp_path: Path) -> None:
    """Enter at the name prompt and at What? > prints that prompt again."""
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = _connect(server._config.port)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"\n")
        assert b"Please enter your name: " in _read_until(client, b"Please enter your name: ")
        client.sendall(b"Ada\n")
        _read_until(client, b"What? > ")
        client.sendall(b"\n")
        heard = _read_until(client, b"What? > ")
        assert heard.endswith(b"What? > ")
        assert b"No such command" not in heard
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_empty_password_line_at_set_prompt_is_rejected(tmp_path: Path) -> None:
    """A blank line at Set a password reaches login and is refused."""
    players = tmp_path / "players"
    players.mkdir()
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = _connect(server._config.port)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"admin\n")
        _read_until(client, b"Set a password: ")
        client.sendall(b"\n")
        assert b"Password cannot be empty.\nSet a password: " in _read_until(
            client, b"Set a password: "
        )
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_admin_sets_a_password_then_logs_in(tmp_path: Path) -> None:
    """The admin name sets a password once, then needs it to come back."""
    players = tmp_path / "players"
    players.mkdir()
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def connect() -> socket.socket:
        client = _connect(server._config.port)
        opened.append(client)
        _read_until(client, b"Please enter your name: ")
        return client

    try:
        first = connect()
        first.sendall(b"admin\n")
        _read_until(first, b"Set a password: ")
        first.sendall(b"secret\n")
        _read_until(first, b"Confirm password: ")
        first.sendall(b"secret\n")
        assert b"The front desk faces the street." in _read_until(first, b"What? > ")
        first.sendall(b"quit\n")
        assert b"Goodbye!\n" in _read_to_close(first)

        watcher = connect()
        watcher.sendall(b"Ada\n")
        _read_until(watcher, b"What? > ")

        failing = connect()
        failing.sendall(b"admin\n")
        _read_until(failing, b"Password: ")
        failing.sendall(b"nope\n")
        assert b"Wrong password.\n" in _read_until(failing, b"Password: ")
        failing.sendall(b"nope\n")
        assert b"Wrong password.\n" in _read_until(failing, b"Password: ")
        failing.sendall(b"nope\n")
        assert b"Wrong password.\n" in _read_to_close(failing)
        assert _hears_nothing(watcher)

        returning = connect()
        returning.sendall(b"admin\n")
        _read_until(returning, b"Password: ")
        returning.sendall(b"secret\n")
        assert b"The front desk faces the street." in _read_until(returning, b"What? > ")
        assert b"[admin has connected]\n" in _read_until(watcher, b"[admin has connected]\n")
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()
    saved = load_saved(player_path(players, "admin"))
    assert saved is not None
    assert saved.rank == "super_admin"
    assert saved.logged_out_at is not None


def test_a_saved_name_answers_for_its_own_spelling(tmp_path: Path) -> None:
    """Ask for the password when a saved name is typed in another case."""
    players = tmp_path / "players"
    players.mkdir()
    config = _config(players=players, admin="Boss")
    server = ChatServer(config, _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []
    try:
        boss = _connect(server._config.port)
        opened.append(boss)
        _read_until(boss, b"Please enter your name: ")
        boss.sendall(b"Boss\n")
        _read_until(boss, b"Set a password: ")
        boss.sendall(b"secret\n")
        _read_until(boss, b"Confirm password: ")
        boss.sendall(b"secret\n")
        _read_until(boss, b"What? > ")
        boss.sendall(b"quit\n")
        _read_to_close(boss)

        other = _connect(server._config.port)
        opened.append(other)
        _read_until(other, b"Please enter your name: ")
        other.sendall(b"boss\n")
        reply = _read_until(other, b"Password: ")
        assert b"That name is saved as" not in reply
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_a_super_user_saves_a_newbie_as_a_resident(tmp_path: Path) -> None:
    """The password pair runs for a player who is already in the room, and staff hear of it."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", "admin", hash_password("pw", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def connect() -> socket.socket:
        client = _connect(server._config.port)
        opened.append(client)
        _read_until(client, b"Please enter your name: ")
        return client

    try:
        boss = connect()
        boss.sendall(b"admin\n")
        _read_until(boss, b"Password: ")
        boss.sendall(b"pw\n")
        _read_until(boss, b"What? > ")

        bea = connect()
        bea.sendall(b"Bea\n")
        _read_until(bea, b"What? > ")
        bea.sendall(b"shout hi\n")
        assert b"No such command 'shout'\n" in _read_until(bea, b"What? > ")

        from lupaxa.chat_server.colour import cyan

        boss.sendall(b"resident Bea\n")
        staff = (cyan("< SU > admin makes Bea a resident.") + "\n").encode()
        assert staff in _read_until(boss, b"What? > ")
        heard = _read_until(bea, b"Set a password: ")
        assert b"< SU >" not in heard
        bea.sendall(b"hunter2\n")
        _read_until(bea, b"Confirm password: ")
        assert load_saved(player_path(players, "Bea")) is None
        bea.sendall(b"hunter2\n")
        assert b"You are now a resident.\n" in _read_until(bea, b"What? > ")
        assert b"Bea is now a resident.\n" in _read_until(boss, b"Bea is now a resident.\n")
        bea.sendall(b"shout hi\n")
        assert b"You shout 'hi'\n" in _read_until(bea, b"What? > ")
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    saved = load_saved(player_path(players, "Bea"))
    assert saved is not None
    assert saved.rank == "resident"


def test_resident_disconnect_before_confirm_writes_nothing(tmp_path: Path) -> None:
    """Closing the socket during the password pair leaves no player file."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", "admin", hash_password("pw", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def connect() -> socket.socket:
        client = _connect(server._config.port)
        opened.append(client)
        _read_until(client, b"Please enter your name: ")
        return client

    try:
        boss = connect()
        boss.sendall(b"admin\n")
        _read_until(boss, b"Password: ")
        boss.sendall(b"pw\n")
        _read_until(boss, b"What? > ")

        bea = connect()
        bea.sendall(b"Bea\n")
        _read_until(bea, b"What? > ")

        boss.sendall(b"resident Bea\n")
        _read_until(bea, b"Set a password: ")
        bea.sendall(b"hunter2\n")
        _read_until(bea, b"Confirm password: ")
        bea.close()
        opened.remove(bea)
        heard = _read_until(boss, b"[Bea has disconnected from 127.0.0.1]\n")
        assert b"[Bea has disconnected from 127.0.0.1]\n" in heard
        assert load_saved(player_path(players, "Bea")) is None
        assert sorted(path.name for path in players.iterdir()) == ["admin.yml"]
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_the_super_user_channel_does_not_reach_a_resident(tmp_path: Path) -> None:
    """A super user's su line reaches the admin and never the resident's socket."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", "admin", hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Ada", RANK_SUPER, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def login(name: bytes) -> socket.socket:
        client = _connect(server._config.port)
        opened.append(client)
        _read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        _read_until(client, b"Password: ")
        client.sendall(b"pw\n")
        _read_until(client, b"What? > ")
        return client

    try:
        boss = login(b"admin")
        ada = login(b"Ada")
        bea = login(b"Bea")
        _read_until(boss, b"[Bea has connected from 127.0.0.1]\n")
        from lupaxa.chat_server.colour import cyan

        ada.sendall(b"su hi\n")
        staff = (cyan("< SU > Ada says 'hi'") + "\n").encode()
        assert staff in _read_until(boss, staff)
        you_say = (cyan("< SU > You say 'hi'") + "\n").encode()
        assert you_say in _read_until(ada, b"What? > ")
        assert _hears_nothing(bea)
        bea.sendall(b"who\n")
        heard = _read_until(bea, b"What? > ")
        assert b"< SU >" not in heard
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_staff_in_the_room_see_the_connect_address(tmp_path: Path) -> None:
    """A super user in the room sees where a player connected from. A resident does not."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Ada", RANK_SUPER, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", RANK_RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def login(name: bytes) -> socket.socket:
        client = _connect(server._config.port)
        opened.append(client)
        _read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        _read_until(client, b"Password: ")
        client.sendall(b"pw\n")
        _read_until(client, b"What? > ")
        return client

    def newbie(name: bytes) -> socket.socket:
        client = _connect(server._config.port)
        opened.append(client)
        _read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        _read_until(client, b"What? > ")
        return client

    try:
        ada = login(b"Ada")
        cy = login(b"Cyd")
        bea = newbie(b"Bea")
        heard = _read_until(cy, b"[Bea has connected]\n")
        assert b"from " not in heard
        assert b"[Bea has connected from 127.0.0.1]\n" in _read_until(
            ada, b"[Bea has connected from 127.0.0.1]\n"
        )

        ada.sendall(b"go west\n")
        _read_until(ada, b"What? > ")
        newbie(b"Dee")
        assert _hears_nothing(ada)

        ada.sendall(b"go east\n")
        _read_until(ada, b"What? > ")
        bea.close()
        opened.remove(bea)
        assert b"[Bea has disconnected]\n" in _read_until(cy, b"[Bea has disconnected]\n")
        assert b"[Bea has disconnected from 127.0.0.1]\n" in _read_until(
            ada, b"[Bea has disconnected from 127.0.0.1]\n"
        )
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_player_named_finds_bob_and_skips_incomplete_clients() -> None:
    """A lowercase bob finds Bob. Clients with no room or no player are skipped."""
    left, right = socket.socketpair()
    try:
        when = datetime.now(timezone.utc)
        no_player = Client(sock=left)
        waiting = Player("Bob", "hall", when)
        waiting.room = None  # type: ignore[assignment]
        roomless = Client(sock=left, player=waiting)
        bob = Client(sock=left, player=Player("Bob", "hall", when))
        found = player_named((no_player, roomless, bob), "bob")
        assert found is bob
        assert bob.player is not None
        assert bob.player.name == "Bob"
        assert player_named((no_player, roomless), "bob") is None
    finally:
        left.close()
        right.close()


def test_a_reported_width_is_stored_on_the_client(tmp_path: Path) -> None:
    """The selector thread copies the latest SSH width onto that socket's client."""
    server = ChatServer(
        _config(players=tmp_path / "players"),
        _layout(tmp_path),
        HelpBook(entries=()),
    )
    left, right = socket.socketpair()
    try:
        client = Client(sock=left, address="127.0.0.1", accepted_at=0.0, ssh=True, terminal=True)
        server._clients[left] = client
        server._note_width(left, 100, 24)
        server._apply_widths()
        assert client.discovered_columns == 100
        assert client.discovered_rows == 24
        server._note_width(left, 200, 40)
        server._apply_widths()
        assert client.discovered_columns == 200
        assert client.discovered_rows == 40
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_apply_widths_leaves_frame_columns_unchanged(tmp_path: Path) -> None:
    """A reported size updates discovery only; manual frame width stays put."""
    server = ChatServer(
        _config(players=tmp_path / "players"),
        _layout(tmp_path),
        HelpBook(entries=()),
    )
    left, right = socket.socketpair()
    try:
        client = Client(sock=left, address="127.0.0.1", accepted_at=0.0, ssh=True, terminal=True)
        client.frame_columns = 70
        server._clients[left] = client
        server._note_width(left, 100, 24)
        server._apply_widths()
        assert client.discovered_columns == 100
        assert client.frame_columns == 70
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_a_resolved_name_is_stored_on_the_client(tmp_path: Path) -> None:
    """A finished lookup replaces the IP stored in hostname."""
    server = ChatServer(
        _config(players=tmp_path / "players"),
        _layout(tmp_path),
        HelpBook(entries=()),
    )
    left, right = socket.socketpair()
    try:
        client = Client(sock=left, address="10.0.0.5", accepted_at=0.0)
        server._clients[left] = client
        server._resolve_hostname = lambda ip: "ada.example"
        server._note_hostname(left, server._resolve_hostname(client.address), time.monotonic())
        server._apply_hostnames()
        assert client.hostname == "ada.example"
        server._note_hostname(left, client.address, time.monotonic())
        server._apply_hostnames()
        assert client.hostname == "10.0.0.5"
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_a_stale_hostname_lookup_is_ignored(tmp_path: Path) -> None:
    """A lookup that took longer than two seconds does not change hostname."""
    server = ChatServer(
        _config(players=tmp_path / "players"),
        _layout(tmp_path),
        HelpBook(entries=()),
    )
    left, right = socket.socketpair()
    try:
        client = Client(sock=left, address="10.0.0.5", accepted_at=0.0)
        server._clients[left] = client
        server._note_hostname(left, "ada.example", time.monotonic() - 3)
        server._apply_hostnames()
        assert client.hostname == "10.0.0.5"
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_a_hostname_for_a_gone_client_is_dropped(tmp_path: Path) -> None:
    """A lookup for a socket no longer in _clients is discarded without error."""
    server = ChatServer(
        _config(players=tmp_path / "players"),
        _layout(tmp_path),
        HelpBook(entries=()),
    )
    left, right = socket.socketpair()
    try:
        server._note_hostname(left, "ada.example", time.monotonic())
        server._apply_hostnames()
        assert left not in server._clients
        assert left not in server._hostnames
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_telnet_only_applies_a_queued_hostname(tmp_path: Path) -> None:
    """Telnet-only servers apply hostname lookups on the select loop."""
    server = ChatServer(
        _config(players=tmp_path / "players"),
        _layout(tmp_path),
        HelpBook(entries=()),
    )
    assert server._wake_r is None
    server._resolve_hostname = lambda _ip: "ada.example"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    peer: socket.socket | None = None
    try:
        peer = _connect(server._config.port)
        deadline = time.monotonic() + 2
        client: Client | None = None
        while time.monotonic() < deadline:
            if len(server._clients) == 1:
                client = next(iter(server._clients.values()))
                if client.hostname == "ada.example":
                    break
            time.sleep(0.05)
        assert client is not None
        assert client.hostname == "ada.example"
    finally:
        if peer is not None:
            peer.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_a_width_queued_before_the_client_is_kept(tmp_path: Path) -> None:
    """A width reported before the socket is a client is applied once that client exists."""
    server = ChatServer(
        _config(players=tmp_path / "players"),
        _layout(tmp_path),
        HelpBook(entries=()),
    )
    left, right = socket.socketpair()
    try:
        server._note_width(left, 100, 24)
        server._apply_widths()
        client = Client(sock=left, address="127.0.0.1", accepted_at=0.0, ssh=True, terminal=True)
        server._clients[left] = client
        server._apply_widths()
        assert client.discovered_columns == 100
        assert client.discovered_rows == 24
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_lsn_lists_the_connected_newbies_with_their_address(tmp_path: Path) -> None:
    """A super user sees each connected newbie by name and address. A resident is not listed."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Ada", RANK_SUPER, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", RANK_RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def join(name: bytes, password: bytes | None) -> socket.socket:
        client = _connect(server._config.port)
        opened.append(client)
        _read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        if password is not None:
            _read_until(client, b"Password: ")
            client.sendall(password + b"\n")
        _read_until(client, b"What? > ")
        return client

    try:
        ada = join(b"Ada", b"pw")
        join(b"Cyd", b"pw")
        join(b"Dee", None)
        join(b"Bea", None)
        ada.sendall(b"lsn\n")
        heard = _read_until(ada, b"What? > ")
        frame = heard[heard.index(b"-" * 80) :]
        assert b"Newbies:" not in frame
        assert b"Bea" in frame
        assert b"Dee" in frame
        assert b"Reception" in frame
        assert frame.count(b"127.0.0.1") == 2
        assert b"Cyd" not in frame
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_a_reconnect_takeover_keeps_the_newcomers_columns(tmp_path: Path) -> None:
    """The surviving client takes the discovered size of the connection that replaced it."""
    server = ChatServer(
        _config(players=tmp_path / "players"),
        _layout(tmp_path),
        HelpBook(entries=()),
    )
    old_sock, old_peer = socket.socketpair()
    new_sock, new_peer = socket.socketpair()
    stale_sock, stale_peer = socket.socketpair()
    telnet_sock, telnet_peer = socket.socketpair()
    try:
        live = Client(sock=old_sock, address="192.168.0.9", accepted_at=0.0)
        live.frame_columns = 70
        newcomer = Client(
            sock=new_sock,
            address="10.0.0.5",
            accepted_at=0.0,
            ssh=True,
            terminal=True,
            discovered_columns=132,
            discovered_rows=48,
        )
        server._clients[old_sock] = live
        server._replace_socket(live, newcomer)
        assert live.frame_columns is None
        assert live.discovered_columns == newcomer.discovered_columns
        assert live.discovered_rows == newcomer.discovered_rows

        again = Client(
            sock=stale_sock,
            address="192.168.0.9",
            accepted_at=0.0,
            ssh=True,
            terminal=True,
            discovered_columns=100,
            discovered_rows=24,
        )
        again.frame_columns = 70
        telnet = Client(sock=telnet_sock, address="10.0.0.5", accepted_at=0.0)
        server._clients[stale_sock] = again
        server._replace_socket(again, telnet)
        assert again.frame_columns is None
        assert again.discovered_columns == telnet.discovered_columns
        assert again.discovered_rows == telnet.discovered_rows
    finally:
        for sock in (
            old_sock,
            old_peer,
            new_sock,
            new_peer,
            stale_sock,
            stale_peer,
            telnet_sock,
            telnet_peer,
        ):
            sock.close()
        server.shutdown()


def test_a_saved_player_reconnects_over_a_live_session(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second login takes over the live session without a logout."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    logged_out = datetime(2020, 1, 1, tzinfo=timezone.utc)
    save_player(
        players,
        SavedPlayer(
            "Bea",
            RANK_RESIDENT,
            hash_password("pw", rounds=4),
            logged_out_at=logged_out,
            ip_address="192.168.0.9",
        ),
    )
    save_player(players, SavedPlayer("Cyd", RANK_RESIDENT, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Ada", RANK_SUPER, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Dee", RANK_SUPER, hash_password("pw", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path, _TINY), HelpBook(entries=()))
    server._resolve_hostname = lambda ip: ip
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def login(name: bytes) -> socket.socket:
        client = _connect(server._config.port)
        opened.append(client)
        _read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        _read_until(client, b"Password: ")
        client.sendall(b"pw\n")
        _read_until(client, b"What? > ")
        return client

    try:
        bea = login(b"Bea")
        cy = login(b"Cyd")
        ada = login(b"Ada")
        dee = login(b"Dee")
        dee.sendall(b"go west\n")
        _read_until(cy, b"Dee goes west to Kitchen.\n")
        _read_until(dee, b"What? > ")
        bea.sendall(b"prompt Go:\n")
        bea.sendall(b"alarm 5\n")
        _read_until(bea, b"Go:")

        again = _connect(server._config.port)
        opened.append(again)
        _read_until(again, b"Please enter your name: ")
        again.sendall(b"Bea\npw\nsay hi\n")

        assert b"You have been reconnected from elsewhere.\n" in _read_to_close(bea)
        heard_cy = _read_until(cy, b"- Bea says 'hi'\n")
        assert b"[Bea has reconnected]\n" in heard_cy
        assert b"from " not in heard_cy
        assert b"[Bea has reconnected from 127.0.0.1]\n" in _read_until(
            ada, b"[Bea has reconnected from 127.0.0.1]\n"
        )
        assert _hears_nothing(dee)
        seen = _read_until(again, b"Go:")
        assert b"The front desk faces the street." in seen
        assert b"[Bea has reconnected]" not in seen

        ada.sendall(b"examine Bea\n")
        assert b"Logged in from : 127.0.0.1" in _read_until(ada, b"Logged in from : 127.0.0.1")

        # Bea is still connected, so her file shows the new address and no new logout.
        saved = load_saved(player_path(players, "Bea"))
        assert saved is not None
        assert saved.ip_address == "127.0.0.1"
        assert saved.hostname == "127.0.0.1"
        assert saved.logged_out_at == logged_out
        # Read the log now. Shutdown below drops the live Bea and logs that.
        before_shutdown = capsys.readouterr().out
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()

    assert "Bea reconnected." in before_shutdown
    assert "Accepting connection from" in before_shutdown
    assert "Bea disconnected." not in before_shutdown
    # Only the closing of the live session at shutdown logs Bea leaving.
    assert capsys.readouterr().out.count("Bea disconnected.") == 1


def test_a_saved_player_reconnect_restores_telnet_echo(tmp_path: Path) -> None:
    """After reconnect, the room brief turns local echo back on."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path, _TINY), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def login(name: bytes) -> socket.socket:
        client = _connect(server._config.port)
        opened.append(client)
        _read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        _read_until(client, b"Password: ")
        client.sendall(b"pw\n")
        _read_until(client, b"What? > ")
        return client

    try:
        bea = login(b"Bea")
        bea.sendall(b"prompt Go:\n")
        bea.sendall(b"alarm 5\n")
        _read_until(bea, b"Go:")

        again = _connect(server._config.port)
        opened.append(again)
        _read_until(again, b"Please enter your name: ")
        again.sendall(b"Bea\n")
        _read_until(again, b"Password: ")
        again.sendall(b"pw\n")
        after_password = b""
        while b"Go:" not in after_password:
            after_password += again.recv(4096)
            assert after_password, "reconnect closed before room brief"
        assert after_password.startswith(_WONT_ECHO)
        assert b"The front desk faces the street." in after_password
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_record_logout_saves_the_total_and_the_time_in_one_write(tmp_path: Path) -> None:
    """A resident leaving after 40 seconds has both in the file. A newbie gets none."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", RANK_RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path, _TINY), HelpBook(entries=()))
    left, right = socket.socketpair()
    other, extra = socket.socketpair()
    try:
        user = Player("Bea", "reception", datetime.now(timezone.utc), rank=RANK_RESIDENT)
        user.connected_seconds = 40
        resident = Client(sock=left, player=user)
        server._clients[left] = resident
        server.drop(resident, announce=False)
        saved = load_saved(player_path(players, "Bea"))
        assert saved is not None
        assert saved.connected_seconds == 40
        assert saved.logged_out_at is not None

        newbie = Player("Cyd", "reception", datetime.now(timezone.utc), connected_seconds=40)
        fresh = Client(sock=other, player=newbie)
        server._clients[other] = fresh
        server.drop(fresh, announce=False)
        assert load_saved(player_path(players, "Cyd")) is None
    finally:
        left.close()
        right.close()
        other.close()
        extra.close()
        server.close()


def test_shutdown_now_stops_the_server_and_closes_every_connection(tmp_path: Path) -> None:
    """Now reaches the rooms only, then both sockets close and the server thread ends."""
    players = _players(tmp_path)
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []
    try:
        waiting = _connect(server._config.port)
        opened.append(waiting)
        _read_until(waiting, b"Please enter your name: ")
        boss = _connect(server._config.port)
        opened.append(boss)
        _read_until(boss, b"Please enter your name: ")
        boss.sendall(b"admin\n")
        _read_until(boss, b"Password: ")
        boss.sendall(b"pw\n")
        _read_until(boss, b"What? > ")

        boss.sendall(b"shutdown now maintenance\n")
        deadline = time.monotonic() + 5
        heard_boss = _read_to_close(boss)
        heard_waiting = _read_to_close(waiting)
        assert b"Shutting down now for 'maintenance'.\n" in heard_boss
        assert b"Shutting down" not in heard_waiting
        while thread.is_alive() and time.monotonic() < deadline:
            thread.join(timeout=0.05)
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


_WILL_ECHO = b"\xff\xfb\x01"
_WONT_ECHO = b"\xff\xfc\x01"
_NAWS_DO = b"\xff\xfd\x1f"


def test_telnet_naws_sets_the_discovered_size_and_not_the_manual_width(
    tmp_path: Path,
) -> None:
    """DO NAWS is sent on accept, and a later resize replaces only the discovered size."""
    client = Client(sock=socket.socket(), address="127.0.0.1", accepted_at=0.0)
    client.frame_columns = 70
    client.iac.extend(bytes((255, 250, 31, 0, 80, 0, 24, 255, 240)))
    _strip_telnet(client, b"")
    assert client.discovered_columns == 80
    assert client.discovered_rows == 24
    assert client.frame_columns == 70
    client.iac.extend(bytes((255, 250, 31, 0, 100, 0, 40, 255, 240)))
    _strip_telnet(client, b"")
    assert client.discovered_columns == 100
    assert client.discovered_rows == 40
    assert client.frame_columns == 70
    client.iac.clear()
    client.iac.extend(bytes((255, 250, 31, 0, 255, 255, 0, 24, 255, 240)))
    _strip_telnet(client, b"")
    assert client.discovered_columns == 255
    assert client.discovered_rows == 24

    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    peer: socket.socket | None = None
    ssh_peer: socket.socket | None = None
    try:
        peer = _connect(server._config.port)
        first = peer.recv(64)
        assert first.startswith(_NAWS_DO)
        left, right = socket.socketpair()
        server._hand_session(left, "127.0.0.1", terminal=True)
        server._take_ssh()
        ssh_peer = right
        ssh_first = ssh_peer.recv(64)
        assert not ssh_first.startswith(_NAWS_DO)
    finally:
        if peer is not None:
            peer.close()
        if ssh_peer is not None:
            ssh_peer.close()
        server.shutdown()
        thread.join(timeout=2)


def test_telnet_hides_the_login_password(tmp_path: Path) -> None:
    """The login password is not echoed, and echo turns back on afterwards."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", RANK_RESIDENT, hash_password("swordfish", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = _connect(server._config.port)
        assert isinstance(server._config.port, int)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"Bea\n")
        asked = _read_until(client, b"Password: ")
        assert asked.endswith(_WILL_ECHO + b"Password: ")
        client.sendall(b"nope\n")
        retry = _read_until(client, b"Password: ")
        assert _WILL_ECHO not in retry
        assert b"Wrong password." in retry
        assert b"nope" not in retry
        client.sendall(b"swordfish\n")
        entered = _read_until(client, b"The front desk")
        assert entered.startswith(_WONT_ECHO)
        assert b"swordfish" not in asked
        assert b"swordfish" not in entered
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_telnet_strips_negotiation_before_the_name(tmp_path: Path) -> None:
    """A telnet DO sequence is not part of the name."""
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = _connect(server._config.port)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"\xff\xfd\x01Nia\n")
        heard = _read_until(client, b"The front desk")
        assert b"\xff" not in heard
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_telnet_backspace_edits_a_password(tmp_path: Path) -> None:
    """Delete removes the previous password character and is not echoed."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", RANK_RESIDENT, hash_password("swordfish", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = _connect(server._config.port)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"Bea\n")
        _read_until(client, b"Password: ")
        client.sendall(b"swordX\x7ffish\n")
        entered = _read_until(client, b"The front desk")
        assert b"swordX" not in entered
        assert entered.startswith(_WONT_ECHO)
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_telnet_hides_each_password_change_prompt(tmp_path: Path) -> None:
    """Current, new, and confirm prompts stay hidden, and echo returns after."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", RANK_RESIDENT, hash_password("swordfish", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = _connect(server._config.port)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"Bea\n")
        _read_until(client, b"Password: ")
        client.sendall(b"swordfish\n")
        _read_until(client, b"What? > ")
        client.sendall(b"password\n")
        current = _read_until(client, b"Current password: ")
        assert current.endswith(_WILL_ECHO + b"Current password: ")
        client.sendall(b"swordfish\n")
        renew = _read_until(client, b"Set a password: ")
        assert _WILL_ECHO not in renew
        assert b"swordfish" not in renew
        client.sendall(b"newpw\n")
        confirm = _read_until(client, b"Confirm password: ")
        assert _WILL_ECHO not in confirm
        assert b"newpw" not in confirm
        client.sendall(b"newpw\n")
        done = _read_until(client, b"Password changed.")
        assert done.startswith(_WONT_ECHO)
        assert b"newpw" not in done
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_telnet_password_change_cr_nul_then_login(tmp_path: Path) -> None:
    """CR NUL line endings at a hidden prompt do not corrupt the next line."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", RANK_RESIDENT, hash_password("swordfish", rounds=4)))
    server = ChatServer(_config(players=players), _layout(tmp_path), HelpBook(entries=()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    cr_nul = b"\r\x00"
    try:
        client = _connect(server._config.port)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"Bea\n")
        _read_until(client, b"Password: ")
        client.sendall(b"swordfish\n")
        _read_until(client, b"What? > ")
        client.sendall(b"password\n")
        _read_until(client, b"Current password: ")
        client.sendall(b"swordfish" + cr_nul)
        _read_until(client, b"Set a password: ")
        client.sendall(b"newpw" + cr_nul)
        _read_until(client, b"Confirm password: ")
        client.sendall(b"newpw" + cr_nul)
        _read_until(client, b"Password changed.")
        client.close()
        client = None
        client = _connect(server._config.port)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"Bea\n")
        _read_until(client, b"Password: ")
        client.sendall(b"newpw\n")
        entered = _read_until(client, b"The front desk")
        assert entered.startswith(_WONT_ECHO)
        assert b"newpw" not in entered
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def _closed_server(
    tmp_path: Path,
    *,
    allow_newbies: bool = True,
    allow_residents: bool = True,
    players: Path | None = None,
) -> tuple[ChatServer, threading.Thread, list[socket.socket]]:
    """Start a server whose allow flags match the closed-rank tests."""
    roster = players if players is not None else _players(tmp_path)
    config = Config(
        port=_free_port(),
        rooms=Path("rooms.yml"),
        start="reception",
        help=Path("help.yml"),
        players=roster,
        admin="admin",
        allow_newbies=allow_newbies,
        allow_residents=allow_residents,
        mfa_enabled=False,
    )
    server = ChatServer(config, _layout(tmp_path), HelpBook(entries=()))
    opened: list[socket.socket] = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, opened


def _connect_login(server: ChatServer, opened: list[socket.socket]) -> socket.socket:
    client = _connect(server._config.port)
    opened.append(client)
    _read_until(client, b"Please enter your name: ")
    return client


def test_closed_new_name_refused_when_residents_closed(tmp_path: Path) -> None:
    """A new name is turned away when residents are closed but newbies are open."""
    server, thread, opened = _closed_server(tmp_path, allow_newbies=True, allow_residents=False)
    try:
        client = _connect_login(server, opened)
        client.sendall(b"Ada\n")
        heard = _read_until(client, b"Please enter your name: ")
        assert b"The talker is closed to new people.\n" in heard
        assert b"The front desk" not in heard
        client.sendall(b"\n")
        assert b"Please enter your name: " in _read_until(client, b"Please enter your name: ")
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_new_name_refused_when_newbies_closed(tmp_path: Path) -> None:
    """A new name is turned away when newbies are closed but residents are open."""
    server, thread, opened = _closed_server(tmp_path, allow_newbies=False, allow_residents=True)
    try:
        client = _connect_login(server, opened)
        client.sendall(b"Ada\n")
        heard = _read_until(client, b"Please enter your name: ")
        assert b"The talker is closed to new people.\n" in heard
        assert b"The front desk" not in heard
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_resident_wrong_password_unchanged(tmp_path: Path) -> None:
    """Residents closed does not change the wrong-password path."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(
        players,
        SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)),
    )
    save_player(
        players,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("secret", rounds=4)),
    )
    server, thread, opened = _closed_server(
        tmp_path, allow_newbies=True, allow_residents=False, players=players
    )
    try:
        client = _connect_login(server, opened)
        client.sendall(b"Bea\n")
        _read_until(client, b"Password: ")
        client.sendall(b"wrong\n")
        assert b"Wrong password.\n" in _read_until(client, b"Password: ")
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_resident_good_password_is_dropped(tmp_path: Path) -> None:
    """A resident with the right password is dropped when residents are closed."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(
        players,
        SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)),
    )
    save_player(
        players,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("secret", rounds=4)),
    )
    server, thread, opened = _closed_server(
        tmp_path, allow_newbies=True, allow_residents=False, players=players
    )
    try:
        client = _connect_login(server, opened)
        client.sendall(b"Bea\n")
        _read_until(client, b"Password: ")
        client.sendall(b"secret\n")
        heard = _read_to_close(client)
        assert b"The talker is closed.\n" in heard
        assert b"The front desk" not in heard
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_super_reconnect_sees_residents_closed_status(tmp_path: Path) -> None:
    """A super user reconnecting while residents are closed sees the staff status."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(
        players,
        SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)),
    )
    save_player(
        players,
        SavedPlayer("Cyd", RANK_SUPER, hash_password("secret", rounds=4)),
    )
    server, thread, opened = _closed_server(
        tmp_path, allow_newbies=True, allow_residents=False, players=players
    )
    try:
        first = _connect_login(server, opened)
        first.sendall(b"Cyd\n")
        _read_until(first, b"Password: ")
        first.sendall(b"secret\n")
        _read_until(first, b"What? > ")

        again = _connect_login(server, opened)
        again.sendall(b"Cyd\nsecret\n")
        heard = _read_until(again, b"What? > ")
        assert b"The front desk faces the street." in heard
        assert b"[ Status ] The talker is closed to newbies and residents.\n" in heard
        assert heard.index(b"The front desk") < heard.index(b"[ Status ]")
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_super_enters_and_sees_both_closed_status(tmp_path: Path) -> None:
    """A super user enters while both ranks are closed and sees the staff status."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(
        players,
        SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)),
    )
    save_player(
        players,
        SavedPlayer("Cyd", RANK_SUPER, hash_password("secret", rounds=4)),
    )
    server, thread, opened = _closed_server(
        tmp_path, allow_newbies=False, allow_residents=False, players=players
    )
    try:
        client = _connect_login(server, opened)
        client.sendall(b"Cyd\n")
        _read_until(client, b"Password: ")
        client.sendall(b"secret\n")
        heard = _read_until(client, b"What? > ")
        assert b"The front desk faces the street." in heard
        assert b"[ Status ] The talker is closed to newbies and residents.\n" in heard
        assert heard.index(b"The front desk") < heard.index(b"[ Status ]")
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_status_newbies_only_for_super(tmp_path: Path) -> None:
    """When only newbies are closed, staff see the shorter status line."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(
        players,
        SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)),
    )
    save_player(
        players,
        SavedPlayer("Cyd", RANK_SUPER, hash_password("secret", rounds=4)),
    )
    server, thread, opened = _closed_server(
        tmp_path, allow_newbies=False, allow_residents=True, players=players
    )
    try:
        client = _connect_login(server, opened)
        client.sendall(b"Cyd\n")
        _read_until(client, b"Password: ")
        client.sendall(b"secret\n")
        heard = _read_until(client, b"What? > ")
        assert b"[ Status ] The talker is closed to newbies.\n" in heard
        assert b"newbies and residents" not in heard
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_open_talker_has_no_status_line(tmp_path: Path) -> None:
    """An open talker sends room text and the prompt with no status line."""
    server, thread, opened = _closed_server(tmp_path)
    try:
        client = _connect_login(server, opened)
        client.sendall(b"Ada\n")
        heard = _read_until(client, b"What? > ")
        assert b"The front desk faces the street." in heard
        assert b"[ Status ]" not in heard
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_resident_no_status_when_only_newbies_closed(tmp_path: Path) -> None:
    """A resident who enters while only newbies are closed sees no status line."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(
        players,
        SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)),
    )
    save_player(
        players,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("secret", rounds=4)),
    )
    server, thread, opened = _closed_server(
        tmp_path, allow_newbies=False, allow_residents=True, players=players
    )
    try:
        client = _connect_login(server, opened)
        client.sendall(b"Bea\n")
        _read_until(client, b"Password: ")
        client.sendall(b"secret\n")
        heard = _read_until(client, b"What? > ")
        assert b"The front desk faces the street." in heard
        assert b"[ Status ]" not in heard
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_telnet_drops_a_client_with_an_endless_negotiation(tmp_path: Path) -> None:
    """An unfinished subnegotiation cannot grow without bound."""
    server = ChatServer(
        _config(players=_players(tmp_path)), _layout(tmp_path), HelpBook(entries=())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        assert isinstance(server._config.port, int)
        client = _connect(server._config.port)
        _read_until(client, b"Please enter your name: ")
        client.sendall(b"\xff\xfa" + b"x" * 5000)
        _read_to_close(client)
        deadline = time.monotonic() + 2
        while server._clients and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server._clients == {}
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
