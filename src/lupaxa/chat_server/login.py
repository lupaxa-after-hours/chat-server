"""Name prompt and the steps before a player is in a room."""

from __future__ import annotations  # noqa: I001

from .broadcast import broadcast_admin
from .client import Client
from .commands import by_name
from .entry import announce_newbie_connecting, announce_newbie_quit, ask, enter, name_taken_reply, player_prompt, restart_login
from .exceptions import ConfigError
from .globals import NAME_PROMPT
from .help import format_page
from .mfa import process_mfa_pw, process_mfa_remove_code, process_mfa_remove_pw, process_mfa_show, process_mfa_sure
from .parse import consider_name, process_commands
from .password import code_prompt, confirm_password_prompt, current_password_prompt, password_prompt, process_code, process_confirm_password, process_current, process_on_duty_password, process_password, process_renew, process_renew_confirm, process_retire_confirm, process_retire_password, process_screenlock_password, process_set_password, send_lock_line, set_password_prompt, unlock_prompt  # fmt: skip  # noqa: E501
from .ranks import Rank, rank_allows
from .rooms import house_path_blocked
from .server import Server
from .colour import center_block
from .utilities import find_player, find_saved, frame_width, get_clients

AGREE_PROMPT = "Do you agree to these rules: "


def name_prompt() -> str:
    """Return the prompt the client types on, with no trailing newline."""
    return NAME_PROMPT


def welcome_text(banner: str, width: int) -> str:
    """Return the centered banner, two blank lines, and the name prompt."""
    block = banner[:-1] if banner.endswith("\n") else banner
    centered = center_block(block + "\n", width)
    return f"\n\n{centered}\n\n{NAME_PROMPT}"


def refusal_text(message: str, width: int) -> str:
    """Return the centered refusal with the banner's blank lines and no name prompt."""
    block = message[:-1] if message.endswith("\n") else message
    centered = center_block(block + "\n", width)
    return f"\n\n{centered}\n\n"


def open_connections(server: Server) -> int:
    """Return how many sockets are open, including this name prompt."""
    return len(list(get_clients(server)))


def newbie_pressure(server: Server, skip: Client) -> int:
    """Return newbies in a room plus connections still reading the disclaimer."""
    count = 0
    for connected in get_clients(server):
        if connected is skip:
            continue
        user = connected.player
        if user is not None and user.rank == Rank.NEWBIE and connected.room is not None:  # noqa: SIM114
            count += 1
        elif connected.auth == "agree":
            count += 1
    return count


def refuse_connection(server: Server, client: Client, message: str) -> None:
    """Show a centered gate file and drop the connection with no logout line."""
    width = frame_width(client.discovered_columns, client.frame_columns)
    server.send(client, refusal_text(message, width))
    server.drop(client, announce=False)


def unsaved_holder_may_quit(client: Client) -> bool:
    """Return whether ``quit`` disconnects instead of being treated as a password."""
    user = client.player
    if user is not None and user.rank == Rank.NEWBIE:
        return True
    return user is None and client.auth in ("set", "confirm", "agree")


def run_named(server: Server, client: Client, word: str) -> None:
    """Run the command registered as ``word`` with no extra text."""
    command = by_name()[word]
    command.function(server, client, command, None)


def current_prompt(client: Client) -> str:
    """Return the prompt this connection is waiting on."""
    auth = client.auth
    if auth in ("password", "on_duty_password", "retire_password"):
        return password_prompt()
    if auth in ("set", "renew"):
        return set_password_prompt()
    if auth in ("confirm", "renew_confirm"):
        return confirm_password_prompt()
    if auth in ("current", "mfa_pw", "mfa_remove_pw"):
        return current_password_prompt()
    if auth in ("code", "mfa_enroll", "mfa_show", "mfa_remove_code"):
        return code_prompt()
    if auth == "screenlock":
        return unlock_prompt()
    if auth == "agree":
        return AGREE_PROMPT
    if auth == "retire_confirm":
        return "Are you sure you want to retire?\n"
    if auth == "mfa_sure":
        return f"Are you sure you want to remove MFA for {client.pending_name}?\n"
    if client.player is None:
        return name_prompt()
    return player_prompt(client)


def reject_long_line(server: Server, client: Client) -> None:
    """Discard a line over the limit and leave the connection at its prompt."""
    text = f"Input is too long.\n{current_prompt(client)}"
    if client.player is not None and client.player.screen_locked:
        send_lock_line(server, client, text)
        return
    server.send(client, text)


def process_input(server: Server, client: Client, line: str) -> None:
    """Login, or run a command. The connection loop does not interpret the line."""
    if client.player is None or client.auth in ("set", "confirm", "code", "mfa_enroll", "mfa_pw", "mfa_show", "mfa_remove_pw", "mfa_remove_code", "mfa_sure", "current", "renew", "renew_confirm", "retire_confirm", "retire_password", "on_duty_password", "screenlock"):  # fmt: skip  # noqa: E501
        process_login(server, client, line)
        return
    process_commands(server, client, line)
    if client.auth in ("current", "renew", "renew_confirm", "retire_confirm", "retire_password", "mfa_pw", "mfa_remove_pw", "mfa_remove_code", "mfa_sure", "on_duty_password", "screenlock"):  # fmt: skip  # noqa: E501
        return
    server.send(client, player_prompt(client))


def process_login(server: Server, client: Client, line: str) -> None:
    """Take one login line for a player who is not in a room yet."""
    if client.auth in ("code", "mfa_enroll") and line.strip().lower() == "quit":
        run_named(server, client, "quit")
        return
    if client.auth != "name" and line == "quit" and unsaved_holder_may_quit(client):
        if client.auth == "agree" and client.pending_name:
            announce_newbie_quit(server, client.pending_name)
        run_named(server, client, "quit")
        return
    if client.auth == "password":
        process_password(server, client, line)
    elif client.auth in ("code", "mfa_enroll"):
        process_code(server, client, line)
    elif client.auth == "set":
        process_set_password(server, client, line)
    elif client.auth == "confirm":
        process_confirm_password(server, client, line)
    elif client.auth == "current":
        process_current(server, client, line)
    elif client.auth == "renew":
        process_renew(server, client, line)
    elif client.auth == "renew_confirm":
        process_renew_confirm(server, client, line)
    elif client.auth == "retire_confirm":
        process_retire_confirm(server, client, line)
    elif client.auth == "retire_password":
        process_retire_password(server, client, line)
    elif client.auth == "on_duty_password":
        process_on_duty_password(server, client, line)
    elif client.auth == "screenlock":
        process_screenlock_password(server, client, line)
    elif client.auth == "mfa_pw":
        process_mfa_pw(server, client, line)
    elif client.auth == "mfa_show":
        process_mfa_show(server, client, line)
    elif client.auth == "mfa_remove_pw":
        process_mfa_remove_pw(server, client, line)
    elif client.auth == "mfa_remove_code":
        process_mfa_remove_code(server, client, line)
    elif client.auth == "mfa_sure":
        process_mfa_sure(server, client, line)
    elif client.auth == "agree":
        process_agree(server, client, line)
    else:
        process_name(server, client, line)


def process_name(server: Server, client: Client, line: str) -> None:
    """Accept a name, ask for a password when it is saved, or refuse it."""
    lowered = line.strip().lower()
    if lowered == "who":
        run_named(server, client, "who")
        server.send(client, NAME_PROMPT)
        return
    if lowered == "quit":
        run_named(server, client, "quit")
        return
    decision = consider_name(line, set())
    if decision.name is None:
        if decision.reply:
            server.send(client, decision.reply)
        return
    name = decision.name
    if house_path_blocked(server.config.rooms, server.config.help, name):
        server.send(client, f"{name} cannot be used. Pick another name.\n\n{NAME_PROMPT}")
        return
    try:
        saved = find_saved(server, name)
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
        return
    held = saved
    online = held is not None and find_player(server, client, held.name, quiet=True) is not None
    exempt = held is not None and (rank_allows(held.rank, Rank.ADMIN) or online)
    if held is not None and exempt:
        ask(client, "password", held.name, held)
        server.send(client, password_prompt())
        return
    if held is None and name.lower() in server.unsaved_names(skip=client):
        server.send(client, name_taken_reply(name))
        return
    if open_connections(server) > server.config.max_connections:
        refuse_connection(server, client, server.full_message)
        return
    if held is None and newbie_pressure(server, client) >= server.config.max_newbies:
        refuse_connection(server, client, server.newbie_message)
        return
    if held is not None:
        ask(client, "password", held.name, held)
        server.send(client, password_prompt())
        return
    if not server.allow_newbies or not server.allow_residents:
        server.send(client, f"The talker is closed to new people.\n{NAME_PROMPT}")
        return
    show_disclaimer(server, client, name)


def show_disclaimer(server: Server, client: Client, name: str) -> None:
    """Show the disclaimer, or drop the newbie when that topic is missing."""
    announce_newbie_connecting(server, client, name)
    entry = server.help.find("disclaimer")
    if entry is None:
        server.send(client, "The disclaimer is missing.\n")
        broadcast_admin(server, "[ Admin ] The disclaimer is missing.\n")
        server.drop(client, announce=False)
        return
    ask(client, "agree", name, None)
    width = frame_width(client.discovered_columns, client.frame_columns)
    visible = frozenset(item.name for item in server.help.entries)
    page = format_page(entry, None, width, visible=visible)
    server.send(client, f"\n\n{page}\n")
    server.send(client, AGREE_PROMPT)


def process_agree(server: Server, client: Client, line: str) -> None:
    """Enter on yes. No leaves. Any other line asks again."""
    word = line.strip().lower()
    if word == "no":
        announce_newbie_quit(server, client.pending_name)
        run_named(server, client, "quit")
        return
    if word != "yes":
        server.send(client, AGREE_PROMPT)
        return
    if not server.allow_newbies or not server.allow_residents:
        restart_login(client)
        server.send(client, f"The talker is closed to new people.\n{NAME_PROMPT}")
        return
    enter(server, client, client.pending_name, Rank.NEWBIE)
