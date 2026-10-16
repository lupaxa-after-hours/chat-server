"""Commands used by newbies."""

from __future__ import annotations  # noqa: I001

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timezone

from .broadcast import broadcast_room
from .chain import Delivery, chain_number, delete_chain, deliver, leave_chain, listed_names, remove_from_chain, split_target_message, tell_names
from .client import Client
from .colour import can_see_colour, center_block, cls, colour_of, green, paint_idle, plain_len, testcard
from .commands import Command, by_name, command_allowed, held_roles, listed_commands, shown_name, shown_syntax
from .defaults import DEFAULT_ENTERMSG, DEFAULT_LOGINMSG, DEFAULT_LOGOUTMSG
from .exceptions import ConfigError
from .globals import CANNOT_NOW, COMMANDS
from .help import HelpBook, HelpEntry, format_page
from .player import Player, is_logged_in, match_name, match_online_player
from .roles import ROLE_LABELS, ROLE_WORDS, role_from_name, role_names, roles_phrase
from .rooms import VOID_ID, cannot_go_line, full_line, is_full, leave_line, others, release_empty_houses, spoken_line
from .saved_player import SavedPlayer, save_player
from .server import Server
from .time import clock_phrase, connected_phrase, duration_phrase
from .utilities import connected_place, examine_frame, examine_idle, find_player, find_saved, frame_width, get_clients, get_parts, get_saved_player, goes_to_line, has_rank, list_frame, login_place, saved_players, terminal_size_line, terminal_size_value, time_frame, visible_width, width_ceiling, wrap_words  # fmt: skip  # noqa: E501
from .version import get_version
from .ranks import Rank, lsu_rank_cell, rank_allows, shown_person_cell, shown_person_rank, shown_rank


def command_say(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Send a spoken line to the player's room."""
    if not server.send(client, f"You say '{text}'\n"):
        return
    broadcast_room(server, client.room, f"- {client.name} says '{text}'\n", skip=client)


def command_emote(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Send an emote to the player's room."""
    if not server.send(client, f"You emote: {text}\n"):
        return
    broadcast_room(server, client.room, f"- {client.name} {text}\n", skip=client)


def command_think(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Send a thought to the player's room."""
    line = f"- {client.name} thinks . o O ( {text} )\n"
    if not server.send(client, f"You think . o O ( {text} )\n"):
        return
    broadcast_room(server, client.room, line, skip=client)


def command_help(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Reply with one help page, a search list, or a not-found line."""
    cleaned = (text or "").strip()
    head, _, rest = cleaned.partition(" ")
    if head.casefold() == "search":
        phrase = rest.strip()
        if phrase == "":
            server.send(client, "Syntax: help search <text>\n")
            return
        player_rank = has_rank(client)
        book = visible_help(server.help, player_rank, mfa_enabled=server.mfa_enabled, country_lookup=server.country_lookup, roles=held_roles(client))
        found = book.search(phrase)
        if len(found) == 1:
            show_help_page(server, client, book, found[0])
            return
        if len(found) > 1:
            lines = "\n".join(f"  {entry.name}" for entry in found)
            server.send(client, f"Help pages:\n{lines}\n")
            return
        server.send(client, f"No help found for '{phrase}'.\n")
        return
    topic = get_parts(text)
    player_rank = has_rank(client)
    book = visible_help(server.help, player_rank, mfa_enabled=server.mfa_enabled, country_lookup=server.country_lookup, roles=held_roles(client))
    entry = book.find(topic)
    if entry is None:
        server.send(client, f"help for {topic} not found\n")
        return
    show_help_page(server, client, book, entry)


def permissions_line(command: Command, colour: bool) -> str:
    """Return the help Permissions value for this command."""
    rank = shown_rank(command.rank, colour)
    if command.role == 0:
        return rank
    labels = ", ".join(role_names(int(command.role)))
    return f"{rank} {command.combination} {labels}"


def show_help_page(server: Server, client: Client, book: HelpBook, entry: HelpEntry) -> None:
    """Send one help page."""
    visible = frozenset(item.name for item in book.entries)
    linked = help_command(entry.name)
    syntax = None if linked is None else shown_syntax(linked)
    permissions = None if linked is None else permissions_line(linked, colour_of(client))
    page = format_page(entry, syntax, frame_width(client.discovered_columns, client.frame_columns), visible=visible, permissions=permissions)
    server.send(client, f"\n\n{page}\n")


def child_of_topic(topic: str, known: Mapping[str, Command]) -> Command | None:
    """Return the child when topic is ``parent child``."""
    parts = topic.split(None, 1)
    if len(parts) != 2:
        return None
    parent = next((command for word, command in known.items() if word.casefold() == parts[0].casefold()), None)
    if parent is None or parent.subcommands is None:
        return None
    folded = parts[1].casefold()
    return next((child for child in parent.subcommands if child.name.casefold() == folded), None)


def help_command(name: str) -> Command | None:
    """Return the command for a help topic name, or None when there is none."""
    child = child_of_topic(name, by_name())
    if child is not None:
        return child
    return by_name().get(name)


def help_syntax(name: str) -> str | None:
    """Return the shown syntax, or None when the topic is not a command."""
    command = help_command(name)
    if command is None:
        return None
    return shown_syntax(command)


def command_version(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Reply with the package version."""
    server.send(client, f"The current version is: {get_version()}\n")


def commands_syntax(rank: Rank, roles: int = 0) -> str:
    """Return the commands syntax line for this rank and these roles."""
    parts = ["<letter>", "aliases"]
    if rank_allows(rank, Rank.SU_CHANNEL):
        parts.append("su")
    if rank_allows(rank, Rank.ADMIN):
        parts.append("admin")
    if rank_allows(rank, Rank.CODER):
        parts.append("coder")
    for role, word in ROLE_WORDS.items():
        if roles & int(role):
            parts.append(word)
    return "Syntax: commands [" + "|".join(parts) + "]"


def commands_gap(word: str) -> str:
    """Return the sentence for a filter that matched nothing."""
    folded = word.casefold()
    if folded == "aliases":
        return "There are no aliases."
    if folded == "su":
        return "There are no Super User commands."
    if folded == "admin":
        return "There are no Admin commands."
    if folded == "coder":
        return "There are no Coder commands."
    for role, role_word in ROLE_WORDS.items():
        if role_word.casefold() == folded:
            return f"There are no {ROLE_LABELS[role]} commands."
    return f"No commands found beginning with '{word}'."


def command_commands(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Reply with the command list, or the names one filter keeps."""
    del command
    rank = has_rank(client)
    roles = held_roles(client)
    words = get_parts(text, split=True)
    if len(words) > 1:
        server.send(client, f"{commands_syntax(rank, roles)}\n")
        return
    chosen = listed_commands(rank, mfa_enabled=server.mfa_enabled, country_lookup=server.country_lookup, roles=roles)
    gap = ""
    if len(words) == 1:
        word = words[0].casefold()
        if len(word) == 1 and word.isascii() and word.isalpha():
            chosen = [item for item in chosen if item.name[:1].casefold() == word]
            gap = commands_gap(words[0])
        elif word == "aliases":
            chosen = [item for item in chosen if item.is_alias]
            gap = commands_gap(words[0])
        elif word == "su" and rank_allows(rank, Rank.SU_CHANNEL):
            chosen = listed_commands(rank, mfa_enabled=server.mfa_enabled, country_lookup=server.country_lookup, roles=roles)
            chosen = [item for item in chosen if item.rank in (Rank.SU_CHANNEL, Rank.SUPER_USER)]
            gap = commands_gap(words[0])
        elif word == "admin" and rank_allows(rank, Rank.ADMIN):
            chosen = listed_commands(rank, exact=Rank.ADMIN, mfa_enabled=server.mfa_enabled, country_lookup=server.country_lookup, roles=roles)
            gap = commands_gap(words[0])
        elif word == "coder" and rank_allows(rank, Rank.CODER):
            chosen = listed_commands(rank, exact=Rank.CODER, mfa_enabled=server.mfa_enabled, country_lookup=server.country_lookup, roles=roles)
            gap = commands_gap(words[0])
        elif (held := role_from_name(words[0])) is not None and (roles & int(held)) != 0:
            chosen = listed_commands(rank, role_filter=int(held), mfa_enabled=server.mfa_enabled, country_lookup=server.country_lookup, roles=roles)
            gap = commands_gap(words[0])
        else:
            server.send(client, f"{commands_syntax(rank, roles)}\n")
            return
    if gap and not chosen:
        server.send(client, f"{gap}\n")
        return
    shown = [shown_name(item, colour_of(client)) for item in chosen]
    wrapped = wrap_words(shown, frame_width(client.discovered_columns, client.frame_columns))
    server.send(client, f"Commands available:\n{wrapped}\n")


def command_quit(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Say goodbye and close the connection."""
    if server.send(client, "Goodbye!\n"):
        server.drop(client, announce=True)


def command_go(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Move the player through an exit, or explain why they stayed."""
    from .parse import parse_direction

    collapsed = " ".join(get_parts(text, split=True))
    code = parse_direction(collapsed)
    if code is None:
        server.send(client, f"No such direction '{collapsed}'.\n")
        return
    layout = server.layout
    destination = layout.destination(client.room, code)
    if destination is None:
        server.send(client, cannot_go_line(code))
        return
    if is_full(layout, get_clients(server), destination):
        server.send(client, full_line(layout.display_name(destination)))
        return
    origin = client.room
    destination_room = layout.rooms[destination]
    leaving = spoken_line(client.name, destination_room.enter) if destination_room.enter else leave_line(client.name, code, layout.display_name(destination))
    broadcast_room(server, origin, leaving, skip=client)
    client.room = destination
    server.layout = release_empty_houses(server.layout, get_clients(server))
    brief = layout.brief(destination, others(get_clients(server), destination, client))
    if not server.send(client, brief):
        return
    broadcast_room(server, destination, spoken_line(client.name, enter_sentence(client)), skip=client)


def command_main(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Move the player to the start room, or explain why they stayed."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    layout = server.layout
    destination = server.config.start
    label = layout.display_name(destination)
    if client.room == destination:
        return
    if is_full(layout, get_clients(server), destination):
        server.send(client, full_line(label))
        return
    origin = client.room
    start_room = layout.rooms[destination]
    leaving = spoken_line(client.name, start_room.enter) if start_room.enter else goes_to_line(client.name, label)
    broadcast_room(server, origin, leaving, skip=client)
    client.room = destination
    server.layout = release_empty_houses(server.layout, get_clients(server))
    brief = layout.brief(destination, others(get_clients(server), destination, client))
    if not server.send(client, brief):
        return
    broadcast_room(server, destination, spoken_line(client.name, enter_sentence(client)), skip=client)


def land_in_void(server: Server, client: Client) -> None:
    """Move ``client`` to The Void and tell them the old room is gone."""
    if client.room == VOID_ID:
        return
    client.room = VOID_ID
    server.send(client, "The room you were in has gone.\n")
    company = others(get_clients(server), VOID_ID, client)
    server.send(client, server.layout.brief(VOID_ID, company))
    broadcast_room(server, VOID_ID, spoken_line(client.name, enter_sentence(client)), skip=client)


def command_map(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Send the map, centered in the terminal, or the syntax line."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    width = frame_width(client.discovered_columns, client.frame_columns)
    colour_on = can_see_colour(client)
    drawn = center_block(server.layout.map_text(client.room, width, colour=colour_on), width)
    server.send(client, f"\n\n{drawn}\n")


def received(target: Client, line: str) -> str:
    """Return ``line`` in green when the recipient has colour on."""
    if can_see_colour(target):
        return green(line) + "\n"
    return line + "\n"


def send_auto_reply(server: Server, client: Client, target: Client) -> None:
    """Tell the sender when the target has a session auto-reply set."""
    if target is client:
        return
    user = target.player
    if user is None or user.auto_reply == "" or user.screen_locked:
        return
    server.send(client, f"[ Auto Reply from {user.name} ] {user.auto_reply}\n")


def send_screen_lock(server: Server, client: Client, target: Client) -> None:
    """Tell the sender when the target's screen is locked."""
    if target is client:
        return
    user = target.player
    if user is None or not user.screen_locked:
        return
    reason = user.screen_lock_reason.strip()
    extra = f" {reason}" if reason else ""
    server.send(client, f"[ Screen Locked ] {user.name}'s screen is currently locked{extra}\n")


def send_chain_note(server: Server, delivery: Delivery) -> None:
    """Tell everyone who was on a removed chain why it went."""
    if delivery.removed_note is None:
        return
    wanted = {name.casefold() for name in delivery.removed_names}
    for person in list(get_clients(server)):
        if person.name is not None and person.name.casefold() in wanted:
            server.send(person, delivery.removed_note)


def chain_prefix(delivery: Delivery) -> str:
    """Return the chain id prefix, or an empty string for one recipient."""
    if delivery.chain is None:
        return ""
    return f"({delivery.chain.number}) "


def recipient_label(delivery: Delivery, you: str | None = None) -> str:
    """Return the other people on this send, with you in the viewer's place."""
    names = []
    for person in delivery.recipients:
        player = person.player
        names.append(player.name if player is not None else (person.name or ""))
    return listed_names(names, you=you)


def command_tell(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Send a spoken line to named players, or to a chain the sender is on."""
    parsed = split_target_message(text)
    if parsed is None:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    pieces, message = parsed
    delivery = deliver(server, client, pieces, datetime.now(timezone.utc))
    for line in delivery.sender_lines:
        server.send(client, line)
    if delivery.stopped or not delivery.recipients:
        return
    send_chain_note(server, delivery)
    prefix = chain_prefix(delivery)
    shown = recipient_label(delivery)
    if not server.send(client, f"{prefix}You tell {shown} '{message}'\n"):
        return
    for target in delivery.recipients:
        if target is client:
            continue
        theirs = recipient_label(delivery, you=target.name)
        server.send(target, received(target, f"{prefix}> {client.name} tells {theirs} '{message}'"))
        send_auto_reply(server, client, target)
        send_screen_lock(server, client, target)


def command_remote(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Emote to named players, or to a chain the sender is on."""
    parsed = split_target_message(text)
    if parsed is None:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    pieces, message = parsed
    delivery = deliver(server, client, pieces, datetime.now(timezone.utc))
    for line in delivery.sender_lines:
        server.send(client, line)
    if delivery.stopped or not delivery.recipients:
        return
    send_chain_note(server, delivery)
    prefix = chain_prefix(delivery)
    shown = recipient_label(delivery)
    if not server.send(client, f"{prefix}You emote: {client.name} {message} to {shown}\n"):
        return
    for target in delivery.recipients:
        if target is client:
            continue
        theirs = recipient_label(delivery, you=target.name)
        server.send(target, received(target, f"{prefix}> {client.name} {message} to {theirs}"))
        send_auto_reply(server, client, target)
        send_screen_lock(server, client, target)


def command_rthink(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Think to named players, or to a chain the sender is on."""
    parsed = split_target_message(text)
    if parsed is None:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    pieces, message = parsed
    delivery = deliver(server, client, pieces, datetime.now(timezone.utc))
    for line in delivery.sender_lines:
        server.send(client, line)
    if delivery.stopped or not delivery.recipients:
        return
    send_chain_note(server, delivery)
    prefix = chain_prefix(delivery)
    shown = recipient_label(delivery)
    if not server.send(client, f"{prefix}You think . o O ( {message} ) to {shown}\n"):
        return
    for target in delivery.recipients:
        if target is client:
            continue
        theirs = recipient_label(delivery, you=target.name)
        server.send(target, received(target, f"{prefix}> {client.name} thinks . o O ( {message} ) to {theirs}"))
        send_auto_reply(server, client, target)
        send_screen_lock(server, client, target)


def command_chains(server: Server, client: Client, command: Command, text: str | None) -> None:
    """List your chains, or leave, remove people, or delete one."""
    if not text:
        send_chain_list(server, client)
        return
    verb, _, rest = text.partition(" ")
    rest = rest.strip()
    if verb.casefold() == "leave":
        chains_leave(server, client, rest)
        return
    if verb.casefold() == "remove":
        chains_remove(server, client, rest)
        return
    if verb.casefold() == "delete":
        chains_delete(server, client, rest)
        return
    server.send(client, f"Syntax: {shown_syntax(command)}\n")


def send_chain_list(server: Server, client: Client) -> None:
    """List the chains this player is on, with idle on the right."""
    key = (client.name or "").casefold()
    mine = [chain for chain in server.chains if any(name.casefold() == key for name in chain.names)]
    if not mine:
        server.send(client, "You are not on a chain.\n")
        return
    mine.sort(key=lambda chain: chain.number)
    now = datetime.now(timezone.utc)
    rows: list[tuple[str, str]] = []
    for chain in mine:
        label = f"Chain {chain.number}: {listed_names(chain.names)}"
        seconds = max(0, int((now - chain.last_message_at).total_seconds()))
        rows.append((label, paint_idle(seconds, colour_of(client))))
    widest = max(plain_len(label) for label, _clock in rows)
    body = []
    for label, clock in rows:
        body.append(f"{label}{' ' * (widest - plain_len(label))}  {clock}")
    width = visible_width(frame_width(client.discovered_columns, client.frame_columns), max(plain_len(line) for line in body))
    rule = "-" * width
    server.send(client, "\n".join([rule, *body, rule]) + "\n")


def chains_leave(server: Server, client: Client, text: str) -> None:
    """Leave one chain. The others hear it, and a chain below three people closes."""
    name = client.name
    parsed = split_id_and_reason(text)
    if name is None or parsed is None:
        server.send(client, "Syntax: chains leave <id> [reason]\n")
        return
    number, reason = parsed
    error, notices = leave_chain(server.chains, number, name, reason)
    if error is not None:
        server.send(client, error)
        return
    server.send(client, f"You have left chain {number}.\n")
    for remaining, line in notices:
        tell_names(server, remaining, line)


def chains_remove(server: Server, client: Client, text: str) -> None:
    """Remove named people from one chain. A chain below three people closes."""
    name = client.name
    parts = text.split(None, 1)
    number = chain_number(parts[0]) if parts else None
    parsed = split_target_message(parts[1], required=False) if number is not None and len(parts) == 2 else None
    if name is None or number is None or parsed is None:
        server.send(client, "Syntax: chains remove <id> <name>[,<name>] [reason]\n")
        return
    names, reason = parsed
    errors, notices = remove_from_chain(server.chains, number, name, names, reason)
    for line in errors:
        server.send(client, line)
    for remaining, line in notices:
        tell_names(server, remaining, line)


def chains_delete(server: Server, client: Client, text: str) -> None:
    """Delete one chain and tell everyone who was on it."""
    name = client.name
    parsed = split_id_and_reason(text)
    if name is None or parsed is None:
        server.send(client, "Syntax: chains delete <id> [reason]\n")
        return
    number, reason = parsed
    error, notice = delete_chain(server.chains, number, name, reason)
    if error is not None:
        server.send(client, error)
        return
    if notice is not None:
        tell_names(server, notice[0], notice[1])


def split_id_and_reason(text: str) -> tuple[int, str] | None:
    """Return a chain id and the rest of the line, or None when the id is missing."""
    parts = text.split(None, 1)
    if not parts:
        return None
    number = chain_number(parts[0])
    if number is None:
        return None
    reason = parts[1].strip() if len(parts) == 2 else ""
    return number, reason


def command_examine(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Show a player in a room, or the saved record when they are logged out."""
    name = " ".join(get_parts(text, split=True))
    if name.lower() == "me":
        send_connected_examine(server, client, client)
        return
    chosen, error = match_examine_name(server, name)
    if error is not None:
        server.send(client, error)
        return
    if chosen is None:
        server.send(client, f"Player {name} not found.\n")
        return
    target = find_player(server, client, chosen, quiet=True)
    if target is not None and target.player is not None:
        send_connected_examine(server, client, target)
        return
    found, saved = get_saved_player(server, client, chosen, quiet=True)
    if not found:
        server.send(client, CANNOT_NOW)
        return
    if saved is not None:
        here = find_player(server, client, saved.name, quiet=True)
        if here is not None and here.player is not None:
            send_connected_examine(server, client, here)
            return
        now = datetime.now(timezone.utc)
        fields = logged_out_fields(saved, now, has_rank(client), colour_of(client))
        server.send(client, examine_frame(saved.name, fields, frame_width(client.discovered_columns, client.frame_columns), plan=saved.plan))  # fmt: skip  # noqa: E501
        return
    server.send(client, f"Player {chosen} not found.\n")


def send_connected_examine(server: Server, viewer: Client, target: Client) -> None:
    """Send the in-room examine frame. Staff also see where they logged in from."""
    user = target.player
    if user is None:
        return
    now = datetime.now(timezone.utc)
    fields: list[tuple[str, str]] = [
        ("User Rank", shown_person_rank(has_rank(viewer), user.rank, colour_of(viewer))),
    ]
    phrase = roles_phrase(user.roles)
    if phrase:
        fields.append(("Roles", phrase))
    if user.status != "":
        fields.append(("Status", user.status))
    fields.append(("Logged in from", login_place(target.hostname, target.address, ssh=target.ssh)))
    fields.append(("Logged in for", connected_phrase(user.connected_at, now)))
    fields.append(("Total Login", duration_phrase(user.connected_seconds)))
    fields.append(("Idle for", examine_idle(user.idle_seconds)))
    if viewer is target:
        fields.append(("Terminal Size", terminal_size_value(viewer.discovered_columns, viewer.discovered_rows, viewer.frame_columns)))
    fields.extend(sentence_fields(user.entermsg, user.loginmsg, user.logoutmsg))
    server.send(viewer, examine_frame(user.name, fields, frame_width(viewer.discovered_columns, viewer.frame_columns), user.description, user.plan))


def command_colour(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Turn this player's colour on or off, or report the current choice."""
    user = client.player
    if user is None:
        return
    word = get_parts(text)
    if word == "":
        server.send(client, colour_line(user.colour))
        return
    if word == "on":
        chosen = True
    elif word == "off":
        chosen = False
    else:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    user.colour = chosen
    keep_colour(server, user)
    server.send(client, colour_line(chosen))


def command_term_width(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Set or clear this connection's frame width."""
    words = get_parts(text, split=True)
    if not words:
        server.send(client, terminal_size_line(client.discovered_columns, client.discovered_rows, client.frame_columns))
        return
    if len(words) != 1:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    if words[0] == "auto":
        client.frame_columns = None
        server.send(client, "Width set to automatic.\n")
        return
    if not (words[0].isascii() and words[0].isdigit()):
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    chosen = int(words[0])
    ceiling = width_ceiling(client)
    if ceiling < 60 or chosen > ceiling:
        server.send(client, f"Width cannot be wider than {ceiling}.\n")
        return
    if chosen < 60:
        server.send(client, "Width must be at least 60.\n")
        return
    client.frame_columns = chosen
    server.send(client, f"Width set to {chosen}.\n")


def command_time(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Send the current UTC time, boot and reboot times when set, and uptime."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    now = datetime.now(timezone.utc)
    seconds = int((now - server.started_at).total_seconds())
    rebooted = server.rebooted_at
    reboot_clock = clock_phrase(rebooted) if rebooted is not None else None
    server.send(client, time_frame(clock_phrase(now), clock_phrase(server.started_at), duration_phrase(seconds), reboot_clock))


def who(clients: Iterable[Client]) -> list[tuple[str, Rank, str, str, int]]:
    """Return each playing name, rank, room, status, and idle seconds, sorted by name."""
    rows: list[tuple[str, Rank, str, str, int]] = []
    for client in clients:
        player = client.player
        if not is_logged_in(client) or player is None:
            continue
        rows.append((player.name, player.rank, player.room, player.status, player.idle_seconds))
    rows.sort()
    return rows


def command_who(server: Server, client: Client, command: Command, text: str | None) -> None:
    """List every player in a room, that room, and the idle clock."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    rows = [(name, shown_person_cell(has_rank(client), rank, colour_of(client)), server.layout.location_name(room_id, name), status, paint_idle(idle, colour_of(client))) for name, rank, room_id, status, idle in who(get_clients(server))]
    if not rows:
        server.send(client, " -=> There is currently no one connected\n")
        return
    server.send(client, list_frame(("Name", "Rank", "Room", "Status", "Idle"), rows, frame_width(client.discovered_columns, client.frame_columns)))  # fmt: skip  # noqa: E501


def command_cls(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Clear this player's screen."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    server.send(client, cls())


def command_testcard(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Show every foreground and background colour."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    server.send(client, testcard())


def command_look(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Show this room's description, exits, and who else is here."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    player = client.player
    if not is_logged_in(client) or player is None:
        return
    company = others(get_clients(server), player.room, client)
    server.send(client, server.layout.brief(player.room, company))


def command_join(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Move to the room of the named player when that room has space."""
    name = get_parts(text)
    target, error = match_online_player(get_clients(server), name)
    if error is not None:
        server.send(client, error)
        return
    if target is None or target.room is None:
        server.send(client, f"Player {name} not found.\n")
        return
    destination = target.room
    layout = server.layout
    label = layout.display_name(destination)
    if client.room == destination:
        server.send(client, f"You are already in {label}.\n")
        return
    owner = layout.houses.get(destination)
    if owner is not None and (client.name or "").casefold() != owner:
        server.send(client, f"You are not permitted to {target.name}'s home.\n")
        return
    if is_full(layout, get_clients(server), destination):
        server.send(client, full_line(label))
        return
    origin = client.room
    broadcast_room(server, origin, spoken_line(client.name, f"goes to join {target.name}"), skip=client)
    client.room = destination
    server.layout = release_empty_houses(server.layout, get_clients(server))
    brief = layout.brief(destination, others(get_clients(server), destination, client))
    if not server.send(client, brief):
        return
    broadcast_room(server, destination, spoken_line(client.name, enter_sentence(client)), skip=client)


def idlers(clients: Iterable[Client]) -> list[tuple[str, int]]:
    """Return each logged-in name and idle count, sorted."""
    rows: list[tuple[str, int]] = []
    for client in clients:
        user = client.player
        if user is None:
            continue
        rows.append((user.name, user.idle_seconds))
    rows.sort()
    return rows


def command_lsu(server: Server, client: Client, command: Command, text: str | None) -> None:
    """List connected SU Channel and above, and how long each has been idle."""
    if text:
        server.send(client, f"Syntax: {shown_syntax(command)}\n")
        return
    rows: list[tuple[str, str, str, str, str]] = []
    for name, idle in idlers(get_clients(server)):
        target = find_player(server, client, name, quiet=True)
        if target is None or not is_logged_in(target) or target.player is None:
            continue
        if not rank_allows(target.player.rank, Rank.SU_CHANNEL):
            continue
        if target.player.rank is Rank.SU_CHANNEL and not rank_allows(has_rank(client), Rank.SU_CHANNEL):
            continue
        hidden = target.player.off_duty or target.player.off_list
        if hidden and not rank_allows(has_rank(client), Rank.SU_CHANNEL):
            continue
        rank = lsu_rank_cell(target.player.rank, off_duty=target.player.off_duty, off_list=target.player.off_list, colour=colour_of(client))
        rows.append((name, rank, server.layout.location_name(target.player.room, name), target.player.status, paint_idle(idle, colour_of(client))))  # fmt: skip  # noqa: E501
    if not rows:
        server.send(client, " -=> There are no staff members connected currently.\n")
        return
    server.send(client, list_frame(("Name", "Rank", "Room", "Status", "Idle"), rows, frame_width(client.discovered_columns, client.frame_columns)))  # fmt: skip  # noqa: E501


def visible_help(book: HelpBook, rank: Rank, *, mfa_enabled: bool, country_lookup: bool = False, roles: int = 0) -> HelpBook:
    """Return the help entries ``rank`` may see."""
    entries = tuple(entry for entry in book.entries if help_entry_visible(entry, rank, mfa_enabled, country_lookup, roles))
    return HelpBook(entries=entries)


def help_entry_visible(entry: HelpEntry, rank: Rank, mfa_enabled: bool, country_lookup: bool = False, roles: int = 0) -> bool:
    """Return whether ``rank`` may see ``entry``."""
    if entry.name == "mfa" and not mfa_enabled:
        return False
    if entry.name == "ipinfo" and not country_lookup:
        return False
    child = child_of_topic(entry.name, by_name())
    if child is not None:
        return command_allowed(rank, roles, child)
    tokens = (entry.name, *entry.aliases)
    commands = tuple(by_name()[token] for token in tokens if token in by_name())
    if not commands:
        return True
    return all(command_allowed(rank, roles, command) for command in commands)


def logged_out_fields(saved: SavedPlayer, now: datetime, viewer: Rank, colour: bool) -> list[tuple[str, str]]:
    """Return examine fields for a saved player who is not in a room."""
    fields: list[tuple[str, str]] = [("User Rank", shown_person_rank(viewer, saved.rank, colour))]
    phrase = roles_phrase(saved.roles)
    if phrase:
        fields.append(("Roles", phrase))
    fields.append(("Last from", connected_place(saved.hostname, saved.ip_address or "")))
    left = saved.logged_out_at
    if left is None:
        fields.append(("Logged out", "unknown"))
    else:
        if left.tzinfo is None:
            left = left.replace(tzinfo=timezone.utc)
        left = left.astimezone(timezone.utc)
        stamp = clock_phrase(left)
        ago = duration_phrase(int((now - left).total_seconds()))
        fields.append(("Logged out", stamp))
        fields.append(("Ago", ago))
    fields.append(("Total Login", duration_phrase(saved.connected_seconds)))
    fields.extend(sentence_fields(saved_sentence(saved.entermsg, DEFAULT_ENTERMSG), saved_sentence(saved.loginmsg, DEFAULT_LOGINMSG), saved_sentence(saved.logoutmsg, DEFAULT_LOGOUTMSG)))  # fmt: skip  # noqa: E501
    return fields


def match_examine_name(server: Server, name: str) -> tuple[str | None, str | None]:
    """Return one online or saved name, or the multiple-matches line.

    Exact names win. A single prefix match is used, including a logged-out
    player. Several prefixes are refused.
    """
    names: dict[str, str] = {}
    for saved in saved_players(server):
        names[saved.name.casefold()] = saved.name
    for client in get_clients(server):
        player = client.player
        if not is_logged_in(client) or player is None:
            continue
        names[player.name.casefold()] = player.name
    return match_name(name, names.values())


def colour_line(enabled: bool) -> str:
    """Return the sentence for this colour choice."""
    if enabled:
        return "Colour is on.\n"
    return "Colour is off.\n"


def keep_colour(server: Server, user: Player) -> None:
    """Write the live colour onto an existing player file."""
    try:
        saved = find_saved(server, user.name)
        if saved is None:
            return
        save_player(server.roster_dir, replace(saved, colour=user.colour))
    except (ConfigError, OSError) as exc:
        print(exc)


def sentence_fields(entermsg: str, loginmsg: str, logoutmsg: str) -> list[tuple[str, str]]:
    """Return the examine rows for the three room sentences."""
    return [("Enter message", entermsg), ("Login message", loginmsg), ("Logout message", logoutmsg)]


def saved_sentence(value: str | None, default: str) -> str:
    """Return a saved sentence, or the default when the file omits it."""
    if value is None:
        return default
    return value


def enter_sentence(client: Client) -> str:
    """Return this player's arrive sentence, or the default."""
    user = client.player
    if user is None:
        return DEFAULT_ENTERMSG
    return user.entermsg


COMMANDS.extend(
    (
        Command("say", command_say, False, "<message>"),
        Command("'", command_say, True, "<message>"),
        Command('"', command_say, True, "<message>"),
        Command("`", command_say, True, "<message>"),
        Command("emote", command_emote, False, "<message>"),
        Command(":", command_emote, True, "<message>"),
        Command(";", command_emote, True, "<message>"),
        Command("think", command_think, False, "<message>"),
        Command("~", command_think, True, "<message>"),
        Command("tell", command_tell, False, "<name> <message>"),
        Command(".", command_tell, True, "<name> <message>"),
        Command(">", command_tell, True, "<name> <message>"),
        Command("remote", command_remote, False, "<name> <message>"),
        Command(",", command_remote, True, "<name> <message>"),
        Command("<", command_remote, True, "<name> <message>"),
        Command("rthink", command_rthink, False, "<name> <message>"),
        Command("chains", command_chains, False, "[leave <id> [reason]|remove <id> <name> [reason]|delete <id> [reason]]"),
        Command("help", command_help, False, "<topic>"),
        Command("?", command_help, True, "<topic>"),
        Command("version", command_version, False, None),
        Command("commands", command_commands, False, None),
        Command("quit", command_quit, False, None),
        Command("go", command_go, False, "<direction>"),
        Command("join", command_join, False, "<name>"),
        Command("main", command_main, False, None),
        Command("map", command_map, False, None),
        Command("who", command_who, False, None),
        Command("w", command_who, True, None, needs_space=True),
        Command("cls", command_cls, False, None),
        Command("testcard", command_testcard, False, None),
        Command("look", command_look, False, None),
        Command("l", command_look, True, None, needs_space=True),
        Command("examine", command_examine, False, "<name>"),
        Command("x", command_examine, True, "<name>", needs_space=True),
        Command("colour", command_colour, False, "on|off"),
        Command("color", command_colour, True, "on|off", needs_space=True),
        Command("term_width", command_term_width, False, "<width>|auto"),
        Command("time", command_time, False, None),
        Command("lsu", command_lsu, False, None),
        Command("staff", command_lsu, True, None, needs_space=True),
    )
)
