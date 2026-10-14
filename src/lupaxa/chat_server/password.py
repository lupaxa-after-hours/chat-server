"""Password prompts and the steps that set or check a password."""

from __future__ import annotations

import time
from dataclasses import replace

from .channel import broadcast_staff, player_named
from .client import Client
from .commands_list import COMMANDS, Command, Host
from .entry import (
    _ask,
    _enter,
    _enter_fresh,
    _restart_login,
    name_taken_reply,
    player_prompt,
)
from .exceptions import ConfigError
from .globals import CANNOT_NOW, MAX_PASSWORD_FAILURES, NAME_PROMPT
from .mfa_seal import refresh_mfa_secret, seal_mfa_secret
from .saved_player import (
    RANK_NEWBIE,
    RANK_RESIDENT,
    RANK_SUPER,
    RANK_SUPER_ADMIN,
    SavedPlayer,
    delete_player,
    hash_password,
    password_ok,
    password_problem,
    rank_allows,
    save_player,
)
from .totp import new_secret, otpauth_uri, qr_lines, record_mfa_step, verify_totp
from .utilities import _find_saved, frame_width


def current_password_prompt() -> str:
    """Return the prompt that asks for the current password."""
    return "Current password: "


def command_password(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Start the three answers that replace a saved password."""
    del command
    if text:
        host.send(client, "Syntax: password\n")
        return
    user = client.player
    if user is None or user.rank == RANK_NEWBIE:
        host.send(client, "You have no saved password.\n")
        return
    _ask(client, "current", user.name, None)
    host.send(client, current_password_prompt())


def set_password_prompt() -> str:
    """Return the prompt that asks for a new password, with no trailing newline."""
    return "Set a password: "


def confirm_password_prompt() -> str:
    """Return the confirm prompt on its own line, after a blank line."""
    return "\n\nConfirm password: "


def password_prompt() -> str:
    """Return the prompt that asks for a saved player's password."""
    return "Password: "


def code_prompt() -> str:
    """Return the prompt that asks for a login code."""
    return "Code: "


def _mfa_enrolment_text(host: Host, client: Client, name: str, secret: str) -> str:
    """Return the secret, optional QR rows, and the code prompt."""
    parts = [secret, ""]
    width = frame_width(client.discovered_columns, client.frame_columns)
    rows = qr_lines(otpauth_uri(name, secret))
    if rows and all(len(row) <= width for row in rows):
        parts.extend(rows)
        parts.append("")
    parts.append(code_prompt().rstrip())
    return "\n".join(parts) + " "


def _after_password(host: Host, client: Client, saved: SavedPlayer, password: str) -> None:
    """Enter, ask for a code, or start forced enrolment after a good password."""
    plain, stored, opened = refresh_mfa_secret(password, saved.mfa_secret)
    if stored != saved.mfa_secret or (opened and plain is not None and not saved.mfa_on):
        updated = replace(
            saved,
            mfa_secret=stored,
            mfa_on=True if opened and plain is not None else saved.mfa_on,
        )
        try:
            save_player(host._roster_dir, updated)
        except (ConfigError, OSError) as exc:
            print(exc)
            _restart_login(client)
            host.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
            return
        saved = updated
    client.pending_saved = saved
    client.pending_mfa_secret = plain or ""
    if saved.rank == RANK_RESIDENT and not host._allow_residents:
        host.send(client, "The talker is closed.\n")
        host.drop(client, announce=False)
        return
    if not host._mfa_enabled:
        _enter_fresh(host, client, saved)
        return
    if saved.rank == RANK_RESIDENT and saved.mfa_secret is None and not saved.mfa_on:
        _enter_fresh(host, client, saved)
        return
    if not opened or (saved.mfa_on and plain is None):
        host.send(client, "Your MFA has been removed.\n")
        if rank_allows(saved.rank, RANK_SUPER):
            client.pending_password = password
            client.pending_mfa_secret = new_secret()
            client.auth = "mfa_enroll"
            host.send(
                client,
                _mfa_enrolment_text(host, client, saved.name, client.pending_mfa_secret),
            )
            return
        try:
            cleared = replace(saved, mfa_on=False, mfa_secret=None)
            save_player(host._roster_dir, cleared)
        except (ConfigError, OSError) as exc:
            print(exc)
            _restart_login(client)
            host.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
            return
        _enter_fresh(host, client, cleared)
        return
    if plain is not None:
        client.password_failures = 0
        client.auth = "code"
        client.pending_password = ""
        host.send(client, code_prompt())
        return
    if rank_allows(saved.rank, RANK_SUPER):
        client.pending_password = password
        client.pending_mfa_secret = new_secret()
        client.auth = "mfa_enroll"
        host.send(client, _mfa_enrolment_text(host, client, saved.name, client.pending_mfa_secret))
        return
    _enter_fresh(host, client, saved)


def _process_password(host: Host, client: Client, line: str) -> None:
    """Check the password of a saved name, and let the player in when it matches."""
    saved = client.pending_saved
    if saved is None:
        _restart_login(client)
        host.send(client, NAME_PROMPT)
        return
    if password_ok(line, saved.password_hash):
        client.password_failures = 0
        _after_password(host, client, saved, line)
        return
    client.password_failures += 1
    host.send(client, "Wrong password.\n")
    if client.password_failures >= MAX_PASSWORD_FAILURES:
        host.drop(client, announce=False)
        return
    host.send(client, password_prompt())


def _process_code(host: Host, client: Client, line: str) -> None:
    """Check a login or enrolment code, then enter or save the secret."""
    enrolling = client.auth == "mfa_enroll"
    if enrolling:
        secret = client.pending_mfa_secret
        name = client.pending_name
        saved = client.pending_saved
    else:
        saved = client.pending_saved
        if saved is None or saved.mfa_secret is None:
            _restart_login(client)
            host.send(client, NAME_PROMPT)
            return
        secret = client.pending_mfa_secret
        name = saved.name
    used = host._mfa_steps.get(name.lower())
    step = verify_totp(secret, line.strip(), now=int(time.time()), used=used)
    if step is None:
        client.password_failures += 1
        host.send(client, "Wrong code.\n")
        if client.password_failures >= MAX_PASSWORD_FAILURES:
            host.drop(client, announce=False)
            return
        host.send(client, code_prompt())
        return
    record_mfa_step(host._mfa_steps, name.lower(), step)
    if enrolling:
        if saved is not None:
            try:
                fresh = _find_saved(host, saved.name)
            except (ConfigError, OSError) as exc:
                print(exc)
                _restart_login(client)
                host.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
                return
            if fresh is None:
                _restart_login(client)
                host.send(client, NAME_PROMPT)
                return
            seal_password = client.pending_password
            try:
                save_player(
                    host._roster_dir,
                    replace(
                        fresh,
                        mfa_secret=seal_mfa_secret(seal_password, secret),
                        mfa_on=True,
                    ),
                )
            except (ConfigError, OSError) as exc:
                print(exc)
                _restart_login(client)
                host.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
                return
            client.pending_password = ""
            _enter_fresh(host, client, fresh)
            return
        password = client.pending_password
        try:
            if _find_saved(host, name) is not None:
                _restart_login(client)
                host.send(client, f"That name is already saved.\n{NAME_PROMPT}")
                return
            record = SavedPlayer(
                name,
                RANK_SUPER_ADMIN,
                hash_password(password),
                mfa_secret=seal_mfa_secret(password, secret),
                mfa_on=True,
            )
            save_player(host._roster_dir, record)
        except (ConfigError, OSError) as exc:
            print(exc)
            _restart_login(client)
            host.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
            return
        client.pending_password = ""
        _enter(host, client, name, RANK_SUPER_ADMIN)
        return
    assert saved is not None  # noqa: S101
    _enter_fresh(host, client, saved)


def _process_set_password(host: Host, client: Client, line: str) -> None:
    """Take the first of two entries of a new password."""
    problem = password_problem(line)
    if problem is not None:
        host.send(client, f"{problem}{set_password_prompt()}")
        return
    client.pending_password = line
    client.auth = "confirm"
    host.send(client, confirm_password_prompt())


def _process_current(host: Host, client: Client, line: str) -> None:
    """Check the current password before asking for a new one."""
    user = client.player
    if user is None:
        _restart_login(client)
        host.send(client, NAME_PROMPT)
        return
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
    if not password_ok(line, saved.password_hash):
        _restart_login(client)
        host.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    client.pending_current_password = line
    client.auth = "renew"
    host.send(client, set_password_prompt())


def _process_renew(host: Host, client: Client, line: str) -> None:
    """Take the first of two entries of a replacement password."""
    problem = password_problem(line)
    if problem is not None:
        host.send(client, f"{problem}{set_password_prompt()}")
        return
    client.pending_password = line
    client.auth = "renew_confirm"
    host.send(client, confirm_password_prompt())


def _process_retire_confirm(host: Host, client: Client, line: str) -> None:
    """Continue only when the whole line is yes."""
    if line.strip().lower() != "yes":
        client.auth = "name"
        host.send(client, f"Retire cancelled.\n{player_prompt(client)}")
        return
    client.auth = "retire_password"
    host.send(client, password_prompt())


def _process_retire_password(host: Host, client: Client, line: str) -> None:
    """Delete the file when the password matches. A miss returns to the room."""
    user = client.player
    if user is None:
        client.auth = "name"
        host.send(client, NAME_PROMPT)
        return
    try:
        saved = _find_saved(host, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        client.auth = "name"
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None or not password_ok(line, saved.password_hash):
        client.auth = "name"
        host.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    try:
        if not delete_player(host._roster_dir, user.name):
            raise ConfigError("No saved player.")
    except (ConfigError, OSError) as exc:
        print(exc)
        client.auth = "name"
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    client.auth = "name"
    host.send(client, "You have retired.\n")
    broadcast_staff(host, f"< SU > {user.name} retires.\n")
    host.drop(client, announce=True)


def _process_renew_confirm(host: Host, client: Client, line: str) -> None:
    """Take the second entry of a replacement password and save it."""
    if line != client.pending_password:
        client.pending_password = ""
        client.auth = "renew"
        host.send(client, f"Those passwords do not match.\n{set_password_prompt()}")
        return
    user = client.player
    if user is None:
        _restart_login(client)
        host.send(client, NAME_PROMPT)
        return
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
    current = client.pending_current_password
    plain, stored, opened = refresh_mfa_secret(current, saved.mfa_secret)
    if opened and plain is not None:
        plain, stored, opened = refresh_mfa_secret(line, plain)
    try:
        save_player(
            host._roster_dir,
            replace(
                saved,
                password_hash=hash_password(line),
                mfa_secret=stored,
                mfa_on=saved.mfa_on,
            ),
        )
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    client.pending_password = ""
    client.pending_current_password = ""
    _restart_login(client)
    host.send(client, f"Password changed.\n{player_prompt(client)}")


def _process_confirm_password(host: Host, client: Client, line: str) -> None:
    """Take the second entry of a new password, then save the admin and enter."""
    if line != client.pending_password:
        client.pending_password = ""
        client.auth = "set"
        host.send(client, f"Those passwords do not match.\n{set_password_prompt()}")
        return
    if client.player is not None:
        _promote_to_resident(host, client, line)
        return
    name = client.pending_name
    taken = {item.lower() for item in host._taken_names()}
    if name.lower() in host._unsaved_names(skip=client) or name.lower() in taken:
        _restart_login(client)
        host.send(client, name_taken_reply(name))
        return
    try:
        if _find_saved(host, name) is not None:
            _restart_login(client)
            host.send(client, f"That name is already saved.\n{NAME_PROMPT}")
            return
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
        return
    if host._mfa_enabled:
        client.pending_mfa_secret = new_secret()
        client.auth = "mfa_enroll"
        host.send(client, _mfa_enrolment_text(host, client, name, client.pending_mfa_secret))
        return
    try:
        record = SavedPlayer(name, RANK_SUPER_ADMIN, hash_password(line))
        save_player(host._roster_dir, record)
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
        return
    _enter(host, client, name, RANK_SUPER_ADMIN)


def _promote_to_resident(host: Host, client: Client, password: str) -> None:
    """Save a connected newbie as a resident once they have confirmed a password."""
    user = client.player
    if user is None:
        return
    issuer = client.pending_issuer
    try:
        if _find_saved(host, user.name) is not None:
            _restart_login(client)
            host.send(client, f"That name is already saved.\n{player_prompt(client)}")
            return
        if user.description == "I am new here":
            user.description = ""
        record = SavedPlayer(
            user.name,
            RANK_RESIDENT,
            hash_password(password),
            ip_address=client.address,
            hostname=client.hostname,
            connected_seconds=user.connected_seconds,
            colour=user.colour,
            description=user.description,
        )
        save_player(host._roster_dir, record)
    except (ConfigError, OSError) as exc:
        print(exc)
        _restart_login(client)
        host.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    _restart_login(client)
    user.rank = RANK_RESIDENT
    user.flushed_seconds = user.connected_seconds
    host.send(client, "You are now a resident.\n")
    issuer_client = player_named(host._clients.values(), issuer) if issuer else None
    if issuer_client is not None:
        host.send(issuer_client, f"{user.name} is now a resident.\n")
    host.send(client, player_prompt(client))


COMMANDS.extend((Command("password", command_password, False, None),))
