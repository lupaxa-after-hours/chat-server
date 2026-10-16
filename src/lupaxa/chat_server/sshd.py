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
from .utilities import peer_address


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


class Auth(asyncssh.SSHServer):
    """Accept any username and do not ask for an SSH password."""

    def __init__(self, connections: set[asyncssh.SSHServerConnection]) -> None:
        """Share the listener's set of open connections."""
        self.connections = connections

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        """Remember ``conn`` so shutdown can close it."""
        self.connections.add(conn)

    def connection_lost(self, exc: Exception | None) -> None:
        """Forget every connection that has gone."""
        del exc
        self.connections.difference_update({conn for conn in self.connections if conn.is_closed()})

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

    def get_kbdint_challenge(self, username: str, lang: str, submethods: str) -> tuple[str, str, str, tuple[()]]:
        """Ask for nothing."""
        del username, lang, submethods
        return ("", "", "", ())

    def validate_kbdint_response(self, username: str, responses: object) -> bool:
        """Succeed on an empty challenge."""
        del username, responses
        return True


class SSHListener:
    """Run ``asyncssh`` and pass each session socket to ``handoff``."""

    def __init__(self, host: str, port: int, key: asyncssh.SSHKey, handoff: Callable[[socket.socket, str, bool], None], note_width: Callable[[socket.socket, int, int], None] | None = None, listen_sock: socket.socket | None = None, listen_socks: list[socket.socket] | None = None) -> None:  # fmt: skip  # noqa: E501
        """Keep the bind address, the already loaded key, and the game-thread handoff."""
        self.host = host
        self.port = port
        self.key = key
        if listen_socks is None:
            listen_socks = [listen_sock] if listen_sock is not None else []
        self.listen_socks = listen_socks
        self.listen_sock = listen_socks[0] if listen_socks else None
        self.handoff = handoff
        self.note_width = note_width
        self.loop: asyncio.AbstractEventLoop | None = None
        self.acceptor: asyncssh.SSHAcceptor | None = None
        self.acceptors: list[asyncssh.SSHAcceptor] = []
        self.connections: set[asyncssh.SSHServerConnection] = set()
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.error: ServerError | None = None

    def start(self) -> None:
        """Bind SSH. Raise ``ServerError`` when the key or the port fails."""
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self.run, name="ssh", daemon=True)
        self.thread.start()
        self.ready.wait()
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        """Stop accepting and close sessions. A second call does nothing."""
        loop = self.loop
        acceptors = list(self.acceptors)
        if self.acceptor is not None and self.acceptor not in acceptors:
            acceptors.append(self.acceptor)
        if loop is not None:
            for acceptor in acceptors:
                loop.call_soon_threadsafe(self.stop, acceptor)
        thread = self.thread
        if thread is not None:
            thread.join(timeout=2)
        self.thread = None

    def stop(self, acceptor: asyncssh.SSHAcceptor) -> None:
        """Stop listening and close every open SSH connection."""
        acceptor.close()
        for conn in list(self.connections):
            conn.close()

    def run(self) -> None:
        """Run the SSH listener until it stops."""
        try:
            asyncio.run(self.serve())
        except ServerError as exc:
            self.error = exc
        except Exception as exc:  # noqa: BLE001
            self.error = ServerError(f"SSH listener stopped: {exc}")
        finally:
            self.ready.set()

    async def serve(self) -> None:
        """Accept SSH sessions and hand each one to the game loop."""
        self.loop = asyncio.get_running_loop()
        listen_kwargs: dict[str, object] = {
            "server_host_keys": [self.key],
            "server_factory": lambda: Auth(self.connections),
            "process_factory": self.session,
            "encoding": None,
            "line_editor": False,
        }
        if self.listen_socks:
            try:
                for sock in self.listen_socks:
                    self.acceptors.append(await asyncssh.listen(**listen_kwargs, sock=sock))
            except OSError as exc:
                for acceptor in self.acceptors:
                    acceptor.close()
                self.acceptors.clear()
                raise ServerError(f"Could not listen for SSH on {self.host}:{self.port}: {exc.strerror}") from exc
        else:
            listen_kwargs["host"] = self.host
            listen_kwargs["port"] = self.port
            try:
                self.acceptors.append(await asyncssh.listen(**listen_kwargs))
            except OSError as exc:
                raise ServerError(f"Could not listen for SSH on {self.host}:{self.port}: {exc.strerror}") from exc
        self.acceptor = self.acceptors[0]
        self.ready.set()
        await asyncio.gather(*(item.wait_closed() for item in self.acceptors))

    async def session(self, process: asyncssh.SSHServerProcess[Any]) -> None:
        """Bridge ``process`` to a socket the game thread already owns."""
        game, remote = socket.socketpair()
        game.setblocking(False)
        remote.setblocking(False)
        peer = process.get_extra_info("peername")
        host = peer_address(peer) if peer is not None else "unknown"
        self.report_width(process, game)
        self.handoff(game, str(host), process.get_terminal_type() is not None)
        loop = asyncio.get_running_loop()
        to_game = asyncio.ensure_future(self.to_game(process, remote, loop, game))
        try:
            await self.to_ssh(process, remote, loop)
        finally:
            to_game.cancel()
            await asyncio.gather(to_game, return_exceptions=True)
            remote.close()

    def report_width(self, process: asyncssh.SSHServerProcess[Any], game: socket.socket) -> None:
        """Queue the latest reported terminal size (columns and rows)."""
        if self.note_width is None:
            return
        size = process.get_terminal_size()
        if size[0] <= 0 or size[1] <= 0:
            return
        self.note_width(game, size[0], size[1])

    async def to_game(self, process: asyncssh.SSHServerProcess[Any], remote: socket.socket, loop: asyncio.AbstractEventLoop, game: socket.socket) -> None:  # fmt: skip  # noqa: E501
        """Copy SSH input onto the game socket."""
        try:
            while True:
                try:
                    data = await process.stdin.read(4096)
                except asyncssh.TerminalSizeChanged:
                    self.report_width(process, game)
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

    async def to_ssh(self, process: asyncssh.SSHServerProcess[Any], remote: socket.socket, loop: asyncio.AbstractEventLoop) -> None:
        """Copy game output onto the SSH channel."""
        try:
            while True:
                data = await loop.sock_recv(remote, 4096)
                if data == b"":
                    break
                process.stdout.write(data)
                await process.stdout.drain()
        finally:
            process.stdout.close()
