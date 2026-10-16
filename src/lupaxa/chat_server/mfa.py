"""In-room MFA setup and removal."""

from __future__ import annotations

import time
from dataclasses import replace

from .broadcast import broadcast_admin
from .client import Client
from .commands import Command, shown_syntax
from .entry import player_prompt, restart_login
from .exceptions import ConfigError
from .globals import CANNOT_NOW, MAX_PASSWORD_FAILURES
from .mfa_seal import refresh_mfa_secret, seal_mfa_secret
from .password import code_prompt, current_password_prompt, mfa_enrolment_text
from .ranks import Rank, rank_allows
from .saved_player import SavedPlayer, has_set_mfa, password_ok, save_player
from .server import Server
from .totp import new_secret, record_mfa_step, verify_totp
from .utilities import find_player, find_saved, get_parts, get_player_name, get_saved_player

REMOVED = "Your MFA has been removed.\n"
REMOVED_WARNING = ("Your MFA has been removed. Set it up again before you log out. The next login will make you set it up before you can enter.\n")  # fmt: skip  # noqa: E501


def register_mfa(commands: list[Command]) -> None:
    """Append the mfa command when the server has MFA enabled."""
    from .commands import by_name

    if "mfa" in by_name():
        return
    entry = Command("mfa", command_mfa, False, "setup|mfa remove|mfa remove <name>", rank=Rank.RESIDENT)
    commands.append(entry)
    by_name()[entry.name] = entry


def command_mfa(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Start MFA setup, self-removal, or another player's removal."""
    parts = get_parts(text, split=True)
    if len(parts) > 2:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    if parts[0] == "setup":
        if len(parts) != 1:
            server.send(client, f"Syntax: {shown_syntax(command)}\n")
            return
        start_setup(server, client)
        return
    if parts[0] == "remove":
        if len(parts) == 1:
            start_self_remove(server, client)
            return
        if len(parts) == 2:
            target = parts[1]
            own = client.name
            if own is not None and target.lower() == own.lower():
                start_self_remove(server, client)
                return
            start_other_remove(server, client, target)
            return
    server.send(client, f"Syntax: {shown_syntax(command)}\n")


def start_setup(server: Server, client: Client) -> None:
    """Begin MFA enrolment for the player on ``client``."""
    user = client.player
    if user is None:
        return
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None:
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if has_set_mfa(saved):
        server.send(client, "MFA is already on.\n")
        return
    client.auth = "mfa_pw"
    client.password_failures = 0
    server.send(client, current_password_prompt())


def start_self_remove(server: Server, client: Client) -> None:
    """Ask this player for the password that starts MFA removal."""
    user = client.player
    if user is None:
        return
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if not has_set_mfa(saved):
        server.send(client, f"{user.name} has no MFA.\n")
        return
    client.auth = "mfa_remove_pw"
    client.password_failures = 0
    server.send(client, current_password_prompt())


def start_other_remove(server: Server, client: Client, name: str) -> None:
    """Ask an admin for the password that removes MFA from ``name``."""
    found, saved = get_saved_player(server, client, name)
    if not found or saved is None:
        return
    caller_rank = client.player.rank if client.player is not None else Rank.RESIDENT
    if rank_allows(saved.rank, caller_rank):
        server.send(client, f"You cannot remove MFA for {saved.name}.\n")
        return
    if not has_set_mfa(saved):
        server.send(client, f"{saved.name} has no MFA.\n")
        return
    client.auth = "mfa_sure"
    client.pending_name = saved.name
    client.password_failures = 0
    server.send(client, f"Are you sure you want to remove MFA for {saved.name}?\n")


def process_mfa_pw(server: Server, client: Client, line: str) -> None:
    """Check the password typed while MFA is being turned on."""
    if line.strip().lower() == "quit":
        restart_login(client)
        server.send(client, player_prompt(client))
        return
    user = client.player
    if user is None:
        restart_login(client)
        server.send(client, player_prompt(client))
        return
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None or not password_ok(line, saved.password_hash):
        restart_login(client)
        server.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    client.pending_password = line
    secret = new_secret()
    client.pending_mfa_secret = secret
    client.auth = "mfa_show"
    server.send(client, mfa_enrolment_text(server, client, user.name, secret))


def process_mfa_show(server: Server, client: Client, line: str) -> None:
    """Confirm the new MFA secret after the password matches."""
    if line.strip().lower() == "quit":
        restart_login(client)
        server.send(client, player_prompt(client))
        return
    user = client.player
    secret = client.pending_mfa_secret
    if user is None or secret == "":
        restart_login(client)
        server.send(client, player_prompt(client))
        return
    used = server.mfa_steps.get(user.name.lower())
    step = verify_totp(secret, line.strip(), now=int(time.time()), used=used)
    if step is None:
        client.password_failures += 1
        server.send(client, "Wrong code.\n")
        if client.password_failures >= MAX_PASSWORD_FAILURES:
            server.drop(client, announce=True)
            return
        server.send(client, code_prompt())
        return
    record_mfa_step(server.mfa_steps, user.name.lower(), step)
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None:
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    seal_password = client.pending_password
    try:
        save_player(server.roster_dir, replace(saved, mfa_secret=seal_mfa_secret(seal_password, secret), mfa_on=True))
    except (ConfigError, OSError) as exc:
        print(exc)
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    client.pending_mfa_secret = ""
    client.pending_password = ""
    restart_login(client)
    server.send(client, player_prompt(client))


def process_mfa_remove_pw(server: Server, client: Client, line: str) -> None:
    """Check the password typed to remove MFA."""
    if line.strip().lower() == "quit":
        restart_login(client)
        server.send(client, player_prompt(client))
        return
    user = client.player
    if user is None:
        restart_login(client)
        server.send(client, player_prompt(client))
        return
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None or not password_ok(line, saved.password_hash):
        restart_login(client)
        server.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    client.pending_password = line
    client.auth = "mfa_remove_code"
    client.password_failures = 0
    server.send(client, code_prompt())


def process_mfa_remove_code(server: Server, client: Client, line: str) -> None:
    """Check the code typed to remove MFA."""
    if line.strip().lower() == "quit":
        restart_login(client)
        server.send(client, player_prompt(client))
        return
    user = client.player
    if user is None:
        restart_login(client)
        server.send(client, player_prompt(client))
        return
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None or not has_set_mfa(saved):
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    plain, stored, opened = refresh_mfa_secret(client.pending_password, saved.mfa_secret)
    if not opened or plain is None:
        restart_login(client)
        server.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    used = server.mfa_steps.get(user.name.lower())
    step = verify_totp(plain, line.strip(), now=int(time.time()), used=used)
    if step is None:
        client.password_failures += 1
        server.send(client, "Wrong code.\n")
        if client.password_failures >= MAX_PASSWORD_FAILURES:
            server.drop(client, announce=True)
            return
        server.send(client, code_prompt())
        return
    record_mfa_step(server.mfa_steps, user.name.lower(), step)
    if not clear_mfa_secret(server, client, saved):
        return
    client.pending_password = ""
    restart_login(client)
    server.send(client, f"{removed_line(saved.rank)}{player_prompt(client)}")


def process_mfa_sure(server: Server, client: Client, line: str) -> None:
    """Remove MFA when the player answers yes."""
    if line.strip().lower() != "yes":
        restart_login(client)
        server.send(client, f"MFA remove cancelled.\n{player_prompt(client)}")
        return
    name = client.pending_name
    try:
        saved = find_saved(server, name)
    except (ConfigError, OSError) as exc:
        print(exc)
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None:
        restart_login(client)
        server.send(client, f"Player {name} not found.\n{player_prompt(client)}")
        return
    if not clear_mfa_secret(server, client, saved):
        return
    target = find_player(server, client, saved.name, quiet=True)
    if target is not None and needs_mfa_after_remove(server, saved.rank):
        server.send(target, f"{REMOVED_WARNING}{player_prompt(target)}")
    broadcast_admin(server, f"[ Admin ] {get_player_name(client)} removes MFA for {saved.name}.\n", skip=client)
    restart_login(client)
    server.send(client, player_prompt(client))


def clear_mfa_secret(server: Server, client: Client, saved: SavedPlayer) -> bool:
    """Clear the MFA secret on ``saved`` and report a write failure."""
    try:
        save_player(server.roster_dir, replace(saved, mfa_secret=None, mfa_on=False))
    except (ConfigError, OSError) as exc:
        print(exc)
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return False
    server.mfa_steps.pop(saved.name.lower(), None)
    return True


def needs_mfa_after_remove(server: Server, rank: Rank) -> bool:
    """Return whether ``rank`` must enrol again after MFA is removed."""
    return server.mfa_enabled and rank_allows(rank, Rank.SU_CHANNEL)


def removed_line(rank: Rank) -> str:
    """Return the line shown when MFA has been removed."""
    if rank_allows(rank, Rank.SU_CHANNEL):
        return REMOVED_WARNING
    return REMOVED
