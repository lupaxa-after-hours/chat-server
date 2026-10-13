"""Commands used by newbies and residents. Staff tools live elsewhere."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from .channel import (
    broadcast_room,
    broadcast_shout,
    idlers,
    match_name,
    match_online_player,
    occupancy,
    others,
    player_named,
    who,
)
from .client import Client
from .colour import green
from .commands_list import COMMANDS, Command, Host, by_name, command_words, wrap_words
from .defaults import DEFAULT_PROMPT
from .exceptions import ConfigError
from .globals import CANNOT_NOW
from .help import HelpBook, HelpEntry, format_entry, format_index
from .parse import parse_direction
from .player import Player
from .rooms import arrive_line, cannot_go_line, full_line, leave_line
from .saved_player import (
    RANK_ADMIN,
    RANK_RESIDENT,
    RANK_SUPER,
    RANK_SUPER_ADMIN,
    SavedPlayer,
    rank_allows,
    save_player,
)
from .time import clock_phrase, connected_phrase, duration_phrase, idle_clock
from .utilities import (
    _find_saved,
    _is_admin,
    _lookup,
    _rank_of,
    _saved_players,
    _width_ceiling,
    arrives_line,
    center_block,
    examine_frame,
    examine_idle,
    frame_width,
    goes_to_line,
    leaves_for_line,
    list_frame,
    shown_rank,
    shown_rank_cell,
    terminal_size_line,
    terminal_size_value,
    time_frame,
)
from .version import get_version


def command_say(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Send a spoken line to the player's room."""
    del command
    if not host.send(client, f"You say '{text}'\n"):
        return
    broadcast_room(host, client.room, f"- {client.name} says '{text}'\n", skip=client)


def command_emote(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Send an emote to the player's room."""
    del command
    if not host.send(client, f"You emote: {text}\n"):
        return
    broadcast_room(host, client.room, f"- {client.name} {text}\n", skip=client)


def command_think(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Send a thought to the player's room."""
    del command
    line = f"- {client.name} thinks . o O ( {text} )\n"
    if not host.send(client, f"You think . o O ( {text} )\n"):
        return
    broadcast_room(host, client.room, line, skip=client)


def command_help(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Reply with the help index, one topic, or a not-found line."""
    del command
    topic = (text or "").strip()
    rank = _rank_of(client)
    book = _visible_help(host._help, rank, mfa_enabled=host._mfa_enabled)
    if topic == "":
        host.send(client, format_index(book))
        return
    entry = book.find(topic)
    if entry is None:
        host.send(client, f"help for {topic} not found\n")
        return
    host.send(client, format_entry(entry))


def command_version(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Reply with the package version."""
    del command, text
    host.send(client, f"The current version is: {get_version()}\n")


def command_commands(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Reply with punctuation aliases, then letter names."""
    del command, text
    words = command_words(
        _rank_of(client),
        colour=_colour_of(client),
        mfa_enabled=host._mfa_enabled,
    ).split()
    wrapped = wrap_words(
        words,
        frame_width(client.discovered_columns, client.frame_columns),
    )
    host.send(client, f"Commands available:\n{wrapped}\n")


def command_quit(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Say goodbye and close the connection."""
    del command, text
    if host.send(client, "Goodbye!\n"):
        host.drop(client, announce=True)


def command_recap(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Change this player's display spelling without changing the key."""
    del command
    user = client.player
    if user is None:
        return
    chosen = (text or "").strip()
    if chosen == "":
        host.send(client, "Syntax: recap <name>\n")
        return
    if chosen.lower() != user.lowercase_name:
        host.send(client, "You can only change the capitalisation of your name.\n")
        return
    try:
        saved = _find_saved(host, user.lowercase_name)
        if saved is not None:
            save_player(host._roster_dir, replace(saved, name=chosen))
    except (ConfigError, OSError) as exc:
        print(exc)
        host.send(client, CANNOT_NOW)
        return
    user.name = chosen
    host.send(client, f"Name set to {chosen}.\n")


def command_go(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Move the player through an exit, or explain why they stayed."""
    del command
    collapsed = " ".join((text or "").split())
    code = parse_direction(collapsed)
    if code is None:
        host.send(client, f"No such direction '{collapsed}'.\n")
        return
    layout = host._layout
    destination = layout.destination(client.room, code)
    if destination is None:
        host.send(client, cannot_go_line(code))
        return
    if occupancy(host._clients.values(), destination) >= layout.rooms[destination].max_occupancy:
        host.send(client, full_line(layout.display_name(destination)))
        return
    origin = client.room
    broadcast_room(
        host,
        origin,
        leave_line(client.name, code, layout.display_name(destination)),
        skip=client,
    )
    client.room = destination
    brief = layout.brief(destination, others(host._clients.values(), destination, client))
    if not host.send(client, brief):
        return
    broadcast_room(host, destination, arrive_line(client.name, code), skip=client)


def command_main(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Move the player to the start room, or explain why they stayed."""
    del command
    if text:
        host.send(client, "Syntax: main\n")
        return
    layout = host._layout
    destination = host._config.start
    label = layout.display_name(destination)
    if client.room == destination:
        return
    if occupancy(host._clients.values(), destination) >= layout.rooms[destination].max_occupancy:
        host.send(client, full_line(label))
        return
    origin = client.room
    broadcast_room(host, origin, goes_to_line(client.name, label), skip=client)
    client.room = destination
    brief = layout.brief(destination, others(host._clients.values(), destination, client))
    if not host.send(client, brief):
        return
    broadcast_room(host, destination, arrives_line(client.name), skip=client)


def command_map(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Send the map, centered in the terminal, or the syntax line."""
    del command
    if text:
        host.send(client, "Syntax: map\n")
        return
    width = frame_width(client.discovered_columns, client.frame_columns)
    colour_on = client.player is not None and client.player.colour
    drawn = center_block(host._layout.map_text(client.room, width, colour=colour_on), width)
    host.send(client, f"\n\n{drawn}\n")


def command_shout(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Send a spoken line to every player who is in a room."""
    del command
    if not host.send(client, f"You shout '{text}'\n"):
        return
    broadcast_shout(host, f"! {client.name} shouts '{text}'", skip=client)


def _received(target: Client, line: str) -> str:
    """Return ``line`` in green when the recipient has colour on."""
    if target.player is not None and target.player.colour:
        return green(line) + "\n"
    return line + "\n"


def _send_auto_reply(host: Host, client: Client, target: Client) -> None:
    """Tell the sender when the target has a session auto-reply set."""
    if target is client:
        return
    user = target.player
    if user is None or user.auto_reply == "":
        return
    host.send(client, f"[ Auto Reply ] {user.auto_reply}\n")


def command_tell(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Send a spoken line to one named player, in any room."""
    parts = (text or "").split(None, 1)
    if len(parts) < 2:
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    name, message = parts
    target, error = match_online_player(host._clients.values(), name)
    if error is not None:
        host.send(client, error)
        return
    if target is None:
        host.send(client, f"Player {name} not found.\n")
        return
    if target is client:
        host.send(client, "Talking to yourself is the first sign of madness.\n")
        return
    shown = target.player.name if target.player is not None else name
    if not host.send(client, f"You tell {shown} '{message}'\n"):
        return
    if target is not client:
        host.send(target, _received(target, f"> {client.name} tells you '{message}'"))
        _send_auto_reply(host, client, target)


def command_remote(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Emote to one named player, in any room."""
    parts = (text or "").split(None, 1)
    if len(parts) < 2:
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    name, message = parts
    target, error = match_online_player(host._clients.values(), name)
    if error is not None:
        host.send(client, error)
        return
    if target is None:
        host.send(client, f"Player {name} not found.\n")
        return
    if target is client:
        host.send(client, "Talking to yourself is the first sign of madness.\n")
        return
    shown = target.player.name if target.player is not None else name
    if not host.send(client, f"You emote: {client.name} {message} to {shown}\n"):
        return
    if target is not client:
        host.send(target, _received(target, f"> {client.name} {message} to you"))
        _send_auto_reply(host, client, target)


def command_examine(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Show a player in a room, or the saved record when they are logged out."""
    del command
    name = " ".join((text or "").split())
    if name == "":
        host.send(client, "Syntax: examine <name>\n")
        return
    if name.lower() == "me":
        _send_connected_examine(host, client, client)
        return
    chosen, error = _match_examine_name(host, name)
    if error is not None:
        host.send(client, error)
        return
    if chosen is None:
        host.send(client, f"Player {name} not found.\n")
        return
    target = player_named(host._clients.values(), chosen)
    if target is not None and target.player is not None:
        _send_connected_examine(host, client, target)
        return
    found, saved = _lookup(host, client, chosen)
    if not found:
        return
    if saved is not None:
        here = player_named(host._clients.values(), saved.name)
        if here is not None and here.player is not None:
            _send_connected_examine(host, client, here)
            return
        now = datetime.now(timezone.utc)
        fields = _logged_out_fields(saved, now, _colour_of(client))
        if rank_allows(_rank_of(client), RANK_SUPER):
            fields.append(("Last from", saved.ip_address or "unknown"))
        host.send(
            client,
            examine_frame(
                saved.name,
                fields,
                frame_width(client.discovered_columns, client.frame_columns),
                saved.description,
            ),
        )
        return
    host.send(client, f"Player {chosen} not found.\n")


def _send_connected_examine(host: Host, viewer: Client, target: Client) -> None:
    """Send the in-room examine frame. Staff also see where they logged in from."""
    user = target.player
    if user is None:
        return
    now = datetime.now(timezone.utc)
    fields: list[tuple[str, str]] = [
        ("User Rank", shown_rank(user.rank, _colour_of(viewer))),
    ]
    if user.status != "":
        fields.append(("Status", user.status))
    if rank_allows(_rank_of(viewer), RANK_SUPER):
        fields.append(("Logged in from", target.address))
    fields.append(("Logged in for", connected_phrase(user.connected_at, now)))
    fields.append(("Total Login", duration_phrase(user.connected_seconds)))
    fields.append(("Idle for", examine_idle(user.idle_seconds)))
    if viewer is target:
        fields.append(
            (
                "Terminal Size",
                terminal_size_value(
                    viewer.discovered_columns,
                    viewer.discovered_rows,
                    viewer.frame_columns,
                ),
            )
        )
    host.send(
        viewer,
        examine_frame(
            user.name,
            fields,
            frame_width(viewer.discovered_columns, viewer.frame_columns),
            user.description,
        ),
    )


def command_alarm(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Set an alarm for a whole number of minutes."""
    del command
    minutes_text = (text or "").strip()
    if not (minutes_text.isascii() and minutes_text.isdigit()) or int(minutes_text) < 1:
        host.send(client, "Syntax: alarm <minutes>\n")
        return
    user = client.player
    if user is None:
        return
    minutes = int(minutes_text)
    user.alarm_seconds = minutes * 60
    unit = "minute" if minutes == 1 else "minutes"
    host.send(client, f"Alarm set for {minutes} {unit}.\n")


def command_description(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Set or clear the one line shown on examine."""
    del command
    chosen = (text or "").strip()
    user = client.player
    if user is None:
        return
    if chosen == "":
        user.description = ""
        _keep_description(host, user)
        host.send(client, "Description cleared.\n")
        return
    if len(chosen) > 80:
        host.send(client, "A description is at most 80 characters.\n")
        return
    user.description = chosen
    _keep_description(host, user)
    host.send(client, "Description set.\n")


def command_status(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Set or clear the session status shown on examine and player lists."""
    del command
    chosen = (text or "").strip()
    user = client.player
    if user is None:
        return
    if chosen == "":
        user.status = ""
        host.send(client, "Status cleared.\n")
        return
    if len(chosen) > 20:
        host.send(client, "A status is at most 20 characters.\n")
        return
    user.status = chosen
    host.send(client, "Status set.\n")


def command_auto_reply(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Set or clear the line returned to tell and remote senders."""
    del command
    chosen = (text or "").strip()
    user = client.player
    if user is None:
        return
    if chosen == "":
        user.auto_reply = ""
        host.send(client, "Auto-reply cleared.\n")
        return
    user.auto_reply = chosen
    host.send(client, "Auto-reply set.\n")


def command_colour(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Turn this player's colour on or off, or report the current choice."""
    user = client.player
    if user is None:
        return
    word = (text or "").strip()
    if word == "":
        host.send(client, _colour_line(user.colour))
        return
    if word == "on":
        chosen = True
    elif word == "off":
        chosen = False
    else:
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    user.colour = chosen
    _keep_colour(host, user)
    host.send(client, _colour_line(chosen))


def command_prompt(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Replace the line this player types at."""
    del command
    chosen = (text or "").strip()
    if chosen == "":
        host.send(client, "Syntax: prompt <prompt>\n")
        return
    if not chosen.endswith(" "):
        chosen += " "
    user = client.player
    if user is None:
        return
    user.prompt = chosen
    _keep_prompt(host, user, chosen)
    host.send(client, "Prompt set.\n")


def command_save(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Write this resident's live profile to the player file."""
    if (text or "").strip() != "":
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    user = client.player
    if user is None:
        return
    try:
        saved = _find_saved(host, user.name)
        if saved is None:
            raise ConfigError("No saved player.")
        kept = None if user.prompt == DEFAULT_PROMPT else user.prompt
        save_player(
            host._roster_dir,
            replace(
                saved,
                description=user.description,
                colour=user.colour,
                prompt=kept,
                connected_seconds=user.connected_seconds,
            ),
        )
    except (ConfigError, OSError) as exc:
        print(exc)
        host.send(client, CANNOT_NOW)
        return
    host.send(client, "Player Saved.\n")


def command_retire(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Ask for confirmation and a password before deleting this resident's file."""
    if (text or "").strip() != "":
        host.send(client, f"Syntax: {command.syntax}\n")
        return
    user = client.player
    if user is None:
        return
    if _is_admin(host, user.name):
        host.send(client, "The admin cannot be removed.\n")
        return
    client.auth = "retire_confirm"
    host.send(client, "Are you sure you want to retire?\n")


def command_term_width(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Set or clear this connection's frame width."""
    del command
    words = (text or "").split()
    if not words:
        host.send(
            client,
            terminal_size_line(
                client.discovered_columns,
                client.discovered_rows,
                client.frame_columns,
            ),
        )
        return
    if len(words) != 1:
        host.send(client, "Syntax: term_width <width>|auto\n")
        return
    if words[0] == "auto":
        client.frame_columns = None
        host.send(client, "Width set to automatic.\n")
        return
    if not (words[0].isascii() and words[0].isdigit()):
        host.send(client, "Syntax: term_width <width>|auto\n")
        return
    chosen = int(words[0])
    ceiling = _width_ceiling(client)
    if ceiling < 60 or chosen > ceiling:
        host.send(client, f"Width cannot be wider than {ceiling}.\n")
        return
    if chosen < 60:
        host.send(client, "Width must be at least 60.\n")
        return
    client.frame_columns = chosen
    host.send(client, f"Width set to {chosen}.\n")


def command_time(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Send the current UTC time and how long this server has existed."""
    del command
    if text:
        host.send(client, "Syntax: time\n")
        return
    now = datetime.now(timezone.utc)
    seconds = int((now - host.started_at).total_seconds())
    host.send(
        client,
        time_frame(
            clock_phrase(now),
            duration_phrase(seconds),
            frame_width(client.discovered_columns, client.frame_columns),
        ),
    )


def command_who(host: Host, client: Client, command: Command, text: str | None) -> None:
    """List every player in a room, that room, and the idle clock."""
    del command
    if text:
        host.send(client, "Syntax: who\n")
        return
    rows = [
        (
            name,
            shown_rank_cell(rank, _colour_of(client)),
            host._layout.display_name(room_id),
            status,
            idle_clock(idle),
        )
        for name, rank, room_id, status, idle in who(host._clients.values())
    ]
    host.send(
        client,
        list_frame(
            ("Name", "Rank", "Room", "Status", "Idle"),
            rows,
            frame_width(client.discovered_columns, client.frame_columns),
        ),
    )


def command_look(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Show this room's description, exits, and who else is here."""
    del command
    if text:
        host.send(client, "Syntax: look\n")
        return
    if client.player is None or client.room is None:
        return
    company = others(host._clients.values(), client.room, client)
    host.send(client, host._layout.brief(client.room, company))


def command_join(host: Host, client: Client, command: Command, text: str | None) -> None:
    """Move to the room of the named player when that room has space."""
    del command
    name = (text or "").strip()
    if name == "":
        host.send(client, "Syntax: join <name>\n")
        return
    target, error = match_online_player(host._clients.values(), name)
    if error is not None:
        host.send(client, error)
        return
    if target is None or target.room is None:
        host.send(client, f"Player {name} not found.\n")
        return
    destination = target.room
    layout = host._layout
    label = layout.display_name(destination)
    if client.room == destination:
        host.send(client, f"You are already in {label}.\n")
        return
    if occupancy(host._clients.values(), destination) >= layout.rooms[destination].max_occupancy:
        host.send(client, full_line(label))
        return
    origin = client.room
    broadcast_room(host, origin, leaves_for_line(client.name, label), skip=client)
    client.room = destination
    brief = layout.brief(destination, others(host._clients.values(), destination, client))
    if not host.send(client, brief):
        return
    broadcast_room(host, destination, arrives_line(client.name), skip=client)


def command_lsu(host: Host, client: Client, command: Command, text: str | None) -> None:
    """List connected super users and the admin, and how long each has been idle."""
    del command
    if text:
        host.send(client, "Syntax: lsu\n")
        return
    rows: list[tuple[str, str, str, str, str]] = []
    for name, idle in idlers(host._clients.values()):
        target = player_named(host._clients.values(), name)
        if target is None or target.player is None or target.room is None:
            continue
        if target.player.rank not in (RANK_SUPER, RANK_ADMIN, RANK_SUPER_ADMIN):
            continue
        rows.append(
            (
                name,
                shown_rank_cell(target.player.rank, _colour_of(client)),
                host._layout.display_name(target.room),
                target.player.status,
                idle_clock(idle),
            )
        )
    if not rows:
        host.send(client, " -=> There are no staff members connected currently.\n")
        return
    host.send(
        client,
        list_frame(
            ("Name", "Rank", "Room", "Status", "Idle"),
            rows,
            frame_width(client.discovered_columns, client.frame_columns),
        ),
    )


def _visible_help(book: HelpBook, rank: str, *, mfa_enabled: bool) -> HelpBook:
    """Return the help entries ``rank`` may see."""
    entries = tuple(
        entry for entry in book.entries if _help_entry_visible(entry, rank, mfa_enabled)
    )
    return HelpBook(entries=entries)


def _help_entry_visible(entry: HelpEntry, rank: str, mfa_enabled: bool) -> bool:
    """Return whether ``rank`` may see ``entry``."""
    if entry.name == "mfa" and not mfa_enabled:
        return False
    tokens = (entry.name, *entry.aliases)
    commands = tuple(by_name()[token] for token in tokens if token in by_name())
    if not commands:
        return True
    return all(rank_allows(rank, command.rank) for command in commands)


def _logged_out_fields(
    saved: SavedPlayer,
    now: datetime,
    colour: bool,
) -> list[tuple[str, str]]:
    """Return examine fields for a saved player who is not in a room."""
    fields: list[tuple[str, str]] = [("User Rank", shown_rank(saved.rank, colour))]
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
    return fields


def _match_examine_name(host: Host, name: str) -> tuple[str | None, str | None]:
    """Return one online or saved name, or the multiple-matches line.

    Exact names win. A single prefix match is used, including a logged-out
    player. Several prefixes are refused.
    """
    names: dict[str, str] = {}
    for saved in _saved_players(host):
        names[saved.name.casefold()] = saved.name
    for client in host._clients.values():
        player = client.player
        if player is None or client.room is None:
            continue
        names[player.name.casefold()] = player.name
    return match_name(name, names.values())


def _colour_of(client: Client) -> bool:
    """Return this player's colour choice. A missing player is on."""
    if client.player is None:
        return True
    return client.player.colour


def _colour_line(enabled: bool) -> str:
    """Return the sentence for this colour choice."""
    if enabled:
        return "Colour is on.\n"
    return "Colour is off.\n"


def _keep_description(host: Host, user: Player) -> None:
    """Write the live description onto an existing player file."""
    try:
        saved = _find_saved(host, user.name)
        if saved is None:
            return
        save_player(host._roster_dir, replace(saved, description=user.description))
    except (ConfigError, OSError) as exc:
        print(exc)


def _keep_colour(host: Host, user: Player) -> None:
    """Write the live colour onto an existing player file."""
    try:
        saved = _find_saved(host, user.name)
        if saved is None:
            return
        save_player(host._roster_dir, replace(saved, colour=user.colour))
    except (ConfigError, OSError) as exc:
        print(exc)


def _keep_prompt(host: Host, user: Player, chosen: str) -> None:
    """Write ``chosen`` to the saved file. The usual prompt removes the saved one."""
    try:
        saved = _find_saved(host, user.name)
        if saved is None:
            return
        kept = None if chosen == DEFAULT_PROMPT else chosen
        save_player(host._roster_dir, replace(saved, prompt=kept))
    except (ConfigError, OSError) as exc:
        print(exc)


COMMANDS.extend(
    (
        Command("say", command_say, False, "say <message>"),
        Command("'", command_say, True, "say <message>"),
        Command('"', command_say, True, "say <message>"),
        Command("`", command_say, True, "say <message>"),
        Command("emote", command_emote, False, "emote <message>"),
        Command(":", command_emote, True, "emote <message>"),
        Command(";", command_emote, True, "emote <message>"),
        Command("think", command_think, False, "think <message>"),
        Command("~", command_think, True, "think <message>"),
        Command("shout", command_shout, False, "shout <message>", rank=RANK_RESIDENT),
        Command("!", command_shout, True, "shout <message>", rank=RANK_RESIDENT),
        Command("tell", command_tell, False, "tell <name> <message>"),
        Command(".", command_tell, True, "tell <name> <message>"),
        Command(">", command_tell, True, "tell <name> <message>"),
        Command("remote", command_remote, False, "remote <name> <message>"),
        Command(",", command_remote, True, "remote <name> <message>"),
        Command("<", command_remote, True, "remote <name> <message>"),
        Command("help", command_help, False, None),
        Command("?", command_help, True, None),
        Command("version", command_version, False, None),
        Command("commands", command_commands, False, None),
        Command("quit", command_quit, False, None),
        Command("recap", command_recap, False, "recap <name>"),
        Command("go", command_go, False, "go <direction>"),
        Command("join", command_join, False, "join <name>"),
        Command("main", command_main, False, None),
        Command("map", command_map, False, None),
        Command("who", command_who, False, None),
        Command("w", command_who, True, None, needs_space=True),
        Command("look", command_look, False, None),
        Command("examine", command_examine, False, "examine <name>"),
        Command("x", command_examine, True, "examine <name>", needs_space=True),
        Command("alarm", command_alarm, False, "alarm <minutes>"),
        Command("colour", command_colour, False, "colour on|off"),
        Command("color", command_colour, True, "colour on|off", needs_space=True),
        Command("description", command_description, False, "description <description>"),
        Command("status", command_status, False, "status <status>"),
        Command("auto_reply", command_auto_reply, False, "auto_reply <message>"),
        Command("prompt", command_prompt, False, "prompt <prompt>", rank=RANK_RESIDENT),
        Command("save", command_save, False, "save", rank=RANK_RESIDENT),
        Command("retire", command_retire, False, "retire", rank=RANK_RESIDENT),
        Command("term_width", command_term_width, False, "term_width <width>|auto"),
        Command("time", command_time, False, "time"),
        Command("lsu", command_lsu, False, None),
        Command("staff", command_lsu, True, None, needs_space=True),
    )
)
