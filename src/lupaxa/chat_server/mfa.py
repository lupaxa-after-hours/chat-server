"""In-room MFA setup and removal."""

from __future__ import annotations

import time
from dataclasses import replace

from .channel import broadcast_admin, player_named
from .client import Client
from .commands_list import Command, Host
from .entry import _restart_login, player_prompt
from .exceptions import ConfigError
from .globals import CANNOT_NOW, MAX_PASSWORD_FAILURES
from .mfa_seal import refresh_mfa_secret, seal_mfa_secret
from .password import _mfa_enrolment_text, code_prompt, current_password_prompt
from .saved_player import (
    RANK_RESIDENT,
    RANK_SUPER,
    SavedPlayer,
    password_ok,
    rank_allows,
    save_player,
)
from .totp import new_secret, record_mfa_step, verify_totp
from .utilities import _find_saved, _lookup, _name_of

_SYNTAX = "Syntax: mfa setup|mfa remove|mfa remove <name>\n"
_REMOVED = "Your MFA has been removed.\n"
_REMOVED_WARNING = (
    "Your MFA has been removed. Set it up again before you log out. "
    "The next login will make you set it up before you can enter.\n"
)


def register_mfa(commands: list[Command]) -> None:
    """Append the mfa command when the server has MFA enabled."""
    from .commands_list import by_name

    if "mfa" in by_name():
        return
    entry = Command(
        "mfa",
        command_mfa,
        False,
        "mfa setup|mfa remove|mfa remove <name>",
        rank=RANK_RESIDENT,
    )
    commands.append(entry)
    by_name()[entry.name] = entry


def command_mfa(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Start MFA setup, self-removal, or another player's removal."""
    del command
    parts = (text or "").strip().split()
    if len(parts) > 2:
        host.send(client, _SYNTAX)
        return
    if not parts:
        host.send(client, _SYNTAX)
        return
    if parts[0] == "setup":
        if len(parts) != 1:
            host.send(client, _SYNTAX)
            return
        _start_setup(host, client)
        return
    if parts[0] == "remove":
        if len(parts) == 1:
            _start_self_remove(host, client)
            return
        if len(parts) == 2:
            target = parts[1]
            own = client.name
            if own is not None and target.lower() == own.lower():
                _start_self_remove(host, client)
                return
            _start_other_remove(host, client, target)
            return
    host.send(client, _SYNTAX)


def _start_setup(host: Host, client: Client) -> None:
    user = client.player
    if user is None:
        return
    try:
        saved = _find_saved(host, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None:
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved.mfa_secret is not None:
        host.send(client, "MFA is already on.\n")
        return
    client.auth = "mfa_pw"
    client.password_failures = 0
    host.send(client, current_password_prompt())


def _start_self_remove(host: Host, client: Client) -> None:
    user = client.player
    if user is None:
        return
    try:
        saved = _find_saved(host, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None or saved.mfa_secret is None:
        host.send(client, f"{user.name} has no MFA.\n")
        return
    client.auth = "mfa_remove_pw"
    client.password_failures = 0
    host.send(client, current_password_prompt())


def _start_other_remove(host: Host, client: Client, name: str) -> None:
    found, saved = _lookup(host, client, name)
    if not found:
        return
    if saved is None:
        host.send(client, f"Player {name} not found.\n")
        return
    caller_rank = client.player.rank if client.player is not None else RANK_RESIDENT
    if rank_allows(saved.rank, caller_rank):
        host.send(client, f"You cannot remove MFA for {saved.name}.\n")
        return
    if saved.mfa_secret is None:
        host.send(client, f"{saved.name} has no MFA.\n")
        return
    client.auth = "mfa_sure"
    client.pending_name = saved.name
    client.password_failures = 0
    host.send(client, f"Are you sure you want to remove MFA for {saved.name}?\n")


def _process_mfa_pw(host: Host, client: Client, line: str) -> None:
    if line.strip().lower() == "quit":
        _restart_login(client)
        host.send(client, player_prompt(client))
        return
    user = client.player
    if user is None:
        _restart_login(client)
        host.send(client, player_prompt(client))
        return
    try:
        saved = _find_saved(host, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None or not password_ok(line, saved.password_hash):
        _restart_login(client)
        host.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    client.pending_password = line
    secret = new_secret()
    client.pending_mfa_secret = secret
    client.auth = "mfa_show"
    host.send(client, _mfa_enrolment_text(host, client, user.name, secret))


def _process_mfa_show(host: Host, client: Client, line: str) -> None:
    if line.strip().lower() == "quit":
        _restart_login(client)
        host.send(client, player_prompt(client))
        return
    user = client.player
    secret = client.pending_mfa_secret
    if user is None or secret == "":
        _restart_login(client)
        host.send(client, player_prompt(client))
        return
    used = host._mfa_steps.get(user.name.lower())
    step = verify_totp(secret, line.strip(), now=int(time.time()), used=used)
    if step is None:
        client.password_failures += 1
        host.send(client, "Wrong code.\n")
        if client.password_failures >= MAX_PASSWORD_FAILURES:
            host.drop(client, announce=True)
            return
        host.send(client, code_prompt())
        return
    record_mfa_step(host._mfa_steps, user.name.lower(), step)
    try:
        saved = _find_saved(host, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None:
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    seal_password = client.pending_password
    try:
        save_player(
            host._roster_dir,
            replace(
                saved,
                mfa_secret=seal_mfa_secret(seal_password, secret),
                mfa_on=True,
            ),
        )
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    client.pending_mfa_secret = ""
    client.pending_password = ""
    _restart_login(client)
    host.send(client, player_prompt(client))


def _process_mfa_remove_pw(host: Host, client: Client, line: str) -> None:
    if line.strip().lower() == "quit":
        _restart_login(client)
        host.send(client, player_prompt(client))
        return
    user = client.player
    if user is None:
        _restart_login(client)
        host.send(client, player_prompt(client))
        return
    try:
        saved = _find_saved(host, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None or not password_ok(line, saved.password_hash):
        _restart_login(client)
        host.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    client.pending_password = line
    client.auth = "mfa_remove_code"
    client.password_failures = 0
    host.send(client, code_prompt())


def _process_mfa_remove_code(host: Host, client: Client, line: str) -> None:
    if line.strip().lower() == "quit":
        _restart_login(client)
        host.send(client, player_prompt(client))
        return
    user = client.player
    if user is None:
        _restart_login(client)
        host.send(client, player_prompt(client))
        return
    try:
        saved = _find_saved(host, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None or saved.mfa_secret is None:
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    plain, _stored, opened = refresh_mfa_secret(client.pending_password, saved.mfa_secret)
    if not opened or plain is None:
        _restart_login(client)
        host.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    used = host._mfa_steps.get(user.name.lower())
    step = verify_totp(plain, line.strip(), now=int(time.time()), used=used)
    if step is None:
        client.password_failures += 1
        host.send(client, "Wrong code.\n")
        if client.password_failures >= MAX_PASSWORD_FAILURES:
            host.drop(client, announce=True)
            return
        host.send(client, code_prompt())
        return
    record_mfa_step(host._mfa_steps, user.name.lower(), step)
    if not _clear_mfa_secret(host, client, saved):
        return
    client.pending_password = ""
    _restart_login(client)
    host.send(client, f"{_removed_line(saved.rank)}{player_prompt(client)}")


def _process_mfa_sure(host: Host, client: Client, line: str) -> None:
    if line.strip().lower() != "yes":
        _restart_login(client)
        host.send(client, f"MFA remove cancelled.\n{player_prompt(client)}")
        return
    name = client.pending_name
    try:
        saved = _find_saved(host, name)
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None:
        _restart_login(client)
        host.send(client, f"Player {name} not found.\n{player_prompt(client)}")
        return
    if not _clear_mfa_secret(host, client, saved):
        return
    target = player_named(host._clients.values(), saved.name)
    if target is not None and _needs_mfa_after_remove(host, saved.rank):
        host.send(target, f"{_REMOVED_WARNING}{player_prompt(target)}")
    broadcast_admin(
        host,
        f"[ Admin ] {_name_of(client)} removes MFA for {saved.name}.\n",
        skip=client,
    )
    _restart_login(client)
    host.send(client, player_prompt(client))


def _clear_mfa_secret(host: Host, client: Client, saved: SavedPlayer) -> bool:
    try:
        save_player(host._roster_dir, replace(saved, mfa_secret=None, mfa_on=False))
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return False
    host._mfa_steps.pop(saved.name.lower(), None)
    return True


def _needs_mfa_after_remove(host: Host, rank: str) -> bool:
    return host._mfa_enabled and rank_allows(rank, RANK_SUPER)


def _removed_line(rank: str) -> str:
    if rank_allows(rank, RANK_SUPER):
        return _REMOVED_WARNING
    return _REMOVED
