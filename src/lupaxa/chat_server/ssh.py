"""SSH listener. Game bytes stay on the selector thread."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import asyncssh

from .exceptions import ServerError


def load_or_create_host_key(path: Path) -> asyncssh.SSHKey:
    """Return the Ed25519 key at ``path``, creating it mode 0600 when absent."""
    if path.exists():
        try:
            return asyncssh.read_private_key(path)
        except OSError as exc:
            raise ServerError(f"Could not read SSH host key {path}: {exc.strerror}") from exc
        except asyncssh.KeyImportError as exc:
            raise ServerError(f"SSH host key {path} is not a valid key.") from exc
    key = asyncssh.generate_private_key("ssh-ed25519")
    data = key.export_private_key()
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    except OSError as exc:
        if created:
            with contextlib.suppress(OSError):
                path.unlink()
        raise ServerError(f"Could not write SSH host key {path}: {exc.strerror}") from exc
    return key


class _Auth(asyncssh.SSHServer):
    """Accept any username and do not ask for an SSH password."""

    def __init__(self, connections: set[asyncssh.SSHServerConnection]) -> None:
        """Share the listener's set of open connections."""
        self._connections = connections

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        """Remember ``conn`` so shutdown can close it."""
        self._connections.add(conn)

    def connection_lost(self, exc: Exception | None) -> None:
        """Forget every connection that has gone."""
        del exc
        self._connections.difference_update(
            {conn for conn in self._connections if conn.is_closed()}
        )

    def begin_auth(self, username: str) -> bool:
        """Let the first auth request succeed. The username is not a chat name."""
        del username
        return False

    def password_auth_supported(self) -> bool:
        """Password authentication is off."""
        return False

    def public_key_auth_supported(self) -> bool:
        """Public key authentication is off."""
        return False

    def kbdint_auth_supported(self) -> bool:
        """Keyboard-interactive is available when auth continues."""
        return True

    def get_kbdint_challenge(
        self, username: str, lang: str, submethods: str
    ) -> tuple[str, str, str, tuple[()]]:
        """Ask for nothing."""
        del username, lang, submethods
        return ("", "", "", ())

    def validate_kbdint_response(self, username: str, responses: object) -> bool:
        """Succeed on an empty challenge."""
        del username, responses
        return True


class SSHListener:
    """Run ``asyncssh`` and pass each session socket to ``handoff``."""

    def __init__(
        self,
        host: str,
        port: int,
        key: asyncssh.SSHKey,
        handoff: Callable[[socket.socket, str, bool], None],
        note_width: Callable[[socket.socket, int, int], None] | None = None,
        listen_sock: socket.socket | None = None,
    ) -> None:
        """Keep the bind address, the already loaded key, and the game-thread handoff."""
        self._host = host
        self._port = port
        self._key = key
        self._listen_sock = listen_sock
        self._handoff = handoff
        self._note_width = note_width
        self._loop: asyncio.AbstractEventLoop | None = None
        self._acceptor: asyncssh.SSHAcceptor | None = None
        self._connections: set[asyncssh.SSHServerConnection] = set()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: ServerError | None = None

    def start(self) -> None:
        """Bind SSH. Raise ``ServerError`` when the key or the port fails."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="ssh", daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            raise self._error

    def close(self) -> None:
        """Stop accepting and close sessions. A second call does nothing."""
        loop = self._loop
        acceptor = self._acceptor
        if loop is not None and acceptor is not None:
            loop.call_soon_threadsafe(self._stop, acceptor)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._thread = None

    def _stop(self, acceptor: asyncssh.SSHAcceptor) -> None:
        """Stop listening and close every open SSH connection."""
        acceptor.close()
        for conn in list(self._connections):
            conn.close()

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except ServerError as exc:
            self._error = exc
        except Exception as exc:  # noqa: BLE001
            self._error = ServerError(f"SSH listener stopped: {exc}")
        finally:
            self._ready.set()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        listen_kwargs: dict[str, object] = {
            "server_host_keys": [self._key],
            "server_factory": lambda: _Auth(self._connections),
            "process_factory": self._session,
            "encoding": None,
            "line_editor": False,
        }
        if self._listen_sock is not None:
            listen_kwargs["sock"] = self._listen_sock
        else:
            listen_kwargs["host"] = self._host
            listen_kwargs["port"] = self._port
        try:
            self._acceptor = await asyncssh.listen(**listen_kwargs)
        except OSError as exc:
            raise ServerError(
                f"Could not listen for SSH on {self._host}:{self._port}: {exc.strerror}"
            ) from exc
        self._ready.set()
        assert self._acceptor is not None  # noqa: S101
        await self._acceptor.wait_closed()

    async def _session(self, process: asyncssh.SSHServerProcess[Any]) -> None:
        """Bridge ``process`` to a socket the game thread already owns."""
        game, remote = socket.socketpair()
        game.setblocking(False)
        remote.setblocking(False)
        peer = process.get_extra_info("peername")
        host = peer[0] if isinstance(peer, tuple) else "unknown"
        self._report_width(process, game)
        self._handoff(game, str(host), process.get_terminal_type() is not None)
        loop = asyncio.get_running_loop()
        to_game = asyncio.ensure_future(self._to_game(process, remote, loop, game))
        try:
            await self._to_ssh(process, remote, loop)
        finally:
            to_game.cancel()
            await asyncio.gather(to_game, return_exceptions=True)
            remote.close()

    def _report_width(self, process: asyncssh.SSHServerProcess[Any], game: socket.socket) -> None:
        """Queue the latest reported terminal size (columns and rows)."""
        if self._note_width is None:
            return
        size = process.get_terminal_size()
        if size[0] <= 0 or size[1] <= 0:
            return
        self._note_width(game, size[0], size[1])

    async def _to_game(
        self,
        process: asyncssh.SSHServerProcess[Any],
        remote: socket.socket,
        loop: asyncio.AbstractEventLoop,
        game: socket.socket,
    ) -> None:
        try:
            while True:
                try:
                    data = await process.stdin.read(4096)
                except asyncssh.TerminalSizeChanged:
                    self._report_width(process, game)
                    continue
                except (asyncssh.BreakReceived, asyncssh.SignalReceived):
                    continue
                if not data:
                    break
                if isinstance(data, str):
                    data = data.encode()
                await loop.sock_sendall(remote, data)
        finally:
            with contextlib.suppress(OSError):
                remote.shutdown(socket.SHUT_WR)

    async def _to_ssh(
        self,
        process: asyncssh.SSHServerProcess[Any],
        remote: socket.socket,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        try:
            while True:
                data = await loop.sock_recv(remote, 4096)
                if data == b"":
                    break
                process.stdout.write(data)
                await process.stdout.drain()
        finally:
            process.stdout.close()
