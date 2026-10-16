"""Password prompts and the steps that set or check a password."""

from __future__ import annotations  # noqa: I001

import time
from dataclasses import replace

from .broadcast import broadcast_staff
from .client import Client
from .commands import Command, shown_syntax
from .entry import ask, enter_fresh, player_prompt, restart_login
from .exceptions import ConfigError
from .globals import CANNOT_NOW, COMMANDS, MAX_PASSWORD_FAILURES, NAME_PROMPT
from .mfa_seal import refresh_mfa_secret, seal_mfa_secret
from .player import find_player
from .rooms import unlink_house
from .saved_player import SavedPlayer, delete_player, has_set_mfa, hash_password, password_ok, password_problem, save_player  # fmt: skip  # noqa: E501
from .server import Server
from .totp import new_secret, otpauth_uri, qr_lines, record_mfa_step, verify_totp
from .utilities import find_saved, frame_width
from .ranks import Rank, rank_allows


def current_password_prompt() -> str:
    """Return the prompt that asks for the current password."""
    return "Current password: "


def command_password(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Start the three answers that replace a saved password."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    user = client.player
    if user is None or user.rank == Rank.NEWBIE:
        server.send(client, "You have no saved password.\n")
        return
    ask(client, "current", user.name, None)
    server.send(client, current_password_prompt())


def set_password_prompt() -> str:
    """Return the prompt that asks for a new password, with no trailing newline."""
    return "Set a password: "


def confirm_password_prompt() -> str:
    """Return the confirm prompt on its own line, after a blank line."""
    return "\n\nConfirm password: "


def password_prompt() -> str:
    """Return the prompt that asks for a saved player's password."""
    return "Password: "


def unlock_prompt() -> str:
    """Return the prompt that asks for the password that unlocks the screen."""
    return "Enter password to unlock: "


def send_lock_line(server: Server, client: Client, text: str) -> None:
    """Send ``text`` even while this connection's screen is locked."""
    client.show_lock = True
    try:
        server.send(client, text)
    finally:
        client.show_lock = False


def code_prompt() -> str:
    """Return the prompt that asks for a login code."""
    return "Code: "


def mfa_enrolment_text(server: Server, client: Client, name: str, secret: str) -> str:
    """Return the secret, optional QR rows, and the code prompt."""
    parts = [secret, ""]
    width = frame_width(client.discovered_columns, client.frame_columns)
    rows = qr_lines(otpauth_uri(name, secret))
    if rows and all(len(row) <= width for row in rows):
        parts.extend(rows)
        parts.append("")
    parts.append(code_prompt().rstrip())
    return "\n".join(parts) + " "


def after_password(server: Server, client: Client, saved: SavedPlayer, password: str) -> None:
    """Enter, ask for a code, or start forced enrolment after a good password."""
    plain, stored, opened = refresh_mfa_secret(password, saved.mfa_secret)
    if stored != saved.mfa_secret or (opened and plain is not None and not saved.mfa_on):
        updated = replace(saved, mfa_secret=stored, mfa_on=True if opened and plain is not None else saved.mfa_on)
        try:
            save_player(server.roster_dir, updated)
        except (ConfigError, OSError) as exc:
            print(exc)
            restart_login(client)
            server.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
            return
        saved = updated
    client.pending_saved = saved
    client.pending_mfa_secret = plain or ""
    if saved.rank == Rank.RESIDENT and not server.allow_residents:
        server.send(client, "The talker is closed.\n")
        server.drop(client, announce=False)
        return
    if not server.mfa_enabled:
        enter_fresh(server, client, saved)
        return
    if saved.rank == Rank.RESIDENT and not has_set_mfa(saved) and not saved.mfa_on:
        enter_fresh(server, client, saved)
        return
    if not opened or (saved.mfa_on and plain is None):
        server.send(client, "Your MFA has been removed.\n")
        if rank_allows(saved.rank, Rank.SU_CHANNEL):
            client.pending_password = password
            client.pending_mfa_secret = new_secret()
            client.auth = "mfa_enroll"
            server.send(client, mfa_enrolment_text(server, client, saved.name, client.pending_mfa_secret))
            return
        try:
            cleared = replace(saved, mfa_on=False, mfa_secret=None)
            save_player(server.roster_dir, cleared)
        except (ConfigError, OSError) as exc:
            print(exc)
            restart_login(client)
            server.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
            return
        enter_fresh(server, client, cleared)
        return
    if plain is not None:
        client.password_failures = 0
        client.auth = "code"
        client.pending_password = ""
        server.send(client, code_prompt())
        return
    if rank_allows(saved.rank, Rank.SU_CHANNEL):
        client.pending_password = password
        client.pending_mfa_secret = new_secret()
        client.auth = "mfa_enroll"
        server.send(client, mfa_enrolment_text(server, client, saved.name, client.pending_mfa_secret))
        return
    enter_fresh(server, client, saved)


def process_password(server: Server, client: Client, line: str) -> None:
    """Check the password of a saved name, and let the player in when it matches."""
    saved = client.pending_saved
    if saved is None:
        restart_login(client)
        server.send(client, NAME_PROMPT)
        return
    if password_ok(line, saved.password_hash):
        client.password_failures = 0
        after_password(server, client, saved, line)
        return
    client.password_failures += 1
    server.send(client, "Wrong password.\n")
    if client.password_failures >= MAX_PASSWORD_FAILURES:
        server.drop(client, announce=False)
        return
    server.send(client, password_prompt())


def process_code(server: Server, client: Client, line: str) -> None:
    """Check a login or enrolment code, then enter or save the secret."""
    enrolling = client.auth == "mfa_enroll"
    if enrolling:
        secret = client.pending_mfa_secret
        name = client.pending_name
        saved = client.pending_saved
    else:
        saved = client.pending_saved
        if saved is None or not has_set_mfa(saved):
            restart_login(client)
            server.send(client, NAME_PROMPT)
            return
        secret = client.pending_mfa_secret
        name = saved.name
    used = server.mfa_steps.get(name.lower())
    step = verify_totp(secret, line.strip(), now=int(time.time()), used=used)
    if step is None:
        client.password_failures += 1
        server.send(client, "Wrong code.\n")
        if client.password_failures >= MAX_PASSWORD_FAILURES:
            server.drop(client, announce=False)
            return
        server.send(client, code_prompt())
        return
    record_mfa_step(server.mfa_steps, name.lower(), step)
    if enrolling:
        if saved is not None:
            try:
                fresh = find_saved(server, saved.name)
            except (ConfigError, OSError) as exc:
                print(exc)
                restart_login(client)
                server.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
                return
            if fresh is None:
                restart_login(client)
                server.send(client, NAME_PROMPT)
                return
            seal_password = client.pending_password
            try:
                save_player(server.roster_dir, replace(fresh, mfa_secret=seal_mfa_secret(seal_password, secret), mfa_on=True))
            except (ConfigError, OSError) as exc:
                print(exc)
                restart_login(client)
                server.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
                return
            client.pending_password = ""
            enter_fresh(server, client, fresh)
            return
        restart_login(client)
        server.send(client, NAME_PROMPT)
        return
    assert saved is not None  # noqa: S101
    enter_fresh(server, client, saved)


def process_set_password(server: Server, client: Client, line: str) -> None:
    """Take the first of two entries of a new password."""
    problem = password_problem(line)
    if problem is not None:
        server.send(client, f"{problem}{set_password_prompt()}")
        return
    client.pending_password = line
    client.auth = "confirm"
    server.send(client, confirm_password_prompt())


def process_current(server: Server, client: Client, line: str) -> None:
    """Check the current password before asking for a new one."""
    user = client.player
    if user is None:
        restart_login(client)
        server.send(client, NAME_PROMPT)
        return
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
    if not password_ok(line, saved.password_hash):
        restart_login(client)
        server.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    client.pending_current_password = line
    client.auth = "renew"
    server.send(client, set_password_prompt())


def process_renew(server: Server, client: Client, line: str) -> None:
    """Take the first of two entries of a replacement password."""
    problem = password_problem(line)
    if problem is not None:
        server.send(client, f"{problem}{set_password_prompt()}")
        return
    client.pending_password = line
    client.auth = "renew_confirm"
    server.send(client, confirm_password_prompt())


def process_retire_confirm(server: Server, client: Client, line: str) -> None:
    """Continue only when the whole line is yes."""
    if line.strip().lower() != "yes":
        client.auth = "name"
        server.send(client, f"Retire cancelled.\n{player_prompt(client)}")
        return
    client.auth = "retire_password"
    server.send(client, password_prompt())


def process_on_duty_password(server: Server, client: Client, line: str) -> None:
    """Return to duty when the password matches. A miss stays off duty."""
    user = client.player
    client.auth = "name"
    if user is None:
        server.send(client, NAME_PROMPT)
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
    if not password_ok(line, saved.password_hash):
        broadcast_staff(server, f"< SU > {user.name} got their password wrong and failed to return to duty.\n")
        server.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    user.off_duty = False
    broadcast_staff(server, f"< SU > {user.name} has returned to duty.\n")
    server.send(client, player_prompt(client))


def process_screenlock_password(server: Server, client: Client, line: str) -> None:
    """Unlock when the password matches. A miss stays on the unlock prompt."""
    user = client.player
    if user is None or not user.screen_locked:
        client.auth = "name"
        server.send(client, player_prompt(client))
        return
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        send_lock_line(server, client, f"Wrong password.\n{unlock_prompt()}")
        return
    if saved is None or not password_ok(line, saved.password_hash):
        send_lock_line(server, client, f"Wrong password.\n{unlock_prompt()}")
        return
    user.screen_locked = False
    user.screen_lock_reason = ""
    client.auth = "name"
    if rank_allows(user.rank, Rank.SU_CHANNEL):
        user.off_duty = False
        broadcast_staff(server, f"< SU > {user.name} has unlocked their screen.\n")
    server.send(client, "Screen is now unlocked.\n")
    server.send(client, player_prompt(client))


def process_retire_password(server: Server, client: Client, line: str) -> None:
    """Delete the file when the password matches. A miss returns to the room."""
    user = client.player
    if user is None:
        client.auth = "name"
        server.send(client, NAME_PROMPT)
        return
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        client.auth = "name"
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None or not password_ok(line, saved.password_hash):
        client.auth = "name"
        server.send(client, f"Wrong password.\n{player_prompt(client)}")
        return
    try:
        if not delete_player(server.roster_dir, user.name):
            raise ConfigError("No saved player.")
    except (ConfigError, OSError) as exc:
        print(exc)
        client.auth = "name"
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    unlink_house(server, user.name)
    client.auth = "name"
    server.send(client, "You have retired.\n")
    broadcast_staff(server, f"< SU > {user.name} retires.\n")
    server.drop(client, announce=True)


def process_renew_confirm(server: Server, client: Client, line: str) -> None:
    """Take the second entry of a replacement password and save it."""
    if line != client.pending_password:
        client.pending_password = ""
        client.auth = "renew"
        server.send(client, f"Those passwords do not match.\n{set_password_prompt()}")
        return
    user = client.player
    if user is None:
        restart_login(client)
        server.send(client, NAME_PROMPT)
        return
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
    current = client.pending_current_password
    plain, stored, opened = refresh_mfa_secret(current, saved.mfa_secret)
    if opened and plain is not None:
        plain, stored, opened = refresh_mfa_secret(line, plain)
    try:
        save_player(server.roster_dir, replace(saved, password_hash=hash_password(line), mfa_secret=stored, mfa_on=saved.mfa_on))
    except (ConfigError, OSError) as exc:
        print(exc)
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    client.pending_password = ""
    client.pending_current_password = ""
    restart_login(client)
    server.send(client, f"Password changed.\n{player_prompt(client)}")


def process_confirm_password(server: Server, client: Client, line: str) -> None:
    """Take the second entry of a new password, then save a resident when connected."""
    if line != client.pending_password:
        client.pending_password = ""
        client.auth = "set"
        server.send(client, f"Those passwords do not match.\n{set_password_prompt()}")
        return
    if client.player is not None:
        promote_to_resident(server, client, line)
        return
    restart_login(client)
    server.send(client, NAME_PROMPT)


def promote_to_resident(server: Server, client: Client, password: str) -> None:
    """Save a connected newbie as a resident once they have confirmed a password."""
    user = client.player
    if user is None:
        return
    issuer = client.pending_issuer
    try:
        if find_saved(server, user.name) is not None:
            restart_login(client)
            server.send(client, f"That name is already saved.\n{player_prompt(client)}")
            return
        if user.description == "I am new here":
            user.description = ""
        record = SavedPlayer(user.name, Rank.RESIDENT, hash_password(password), ip_address=client.address, hostname=client.hostname, connected_seconds=user.connected_seconds, colour=user.colour, description=user.description, country_name=client.country_name, country_code=client.country_code)  # fmt: skip  # noqa: E501
        save_player(server.roster_dir, record)
    except (ConfigError, OSError) as exc:
        print(exc)
        restart_login(client)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    restart_login(client)
    user.rank = Rank.RESIDENT
    user.flushed_seconds = user.connected_seconds
    server.send(client, "You are now a resident.\n")
    issuer_client = find_player(server, client, issuer, quiet=True) if issuer else None
    if issuer_client is not None:
        server.send(issuer_client, f"{user.name} is now a resident.\n")
    server.send(client, player_prompt(client))


COMMANDS.extend((Command("password", command_password, False, None),))
