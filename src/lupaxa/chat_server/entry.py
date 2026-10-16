"""Hold a name during a password prompt, and put a player in the start room."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from .broadcast import broadcast_room, broadcast_staff
from .client import Client, send_lock_line
from .commands import held_roles
from .defaults import DEFAULT_LOGINMSG, DEFAULT_PROMPT
from .exceptions import ConfigError
from .globals import NAME_PROMPT
from .player import Player
from .ranks import Rank, rank_allows
from .roles import Roles, join_names
from .rooms import full_line, house_path, house_path_blocked, house_room_id, is_full, load_house, others, spoken_line, system_room_ids
from .saved_player import SavedPlayer, save_player, super_admins
from .server import Server
from .utilities import find_player, find_saved, get_clients, reconnected_line


def name_taken_reply(name: str) -> str:
    """Return the line that sends a duplicate name back to the name prompt."""
    return f"{name} is already in use. Pick another name.\n\n{NAME_PROMPT}"


def player_prompt(client: Client) -> str:
    """Return the prompt this player types at, with no trailing newline."""
    if client.player is None:
        return DEFAULT_PROMPT
    prompt = client.player.prompt
    if prompt.endswith(" "):
        return prompt
    return prompt + " "


def ask(client: Client, stage: str, name: str, saved: SavedPlayer | None) -> None:
    """Hold ``name`` while the player answers a password prompt."""
    client.auth = stage
    client.pending_name = name
    client.pending_saved = saved
    client.pending_password = ""
    client.pending_current_password = ""
    client.pending_mfa_secret = ""
    client.pending_issuer = ""
    client.password_failures = 0


def restart_login(client: Client) -> None:
    """Forget the held name and go back to asking for one."""
    ask(client, "name", "", None)


def closed_status(server: Server) -> str | None:
    """Return the staff line for a closed talker, or None when it is open."""
    if not server.allow_residents:
        return "[ Status ] The talker is closed to newbies and residents.\n"
    if not server.allow_newbies:
        return "[ Status ] The talker is closed to newbies.\n"
    return None


NEWBIE_ALERT = "[ Newbie Alert ] "


def announce_login(server: Server, client: Client) -> None:
    """Tell the room the login sentence."""
    user = client.player
    name = "" if user is None else user.name
    sentence = DEFAULT_LOGINMSG if user is None else user.loginmsg
    room = client.room if user is None else user.room
    broadcast_room(server, room, spoken_line(name, sentence), skip=client)


def announce_reconnect(server: Server, client: Client) -> None:
    """Tell the room this name took over a session."""
    user = client.player
    name = "" if user is None else user.name
    room = client.room if user is None else user.room
    broadcast_room(server, room, reconnected_line(name), skip=client)


def announce_newbie_connecting(server: Server, client: Client, name: str) -> None:
    """Tell staff a newbie has typed a free name."""
    host = client.hostname or client.address or "unknown"
    pad = " " * len(NEWBIE_ALERT)
    broadcast_staff(server, f"{NEWBIE_ALERT}AWOOGA AWOOGA!!\n{pad}{name} is connecting from {host}\n", skip=client)


def announce_newbie_quit(server: Server, name: str) -> None:
    """Tell staff a newbie refused the disclaimer."""
    broadcast_staff(server, f"{NEWBIE_ALERT}{name} quit at the disclaimer\n")


def announce_newbie_landed(server: Server, client: Client, name: str) -> None:
    """Tell staff which room a newbie entered."""
    room = "" if client.room is None else client.room
    shown = server.layout.display_name(room) if room else ""
    broadcast_staff(server, f"{NEWBIE_ALERT}{name} is in {shown}.\n", skip=client)


def announce_logout(server: Server, name: str, room: str | None, sentence: str) -> None:
    """Tell the room the logout sentence after the socket is gone."""
    broadcast_room(server, room, spoken_line(name, sentence), skip=None)


def issue_lines(server: Server) -> tuple[str, ...]:
    """Return the site-admin issue lines for extra super admins and obsolete keys."""
    lines: list[str] = []
    try:
        found = super_admins(server.roster_dir)
    except (ConfigError, OSError) as exc:
        print(exc)
        found = ()
    if len(found) > 1:
        names = join_names(player.name for player in found)
        lines.append(f"[ Issue Located ] There are multi super-admins '{names}' - this needs to be resolved before the next restart.\n")
    if server.config.obsolete_hot:
        keys = join_names(server.config.obsolete_hot)
        lines.append(f"[ Issue Located ] Obsolete keys found in {server.config.hot_path.name}: {keys}. Please remove them.\n")
    if server.config.obsolete_cold:
        keys = join_names(server.config.obsolete_cold)
        lines.append(f"[ Issue Located ] Obsolete keys found in {server.config.cold_path.name}: {keys}. Please remove them.\n")
    return tuple(lines)


def warn_site_admin(server: Server, client: Client) -> None:
    """Tell this site admin about extra super admins and obsolete config keys."""
    if (held_roles(client) & int(Roles.SITE_ADMIN)) == 0:
        return
    for line in issue_lines(server):
        send_lock_line(server, client, line)


def warn_connected_site_admins(server: Server) -> None:
    """Tell every connected site admin about extra super admins and obsolete config keys."""
    for person in list(get_clients(server)):
        warn_site_admin(server, person)


def settle_unknown_rank(server: Server, client: Client) -> None:
    """Rewrite an unknown file rank to resident and tell the site admins."""
    user = client.player
    if user is None:
        return
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        return
    if saved is None or saved.unknown_rank is None:
        return
    word = saved.unknown_rank
    try:
        save_player(server.roster_dir, replace(saved, rank=Rank.RESIDENT, unknown_rank=None))
    except (ConfigError, OSError) as exc:
        print(exc)
        return
    user.rank = Rank.RESIDENT
    server.send(client, f"You logged in with an unknown rank '{word}' and have been auto-demoted to 'resident'.\n")
    line = f"[ Issue Located ] {saved.name} logged in with an unknown rank '{word}' and has been auto-demoted to 'resident'.\n"
    for person in get_clients(server):
        if (held_roles(person) & int(Roles.SITE_ADMIN)) == 0:
            continue
        server.send(person, line)


def enter(server: Server, client: Client, name: str, rank: Rank) -> None:
    """Put a newbie in newbie_start, others in home or start, or refuse when full."""
    taken = {item.lower() for item in server.taken_names()}
    if name.lower() in server.unsaved_names(skip=client) or name.lower() in taken:
        restart_login(client)
        server.send(client, name_taken_reply(name))
        return
    if rank == Rank.NEWBIE:
        start = server.config.newbie_start or server.config.start
    else:
        start = server.config.start
        try:
            chosen = find_saved(server, name)
        except (ConfigError, OSError):
            chosen = None
        if chosen is not None and chosen.home and chosen.home_login and not house_path_blocked(server.config.rooms, server.config.help, chosen.name):
            try:
                rooms = load_house(house_path(server.config.rooms, chosen.name), chosen.name, system_room_ids(server.layout))
            except (ConfigError, OSError):
                rooms = None
            home_id = house_room_id(chosen.name)
            if rooms is not None and home_id in rooms:
                server.layout = server.layout.with_house(chosen.name, rooms)
                start = home_id
    if is_full(server.layout, get_clients(server), start):
        server.send(client, full_line(server.layout.display_name(start)))
        server.drop(client, announce=False)
        return
    restart_login(client)
    newbie_text = "I am new here"
    description = newbie_text if rank == Rank.NEWBIE else ""
    client.player = Player(name, start, datetime.now(timezone.utc), rank=rank, description=description)
    client.room = start
    load_profile(server, client.player)
    sync_country(client)
    remember_address(server, client)
    announce_login(server, client)
    if rank == Rank.NEWBIE:
        announce_newbie_landed(server, client, name)
    brief = server.layout.brief(start, others(get_clients(server), start, client))
    if server.send(client, brief):
        user = client.player
        if user is not None and rank_allows(user.rank, Rank.SU_CHANNEL):
            status = closed_status(server)
            if status is not None:
                server.send(client, status)
        warn_site_admin(server, client)
        settle_unknown_rank(server, client)
        server.send(client, player_prompt(client))


def enter_fresh(server: Server, client: Client, held: SavedPlayer) -> None:
    """Enter with the rank in the file now, or go back to the name prompt when it is gone."""
    try:
        fresh = find_saved(server, held.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        restart_login(client)
        server.send(client, f"That name cannot be used right now.\n{NAME_PROMPT}")
        return
    if fresh is None:
        restart_login(client)
        server.send(client, NAME_PROMPT)
        return
    live = find_player(server, client, fresh.name, quiet=True)
    if live is not None:
        reconnect(server, live, client, fresh)
        return
    enter(server, client, fresh.name, fresh.rank)


def reconnect(server: Server, live: Client, newcomer: Client, fresh: SavedPlayer) -> None:
    """Keep ``live`` and attach ``newcomer``'s socket to it."""
    user = live.player
    if user is None:
        enter(server, newcomer, fresh.name, fresh.rank)
        return
    user.idle_seconds = 0
    user.rank = fresh.rank
    server.replace_socket(live, newcomer)
    if live.auth in ("current", "renew", "renew_confirm"):
        restart_login(live)
    remember_address(server, live)
    announce_reconnect(server, live)
    brief = server.layout.brief(user.room, others(get_clients(server), user.room, live))
    if server.send(live, brief):
        user = live.player
        if user is not None and rank_allows(user.rank, Rank.SU_CHANNEL):
            status = closed_status(server)
            if status is not None:
                server.send(live, status)
        settle_unknown_rank(server, live)
        server.send(live, player_prompt(live))


def load_profile(server: Server, user: Player) -> None:
    """Start a saved player's total and prompt from the file. A newbie keeps the defaults."""
    if user.rank == Rank.NEWBIE:
        return
    try:
        saved = find_saved(server, user.name)
    except (ConfigError, OSError) as exc:
        print(exc)
        return
    if saved is None:
        return
    user.connected_seconds = saved.connected_seconds
    user.flushed_seconds = saved.connected_seconds
    if saved.prompt is not None:
        user.prompt = saved.prompt
    if saved.entermsg is not None:
        user.entermsg = saved.entermsg
    if saved.loginmsg is not None:
        user.loginmsg = saved.loginmsg
    if saved.logoutmsg is not None:
        user.logoutmsg = saved.logoutmsg
    user.colour = saved.colour
    user.description = saved.description
    user.plan = saved.plan
    user.country_name = saved.country_name
    user.country_code = saved.country_code
    user.roles = saved.roles


def sync_country(client: Client) -> None:
    """Keep the live player and this connection on the same country."""
    user = client.player
    if user is None:
        return
    if client.country_name:
        user.country_name = client.country_name
        user.country_code = client.country_code
        return
    client.country_name = user.country_name
    client.country_code = user.country_code


def remember_address(server: Server, client: Client) -> None:
    """Save where a saved player connected from, when it differs from the file."""
    user = client.player
    if user is None or user.rank == Rank.NEWBIE:
        return
    try:
        saved = find_saved(server, user.name)
        if saved is None:
            return
        country_name = client.country_name or saved.country_name
        country_code = client.country_code or saved.country_code
        if saved.ip_address == client.address and saved.hostname == client.hostname and saved.country_name == country_name and saved.country_code == country_code:
            return
        save_player(server.roster_dir, replace(saved, ip_address=client.address, hostname=client.hostname, country_name=country_name, country_code=country_code))
    except (ConfigError, OSError) as exc:
        print(exc)
