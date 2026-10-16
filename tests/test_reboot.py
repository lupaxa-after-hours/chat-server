"""Reboot snapshot round trip."""

from __future__ import annotations  # noqa: I001

import contextlib
import socket
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from test_chatserver import TEST_HELP, free_port, layout, make_config, player_dir
from test_commands import FakePlayer, FakeServer, route_scans_to_the_host_fake  # noqa: F401

from lupaxa.chat_server.chatserver import ChatServer, bind_listener
from lupaxa.chat_server.client import Client
from lupaxa.chat_server.help import HelpStamp
from lupaxa.chat_server.parse import process_commands
from lupaxa.chat_server.player import Player
from lupaxa.chat_server.client import output_hidden
from lupaxa.chat_server.netstats import NetStats
from lupaxa.chat_server.chain import Chain
from lupaxa.chat_server.colour import cyan
from lupaxa.chat_server.reboot import COMPLETED, RebootClient, announce_completed, clients_from_state, decode_state, discard_leftover, encode_state, finish_reboot, read_reboot, reboot_path, snapshot, write_reboot
from lupaxa.chat_server.saved_player import SavedPlayer, hash_password, save_player
from lupaxa.chat_server.ranks import Rank
from lupaxa.chat_server.roles import Roles

# ruff: noqa: PT018, S105, S106


@dataclass
class FinishRebootServer:
    roster_dir: Path
    clients: dict[socket.socket, Client]
    listener: socket.socket | None = None
    ssh_listen_sock: socket.socket | None = None
    handoff: list | None = None
    sent: list[tuple[Client, str]] = field(default_factory=list)
    dropped: list[Client] = field(default_factory=list)

    def send(self, client: Client, text: str) -> bool:
        if output_hidden(client):
            return True
        self.sent.append((client, text))
        return True

    def flush(self, client: Client) -> bool:
        return True

    def drop(self, client: Client, *, announce: bool) -> None:
        del announce
        self.dropped.append(client)


@pytest.fixture(autouse=True)
def noop_replace_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lupaxa.chat_server.reboot.replace_process", lambda _argv: None)


def test_snapshot_records_listener_and_ssh_listen_fds() -> None:
    listener = socket.socket()
    ssh_listen = socket.socket()
    server = FakeServer()
    server.listener = listener
    server.ssh_listen_sock = ssh_listen
    state = snapshot(server, [], "")
    assert state.telnet_fd == listener.fileno()
    assert state.ssh_fd == ssh_listen.fileno()
    listener.close()
    ssh_listen.close()


def test_finish_reboot_rewrites_outgoing_after_ssh_drop(tmp_path: Path) -> None:
    sock_r, sock_w = socket.socketpair()
    player = Player("Ada", "reception", datetime.now(timezone.utc))
    client = Client(sock_r, player=player, auth="name")
    server = FinishRebootServer(roster_dir=tmp_path, clients={sock_r: client})
    state = snapshot(FakeServer(roster_dir=tmp_path), [client], "")
    write_reboot(tmp_path, state)
    client.outgoing.extend(b"later-bytes")
    finish_reboot(server, client, state, replace=lambda _argv: None)
    loaded = read_reboot(reboot_path(tmp_path))
    assert loaded.clients[0].outgoing == b"later-bytes"
    sock_r.close()
    sock_w.close()


def test_snapshot_round_trips_live_state(tmp_path: Path) -> None:
    started = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    connected = datetime(2026, 10, 15, 8, 1, tzinfo=timezone.utc)
    logged_out = datetime(2026, 10, 15, 7, 0, tzinfo=timezone.utc)
    player = Player("Ada", "reception", connected, alarm_seconds=12, idle_seconds=3, prompt="Yes? ", rank=Rank.RESIDENT, connected_seconds=9, flushed_seconds=4, colour=False, description="Here", status="Away", auto_reply="Later", entermsg="arrives")
    saved = SavedPlayer("Ada", Rank.RESIDENT, "hash", logged_out_at=logged_out, ip_address="127.0.0.1", hostname="localhost", connected_seconds=9, prompt="Yes? ", colour=False, description="Here", mfa_secret="secret", mfa_on=True, entermsg="arrives")  # noqa: E501
    client = Client(socket.socket(), player=player, auth="renew", pending_name="Ada", pending_saved=saved, pending_password="new", pending_current_password="old", pending_mfa_secret="sec", pending_issuer="chat", password_failures=1, address="127.0.0.1", hostname="localhost", accepted_at=10.5, terminal=True, hide_input=True, discovered_columns=80, discovered_rows=24, frame_columns=72)  # fmt: skip  # noqa: E501
    client.buffer.extend(b"hel")
    client.outgoing.extend(b"out")
    client.iac.extend(b"\xff")
    client.saw_cr = True
    client.net.in_total = 5
    client.net.out_pack_current = 2
    server = FakeServer(started_at=started, timer_mark=1.5, timer_remainder=0.25, net_count=4, shutdown_seconds=30, shutdown_reason="maintenance", allow_newbies=False, allow_residents=True, mfa_steps={"ada": 8}, help_failed=HelpStamp(3, 4), help_check_seconds=2, roster_dir=tmp_path)  # fmt: skip  # noqa: E501
    server.telnet_net.in_bps = 11
    server.ssh_net.out_total = 12
    server.combined_net.in_average = 13
    client.sock.close()
    state = snapshot(server, [client], "")
    state.clients[0].fileno = 7
    state.telnet_fd = 4
    state.ssh_fd = 5
    write_reboot(tmp_path, state)
    path = reboot_path(tmp_path)
    assert path.name == "reboot.json"
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = read_reboot(path)
    assert loaded.telnet_fd == 4 and loaded.ssh_fd == 5
    assert loaded.started_at == started
    assert loaded.allow_newbies is False
    assert loaded.mfa_steps == {"ada": 8}
    assert loaded.shutdown_seconds == 30 and loaded.shutdown_reason == "maintenance"
    assert loaded.timer_mark == 1.5 and loaded.timer_remainder == 0.25
    assert loaded.net_count == 4
    assert loaded.help_failed == HelpStamp(3, 4) and loaded.help_check_seconds == 2
    assert loaded.telnet_net.in_bps == 11
    assert loaded.ssh_net.out_total == 12
    assert loaded.combined_net.in_average == 13
    one = loaded.clients[0]
    assert one.fileno == 7
    assert one.buffer == b"hel" and one.outgoing == b"out" and one.iac == b"\xff"
    assert one.saw_cr is True and one.auth == "renew"
    assert one.pending_password == "new" and one.pending_current_password == "old"
    assert one.pending_mfa_secret == "sec" and one.pending_issuer == "chat"
    assert one.password_failures == 1 and one.hide_input is True
    assert one.player is not None and one.player.status == "Away" and one.player.auto_reply == "Later"
    assert one.player.alarm_seconds == 12 and one.player.idle_seconds == 3
    assert one.net.in_total == 5 and one.net.out_pack_current == 2
    assert one.pending_saved is not None and one.pending_saved.mfa_secret == "secret"
    raw = path.read_text()
    assert "help.yml" not in raw and "rooms.yml" not in raw


def test_an_unknown_step_is_cleared_only_in_the_file() -> None:
    player = Player("Ada", "reception", datetime(2026, 10, 15, tzinfo=timezone.utc))
    client = Client(socket.socket(), player=player, auth="mystery", pending_password="secret")
    server = FakeServer()
    state = snapshot(server, [client], "maintenance")
    client.sock.close()
    assert client.auth == "mystery"
    assert client.pending_password == "secret"
    one = state.clients[0]
    assert one.auth == "name"
    assert one.pending_password == ""
    assert one.player is not None and one.player.room == "reception"
    assert one.restart_notice == "Rebooting now for 'maintenance'. You will need to start this again after the reboot.\n"


def test_a_normal_start_deletes_a_leftover_file(tmp_path: Path) -> None:
    path = reboot_path(tmp_path)
    path.write_text("{}", encoding="utf-8")
    discard_leftover(tmp_path)
    assert not path.exists()


def test_a_dead_fd_is_skipped_and_the_other_client_returns() -> None:
    live_r, live_w = socket.socketpair()
    state = snapshot(FakeServer(), [], "")
    good = RebootClient(fileno=live_r.fileno(), buffer=b"", outgoing=b"hi", player=Player("Ada", "reception", datetime.now(timezone.utc)), auth="name", pending_name="", pending_saved=None, pending_password="", pending_current_password="", pending_mfa_secret="", pending_issuer="", password_failures=0, address="127.0.0.1", hostname="localhost", accepted_at=1.0, terminal=False, hide_input=False, iac=b"", saw_cr=False, discovered_columns=None, discovered_rows=None, frame_columns=None, net=NetStats(), restart_notice="")  # fmt: skip  # noqa: E501
    dead = replace(good, fileno=99999, player=Player("Bea", "hall", datetime.now(timezone.utc)))
    state.clients = [good, dead]
    restored = clients_from_state(state)
    assert [client.name for client in restored] == ["Ada"]
    assert restored[0].outgoing == b"hi"
    live_r.close()
    live_w.close()


@pytest.mark.usefixtures("route_scans_to_the_host_fake")
def test_reboot_completed_announces_on_the_staff_channel() -> None:
    """The completed line is a staff line."""
    server = FakeServer()
    ada = FakePlayer(name="Ada")
    assert ada.player is not None
    ada.player.rank = Rank.CODER
    ada.player.colour = False
    server.people = [ada]
    announce_completed(server)
    assert server.staff_lines == ["< SU > Reboot completed.\n"]
    assert server.admin_lines == []


@pytest.mark.usefixtures("route_scans_to_the_host_fake")
def test_an_off_duty_staff_member_misses_the_reboot_line() -> None:
    """Off duty skips the staff channel, and colour paints the line cyan."""
    server = FakeServer()
    ada = FakePlayer(name="Ada")
    assert ada.player is not None
    ada.player.rank = Rank.SU_CHANNEL
    ada.player.off_duty = True
    ada.player.colour = False
    bea = FakePlayer(name="Bea")
    assert bea.player is not None
    bea.player.rank = Rank.ADMIN
    bea.player.colour = True
    cyd = FakePlayer(name="Cyd")
    assert cyd.player is not None
    cyd.player.rank = Rank.SU_CHANNEL
    cyd.player.colour = False
    server.people = [ada, bea, cyd]
    announce_completed(server)
    assert ada.sent == []
    assert bea.sent == [cyan("< SU > Reboot completed.") + "\n"]
    assert cyd.sent == ["< SU > Reboot completed.\n"]
    assert server.admin_lines == []


def test_an_unknown_step_with_no_room_returns_to_the_name_prompt() -> None:
    client = Client(socket.socket(), player=None, auth="mystery", pending_name="Bea")
    state = snapshot(FakeServer(), [client], "")
    client.sock.close()
    one = state.clients[0]
    assert one.player is None and one.auth == "name" and one.pending_name == ""
    assert one.restart_notice == "Rebooting now! You will need to start this again after the reboot.\n"


@pytest.mark.usefixtures("route_scans_to_the_host_fake")
def test_reboot_without_telnet_tells_the_admin_to_shut_down(tmp_path: Path) -> None:
    server = FakeServer()
    server.config = replace(server.config, port=None, ssh_port=2022, players=tmp_path)
    server.roster_dir = tmp_path
    ada = FakePlayer(name="Ada", ssh=True)
    ada.player.rank = Rank.CODER
    server.people = [ada]
    process_commands(server, ada, "reboot")  # type: ignore[arg-type]
    assert ada.sent == ["No telnet port is open. Use shutdown.\n"]
    assert server.admin_lines == []
    assert server.dropped_clients == []
    assert not reboot_path(tmp_path).exists()


@pytest.mark.usefixtures("route_scans_to_the_host_fake")
def test_reboot_without_reason_warns_ssh_and_announces_admin(tmp_path: Path) -> None:
    server = FakeServer(roster_dir=tmp_path)
    server.config = replace(server.config, players=tmp_path)
    ada = FakePlayer(name="Ada")
    ada.player.rank = Rank.CODER
    ada.player.colour = False
    bea = FakePlayer(name="Bea", ssh=True, player=Player("Bea", "reception", datetime.now(timezone.utc)))
    server.people = [ada, bea]
    process_commands(server, ada, "reboot")  # type: ignore[arg-type]
    assert "< SU > Ada has initiated a reboot.\n" in server.staff_lines
    assert server.admin_lines == []
    assert "Rebooting now!\n" in bea.sent
    assert reboot_path(tmp_path).exists()


@pytest.mark.usefixtures("route_scans_to_the_host_fake")
def test_reboot_announces_and_warns_ssh(tmp_path: Path) -> None:
    server = FakeServer(roster_dir=tmp_path)
    server.config = replace(server.config, players=tmp_path)
    ada = FakePlayer(name="Ada")
    ada.player.rank = Rank.CODER
    ada.player.colour = False
    bea = FakePlayer(name="Bea", ssh=True, player=Player("Bea", "reception", datetime.now(timezone.utc)))
    server.people = [ada, bea]
    process_commands(server, ada, "reboot maintenance")  # type: ignore[arg-type]
    assert "< SU > Ada has initiated a reboot for 'maintenance'.\n" in server.staff_lines
    assert server.admin_lines == []
    assert "Rebooting now for 'maintenance'.\n" in bea.sent
    assert "Rebooting now" not in "".join(ada.sent)
    assert reboot_path(tmp_path).exists()
    saved = read_reboot(reboot_path(tmp_path))
    assert [client.player.name for client in saved.clients if client.player is not None] == ["Ada"]


@pytest.mark.usefixtures("route_scans_to_the_host_fake")
def test_a_failing_first_write_reboot_leaves_everyone_connected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeServer(roster_dir=tmp_path)
    server.config = replace(server.config, players=tmp_path)
    ada = FakePlayer(name="Ada")
    ada.player.rank = Rank.CODER
    ada.player.colour = False
    server.people = [ada]

    def refuse(_directory: Path, _state: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("lupaxa.chat_server.reboot.write_reboot", refuse)
    process_commands(server, ada, "reboot")  # type: ignore[arg-type]
    assert "Cannot write the reboot file.\n" in ada.sent
    assert server.admin_lines == []
    assert server.dropped_clients == []
    assert not reboot_path(tmp_path).exists()


@pytest.mark.usefixtures("route_scans_to_the_host_fake")
def test_exec_failure_tells_telnet_players_to_carry_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeServer(roster_dir=tmp_path)
    server.config = replace(server.config, players=tmp_path)
    ada = FakePlayer(name="Ada")
    ada.player.rank = Rank.CODER
    ada.player.colour = False
    ada.auth = "mystery"
    ada.pending_password = "secret"
    ada.sock = socket.socket()
    cay = FakePlayer(name="Cay", ssh=True, player=Player("Cay", "hall", datetime.now(timezone.utc)))
    server.people = [ada, cay]

    def refuse(_argv: list[str]) -> None:
        raise OSError("exec failed")

    monkeypatch.setattr("lupaxa.chat_server.reboot.replace_process", refuse)
    process_commands(server, ada, "reboot")  # type: ignore[arg-type]
    assert cay in server.dropped_clients
    assert server.announced[-1] is True
    assert ada.auth == "mystery" and ada.pending_password == "secret"
    assert "The reboot did not happen. Carry on with what you were doing.\n" in ada.sent
    assert "< SU > Reboot failed.\n" in server.staff_lines
    assert server.admin_lines == []
    assert "Carry on" not in "".join(cay.sent)
    assert not reboot_path(tmp_path).exists()
    ada.sock.close()


def test_adopt_warns_every_connected_site_admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every restored site admin hears both issue lines, including a locked off-duty one."""
    players = player_dir(tmp_path)
    save_player(players, SavedPlayer("Fay", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(players, SavedPlayer("Guy", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    listener = bind_listener("127.0.0.1", free_port())
    now = datetime.now(timezone.utc)
    ada_left, ada_right = socket.socketpair()
    bea_left, bea_right = socket.socketpair()
    cyd_left, cyd_right = socket.socketpair()
    ada = Client(ada_left, player=Player("Ada", "reception", now, rank=Rank.RESIDENT, roles=int(Roles.SITE_ADMIN), screen_locked=True, off_duty=True))
    bea = Client(bea_left, player=Player("Bea", "reception", now, rank=Rank.RESIDENT, roles=int(Roles.SITE_ADMIN)))
    cyd = Client(cyd_left, player=Player("Cyd", "reception", now, rank=Rank.RESIDENT))
    state = snapshot(FakeServer(roster_dir=players), [ada, bea, cyd], "")
    state.telnet_fd = listener.fileno()
    write_reboot(players, state)
    order: list[str] = []

    def record_staff(_server: object, text: str, *, skip: object = None) -> None:
        del skip
        order.append(text)

    original_send = ChatServer.send

    def record_issue(self: ChatServer, client: Client, text: str) -> bool:
        if text.startswith("[ Issue Located ]"):
            order.append(text)
        return original_send(self, client, text)

    monkeypatch.setattr("lupaxa.chat_server.reboot.broadcast_staff", record_staff)
    monkeypatch.setattr(ChatServer, "send", record_issue)
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    server.config = replace(server.config, obsolete_hot=("admin", "colour"))
    keys = "Obsolete keys found in hot.yml: admin & colour."
    admins = "There are multi super-admins 'Fay & Guy'"
    peers = {"Ada": ada_right, "Bea": bea_right, "Cyd": cyd_right}

    def taken(name: str) -> str:
        live = next(person for person in server.clients.values() if person.name == name)
        peer = peers[name]
        peer.setblocking(False)
        try:
            data = bytes(live.outgoing) + peer.recv(65536)
        except BlockingIOError:
            data = bytes(live.outgoing)
        return data.decode()

    try:
        server.adopt(read_reboot(reboot_path(players)))
        ada_text = taken("Ada")
        bea_text = taken("Bea")
        cyd_text = taken("Cyd")
        assert keys in ada_text and admins in ada_text
        assert keys in bea_text and admins in bea_text
        assert keys not in cyd_text and admins not in cyd_text
        completed_at = order.index(COMPLETED)
        for index, line in enumerate(order):
            if line.startswith("[ Issue Located ]"):
                assert completed_at < index
    finally:
        server.close()
        with contextlib.suppress(OSError):
            ada_left.close()
            ada_right.close()
            bea_left.close()
            bea_right.close()
            cyd_left.close()
            cyd_right.close()
            listener.close()


def test_adopt_registers_inherited_telnet_and_deletes_reboot_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    players = player_dir(tmp_path)
    listener = bind_listener("127.0.0.1", free_port())
    started = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    server_stub = FakeServer(started_at=started, roster_dir=players)
    state = snapshot(server_stub, [], "")
    state.telnet_fd = listener.fileno()
    write_reboot(players, state)
    path = reboot_path(players)
    assert path.is_file()
    staff_lines: list[str] = []

    def record_staff(_server: object, text: str, *, skip: object = None) -> None:
        del skip
        staff_lines.append(text)

    monkeypatch.setattr("lupaxa.chat_server.reboot.broadcast_staff", record_staff)
    server = ChatServer(make_config(players=players), layout(tmp_path), TEST_HELP)
    try:
        server.adopt(read_reboot(path))
        assert server.started_at == started
        assert server.rebooted_at is not None
        assert server.rebooted_at != started
        assert "rebooted_at" not in encode_state(state)
        assert server.listener is not None
        assert not path.exists()
        assert staff_lines == ["< SU > Reboot completed.\n"]
    finally:
        server.close()
        with contextlib.suppress(OSError):
            listener.close()


def test_adopt_restores_a_home_room(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A player restored at home has that room in the layout."""
    players = player_dir(tmp_path)
    listener = bind_listener("127.0.0.1", free_port())
    left, right = socket.socketpair()
    player = Player("Ada", "home_ada", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    client = Client(left, player=player)
    server_stub = FakeServer(roster_dir=players)
    state = snapshot(server_stub, [client], "")
    state.telnet_fd = listener.fileno()
    write_reboot(players, state)
    loaded = read_reboot(reboot_path(players))
    assert loaded.clients[0].player is not None
    assert loaded.clients[0].player.room == "home_ada"
    monkeypatch.setattr("lupaxa.chat_server.reboot.broadcast_staff", lambda *_args, **_kwargs: None)
    office = layout(tmp_path)
    config = replace(make_config(players=players), rooms=tmp_path / "rooms.yml")
    server = ChatServer(config, office, TEST_HELP)
    try:
        server.adopt(loaded)
        assert "ada_home" in server.layout.rooms
        assert server.layout.rooms["ada_home"].description == "You are home."
        assert server.layout.display_name("ada_home") == "Home"
    finally:
        server.close()
        with contextlib.suppress(OSError):
            left.close()
            right.close()
            listener.close()


def test_adopt_puts_a_missing_room_in_the_void(tmp_path: Path) -> None:
    """A restored player whose room is gone stands in The Void."""
    players = player_dir(tmp_path)
    listener = bind_listener("127.0.0.1", free_port())
    left, right = socket.socketpair()
    player = Player("Ada", "kitchen", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    client = Client(left, player=player)
    server_stub = FakeServer(roster_dir=players)
    state = snapshot(server_stub, [client], "")
    state.telnet_fd = listener.fileno()
    write_reboot(players, state)
    office = "reception:\n  description: The front desk.\n  max_occupancy: 2\n  exits: {}\n"
    server = ChatServer(make_config(players=players), layout(tmp_path, office).with_void(), TEST_HELP)
    try:
        server.adopt(read_reboot(reboot_path(players)))
        live = next(iter(server.clients.values()))
        player = live.player
        assert player is not None
        assert player.room == "void"
        right.setblocking(False)
        try:
            heard = bytes(live.outgoing) + right.recv(65536)
        except BlockingIOError:
            heard = bytes(live.outgoing)
        text = heard.decode()
        assert "The room you were in has gone.\n" in text
        assert "You are in The Void." in text
    finally:
        server.close()
        with contextlib.suppress(OSError):
            left.close()
            right.close()
            listener.close()


def test_reboot_snapshot_round_trips_chains(tmp_path: Path) -> None:
    """A chain's id, names, and last message survive the reboot file."""
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    server = FakeServer(roster_dir=tmp_path)
    server.chains = [Chain(4, ["Ada", "Bea", "Cyd"], when)]
    state = snapshot(server, [], "")
    write_reboot(tmp_path, state)
    loaded = read_reboot(reboot_path(tmp_path))
    assert len(loaded.chains) == 1
    assert loaded.chains[0].number == 4
    assert loaded.chains[0].names == ["Ada", "Bea", "Cyd"]
    assert loaded.chains[0].last_message_at == when


def test_old_reboot_file_has_no_chains() -> None:
    """A snapshot from before chains loads an empty list."""
    raw = encode_state(snapshot(FakeServer(), [], ""))
    del raw["chains"]
    loaded = decode_state(raw)
    assert loaded.chains == []
