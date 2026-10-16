"""Two clients on a real listening socket."""

from __future__ import annotations  # noqa: I001

import selectors
import socket
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lupaxa.chat_server import chatserver as chatserver_module
from lupaxa.chat_server.client import Client
from lupaxa.chat_server.config import Config
from lupaxa.chat_server.defaults import DEFAULT_HOST
from lupaxa.chat_server.entry import name_taken_reply, remember_address
from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.help import HelpBook, HelpEntry
from lupaxa.chat_server.login import welcome_text
from lupaxa.chat_server.player import Player
from lupaxa.chat_server.rooms import Layout, load_layout
from lupaxa.chat_server.saved_player import SavedPlayer, hash_password, load_saved, player_path, save_player  # fmt: skip  # noqa: E501
from lupaxa.chat_server.chatserver import ChatServer, bind_listener, strip_telnet
from lupaxa.chat_server.utilities import find_player, get_clients, lookup_hostname
from lupaxa.chat_server.ranks import Rank


@pytest.fixture(autouse=True)
def stub_reverse_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub reverse DNS so live server tests do not hit the network."""
    monkeypatch.setattr(chatserver_module, "lookup_hostname", lambda ip: ip)


def free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind((DEFAULT_HOST, 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def connect(port: int | None, *, recv_buffer: int | None = None) -> socket.socket:
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


def read_until(client: socket.socket, needle: bytes) -> bytes:
    data = b""
    while needle not in data:
        chunk = client.recv(4096)
        assert chunk, data
        data += chunk
    return data


TINY = """\
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


def layout(tmp_path: Path, text: str = TINY) -> Layout:
    path = tmp_path / "rooms.yml"
    path.write_text(text, encoding="utf-8")
    return load_layout(path)


def make_config(start: str = "reception", *, players: Path | None = None) -> Config:
    return Config(port=free_port(), rooms=Path("rooms.yml"), start=start, help=Path("help.yml"), players=players if players is not None else Path("players"), mfa_enabled=False)  # fmt: skip  # noqa: E501


def player_dir(tmp_path: Path, admin: str = "admin", rank: Rank = Rank.ADMIN) -> Path:
    directory = tmp_path / "players"
    directory.mkdir()
    save_player(directory, SavedPlayer(admin, rank, hash_password("pw", rounds=4)))
    return directory


def read_to_close(client: socket.socket) -> bytes:
    data = b""
    while True:
        chunk = client.recv(4096)
        if chunk == b"":
            return data
        data += chunk


def hears_nothing(client: socket.socket) -> bool:
    client.settimeout(0.3)
    try:
        return client.recv(4096) == b""
    except TimeoutError:
        return True
    finally:
        client.settimeout(2)


TEST_HELP = HelpBook(entries=(HelpEntry("disclaimer", (), "Be kind."),))


def accept_disclaimer(client: socket.socket) -> None:
    read_until(client, b"Do you agree to these rules: ")
    client.sendall(b"yes\n")


def test_a_blank_line_during_the_disclaimer_asks_again(tmp_path: Path) -> None:
    """A blank line during agree must not re-prompt for a name."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = connect(server.config.port)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"Ada\n")
        read_until(client, b"Do you agree to these rules: ")
        client.sendall(b"\n")
        reply = read_until(client, b"Do you agree to these rules: ")
        assert b"Do you agree to these rules: " in reply
        assert b"Please enter your name:" not in reply
        client.sendall(b"yes\n")
        assert b"What? > " in read_until(client, b"What? > ")
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_a_name_at_the_disclaimer_holds_the_name(tmp_path: Path) -> None:
    """A second connection with the same name is sent back to the name prompt."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first: socket.socket | None = None
    second: socket.socket | None = None
    try:
        first = connect(server.config.port)
        read_until(first, b"Please enter your name: ")
        first.sendall(b"Ada\n")
        read_until(first, b"Do you agree to these rules: ")
        second = connect(server.config.port)
        read_until(second, b"Please enter your name: ")
        second.sendall(b"Ada\n")
        reply = read_until(second, b"Please enter your name: ")
        assert name_taken_reply("Ada").encode() in reply
        assert b"Be kind." not in reply
        first.sendall(b"yes\n")
        assert b"What? > " in read_until(first, b"What? > ")
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
        server.shutdown()
        thread.join(timeout=2)


def test_server_copies_allow_flags(tmp_path: Path) -> None:
    """Startup copies the file. It does not keep a second default."""
    config = Config(port=free_port(), rooms=Path("rooms.yml"), start="reception", help=Path("help.yml"), players=tmp_path, allow_newbies=False, allow_residents=True, mfa_enabled=False)  # fmt: skip  # noqa: E501
    server = ChatServer(config, layout(tmp_path), TEST_HELP)
    assert server.allow_newbies is False
    assert server.allow_residents is True


def test_the_server_records_when_it_was_created(tmp_path: Path) -> None:
    """started_at is UTC and is the moment the object is created."""
    before = datetime.now(timezone.utc)
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    after = datetime.now(timezone.utc)
    assert server.started_at.tzinfo is timezone.utc
    assert before <= server.started_at <= after


def test_say_reaches_the_other_client(tmp_path: Path) -> None:
    """A named player's say line is delivered to the other player."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    left: socket.socket | None = None
    right: socket.socket | None = None
    try:
        left = connect(server.config.port)
        right = connect(server.config.port)
        assert b"Please enter your name: " in read_until(left, b"Please enter your name: ")
        assert b"Please enter your name: " in read_until(right, b"Please enter your name: ")
        left.sendall(b"Ada\n")
        right.sendall(b"Bea\n")
        accept_disclaimer(left)
        accept_disclaimer(right)
        assert b"Bea has connected.\n" in read_until(left, b"Bea has connected.\n")
        left.sendall(b"say hello\n")
        heard = read_until(right, b"- Ada says 'hello'\n")
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Ada", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ada: socket.socket | None = None
    bea: socket.socket | None = None
    waiting: socket.socket | None = None
    try:
        ada = connect(server.config.port)
        bea = connect(server.config.port)
        waiting = connect(server.config.port)
        for client in (ada, bea, waiting):
            assert b"Please enter your name: " in read_until(client, b"Please enter your name: ")
        ada.sendall(b"Ada\n")
        assert b"Password: " in read_until(ada, b"Password: ")
        ada.sendall(b"pw\n")
        assert b"What? > " in read_until(ada, b"What? > ")
        bea.sendall(b"Bea\n")
        accept_disclaimer(bea)
        assert b"Bea has connected.\n" in read_until(ada, b"Bea has connected.\n")
        bea.sendall(b"go west\n")
        assert b"Bea goes west to Kitchen.\n" in read_until(ada, b"Bea goes west to Kitchen.\n")
        from lupaxa.chat_server.colour import green, yellow

        ada.sendall(b"tell Bea hello\n")
        tell = (green("> Ada tells you 'hello'") + "\n").encode()
        heard = read_until(bea, tell)
        assert tell in heard
        ada.sendall(b"!hey\n")
        shout = (yellow("! Ada shouts 'hey'") + "\n").encode()
        heard = read_until(bea, shout)
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


def test_overlong_line_is_discarded(tmp_path: Path) -> None:
    """More than 1024 characters is discarded and the connection stays."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = connect(server.config.port)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"a" * 1025 + b"\n")
        heard = read_until(client, b"Please enter your name: ")
        assert b"Input is too long.\n" in heard
        client.sendall(b"Ada\n")
        assert b"Do you agree to these rules: " in read_until(client, b"Do you agree to these rules: ")
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_idle_reader_is_dropped_when_outgoing_exceeds_cap(tmp_path: Path) -> None:
    """A client that stops reading is dropped once queued output exceeds 64 KiB."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sender: socket.socket | None = None
    idle: socket.socket | None = None
    try:
        sender = connect(server.config.port)
        idle = connect(server.config.port, recv_buffer=4096)
        assert b"Please enter your name: " in read_until(sender, b"Please enter your name: ")
        assert b"Please enter your name: " in read_until(idle, b"Please enter your name: ")
        sender.sendall(b"Ada\n")
        idle.sendall(b"Bea\n")
        accept_disclaimer(sender)
        accept_disclaimer(idle)
        assert b"Bea has connected.\n" in read_until(sender, b"Bea has connected.\n")
        sender.setblocking(False)
        needle = b"Bea has disconnected.\n"
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
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    left: socket.socket | None = None
    right: socket.socket | None = None
    try:
        left = connect(server.config.port)
        right = connect(server.config.port)
        read_until(left, b"Please enter your name: ")
        read_until(right, b"Please enter your name: ")
        left.sendall(b"Ada\n")
        right.sendall(b"Bea\n")
        accept_disclaimer(left)
        accept_disclaimer(right)
        read_until(left, b"Bea has connected.\n")
        left.sendall(b"go west\n")
        heard = read_until(left, b"  east -> Reception\n")
        assert b"The kitchen smells of coffee." in heard
        assert b"Ada goes west to Kitchen.\n" in read_until(right, b"Ada goes west to Kitchen.\n")
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
    text = TINY.replace("max_occupancy: 4", "max_occupancy: 1", 1)
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path, text), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first: socket.socket | None = None
    second: socket.socket | None = None
    try:
        first = connect(server.config.port)
        read_until(first, b"Please enter your name: ")
        first.sendall(b"Ada\n")
        accept_disclaimer(first)
        read_until(first, b"  west  -> Kitchen\n")
        second = connect(server.config.port)
        read_until(second, b"Please enter your name: ")
        second.sendall(b"Bea\n")
        accept_disclaimer(second)
        assert b"Reception is full.\n" in read_until(second, b"Reception is full.\n")
        assert second.recv(64) == b""
        second = connect(server.config.port)
        read_until(second, b"Please enter your name: ")
        second.sendall(b"Bea\n")
        accept_disclaimer(second)
        assert b"Reception is full.\n" in read_until(second, b"Reception is full.\n")
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
        server.shutdown()
        thread.join(timeout=2)


def test_full_room_refuses_the_move(tmp_path: Path) -> None:
    """Toilet holds one person. The second player stays in Reception."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    left: socket.socket | None = None
    right: socket.socket | None = None
    try:
        left = connect(server.config.port)
        right = connect(server.config.port)
        read_until(left, b"Please enter your name: ")
        read_until(right, b"Please enter your name: ")
        left.sendall(b"Ada\n")
        right.sendall(b"Bea\n")
        accept_disclaimer(left)
        accept_disclaimer(right)
        read_until(left, b"Bea has connected.\n")
        left.sendall(b"go south\n")
        read_until(left, b"What? > ")
        right.sendall(b"go south\n")
        assert b"Toilet is full.\n" in read_until(right, b"Toilet is full.\n")
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
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = connect(server.config.port)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"Ada\n")
        accept_disclaimer(client)
        read_until(client, b"  west  -> Kitchen\n")
        client.sendall(b"go north\n")
        assert b"You cannot go north.\n" in read_until(client, b"You cannot go north.\n")
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_arrival_is_told_to_the_new_room(tmp_path: Path) -> None:
    """Someone already in Kitchen hears Ada arrive from the east."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first: socket.socket | None = None
    second: socket.socket | None = None
    try:
        first = connect(server.config.port)
        second = connect(server.config.port)
        read_until(first, b"Please enter your name: ")
        read_until(second, b"Please enter your name: ")
        first.sendall(b"Bea\n")
        second.sendall(b"Ada\n")
        accept_disclaimer(first)
        accept_disclaimer(second)
        read_until(first, b"Ada has connected.\n")
        first.sendall(b"go west\n")
        read_until(first, b"  east -> Reception\n")
        second.sendall(b"go west\n")
        assert b"Ada arrives.\n" in read_until(first, b"Ada arrives.\n")
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
        server.shutdown()
        thread.join(timeout=2)


def test_start_room_comes_from_make_config(tmp_path: Path) -> None:
    """A layout with no reception still starts in the configured room."""
    text = """\
arena:
  description: A court.
  max_occupancy: 1
  exits: {}
"""
    server = ChatServer(make_config("arena", players=player_dir(tmp_path)), layout(tmp_path, text), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first: socket.socket | None = None
    second: socket.socket | None = None
    try:
        first = connect(server.config.port)
        read_until(first, b"Please enter your name: ")
        first.sendall(b"Ada\n")
        accept_disclaimer(first)
        assert b"A court.\n" in read_until(first, b"Exits: none.\n")
        second = connect(server.config.port)
        read_until(second, b"Please enter your name: ")
        second.sendall(b"Bea\n")
        accept_disclaimer(second)
        assert b"Arena is full.\n" in read_until(second, b"Arena is full.\n")
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
        ChatServer(make_config("arena"), layout(tmp_path), TEST_HELP)


def test_unknown_newbie_start_is_rejected(tmp_path: Path) -> None:
    """newbie_start must be a room in the office file."""
    config = replace(make_config(players=player_dir(tmp_path)), newbie_start="waiting")
    with pytest.raises(ConfigError, match=f"Newbie start room waiting is not in {config.rooms}."):
        ChatServer(config, layout(tmp_path), TEST_HELP)


def test_void_newbie_start_is_rejected(tmp_path: Path) -> None:
    """The Void is not a newbie landing room."""
    config = replace(make_config(players=player_dir(tmp_path)), newbie_start="void")
    office = layout(tmp_path).with_void()
    with pytest.raises(ConfigError, match=f"Newbie start room void is not in {config.rooms}."):
        ChatServer(config, office, TEST_HELP)


def test_empty_return_shows_the_prompt_again(tmp_path: Path) -> None:
    """Enter at the name prompt and at What? > prints that prompt again."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = connect(server.config.port)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"\n")
        assert b"Please enter your name: " in read_until(client, b"Please enter your name: ")
        client.sendall(b"Ada\n")
        accept_disclaimer(client)
        read_until(client, b"What? > ")
        client.sendall(b"\n")
        heard = read_until(client, b"What? > ")
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
    save_player(players, SavedPlayer("Ada", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []
    try:
        bea = connect(server.config.port)
        opened.append(bea)
        read_until(bea, b"Please enter your name: ")
        bea.sendall(b"Bea\n")
        accept_disclaimer(bea)
        read_until(bea, b"What? > ")

        ada = connect(server.config.port)
        opened.append(ada)
        read_until(ada, b"Please enter your name: ")
        ada.sendall(b"Ada\n")
        read_until(ada, b"Password: ")
        ada.sendall(b"pw\n")
        read_until(ada, b"What? > ")
        ada.sendall(b"resident Bea\n")

        read_until(bea, b"Set a password: ")
        bea.sendall(b"\n")
        assert b"Password cannot be empty.\nSet a password: " in read_until(bea, b"Set a password: ")
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_admin_sets_a_password_then_logs_in(tmp_path: Path) -> None:
    """A saved super admin needs the password to come back."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", Rank.SUPER_ADMIN, hash_password("secret", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def open_client() -> socket.socket:
        client = connect(server.config.port)
        opened.append(client)
        read_until(client, b"Please enter your name: ")
        return client

    try:
        first = open_client()
        first.sendall(b"admin\n")
        read_until(first, b"Password: ")
        first.sendall(b"secret\n")
        assert b"The front desk faces the street." in read_until(first, b"What? > ")
        first.sendall(b"quit\n")
        assert b"Goodbye!\n" in read_to_close(first)

        watcher = open_client()
        watcher.sendall(b"Ada\n")
        accept_disclaimer(watcher)
        read_until(watcher, b"What? > ")

        failing = open_client()
        failing.sendall(b"admin\n")
        read_until(failing, b"Password: ")
        failing.sendall(b"nope\n")
        assert b"Wrong password.\n" in read_until(failing, b"Password: ")
        failing.sendall(b"nope\n")
        assert b"Wrong password.\n" in read_until(failing, b"Password: ")
        failing.sendall(b"nope\n")
        assert b"Wrong password.\n" in read_to_close(failing)
        assert hears_nothing(watcher)

        returning = open_client()
        returning.sendall(b"admin\n")
        read_until(returning, b"Password: ")
        returning.sendall(b"secret\n")
        assert b"The front desk faces the street." in read_until(returning, b"What? > ")
        assert b"admin has connected.\n" in read_until(watcher, b"admin has connected.\n")
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()
    saved = load_saved(player_path(players, "admin"))
    assert saved is not None
    assert saved.rank is Rank.SUPER_ADMIN
    assert saved.logged_out_at is not None


def test_a_saved_name_answers_for_its_own_spelling(tmp_path: Path) -> None:
    """Ask for the password when a saved name is typed in another case."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("Boss", Rank.RESIDENT, hash_password("secret", rounds=4)))
    config = make_config(players=players)
    server = ChatServer(config, layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []
    try:
        boss = connect(server.config.port)
        opened.append(boss)
        read_until(boss, b"Please enter your name: ")
        boss.sendall(b"Boss\n")
        read_until(boss, b"Password: ")
        boss.sendall(b"secret\n")
        read_until(boss, b"What? > ")
        boss.sendall(b"quit\n")
        read_to_close(boss)

        other = connect(server.config.port)
        opened.append(other)
        read_until(other, b"Please enter your name: ")
        other.sendall(b"boss\n")
        reply = read_until(other, b"Password: ")
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def open_client() -> socket.socket:
        client = connect(server.config.port)
        opened.append(client)
        read_until(client, b"Please enter your name: ")
        return client

    try:
        boss = open_client()
        boss.sendall(b"admin\n")
        read_until(boss, b"Password: ")
        boss.sendall(b"pw\n")
        read_until(boss, b"What? > ")

        bea = open_client()
        bea.sendall(b"Bea\n")
        accept_disclaimer(bea)
        read_until(bea, b"What? > ")
        bea.sendall(b"shout hi\n")
        assert b"No such command 'shout'\n" in read_until(bea, b"What? > ")

        from lupaxa.chat_server.colour import cyan

        boss.sendall(b"resident Bea\n")
        staff = (cyan("< SU > admin makes Bea a resident.") + "\n").encode()
        assert staff in read_until(boss, b"What? > ")
        heard = read_until(bea, b"Set a password: ")
        assert b"< SU >" not in heard
        bea.sendall(b"hunter2\n")
        read_until(bea, b"Confirm password: ")
        assert load_saved(player_path(players, "Bea")) is None
        bea.sendall(b"hunter2\n")
        assert b"You are now a resident.\n" in read_until(bea, b"What? > ")
        assert b"Bea is now a resident.\n" in read_until(boss, b"Bea is now a resident.\n")
        bea.sendall(b"shout hi\n")
        assert b"You shout 'hi'\n" in read_until(bea, b"What? > ")
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    saved = load_saved(player_path(players, "Bea"))
    assert saved is not None
    assert saved.rank is Rank.RESIDENT


def test_resident_disconnect_before_confirm_writes_nothing(tmp_path: Path) -> None:
    """Closing the socket during the password pair leaves no player file."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def open_client() -> socket.socket:
        client = connect(server.config.port)
        opened.append(client)
        read_until(client, b"Please enter your name: ")
        return client

    try:
        boss = open_client()
        boss.sendall(b"admin\n")
        read_until(boss, b"Password: ")
        boss.sendall(b"pw\n")
        read_until(boss, b"What? > ")

        bea = open_client()
        bea.sendall(b"Bea\n")
        accept_disclaimer(bea)
        read_until(bea, b"What? > ")

        boss.sendall(b"resident Bea\n")
        read_until(bea, b"Set a password: ")
        bea.sendall(b"hunter2\n")
        read_until(bea, b"Confirm password: ")
        bea.close()
        opened.remove(bea)
        heard = read_until(boss, b"Bea has disconnected.\n")
        assert b"Bea has disconnected.\n" in heard
        assert b"has disconnected from" not in heard
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Ada", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def login(name: bytes) -> socket.socket:
        client = connect(server.config.port)
        opened.append(client)
        read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        read_until(client, b"Password: ")
        client.sendall(b"pw\n")
        read_until(client, b"What? > ")
        return client

    try:
        boss = login(b"admin")
        ada = login(b"Ada")
        bea = login(b"Bea")
        read_until(boss, b"Bea has connected.\n")
        from lupaxa.chat_server.colour import cyan

        ada.sendall(b"su hi\n")
        staff = (cyan("< SU > Ada says 'hi'") + "\n").encode()
        assert staff in read_until(boss, staff)
        you_say = (cyan("< SU > You say 'hi'") + "\n").encode()
        assert you_say in read_until(ada, b"What? > ")
        assert hears_nothing(bea)
        bea.sendall(b"who\n")
        heard = read_until(bea, b"What? > ")
        assert b"< SU >" not in heard
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_staff_in_the_room_see_the_connect_address(tmp_path: Path) -> None:
    """A newbie alerts staff. The room hears the login sentence."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Ada", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def login(name: bytes) -> socket.socket:
        client = connect(server.config.port)
        opened.append(client)
        read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        read_until(client, b"Password: ")
        client.sendall(b"pw\n")
        read_until(client, b"What? > ")
        return client

    def newbie(name: bytes) -> socket.socket:
        client = connect(server.config.port)
        opened.append(client)
        read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        accept_disclaimer(client)
        read_until(client, b"What? > ")
        return client

    try:
        ada = login(b"Ada")
        cy = login(b"Cyd")
        bea = newbie(b"Bea")
        heard = read_until(cy, b"Bea has connected.\n")
        assert b"from " not in heard
        assert b"Newbie Alert" not in heard
        assert b"< SU >" not in heard
        staff = read_until(ada, b"[ Newbie Alert ] Bea is in Reception.")
        assert b"AWOOGA AWOOGA!!" in staff
        assert b"Bea is connecting from 127.0.0.1" in staff
        assert b"has connected from" not in staff
        assert b"< SU >" not in staff

        ada.sendall(b"go west\n")
        read_until(ada, b"What? > ")
        newbie(b"Dee")
        away = read_until(ada, b"[ Newbie Alert ] Dee is in Reception.")
        assert b"Dee is connecting from 127.0.0.1" in away
        assert b"has connected from" not in away
        assert b"< SU >" not in away

        ada.sendall(b"go east\n")
        read_until(ada, b"What? > ")
        bea.close()
        opened.remove(bea)
        left = read_until(cy, b"Bea has disconnected.\n")
        assert b"Bea has disconnected.\n" in left
        assert b"from " not in left
        gone = read_until(ada, b"Bea has disconnected.\n")
        assert b"has disconnected from" not in gone
        assert b"< SU >" not in gone
    finally:
        for client in opened:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_get_clients_defaults_to_login_order_and_can_sort_by_name() -> None:
    """Login order stays the default. Alphabetical order is by name, with no name last."""
    left, right = socket.socketpair()
    try:
        when = datetime.now(timezone.utc)
        cara = Client(sock=left, player=Player("Cara", "hall", when))
        ada = Client(sock=left, player=Player("Ada", "hall", when))
        pending = Client(sock=left)
        server = type("Roster", (), {"clients": {1: cara, 2: ada, 3: pending}})()
        assert list(get_clients(server)) == [cara, ada, pending]  # type: ignore[arg-type]
        named = list(get_clients(server, alphabetical=True))  # type: ignore[arg-type]
        assert named == [ada, cara, pending]
    finally:
        left.close()
        right.close()


def test_find_player_finds_bob_and_skips_incomplete_clients() -> None:
    """A lowercase bob finds Bob. Clients with no room or no player are skipped."""
    left, right = socket.socketpair()
    try:
        when = datetime.now(timezone.utc)
        no_player = Client(sock=left)
        waiting = Player("Bob", "hall", when)
        waiting.room = None  # type: ignore[assignment]
        roomless = Client(sock=left, player=waiting)
        bob = Client(sock=left, player=Player("Bob", "hall", when))
        sent: list[str] = []

        def send(asker: Client, text: str) -> bool:
            del asker
            sent.append(text)
            return True

        present = type("Roster", (), {"clients": {1: no_player, 2: roomless, 3: bob}, "send": staticmethod(send)})()
        found = find_player(present, bob, "bob")  # type: ignore[arg-type]
        assert found is bob
        assert bob.player is not None
        assert bob.player.name == "Bob"
        assert sent == []
        missing = type("Roster", (), {"clients": {1: no_player, 2: roomless}, "send": staticmethod(send)})()
        assert find_player(missing, bob, "bob", quiet=True) is None  # type: ignore[arg-type]
        assert sent == []
        assert find_player(missing, bob, "bob") is None  # type: ignore[arg-type]
        assert sent == ["No such player 'bob'.\n"]
    finally:
        left.close()
        right.close()


def test_a_reported_width_is_stored_on_the_client(tmp_path: Path) -> None:
    """The selector thread copies the latest SSH width onto that socket's client."""
    server = ChatServer(make_config(players=tmp_path / "players"), layout(tmp_path), TEST_HELP)
    left, right = socket.socketpair()
    try:
        client = Client(sock=left, address="127.0.0.1", accepted_at=0.0, ssh=True, terminal=True)
        server.clients[left] = client
        server.note_width(left, 100, 24)
        server.apply_widths()
        assert client.discovered_columns == 100
        assert client.discovered_rows == 24
        server.note_width(left, 200, 40)
        server.apply_widths()
        assert client.discovered_columns == 200
        assert client.discovered_rows == 40
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_queued_ssh_width_centers_the_welcome_banner(tmp_path: Path) -> None:
    """A terminal size queued before handoff shapes the welcome banner."""
    banner = "X\n"
    server = ChatServer(make_config(players=tmp_path / "players"), layout(tmp_path), TEST_HELP, banner=banner)
    left, right = socket.socketpair()
    try:
        right.settimeout(1)
        server.note_width(left, 100, 24)
        server.hand_session(left, "127.0.0.1", terminal=True)
        server.take_ssh()
        data = right.recv(4096).decode("utf-8").replace("\r\n", "\n")
        assert data == welcome_text(banner, 100)
        assert data != welcome_text(banner, 80)
        assert left not in server.widths
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_apply_widths_leaves_frame_columns_unchanged(tmp_path: Path) -> None:
    """A reported size updates discovery only; manual frame width stays put."""
    server = ChatServer(make_config(players=tmp_path / "players"), layout(tmp_path), TEST_HELP)
    left, right = socket.socketpair()
    try:
        client = Client(sock=left, address="127.0.0.1", accepted_at=0.0, ssh=True, terminal=True)
        client.frame_columns = 70
        server.clients[left] = client
        server.note_width(left, 100, 24)
        server.apply_widths()
        assert client.discovered_columns == 100
        assert client.frame_columns == 70
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_a_resolved_name_is_stored_on_the_client(tmp_path: Path) -> None:
    """A finished lookup replaces the IP stored in hostname."""
    server = ChatServer(make_config(players=tmp_path / "players"), layout(tmp_path), TEST_HELP)
    left, right = socket.socketpair()
    try:
        client = Client(sock=left, address="10.0.0.5", accepted_at=0.0)
        server.clients[left] = client
        server.resolve_hostname = lambda ip: "ada.example"
        server.note_hostname(left, server.resolve_hostname(client.address), time.monotonic())
        server.apply_hostnames()
        assert client.hostname == "ada.example"
        server.note_hostname(left, client.address, time.monotonic())
        server.apply_hostnames()
        assert client.hostname == "10.0.0.5"
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_a_country_is_stored_with_the_hostname(tmp_path: Path) -> None:
    """A finished lookup stores the country beside the hostname."""
    database = tmp_path / "geo.mmdb"
    database.write_bytes(b"x")
    config = replace(make_config(players=tmp_path / "players"), country_lookup=True, country_database=database)
    server = ChatServer(config, layout(tmp_path), TEST_HELP)
    left, right = socket.socketpair()
    try:
        assert server.country_lookup is True
        client = Client(sock=left, address="8.8.8.8", accepted_at=0.0)
        server.clients[left] = client
        server.note_hostname(left, "dns.google", time.monotonic(), "United States", "US")
        server.apply_hostnames()
        assert client.hostname == "dns.google"
        assert client.country_name == "United States"
        assert client.country_code == "US"
    finally:
        left.close()
        right.close()
        server.close()


def test_a_missing_country_database_turns_lookup_off(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Startup reads the database file. A missing file leaves lookup off."""
    config = replace(make_config(players=tmp_path / "players"), country_lookup=True, country_database=tmp_path / "missing.mmdb")
    server = ChatServer(config, layout(tmp_path), TEST_HELP)
    try:
        assert server.country_lookup is False
        assert "Country lookup is off" in capsys.readouterr().out
    finally:
        server.close()


def test_a_private_address_is_labelled_without_the_database(tmp_path: Path) -> None:
    """RFC 1918 and IPv6 unique-local addresses do not need a database record."""
    server = ChatServer(make_config(players=tmp_path / "players"), layout(tmp_path), TEST_HELP)
    try:
        assert server.country_of("127.0.0.1") == ("Localhost", "XL")
        assert server.country_of("::1") == ("Localhost", "XL")
        assert server.country_of("::ffff:127.0.0.1") == ("Localhost", "XL")
        assert server.country_of("192.168.1.9") == ("Private Network", "XP")
        assert server.country_of("fd00::1") == ("Private Network", "XP")
        assert server.country_of("::ffff:10.1.2.3") == ("Private Network", "XP")
        assert server.country_of("8.8.8.8") == ("Unknown", "XU")
        assert server.country_of("2001:4860:4860::8888") == ("Unknown", "XU")
    finally:
        server.close()


def test_remember_address_writes_the_country(tmp_path: Path) -> None:
    """A saved player file keeps the country from this connection."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), ip_address="10.0.0.5", hostname="bea.example"))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    left, right = socket.socketpair()
    try:
        user = Player("Bea", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT)
        client = Client(sock=left, player=user, address="8.8.8.8", hostname="dns.google", country_name="United States", country_code="US")
        remember_address(server, client)  # type: ignore[arg-type]
        saved = load_saved(player_path(players, "Bea"))
        assert saved is not None
        assert saved.country_name == "United States"
        assert saved.country_code == "US"
        assert saved.ip_address == "8.8.8.8"
    finally:
        left.close()
        right.close()
        server.close()


def test_a_stale_hostname_lookup_is_ignored(tmp_path: Path) -> None:
    """A lookup that took longer than two seconds does not change hostname."""
    server = ChatServer(make_config(players=tmp_path / "players"), layout(tmp_path), TEST_HELP)
    left, right = socket.socketpair()
    try:
        client = Client(sock=left, address="10.0.0.5", accepted_at=0.0)
        server.clients[left] = client
        server.note_hostname(left, "ada.example", time.monotonic() - 3)
        server.apply_hostnames()
        assert client.hostname == "10.0.0.5"
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_a_hostname_for_a_gone_client_is_dropped(tmp_path: Path) -> None:
    """A lookup for a socket no longer in clients is discarded without error."""
    server = ChatServer(make_config(players=tmp_path / "players"), layout(tmp_path), TEST_HELP)
    left, right = socket.socketpair()
    try:
        server.note_hostname(left, "ada.example", time.monotonic())
        server.apply_hostnames()
        assert left not in server.clients
        assert left not in server.hostnames
    finally:
        left.close()
        right.close()
        server.shutdown()


def test_telnet_only_applies_a_queued_hostname(tmp_path: Path) -> None:
    """Telnet-only servers apply hostname lookups on the select loop."""
    server = ChatServer(make_config(players=tmp_path / "players"), layout(tmp_path), TEST_HELP)
    assert server.wake_r is None
    server.resolve_hostname = lambda ip: "ada.example"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    peer: socket.socket | None = None
    try:
        peer = connect(server.config.port)
        deadline = time.monotonic() + 2
        client: Client | None = None
        while time.monotonic() < deadline:
            if len(server.clients) == 1:
                client = next(iter(server.clients.values()))
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
    server = ChatServer(make_config(players=tmp_path / "players"), layout(tmp_path), TEST_HELP)
    left, right = socket.socketpair()
    try:
        server.note_width(left, 100, 24)
        server.apply_widths()
        client = Client(sock=left, address="127.0.0.1", accepted_at=0.0, ssh=True, terminal=True)
        server.clients[left] = client
        server.apply_widths()
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Ada", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def join(name: bytes, password: bytes | None) -> socket.socket:
        client = connect(server.config.port)
        opened.append(client)
        read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        if password is not None:
            read_until(client, b"Password: ")
            client.sendall(password + b"\n")
        else:
            accept_disclaimer(client)
        read_until(client, b"What? > ")
        return client

    try:
        ada = join(b"Ada", b"pw")
        join(b"Cyd", b"pw")
        join(b"Dee", None)
        join(b"Bea", None)
        ada.sendall(b"lsn\n")
        heard = read_until(ada, b"What? > ")
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
    server = ChatServer(make_config(players=tmp_path / "players"), layout(tmp_path), TEST_HELP)
    old_sock, old_peer = socket.socketpair()
    new_sock, new_peer = socket.socketpair()
    stale_sock, stale_peer = socket.socketpair()
    telnet_sock, telnet_peer = socket.socketpair()
    try:
        live = Client(sock=old_sock, address="192.168.0.9", accepted_at=0.0)
        live.frame_columns = 70
        newcomer = Client(sock=new_sock, address="10.0.0.5", accepted_at=0.0, ssh=True, terminal=True, discovered_columns=132, discovered_rows=48)  # fmt: skip  # noqa: E501
        server.clients[old_sock] = live
        server.replace_socket(live, newcomer)
        assert live.frame_columns is None
        assert live.discovered_columns == newcomer.discovered_columns
        assert live.discovered_rows == newcomer.discovered_rows

        again = Client(sock=stale_sock, address="192.168.0.9", accepted_at=0.0, ssh=True, terminal=True, discovered_columns=100, discovered_rows=24)  # fmt: skip  # noqa: E501
        again.frame_columns = 70
        telnet = Client(sock=telnet_sock, address="10.0.0.5", accepted_at=0.0)
        server.clients[stale_sock] = again
        server.replace_socket(again, telnet)
        assert again.frame_columns is None
        assert again.discovered_columns == telnet.discovered_columns
        assert again.discovered_rows == telnet.discovered_rows
    finally:
        for sock in (old_sock, old_peer, new_sock, new_peer, stale_sock, stale_peer, telnet_sock, telnet_peer):
            sock.close()
        server.shutdown()


def test_a_saved_player_reconnects_over_a_live_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A second login takes over the live session without a logout."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    logged_out = datetime(2020, 1, 1, tzinfo=timezone.utc)
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4), logged_out_at=logged_out, ip_address="192.168.0.9"))  # fmt: skip  # noqa: E501
    save_player(players, SavedPlayer("Cyd", Rank.RESIDENT, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Ada", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Dee", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path, TINY), TEST_HELP)
    server.resolve_hostname = lambda ip: ip
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def login(name: bytes) -> socket.socket:
        client = connect(server.config.port)
        opened.append(client)
        read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        read_until(client, b"Password: ")
        client.sendall(b"pw\n")
        read_until(client, b"What? > ")
        return client

    try:
        bea = login(b"Bea")
        cy = login(b"Cyd")
        ada = login(b"Ada")
        dee = login(b"Dee")
        dee.sendall(b"go west\n")
        read_until(cy, b"Dee goes west to Kitchen.\n")
        read_until(dee, b"What? > ")
        bea.sendall(b"set prompt Go:\n")
        bea.sendall(b"alarm 5\n")
        read_until(bea, b"Go:")

        again = connect(server.config.port)
        opened.append(again)
        read_until(again, b"Please enter your name: ")
        again.sendall(b"Bea\npw\nsay hi\n")

        assert b"You have been reconnected from elsewhere.\n" in read_to_close(bea)
        heard_cy = read_until(cy, b"- Bea says 'hi'\n")
        assert b"[Bea has reconnected]\n" in heard_cy
        assert b"from " not in heard_cy
        ada_heard = read_until(ada, b"[Bea has reconnected]\n")
        assert b"has reconnected from" not in ada_heard
        assert hears_nothing(dee)
        seen = read_until(again, b"Go:")
        assert b"The front desk faces the street." in seen
        assert b"[Bea has reconnected]" not in seen

        ada.sendall(b"examine Bea\n")
        assert b"Logged in from : 127.0.0.1 (127.0.0.1)" in read_until(ada, b"Logged in from : 127.0.0.1 (127.0.0.1)")

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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path, TINY), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []

    def login(name: bytes) -> socket.socket:
        client = connect(server.config.port)
        opened.append(client)
        read_until(client, b"Please enter your name: ")
        client.sendall(name + b"\n")
        read_until(client, b"Password: ")
        client.sendall(b"pw\n")
        read_until(client, b"What? > ")
        return client

    try:
        bea = login(b"Bea")
        bea.sendall(b"set prompt Go:\n")
        bea.sendall(b"alarm 5\n")
        read_until(bea, b"Go:")

        again = connect(server.config.port)
        opened.append(again)
        read_until(again, b"Please enter your name: ")
        again.sendall(b"Bea\n")
        read_until(again, b"Password: ")
        again.sendall(b"pw\n")
        after_password = b""
        while b"Go:" not in after_password:
            after_password += again.recv(4096)
            assert after_password, "reconnect closed before room brief"
        assert after_password.startswith(WONT_ECHO)
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path, TINY), TEST_HELP)
    left, right = socket.socketpair()
    other, extra = socket.socketpair()
    try:
        user = Player("Bea", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT)
        user.connected_seconds = 40
        resident = Client(sock=left, player=user)
        server.clients[left] = resident
        server.drop(resident, announce=False)
        saved = load_saved(player_path(players, "Bea"))
        assert saved is not None
        assert saved.connected_seconds == 40
        assert saved.logged_out_at is not None

        newbie = Player("Cyd", "reception", datetime.now(timezone.utc), connected_seconds=40)
        fresh = Client(sock=other, player=newbie)
        server.clients[other] = fresh
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
    players = player_dir(tmp_path, rank=Rank.CODER)
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened: list[socket.socket] = []
    try:
        waiting = connect(server.config.port)
        opened.append(waiting)
        read_until(waiting, b"Please enter your name: ")
        boss = connect(server.config.port)
        opened.append(boss)
        read_until(boss, b"Please enter your name: ")
        boss.sendall(b"admin\n")
        read_until(boss, b"Password: ")
        boss.sendall(b"pw\n")
        read_until(boss, b"What? > ")

        boss.sendall(b"shutdown now maintenance\n")
        deadline = time.monotonic() + 5
        heard_boss = read_to_close(boss)
        heard_waiting = read_to_close(waiting)
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


WILL_ECHO = b"\xff\xfb\x01"
WONT_ECHO = b"\xff\xfc\x01"
NAWS_DO = b"\xff\xfd\x1f"


def test_telnet_naws_centers_the_welcome_before_the_banner(tmp_path: Path) -> None:
    """The banner waits for NAWS so a wide terminal is not centered at 80."""
    banner = "X\n"
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP, banner=banner)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    peer: socket.socket | None = None
    try:
        peer = connect(server.config.port)
        offered = peer.recv(3)
        assert offered == NAWS_DO
        peer.sendall(bytes((255, 251, 31, 255, 250, 31, 0, 100, 0, 40, 255, 240)))
        heard = read_until(peer, b"Please enter your name: ")
        assert welcome_text(banner, 100).encode() in heard
        assert welcome_text(banner, 80).encode() not in heard
    finally:
        if peer is not None:
            peer.close()
        server.shutdown()
        thread.join(timeout=2)


def test_telnet_naws_sets_the_discovered_size_and_not_the_manual_width(tmp_path: Path) -> None:
    """DO NAWS is sent on accept, and a later resize replaces only the discovered size."""
    client = Client(sock=socket.socket(), address="127.0.0.1", accepted_at=0.0)
    client.frame_columns = 70
    client.iac.extend(bytes((255, 250, 31, 0, 80, 0, 24, 255, 240)))
    strip_telnet(client, b"")
    assert client.discovered_columns == 80
    assert client.discovered_rows == 24
    assert client.frame_columns == 70
    client.iac.extend(bytes((255, 250, 31, 0, 100, 0, 40, 255, 240)))
    strip_telnet(client, b"")
    assert client.discovered_columns == 100
    assert client.discovered_rows == 40
    assert client.frame_columns == 70
    client.iac.clear()
    client.iac.extend(bytes((255, 250, 31, 0, 255, 255, 0, 24, 255, 240)))
    strip_telnet(client, b"")
    assert client.discovered_columns == 255
    assert client.discovered_rows == 24

    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    peer: socket.socket | None = None
    ssh_peer: socket.socket | None = None
    try:
        peer = connect(server.config.port)
        first = peer.recv(64)
        assert first.startswith(NAWS_DO)
        left, right = socket.socketpair()
        server.hand_session(left, "127.0.0.1", terminal=True)
        server.take_ssh()
        ssh_peer = right
        ssh_first = ssh_peer.recv(64)
        assert not ssh_first.startswith(NAWS_DO)
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("swordfish", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = connect(server.config.port)
        assert isinstance(server.config.port, int)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"Bea\n")
        asked = read_until(client, b"Password: ")
        assert asked.endswith(WILL_ECHO + b"Password: ")
        client.sendall(b"nope\n")
        retry = read_until(client, b"Password: ")
        assert WILL_ECHO not in retry
        assert b"Wrong password." in retry
        assert b"nope" not in retry
        client.sendall(b"swordfish\n")
        entered = read_until(client, b"The front desk")
        assert entered.startswith(WONT_ECHO)
        assert b"swordfish" not in asked
        assert b"swordfish" not in entered
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_telnet_strips_negotiation_before_the_name(tmp_path: Path) -> None:
    """A telnet DO sequence is not part of the name."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = connect(server.config.port)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"\xff\xfd\x01Nia\n")
        accept_disclaimer(client)
        heard = read_until(client, b"The front desk")
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("swordfish", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = connect(server.config.port)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"Bea\n")
        read_until(client, b"Password: ")
        client.sendall(b"swordX\x7ffish\n")
        entered = read_until(client, b"The front desk")
        assert b"swordX" not in entered
        assert entered.startswith(WONT_ECHO)
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def test_telnet_hides_each_password_change_prompt(tmp_path: Path) -> None:
    """Current, new, and confirm prompts stay hidden, and echo returns after."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("swordfish", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        client = connect(server.config.port)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"Bea\n")
        read_until(client, b"Password: ")
        client.sendall(b"swordfish\n")
        read_until(client, b"What? > ")
        client.sendall(b"password\n")
        current = read_until(client, b"Current password: ")
        assert current.endswith(WILL_ECHO + b"Current password: ")
        client.sendall(b"swordfish\n")
        renew = read_until(client, b"Set a password: ")
        assert WILL_ECHO not in renew
        assert b"swordfish" not in renew
        client.sendall(b"newpw\n")
        confirm = read_until(client, b"Confirm password: ")
        assert WILL_ECHO not in confirm
        assert b"newpw" not in confirm
        client.sendall(b"newpw\n")
        done = read_until(client, b"Password changed.")
        assert done.startswith(WONT_ECHO)
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("swordfish", rounds=4)))
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    cr_nul = b"\r\x00"
    try:
        client = connect(server.config.port)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"Bea\n")
        read_until(client, b"Password: ")
        client.sendall(b"swordfish\n")
        read_until(client, b"What? > ")
        client.sendall(b"password\n")
        read_until(client, b"Current password: ")
        client.sendall(b"swordfish" + cr_nul)
        read_until(client, b"Set a password: ")
        client.sendall(b"newpw" + cr_nul)
        read_until(client, b"Confirm password: ")
        client.sendall(b"newpw" + cr_nul)
        read_until(client, b"Password changed.")
        client.close()
        client = None
        client = connect(server.config.port)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"Bea\n")
        read_until(client, b"Password: ")
        client.sendall(b"newpw\n")
        entered = read_until(client, b"The front desk")
        assert entered.startswith(WONT_ECHO)
        assert b"newpw" not in entered
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


def closed_server(tmp_path: Path, *, allow_newbies: bool = True, allow_residents: bool = True, players: Path | None = None) -> tuple[ChatServer, threading.Thread, list[socket.socket]]:  # fmt: skip  # noqa: E501
    """Start a server whose allow flags match the closed-rank tests."""
    roster = players if players is not None else player_dir(tmp_path)
    config = Config(port=free_port(), rooms=Path("rooms.yml"), start="reception", help=Path("help.yml"), players=roster, allow_newbies=allow_newbies, allow_residents=allow_residents, mfa_enabled=False)  # fmt: skip  # noqa: E501
    server = ChatServer(config, layout(tmp_path), TEST_HELP)
    opened: list[socket.socket] = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, opened


def connect_login(server: ChatServer, opened: list[socket.socket]) -> socket.socket:
    client = connect(server.config.port)
    opened.append(client)
    read_until(client, b"Please enter your name: ")
    return client


def test_closed_new_name_refused_when_residents_closed(tmp_path: Path) -> None:
    """A new name is turned away when residents are closed but newbies are open."""
    server, thread, opened = closed_server(tmp_path, allow_newbies=True, allow_residents=False)
    try:
        client = connect_login(server, opened)
        client.sendall(b"Ada\n")
        heard = read_until(client, b"Please enter your name: ")
        assert b"The talker is closed to new people.\n" in heard
        assert b"The front desk" not in heard
        client.sendall(b"\n")
        assert b"Please enter your name: " in read_until(client, b"Please enter your name: ")
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_new_name_refused_when_newbies_closed(tmp_path: Path) -> None:
    """A new name is turned away when newbies are closed but residents are open."""
    server, thread, opened = closed_server(tmp_path, allow_newbies=False, allow_residents=True)
    try:
        client = connect_login(server, opened)
        client.sendall(b"Ada\n")
        heard = read_until(client, b"Please enter your name: ")
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("secret", rounds=4)))
    server, thread, opened = closed_server(tmp_path, allow_newbies=True, allow_residents=False, players=players)
    try:
        client = connect_login(server, opened)
        client.sendall(b"Bea\n")
        read_until(client, b"Password: ")
        client.sendall(b"wrong\n")
        assert b"Wrong password.\n" in read_until(client, b"Password: ")
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_resident_good_password_is_dropped(tmp_path: Path) -> None:
    """A resident with the right password is dropped when residents are closed."""
    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("secret", rounds=4)))
    server, thread, opened = closed_server(tmp_path, allow_newbies=True, allow_residents=False, players=players)
    try:
        client = connect_login(server, opened)
        client.sendall(b"Bea\n")
        read_until(client, b"Password: ")
        client.sendall(b"secret\n")
        heard = read_to_close(client)
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", Rank.SUPER_USER, hash_password("secret", rounds=4)))
    server, thread, opened = closed_server(tmp_path, allow_newbies=True, allow_residents=False, players=players)
    try:
        first = connect_login(server, opened)
        first.sendall(b"Cyd\n")
        read_until(first, b"Password: ")
        first.sendall(b"secret\n")
        read_until(first, b"What? > ")

        again = connect_login(server, opened)
        again.sendall(b"Cyd\nsecret\n")
        heard = read_until(again, b"What? > ")
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", Rank.SUPER_USER, hash_password("secret", rounds=4)))
    server, thread, opened = closed_server(tmp_path, allow_newbies=False, allow_residents=False, players=players)
    try:
        client = connect_login(server, opened)
        client.sendall(b"Cyd\n")
        read_until(client, b"Password: ")
        client.sendall(b"secret\n")
        heard = read_until(client, b"What? > ")
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Cyd", Rank.SUPER_USER, hash_password("secret", rounds=4)))
    server, thread, opened = closed_server(tmp_path, allow_newbies=False, allow_residents=True, players=players)
    try:
        client = connect_login(server, opened)
        client.sendall(b"Cyd\n")
        read_until(client, b"Password: ")
        client.sendall(b"secret\n")
        heard = read_until(client, b"What? > ")
        assert b"[ Status ] The talker is closed to newbies.\n" in heard
        assert b"newbies and residents" not in heard
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_closed_open_talker_has_no_status_line(tmp_path: Path) -> None:
    """An open talker sends room text and the prompt with no status line."""
    server, thread, opened = closed_server(tmp_path)
    try:
        client = connect_login(server, opened)
        client.sendall(b"Ada\n")
        accept_disclaimer(client)
        heard = read_until(client, b"What? > ")
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
    save_player(players, SavedPlayer("admin", Rank.ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", Rank.RESIDENT, hash_password("secret", rounds=4)))
    server, thread, opened = closed_server(tmp_path, allow_newbies=False, allow_residents=True, players=players)
    try:
        client = connect_login(server, opened)
        client.sendall(b"Bea\n")
        read_until(client, b"Password: ")
        client.sendall(b"secret\n")
        heard = read_until(client, b"What? > ")
        assert b"The front desk faces the street." in heard
        assert b"[ Status ]" not in heard
    finally:
        for sock in opened:
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


def test_telnet_drops_a_client_with_an_endless_negotiation(tmp_path: Path) -> None:
    """An unfinished subnegotiation cannot grow without bound."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client: socket.socket | None = None
    try:
        assert isinstance(server.config.port, int)
        client = connect(server.config.port)
        read_until(client, b"Please enter your name: ")
        client.sendall(b"\xff\xfa" + b"x" * 5000)
        read_to_close(client)
        deadline = time.monotonic() + 2
        while server.clients and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.clients == {}
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        thread.join(timeout=2)


class ScriptedSocket:
    """A socket whose send results are queued. A result may be an exception."""

    def __init__(self, results: list[int | BaseException]) -> None:
        self.results = results

    def send(self, data: bytes) -> int:
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self) -> None:
        return None


def test_a_telnet_read_counts_the_port_and_the_connection(tmp_path: Path) -> None:
    """Bytes read on telnet update telnet, combined, and that client."""
    left, right = socket.socketpair()
    left.setblocking(False)
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    client = Client(sock=left, address="127.0.0.1", accepted_at=0.0)
    server.clients[left] = client
    server.selector.register(left, selectors.EVENT_READ)
    try:
        right.sendall(b"hello")
        server.read(left)
        assert client.net.in_current == 5
        assert client.net.in_pack_current == 1
        assert server.telnet_net.in_current == 5
        assert server.ssh_net.in_current == 0
        assert server.combined_net.in_current == 5
    finally:
        right.close()
        server.close()


def test_an_ssh_read_does_not_touch_telnet(tmp_path: Path) -> None:
    """An SSH client updates the SSH counter."""
    left, right = socket.socketpair()
    left.setblocking(False)
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    client = Client(sock=left, address="127.0.0.1", accepted_at=0.0, ssh=True)
    server.clients[left] = client
    server.selector.register(left, selectors.EVENT_READ)
    try:
        right.sendall(b"ab")
        server.read(left)
        assert server.ssh_net.in_current == 2
        assert server.telnet_net.in_current == 0
        assert server.combined_net.in_current == 2
    finally:
        right.close()
        server.close()


def test_a_partial_write_counts_only_the_bytes_sent(tmp_path: Path) -> None:
    """Send returning 4 of 7 bytes is one 4-byte packet. The rest blocks."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    sock = ScriptedSocket([4, BlockingIOError()])
    client = Client(sock=sock, address="127.0.0.1", accepted_at=0.0)  # type: ignore[arg-type]
    client.outgoing = bytearray(b"hello!!")
    server.clients[sock] = client  # type: ignore[index]
    server.selector.modify = lambda *args: None  # type: ignore[method-assign]
    assert server.flush(client) is True
    assert client.net.out_current == 4
    assert client.net.out_pack_current == 1
    assert server.telnet_net.out_current == 4
    assert bytes(client.outgoing) == b"o!!"


def test_an_empty_read_and_a_zero_write_are_not_packets(tmp_path: Path) -> None:
    """A closed read and a zero send do not increment the counters."""
    left, right = socket.socketpair()
    left.setblocking(False)
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    client = Client(sock=left, address="127.0.0.1", accepted_at=0.0)
    server.clients[left] = client
    server.selector.register(left, selectors.EVENT_READ)
    right.close()
    server.read(left)
    assert client.net.in_pack_current == 0
    sock = ScriptedSocket([0])
    other = Client(sock=sock, address="127.0.0.1", accepted_at=0.0)  # type: ignore[arg-type]
    other.outgoing = bytearray(b"hi")
    server.clients[sock] = other  # type: ignore[index]
    server.flush(other)
    assert other.net.out_pack_current == 0


def test_a_blocked_write_is_not_a_packet(tmp_path: Path) -> None:
    """BlockingIOError leaves the counters alone."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    sock = ScriptedSocket([BlockingIOError()])
    client = Client(sock=sock, address="127.0.0.1", accepted_at=0.0)  # type: ignore[arg-type]
    client.outgoing = bytearray(b"hi")
    server.clients[sock] = client  # type: ignore[index]
    server.selector.modify = lambda *args: None  # type: ignore[method-assign]
    server.flush(client)
    assert client.net.out_pack_current == 0
    assert bytes(client.outgoing) == b"hi"


def test_reconnect_adds_the_new_socket_without_resetting(tmp_path: Path) -> None:
    """The surviving connection keeps its rates and gains the new totals."""
    server = ChatServer(make_config(players=player_dir(tmp_path)), layout(tmp_path), TEST_HELP)
    live_sock, other = socket.socketpair()
    new_sock, new_other = socket.socketpair()
    live = Client(sock=live_sock, address="127.0.0.1", accepted_at=0.0)
    newcomer = Client(sock=new_sock, address="127.0.0.1", accepted_at=1.0)
    live.net.in_total = 10
    live.net.in_bps = 2
    newcomer.net.in_total = 7
    newcomer.net.in_current = 3
    server.clients[live_sock] = live
    server.clients[new_sock] = newcomer
    server.replace_socket(live, newcomer)
    assert live.net.in_total == 17
    assert live.net.in_current == 3
    assert live.net.in_bps == 2


def test_an_ipv6_listener_is_ipv6_only() -> None:
    """An IPv6 socket does not also accept IPv4."""
    try:
        sock = bind_listener("::1", 0)
    except OSError:
        pytest.skip("IPv6 is not available")
    else:
        try:
            assert sock.family == socket.AF_INET6
            assert sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 1
        finally:
            sock.close()


def test_lookup_hostname_accepts_an_ipv6_address() -> None:
    """Reverse lookup of a loopback IPv6 address returns a name or the address."""
    assert lookup_hostname("::1")


def test_dual_stack_accepts_ipv4_and_ipv6(tmp_path: Path) -> None:
    """A dual-stack server stores the family that actually connected."""
    try:
        probe = bind_listener("::1", 0)
    except OSError:
        pytest.skip("IPv6 is not available")
    else:
        probe.close()
    config = replace(make_config(players=player_dir(tmp_path)), host=DEFAULT_HOST, stack="dual")
    server = ChatServer(config, layout(tmp_path), TEST_HELP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    v4: socket.socket | None = None
    v6: socket.socket | None = None
    try:
        assert server.config.port is not None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not server.listeners_ready:
            time.sleep(0.05)
        v4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        v4.settimeout(2)
        v4.connect(("127.0.0.1", server.config.port))
        v6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        v6.settimeout(2)
        v6.connect(("::1", server.config.port))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and len(server.clients) < 2:
            time.sleep(0.05)
        addresses = {client.address for client in server.clients.values()}
        assert addresses == {"127.0.0.1", "::1"}
    finally:
        if v4 is not None:
            v4.close()
        if v6 is not None:
            v6.close()
        server.shutdown()
        thread.join(timeout=2)
