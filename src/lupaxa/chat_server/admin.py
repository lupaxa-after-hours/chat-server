"""Commands only the admin can use."""

from __future__ import annotations

from dataclasses import replace

from .broadcast import broadcast_admin, broadcast_staff
from .client import Client
from .colour import paint_admin, paint_staff
from .commands import Command, shown_syntax
from .country import address_text, ipinfo_fields, is_local_address, is_private_address, normalised_address, read_ipinfo
from .exceptions import ConfigError
from .globals import CANNOT_NOW, COMMANDS
from .player import find_player, is_logged_in, match_online_player
from .ranks import Rank, rank_allows, rank_label
from .roles import ROLE_LABELS, ROLE_WORDS, appoint_to_role, dismiss_from_role, role_from_name
from .saved_player import SavedPlayer, has_set_mfa, save_player
from .server import Server
from .utilities import examine_frame, find_saved, frame_width, get_clients, get_parts, get_player_name, get_saved_player, has_rank, lookup_hostname, refuses_self, saved_players


def command_au(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Speak on the admin channel."""
    admin_channel_wrapper(server, client, f"You say '{text}'", f"{get_player_name(client)} says '{text}'")


def command_ae(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Emote on the admin channel."""
    admin_channel_wrapper(server, client, f"You emote: {text}", f"{get_player_name(client)} {text}")


def command_at(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Think on the admin channel."""
    admin_channel_wrapper(server, client, f"You think . o O ( {text} )", f"{get_player_name(client)} thinks . o O ( {text} )")


def command_off_list(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Hide this admin from the public staff list until on_list."""
    user = client.player
    reason = (text or "").strip()
    if user is None or reason == "":
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    if user.off_list:
        server.send(client, "You are already off list.\n")
        return
    broadcast_staff(server, f"< SU > {user.name} has gone off list because '{reason}'.\n")
    user.off_list = True


def command_on_list(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Show this admin on the public staff list again."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    user = client.player
    if user is None:
        return
    if not user.off_list:
        server.send(client, "You are already on list.\n")
        return
    user.off_list = False
    broadcast_staff(server, f"< SU > {user.name} has gone back on list.\n")


def admin_channel_wrapper(server: Server, client: Client, mine: str, theirs: str) -> None:
    """Send the sender ``mine`` and the rest of the admins ``theirs``."""
    if not server.send(client, paint_admin(client, f"[ Admin ] {mine}\n")):
        return
    broadcast_admin(server, f"[ Admin ] {theirs}\n", skip=client)


def command_allow(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Open or close newbies or residents. The config file stays as it was."""
    parts = get_parts(text, split=True)
    if len(parts) != 2 or parts[0] not in ("newbies", "residents"):
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    if parts[1] not in ("on", "off"):
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    opened = parts[1] == "on"
    if parts[0] == "newbies":
        server.allow_newbies = opened
    else:
        server.allow_residents = opened
    verb = "opens" if opened else "closes"
    who = get_player_name(client)
    broadcast_staff(server, f"< SU > {who} {verb} the talker to {parts[0]}.\n")


LADDER = (Rank.RESIDENT, Rank.SU_CHANNEL, Rank.SUPER_USER, Rank.ADMIN, Rank.CODER)


def next_rank(rank: Rank) -> Rank | None:
    """Return the next saved rung, or None at coder."""
    if rank not in LADDER or rank is Rank.CODER:
        return None
    return LADDER[LADDER.index(rank) + 1]


def previous_rank(rank: Rank) -> Rank | None:
    """Return the previous saved rung, or None at resident."""
    if rank not in LADDER or rank is Rank.RESIDENT:
        return None
    return LADDER[LADDER.index(rank) - 1]


def coder_step(source: Rank, destination: Rank) -> bool:
    """Return whether this step moves someone onto or off Coder."""
    return source is Rank.CODER or destination is Rank.CODER


def admin_step(source: Rank, destination: Rank) -> bool:
    """Return whether this step touches Admin and does not touch Coder."""
    if coder_step(source, destination):
        return False
    return source is Rank.ADMIN or destination is Rank.ADMIN


def command_promote(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Move a connected player up one saved rank."""
    del command
    name = get_parts(text)
    if refuses_self(server, client, name):
        return
    target = find_player(server, client, name)
    if target is None or target.player is None:
        return
    player = target.player
    if player.rank is Rank.NEWBIE:
        server.send(client, f"{player.name} is not a resident.\n")
        return
    if player.rank is Rank.SUPER_ADMIN:
        server.send(client, f"{player.name} is the super admin.\n")
        return
    if player.rank is Rank.CODER:
        server.send(client, f"{player.name} is already a coder.\n")
        return
    destination = next_rank(player.rank)
    if destination is None:
        server.send(client, f"{player.name} cannot be promoted.\n")
        return
    found, saved = get_saved_player(server, client, player.name)
    if not found or saved is None:
        return
    if server.mfa_enabled and not has_set_mfa(saved):
        server.send(client, f"{player.name} must turn on MFA first.\n")
        return
    caller = has_rank(client)
    if coder_step(player.rank, destination) and caller is not Rank.SUPER_ADMIN:
        server.send(client, f"Only the super admin can promote {player.name} to {rank_label(destination)}.\n")
        return
    if admin_step(player.rank, destination) and caller not in (Rank.CODER, Rank.SUPER_ADMIN):
        server.send(client, f"Only a coder or the super admin can promote {player.name} to {rank_label(destination)}.\n")
        return
    if not set_rank(server, client, saved, destination):
        return
    line = f"{get_player_name(client)} promotes {player.name} to {rank_label(destination)}.\n"
    broadcast_staff(server, f"< SU > {line}")


def command_demote(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Move a saved player down one rank. ``force`` drops them to resident."""
    parts = get_parts(text, split=True)
    if len(parts) == 1:
        name, force = parts[0], False
    elif len(parts) == 2 and parts[1].lower() == "force":
        name, force = parts[0], True
    else:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    if refuses_self(server, client, name):
        return
    found, saved = get_saved_player(server, client, name, quiet=True)
    if not found:
        server.send(client, CANNOT_NOW)
        return
    if saved is not None and saved.rank is Rank.SUPER_ADMIN:
        server.send(client, "The super admin cannot be demoted.\n")
        return
    connected = find_player(server, client, name, quiet=True)
    if saved is None and connected is None:
        server.send(client, f"Player {name} not found.\n")
        return
    if saved is None or saved.rank not in LADDER or saved.rank is Rank.RESIDENT:
        if saved is not None and saved.rank is Rank.RESIDENT:
            server.send(client, f"{saved.name} is a resident.\n")
            return
        shown = connected.player.name if connected is not None and connected.player is not None else name
        server.send(client, f"{shown} cannot be demoted.\n")
        return
    destination = Rank.RESIDENT if force else previous_rank(saved.rank)
    if destination is None:
        server.send(client, f"{saved.name} is a resident.\n")
        return
    caller = has_rank(client)
    if coder_step(saved.rank, destination) and caller is not Rank.SUPER_ADMIN:
        server.send(client, f"Only the super admin can demote {saved.name} from {rank_label(saved.rank)}.\n")
        return
    if admin_step(saved.rank, destination) and caller not in (Rank.CODER, Rank.SUPER_ADMIN):
        server.send(client, f"Only a coder or the super admin can demote {saved.name} from {rank_label(saved.rank)}.\n")
        return
    source = saved.rank
    if not set_rank(server, client, saved, destination):
        return
    body = f"{get_player_name(client)} demotes {saved.name} from {rank_label(source)}.\n"
    broadcast_staff(server, f"< SU > {body}")


def command_ipinfo(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Show the country-database record for a player or an IP address."""
    parts = get_parts(text, split=True)
    if len(parts) != 1:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    subject = ipinfo_subject(server, client, parts[0])
    if isinstance(subject, str):
        server.send(client, subject)
        return
    title, ip, hostname = subject
    record: object = None
    prefix: int | None = None
    if not is_local_address(ip) and not is_private_address(ip):
        record, prefix = read_ipinfo(country_reader(server), ip)
    fields = ipinfo_fields(ip, hostname, record, prefix)
    width = frame_width(client.discovered_columns, client.frame_columns)
    server.send(client, examine_frame(title, fields, width))


def ipinfo_subject(server: Server, client: Client, token: str) -> tuple[str, str, str] | str:
    """Return a title, address, and hostname, or a line explaining the miss."""
    if token.lower() == "me":
        user = client.player
        if user is None:
            return "Player me not found.\n"
        return user.name, client.address, client.hostname or client.address
    if normalised_address(token) is not None:
        ip = address_text(token)
        return ip, ip, hostname_for(server, ip)
    found, error = match_online_player(get_clients(server), token)
    if error is not None:
        return error
    if found is not None and found.player is not None:
        return found.player.name, found.address, found.hostname or found.address
    from .newbie import match_examine_name

    chosen, name_error = match_examine_name(server, token)
    if name_error is not None:
        return name_error
    if chosen is None:
        return f"Player {token} not found.\n"
    loaded, saved = get_saved_player(server, client, chosen, quiet=True)
    if not loaded:
        return CANNOT_NOW
    if saved is None:
        return f"Player {token} not found.\n"
    if not saved.ip_address:
        return f"{saved.name} has no saved address.\n"
    return saved.name, saved.ip_address, saved.hostname or saved.ip_address


def hostname_for(server: Server, ip: str) -> str:
    """Return a connected player's hostname for ``ip``, or a reverse lookup."""
    wanted = address_text(ip)
    for person in get_clients(server):
        if address_text(person.address) == wanted and person.hostname:
            return person.hostname
    return lookup_hostname(wanted)


def country_reader(server: Server) -> object:
    """Return the open country database, or None when this server has none."""
    ensure = getattr(server, "ensure_country_reader", None)
    if ensure is None:
        return None
    return ensure()


def change_role(server: Server, client: Client, text: str | None, *, grant: bool) -> None:
    """Give or take the role named by the rest of the line."""
    words = get_parts(text, split=True)
    if len(words) == 0:
        body = "\n".join(f"  {word}" for word in ROLE_WORDS.values())
        server.send(client, f"Roles:\n{body}\n")
        return
    syntax = "appoint" if grant else "dismiss"
    if len(words) < 2:
        server.send(client, f"Syntax: {syntax} <name> <role>\n")
        return
    name = words[0]
    role = role_from_name(" ".join(words[1:]))
    if role is None:
        server.send(client, f"There is no role '{' '.join(words[1:])}'.\n")
        return
    try:
        saved = find_saved(server, name)
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, CANNOT_NOW)
        return
    if saved is None:
        server.send(client, f"No such player '{name}'.\n")
        return
    caller = get_player_name(client)
    if saved.rank == Rank.SUPER_ADMIN and saved.name.casefold() != caller.casefold():
        server.send(client, "Roles cannot be changed for the super admin.\n")
        return
    held = (saved.roles & int(role)) != 0
    label = ROLE_LABELS[role]
    if grant and held:
        server.send(client, f"{saved.name} already has the {label} role.\n")
        return
    if not grant and not held:
        server.send(client, f"{saved.name} does not have the {label} role.\n")
        return
    updated = appoint_to_role(saved.roles, role) if grant else dismiss_from_role(saved.roles, role)
    try:
        save_player(server.roster_dir, replace(saved, roles=updated))
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, CANNOT_NOW)
        return
    target = find_player(server, client, saved.name, quiet=True)
    if target is not None and target.player is not None:
        target.player.roles = updated
    line = f"< SU > {caller} assigns {saved.name} to the role of '{label}'\n" if grant else f"< SU > {caller} dismisses {saved.name} from the role of '{label}'.\n"
    broadcast_staff(server, line)
    if target is None or target.player is None:
        return
    if rank_allows(target.player.rank, Rank.SU_CHANNEL) and not target.player.off_duty:
        return
    server.send(target, paint_staff(target, line))


def command_appoint(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Give a saved player the role named on the line."""
    del command
    change_role(server, client, text, grant=True)


def command_dismiss(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Take the role named on the line from a saved player."""
    del command
    change_role(server, client, text, grant=False)


def set_rank(server: Server, client: Client, saved: SavedPlayer, rank: Rank) -> bool:
    """Write ``rank`` to the file of ``saved`` and to the live user, when connected."""
    try:
        save_player(server.roster_dir, replace(saved, rank=rank, unknown_rank=None))
    except (ConfigError, OSError) as exc:
        print(exc)
        server.send(client, CANNOT_NOW)
        return False
    target = find_player(server, client, saved.name, quiet=True)
    if target is not None and target.player is not None:
        target.player.rank = rank
    return True


RANK_PAIRS = (
    ("Residents", Rank.RESIDENT, "Channel Only", Rank.SU_CHANNEL),
    ("Super Users", Rank.SUPER_USER, "Admins", Rank.ADMIN),
    ("Coders", Rank.CODER, "Super Admin", Rank.SUPER_ADMIN),
)


def pstats_box(server: Server) -> str:
    """Return the player-count box, ending with a newline."""
    saved = saved_players(server)
    newbies = 0
    for client in get_clients(server):
        user = client.player
        if is_logged_in(client) and user is not None and user.rank is Rank.NEWBIE:
            newbies += 1
    by_rank: dict[Rank, int] = {rank: 0 for rank in Rank if rank is not Rank.NEWBIE}
    for player in saved:
        by_rank[player.rank] = by_rank.get(player.rank, 0) + 1
    pairs: list[tuple[tuple[str, int], tuple[str, int] | None]] = [(("Newbies", newbies), ("Total Residents", len(saved)))]
    for left_label, left_rank, right_label, right_rank in RANK_PAIRS:
        pairs.append(((left_label, by_rank[left_rank]), (right_label, by_rank[right_rank])))
    role_counts = sorted(((label, sum(1 for player in saved if player.roles & int(role))) for role, label in ROLE_LABELS.items()), key=lambda item: item[0].casefold())
    role_rows: list[tuple[tuple[str, int], tuple[str, int] | None]] = []
    for index in range(0, len(role_counts), 2):
        right = role_counts[index + 1] if index + 1 < len(role_counts) else None
        role_rows.append((role_counts[index], right))
    left_labels = [left[0] for left, _ in pairs] + [left[0] for left, _ in role_rows]
    right_labels = [right[0] for _, right in (*pairs, *role_rows) if right is not None]
    left_width = max(len(label) for label in left_labels)
    right_width = max(len(label) for label in right_labels)
    left_counts = [left[1] for left, _ in pairs] + [left[1] for left, _ in role_rows]
    right_counts = [right[1] for _, right in (*pairs, *role_rows) if right is not None]
    left_count_width = max(len(str(count)) for count in left_counts)
    right_count_width = max(len(str(count)) for count in right_counts)

    def row(left: tuple[str, int], right: tuple[str, int] | None) -> str:
        text = f" {left[0].ljust(left_width)} : {str(left[1]).ljust(left_count_width)}"
        if right is not None:
            text += f"   {right[0].ljust(right_width)} : {str(right[1]).ljust(right_count_width)}"
        return text

    lines: list[str | None] = [row(*pairs[0]), None]
    lines.extend(row(*pair) for pair in pairs[1:])
    if role_rows:
        lines.append(None)
        lines.extend(row(*pair) for pair in role_rows)
    inner = max(len(line) for line in lines if line is not None) + 1
    border = "+" + ("-" * inner) + "+"
    rule = "|" + ("-" * inner) + "|"
    body = [rule if line is None else "|" + line.ljust(inner) + "|" for line in lines]
    return "\n".join((border, *body, border)) + "\n"


def command_pstats(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Show logged-in newbies, saved ranks, and saved role counts."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    server.send(client, pstats_box(server))


COMMANDS.extend(
    (
        Command("allow", command_allow, False, "newbies|residents on|off", rank=Rank.ADMIN),
        Command("promote", command_promote, False, "<name>", rank=Rank.ADMIN),
        Command("pstats", command_pstats, False, None, rank=Rank.ADMIN),
        Command("appoint", command_appoint, False, "<name> <role>", rank=Rank.ADMIN),
        Command("dismiss", command_dismiss, False, "<name> <role>", rank=Rank.ADMIN),
        Command("demote", command_demote, False, "<name> [force]", rank=Rank.ADMIN),
        Command("ae", command_ae, False, "<message>", rank=Rank.ADMIN),
        Command("at", command_at, False, "<message>", rank=Rank.ADMIN),
        Command("au", command_au, False, "<message>", rank=Rank.ADMIN),
        Command("off_list", command_off_list, False, "<reason>", rank=Rank.ADMIN),
        Command("on_list", command_on_list, False, None, rank=Rank.ADMIN),
        Command("ipinfo", command_ipinfo, False, "<player>|<address>", rank=Rank.ADMIN),
    )
)
