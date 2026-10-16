"""Message chains shared by tell, remote, and rthink."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import Client
    from .server import Server


MAX_CHAIN_PEOPLE = 10
CHAIN_UNUSED_SECONDS = 2 * 60 * 60


@dataclass
class Chain:
    """One group, its id, and when a message was last sent on it."""

    number: int
    names: list[str]
    last_message_at: datetime


def listed_names(names: Sequence[str], *, you: str | None = None) -> str:
    """Return names sorted case-insensitively, with the viewer written as you."""
    ordered = sorted(names, key=str.casefold)
    if you is not None:
        key = you.casefold()
        ordered = ["you" if name.casefold() == key else name for name in ordered]
    if len(ordered) < 2:
        return ordered[0] if ordered else ""
    if len(ordered) == 2:
        return f"{ordered[0]} & {ordered[1]}"
    return f"{', '.join(ordered[:-1])} & {ordered[-1]}"


def lowest_free_number(chains: Sequence[Chain]) -> int:
    """Return the smallest positive id that no chain uses."""
    used = {chain.number for chain in chains}
    number = 1
    while number in used:
        number += 1
    return number


def find_chain(chains: Sequence[Chain], number: int) -> Chain | None:
    """Return the chain with this id, or None."""
    for chain in chains:
        if chain.number == number:
            return chain
    return None


def find_same_chain(chains: Sequence[Chain], names: Sequence[str]) -> Chain | None:
    """Return the chain whose members match these names, ignoring case and order."""
    wanted = {name.casefold() for name in names}
    for chain in chains:
        if {name.casefold() for name in chain.names} == wanted:
            return chain
    return None


def reason_suffix(reason: str) -> str:
    """Return a space and the reason, or an empty string when there is none."""
    cleaned = " ".join(reason.split())
    if cleaned == "":
        return ""
    return f" {cleaned}"


def collapse_duplicates(chains: list[Chain]) -> list[tuple[list[str], str]]:
    """Delete the higher id whenever two chains hold the same people."""
    notices: list[tuple[list[str], str]] = []
    changed = True
    while changed:
        changed = False
        for chain in list(chains):
            twin = find_same_chain([item for item in chains if item is not chain], chain.names)
            if twin is None:
                continue
            higher = chain if chain.number > twin.number else twin
            lower = twin if higher is chain else chain
            notices.append((list(higher.names), f"Chain {higher.number} has been removed. It is the same as chain {lower.number}.\n"))
            chains.remove(higher)
            changed = True
            break
    return notices


def split_target_message(text: str | None, *, required: bool = True) -> tuple[list[str], str] | None:
    """Return target pieces and the message, or None when the message is missing."""
    raw = (text or "").strip()
    if raw == "":
        return None
    words = raw.split()
    if not words:
        return None
    targets: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word.endswith(","):
            if ",," in word or word == ",":
                return None
            parts = word[:-1].split(",")
            if any(piece == "" for piece in parts):
                return None
            targets.extend(parts)
            index += 1
            continue
        if "," in word:
            parts = word.split(",")
            if any(piece == "" for piece in parts):
                return None
            targets.extend(parts)
            index += 1
            if index >= len(words):
                if required or not targets:
                    return None
                return targets, ""
            return targets, " ".join(words[index:])
        targets.append(word)
        index += 1
        break
    if not targets:
        return None
    if index >= len(words):
        if required:
            return None
        return targets, ""
    return targets, " ".join(words[index:])


@dataclass
class Delivery:
    """One resolved tell, remote, or rthink, before any message is sent."""

    sender_lines: list[str]
    stopped: bool
    recipients: list[Client]
    chain: Chain | None
    removed_note: str | None
    removed_names: list[str]


def chain_number(piece: str) -> int | None:
    """Return a chain id when every character is an ASCII digit."""
    if piece.isascii() and piece.isdigit():
        return int(piece)
    return None


def deliver(server: Server, client: Client, pieces: Sequence[str], now: datetime) -> Delivery:
    """Resolve targets, update chains, and return who should hear the message."""
    from .player import match_online_player
    from .utilities import get_clients

    def display_name(connection: Client) -> str:
        player = connection.player
        if player is not None and player.name:
            return player.name
        return connection.name or ""

    numbers = [chain_number(piece) for piece in pieces]
    chain_ids = [number for number in numbers if number is not None]
    distinct = list(dict.fromkeys(chain_ids))
    if len(distinct) > 1:
        return Delivery(["Name one chain.\n"], True, [], None, None, [])
    chain = None
    if distinct:
        chain = find_chain(server.chains, distinct[0])
        sender_key = display_name(client).casefold()
        on_it = chain is not None and any(name.casefold() == sender_key for name in chain.names)
        if not on_it:
            return Delivery([f"You are not on chain {distinct[0]}.\n"], True, [], None, None, [])
    lines: list[str] = []
    matched: list[Client] = []
    seen: set[str] = set()
    for piece, number in zip(pieces, numbers, strict=True):
        if number is not None:
            continue
        if piece.casefold() == display_name(client).casefold():
            lines.append("Talking to yourself is the first sign of madness.\n")
            continue
        target, error = match_online_player(get_clients(server), piece)
        if error is not None:
            lines.append(error if error.endswith("\n") else error + "\n")
            continue
        if target is None:
            lines.append(f"Player {piece} not found.\n")
            continue
        if target is client:
            lines.append("Talking to yourself is the first sign of madness.\n")
            continue
        key = display_name(target).casefold()
        if key in seen:
            continue
        seen.add(key)
        matched.append(target)
    names = list(chain.names) if chain is not None else [display_name(client)]
    known = {name.casefold() for name in names}
    for target in matched:
        shown = display_name(target)
        if shown.casefold() not in known:
            names.append(shown)
            known.add(shown.casefold())
    if len(names) > MAX_CHAIN_PEOPLE:
        lines.append(f"A chain can hold {MAX_CHAIN_PEOPLE} people. Get in a room and use say.\n")
        return Delivery(lines, True, [], None, None, [])
    others = [target for target in clients_for_names(server, names) if target is not client]
    if chain is None and len(others) < 2:
        return Delivery(lines, False, others, None, None, [])
    removed_note = None
    removed_names: list[str] = []
    if chain is None:
        existing = find_same_chain(server.chains, names)
        if existing is None:
            chain = Chain(lowest_free_number(server.chains), names, now)
            server.chains.append(chain)
        else:
            chain = existing
            chain.last_message_at = now
    else:
        chain.names = names
        chain.last_message_at = now
        notes = collapse_duplicates(server.chains)
        survivor = find_same_chain(server.chains, names)
        if survivor is not None:
            survivor.last_message_at = now
            chain = survivor
        if notes:
            removed_names = notes[0][0]
            removed_note = notes[0][1]
    others = [target for target in clients_for_names(server, chain.names) if target is not client]
    return Delivery(lines, False, others, chain, removed_note, removed_names)


def logged_out_line(chain: Chain, gone: Sequence[str], *, closed: bool) -> str:
    """Return the line for people who left because they are no longer connected."""
    if closed:
        verb = "has" if len(gone) == 1 else "have"
        return f"({chain.number}) Chain closed because {listed_names(gone)} {verb} logged out.\n"
    if len(gone) == 1:
        return f"({chain.number}) {gone[0]} has left the chain because they logged out.\n"
    return f"({chain.number}) {listed_names(gone)} have left the chain because they logged out.\n"


def prune_offline(chains: list[Chain], online: set[str]) -> tuple[list[Chain], list[tuple[list[str], str]]]:
    """Drop names that are not connected, then close or merge what remains."""
    kept: list[Chain] = []
    notices: list[tuple[list[str], str]] = []
    for chain in chains:
        gone = [name for name in chain.names if name.casefold() not in online]
        chain.names = [name for name in chain.names if name.casefold() in online]
        if len(chain.names) >= 3:
            kept.append(chain)
            if gone:
                notices.append((list(chain.names), logged_out_line(chain, gone, closed=False)))
            continue
        if not chain.names:
            continue
        notices.append((list(chain.names), logged_out_line(chain, gone, closed=True)))
    notices.extend(collapse_duplicates(kept))
    return kept, notices


def depart(chains: list[Chain], name: str) -> list[tuple[list[str], str]]:
    """Remove one person. Tell the others, and close the chain below three people."""
    key = name.casefold()
    notices: list[tuple[list[str], str]] = []
    for chain in list(chains):
        if not any(item.casefold() == key for item in chain.names):
            continue
        chain.names = [item for item in chain.names if item.casefold() != key]
        if len(chain.names) >= 3:
            notices.append((list(chain.names), f"({chain.number}) {name} has left the chain because they logged out.\n"))
            continue
        chains.remove(chain)
        if chain.names:
            notices.append((list(chain.names), f"({chain.number}) Chain closed because {name} has logged out.\n"))
    notices.extend(collapse_duplicates(chains))
    return notices


def note_chain_departure(server: Server, name: str) -> None:
    """Tell whoever is left when a disconnect changes their chain."""
    notices = depart(server.chains, name)
    for remaining, line in notices:
        tell_names(server, remaining, line, skip=name)


def on_chain(chain: Chain, name: str) -> bool:
    """Return whether this display name is a member."""
    key = name.casefold()
    return any(item.casefold() == key for item in chain.names)


def leave_chain(chains: list[Chain], number: int, name: str, reason: str) -> tuple[str | None, list[tuple[list[str], str]]]:
    """Remove one member who chose to leave. Return an error, or notices for the others."""
    chain = find_chain(chains, number)
    if chain is None or not on_chain(chain, name):
        return f"You are not on chain {number}.\n", []
    suffix = reason_suffix(reason)
    chain.names = [item for item in chain.names if item.casefold() != name.casefold()]
    if len(chain.names) >= 3:
        notices = [(list(chain.names), f"({chain.number}) {name} has left the chain.{suffix}\n")]
    else:
        chains.remove(chain)
        notices = []
        if chain.names:
            notices.append(([*chain.names, name], f"({number}) Chain closed because {name} left the chain.{suffix}\n"))
    notices.extend(collapse_duplicates(chains))
    return None, notices


def remove_from_chain(chains: list[Chain], number: int, actor: str, gone: Sequence[str], reason: str) -> tuple[list[str], list[tuple[list[str], str]]]:
    """Remove named members. Return error lines and notices for the people involved."""
    chain = find_chain(chains, number)
    if chain is None or not on_chain(chain, actor):
        return [f"You are not on chain {number}.\n"], []
    errors: list[str] = []
    present: list[str] = []
    seen: set[str] = set()
    refused_self = False
    for name in gone:
        key = name.casefold()
        if key == actor.casefold():
            if not refused_self:
                errors.append("Use chains leave to leave a chain.\n")
                refused_self = True
            continue
        if key in seen:
            continue
        seen.add(key)
        stored = next((item for item in chain.names if item.casefold() == key), None)
        if stored is None:
            errors.append(f"{name} is not on chain {number}.\n")
            continue
        present.append(stored)
    if not present:
        return errors, []
    suffix = reason_suffix(reason)
    listed = listed_names(present)
    chain.names = [item for item in chain.names if item.casefold() not in seen]
    audience = list(chain.names) + present
    if len(chain.names) >= 3:
        line = f"({chain.number}) {actor} removed {listed} from the chain.{suffix}\n"
        notices = [(audience, line)]
    else:
        line = f"({number}) Chain closed because {actor} removed {listed} from the chain.{suffix}\n"
        chains.remove(chain)
        notices = [(audience, line)]
    notices.extend(collapse_duplicates(chains))
    return errors, notices


def delete_chain(chains: list[Chain], number: int, actor: str, reason: str) -> tuple[str | None, tuple[list[str], str] | None]:
    """Delete one chain the actor is on. Return an error, or the notice for its members."""
    chain = find_chain(chains, number)
    if chain is None or not on_chain(chain, actor):
        return f"You are not on chain {number}.\n", None
    names = list(chain.names)
    chains.remove(chain)
    suffix = reason_suffix(reason)
    return None, (names, f"({number}) {actor} deleted the chain.{suffix}\n")


def close_idle_chains(server: Server, now: datetime) -> None:
    """Delete a chain that has had no message for two hours, and tell its members."""
    chains = getattr(server, "chains", None)
    if not chains:
        return
    for chain in list(chains):
        idle = (now - chain.last_message_at).total_seconds()
        if idle < CHAIN_UNUSED_SECONDS:
            continue
        names = list(chain.names)
        number = chain.number
        chains.remove(chain)
        tell_names(server, names, f"({number}) Chain closed. It was unused for 2 hours.\n")


def tell_names(server: Server, names: Sequence[str], line: str, *, skip: str | None = None) -> None:
    """Send one line to each connected person in ``names``."""
    from .utilities import get_clients

    wanted = {name.casefold() for name in names}
    skipped = skip.casefold() if skip else None
    for person in list(get_clients(server)):
        if person.name is None or person.name.casefold() == skipped:
            continue
        if person.name.casefold() in wanted:
            server.send(person, line)


def clients_for_names(server: Server, names: Sequence[str]) -> list[Client]:
    """Return logged-in clients whose names are in this list."""
    from .utilities import get_clients

    wanted = {name.casefold() for name in names}
    found: list[Client] = []
    for person in get_clients(server):
        if person.name is None or person.player is None or person.room is None:
            continue
        if person.name.casefold() in wanted and person not in found:
            found.append(person)
    return found
