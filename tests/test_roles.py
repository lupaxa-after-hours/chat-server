"""Role flags and the integer stored on a player file."""

from pathlib import Path

import pytest

from lupaxa.chat_server.colour import magenta
from lupaxa.chat_server.commands import Command, command_allowed, command_words, ensure_commands
from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.help import load_help
from lupaxa.chat_server.newbie import permissions_line, visible_help
from lupaxa.chat_server.ranks import Rank
from lupaxa.chat_server.reboot import copy_saved, decode_saved, encode_saved
from lupaxa.chat_server.roles import Roles, appoint_to_role, dismiss_from_role, join_names, role_from_name, role_names
from lupaxa.chat_server.saved_player import SavedPlayer, hash_password, load_saved, player_path, save_player


def test_site_admin_is_the_first_flag() -> None:
    """The first flag is 1, and the helpers set and clear that bit."""
    assert int(Roles.SITE_ADMIN) == 1
    assert appoint_to_role(0, Roles.SITE_ADMIN) == 1
    assert appoint_to_role(1, Roles.SITE_ADMIN) == 1
    assert dismiss_from_role(1, Roles.SITE_ADMIN) == 0
    assert dismiss_from_role(0, Roles.SITE_ADMIN) == 0


def test_unnamed_bits_stay_and_are_not_listed() -> None:
    """A bit with no name survives appoint and dismiss and is not shown."""
    stored = appoint_to_role(2, Roles.SITE_ADMIN)
    assert stored == 3
    assert role_names(stored) == ("Site Admin",)
    assert role_names(2) == ()
    assert dismiss_from_role(stored, Roles.SITE_ADMIN) == 2


def test_site_admin_is_the_only_role_name() -> None:
    """The typed name site_admin maps to the flag. The label does not."""
    assert role_from_name("site_admin") is Roles.SITE_ADMIN
    assert role_from_name("Site_Admin") is Roles.SITE_ADMIN
    assert role_from_name("SITE_ADMIN") is Roles.SITE_ADMIN
    assert role_from_name("site admin") is None
    assert role_from_name("Site Admin") is None
    assert role_from_name("widget") is None
    assert role_from_name("site") is None


def test_join_names_sorts_casefold_and_uses_ampersand() -> None:
    """Two names use &, and three or more put & before the last name."""
    assert join_names(["Bea", "ada"]) == "ada & Bea"
    assert join_names(["Cora", "ada", "Bea"]) == "ada, Bea & Cora"
    assert join_names(["Ada"]) == "Ada"


def test_saved_roles_round_trip_through_reboot_helpers() -> None:
    """Reboot encode, decode, and copy keep roles and leave home false."""
    stored = appoint_to_role(2, Roles.SITE_ADMIN)
    assert stored == 3
    original = SavedPlayer("Ada", Rank.RESIDENT, hash_password("pw", rounds=4), roles=stored)
    assert original.home is False
    decoded = decode_saved(encode_saved(original))
    assert decoded.roles == 3
    assert decoded.home is False
    copied = copy_saved(original)
    assert copied.roles == 3
    assert copied.home is False


def test_roles_round_trip_and_zero_is_omitted(tmp_path: Path) -> None:
    """A non-zero flag is written as an integer. Zero leaves the key out."""
    save_player(tmp_path, SavedPlayer("Ada", Rank.RESIDENT, hash_password("pw", rounds=4), roles=1))
    text = player_path(tmp_path, "Ada").read_text(encoding="utf-8")
    assert "roles: 1" in text
    loaded = load_saved(player_path(tmp_path, "Ada"))
    assert loaded is not None
    assert loaded.roles == 1
    save_player(tmp_path, SavedPlayer("Bea", Rank.RESIDENT, hash_password("pw", rounds=4)))
    bea = player_path(tmp_path, "Bea").read_text(encoding="utf-8")
    assert "roles:" not in bea
    plain = load_saved(player_path(tmp_path, "Bea"))
    assert plain is not None
    assert plain.roles == 0


@pytest.mark.parametrize("text", ["roles: true\n", "roles: false\n", "roles: -1\n", "roles: site\n"])
def test_roles_rejects_a_bool_a_negative_and_a_word(tmp_path: Path, text: str) -> None:
    """true, false, a negative, and a word are not a roles value."""
    path = tmp_path / "ada.yml"
    path.write_text(f"name: Ada\nlowercase_name: ada\nrank: resident\npassword: x\n{text}", encoding="utf-8")
    with pytest.raises(ConfigError, match="roles must be a whole number"):
        load_saved(path)


def noop(*_args: object) -> None:
    """Do nothing. Command rows in these tests are never called."""


def test_role_zero_uses_rank_only() -> None:
    """A command with no role ignores combination and the player's bits."""
    command = Command("say", noop, False, combination="and")
    assert command_allowed(Rank.NEWBIE, 0, command) is True


def test_or_allows_the_rank_or_any_bit() -> None:
    """Or matches the rank, the site-admin bit, or another bit in the mask."""
    mask = int(Roles.SITE_ADMIN) | 2
    command = Command("shutdown", noop, False, rank=Rank.ADMIN, role=mask, combination="or")
    assert command_allowed(Rank.ADMIN, 0, command) is True
    assert command_allowed(Rank.RESIDENT, int(Roles.SITE_ADMIN), command) is True
    assert command_allowed(Rank.RESIDENT, 2, command) is True
    assert command_allowed(Rank.RESIDENT, 0, command) is False
    assert command_allowed(Rank.SUPER_USER, 0, command) is False


def test_and_needs_the_rank_and_a_bit() -> None:
    """And matches only when the rank is high enough and a mask bit is set."""
    command = Command("shutdown", noop, False, rank=Rank.ADMIN, role=int(Roles.SITE_ADMIN), combination="and")
    assert command_allowed(Rank.ADMIN, 0, command) is False
    assert command_allowed(Rank.ADMIN, int(Roles.SITE_ADMIN), command) is True
    assert command_allowed(Rank.RESIDENT, int(Roles.SITE_ADMIN), command) is False


def test_shutdown_is_listed_for_coder_rank_or_the_site_admin_role() -> None:
    """The open command list and the help book use the same or test."""
    ensure_commands()
    assert "shutdown" in command_words(Rank.CODER).split()
    assert "shutdown" not in command_words(Rank.ADMIN).split()
    assert "shutdown" not in command_words(Rank.RESIDENT).split()
    assert "shutdown" not in command_words(Rank.SUPER_USER).split()
    assert "shutdown" in command_words(Rank.RESIDENT, roles=int(Roles.SITE_ADMIN)).split()
    book = load_help(Path(__file__).resolve().parents[1] / "config" / "help.yml")
    hidden = visible_help(book, Rank.RESIDENT, mfa_enabled=False)
    shown = visible_help(book, Rank.RESIDENT, mfa_enabled=False, roles=int(Roles.SITE_ADMIN))
    assert hidden.find("shutdown") is None
    assert shown.find("shutdown") is not None


def test_shutdown_help_names_the_role_on_the_permissions_line() -> None:
    """A role command shows rank, or, and the label. A rank-only command shows the rank."""
    from lupaxa.chat_server.commands import by_name
    from lupaxa.chat_server.help import HelpEntry, format_page

    ensure_commands()
    command = by_name()["shutdown"]
    page = format_page(HelpEntry("shutdown", (), "Stop."), command.syntax, 80, visible=frozenset(), permissions=permissions_line(command, False))
    assert "Permissions: Coder or Site Admin" in page
    assert "Roles" not in page
    assert "Rank" not in page
    say = format_page(HelpEntry("say", (), "Talk."), "<message>", 80, visible=frozenset(), permissions="Newbie")
    assert "Permissions: Newbie" in say
    assert " or " not in say
    assert "Roles" not in say


def test_permissions_line_uses_the_stored_combination() -> None:
    """Or and and are the words the command stores. No role is the rank alone."""
    from lupaxa.chat_server.commands import by_name

    ensure_commands()
    shutdown = by_name()["shutdown"]
    assert permissions_line(shutdown, False) == "Coder or Site Admin"
    assert permissions_line(shutdown, True) == magenta("Coder") + " or Site Admin"
    both = Command("gate", noop, False, rank=Rank.ADMIN, role=int(Roles.SITE_ADMIN), combination="and")
    assert permissions_line(both, False) == "Admin and Site Admin"
    shout = by_name()["shout"]
    assert permissions_line(shout, False) == "Resident"
    reload = by_name()["reload"]
    config = next(child for child in reload.subcommands or () if child.name == "config")
    assert permissions_line(config, False) == "Coder or Site Admin"
