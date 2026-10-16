"""Rank order, file words, and the coloured rank text."""

from __future__ import annotations

from enum import Enum

from .colour import cyan, green, light_blue, magenta, red
from .exceptions import ConfigError


class Rank(Enum):
    """One rung on the ladder. The value is the player-file word."""

    NEWBIE = "newbie"
    RESIDENT = "resident"
    SU_CHANNEL = "su_channel"
    SUPER_USER = "super_user"
    ADMIN = "admin"
    CODER = "coder"
    SUPER_ADMIN = "super_admin"


SAVED_RANKS = frozenset(rank for rank in Rank if rank is not Rank.NEWBIE)
LABELS = {
    Rank.RESIDENT: "Resident",
    Rank.SU_CHANNEL: "SU Channel",
    Rank.SUPER_USER: "Super User",
    Rank.ADMIN: "Admin",
    Rank.CODER: "Coder",
    Rank.SUPER_ADMIN: "Super Admin",
}


def rank_from_word(word: str) -> Rank | None:
    """Return the member whose file word is ``word``, or None."""
    for rank in Rank:
        if rank.value == word:
            return rank
    return None


def saved_rank(word: str) -> tuple[Rank, str | None]:
    """Return a saved member, or resident plus the unknown word."""
    found = rank_from_word(word)
    if found is not None and found in SAVED_RANKS:
        return found, None
    return Rank.RESIDENT, word


def live_rank(word: str) -> Rank:
    """Return a live rank. An unknown word is resident."""
    found = rank_from_word(word)
    if found is None:
        return Rank.RESIDENT
    return found


def rank_allows(have: Rank, need: Rank) -> bool:
    """Return whether ``have`` is ``need`` or a higher rank."""
    order = list(Rank)
    return order.index(have) >= order.index(need)


def rank_label(rank: Rank) -> str:
    """Return the label players see for a saved rank."""
    try:
        return LABELS[rank]
    except KeyError as exc:
        raise ConfigError(f"Unknown rank {rank}.") from exc


def rank_words(rank: Rank) -> str:
    """Return the examine rank words, with no brackets."""
    if rank is Rank.NEWBIE:
        return "Newbie"
    return rank_label(rank)


def shown_rank(rank: Rank, enabled: bool) -> str:
    """Return the examine rank words, coloured when ``enabled`` is true."""
    words = rank_words(rank)
    if not enabled:
        return words
    if rank is Rank.NEWBIE:
        return green(words)
    if rank is Rank.SUPER_USER:
        return cyan(words)
    if rank is Rank.ADMIN:
        return red(words)
    if rank is Rank.CODER:
        return magenta(words)
    if rank is Rank.SUPER_ADMIN:
        return light_blue(words)
    return words


def duty_cell(rank: Rank, label: str, colour: bool) -> str:
    """Return ``label`` in the brackets and colour of ``rank``."""
    if rank is Rank.SUPER_ADMIN:
        cell = f"( {label} )"
    elif rank is Rank.ADMIN:
        cell = f"[ {label} ]"
    elif rank is Rank.CODER:
        cell = f"{{ {label} }}"
    elif rank in (Rank.SUPER_USER, Rank.SU_CHANNEL):
        cell = f"< {label} >"
    else:
        cell = label
    if not colour:
        return cell
    if rank in (Rank.SUPER_USER, Rank.SU_CHANNEL):
        return cyan(cell)
    if rank is Rank.ADMIN:
        return red(cell)
    if rank is Rank.CODER:
        return magenta(cell)
    if rank is Rank.SUPER_ADMIN:
        return light_blue(cell)
    return cell


def rank_cell(rank: Rank) -> str:
    """Return the list rank cell. Each rank keeps its own brackets."""
    label = rank_words(rank)
    if rank is Rank.SUPER_ADMIN:
        return f"( {label} )"
    if rank is Rank.ADMIN:
        return f"[ {label} ]"
    if rank is Rank.CODER:
        return f"{{ {label} }}"
    if rank is Rank.SUPER_USER:
        return f"< {label} >"
    return label


def shown_rank_cell(rank: Rank, enabled: bool) -> str:
    """Return the list rank cell, coloured when ``enabled`` is true."""
    cell = rank_cell(rank)
    if not enabled:
        return cell
    if rank is Rank.SUPER_USER:
        return cyan(cell)
    if rank is Rank.ADMIN:
        return red(cell)
    if rank is Rank.CODER:
        return magenta(cell)
    if rank is Rank.SUPER_ADMIN:
        return light_blue(cell)
    return cell


def shown_person_rank(viewer: Rank, person: Rank, enabled: bool) -> str:
    """Return the examine words this viewer sees for this person."""
    if person is Rank.SU_CHANNEL and rank_allows(viewer, Rank.SU_CHANNEL):
        words = "Super User Channel"
        return cyan(words) if enabled else words
    seen = Rank.RESIDENT if person is Rank.SU_CHANNEL else person
    return shown_rank(seen, enabled)


def shown_person_cell(viewer: Rank, person: Rank, enabled: bool) -> str:
    """Return the who cell this viewer sees for this person."""
    if person is Rank.SU_CHANNEL:
        return shown_person_rank(viewer, person, enabled)
    return shown_rank_cell(person, enabled)


def lsu_rank_cell(rank: Rank, *, off_duty: bool, off_list: bool, colour: bool) -> str:
    """Return the lsu rank cell. Off Duty wins over Off List, in the rank's colour."""
    if off_duty:
        return duty_cell(rank, "Off Duty", colour)
    if off_list:
        return duty_cell(rank, "Off List", colour)
    if rank is Rank.SU_CHANNEL:
        return duty_cell(rank, "Channel Only", colour)
    return shown_rank_cell(rank, colour)
