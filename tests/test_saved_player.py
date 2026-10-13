"""Saved player files."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.saved_player import (
    RANK_ADMIN,
    RANK_NEWBIE,
    RANK_RESIDENT,
    RANK_SUPER,
    SavedPlayer,
    delete_player,
    ensure_players_directory,
    hash_password,
    load_saved,
    password_ok,
    password_problem,
    player_path,
    rank_allows,
    rank_label,
    savable_name,
    save_player,
)
from lupaxa.chat_server.utilities import display_address


def test_savable_name_is_three_to_twenty_letters_or_digits() -> None:
    """A saved name matches the typed-name rule."""
    assert savable_name("Admin") is True
    assert savable_name("Ada1") is True
    assert savable_name("A" * 20) is True
    assert savable_name("Bea Smith") is False
    assert savable_name("ab") is False
    assert savable_name("1Ada") is False
    assert savable_name("A" * 21) is False
    assert savable_name("a..b") is False
    assert savable_name("a/b") is False


def test_player_path_lowercases_the_file_name(tmp_path: Path) -> None:
    """The file name is the name in lowercase, plus ``.yml``."""
    assert str(player_path(tmp_path, "BeaSmith")).endswith("beasmith.yml")


def test_hash_password_verifies_and_hides_the_password() -> None:
    """The stored value is a hash, and only the original password matches."""
    hashed = hash_password("secret", rounds=4)
    assert hashed != "secret"
    assert password_ok("secret", hashed) is True
    assert password_ok("nope", hashed) is False


def test_password_problem_rejects_empty_and_over_72_bytes() -> None:
    """Empty and over-long passwords are the two sentences. 72 bytes is fine."""
    assert password_problem("") == "Password cannot be empty.\n"
    assert password_problem("x" * 73) == "Password is too long.\n"
    assert password_problem("secret") is None
    assert password_problem("x" * 72) is None


def test_rank_allows_when_have_is_at_least_need() -> None:
    """A rank allows itself and every rank below it."""
    assert rank_allows(RANK_ADMIN, RANK_NEWBIE) is True
    assert rank_allows(RANK_RESIDENT, RANK_RESIDENT) is True
    assert rank_allows(RANK_SUPER, RANK_ADMIN) is False
    assert rank_allows(RANK_NEWBIE, RANK_RESIDENT) is False


def test_rank_label_names_saved_ranks() -> None:
    """Saved ranks use the labels players see."""
    assert rank_label(RANK_RESIDENT) == "Resident"
    assert rank_label(RANK_SUPER) == "Super User"
    assert rank_label(RANK_ADMIN) == "Admin"


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    """A saved player comes back with the same spelling, rank, and logout time."""
    logged_out = datetime(2026, 10, 12, 1, 16, tzinfo=timezone.utc)
    hashed = hash_password("secret", rounds=4)
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed, logged_out))
    path = player_path(tmp_path, "Bea")
    assert load_saved(path) == SavedPlayer("Bea", RANK_RESIDENT, hashed, logged_out)
    assert "secret" not in path.read_text(encoding="utf-8")


def test_same_key_updates_the_display_spelling(tmp_path: Path) -> None:
    """The same key may update the display spelling instead of creating a twin file."""
    hashed = hash_password("secret", rounds=4)
    save_player(tmp_path, SavedPlayer("Bob", RANK_RESIDENT, hashed))
    path = player_path(tmp_path, "Bob")
    assert path.name == "bob.yml"
    assert "logged_out_at" not in path.read_text(encoding="utf-8")
    save_player(tmp_path, SavedPlayer("bob", RANK_RESIDENT, hashed))
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.name == "bob"
    assert loaded.lowercase_name == "bob"
    assert path.name == "bob.yml"


def test_delete_player_removes_the_file_once(tmp_path: Path) -> None:
    """The first delete removes the file. The second finds nothing."""
    hashed = hash_password("secret", rounds=4)
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed))
    assert delete_player(tmp_path, "Bea") is True
    assert delete_player(tmp_path, "Bea") is False
    assert load_saved(player_path(tmp_path, "Bea")) is None


def test_bad_player_file_names_the_path(tmp_path: Path) -> None:
    """A file whose rank is not a saved rank names that file in the error."""
    path = tmp_path / "bea.yml"
    path.write_text("name: Bea\nrank: newbie\npassword: hash\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="bea.yml"):
        load_saved(path)


def test_ensure_players_directory(tmp_path: Path) -> None:
    """A missing directory is created. A file at that path is a config error."""
    missing = tmp_path / "players"
    ensure_players_directory(missing)
    assert missing.is_dir()
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match="players must be a directory"):
        ensure_players_directory(blocked)


def test_display_address_unwraps_ipv4_mapped_ipv6() -> None:
    """An IPv4 address carried as IPv6 is shown without the prefix."""
    assert display_address("::ffff:10.0.0.5") == "10.0.0.5"
    assert display_address("::FFFF:10.0.0.5") == "10.0.0.5"
    assert display_address("10.0.0.5") == "10.0.0.5"
    assert display_address("2001:db8::1") == "2001:db8::1"
    assert display_address("") == "unknown"


def test_an_old_address_file_loads_as_ip_and_hostname(tmp_path: Path) -> None:
    """A file that still has address loads, and the next save writes the new keys."""
    path = tmp_path / "bea.yml"
    path.write_text(
        "name: Bea\nlowercase_name: bea\nrank: resident\npassword: hashed\naddress: 10.0.0.5\n",
        encoding="utf-8",
    )
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.ip_address == "10.0.0.5"
    assert loaded.hostname == "10.0.0.5"
    save_player(tmp_path, loaded)
    text = path.read_text(encoding="utf-8")
    assert "ip_address: 10.0.0.5" in text
    assert "hostname: 10.0.0.5" in text
    assert not any(line.startswith("address:") for line in text.splitlines())


def test_address_round_trips_and_is_omitted_when_absent(tmp_path: Path) -> None:
    """A stored address comes back, and an older file has none."""
    hashed = hash_password("secret", rounds=4)
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed, ip_address="10.0.0.5"))
    loaded = load_saved(tmp_path / "bea.yml")
    assert loaded is not None
    assert loaded.ip_address == "10.0.0.5"
    assert loaded.hostname == "10.0.0.5"
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed))
    again = load_saved(tmp_path / "bea.yml")
    assert again is not None
    assert again.ip_address is None
    assert again.hostname == ""
    text = (tmp_path / "bea.yml").read_text(encoding="utf-8")
    assert "ip_address:" not in text
    assert not any(line.startswith("address:") for line in text.splitlines())


def test_load_saved_rejects_non_string_address(tmp_path: Path) -> None:
    """A numeric address in the file raises ConfigError naming address."""
    path = tmp_path / "bea.yml"
    path.write_text(
        "name: Bea\nrank: resident\npassword: hash\naddress: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="address"):
        load_saved(path)


def test_load_saved_names_a_file_that_is_not_utf8(tmp_path: Path) -> None:
    """A file that is not UTF-8 raises ConfigError naming the path, not UnicodeDecodeError."""
    path = player_path(tmp_path, "Bea")
    path.write_bytes(b"name: Bea\nrank: resident\npassword: \xff\xfe\n")
    with pytest.raises(ConfigError, match="is not UTF-8") as caught:
        load_saved(path)
    assert str(path) in str(caught.value)
    assert not isinstance(caught.value, UnicodeDecodeError)


def test_connected_seconds_round_trip_and_are_omitted_when_zero(tmp_path: Path) -> None:
    """The total comes back from the file, and a zero total is not written."""
    hashed = hash_password("secret", rounds=4)
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed, connected_seconds=90))
    path = player_path(tmp_path, "Bea")
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.connected_seconds == 90
    assert "connected_seconds: 90" in path.read_text(encoding="utf-8")
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed, connected_seconds=0))
    assert "connected_seconds" not in path.read_text(encoding="utf-8")
    again = load_saved(path)
    assert again is not None
    assert again.connected_seconds == 0


@pytest.mark.parametrize("value", ["-1", '"no"', "true", "1.5"])
def test_load_saved_rejects_a_bad_connected_seconds(tmp_path: Path, value: str) -> None:
    """A negative, bool, float, or text total raises ConfigError naming connected_seconds."""
    path = tmp_path / "bea.yml"
    path.write_text(
        f"name: Bea\nrank: resident\npassword: hash\nconnected_seconds: {value}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="connected_seconds"):
        load_saved(path)


def test_prompt_round_trips_and_is_omitted_when_none(tmp_path: Path) -> None:
    """A saved prompt comes back from the file, and no prompt writes no key."""
    hashed = hash_password("secret", rounds=4)
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed, prompt="Go: "))
    path = player_path(tmp_path, "Bea")
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.prompt == "Go: "
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed, prompt=None))
    assert "prompt" not in path.read_text(encoding="utf-8")
    again = load_saved(path)
    assert again is not None
    assert again.prompt is None


@pytest.mark.parametrize("value", ["1", "''", "true", "[a]"])
def test_load_saved_rejects_a_bad_prompt(tmp_path: Path, value: str) -> None:
    """A prompt that is not a non-empty string raises ConfigError naming prompt."""
    path = tmp_path / "bea.yml"
    path.write_text(
        f"name: Bea\nrank: resident\npassword: hash\nprompt: {value}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="prompt"):
        load_saved(path)


def test_a_file_without_a_lowercase_name_still_loads(tmp_path: Path) -> None:
    """A file written before the key existed gets the key from the display spelling."""
    path = tmp_path / "bob.yml"
    path.write_text("name: Bob\nrank: resident\npassword: hashed\n", encoding="utf-8")
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.name == "Bob"
    assert loaded.lowercase_name == "bob"


def test_save_writes_the_key_and_allows_a_new_spelling(tmp_path: Path) -> None:
    """The same key can change capitalisation. The file name does not move."""
    save_player(tmp_path, SavedPlayer("Bob", RANK_RESIDENT, "hashed"))
    first = load_saved(player_path(tmp_path, "bob"))
    assert first is not None
    assert first.lowercase_name == "bob"
    text = player_path(tmp_path, "bob").read_text(encoding="utf-8")
    assert "lowercase_name: bob" in text
    save_player(tmp_path, SavedPlayer("BoB", RANK_RESIDENT, "hashed", lowercase_name="bob"))
    second = load_saved(player_path(tmp_path, "bob"))
    assert second is not None
    assert second.name == "BoB"
    assert second.lowercase_name == "bob"
    assert player_path(tmp_path, "bob").name == "bob.yml"


def test_colour_round_trips_and_is_omitted_when_on(tmp_path: Path) -> None:
    """Off is stored as false. On writes no colour key and loads as on."""
    hashed = hash_password("secret", rounds=4)
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed, colour=False))
    path = player_path(tmp_path, "Bea")
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.colour is False
    assert "colour: false" in path.read_text(encoding="utf-8")
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed, colour=True))
    assert "colour" not in path.read_text(encoding="utf-8")
    again = load_saved(path)
    assert again is not None
    assert again.colour is True


def test_load_saved_rejects_a_bad_colour(tmp_path: Path) -> None:
    """A colour that is not true or false raises ConfigError."""
    path = tmp_path / "bea.yml"
    path.write_text(
        "name: Bea\nrank: resident\npassword: hash\ncolour: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="colour"):
        load_saved(path)


def test_description_round_trips_and_is_omitted_when_blank(tmp_path: Path) -> None:
    """A description is stored, and a blank one writes no key."""
    hashed = hash_password("secret", rounds=4)
    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hashed, description="Hello"),
    )
    path = player_path(tmp_path, "Bea")
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.description == "Hello"
    assert "description: Hello" in path.read_text(encoding="utf-8")
    save_player(tmp_path, SavedPlayer("Bea", RANK_RESIDENT, hashed, description=""))
    assert "description" not in path.read_text(encoding="utf-8")
    again = load_saved(path)
    assert again is not None
    assert again.description == ""


def test_load_saved_rejects_a_bad_description(tmp_path: Path) -> None:
    """An empty, long, or multi-line description raises ConfigError."""
    path = tmp_path / "bea.yml"
    path.write_text(
        "name: Bea\nrank: resident\npassword: hash\ndescription: ''\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="description"):
        load_saved(path)


def test_logout_keeps_colour_off(tmp_path: Path) -> None:
    """A later write of the file does not drop a stored colour of off."""
    from lupaxa.chat_server.player import Player
    from lupaxa.chat_server.saved_player import record_saved_logout

    save_player(
        tmp_path,
        SavedPlayer("Bea", RANK_RESIDENT, hash_password("secret", rounds=4), colour=False),
    )
    user = Player(
        "Bea",
        "hall",
        datetime.now(timezone.utc),
        rank=RANK_RESIDENT,
        colour=False,
    )
    record_saved_logout(tmp_path, user)
    saved = load_saved(player_path(tmp_path, "bea"))
    assert saved is not None
    assert saved.colour is False
