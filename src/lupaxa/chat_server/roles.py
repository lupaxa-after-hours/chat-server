"""Role flags stored as one integer."""

from collections.abc import Iterable
from enum import IntFlag, auto


class Roles(IntFlag):
    """Bits stored in a player's roles integer."""

    SITE_ADMIN = auto()


ROLE_WORDS = {Roles.SITE_ADMIN: "site_admin"}
ROLE_LABELS = {Roles.SITE_ADMIN: "Site Admin"}


def appoint_to_role(roles: int, role: Roles) -> int:
    """Return ``roles`` with ``role`` set."""
    return int(Roles(roles) | role)


def dismiss_from_role(roles: int, role: Roles) -> int:
    """Return ``roles`` with ``role`` cleared."""
    return roles & ~int(role)


def role_from_name(text: str) -> Roles | None:
    """Return the flag for a typed name, or None when it is not one."""
    folded = text.casefold()
    for role, word in ROLE_WORDS.items():
        if word.casefold() == folded:
            return role
    return None


def role_names(mask: int) -> tuple[str, ...]:
    """Return the display labels in ``mask``, in flag order."""
    return tuple(ROLE_LABELS[role] for role in ROLE_WORDS if mask & int(role))


def roles_phrase(mask: int) -> str:
    """Return the named roles in flag order, separated by commas."""
    return ", ".join(role_names(mask))


def join_names(names: Iterable[str]) -> str:
    """Return names in casefold order, with & before the last name."""
    ordered = sorted(names, key=str.casefold)
    if len(ordered) < 2:
        return ordered[0] if ordered else ""
    if len(ordered) == 2:
        return f"{ordered[0]} & {ordered[1]}"
    return f"{', '.join(ordered[:-1])} & {ordered[-1]}"
