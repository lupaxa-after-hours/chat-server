"""Commands registered at coder rank."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import psutil

from . import reboot as reboot_api
from .broadcast import broadcast_all, broadcast_coder
from .client import Client
from .colour import can_see_colour, paint_coder, red
from .commands import Command, register_subcommands, shown_syntax
from .globals import COMMANDS
from .help import load_help_into
from .netstats import network_box
from .player import is_logged_in, match_online_player
from .ranks import Rank
from .reboot import finish_reboot, prepare_reboot
from .roles import Roles
from .server import Server
from .time import clock_phrase, cpu_phrase, duration_phrase
from .timer import reload_hot_config, reload_messages, reload_rooms, shutdown_phrase
from .utilities import center_block, frame_width, get_clients, get_parts, get_player_name


def command_cu(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Speak on the coder channel."""
    coder_channel_wrapper(server, client, f"You say '{text}'", f"{get_player_name(client)} says '{text}'")


def command_ce(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Emote on the coder channel."""
    coder_channel_wrapper(server, client, f"You emote: {text}", f"{get_player_name(client)} {text}")


def command_ct(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Think on the coder channel."""
    coder_channel_wrapper(server, client, f"You think . o O ( {text} )", f"{get_player_name(client)} thinks . o O ( {text} )")


def coder_channel_wrapper(server: Server, client: Client, mine: str, theirs: str) -> None:
    """Send the sender ``mine`` and the rest of the coder channel ``theirs``."""
    if not server.send(client, paint_coder(client, f"{{ Coder }} {mine}\n")):
        return
    broadcast_coder(server, f"{{ Coder }} {theirs}\n", skip=client)


def command_broadcast(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Send a centred broadcast message to every player who is in a room."""
    message = get_parts(text)
    plain = f"-=> Broadcast Messages from {client.name}: {message} <=-"
    for person in list(get_clients(server)):
        if not is_logged_in(person):
            continue
        shown = red(plain) if can_see_colour(person) else plain
        width = frame_width(person.discovered_columns, person.frame_columns)
        padded = center_block(shown + "\n", width).removesuffix("\n")
        server.send(person, f"\n\n\n{padded}\n\n\n\n")


def command_reboot(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Write a reboot snapshot and warn connected clients."""
    reason = get_parts(text)
    state = prepare_reboot(server, client, reason)
    if state is None:
        return
    finish_reboot(server, client, state, replace=reboot_api.replace_process)


def command_reload(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Parent for reload. The dispatcher runs a child instead."""
    del server, client, command, text


def reload_extra(server: Server, client: Client, command: Command, text: str | None) -> bool:
    """Return whether text is an extra word, and send the syntax line."""
    if text is None or text.strip() == "":
        return False
    server.send(client, f"Syntax: {shown_syntax(command)}\n")
    return True


def command_reload_rooms(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Read the office and loaded houses again."""
    if reload_extra(server, client, command, text):
        return
    errors = reload_rooms(server, force=True)
    if errors == []:
        server.send(client, "Rooms reloaded.\n")
        return
    for error in errors:
        server.send(client, f"{error}\n")


def command_reload_help(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Read the help file again."""
    if reload_extra(server, client, command, text):
        return
    error = load_help_into(server)
    if error is None:
        server.send(client, "Help reloaded.\n")
        return
    server.send(client, f"Cannot reload help: {error}\n")


def command_reload_messages(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Read the three message files again."""
    if reload_extra(server, client, command, text):
        return
    errors = reload_messages(server, force=True)
    if errors == []:
        server.send(client, "Messages reloaded.\n")
        return
    for error in errors:
        server.send(client, f"{error}\n")


def command_reload_config(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Read the hot file again. The cold file stays as it was."""
    if reload_extra(server, client, command, text):
        return
    error, notice = reload_hot_config(server)
    if error is not None:
        server.send(client, f"Cannot reload config: {error}\n")
        return
    server.send(client, "Config reloaded.\n")
    if notice is not None:
        server.send(client, f"{notice}\n")


def command_reload_all(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Read the hot config, then rooms, then help, then the message files."""
    if reload_extra(server, client, command, text):
        return
    error, notice = reload_hot_config(server)
    if error is None:
        server.send(client, "Config reloaded.\n")
        if notice is not None:
            server.send(client, f"{notice}\n")
    else:
        server.send(client, f"Cannot reload config: {error}\n")
    errors = reload_rooms(server, force=True)
    if errors == []:
        server.send(client, "Rooms reloaded.\n")
    else:
        for room_error in errors:
            server.send(client, f"{room_error}\n")
    error = load_help_into(server)
    if error is None:
        server.send(client, "Help reloaded.\n")
    else:
        server.send(client, f"Cannot reload help: {error}\n")
    errors = reload_messages(server, force=True)
    if errors == []:
        server.send(client, "Messages reloaded.\n")
        return
    for error in errors:
        server.send(client, f"{error}\n")


def command_shutdown(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Start, replace, cancel, or finish the one server countdown."""
    parts = get_parts(text).split(None, 1)
    if len(parts) < 2 or parts[1].strip() == "":
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    word, reason = parts[0], parts[1].strip()
    if word == "now":
        server.shutdown_seconds = None
        server.shutdown_reason = ""
        broadcast_all(server, f"Shutting down now for '{reason}'.\n", skip=None)
        server.shutdown()
        return
    if word == "cancel":
        if server.shutdown_seconds is None:
            server.send(client, "No shutdown is running.\n")
            return
        server.shutdown_seconds = None
        server.shutdown_reason = ""
        broadcast_all(server, f"Shutdown cancelled for '{reason}'.\n", skip=None)
        return
    if not word.isascii() or not word.isdigit() or int(word) < 1:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    seconds = int(word)
    server.shutdown_seconds = seconds
    server.shutdown_reason = reason
    broadcast_all(server, shutdown_phrase(seconds, reason), skip=None)
    if seconds == 1:
        broadcast_all(server, "Shutting down now!\n", skip=None)


def mib_phrase(amount: int) -> str:
    """Return ``amount`` bytes as mebibytes with one decimal."""
    return f"{amount / (1024 * 1024):.1f} MiB"


def stat_box(groups: Sequence[Sequence[tuple[str, str]]]) -> str:
    """Return the snug stat box for these groups, ending with a newline."""
    width = max(len(label) for group in groups for label, _ in group)
    rows: list[str | None] = []
    for index, group in enumerate(groups):
        if index:
            rows.append(None)
        rows.extend(f" {label.ljust(width)} : {value}" for label, value in group)
    inner = max(len(row) for row in rows if row is not None) + 1
    border = "+" + ("-" * inner) + "+"
    rule = "|" + ("-" * inner) + "|"
    body = [rule if row is None else "|" + row.ljust(inner) + "|" for row in rows]
    return "\n".join((border, *body, border)) + "\n"


def system_box(server: Server) -> str:
    """Return the process box, ending with a newline."""
    process: Any = server.process
    missing = "unavailable"
    seconds = int((datetime.now(timezone.utc) - server.started_at).total_seconds())
    pid = cpu_usage = cpu_user = cpu_system = missing
    rss = uss = vms = threads = descriptors = sockets = missing
    disk_read = disk_written = voluntary = involuntary = missing
    try:
        with process.oneshot():
            pid = str(process.pid)
            cpu_usage = f"{process.cpu_percent(interval=None):.1f}%"
            times = process.cpu_times()
            cpu_user = cpu_phrase(times.user)
            cpu_system = cpu_phrase(times.system)
            try:
                memory = process.memory_info()
                rss = mib_phrase(memory.rss)
                vms = mib_phrase(memory.vms)
            except (psutil.Error, OSError):
                rss = vms = missing
            try:
                uss = mib_phrase(process.memory_full_info().uss)
            except (psutil.Error, OSError):
                uss = missing
            try:
                threads = str(process.num_threads())
            except (psutil.Error, OSError):
                threads = missing
            try:
                descriptors = str(process.num_fds())
            except (psutil.Error, OSError, AttributeError):
                descriptors = missing
            try:
                sockets = str(len(process.net_connections(kind="inet")))
            except (psutil.Error, OSError, AttributeError):
                sockets = missing
            try:
                disk = process.io_counters()
                disk_read = mib_phrase(disk.read_bytes)
                disk_written = mib_phrase(disk.write_bytes)
            except (psutil.Error, OSError, AttributeError):
                disk_read = disk_written = missing
            try:
                switches = process.num_ctx_switches()
                voluntary = str(switches.voluntary)
                involuntary = str(switches.involuntary)
            except (psutil.Error, OSError):
                voluntary = involuntary = missing
    except (psutil.Error, OSError):
        pass
    head = [("PID", pid), ("Uptime", duration_phrase(seconds)), ("Booted at", clock_phrase(server.started_at))]
    if server.rebooted_at is not None:
        head.append(("Rebooted at", clock_phrase(server.rebooted_at)))
    head.extend((("CPU usage", cpu_usage), ("CPU user time", cpu_user), ("CPU system time", cpu_system)))
    sessions = str(sum(1 for _ in get_clients(server)))
    return stat_box(
        [
            head,
            [("Memory RSS", rss), ("Memory USS", uss), ("Memory VMS", vms)],
            [("Threads", threads), ("File descriptors", descriptors), ("Total sockets", sockets), ("Sessions", sessions)],
            [("Disk read", disk_read), ("Disk written", disk_written)],
            [("Voluntary switches", voluntary), ("Involuntary switches", involuntary)],
        ]
    )


def command_system(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Show this process report: uptime, CPU, memory, threads, sockets, sessions, disk, and context switches."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    server.send(client, system_box(server))


def command_network(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Show combined, port, or one connection's counters."""
    parts = get_parts(text, split=True)
    if len(parts) > 1:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    if not parts:
        counter = server.combined_net
    elif parts[0].lower() == "telnet":
        counter = server.telnet_net
    elif parts[0].lower() == "ssh":
        counter = server.ssh_net
    elif parts[0].lower() == "me":
        counter = client.net
    else:
        found, error = match_online_player(get_clients(server), parts[0])
        if error is not None:
            server.send(client, error)
            return
        if found is None or found.player is None:
            server.send(client, f"Player {parts[0]} not found.\n")
            return
        counter = found.net
    server.send(client, network_box(counter))


COMMANDS.extend(
    (
        Command("cu", command_cu, False, "<message>", rank=Rank.CODER),
        Command("ce", command_ce, False, "<message>", rank=Rank.CODER),
        Command("ct", command_ct, False, "<message>", rank=Rank.CODER),
        Command("broadcast", command_broadcast, False, "<message>", rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or"),
        Command("shutdown", command_shutdown, False, "<seconds> <reason>", rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or"),
        Command("reboot", command_reboot, False, "[reason]", rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or"),
        Command("reload", command_reload, False, "<command>", rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or", subcommands=()),
        Command("network", command_network, False, "[telnet|ssh|<player>]", rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or"),
        Command("system", command_system, False, None, rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or"),
    )
)
register_subcommands(
    "reload",
    Command("all", command_reload_all, False, None, rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or"),
    Command("config", command_reload_config, False, None, rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or"),
    Command("help", command_reload_help, False, None, rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or"),
    Command("messages", command_reload_messages, False, None, rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or"),
    Command("rooms", command_reload_rooms, False, None, rank=Rank.CODER, role=int(Roles.SITE_ADMIN), combination="or"),
)
