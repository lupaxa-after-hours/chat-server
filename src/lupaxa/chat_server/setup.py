"""Create or maintain the one super admin player file."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .globals import RESERVED_NAMES
from .ranks import Rank
from .rooms import house_path_blocked
from .saved_player import SavedPlayer, hash_password, load_saved, password_problem, player_path, savable_name, save_player, super_admins

ALLOWED_RANKS = {
    Rank.RESIDENT.value: Rank.RESIDENT,
    Rank.SU_CHANNEL.value: Rank.SU_CHANNEL,
    Rank.SUPER_USER.value: Rank.SUPER_USER,
    Rank.ADMIN.value: Rank.ADMIN,
    Rank.CODER.value: Rank.CODER,
}


def run_setup(players: Path, rooms: Path, help_path: Path, *, read_line: Callable[[str], str], read_secret: Callable[[str], str], write: Callable[[str], None]) -> None:
    """Create or maintain the one super admin file."""

    def ask(prompt: str, *, secret: bool = False) -> str:
        write(prompt)
        if secret:
            return read_secret(prompt)
        return read_line(prompt)

    def read_password() -> str:
        while True:
            password = ask("Password:", secret=True)
            problem = password_problem(password)
            if problem is not None:
                write(problem)
                continue
            again = ask("Password again:", secret=True)
            if again != password:
                write("Those passwords do not match.\n")
                continue
            return password

    def read_rank(name: str) -> Rank:
        prompt = f"Rank for {name} (resident, su_channel, super_user, admin, coder):"
        while True:
            rank = ALLOWED_RANKS.get(ask(prompt).strip().casefold())
            if rank is not None:
                return rank

    def read_new_super_admin(moved: str | None) -> None:
        while True:
            name = ask("Name:").strip()
            if moved is not None and name.casefold() == moved.casefold():
                write(f"{name} is the super admin you just moved. Pick another name.\n")
                continue
            if not savable_name(name) or name.lower() in RESERVED_NAMES or house_path_blocked(rooms, help_path, name):
                write(f"{name} cannot be used. Pick another name.\n")
                continue
            existing = load_saved(player_path(players, name))
            if existing is not None:
                save_player(players, replace(existing, rank=Rank.SUPER_ADMIN, unknown_rank=None))
                return
            save_player(players, SavedPlayer(name, Rank.SUPER_ADMIN, hash_password(read_password())))
            return

    def maintain(saved: SavedPlayer) -> None:
        write("A super admin already exists.\n")
        write("1 Change the password\n")
        write("2 Clear MFA\n")
        write("3 Replace the super admin\n")
        while True:
            choice = ask("Choice:").strip()
            if choice == "1":
                save_player(players, replace(saved, password_hash=hash_password(read_password())))
                return
            if choice == "2":
                save_player(players, replace(saved, mfa_secret=None, mfa_on=False))
                return
            if choice == "3":
                rank = read_rank(saved.name)
                save_player(players, replace(saved, rank=rank, unknown_rank=None))
                read_new_super_admin(saved.name)
                return

    found = super_admins(players)
    if len(found) > 1:
        write("More than one super admin exists.\n")
        for admin in found:
            write(f"{admin.name}\n")
        by_name = {admin.name.casefold(): admin for admin in found}
        chosen: SavedPlayer | None = None
        while chosen is None:
            chosen = by_name.get(ask("Which super admin remains?").strip().casefold())
        for admin in found:
            if admin.name.casefold() == chosen.name.casefold():
                continue
            save_player(players, replace(admin, rank=read_rank(admin.name), unknown_rank=None))
        found = (chosen,)
    if len(found) == 1:
        maintain(found[0])
        return
    read_new_super_admin(None)
