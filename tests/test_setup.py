"""Scripted setup for the one super admin."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from totp_fixtures import TOTP_TEST_SEED

from lupaxa.chat_server.globals import RESERVED_NAMES
from lupaxa.chat_server.ranks import Rank
from lupaxa.chat_server.rooms import house_path_blocked
from lupaxa.chat_server.saved_player import SavedPlayer, hash_password, load_saved, password_ok, player_path, savable_name, save_player
from lupaxa.chat_server.setup import run_setup


def drive(
    tmp_path: Path,
    lines: list[str],
    secrets: list[str],
    on_write: Callable[[str, Path], None] | None = None,
) -> tuple[Path, list[str], list[str], list[str]]:
    """Run setup and return the players directory, writes, and prompts."""
    players = tmp_path / "players"
    players.mkdir(exist_ok=True)
    pending_lines = list(lines)
    pending_secrets = list(secrets)
    written: list[str] = []
    line_prompts: list[str] = []
    secret_prompts: list[str] = []

    def read_line(prompt: str) -> str:
        assert written[-1] == prompt
        line_prompts.append(prompt)
        return pending_lines.pop(0)

    def read_secret(prompt: str) -> str:
        assert written[-1] == prompt
        secret_prompts.append(prompt)
        return pending_secrets.pop(0)

    def write(text: str) -> None:
        written.append(text)
        if on_write is not None:
            on_write(text, players)

    run_setup(players, tmp_path / "rooms.yml", tmp_path / "help.yml", read_line=read_line, read_secret=read_secret, write=write)
    assert pending_lines == []
    assert pending_secrets == []
    return players, written, line_prompts, secret_prompts


def stored(players: Path, name: str) -> SavedPlayer:
    """Return the player file for ``name``."""
    loaded = load_saved(player_path(players, name))
    assert loaded is not None
    return loaded


def seed(players: Path, name: str, rank: Rank, *, mfa_secret: str | None = None, mfa_on: bool = False, description: str = "") -> SavedPlayer:
    """Write one player file with a fast hash."""
    saved = SavedPlayer(name, rank, hash_password("pw", rounds=4), mfa_secret=mfa_secret, mfa_on=mfa_on, description=description)
    save_player(players, saved)
    return saved


def test_new_name_writes_a_super_admin(tmp_path: Path) -> None:
    """A new name and two matching secrets become the super admin."""
    players, written, line_prompts, secret_prompts = drive(tmp_path, ["Ada"], ["secret", "secret"])
    saved = stored(players, "Ada")
    assert saved.rank == Rank.SUPER_ADMIN
    assert password_ok("secret", saved.password_hash)
    assert saved.mfa_secret is None
    assert saved.mfa_on is False
    assert line_prompts == ["Name:"]
    assert secret_prompts == ["Password:", "Password again:"]
    assert "Name:" in written
    assert "Password:" in written
    assert "Password again:" in written


def test_mismatched_password_writes_nothing_until_they_match(tmp_path: Path) -> None:
    """A second secret that differs writes no file, and a later pair does."""

    def on_write(text: str, players: Path) -> None:
        if text == "Those passwords do not match.\n":
            assert list(players.glob("*.yml")) == []

    players, written, _line_prompts, _secret_prompts = drive(tmp_path, ["Ada"], ["secret", "nope", "secret", "secret"], on_write)
    assert "Those passwords do not match.\n" in written
    assert password_ok("secret", stored(players, "Ada").password_hash)


def test_empty_password_is_refused(tmp_path: Path) -> None:
    """An empty secret prints the password problem and asks again."""
    _players, written, _line_prompts, _secret_prompts = drive(tmp_path, ["Ada"], ["", "secret", "secret"])
    assert "Password cannot be empty.\n" in written


def test_reserved_name_is_refused(tmp_path: Path) -> None:
    """A reserved name is refused and setup asks for another name."""
    assert savable_name("who") is True
    assert "who" in RESERVED_NAMES
    players, written, _line_prompts, _secret_prompts = drive(tmp_path, ["who", "Ada"], ["secret", "secret"])
    assert "who cannot be used. Pick another name.\n" in written
    assert stored(players, "Ada").rank == Rank.SUPER_ADMIN


def test_rooms_file_stem_is_refused(tmp_path: Path) -> None:
    """The rooms file stem is refused by house_path_blocked, then another name is saved."""
    rooms = tmp_path / "rooms.yml"
    help_path = tmp_path / "help.yml"
    assert savable_name("rooms") is True
    assert "rooms" not in RESERVED_NAMES
    assert house_path_blocked(rooms, help_path, "rooms") is True
    players, written, _line_prompts, _secret_prompts = drive(tmp_path, ["rooms", "Ada"], ["secret", "secret"])
    assert "rooms cannot be used. Pick another name.\n" in written
    assert stored(players, "Ada").rank == Rank.SUPER_ADMIN


def test_existing_resident_becomes_super_admin_without_a_password(tmp_path: Path) -> None:
    """An existing file keeps its password and MFA and only the rank changes."""
    players = tmp_path / "players"
    players.mkdir()
    before = seed(players, "Bea", Rank.RESIDENT, mfa_secret=TOTP_TEST_SEED, mfa_on=True, description="keeper")
    _players, written, line_prompts, secret_prompts = drive(tmp_path, ["Bea"], [])
    saved = stored(players, "Bea")
    assert saved.rank == Rank.SUPER_ADMIN
    assert saved.password_hash == before.password_hash
    assert saved.mfa_secret == TOTP_TEST_SEED
    assert saved.description == "keeper"
    assert line_prompts == ["Name:"]
    assert secret_prompts == []
    assert "Password:" not in "".join(written)


def test_change_password_keeps_mfa(tmp_path: Path) -> None:
    """Choice 1 replaces the hash and leaves the MFA secret."""
    players = tmp_path / "players"
    players.mkdir()
    before = seed(players, "Ada", Rank.SUPER_ADMIN, mfa_secret=TOTP_TEST_SEED)
    players, _written, line_prompts, secret_prompts = drive(tmp_path, ["1"], ["next", "next"])
    saved = stored(players, "Ada")
    assert saved.password_hash != before.password_hash
    assert password_ok("next", saved.password_hash)
    assert saved.mfa_secret == TOTP_TEST_SEED
    assert line_prompts == ["Choice:"]
    assert secret_prompts == ["Password:", "Password again:"]


def test_clear_mfa_keeps_the_password(tmp_path: Path) -> None:
    """Choice 2 clears the MFA secret and flag and leaves the hash."""
    players = tmp_path / "players"
    players.mkdir()
    before = seed(players, "Ada", Rank.SUPER_ADMIN, mfa_secret=TOTP_TEST_SEED, mfa_on=True)
    players, _written, _line_prompts, secret_prompts = drive(tmp_path, ["2"], [])
    saved = stored(players, "Ada")
    assert saved.mfa_secret is None
    assert saved.mfa_on is False
    assert saved.password_hash == before.password_hash
    assert secret_prompts == []


def test_replace_writes_the_old_rank_then_a_new_super_admin(tmp_path: Path) -> None:
    """Choice 3 demotes the current super admin before the new file is created."""
    players = tmp_path / "players"
    players.mkdir()
    seed(players, "Ada", Rank.SUPER_ADMIN)

    def on_write(text: str, directory: Path) -> None:
        if text == "Name:":
            assert stored(directory, "Ada").rank == Rank.RESIDENT
            assert list(directory.glob("*.yml")) == [player_path(directory, "Ada")]

    players, _written, line_prompts, _secret_prompts = drive(tmp_path, ["3", "resident", "Bea"], ["secret", "secret"], on_write)
    assert stored(players, "Ada").rank == Rank.RESIDENT
    assert stored(players, "Bea").rank == Rank.SUPER_ADMIN
    assert password_ok("secret", stored(players, "Bea").password_hash)
    assert line_prompts == ["Choice:", "Rank for Ada (resident, su_channel, super_user, admin, coder):", "Name:"]


def test_bad_rank_is_asked_again(tmp_path: Path) -> None:
    """A rank outside the five saved choices asks for the rank again."""
    players = tmp_path / "players"
    players.mkdir()
    seed(players, "Ada", Rank.SUPER_ADMIN)
    _players, written, line_prompts, _secret_prompts = drive(tmp_path, ["3", "nope", "resident", "Bea"], ["secret", "secret"])
    prompt = "Rank for Ada (resident, su_channel, super_user, admin, coder):"
    assert line_prompts.count(prompt) == 2
    assert written.count(prompt) == 2


def test_super_is_asked_again_and_coder_is_stored(tmp_path: Path) -> None:
    """The word super asks for the rank again, and Coder is stored as coder."""
    players = tmp_path / "players"
    players.mkdir()
    seed(players, "Ada", Rank.SUPER_ADMIN)
    players, written, line_prompts, _secret_prompts = drive(tmp_path, ["3", "super", "Coder", "Bea"], ["secret", "secret"])
    prompt = "Rank for Ada (resident, su_channel, super_user, admin, coder):"
    assert line_prompts.count(prompt) == 2
    assert written.count(prompt) == 2
    assert stored(players, "Ada").rank is Rank.CODER
    assert stored(players, "Bea").rank is Rank.SUPER_ADMIN


def test_moved_super_admin_cannot_be_the_new_name(tmp_path: Path) -> None:
    """The person just moved is refused and setup asks for a name again."""
    players = tmp_path / "players"
    players.mkdir()
    seed(players, "Ada", Rank.SUPER_ADMIN)
    players, written, line_prompts, _secret_prompts = drive(tmp_path, ["3", "resident", "Ada", "Bea"], ["secret", "secret"])
    assert "Ada is the super admin you just moved. Pick another name.\n" in written
    assert line_prompts.count("Name:") == 2
    assert stored(players, "Ada").rank == Rank.RESIDENT
    assert stored(players, "Bea").rank == Rank.SUPER_ADMIN


def test_extra_super_admins_are_moved_before_the_menu(tmp_path: Path) -> None:
    """Extra super admins are ranked before the menu is shown."""
    players = tmp_path / "players"
    players.mkdir()
    seed(players, "Bea", Rank.SUPER_ADMIN)
    seed(players, "Ada", Rank.SUPER_ADMIN)
    players, written, line_prompts, _secret_prompts = drive(tmp_path, ["Ada", "super_user", "2"], [])
    text = "".join(written)
    assert text.startswith("More than one super admin exists.\nAda\nBea\nWhich super admin remains?")
    assert text.index("Rank for Bea (resident, su_channel, super_user, admin, coder):") < text.index("A super admin already exists.")
    assert line_prompts[0] == "Which super admin remains?"
    assert stored(players, "Ada").rank == Rank.SUPER_ADMIN
    assert stored(players, "Bea").rank == Rank.SUPER_USER


def test_bad_choice_is_asked_again(tmp_path: Path) -> None:
    """A choice other than 1, 2, or 3 asks for Choice: again."""
    players = tmp_path / "players"
    players.mkdir()
    seed(players, "Ada", Rank.SUPER_ADMIN)
    _players, written, line_prompts, _secret_prompts = drive(tmp_path, ["9", "2"], [])
    assert line_prompts == ["Choice:", "Choice:"]
    assert written.count("Choice:") == 2
