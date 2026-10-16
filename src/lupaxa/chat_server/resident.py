"""Commands used by residents."""

from __future__ import annotations

from dataclasses import replace

from .broadcast import broadcast_room, broadcast_shout, broadcast_staff
from .client import Client
from .colour import cls
from .commands import Command, register_subcommands, shown_syntax
from .defaults import DEFAULT_ENTERMSG, DEFAULT_LOGINMSG, DEFAULT_LOGOUTMSG, DEFAULT_PROMPT
from .entry import player_prompt
from .exceptions import ConfigError
from .globals import CANNOT_NOW, COMMANDS
from .password import send_lock_line, unlock_prompt
from .player import Player
from .ranks import Rank, rank_allows
from .roles import role_names
from .rooms import house_path, house_path_blocked, house_room_id, load_house, others, release_empty_houses, spoken_line, system_room_ids, write_default_house
from .saved_player import SavedPlayer, save_player
from .server import Server
from .utilities import find_player, find_saved, get_clients, get_parts, is_admin


def command_shout(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Send a spoken line to every player who is in a room."""
    if not server.send(client, f"You shout '{text}'\n"):
        return
    broadcast_shout(server, f"! {client.name} shouts '{text}'", skip=client)


def command_prompt(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Replace the line this player types at."""
    chosen = get_parts(text)
    if not chosen.endswith(" "):
        chosen += " "
    user = client.player
    if user is None:
        return
    user.prompt = chosen
    keep_prompt(server, user, chosen)
    server.send(client, "Prompt set.\n")


def command_entermsg(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Set or clear the sentence people hear when this player enters."""
    store_room_sentence(server, client, text, field="entermsg", default=DEFAULT_ENTERMSG, label="Enter", too_long="An enter message is at most 80 characters.\n")


def command_loginmsg(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Set or clear the sentence people hear when this player logs in."""
    store_room_sentence(server, client, text, field="loginmsg", default=DEFAULT_LOGINMSG, label="Login", too_long="A login message is at most 80 characters.\n")


def command_logoutmsg(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Set or clear the sentence people hear when this player logs out."""
    store_room_sentence(server, client, text, field="logoutmsg", default=DEFAULT_LOGOUTMSG, label="Logout", too_long="A logout message is at most 80 characters.\n")


def store_room_sentence(server: Server, client: Client, text: str | None, *, field: str, default: str, label: str, too_long: str) -> None:
    """Store one room sentence on the live player and on the player file."""
    user = client.player
    if user is None:
        return
    chosen = get_parts(text)
    if chosen == "":
        setattr(user, field, default)
        keep_room_sentence(server, user, field, None)
        server.send(client, f"{label} message cleared.\n")
        return
    if len(chosen) > 80:
        server.send(client, too_long)
        return
    setattr(user, field, chosen)
    kept = None if chosen == default else chosen
    keep_room_sentence(server, user, field, kept)
    server.send(client, f"{label} message set.\n")


def command_save(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Write this resident's live profile to the player file."""
    if get_parts(text) != "":
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    user = client.player
    if user is None:
        return
    try:
        saved = find_saved(server, user.name)
        if saved is None:
            raise ConfigError("No saved player.")
        kept_prompt = None if user.prompt == DEFAULT_PROMPT else user.prompt
        kept_entermsg = None if user.entermsg == DEFAULT_ENTERMSG else user.entermsg
        kept_loginmsg = None if user.loginmsg == DEFAULT_LOGINMSG else user.loginmsg
        kept_logoutmsg = None if user.logoutmsg == DEFAULT_LOGOUTMSG else user.logoutmsg
        country_name = user.country_name or saved.country_name
        country_code = user.country_code or saved.country_code
        save_player(server.roster_dir, replace(saved, description=user.description, plan=user.plan, colour=user.colour, prompt=kept_prompt, entermsg=kept_entermsg, loginmsg=kept_loginmsg, logoutmsg=kept_logoutmsg, connected_seconds=user.connected_seconds, country_name=country_name, country_code=country_code))  # fmt: skip  # noqa: E501
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, CANNOT_NOW)
        return
    server.send(client, "Player Saved.\n")


def command_retire(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Ask for confirmation and a password before deleting this resident's file."""
    if get_parts(text) != "":
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    user = client.player
    if user is None:
        return
    try:
        if is_admin(server, user.name):
            server.send(client, "The super admin cannot be removed.\n")
            return
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, CANNOT_NOW)
        return
    client.auth = "retire_confirm"
    server.send(client, "Are you sure you want to retire?\n")


def command_recap(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Change this player's display spelling without changing the key."""
    user = client.player
    if user is None:
        return
    chosen = get_parts(text)
    if chosen.lower() != user.lowercase_name:
        server.send(client, "You can only change the capitalisation of your name.\n")
        return
    try:
        saved = find_saved(server, user.lowercase_name)
        if saved is not None:
            save_player(server.roster_dir, replace(saved, name=chosen))
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, CANNOT_NOW)
        return
    user.name = chosen
    server.send(client, f"Name set to {chosen}.\n")


def command_alarm(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Set an alarm for a whole number of minutes."""
    minutes_text = get_parts(text)
    if not (minutes_text.isascii() and minutes_text.isdigit()) or int(minutes_text) < 1:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    user = client.player
    if user is None:
        return
    minutes = int(minutes_text)
    user.alarm_seconds = minutes * 60
    unit = "minute" if minutes == 1 else "minutes"
    server.send(client, f"Alarm set for {minutes} {unit}.\n")


def command_plan(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Set or clear the one line shown on a logged-out examine."""
    chosen = get_parts(text)
    user = client.player
    if user is None:
        return
    if chosen == "":
        user.plan = ""
        keep_plan(server, user)
        server.send(client, "Plan cleared.\n")
        return
    if len(chosen) > 80:
        server.send(client, "A plan is at most 80 characters.\n")
        return
    user.plan = chosen
    keep_plan(server, user)
    server.send(client, "Plan set.\n")


def keep_plan(server: Server, user: Player) -> None:
    """Write the live plan onto an existing player file."""
    try:
        saved = find_saved(server, user.name)
        if saved is None:
            return
        save_player(server.roster_dir, replace(saved, plan=user.plan))
    except (ConfigError, OSError) as exc:
        print(exc)


def command_description(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Set or clear the one line shown on examine."""
    chosen = get_parts(text)
    user = client.player
    if user is None:
        return
    if chosen == "":
        user.description = ""
        keep_description(server, user)
        server.send(client, "Description cleared.\n")
        return
    if len(chosen) > 80:
        server.send(client, "A description is at most 80 characters.\n")
        return
    user.description = chosen
    keep_description(server, user)
    server.send(client, "Description set.\n")


def keep_description(server: Server, user: Player) -> None:
    """Write the live description onto an existing player file."""
    try:
        saved = find_saved(server, user.name)
        if saved is None:
            return
        save_player(server.roster_dir, replace(saved, description=user.description))
    except (ConfigError, OSError) as exc:
        print(exc)


def command_status(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Set or clear the session status shown on examine and player lists."""
    chosen = get_parts(text)
    user = client.player
    if user is None:
        return
    if chosen == "":
        user.status = ""
        server.send(client, "Status cleared.\n")
        return
    if len(chosen) > 20:
        server.send(client, "A status is at most 20 characters.\n")
        return
    user.status = chosen
    server.send(client, "Status set.\n")


def keep_prompt(server: Server, user: Player, chosen: str) -> None:
    """Write ``chosen`` to the saved file. The usual prompt removes the saved one."""
    try:
        saved = find_saved(server, user.name)
        if saved is None:
            return
        kept = None if chosen == DEFAULT_PROMPT else chosen
        save_player(server.roster_dir, replace(saved, prompt=kept))
    except (ConfigError, OSError) as exc:
        print(exc)


def keep_room_sentence(server: Server, user: Player, field: str, chosen: str | None) -> None:
    """Write one room sentence onto the player file. None removes the key."""
    try:
        saved = find_saved(server, user.name)
        if saved is None:
            return
        if field == "entermsg":
            updated = replace(saved, entermsg=chosen)
        elif field == "loginmsg":
            updated = replace(saved, loginmsg=chosen)
        elif field == "logoutmsg":
            updated = replace(saved, logoutmsg=chosen)
        else:
            return
        save_player(server.roster_dir, updated)
    except (ConfigError, OSError) as exc:
        print(exc)


def command_screenlock(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Lock the screen until the saved password is typed."""
    del command
    user = client.player
    if user is None:
        return
    reason = " ".join((text or "").split())
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    if saved is None:
        server.send(client, f"{CANNOT_NOW}{player_prompt(client)}")
        return
    user.screen_lock_reason = reason
    user.screen_locked = True
    client.auth = "screenlock"
    if rank_allows(user.rank, Rank.SU_CHANNEL):
        user.off_duty = True
        because = f" because '{reason}'" if reason else ""
        broadcast_staff(server, f"< SU > {user.name} has locked their screen{because}.\n")
    send_lock_line(server, client, cls())
    send_lock_line(server, client, unlock_prompt())


def command_home(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Move this resident home."""
    if get_parts(text, split=True):
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    go_home(server, client)


def command_login(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Report or set where the next fresh login starts."""
    words = get_parts(text, split=True)
    if words == []:
        report_home_login(server, client)
        return
    if words == ["home"]:
        set_home_login(server, client, True)
        return
    if words == ["main"]:
        set_home_login(server, client, False)
        return
    server.send(client, f"Syntax: {shown_syntax(command)}\n")


def prepare_house(server: Server, name: str) -> str | None:
    """Create a missing house file and report when it cannot be used."""
    path = house_path(server.config.rooms, name)
    if house_path_blocked(server.config.rooms, server.config.help, name):
        return "Cannot read your home.\n"
    try:
        if not path.is_file():
            write_default_house(server.config.rooms, name)
        rooms = load_house(path, name, system_room_ids(server.layout))
    except (ConfigError, OSError) as exc:
        print(exc)
        return "Cannot read your home.\n"
    if house_room_id(name) not in rooms:
        return "Cannot read your home.\n"
    return None


def go_home(server: Server, client: Client) -> None:
    """Create the home if needed and move this resident there."""
    user = client.player
    if user is None:
        return
    saved = read_home(server, client, user.name)
    if saved is None:
        return
    room_id = house_room_id(user.name)
    if client.room == room_id:
        server.send(client, "You are already home.\n")
        return
    error = prepare_house(server, user.name)
    if error is not None:
        server.send(client, error)
        return
    if not saved.home:
        save_player(server.roster_dir, replace(saved, home=True))
        server.send(client, "Your home has been created.\n")
    server.layout = server.layout.with_house(user.name, load_house(house_path(server.config.rooms, user.name), user.name, system_room_ids(server.layout)))
    broadcast_room(server, client.room, spoken_line(user.name, "goes home"), skip=client)
    client.room = room_id
    company = others(get_clients(server), room_id, client)
    server.send(client, server.layout.brief(room_id, company))
    server.layout = release_empty_houses(server.layout, get_clients(server))


def report_home_login(server: Server, client: Client) -> None:
    """Tell this resident where the next fresh login starts."""
    user = client.player
    if user is None:
        return
    saved = read_home(server, client, user.name)
    if saved is None:
        return
    if saved.home_login:
        server.send(client, "You will login to your home room.\n")
        return
    server.send(client, "You will login to the main room.\n")


def set_home_login(server: Server, client: Client, enabled: bool) -> None:
    """Save whether the next fresh login starts at home."""
    user = client.player
    if user is None:
        return
    saved = read_home(server, client, user.name)
    if saved is None:
        return
    if enabled:
        error = prepare_house(server, user.name)
        if error is not None:
            server.send(client, error)
            return
        if not saved.home:
            save_player(server.roster_dir, replace(saved, home=True, home_login=True))
            server.send(client, "Your home has been created. You will login to your home room.\n")
            return
        save_player(server.roster_dir, replace(saved, home_login=True))
        server.send(client, "You will login to your home room.\n")
        return
    save_player(server.roster_dir, replace(saved, home_login=False))
    server.send(client, "You will login to the main room.\n")


def read_home(server: Server, client: Client, name: str) -> SavedPlayer | None:
    """Return the saved player, or tell them the file cannot be used."""
    try:
        saved = find_saved(server, name)
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, CANNOT_NOW)
        return None
    if saved is None:
        server.send(client, CANNOT_NOW)
        return None
    return saved


def command_auto_reply(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Set or clear the line returned to tell and remote senders."""
    chosen = get_parts(text)
    user = client.player
    if user is None:
        return
    if chosen == "":
        user.auto_reply = ""
        server.send(client, "Auto-reply cleared.\n")
        return
    user.auto_reply = chosen
    server.send(client, "Auto-reply set.\n")


def command_roles(server: Server, client: Client, command: Command, text: str | None) -> None:
    """List this player's roles, or another player's when the caller is an admin."""
    words = get_parts(text, split=True)
    user = client.player
    if user is None:
        return
    if len(words) == 0:
        names = role_names(user.roles)
        if not names:
            server.send(client, "You have no roles.\n")
            return
        body = "\n".join(f"  {name}" for name in names)
        server.send(client, f"Roles:\n{body}\n")
        return
    if len(words) != 1:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    if not rank_allows(user.rank, Rank.ADMIN):
        server.send(client, "Only an admin can look up someone else's roles.\n")
        return
    try:
        saved = find_saved(server, words[0])
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, CANNOT_NOW)
        return
    connected = find_player(server, client, words[0], quiet=True)
    live = connected.player if connected is not None else None
    if saved is not None:
        shown = saved.name
        mask = saved.roles
    elif live is not None:
        shown = live.name
        mask = live.roles
    else:
        server.send(client, f"No such player '{words[0]}'.\n")
        return
    names = role_names(mask)
    if not names:
        server.send(client, f"{shown} has no roles.\n")
        return
    body = "\n".join(f"  {name}" for name in names)
    server.send(client, f"{shown}:\n{body}\n")


def command_set(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Parent for set. The dispatcher runs a child instead."""


COMMANDS.extend(
    (
        Command("recap", command_recap, False, "<name>", rank=Rank.RESIDENT),
        Command("alarm", command_alarm, False, "<minutes>", rank=Rank.RESIDENT),
        Command("screenlock", command_screenlock, False, "[<reason>]", rank=Rank.RESIDENT),
        Command("shout", command_shout, False, "<message>", rank=Rank.RESIDENT),
        Command("!", command_shout, True, "<message>", rank=Rank.RESIDENT),
        Command("save", command_save, False, None, rank=Rank.RESIDENT),
        Command("retire", command_retire, False, None, rank=Rank.RESIDENT),
        Command("roles", command_roles, False, "[<name>]", rank=Rank.RESIDENT),
        Command("home", command_home, False, None, rank=Rank.RESIDENT),
        Command("set", command_set, False, "<command>", rank=Rank.RESIDENT, subcommands=()),
    )
)
register_subcommands(
    "set",
    Command("auto_reply", command_auto_reply, False, "<message>", rank=Rank.RESIDENT),
    Command("description", command_description, False, "<description>", rank=Rank.RESIDENT),
    Command("entermsg", command_entermsg, False, "<sentence>", rank=Rank.RESIDENT),
    Command("login", command_login, False, "home|main", rank=Rank.RESIDENT),
    Command("loginmsg", command_loginmsg, False, "<sentence>", rank=Rank.RESIDENT),
    Command("logoutmsg", command_logoutmsg, False, "<sentence>", rank=Rank.RESIDENT),
    Command("plan", command_plan, False, "<plan>", rank=Rank.RESIDENT),
    Command("prompt", command_prompt, False, "<prompt>", rank=Rank.RESIDENT),
    Command("status", command_status, False, "<status>", rank=Rank.RESIDENT),
)
