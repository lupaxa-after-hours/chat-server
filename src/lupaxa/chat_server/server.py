"""Multi-room TCP chat on a selectors loop."""

from __future__ import annotations

import contextlib
import ipaddress
import selectors
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .channel import announce_reconnect, broadcast_room_split
from .client import Client
from .commands_list import COMMANDS, ensure_commands
from .config import Config
from .defaults import MAX_LINE_CHARS, MAX_OUTGOING_BYTES
from .entry import _remember_address
from .exceptions import ConfigError, ServerError
from .help import HelpBook
from .login import name_prompt, process_input, welcome_text
from .mfa import register_mfa
from .rooms import Layout
from .saved_player import RANK_NEWBIE, record_saved_logout
from .ssh import SSHListener, load_or_create_host_key
from .timer import process_timer
from .utilities import disconnected_from_line, disconnected_line, display_address, lookup_hostname
from .version import get_version

_SELECT_TIMEOUT_SECONDS = 0.2
_MAX_BUFFER_BYTES = MAX_LINE_CHARS * 4
_HIDE_AUTH = frozenset(
    {
        "password",
        "set",
        "confirm",
        "code",
        "mfa_enroll",
        "mfa_pw",
        "mfa_remove_pw",
        "mfa_remove_code",
        "current",
        "renew",
        "renew_confirm",
        "retire_password",
    }
)


def _bind_listener(host: str, port: int) -> socket.socket:
    """Return a non-blocking listening TCP socket on ``host`` and ``port``."""
    address = ipaddress.ip_address(host)
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(16)
    sock.setblocking(False)
    return sock


def _read_subnegotiation(pending: bytearray, start: int) -> tuple[bytes, int] | None:
    """Return the unescaped payload and the index after IAC SE.

    ``255, 255`` is one data byte. ``255, 240`` ends the subnegotiation.
    ``None`` means the sequence is still incomplete.
    """
    data = bytearray()
    index = start
    while index < len(pending):
        byte = pending[index]
        if byte != 255:
            data.append(byte)
            index += 1
            continue
        if index + 1 >= len(pending):
            return None
        mark = pending[index + 1]
        if mark == 255:
            data.append(255)
            index += 2
            continue
        if mark == 240:
            return bytes(data), index + 2
        return bytes(data), index + 2
    return None


def _pop_line_char(buf: bytearray) -> bool:
    """Remove the last character of the unfinished line. Return whether one was removed."""
    start = buf.rfind(b"\n") + 1
    line = bytes(buf[start:])
    if line == b"":
        return False
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        del buf[-1:]
        return True
    buf[start:] = text[:-1].encode("utf-8")
    return True


def _strip_telnet(client: Client, chunk: bytes) -> bytes:
    """Remove telnet negotiation from ``chunk``, keeping a partial sequence on ``client``."""
    pending = client.iac
    pending.extend(chunk)
    out = bytearray()
    index = 0
    while index < len(pending):
        if pending[index] != 255:
            out.append(pending[index])
            index += 1
            continue
        if index + 1 >= len(pending):
            break
        command = pending[index + 1]
        if command == 255:
            out.append(255)
            index += 2
            continue
        if command in (251, 252, 253, 254):
            if index + 2 >= len(pending):
                break
            index += 3
            continue
        if command == 250:
            start = index + 2
            parsed = _read_subnegotiation(pending, start)
            if parsed is None:
                break
            payload, after = parsed
            index = after
            if len(payload) >= 5 and payload[0] == 31:
                width = (payload[1] << 8) | payload[2]
                height = (payload[3] << 8) | payload[4]
                if width > 0 and height > 0:
                    client.discovered_columns = width
                    client.discovered_rows = height
            continue
        index += 2
    del pending[:index]
    return bytes(out)


class ChatServer:
    """Bind ``0.0.0.0`` and serve until ``shutdown``."""

    def __init__(self, config: Config, layout: Layout, help_book: HelpBook) -> None:
        """Keep ``config``, ``layout``, and the help book. The socket opens in ``start``."""
        if config.start not in layout.rooms:
            raise ConfigError(f"Start room {config.start} is not in {config.rooms}.")
        self._config = config
        self._layout = layout
        self._help = help_book
        self.started_at = datetime.now(timezone.utc)
        self._roster_dir: Path = config.players
        self._stop = threading.Event()
        self._closed = False
        self._selector = selectors.DefaultSelector()
        self._listener: socket.socket | None = None
        self._ssh_listen_sock: socket.socket | None = None
        self._ssh: SSHListener | None = None
        self._listeners_ready = False
        self._wake_r: socket.socket | None = None
        self._wake_w: socket.socket | None = None
        self._handoff: deque[tuple[socket.socket, str, bool]] = deque()
        self._widths: dict[socket.socket, tuple[int, int]] = {}
        self._resolve_hostname: Callable[[str], str] = lookup_hostname
        self._hostnames: dict[socket.socket, tuple[str, float]] = {}
        self._handoff_lock = threading.Lock()
        self._clients: dict[socket.socket, Client] = {}
        self._timer_mark: float | None = None
        self._timer_remainder = 0.0
        self._shutdown_seconds: int | None = None
        self._shutdown_reason = ""
        self._allow_newbies = config.allow_newbies
        self._allow_residents = config.allow_residents
        self._mfa_enabled = config.mfa_enabled
        self._mfa_steps: dict[str, int] = {}
        if self._mfa_enabled:
            register_mfa(COMMANDS)

    def start(self) -> None:
        """Bind the configured listeners and print the startup lines."""
        if self._listeners_ready:
            return
        host_key = None
        ssh_port = self._config.ssh_port
        if ssh_port is not None:
            host_key = load_or_create_host_key(self._config.ssh_host_key)
        port = self._config.port
        if port is not None:
            try:
                sock = _bind_listener(self._config.host, port)
            except OSError as exc:
                message = f"Could not listen on {self._config.host}:{port}: {exc.strerror}"
                raise ServerError(message) from exc
            self._listener = sock
            self._selector.register(sock, selectors.EVENT_READ)
        if host_key is not None:
            assert ssh_port is not None  # noqa: S101
            try:
                ssh_sock = _bind_listener(self._config.host, ssh_port)
            except OSError as exc:
                self._close_telnet()
                message = (
                    f"Could not listen for SSH on {self._config.host}:{ssh_port}: {exc.strerror}"
                )
                raise ServerError(message) from exc
            self._ssh_listen_sock = ssh_sock
            wake_r, wake_w = socket.socketpair()
            wake_r.setblocking(False)
            wake_w.setblocking(False)
            self._wake_r = wake_r
            self._wake_w = wake_w
            self._selector.register(wake_r, selectors.EVENT_READ)
            self._ssh = SSHListener(
                self._config.host,
                ssh_port,
                host_key,
                self._hand_session,
                self._note_width,
                listen_sock=ssh_sock,
            )
        print(f"Chat Server {get_version()}")
        if port is not None:
            print(f"Listening on {self._config.host}:{port}")
        if self._ssh is not None:
            print(f"Listening for SSH on {self._config.host}:{ssh_port}")
        self._listeners_ready = True

    def _start_ssh_thread(self) -> None:
        """Start the SSH acceptor thread when listeners were bound in ``start``."""
        ssh = self._ssh
        if ssh is None:
            return
        try:
            ssh.start()
        except ServerError:
            self._close_telnet()
            self.close()
            raise

    def serve_forever(self) -> None:
        """Accept, read, and flush until ``shutdown`` is called."""
        if not self._listeners_ready:
            self.start()
        self._start_ssh_thread()
        try:
            while not self._stop.is_set():
                ready = self._selector.select(timeout=_SELECT_TIMEOUT_SECONDS)
                for key, mask in ready:
                    fileobj = key.fileobj
                    if fileobj is self._listener:
                        self._accept()
                    elif fileobj is self._wake_r:
                        self._read_wake()
                        self._take_ssh()
                        self._apply_widths()
                        self._apply_hostnames()
                    elif isinstance(fileobj, socket.socket):
                        if mask & selectors.EVENT_WRITE:
                            client = self._clients.get(fileobj)
                            if client is not None:
                                self._flush(client)
                        if mask & selectors.EVENT_READ and fileobj in self._clients:
                            self._read(fileobj)
                self._apply_hostnames()
                process_timer(self)
        finally:
            self.close()

    def shutdown(self) -> None:
        """Ask the loop to stop. Safe to call from another thread."""
        self._stop.set()

    def is_stopping(self) -> bool:
        """Return whether ``shutdown`` has been asked for."""
        return self._stop.is_set()

    def close(self) -> None:
        """Close the listener and every client. A second call does nothing."""
        if self._closed:
            return
        self._closed = True
        for client in list(self._clients.values()):
            self.drop(client, announce=False)
        ssh = self._ssh
        self._ssh = None
        if ssh is not None:
            ssh.close()
        listen_sock = self._ssh_listen_sock
        self._ssh_listen_sock = None
        if listen_sock is not None:
            with contextlib.suppress(OSError):
                listen_sock.close()
        self._close_telnet()
        self._listeners_ready = False
        self._close_wake()
        self._close_pending_handoff()
        self._selector.close()

    def _close_telnet(self) -> None:
        """Close the telnet listener when one was bound."""
        listener = self._listener
        self._listener = None
        if listener is None:
            return
        self._unregister(listener)
        listener.close()

    def _close_wake(self) -> None:
        """Unregister and close the SSH wake sockets."""
        for sock in (self._wake_r, self._wake_w):
            if sock is None:
                continue
            self._unregister(sock)
            with contextlib.suppress(OSError):
                sock.close()
        self._wake_r = None
        self._wake_w = None

    def _close_pending_handoff(self) -> None:
        """Close SSH sockets queued after the selector stopped."""
        with self._handoff_lock:
            pending = list(self._handoff)
            self._handoff.clear()
        for sock, _host, _terminal in pending:
            with contextlib.suppress(OSError):
                sock.close()

    def _accept(self) -> None:
        if self._listener is None:
            return
        try:
            sock, address = self._listener.accept()
        except OSError:
            return
        sock.setblocking(False)
        host = address[0] if isinstance(address, tuple) else str(address)
        client = Client(sock=sock, address=display_address(host), accepted_at=time.monotonic())
        self._clients[sock] = client
        self._selector.register(sock, selectors.EVENT_READ)
        print(f"Accepting connection from {host}")
        self._send_bytes(client, bytes((255, 253, 31)))
        self.send(client, welcome_text(get_version()))
        self._start_hostname_lookup(client)

    def _hand_session(self, sock: socket.socket, host: str, terminal: bool) -> None:
        """Queue one SSH session and wake the selector thread."""
        with self._handoff_lock:
            self._handoff.append((sock, host, terminal))
        wake = self._wake_w
        if wake is None:
            return
        with contextlib.suppress(OSError):
            wake.send(b"\x00")

    def _read_wake(self) -> None:
        """Read the wake byte so the selector does not stay readable."""
        wake = self._wake_r
        if wake is None:
            return
        with contextlib.suppress(OSError):
            wake.recv(4096)

    def _take_ssh(self) -> None:
        """Register every SSH session queued by the SSH thread."""
        with self._handoff_lock:
            queued = list(self._handoff)
            self._handoff.clear()
        for sock, host, terminal in queued:
            client = Client(
                sock=sock,
                address=display_address(host),
                accepted_at=time.monotonic(),
                ssh=True,
                terminal=terminal,
            )
            self._clients[sock] = client
            self._selector.register(sock, selectors.EVENT_READ)
            print(f"Accepting connection from {host}")
            self.send(client, welcome_text(get_version()))
            self._start_hostname_lookup(client)

    def _note_width(self, sock: socket.socket, columns: int, rows: int) -> None:
        """Remember the latest terminal size for this socket."""
        with self._handoff_lock:
            self._widths[sock] = (columns, rows)
        wake = self._wake_w
        if wake is None:
            return
        with contextlib.suppress(OSError):
            wake.send(b"\x00")

    def _apply_widths(self) -> None:
        """Copy a stored size onto a client that exists. Leave the rest waiting."""
        with self._handoff_lock:
            ready = [
                (sock, columns, rows)
                for sock, (columns, rows) in self._widths.items()
                if sock in self._clients
            ]
            for sock, _columns, _rows in ready:
                del self._widths[sock]
        for sock, columns, rows in ready:
            client = self._clients.get(sock)
            if client is None:
                continue
            if columns > 0:
                client.discovered_columns = columns
            if rows > 0:
                client.discovered_rows = rows

    def _start_hostname_lookup(self, client: Client) -> None:
        """Resolve ``client`` away from the selector thread."""
        ip = client.address
        started = time.monotonic()
        sock = client.sock

        def work() -> None:
            found = self._resolve_hostname(ip)
            self._note_hostname(sock, found, started)

        threading.Thread(target=work, name="hostname", daemon=True).start()

    def _note_hostname(self, sock: socket.socket, found: str, started: float) -> None:
        """Queue one lookup result for the selector thread."""
        with self._handoff_lock:
            self._hostnames[sock] = (found, started)
        wake = self._wake_w
        if wake is None:
            return
        with contextlib.suppress(OSError):
            wake.send(b"\x00")

    def _apply_hostnames(self) -> None:
        """Store a lookup that finished within 2 seconds for a live client."""
        with self._handoff_lock:
            ready = [(sock, found, started) for sock, (found, started) in self._hostnames.items()]
            self._hostnames.clear()
        now = time.monotonic()
        for sock, found, started in ready:
            if now - started > 2:
                continue
            client = self._clients.get(sock)
            if client is None or found == "":
                continue
            client.hostname = found
            _remember_address(self, client)

    def _read(self, sock: socket.socket) -> None:
        client = self._clients.get(sock)
        if client is None:
            return
        try:
            chunk = sock.recv(4096)
        except BlockingIOError:
            return
        except OSError:
            self.drop(client, announce=True)
            return
        if chunk == b"":
            self.drop(client, announce=True)
            return
        self._feed(sock, chunk)

    def _feed(self, sock: socket.socket, chunk: bytes) -> None:
        """Edit ``chunk`` one byte at a time, handling each line before the next byte."""
        client = self._clients.get(sock)
        if client is None:
            return
        data = chunk if client.ssh else _strip_telnet(client, chunk)
        if len(client.iac) > _MAX_BUFFER_BYTES:
            self.drop(client, announce=True)
            return
        for byte in data:
            if not self._take_key(client, byte):
                continue
            current = self._handle_buffered(sock)
            if current is None:
                return
            client = current
        if b"\n" not in client.buffer and len(client.buffer) > _MAX_BUFFER_BYTES:
            self.drop(client, announce=True)

    def _handle_buffered(self, sock: socket.socket) -> Client | None:
        """Handle every complete line. Return the client now on ``sock``, or None if gone."""
        client = self._clients.get(sock)
        while client is not None and b"\n" in client.buffer:
            raw, _sep, rest = client.buffer.partition(b"\n")
            client.buffer = bytearray(rest)
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                self.drop(client, announce=True)
                return None
            self._handle_line(client, decoded)
            client = self._clients.get(sock)
        return client

    def _handle_line(self, client: Client, decoded: str) -> None:
        if client.auth in (
            "set",
            "confirm",
            "current",
            "renew",
            "renew_confirm",
            "code",
            "mfa_enroll",
            "mfa_pw",
            "mfa_remove_pw",
        ) or (client.player is None and client.auth == "password"):
            line = decoded.strip("\r")
            if len(line) > MAX_LINE_CHARS:
                self.drop(client, announce=True)
                return
            process_input(self, client, line)
            return
        stripped = decoded.strip("\r").strip()
        if stripped == "":
            if client.player is None:
                self.send(client, name_prompt())
            else:
                process_input(self, client, "")
            return
        if len(stripped) > MAX_LINE_CHARS:
            self.drop(client, announce=True)
            return
        process_input(self, client, stripped)

    def _taken_names(self) -> set[str]:
        return {client.name for client in self._clients.values() if client.name is not None}

    def _unsaved_names(self, skip: Client | None = None) -> set[str]:
        """Return the lowercase names held by unsaved connections, except ``skip``."""
        claimed: set[str] = set()
        for client in self._clients.values():
            if client is skip:
                continue
            user = client.player
            if user is not None and user.rank == RANK_NEWBIE:
                claimed.add(user.name.lower())
            elif user is None and client.auth in ("set", "confirm") and client.pending_name:
                claimed.add(client.pending_name.lower())
        return claimed

    def _replace_socket(self, live: Client, newcomer: Client) -> None:
        """Move ``newcomer``'s socket onto ``live`` and close the old one.

        The old socket is offered one line. No logout is written and the room
        is not told about a disconnect.
        """
        old = live.sock
        live.outgoing.clear()
        with contextlib.suppress(OSError):
            old.send(b"You have been reconnected from elsewhere.\n")
        if old in self._clients:
            del self._clients[old]
        with self._handoff_lock:
            self._widths.pop(old, None)
            self._hostnames.pop(old, None)
        self._unregister(old)
        with contextlib.suppress(OSError):
            old.close()
        new_sock = newcomer.sock
        live.sock = new_sock
        live.address = newcomer.address
        live.hostname = newcomer.hostname
        live.ssh = newcomer.ssh
        live.discovered_columns = newcomer.discovered_columns
        live.discovered_rows = newcomer.discovered_rows
        live.frame_columns = None
        live.terminal = newcomer.terminal
        live.buffer = newcomer.buffer
        live.outgoing = newcomer.outgoing
        live.iac = newcomer.iac
        live.saw_cr = newcomer.saw_cr
        if newcomer.hide_input and not live.hide_input:
            live.hide_input = True
        self._clients[new_sock] = live
        announce_reconnect(str(live.name))

    def send(self, client: Client, text: str) -> bool:
        """Queue ``text``, hide echo during password entry, and flush."""
        if client.sock not in self._clients:
            return False
        hiding = client.auth in _HIDE_AUTH
        prefix = b""
        if hiding and not client.hide_input:
            client.hide_input = True
            if not client.ssh:
                prefix = b"\xff\xfb\x01"
        elif client.hide_input and not hiding:
            client.hide_input = False
            if not client.ssh:
                prefix = b"\xff\xfc\x01"
        client.outgoing.extend(prefix)
        if client.ssh and client.terminal:
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        client.outgoing.extend(text.encode("utf-8"))
        if len(client.outgoing) > MAX_OUTGOING_BYTES:
            self.drop(client, announce=True)
            return False
        return self._flush(client)

    def _take_key(self, client: Client, byte: int) -> bool:
        """Edit one byte into the line buffer. Return whether it completed a line."""
        if byte in (10, 13):
            if byte == 10 and client.saw_cr:
                client.saw_cr = False
                return False
            client.saw_cr = byte == 13
            if client.ssh and client.terminal:
                self._send_bytes(client, b"\r\n")
            client.buffer.extend(b"\n")
            return True
        if byte == 0 and client.saw_cr:
            client.saw_cr = False
            return False
        client.saw_cr = False
        if byte in (8, 127):
            if (
                _pop_line_char(client.buffer)
                and client.ssh
                and client.terminal
                and not client.hide_input
            ):
                self._send_bytes(client, b"\b \b")
            return False
        client.buffer.append(byte)
        if client.ssh and client.terminal and not client.hide_input:
            self._send_bytes(client, bytes((byte,)))
        return False

    def _send_bytes(self, client: Client, data: bytes) -> bool:
        """Queue raw ``data`` and flush it. This does not change echo mode."""
        if client.sock not in self._clients:
            return False
        client.outgoing.extend(data)
        if len(client.outgoing) > MAX_OUTGOING_BYTES:
            self.drop(client, announce=True)
            return False
        return self._flush(client)

    def _flush(self, client: Client) -> bool:
        if client.sock not in self._clients:
            return False
        while client.outgoing:
            try:
                sent = client.sock.send(client.outgoing)
            except BlockingIOError:
                self._selector.modify(client.sock, selectors.EVENT_READ | selectors.EVENT_WRITE)
                return True
            except OSError:
                self.drop(client, announce=True)
                return False
            if sent == 0:
                self.drop(client, announce=True)
                return False
            del client.outgoing[:sent]
        self._selector.modify(client.sock, selectors.EVENT_READ)
        return True

    def drop(self, client: Client, *, announce: bool) -> None:
        """Close ``client`` and tell the room when ``announce`` is true."""
        if client.sock not in self._clients:
            return
        name = client.name
        room = client.room
        address = client.address
        if client.player is not None:
            record_saved_logout(self._roster_dir, client.player)
        del self._clients[client.sock]
        with self._handoff_lock:
            self._widths.pop(client.sock, None)
            self._hostnames.pop(client.sock, None)
        self._unregister(client.sock)
        client.sock.close()
        if name is None:
            print("Connection closed before a name was chosen.")
            return
        print(f"{name} disconnected.")
        if announce and room is not None:
            broadcast_room_split(
                self,
                room,
                disconnected_line(name),
                disconnected_from_line(name, address),
                skip=None,
            )

    def _unregister(self, sock: socket.socket) -> None:
        try:
            self._selector.unregister(sock)
        except KeyError:
            return


ensure_commands()
