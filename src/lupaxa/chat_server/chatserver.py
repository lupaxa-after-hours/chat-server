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
from typing import cast

import maxminddb
import psutil

from .chain import Chain, note_chain_departure, prune_offline
from .client import Client, output_hidden
from .commands import ensure_commands
from .config import Config, listen_hosts
from .country import (
    LOCAL_COUNTRY_CODE,
    LOCAL_COUNTRY_NAME,
    PRIVATE_COUNTRY_CODE,
    PRIVATE_COUNTRY_NAME,
    UNKNOWN_COUNTRY_CODE,
    UNKNOWN_COUNTRY_NAME,
    address_text,
    close_country_database,
    country_from_record,
    database_readable,
    is_local_address,
    is_private_address,
    open_country_database,
)
from .defaults import DEFAULT_LOGOUTMSG, MAX_LINE_CHARS, MAX_OUTGOING_BYTES
from .entry import announce_logout, remember_address, sync_country, warn_connected_site_admins
from .exceptions import ConfigError, ServerError
from .globals import COMMANDS
from .help import HelpBook, HelpStamp, help_stamp
from .login import name_prompt, process_input, reject_long_line, welcome_text
from .mfa import register_mfa
from .netstats import NetStats, add_counts, note_traffic
from .ranks import Rank
from .reboot import RebootState, announce_completed, clients_from_state, copy_net, discard_leftover, open_inherited
from .rooms import VOID_ID, Layout, others, release_empty_houses, restore_saved_room
from .saved_player import record_saved_logout
from .sshd import SSHListener, load_or_create_host_key
from .timer import message_file_stamps, process_timer
from .utilities import display_address, frame_width, get_clients, lookup_hostname, peer_address
from .version import get_version

SELECT_TIMEOUT_SECONDS = 0.2
BANNER_WAIT_SECONDS = 0.5
NAWS_OPTION = 31
MAX_BUFFER_BYTES = MAX_LINE_CHARS * 4
HIDE_AUTH = frozenset({"password", "set", "confirm", "code", "mfa_enroll", "mfa_pw", "mfa_remove_pw", "mfa_remove_code", "current", "renew", "renew_confirm", "retire_password", "on_duty_password", "screenlock",})  # fmt: skip  # noqa: E501


def format_endpoint(host: str, port: int) -> str:
    """Return ``host`` and ``port`` with brackets around an IPv6 address."""
    if ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def bind_listener(host: str, port: int) -> socket.socket:
    """Return a non-blocking listening TCP socket on ``host`` and ``port``.

    An IPv6 socket is IPv6-only. Dual stack uses a second IPv4 socket.
    """
    address = ipaddress.ip_address(host)
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind((host, port))
    sock.listen(16)
    sock.setblocking(False)
    return sock


def read_subnegotiation(pending: bytearray, start: int) -> tuple[bytes, int] | None:
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


def pop_line_char(buf: bytearray) -> bool:
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


def strip_telnet(client: Client, chunk: bytes) -> bytes:
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
            option = pending[index + 2]
            if option == NAWS_OPTION and command in (252, 254):
                client.naws_refused = True
            index += 3
            continue
        if command == 250:
            start = index + 2
            parsed = read_subnegotiation(pending, start)
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

    def __init__(self, config: Config, layout: Layout, help_book: HelpBook, banner: str = "", full_message: str = "", newbie_message: str = "") -> None:
        """Keep ``config``, ``layout``, the help book, and the banner.

        The socket opens in ``start``.
        """
        if config.start == VOID_ID or config.start not in layout.rooms:
            raise ConfigError(f"Start room {config.start} is not in {config.rooms}.")
        newbie_start = config.newbie_start
        if newbie_start and (newbie_start == VOID_ID or newbie_start not in layout.rooms):
            raise ConfigError(f"Newbie start room {newbie_start} is not in {config.rooms}.")
        self.config = config
        self.layout = layout
        self.help = help_book
        self.banner = banner
        self.full_message = full_message
        self.newbie_message = newbie_message
        self.net_count = 10
        self.telnet_net = NetStats()
        self.ssh_net = NetStats()
        self.combined_net = NetStats()
        self.help_stamp = help_stamp(config.help)
        self.help_failed: HelpStamp | None = None
        self.hot_stamp = help_stamp(config.hot_path)
        self.hot_failed: HelpStamp | None = None
        self.rooms_stamp = help_stamp(config.rooms)
        self.rooms_failed: HelpStamp | None = None
        self.house_stamps: dict[str, HelpStamp] = {}
        self.house_failed: dict[str, HelpStamp] = {}
        self.message_stamps = message_file_stamps(config.messages_path)
        self.message_failed: dict[str, HelpStamp] = {}
        self.help_check_seconds = 0
        self.started_at = datetime.now(timezone.utc)
        self.rebooted_at: datetime | None = None
        self.process = psutil.Process()
        self.process.cpu_percent(interval=None)
        self.roster_dir: Path = config.players
        self.stop = threading.Event()
        self.closed = False
        self.selector = selectors.DefaultSelector()
        self.listener: socket.socket | None = None
        self.listeners: list[socket.socket] = []
        self.ssh_listen_sock: socket.socket | None = None
        self.ssh_listen_socks: list[socket.socket] = []
        self.ssh: SSHListener | None = None
        self.listeners_ready = False
        self.wake_r: socket.socket | None = None
        self.wake_w: socket.socket | None = None
        self.handoff: deque[tuple[socket.socket, str, bool]] = deque()
        self.widths: dict[socket.socket, tuple[int, int]] = {}
        self.resolve_hostname: Callable[[str], str] = lookup_hostname
        self.resolve_country: Callable[[str], tuple[str, str]] = self.country_of
        self.hostnames: dict[socket.socket, tuple[str, float, str, str]] = {}
        self.country_lookup = config.country_lookup
        self.country_db: object | None = None
        self.country_lock = threading.Lock()
        database = config.country_database
        if self.country_lookup and (database is None or not database_readable(database)):
            shown = "the country database" if database is None else str(database)
            print(f"Country lookup is off. Cannot read {shown}.")
            self.country_lookup = False
        self.handoff_lock = threading.Lock()
        self.clients: dict[socket.socket, Client] = {}
        self.timer_mark: float | None = None
        self.timer_remainder = 0.0
        self.shutdown_seconds: int | None = None
        self.shutdown_reason = ""
        self.allow_newbies = config.allow_newbies
        self.allow_residents = config.allow_residents
        self.mfa_enabled = config.mfa_enabled
        self.mfa_steps: dict[str, int] = {}
        self.chains: list[Chain] = []
        if self.mfa_enabled:
            register_mfa(COMMANDS)

    def start(self) -> None:
        """Bind the configured listeners and print the startup lines."""
        if self.listeners_ready:
            return
        host_key = None
        ssh_port = self.config.ssh_port
        if ssh_port is not None:
            host_key = load_or_create_host_key(self.config.ssh_host_key)
        port = self.config.port
        if port is not None:
            self.listeners = self.bind_port(port, "Could not listen on")
            for sock in self.listeners:
                self.selector.register(sock, selectors.EVENT_READ)
            self.listener = self.listeners[0] if self.listeners else None
        if host_key is not None:
            assert ssh_port is not None  # noqa: S101
            try:
                self.ssh_listen_socks = self.bind_port(ssh_port, "Could not listen for SSH on")
            except ServerError:
                self.close_telnet()
                raise
            self.ssh_listen_sock = self.ssh_listen_socks[0] if self.ssh_listen_socks else None
            wake_r, wake_w = socket.socketpair()
            wake_r.setblocking(False)
            wake_w.setblocking(False)
            self.wake_r = wake_r
            self.wake_w = wake_w
            self.selector.register(wake_r, selectors.EVENT_READ)
            self.ssh = SSHListener(self.config.host, ssh_port, host_key, self.hand_session, self.note_width, listen_socks=self.ssh_listen_socks)
        print(f"Chat Server {get_version()}")
        if port is not None:
            for host in listen_hosts(self.config.host, self.config.stack):
                print(f"Listening on {format_endpoint(host, port)}")
        if self.ssh is not None and ssh_port is not None:
            for host in listen_hosts(self.config.host, self.config.stack):
                print(f"Listening for SSH on {format_endpoint(host, ssh_port)}")
        self.listeners_ready = True

    def bind_port(self, port: int, label: str) -> list[socket.socket]:
        """Bind every address for this stack. Close them all when one bind fails."""
        bound: list[socket.socket] = []
        try:
            for host in listen_hosts(self.config.host, self.config.stack):
                try:
                    bound.append(bind_listener(host, port))
                except OSError as exc:
                    message = f"{label} {format_endpoint(host, port)}: {exc.strerror}"
                    raise ServerError(message) from exc
        except ServerError:
            for sock in bound:
                sock.close()
            raise
        return bound

    def adopt(self, state: RebootState) -> None:
        """Register inherited listeners and telnet clients from a reboot snapshot."""
        self.rebooted_at = datetime.now(timezone.utc)
        self.started_at = state.started_at
        self.timer_mark = state.timer_mark
        self.timer_remainder = state.timer_remainder
        self.net_count = state.net_count
        self.shutdown_seconds = state.shutdown_seconds
        self.shutdown_reason = state.shutdown_reason
        self.allow_newbies = state.allow_newbies
        self.allow_residents = state.allow_residents
        self.mfa_steps = dict(state.mfa_steps)
        self.help_failed = state.help_failed
        self.help_check_seconds = state.help_check_seconds
        self.telnet_net = copy_net(state.telnet_net)
        self.ssh_net = copy_net(state.ssh_net)
        self.combined_net = copy_net(state.combined_net)
        if state.telnet_fd is not None or state.telnet_fds:
            fds = state.telnet_fds or ([state.telnet_fd] if state.telnet_fd is not None else [])
            for fd in fds:
                telnet = open_inherited(fd)
                if telnet is None:
                    continue
                telnet.setblocking(False)
                self.listeners.append(telnet)
                self.selector.register(telnet, selectors.EVENT_READ)
            self.listener = self.listeners[0] if self.listeners else None
        ssh_port = self.config.ssh_port
        ssh_fds = state.ssh_fds or ([state.ssh_fd] if state.ssh_fd is not None else [])
        if ssh_fds and ssh_port is None:
            for fd in ssh_fds:
                inherited = open_inherited(fd)
                if inherited is not None:
                    with contextlib.suppress(OSError):
                        inherited.close()
        if ssh_fds and ssh_port is not None:
            for fd in ssh_fds:
                ssh_sock = open_inherited(fd)
                if ssh_sock is None:
                    continue
                ssh_sock.setblocking(False)
                self.ssh_listen_socks.append(ssh_sock)
            if self.ssh_listen_socks:
                self.ssh_listen_sock = self.ssh_listen_socks[0]
                host_key = load_or_create_host_key(self.config.ssh_host_key)
                wake_r, wake_w = socket.socketpair()
                wake_r.setblocking(False)
                wake_w.setblocking(False)
                self.wake_r = wake_r
                self.wake_w = wake_w
                self.selector.register(wake_r, selectors.EVENT_READ)
                self.ssh = SSHListener(self.config.host, ssh_port, host_key, self.hand_session, self.note_width, listen_socks=self.ssh_listen_socks)
        voided: set[str] = set()
        for entry in state.clients:
            if entry.player is None:
                continue
            previous = entry.player.room
            restored = restore_saved_room(self, entry.player.name, previous)
            entry.player.room = restored
            if previous != VOID_ID and restored == VOID_ID:
                voided.add(entry.player.name)
        for client in clients_from_state(state):
            self.clients[client.sock] = client
            self.selector.register(client.sock, selectors.EVENT_READ)
            if client.outgoing:
                self.flush(client)
        for client in list(self.clients.values()):
            if client.name not in voided:
                continue
            self.send(client, "The room you were in has gone.\n")
            company = others(get_clients(self), VOID_ID, client)
            self.send(client, self.layout.brief(VOID_ID, company))
        online = {person.name.casefold() for person in self.clients.values() if person.name}
        kept, notices = prune_offline(list(state.chains), online)
        self.chains = kept
        for remaining, line in notices:
            wanted = {name.casefold() for name in remaining}
            for person in list(self.clients.values()):
                if person.name is not None and person.name.casefold() in wanted:
                    self.send(person, line)
        self.listeners_ready = True
        if self.ssh is not None:
            self.start_ssh_thread(from_adopt=True)
        discard_leftover(self.roster_dir)
        announce_completed(self)
        warn_connected_site_admins(self)

    def start_ssh_thread(self, *, from_adopt: bool = False) -> None:
        """Start the SSH acceptor thread when listeners were bound in ``start``."""
        ssh = self.ssh
        if ssh is None:
            return
        try:
            ssh.start()
        except ServerError as exc:
            if from_adopt:
                print(exc)
                self._close_ssh_listener_only()
                return
            self.close_telnet()
            self.close()
            raise

    def _close_ssh_listener_only(self) -> None:
        """Close SSH listener state without touching telnet."""
        ssh = self.ssh
        self.ssh = None
        if ssh is not None:
            with contextlib.suppress(Exception):
                ssh.close()
        self.close_ssh_sockets()
        self.close_wake()

    def serve_forever(self) -> None:
        """Accept, read, and flush until ``shutdown`` is called."""
        if not self.listeners_ready:
            self.start()
        self.start_ssh_thread()
        try:
            while not self.stop.is_set():
                ready = self.selector.select(timeout=SELECT_TIMEOUT_SECONDS)
                for key, mask in ready:
                    fileobj = key.fileobj
                    if isinstance(fileobj, socket.socket) and any(fileobj is sock for sock in self.listeners):
                        self.accept(fileobj)
                    elif fileobj is self.wake_r:
                        self.read_wake()
                        self.take_ssh()
                        self.apply_widths()
                        self.apply_hostnames()
                    elif isinstance(fileobj, socket.socket):
                        if mask & selectors.EVENT_WRITE:
                            client = self.clients.get(fileobj)
                            if client is not None:
                                self.flush(client)
                        if mask & selectors.EVENT_READ and fileobj in self.clients:
                            self.read(fileobj)
                self.apply_hostnames()
                self.release_pending_welcomes()
                process_timer(self)
        finally:
            self.close()

    def shutdown(self) -> None:
        """Ask the loop to stop. Safe to call from another thread."""
        self.stop.set()

    def is_stopping(self) -> bool:
        """Return whether ``shutdown`` has been asked for."""
        return self.stop.is_set()

    def close(self) -> None:
        """Close the listener and every client. A second call does nothing."""
        if self.closed:
            return
        self.closed = True
        for client in list(get_clients(self)):
            self.drop(client, announce=False)
        ssh = self.ssh
        self.ssh = None
        if ssh is not None:
            ssh.close()
        self.close_ssh_sockets()
        self.close_telnet()
        self.listeners_ready = False
        self.close_wake()
        self.close_pending_handoff()
        self.selector.close()
        with self.country_lock:
            close_country_database(self.country_db)
            self.country_db = None

    def close_telnet(self) -> None:
        """Close every telnet listener."""
        listeners = list(self.listeners)
        if self.listener is not None and self.listener not in listeners:
            listeners.append(self.listener)
        self.listeners = []
        self.listener = None
        for listener in listeners:
            self.unregister(listener)
            with contextlib.suppress(OSError):
                listener.close()

    def close_ssh_sockets(self) -> None:
        """Close every SSH listening socket."""
        socks = list(self.ssh_listen_socks)
        if self.ssh_listen_sock is not None and self.ssh_listen_sock not in socks:
            socks.append(self.ssh_listen_sock)
        self.ssh_listen_socks = []
        self.ssh_listen_sock = None
        for sock in socks:
            with contextlib.suppress(OSError):
                self.unregister(sock)
                sock.close()

    def close_wake(self) -> None:
        """Unregister and close the SSH wake sockets."""
        for sock in (self.wake_r, self.wake_w):
            if sock is None:
                continue
            self.unregister(sock)
            with contextlib.suppress(OSError):
                sock.close()
        self.wake_r = None
        self.wake_w = None

    def close_pending_handoff(self) -> None:
        """Close SSH sockets queued after the selector stopped."""
        with self.handoff_lock:
            pending = list(self.handoff)
            self.handoff.clear()
        for sock, _, _ in pending:
            with contextlib.suppress(OSError):
                sock.close()

    def accept(self, listener: socket.socket) -> None:
        """Accept one telnet connection and send the welcome."""
        try:
            sock, address = listener.accept()
        except OSError:
            return
        sock.setblocking(False)
        host = peer_address(address)
        client = Client(sock=sock, address=host, accepted_at=time.monotonic())
        self.clients[sock] = client
        self.selector.register(sock, selectors.EVENT_READ)
        print(f"Accepting connection from {host}")
        client.welcome_pending = True
        self.send_bytes(client, bytes((255, 253, NAWS_OPTION)))
        self.start_hostname_lookup(client)

    def hand_session(self, sock: socket.socket, host: str, terminal: bool) -> None:
        """Queue one SSH session and wake the selector thread."""
        with self.handoff_lock:
            self.handoff.append((sock, host, terminal))
        wake = self.wake_w
        if wake is None:
            return
        with contextlib.suppress(OSError):
            wake.send(b"\x00")

    def read_wake(self) -> None:
        """Read the wake byte so the selector does not stay readable."""
        wake = self.wake_r
        if wake is None:
            return
        with contextlib.suppress(OSError):
            wake.recv(4096)

    def take_ssh(self) -> None:
        """Register every SSH session queued by the SSH thread."""
        with self.handoff_lock:
            queued = list(self.handoff)
            self.handoff.clear()
        for sock, host, terminal in queued:
            client = Client(sock=sock, address=display_address(host), accepted_at=time.monotonic(), ssh=True, terminal=terminal)
            self.clients[sock] = client
            self.selector.register(sock, selectors.EVENT_READ)
            print(f"Accepting connection from {host}")
            with self.handoff_lock:
                stored = self.widths.pop(sock, None)
                if stored is not None:
                    columns, rows = stored
                    if columns > 0:
                        client.discovered_columns = columns
                    if rows > 0:
                        client.discovered_rows = rows
            width = frame_width(client.discovered_columns, client.frame_columns)
            self.send(client, welcome_text(self.banner, width))
            self.start_hostname_lookup(client)

    def note_width(self, sock: socket.socket, columns: int, rows: int) -> None:
        """Remember the latest terminal size for this socket."""
        with self.handoff_lock:
            self.widths[sock] = (columns, rows)
        wake = self.wake_w
        if wake is None:
            return
        with contextlib.suppress(OSError):
            wake.send(b"\x00")

    def apply_widths(self) -> None:
        """Copy a stored size onto a client that exists. Leave the rest waiting."""
        with self.handoff_lock:
            ready = [(sock, columns, rows) for sock, (columns, rows) in self.widths.items() if sock in self.clients]
            for sock, _, _ in ready:
                del self.widths[sock]
        for sock, columns, rows in ready:
            client = self.clients.get(sock)
            if client is None:
                continue
            if columns > 0:
                client.discovered_columns = columns
            if rows > 0:
                client.discovered_rows = rows

    def start_hostname_lookup(self, client: Client) -> None:
        """Resolve ``client`` away from the selector thread."""
        ip = client.address
        started = time.monotonic()
        sock = client.sock

        def work() -> None:
            found = self.resolve_hostname(ip)
            country_name = ""
            country_code = ""
            if self.country_lookup:
                country_name, country_code = self.resolve_country(ip)
            self.note_hostname(sock, found, started, country_name, country_code)

        threading.Thread(target=work, name="hostname", daemon=True).start()

    def note_hostname(self, sock: socket.socket, found: str, started: float, country_name: str = "", country_code: str = "") -> None:
        """Queue one lookup result for the selector thread."""
        with self.handoff_lock:
            self.hostnames[sock] = (found, started, country_name, country_code)
        wake = self.wake_w
        if wake is None:
            return
        with contextlib.suppress(OSError):
            wake.send(b"\x00")

    def country_of(self, ip: str) -> tuple[str, str]:
        """Return the country name and code for ``ip``."""
        if is_local_address(ip):
            return LOCAL_COUNTRY_NAME, LOCAL_COUNTRY_CODE
        if is_private_address(ip):
            return PRIVATE_COUNTRY_NAME, PRIVATE_COUNTRY_CODE
        with self.country_lock:
            reader = self.ensure_country_reader_locked()
            if reader is None:
                return UNKNOWN_COUNTRY_NAME, UNKNOWN_COUNTRY_CODE
            try:
                record = reader.get(address_text(ip))
            except (OSError, ValueError):
                return UNKNOWN_COUNTRY_NAME, UNKNOWN_COUNTRY_CODE
            return country_from_record(record)

    def ensure_country_reader(self) -> maxminddb.Reader | None:
        """Open the country database once, or turn lookup off when it cannot be read."""
        with self.country_lock:
            return self.ensure_country_reader_locked()

    def ensure_country_reader_locked(self) -> maxminddb.Reader | None:
        """Return the open reader when lookup is on. Caller holds ``country_lock``."""
        if not self.country_lookup:
            return None
        if self.country_db is not None:
            return cast(maxminddb.Reader, self.country_db)
        path = self.config.country_database
        if path is None:
            self.country_lookup = False
            return None
        reader = open_country_database(path)
        if reader is None:
            print(f"Country lookup is off. Cannot read {path}.")
            self.country_lookup = False
            return None
        self.country_db = reader
        return reader

    def apply_hostnames(self) -> None:
        """Store a lookup that finished within 2 seconds for a live client."""
        with self.handoff_lock:
            ready = [(sock, found, started, country_name, country_code) for sock, (found, started, country_name, country_code) in self.hostnames.items()]
            self.hostnames.clear()
        now = time.monotonic()
        for sock, found, started, country_name, country_code in ready:
            if now - started > 2:
                continue
            client = self.clients.get(sock)
            if client is None:
                continue
            if found == "" and country_name == "":
                continue
            if found:
                client.hostname = found
            if self.country_lookup and country_name:
                client.country_name = country_name
                client.country_code = country_code
                sync_country(client)
            if found or country_name:
                remember_address(self, client)

    def count(self, client: Client, direction: str, size: int) -> None:
        """Record ``size`` on this client's port, the combined counter, and the client."""
        port = self.ssh_net if client.ssh else self.telnet_net
        note_traffic(port, self.combined_net, client.net, direction, size)

    def read(self, sock: socket.socket) -> None:
        """Read one ready socket and feed the bytes into its client."""
        client = self.clients.get(sock)
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
        self.count(client, "in", len(chunk))
        self.feed(sock, chunk)

    def feed(self, sock: socket.socket, chunk: bytes) -> None:
        """Edit ``chunk`` one byte at a time, handling each line before the next byte."""
        client = self.clients.get(sock)
        if client is None:
            return
        data = chunk if client.ssh else strip_telnet(client, chunk)
        if client.welcome_pending and (client.discovered_columns or client.naws_refused or data):
            self.send_welcome(client)
        if len(client.iac) > MAX_BUFFER_BYTES:
            self.drop(client, announce=True)
            return
        for byte in data:
            if not self.take_key(client, byte):
                continue
            current = self.handle_buffered(sock)
            if current is None:
                return
            client = current
        if b"\n" not in client.buffer and len(client.buffer) > MAX_LINE_CHARS:
            client.buffer.clear()
            reject_long_line(self, client)

    def handle_buffered(self, sock: socket.socket) -> Client | None:
        """Handle every complete line. Return the client now on ``sock``, or None if gone."""
        client = self.clients.get(sock)
        while client is not None and b"\n" in client.buffer:
            raw, sep, rest = client.buffer.partition(b"\n")
            client.buffer = bytearray(rest)
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                self.drop(client, announce=True)
                return None
            self.handle_line(client, decoded)
            client = self.clients.get(sock)
        return client

    def handle_line(self, client: Client, decoded: str) -> None:
        """Turn one decoded line into login or a command."""
        if client.auth in ("agree", "set", "confirm", "current", "renew", "renew_confirm", "code", "mfa_enroll", "mfa_pw", "mfa_remove_pw", "on_duty_password", "screenlock") or (client.player is None and client.auth == "password"):  # fmt: skip  # noqa: E501
            line = decoded.strip("\r")
            if len(line) > MAX_LINE_CHARS:
                reject_long_line(self, client)
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
            reject_long_line(self, client)
            return
        process_input(self, client, stripped)

    def taken_names(self) -> set[str]:
        """Return the names of players who are already in a room."""
        return {client.name for client in get_clients(self) if client.name is not None}

    def unsaved_names(self, skip: Client | None = None) -> set[str]:
        """Return the lowercase names held by unsaved connections, except ``skip``."""
        claimed: set[str] = set()
        for client in get_clients(self):
            if client is skip:
                continue
            user = client.player
            if user is not None and user.rank == Rank.NEWBIE:
                claimed.add(user.name.lower())
            elif user is None and client.auth in ("set", "confirm", "agree") and client.pending_name:
                claimed.add(client.pending_name.lower())
        return claimed

    def replace_socket(self, live: Client, newcomer: Client) -> None:
        """Move ``newcomer``'s socket onto ``live`` and close the old one.

        The old socket is offered one line. No logout is written and the room
        is not told about a disconnect.
        """
        add_counts(live.net, newcomer.net)
        old = live.sock
        live.outgoing.clear()
        with contextlib.suppress(OSError):
            old.send(b"You have been reconnected from elsewhere.\n")
        if old in self.clients:
            del self.clients[old]
        with self.handoff_lock:
            self.widths.pop(old, None)
            self.hostnames.pop(old, None)
        self.unregister(old)
        with contextlib.suppress(OSError):
            old.close()
        new_sock = newcomer.sock
        live.sock = new_sock
        live.address = newcomer.address
        live.hostname = newcomer.hostname
        if newcomer.country_name:
            live.country_name = newcomer.country_name
            live.country_code = newcomer.country_code
        sync_country(live)
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
        self.clients[new_sock] = live
        print(f"{live.name} reconnected.")

    def send_welcome(self, client: Client) -> None:
        """Send the centered banner once the terminal width is known, or the wait is over."""
        if not client.welcome_pending:
            return
        client.welcome_pending = False
        width = frame_width(client.discovered_columns, client.frame_columns)
        self.send(client, welcome_text(self.banner, width))

    def release_pending_welcomes(self) -> None:
        """Send the banner when a telnet client has not reported a size in time."""
        now = time.monotonic()
        for client in list(self.clients.values()):
            if not client.welcome_pending:
                continue
            if client.discovered_columns or client.naws_refused or now - client.accepted_at >= BANNER_WAIT_SECONDS:
                self.send_welcome(client)

    def send(self, client: Client, text: str) -> bool:
        """Queue ``text``, hide echo during password entry, and flush."""
        if client.sock not in self.clients:
            return False
        if output_hidden(client):
            return True
        hiding = client.auth in HIDE_AUTH
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
        return self.flush(client)

    def take_key(self, client: Client, byte: int) -> bool:
        """Edit one byte into the line buffer. Return whether it completed a line."""
        if byte in (10, 13):
            if byte == 10 and client.saw_cr:
                client.saw_cr = False
                return False
            client.saw_cr = byte == 13
            if client.ssh and client.terminal:
                self.send_bytes(client, b"\r\n")
            client.buffer.extend(b"\n")
            return True
        if byte == 0 and client.saw_cr:
            client.saw_cr = False
            return False
        client.saw_cr = False
        if byte in (8, 127):
            if pop_line_char(client.buffer) and client.ssh and client.terminal and not client.hide_input:
                self.send_bytes(client, b"\b \b")
            return False
        client.buffer.append(byte)
        if client.ssh and client.terminal and not client.hide_input:
            self.send_bytes(client, bytes((byte,)))
        return False

    def send_bytes(self, client: Client, data: bytes) -> bool:
        """Queue raw ``data`` and flush it. This does not change echo mode."""
        if client.sock not in self.clients:
            return False
        client.outgoing.extend(data)
        if len(client.outgoing) > MAX_OUTGOING_BYTES:
            self.drop(client, announce=True)
            return False
        return self.flush(client)

    def flush(self, client: Client) -> bool:
        """Write queued bytes for ``client``. Return False when the socket is gone."""
        if client.sock not in self.clients:
            return False
        while client.outgoing:
            try:
                sent = client.sock.send(client.outgoing)
            except BlockingIOError:
                self.selector.modify(client.sock, selectors.EVENT_READ | selectors.EVENT_WRITE)
                return True
            except OSError:
                self.drop(client, announce=True)
                return False
            if sent == 0:
                self.drop(client, announce=True)
                return False
            self.count(client, "out", sent)
            del client.outgoing[:sent]
        self.selector.modify(client.sock, selectors.EVENT_READ)
        return True

    def drop(self, client: Client, *, announce: bool, skip_chain_departure: bool = False) -> None:
        """Close ``client`` and tell the room when ``announce`` is true."""
        if client.sock not in self.clients:
            return
        name = client.name
        room = client.room
        sentence = DEFAULT_LOGOUTMSG if client.player is None else client.player.logoutmsg
        if client.player is not None:
            record_saved_logout(self.roster_dir, client.player)
        if name is not None and not skip_chain_departure:
            note_chain_departure(self, name)
        del self.clients[client.sock]
        self.layout = release_empty_houses(self.layout, self.clients.values())
        with self.handoff_lock:
            self.widths.pop(client.sock, None)
            self.hostnames.pop(client.sock, None)
        self.unregister(client.sock)
        client.sock.close()
        if name is None:
            print("Connection closed before a name was chosen.")
            return
        print(f"{name} disconnected.")
        if announce and room is not None:
            announce_logout(self, name, room, sentence)

    def unregister(self, sock: socket.socket) -> None:
        """Drop ``sock`` from the selector when it is still registered."""
        try:
            self.selector.unregister(sock)
        except (KeyError, ValueError):
            return


ensure_commands()
