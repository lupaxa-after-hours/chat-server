"""SSH sessions on a real listener."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path

import asyncssh
import pytest

from lupaxa.chat_server.config import Config
from lupaxa.chat_server.exceptions import ServerError
from lupaxa.chat_server.help import HelpBook
from lupaxa.chat_server.rooms import Layout, load_layout
from lupaxa.chat_server.server import ChatServer
from lupaxa.chat_server.ssh import SSHListener, load_or_create_host_key
from lupaxa.chat_server.version import get_version

_TINY = """\
reception:
  description: The front desk faces the street. A bell sits on the counter.
  max_occupancy: 4
  exits: {}
"""


def _layout(tmp_path: Path) -> Layout:
    path = tmp_path / "rooms.yml"
    path.write_text(_TINY, encoding="utf-8")
    return load_layout(path)


def _server(
    tmp_path: Path, *, port: int | None, ssh_port: int | None, players: Path | None = None
) -> ChatServer:
    config_path = tmp_path / "config.yml"
    config_path.write_text("placeholder\n", encoding="utf-8")
    config = Config(
        port=port,
        rooms=Path("rooms.yml"),
        start="reception",
        help=Path("help.yml"),
        players=players if players is not None else tmp_path / "players",
        host="127.0.0.1",
        ssh_port=ssh_port,
        ssh_host_key=tmp_path / "ssh_host_key",
    )
    return ChatServer(config, _layout(tmp_path), HelpBook(entries=()))


def _free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    number = int(probe.getsockname()[1])
    probe.close()
    return number


def _wait_until_listening(port: int) -> None:
    """Poll until ``port`` accepts a TCP connection."""
    deadline = time.monotonic() + 2
    while True:
        try:
            probe = socket.create_connection(("127.0.0.1", port), timeout=0.2)
        except OSError:
            assert time.monotonic() < deadline
            time.sleep(0.05)
            continue
        probe.close()
        return


def test_ssh_shows_the_name_prompt_without_an_ssh_password(tmp_path: Path) -> None:
    """Stock SSH auth is enough. The first game prompt is the chat name."""
    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_listening(ssh_port)

    async def session() -> str:
        async with asyncssh.connect(
            "127.0.0.1",
            port=ssh_port,
            known_hosts=None,
            username="someone",
        ) as conn:
            process = await conn.create_process(term_type="xterm")
            text = await process.stdout.read(256)
            assert text is not None
            assert "Please enter your name: " in text
            addresses = [client.address for client in server._clients.values()]
            assert addresses == ["127.0.0.1"]
            return text

    try:
        text = asyncio.run(session())
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert "password" not in text.lower()
    assert (tmp_path / "ssh_host_key").is_file()


def test_ssh_reuses_the_host_key(tmp_path: Path) -> None:
    """A second SSH start keeps the same key file bytes."""
    server = _server(tmp_path, port=None, ssh_port=_free_port())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while not (tmp_path / "ssh_host_key").exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    server.shutdown()
    thread.join(timeout=2)
    key = (tmp_path / "ssh_host_key").read_bytes()
    second = _free_port()
    again = _server(tmp_path, port=None, ssh_port=second)
    again_thread = threading.Thread(target=again.serve_forever, daemon=True)
    again_thread.start()
    deadline = time.monotonic() + 2
    connected = False
    while time.monotonic() < deadline:
        try:
            probe = socket.create_connection(("127.0.0.1", second), timeout=0.2)
        except OSError:
            time.sleep(0.05)
            continue
        probe.close()
        connected = True
        break
    try:
        assert connected
        assert (tmp_path / "ssh_host_key").read_bytes() == key
    finally:
        again.shutdown()
        again_thread.join(timeout=2)


def test_a_corrupt_host_key_is_left_in_place(tmp_path: Path) -> None:
    """An unreadable key stops startup and is not replaced."""
    path = tmp_path / "ssh_host_key"
    path.write_bytes(b"not a key\n")
    server = _server(tmp_path, port=_free_port(), ssh_port=_free_port())
    with pytest.raises(ServerError, match="is not a valid key"):
        server.start()
    assert path.read_bytes() == b"not a key\n"


def test_telnet_only_does_not_create_a_host_key(tmp_path: Path) -> None:
    """A telnet listener never writes ssh_host_key."""
    server = _server(tmp_path, port=_free_port(), ssh_port=None)
    server.start()
    try:
        assert not (tmp_path / "ssh_host_key").exists()
    finally:
        server.close()


def test_ssh_survives_a_terminal_resize(tmp_path: Path) -> None:
    """A window resize must not drop the game client."""
    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_listening(ssh_port)

    async def session() -> None:
        async with asyncssh.connect(
            "127.0.0.1",
            port=ssh_port,
            known_hosts=None,
            username="someone",
        ) as conn:
            process = await conn.create_process(term_type="xterm")
            text = await process.stdout.read(256)
            assert text is not None
            assert "Please enter your name: " in text
            assert len(server._clients) == 1
            process.change_terminal_size(120, 40)
            await asyncio.sleep(0.05)
            assert len(server._clients) == 1

    try:
        asyncio.run(session())
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_shutdown_closes_an_ssh_session_quickly(tmp_path: Path) -> None:
    """Dropping game clients before the SSH join must not stall two seconds."""
    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_listening(ssh_port)
    ready = threading.Event()

    async def session() -> None:
        async with asyncssh.connect(
            "127.0.0.1",
            port=ssh_port,
            known_hosts=None,
            username="someone",
        ) as conn:
            process = await conn.create_process(term_type="xterm")
            text = await process.stdout.read(256)
            assert text is not None
            assert "Please enter your name: " in text
            ready.set()
            await asyncio.sleep(3600)

    client_task = threading.Thread(
        target=lambda: asyncio.run(session()),
        daemon=True,
    )
    client_task.start()
    assert ready.wait(timeout=2)
    assert len(server._clients) == 1
    started = time.monotonic()
    server.shutdown()
    thread.join(timeout=2)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    assert server._clients == {}
    client_task.join(timeout=1)


def test_ssh_echoes_the_name_and_hides_the_password(tmp_path: Path) -> None:
    """Typed name characters come back. Password characters do not."""
    from lupaxa.chat_server.saved_player import (
        RANK_ADMIN,
        RANK_RESIDENT,
        SavedPlayer,
        hash_password,
        save_player,
    )

    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", RANK_RESIDENT, hash_password("swordfish", rounds=4)))
    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port, players=players)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_listening(ssh_port)

    async def session() -> bytes:
        async with asyncssh.connect(
            "127.0.0.1", port=ssh_port, known_hosts=None, username="someone"
        ) as conn:
            process = await conn.create_process(term_type="xterm", encoding=None)
            data = b""
            while b"Please enter your name: " not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            process.stdin.write(b"Bea\r")
            while b"Password: " not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            process.stdin.write(b"swordfish\r")
            while b"The front desk" not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            return data

    try:
        heard = asyncio.run(session())
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert b"Bea" in heard
    assert b"swordfish" not in heard
    assert b"\r\n" in heard


def test_ssh_backspace_rubs_out_before_the_password(tmp_path: Path) -> None:
    """A mistyped name character is erased before the password prompt."""
    from lupaxa.chat_server.saved_player import (
        RANK_ADMIN,
        RANK_RESIDENT,
        SavedPlayer,
        hash_password,
        save_player,
    )

    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", RANK_RESIDENT, hash_password("swordfish", rounds=4)))
    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port, players=players)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_listening(ssh_port)

    async def session() -> bytes:
        async with asyncssh.connect(
            "127.0.0.1", port=ssh_port, known_hosts=None, username="someone"
        ) as conn:
            process = await conn.create_process(term_type="xterm", encoding=None)
            data = b""
            while b"Please enter your name: " not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            process.stdin.write(b"B\x7fBea\r")
            while b"Password: " not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            before_password = data
            process.stdin.write(b"swordfish\r")
            while b"The front desk" not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            return before_password

    try:
        heard = asyncio.run(session())
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert b"\b \b" in heard
    assert heard.index(b"\b \b") < heard.index(b"Password:")


def test_ssh_without_a_terminal_does_not_echo_the_name(tmp_path: Path) -> None:
    """No PTY means typed name bytes are not echoed back."""
    from lupaxa.chat_server.saved_player import RANK_ADMIN, SavedPlayer, hash_password, save_player

    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port, players=players)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_listening(ssh_port)

    async def session() -> bytes:
        async with asyncssh.connect(
            "127.0.0.1", port=ssh_port, known_hosts=None, username="someone"
        ) as conn:
            process = await conn.create_process(encoding=None)
            data = b""
            while b"Please enter your name: " not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            prompt_end = len(data)
            process.stdin.write(b"Nia\n")
            while b"The front desk" not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            return data[prompt_end:]

    try:
        after_name = asyncio.run(session())
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert b"The front desk" in after_name
    assert after_name.count(b"Nia") == 0


def test_ssh_login_timeout_uses_the_existing_timer(tmp_path: Path) -> None:
    """An SSH session that never enters a room can be timed out without waiting."""
    from lupaxa.chat_server.timer import apply_login_timeouts

    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_listening(ssh_port)

    async def wait_for_client() -> None:
        async with asyncssh.connect(
            "127.0.0.1", port=ssh_port, known_hosts=None, username="someone"
        ) as conn:
            process = await conn.create_process(term_type="xterm")
            while not server._clients:
                await asyncio.sleep(0.05)
            client = next(iter(server._clients.values()))
            client.accepted_at = 0.0
            apply_login_timeouts(server, 180.0)
            assert client.sock not in server._clients
            await process.wait_closed()

    try:
        asyncio.run(wait_for_client())
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_a_failed_ssh_bind_closes_telnet(tmp_path: Path) -> None:
    """The telnet port does not stay open when SSH cannot bind."""
    telnet = _free_port()
    ssh_port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", ssh_port))
    blocker.listen(1)
    server = _server(tmp_path, port=telnet, ssh_port=ssh_port)
    try:
        with pytest.raises(ServerError, match=f"Could not listen for SSH on 127.0.0.1:{ssh_port}"):
            server.start()
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        with pytest.raises(OSError):  # noqa: PT011
            probe.connect(("127.0.0.1", telnet))
        probe.close()
    finally:
        blocker.close()
        server.close()


def test_ssh_output_uses_carriage_returns(tmp_path: Path) -> None:
    """Game text reaches an SSH terminal with CRLF line ends, never a bare LF."""
    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_listening(ssh_port)

    async def session() -> bytes:
        async with asyncssh.connect(
            "127.0.0.1", port=ssh_port, known_hosts=None, username="someone"
        ) as conn:
            process = await conn.create_process(term_type="xterm", encoding=None)
            data = b""
            while b"Please enter your name: " not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            return data

    try:
        heard = asyncio.run(session())
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert b"\n" in heard
    assert heard.replace(b"\r\n", b"").count(b"\n") == 0
    assert b"\r\r" not in heard


def test_ssh_does_not_echo_a_password_typed_ahead(tmp_path: Path) -> None:
    """A password sent in the same read as the name is never echoed."""
    from lupaxa.chat_server.saved_player import (
        RANK_ADMIN,
        RANK_RESIDENT,
        SavedPlayer,
        hash_password,
        save_player,
    )

    players = tmp_path / "players"
    players.mkdir()
    save_player(players, SavedPlayer("admin", RANK_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Bea", RANK_RESIDENT, hash_password("swordfish", rounds=4)))
    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port, players=players)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_listening(ssh_port)

    async def session() -> bytes:
        async with asyncssh.connect(
            "127.0.0.1", port=ssh_port, known_hosts=None, username="someone"
        ) as conn:
            process = await conn.create_process(term_type="xterm", encoding=None)
            data = b""
            while b"Please enter your name: " not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            process.stdin.write(b"Bea\rswordfish\r")
            while b"The front desk" not in data:
                chunk = await process.stdout.read(64)
                assert chunk
                data += chunk
            return data

    try:
        heard = asyncio.run(session())
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert b"Bea" in heard
    assert b"Password: " in heard
    assert b"swordfish" not in heard


def test_ssh_hang_up_drops_the_player(tmp_path: Path) -> None:
    """Closing the SSH client before a room is entered removes the game client."""
    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_listening(ssh_port)

    async def session() -> None:
        async with asyncssh.connect(
            "127.0.0.1", port=ssh_port, known_hosts=None, username="someone"
        ) as conn:
            process = await conn.create_process(term_type="xterm")
            text = await process.stdout.read(256)
            assert text is not None
            assert "Please enter your name: " in text
            assert len(server._clients) == 1
            process.close()
            await process.wait_closed()

    try:
        asyncio.run(session())
        deadline = time.monotonic() + 2
        while server._clients and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server._clients == {}
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_startup_prints_the_version_then_each_listener(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The startup lines are the version, telnet, then SSH."""
    port = _free_port()
    ssh_port = _free_port()
    server = _server(tmp_path, port=port, ssh_port=ssh_port)
    try:
        server.start()
        out = capsys.readouterr().out
    finally:
        server.close()
    assert out.splitlines() == [
        f"Chat Server {get_version()}",
        f"Listening on 127.0.0.1:{port}",
        f"Listening for SSH on 127.0.0.1:{ssh_port}",
    ]


def test_startup_prints_only_the_ssh_line_when_telnet_is_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With telnet off the SSH line follows the version line."""
    ssh_port = _free_port()
    server = _server(tmp_path, port=None, ssh_port=ssh_port)
    try:
        server.start()
        out = capsys.readouterr().out
    finally:
        server.close()
    assert out.splitlines() == [
        f"Chat Server {get_version()}",
        f"Listening for SSH on 127.0.0.1:{ssh_port}",
    ]


def test_a_new_host_key_is_private(tmp_path: Path) -> None:
    """A newly created host key is mode 0600."""
    path = tmp_path / "ssh_host_key"
    load_or_create_host_key(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert asyncssh.read_private_key(path) is not None


def test_a_failed_host_key_write_leaves_no_partial_file(tmp_path: Path) -> None:
    """A write failure raises and removes the file this call created."""
    path = tmp_path / "missing" / "ssh_host_key"
    with pytest.raises(ServerError, match="Could not write SSH host key"):
        load_or_create_host_key(path)
    assert not path.exists()


def test_a_listener_thread_crash_raises_from_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unexpected error in the SSH thread does not leave start() blocked."""
    key = load_or_create_host_key(tmp_path / "ssh_host_key")
    listener = SSHListener("127.0.0.1", _free_port(), key, lambda *_args: None)

    async def boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(listener, "_serve", boom)
    with pytest.raises(ServerError, match="boom"):
        listener.start()
    listener.close()
