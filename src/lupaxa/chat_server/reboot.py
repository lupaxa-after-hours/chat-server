"""Telnet reboot snapshot written to the players directory."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import socket
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .broadcast import broadcast_staff
from .chain import Chain
from .client import Client
from .defaults import DEFAULT_ENTERMSG, DEFAULT_LOGINMSG, DEFAULT_LOGOUTMSG, DEFAULT_PROMPT
from .help import HelpStamp
from .netstats import NetStats
from .player import Player
from .ranks import live_rank, saved_rank
from .saved_player import SavedPlayer
from .utilities import get_clients, get_player_name

if TYPE_CHECKING:
    from .server import Server

NET_FIELDS = ("in_total", "out_total", "in_pack_total", "out_pack_total", "in_current", "out_current", "in_pack_current", "out_pack_current", "in_bps", "out_bps", "in_pps", "out_pps", "in_average", "out_average", "in_pack_average", "out_pack_average")  # fmt: skip  # noqa: E501

CARRY_ON = "The reboot did not happen. Carry on with what you were doing.\n"

COMPLETED = "< SU > Reboot completed.\n"

KNOWN_AUTH = frozenset(
    {
        "name",
        "password",
        "agree",
        "set",
        "confirm",
        "code",
        "mfa_enroll",
        "mfa_pw",
        "mfa_show",
        "mfa_remove_pw",
        "mfa_remove_code",
        "mfa_sure",
        "current",
        "renew",
        "renew_confirm",
        "retire_confirm",
        "retire_password",
        "on_duty_password",
        "screenlock",
    }
)


@dataclass
class RebootClient:
    """One telnet client at snapshot time, without its live socket."""

    fileno: int
    buffer: bytes = b""
    outgoing: bytes = b""
    iac: bytes = b""
    player: Player | None = None
    auth: str = "name"
    pending_name: str = ""
    pending_saved: SavedPlayer | None = None
    pending_password: str = ""
    pending_current_password: str = ""
    pending_mfa_secret: str = ""
    pending_issuer: str = ""
    password_failures: int = 0
    address: str = "unknown"
    hostname: str = ""
    accepted_at: float = 0.0
    terminal: bool = False
    hide_input: bool = False
    discovered_columns: int | None = None
    discovered_rows: int | None = None
    frame_columns: int | None = None
    saw_cr: bool = False
    restart_notice: str = ""
    net: NetStats = field(default_factory=NetStats)
    country_name: str = ""
    country_code: str = ""
    welcome_pending: bool = False


@dataclass
class RebootState:
    """Live server fields and telnet clients for a reboot file."""

    started_at: datetime
    timer_mark: float | None
    timer_remainder: float
    net_count: int
    shutdown_seconds: int | None
    shutdown_reason: str
    allow_newbies: bool
    allow_residents: bool
    mfa_steps: dict[str, int]
    help_failed: HelpStamp | None
    help_check_seconds: int
    telnet_net: NetStats
    ssh_net: NetStats
    combined_net: NetStats
    telnet_fd: int | None = None
    ssh_fd: int | None = None
    reason: str = ""
    clients: list[RebootClient] = field(default_factory=list)
    telnet_fds: list[int] = field(default_factory=list)
    ssh_fds: list[int] = field(default_factory=list)
    chains: list[Chain] = field(default_factory=list)


def rebooting_line(reason: str) -> str:
    """Return the reboot warning sent to SSH clients."""
    if reason:
        return f"Rebooting now for '{reason}'.\n"
    return "Rebooting now!\n"


def initiated_line(name: str, reason: str) -> str:
    """Return the staff-channel line when a reboot file has been written."""
    if reason:
        return f"< SU > {name} has initiated a reboot for '{reason}'.\n"
    return f"< SU > {name} has initiated a reboot.\n"


def prepare_reboot(server: Server, client: Client, reason: str) -> RebootState | None:
    """Write the reboot file and warn SSH and telnet clients. Does not drop or exec."""
    if server.config.port is None:
        server.send(client, "No telnet port is open. Use shutdown.\n")
        return None
    clients = list(get_clients(server))
    state = snapshot(server, clients, reason)
    try:
        write_reboot(server.roster_dir, state)
    except OSError as exc:
        print(exc)
        server.send(client, "Cannot write the reboot file.\n")
        return None
    broadcast_staff(server, initiated_line(get_player_name(client), reason))
    telnet_index = 0
    for live in clients:
        if live.ssh:
            server.send(live, rebooting_line(reason))
            continue
        notice = state.clients[telnet_index].restart_notice
        telnet_index += 1
        if notice:
            server.send(live, notice)
    return state


def replace_process(argv: list[str]) -> None:
    """Replace this process with a new server run that adopts telnet sockets."""
    os.execv(sys.executable, [sys.executable, *argv])  # noqa: S606


def finish_reboot(server: Server, client: Client, state: RebootState, *, replace: Callable[[list[str]], None]) -> None:
    """Mark telnet sockets inheritable, drop SSH clients, and exec the reboot child."""
    del client
    try:
        for live in get_clients(server):
            if live.ssh:
                continue
            sock = getattr(live, "sock", None)
            if sock is not None:
                sock.set_inheritable(True)
        for plural, singular in (("listeners", "listener"), ("ssh_listen_socks", "ssh_listen_sock")):
            for listen in listen_sockets(server, plural, singular):
                listen.set_inheritable(True)
        handoff = getattr(server, "handoff", None)
        if handoff is not None:
            handoff_lock = getattr(server, "handoff_lock", None)
            if handoff_lock is not None:
                with handoff_lock:
                    pending = list(handoff)
                    handoff.clear()
            else:
                pending = list(handoff)
                handoff.clear()
            for item in pending:
                sock = item[0]
                with contextlib.suppress(OSError):
                    sock.close()
        for live in list(get_clients(server)):
            if not live.ssh:
                continue
            server.flush(live)
            server.drop(live, announce=True, skip_chain_departure=True)
        telnet_numbers = socket_numbers(listen_sockets(server, "listeners", "listener"))
        ssh_numbers = socket_numbers(listen_sockets(server, "ssh_listen_socks", "ssh_listen_sock"))
        state.telnet_fds = telnet_numbers
        state.ssh_fds = ssh_numbers
        state.telnet_fd = telnet_numbers[0] if telnet_numbers else None
        state.ssh_fd = ssh_numbers[0] if ssh_numbers else None
        refresh_reboot_clients(server, state)
        write_reboot(server.roster_dir, state)
        argv = [*sys.argv, "--reboot", str(reboot_path(server.roster_dir))]
        replace(argv)
    except OSError:
        broadcast_staff(server, "< SU > Reboot failed.\n")
        for live in get_clients(server):
            if live.ssh:
                continue
            entry = reboot_client_for_live(state, live)
            if entry is not None and entry.restart_notice:
                server.send(live, CARRY_ON)
        discard_leftover(server.roster_dir)


def restart_line(reason: str) -> str:
    """Return the reboot notice shown when an unsavable login step was cleared."""
    if reason:
        return f"Rebooting now for '{reason}'. You will need to start this again after the reboot.\n"
    return "Rebooting now! You will need to start this again after the reboot.\n"


def reboot_path(directory: Path) -> Path:
    """Return the reboot snapshot path inside ``directory``."""
    return directory / "reboot.json"


def discard_leftover(directory: Path) -> None:
    """Remove a leftover ``reboot.json`` when the server is not adopting one."""
    path = reboot_path(directory)
    if path.is_file():
        path.unlink()


def open_inherited(fd: int) -> socket.socket | None:
    """Return a socket for an inherited file descriptor, or ``None`` when it is gone."""
    try:
        return socket.socket(fileno=fd)
    except OSError:
        return None


def clients_from_state(state: RebootState) -> list[Client]:
    """Rebuild telnet clients from a reboot snapshot, skipping dead file descriptors."""
    restored: list[Client] = []
    for entry in state.clients:
        sock = open_inherited(entry.fileno)
        if sock is None:
            continue
        client = Client(
            sock=sock,
            player=entry.player,
            auth=entry.auth,
            pending_name=entry.pending_name,
            pending_saved=entry.pending_saved,
            pending_password=entry.pending_password,
            pending_current_password=entry.pending_current_password,
            pending_mfa_secret=entry.pending_mfa_secret,
            pending_issuer=entry.pending_issuer,
            password_failures=entry.password_failures,
            address=entry.address,
            hostname=entry.hostname,
            country_name=entry.country_name,
            country_code=entry.country_code,
            accepted_at=entry.accepted_at,
            terminal=entry.terminal,
            hide_input=entry.hide_input,
            discovered_columns=entry.discovered_columns,
            discovered_rows=entry.discovered_rows,
            frame_columns=entry.frame_columns,
        )
        client.buffer.extend(entry.buffer)
        client.outgoing.extend(entry.outgoing)
        client.iac.extend(entry.iac)
        client.saw_cr = entry.saw_cr
        client.welcome_pending = entry.welcome_pending
        for name in NET_FIELDS:
            setattr(client.net, name, getattr(entry.net, name))
        sock.setblocking(False)
        restored.append(client)
    return restored


def announce_completed(server: Server) -> None:
    """Tell staff that the reboot child has taken over the telnet listeners."""
    broadcast_staff(server, COMPLETED)


def listen_sockets(server: Server, plural: str, singular: str) -> list[socket.socket]:
    """Return listening sockets from the list, then the single socket when it is separate."""
    socks = [sock for sock in list(getattr(server, plural, []) or []) if isinstance(sock, socket.socket)]
    one = getattr(server, singular, None)
    if isinstance(one, socket.socket) and one not in socks:
        socks.append(one)
    return socks


def socket_numbers(socks: list[socket.socket]) -> list[int]:
    """Return filenos, skipping a socket that is already closed."""
    numbers: list[int] = []
    for sock in socks:
        try:
            numbers.append(sock.fileno())
        except OSError:
            continue
    return numbers


def fd_numbers(values: object, singular: object) -> list[int]:
    """Return listener fds from a list, or one fd from the older singular field."""
    if isinstance(values, list):
        return [item for item in values if isinstance(item, int) and not isinstance(item, bool)]
    if isinstance(singular, int) and not isinstance(singular, bool):
        return [singular]
    return []


def snapshot(server: Server, clients: list[Client], reason: str) -> RebootState:
    """Copy telnet clients and the server counters that must survive a reboot."""
    listener = getattr(server, "listener", None)
    telnet_numbers = socket_numbers(listen_sockets(server, "listeners", "listener"))
    if not telnet_numbers and listener is not None:
        telnet_numbers = [listener.fileno()]
    telnet_fd = telnet_numbers[0] if telnet_numbers else getattr(server, "telnet_fd", None)
    ssh_numbers = socket_numbers(listen_sockets(server, "ssh_listen_socks", "ssh_listen_sock"))
    ssh_fd = ssh_numbers[0] if ssh_numbers else getattr(server, "ssh_fd", None)
    reboot_clients: list[RebootClient] = []
    for client in clients:
        if client.ssh:
            continue
        reboot_clients.append(client_snapshot(client, reason))
    return RebootState(
        started_at=server.started_at,
        timer_mark=server.timer_mark,
        timer_remainder=server.timer_remainder,
        net_count=server.net_count,
        shutdown_seconds=server.shutdown_seconds,
        shutdown_reason=server.shutdown_reason,
        allow_newbies=server.allow_newbies,
        allow_residents=server.allow_residents,
        mfa_steps=dict(server.mfa_steps),
        help_failed=server.help_failed,
        help_check_seconds=server.help_check_seconds,
        telnet_net=copy_net(server.telnet_net),
        ssh_net=copy_net(server.ssh_net),
        combined_net=copy_net(server.combined_net),
        telnet_fd=telnet_fd,
        ssh_fd=ssh_fd,
        reason=reason,
        clients=reboot_clients,
        telnet_fds=telnet_numbers,
        ssh_fds=ssh_numbers,
        chains=[Chain(chain.number, list(chain.names), chain.last_message_at) for chain in server.chains],
    )


def write_reboot(directory: Path, state: RebootState) -> None:
    """Write ``state`` as ``reboot.json`` with mode ``0600``."""
    path = reboot_path(directory)
    payload = encode_state(state)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    descriptor, name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_reboot(path: Path) -> RebootState:
    """Load a reboot snapshot from ``path``."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("reboot.json must be a JSON object.")
    return decode_state(raw)


def client_fileno(sock: socket.socket) -> int:
    """Return the socket number, or -1 when the socket is closed."""
    try:
        return sock.fileno()
    except OSError:
        return -1


def client_fileno_for(client: Client) -> int:
    """Return the client's socket number, or -1 when it has none."""
    sock = getattr(client, "sock", None)
    if sock is None:
        return -1
    return client_fileno(sock)


def client_byte_field(client: Client, name: str) -> bytes:
    """Return one byte buffer from a client, or empty bytes when it is missing."""
    return bytes(getattr(client, name, b""))


def reboot_client_for_live(state: RebootState, live: Client) -> RebootClient | None:
    """Return the snapshot row for this live connection."""
    sock = getattr(live, "sock", None)
    if sock is None:
        return None
    try:
        fileno = sock.fileno()
    except OSError:
        return None
    for entry in state.clients:
        if entry.fileno == fileno:
            return entry
    return None


def refresh_reboot_clients(server: Server, state: RebootState) -> None:
    """Copy telnet buffers from the live connections into the snapshot."""
    for live in get_clients(server):
        if live.ssh:
            continue
        entry = reboot_client_for_live(state, live)
        if entry is None:
            continue
        entry.buffer = client_byte_field(live, "buffer")
        entry.outgoing = client_byte_field(live, "outgoing")
        entry.iac = client_byte_field(live, "iac")
        entry.saw_cr = bool(getattr(live, "saw_cr", False))
        entry.hide_input = bool(getattr(live, "hide_input", False))


def copy_net(net: NetStats) -> NetStats:
    """Return a copy of every counter on a network stats object."""
    return NetStats(**{name: getattr(net, name) for name in NET_FIELDS})


def auth_step_is_stored(client: Client) -> bool:
    """Return whether this login step can be written into the reboot file."""
    if client.auth not in KNOWN_AUTH:
        return False
    pending = client.pending_saved
    return pending is None or isinstance(pending, SavedPlayer)


def client_snapshot(client: Client, reason: str) -> RebootClient:
    """Copy one telnet client, clearing a step the file cannot store."""
    player = client.player
    if not auth_step_is_stored(client):
        return RebootClient(
            fileno=client_fileno_for(client),
            buffer=client_byte_field(client, "buffer"),
            outgoing=client_byte_field(client, "outgoing"),
            iac=client_byte_field(client, "iac"),
            player=copy_player(player) if player is not None else None,
            auth="name",
            pending_name="",
            pending_saved=None,
            pending_password="",
            pending_current_password="",
            pending_mfa_secret="",
            pending_issuer="",
            password_failures=0,
            address=client.address,
            hostname=client.hostname,
            country_name=client.country_name,
            country_code=client.country_code,
            accepted_at=client.accepted_at,
            terminal=bool(getattr(client, "terminal", False)),
            hide_input=bool(getattr(client, "hide_input", False)),
            discovered_columns=client.discovered_columns,
            discovered_rows=client.discovered_rows,
            frame_columns=getattr(client, "frame_columns", None),
            saw_cr=bool(getattr(client, "saw_cr", False)),
            restart_notice=restart_line(reason),
            net=copy_net(client.net),
            welcome_pending=bool(getattr(client, "welcome_pending", False)),
        )
    pending = client.pending_saved
    return RebootClient(
        fileno=client_fileno_for(client),
        buffer=client_byte_field(client, "buffer"),
        outgoing=client_byte_field(client, "outgoing"),
        iac=client_byte_field(client, "iac"),
        player=copy_player(player) if player is not None else None,
        auth=client.auth,
        pending_name=client.pending_name,
        pending_saved=copy_saved(pending) if pending is not None else None,
        pending_password=client.pending_password,
        pending_current_password=client.pending_current_password,
        pending_mfa_secret=client.pending_mfa_secret,
        pending_issuer=client.pending_issuer,
        password_failures=client.password_failures,
        address=client.address,
        hostname=client.hostname,
        country_name=client.country_name,
        country_code=client.country_code,
        accepted_at=client.accepted_at,
        terminal=bool(getattr(client, "terminal", False)),
        hide_input=bool(getattr(client, "hide_input", False)),
        discovered_columns=client.discovered_columns,
        discovered_rows=client.discovered_rows,
        frame_columns=getattr(client, "frame_columns", None),
        saw_cr=bool(getattr(client, "saw_cr", False)),
        restart_notice="",
        net=copy_net(client.net),
        welcome_pending=bool(getattr(client, "welcome_pending", False)),
    )


def copy_player(player: Player) -> Player:
    """Return a copy of a live player."""
    return Player(
        player.name,
        player.room,
        player.connected_at,
        player.alarm_seconds,
        player.idle_seconds,
        player.prompt,
        player.rank,
        player.connected_seconds,
        player.flushed_seconds,
        player.lowercase_name,
        player.colour,
        player.description,
        player.status,
        player.auto_reply,
        player.entermsg,
        player.loginmsg,
        player.logoutmsg,
        player.plan,
        player.country_name,
        player.country_code,
        player.off_duty,
        player.off_list,
        player.screen_locked,
        player.screen_lock_reason,
        player.roles,
    )


def copy_saved(saved: SavedPlayer) -> SavedPlayer:
    """Return a copy of a saved player."""
    return SavedPlayer(
        saved.name,
        saved.rank,
        saved.password_hash,
        saved.logged_out_at,
        saved.ip_address,
        saved.hostname,
        saved.connected_seconds,
        saved.prompt,
        saved.lowercase_name,
        saved.colour,
        saved.description,
        saved.mfa_secret,
        saved.mfa_on,
        saved.entermsg,
        saved.loginmsg,
        saved.logoutmsg,
        saved.plan,
        saved.country_name,
        saved.country_code,
        roles=saved.roles,
        unknown_rank=saved.unknown_rank,
    )


def encode_bytes(value: bytes) -> str:
    """Return bytes as ASCII base64."""
    return base64.b64encode(value).decode("ascii")


def decode_bytes(value: str) -> bytes:
    """Return bytes from an ASCII base64 string."""
    return base64.b64decode(value.encode("ascii"))


def encode_datetime(value: datetime | None) -> str | None:
    """Return an ISO timestamp, or None."""
    if value is None:
        return None
    return value.isoformat()


def decode_datetime(value: str | None) -> datetime | None:
    """Return a datetime from an ISO timestamp, or None."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


def encode_net(net: NetStats) -> dict[str, int]:
    """Return the network counters as a dict."""
    return {name: getattr(net, name) for name in NET_FIELDS}


def decode_net(raw: dict[str, Any]) -> NetStats:
    """Return network counters from a dict."""
    return NetStats(**{name: int(raw[name]) for name in NET_FIELDS})


def encode_help_stamp(stamp: HelpStamp | None) -> dict[str, int] | None:
    """Return a help stamp as a dict, or None."""
    if stamp is None:
        return None
    return {"mtime_ns": stamp.mtime_ns, "size": stamp.size}


def decode_help_stamp(raw: dict[str, Any] | None) -> HelpStamp | None:
    """Return a help stamp from a dict, or None."""
    if raw is None:
        return None
    return HelpStamp(int(raw["mtime_ns"]), int(raw["size"]))


def encode_player(player: Player) -> dict[str, Any]:
    """Return a live player as a dict."""
    return {
        "name": player.name,
        "room": player.room,
        "connected_at": encode_datetime(player.connected_at),
        "alarm_seconds": player.alarm_seconds,
        "idle_seconds": player.idle_seconds,
        "prompt": player.prompt,
        "rank": player.rank.value,
        "connected_seconds": player.connected_seconds,
        "flushed_seconds": player.flushed_seconds,
        "lowercase_name": player.lowercase_name,
        "colour": player.colour,
        "description": player.description,
        "status": player.status,
        "auto_reply": player.auto_reply,
        "entermsg": player.entermsg,
        "loginmsg": player.loginmsg,
        "logoutmsg": player.logoutmsg,
        "plan": player.plan,
        "country_name": player.country_name,
        "country_code": player.country_code,
        "off_duty": player.off_duty,
        "off_list": player.off_list,
        "screen_locked": player.screen_locked,
        "screen_lock_reason": player.screen_lock_reason,
        "roles": player.roles,
    }


def decode_player(raw: dict[str, Any]) -> Player:
    """Return a live player from a dict."""
    connected_at = decode_datetime(raw["connected_at"])
    if connected_at is None:
        raise ValueError("player connected_at is required.")
    return Player(
        str(raw["name"]),
        str(raw["room"]),
        connected_at,
        raw.get("alarm_seconds"),
        int(raw.get("idle_seconds", 0)),
        str(raw.get("prompt", DEFAULT_PROMPT)),
        live_rank(str(raw.get("rank", "newbie"))),
        int(raw.get("connected_seconds", 0)),
        int(raw.get("flushed_seconds", 0)),
        str(raw.get("lowercase_name", "")),
        bool(raw.get("colour", True)),
        str(raw.get("description", "")),
        str(raw.get("status", "")),
        str(raw.get("auto_reply", "")),
        str(raw.get("entermsg", DEFAULT_ENTERMSG)),
        str(raw.get("loginmsg", DEFAULT_LOGINMSG)),
        str(raw.get("logoutmsg", DEFAULT_LOGOUTMSG)),
        str(raw.get("plan", "")),
        str(raw.get("country_name", "")),
        str(raw.get("country_code", "")),
        bool(raw.get("off_duty", False)),
        bool(raw.get("off_list", False)),
        bool(raw.get("screen_locked", False)),
        str(raw.get("screen_lock_reason", "")),
        int(raw.get("roles", 0)),
    )


def encode_saved(saved: SavedPlayer) -> dict[str, Any]:
    """Return a saved player as a dict."""
    return {
        "name": saved.name,
        "rank": saved.unknown_rank if saved.unknown_rank is not None else saved.rank.value,
        "password_hash": saved.password_hash,
        "logged_out_at": encode_datetime(saved.logged_out_at),
        "ip_address": saved.ip_address,
        "hostname": saved.hostname,
        "connected_seconds": saved.connected_seconds,
        "prompt": saved.prompt,
        "lowercase_name": saved.lowercase_name,
        "colour": saved.colour,
        "description": saved.description,
        "mfa_secret": saved.mfa_secret,
        "mfa_on": saved.mfa_on,
        "entermsg": saved.entermsg,
        "loginmsg": saved.loginmsg,
        "logoutmsg": saved.logoutmsg,
        "plan": saved.plan,
        "country_name": saved.country_name,
        "country_code": saved.country_code,
        "roles": saved.roles,
    }


def decode_saved(raw: dict[str, Any]) -> SavedPlayer:
    """Return a saved player from a dict."""
    rank, unknown = saved_rank(str(raw["rank"]))
    return SavedPlayer(
        str(raw["name"]),
        rank,
        str(raw["password_hash"]),
        decode_datetime(raw.get("logged_out_at")),
        raw.get("ip_address"),
        str(raw.get("hostname", "")),
        int(raw.get("connected_seconds", 0)),
        raw.get("prompt"),
        str(raw.get("lowercase_name", "")),
        bool(raw.get("colour", True)),
        str(raw.get("description", "")),
        raw.get("mfa_secret"),
        bool(raw.get("mfa_on", False)),
        raw.get("entermsg"),
        raw.get("loginmsg"),
        raw.get("logoutmsg"),
        str(raw.get("plan", "")),
        str(raw.get("country_name", "")),
        str(raw.get("country_code", "")),
        roles=int(raw.get("roles", 0)),
        unknown_rank=unknown,
    )


def encode_client(client: RebootClient) -> dict[str, Any]:
    """Return one reboot client as a dict."""
    return {
        "fileno": client.fileno,
        "buffer": encode_bytes(client.buffer),
        "outgoing": encode_bytes(client.outgoing),
        "iac": encode_bytes(client.iac),
        "player": encode_player(client.player) if client.player is not None else None,
        "auth": client.auth,
        "pending_name": client.pending_name,
        "pending_saved": encode_saved(client.pending_saved) if client.pending_saved is not None else None,
        "pending_password": client.pending_password,
        "pending_current_password": client.pending_current_password,
        "pending_mfa_secret": client.pending_mfa_secret,
        "pending_issuer": client.pending_issuer,
        "password_failures": client.password_failures,
        "address": client.address,
        "hostname": client.hostname,
        "country_name": client.country_name,
        "country_code": client.country_code,
        "accepted_at": client.accepted_at,
        "terminal": client.terminal,
        "hide_input": client.hide_input,
        "discovered_columns": client.discovered_columns,
        "discovered_rows": client.discovered_rows,
        "frame_columns": client.frame_columns,
        "saw_cr": client.saw_cr,
        "restart_notice": client.restart_notice,
        "net": encode_net(client.net),
        "welcome_pending": client.welcome_pending,
    }


def decode_client(raw: dict[str, Any]) -> RebootClient:
    """Return one reboot client from a dict."""
    player_raw = raw.get("player")
    pending_raw = raw.get("pending_saved")
    return RebootClient(
        int(raw["fileno"]),
        decode_bytes(str(raw.get("buffer", ""))),
        decode_bytes(str(raw.get("outgoing", ""))),
        decode_bytes(str(raw.get("iac", ""))),
        decode_player(player_raw) if isinstance(player_raw, dict) else None,
        str(raw.get("auth", "name")),
        str(raw.get("pending_name", "")),
        decode_saved(pending_raw) if isinstance(pending_raw, dict) else None,
        str(raw.get("pending_password", "")),
        str(raw.get("pending_current_password", "")),
        str(raw.get("pending_mfa_secret", "")),
        str(raw.get("pending_issuer", "")),
        int(raw.get("password_failures", 0)),
        str(raw.get("address", "unknown")),
        str(raw.get("hostname", "")),
        float(raw.get("accepted_at", 0.0)),
        bool(raw.get("terminal", False)),
        bool(raw.get("hide_input", False)),
        raw.get("discovered_columns"),
        raw.get("discovered_rows"),
        raw.get("frame_columns"),
        bool(raw.get("saw_cr", False)),
        str(raw.get("restart_notice", "")),
        decode_net(raw["net"]),
        str(raw.get("country_name", "")),
        str(raw.get("country_code", "")),
        bool(raw.get("welcome_pending", False)),
    )


def encode_state(state: RebootState) -> dict[str, Any]:
    """Return a reboot snapshot as a dict."""
    return {
        "allow_newbies": state.allow_newbies,
        "allow_residents": state.allow_residents,
        "chains": [{"last_message_at": encode_datetime(chain.last_message_at), "names": list(chain.names), "number": chain.number} for chain in state.chains],
        "clients": [encode_client(client) for client in state.clients],
        "combined_net": encode_net(state.combined_net),
        "help_check_seconds": state.help_check_seconds,
        "help_failed": encode_help_stamp(state.help_failed),
        "mfa_steps": state.mfa_steps,
        "net_count": state.net_count,
        "reason": state.reason,
        "shutdown_reason": state.shutdown_reason,
        "shutdown_seconds": state.shutdown_seconds,
        "ssh_fd": state.ssh_fd,
        "ssh_fds": state.ssh_fds,
        "ssh_net": encode_net(state.ssh_net),
        "started_at": encode_datetime(state.started_at),
        "telnet_fd": state.telnet_fd,
        "telnet_fds": state.telnet_fds,
        "telnet_net": encode_net(state.telnet_net),
        "timer_mark": state.timer_mark,
        "timer_remainder": state.timer_remainder,
    }


def decode_state(raw: dict[str, Any]) -> RebootState:
    """Return a reboot snapshot from a dict."""
    started_at = decode_datetime(raw.get("started_at"))
    if started_at is None:
        raise ValueError("reboot started_at is required.")
    clients_raw = raw.get("clients", [])
    clients = [decode_client(item) for item in clients_raw if isinstance(item, dict)]
    return RebootState(
        started_at,
        raw.get("timer_mark"),
        float(raw.get("timer_remainder", 0.0)),
        int(raw.get("net_count", 0)),
        raw.get("shutdown_seconds"),
        str(raw.get("shutdown_reason", "")),
        bool(raw.get("allow_newbies", True)),
        bool(raw.get("allow_residents", True)),
        {str(key): int(value) for key, value in dict(raw.get("mfa_steps", {})).items()},
        decode_help_stamp(raw.get("help_failed") if isinstance(raw.get("help_failed"), dict) else None),
        int(raw.get("help_check_seconds", 0)),
        decode_net(raw["telnet_net"]),
        decode_net(raw["ssh_net"]),
        decode_net(raw["combined_net"]),
        raw.get("telnet_fd"),
        raw.get("ssh_fd"),
        str(raw.get("reason", "")),
        clients,
        fd_numbers(raw.get("telnet_fds"), raw.get("telnet_fd")),
        fd_numbers(raw.get("ssh_fds"), raw.get("ssh_fd")),
        decode_chains(raw.get("chains")),
    )


def decode_chains(raw: object) -> list[Chain]:
    """Return chains from a snapshot. A missing list is empty."""
    if not isinstance(raw, list):
        return []
    chains: list[Chain] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        when = decode_datetime(item.get("last_message_at"))
        if when is None or not isinstance(item.get("number"), int):
            continue
        names = item.get("names", [])
        if not isinstance(names, list):
            continue
        chains.append(Chain(int(item["number"]), [str(name) for name in names], when))
    return chains
