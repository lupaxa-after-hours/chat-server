"""Name prompt and the steps before a player is in a room."""

from __future__ import annotations

from .channel import broadcast_admin
from .client import Client
from .commands_list import Host, by_name
from .entry import _ask, _enter, _restart_login, name_taken_reply, player_prompt
from .exceptions import ConfigError
from .globals import LOCALHOST, NAME_PROMPT
from .help import format_page
from .mfa import (
    _process_mfa_pw,
    _process_mfa_remove_code,
    _process_mfa_remove_pw,
    _process_mfa_show,
    _process_mfa_sure,
)
from .parse import consider_name, process_commands
from .password import (
    _process_code,
    _process_confirm_password,
    _process_current,
    _process_password,
    _process_renew,
    _process_renew_confirm,
    _process_retire_confirm,
    _process_retire_password,
    _process_set_password,
    password_prompt,
    set_password_prompt,
)
from .saved_player import RANK_NEWBIE
from .utilities import _find_saved, center_block, frame_width

AGREE_PROMPT = "Do you agree to these rules: "


def admin_gate_reply() -> str:
    """Return the line that waits until the admin password exists."""
    return f"The admin must set a password from this machine first.\n\n{NAME_PROMPT}"


def name_prompt() -> str:
    """Return the prompt the client types on, with no trailing newline."""
    return NAME_PROMPT


def welcome_text(banner: str, width: int) -> str:
    """Return the centered banner, two blank lines, and the name prompt."""
    block = banner[:-1] if banner.endswith("\n") else banner
    centered = center_block(block + "\n", width)
    return f"\n\n{centered}\n\n{NAME_PROMPT}"


def _unsaved_holder_may_quit(client: Client) -> bool:
    """Return whether ``quit`` disconnects instead of being treated as a password."""
    user = client.player
    if user is not None and user.rank == RANK_NEWBIE:
        return True
    return user is None and client.auth in ("set", "confirm", "agree")


def _run_named(host: Host, client: Client, word: str) -> None:
    """Run the command registered as ``word`` with no extra text."""
    command = by_name()[word]
    command.function(host, client, command, None)


def process_input(host: Host, client: Client, line: str) -> None:
    """Login, or run a command. The connection loop does not interpret the line."""
    if client.player is None or client.auth in (
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
    ):
        process_login(host, client, line)
        return
    process_commands(host, client, line)
    if client.auth in (
        "current",
        "renew",
        "renew_confirm",
        "retire_confirm",
        "retire_password",
        "mfa_pw",
        "mfa_remove_pw",
        "mfa_remove_code",
        "mfa_sure",
    ):
        return
    host.send(client, player_prompt(client))


def process_login(host: Host, client: Client, line: str) -> None:
    """Take one login line for a player who is not in a room yet."""
    if client.auth in ("code", "mfa_enroll") and line.strip().lower() == "quit":
        _run_named(host, client, "quit")
        return
    if client.auth != "name" and line == "quit" and _unsaved_holder_may_quit(client):
        _run_named(host, client, "quit")
        return
    if client.auth == "password":
        _process_password(host, client, line)
    elif client.auth in ("code", "mfa_enroll"):
        _process_code(host, client, line)
    elif client.auth == "set":
        _process_set_password(host, client, line)
    elif client.auth == "confirm":
        _process_confirm_password(host, client, line)
    elif client.auth == "current":
        _process_current(host, client, line)
    elif client.auth == "renew":
        _process_renew(host, client, line)
    elif client.auth == "renew_confirm":
        _process_renew_confirm(host, client, line)
    elif client.auth == "retire_confirm":
        _process_retire_confirm(host, client, line)
    elif client.auth == "retire_password":
        _process_retire_password(host, client, line)
    elif client.auth == "mfa_pw":
        _process_mfa_pw(host, client, line)
    elif client.auth == "mfa_show":
        _process_mfa_show(host, client, line)
    elif client.auth == "mfa_remove_pw":
        _process_mfa_remove_pw(host, client, line)
    elif client.auth == "mfa_remove_code":
        _process_mfa_remove_code(host, client, line)
    elif client.auth == "mfa_sure":
        _process_mfa_sure(host, client, line)
    elif client.auth == "agree":
        _process_agree(host, client, line)
    else:
        _process_name(host, client, line)


def _process_name(host: Host, client: Client, line: str) -> None:
    """Accept a name, ask for a password when it is saved, or refuse it."""
    lowered = line.strip().lower()
    if lowered == "who":
        _run_named(host, client, "who")
        host.send(client, NAME_PROMPT)
        return
    if lowered == "quit":
        _run_named(host, client, "quit")
        return
    decision = consider_name(line, set())
    if decision.name is None:
        if decision.reply:
            host.send(client, decision.reply)
        return
    name = decision.name
    try:
        admin_saved = _find_saved(host, host._config.admin)
        saved = _find_saved(host, name)
    except (ConfigError, OSError) as exc:
        print(exc)
        host.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
        return
    if admin_saved is None:
        held = name.lower() in host._unsaved_names(skip=client)
        if held and name.lower() == host._config.admin.lower():
            host.send(client, name_taken_reply(name))
            return
        if client.address not in LOCALHOST or name.lower() != host._config.admin.lower():
            host.send(client, admin_gate_reply())
            return
    if saved is not None:
        _ask(client, "password", saved.name, saved)
        host.send(client, password_prompt())
        return
    if name.lower() in host._unsaved_names(skip=client):
        host.send(client, name_taken_reply(name))
        return
    if name.lower() == host._config.admin.lower():
        _ask(client, "set", name, None)
        host.send(client, set_password_prompt())
        return
    if not host._allow_newbies or not host._allow_residents:
        host.send(client, f"The talker is closed to new people.\n{NAME_PROMPT}")
        return
    _show_disclaimer(host, client, name)


def _show_disclaimer(host: Host, client: Client, name: str) -> None:
    """Show the disclaimer, or drop the newbie when that topic is missing."""
    entry = host._help.find("disclaimer")
    if entry is None:
        host.send(client, "The disclaimer is missing.\n")
        broadcast_admin(host, "[ Admin ] The disclaimer is missing.\n")
        host.drop(client, announce=False)
        return
    _ask(client, "agree", name, None)
    width = frame_width(client.discovered_columns, client.frame_columns)
    visible = frozenset(item.name for item in host._help.entries)
    page = format_page(entry, None, width, visible=visible)
    host.send(client, f"\n\n{page}\n")
    host.send(client, AGREE_PROMPT)


def _process_agree(host: Host, client: Client, line: str) -> None:
    """Enter on yes. Any other line asks again."""
    if line.strip().lower() != "yes":
        host.send(client, AGREE_PROMPT)
        return
    if not host._allow_newbies or not host._allow_residents:
        _restart_login(client)
        host.send(client, f"The talker is closed to new people.\n{NAME_PROMPT}")
        return
    _enter(host, client, client.pending_name, "newbie")
