"""The rank enum, its file words, and the coloured labels."""

from pathlib import Path

import pytest

from lupaxa.chat_server.colour import cyan, green, light_blue, magenta, red
from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.ranks import Rank, live_rank, rank_allows, rank_cell, rank_from_word, rank_label, saved_rank, shown_rank, shown_rank_cell
from lupaxa.chat_server.saved_player import SavedPlayer, hash_password, load_saved, player_path, save_player


def test_members_follow_the_saved_order_and_file_words() -> None:
    """Declaration order is the privilege order, and value is the file word."""
    assert [rank.value for rank in Rank] == ["newbie", "resident", "su_channel", "super_user", "admin", "coder", "super_admin"]
    assert rank_from_word("super_user") is Rank.SUPER_USER
    assert rank_from_word("super") is None
    assert rank_from_word("Super_User") is None
    assert str(Rank.RESIDENT) != "resident"


def test_rank_allows_uses_declaration_order() -> None:
    """A rank reaches its own rung and every rung below it."""
    assert rank_allows(Rank.CODER, Rank.ADMIN) is True
    assert rank_allows(Rank.SU_CHANNEL, Rank.RESIDENT) is True
    assert rank_allows(Rank.SU_CHANNEL, Rank.SUPER_USER) is False
    assert rank_allows(Rank.RESIDENT, Rank.RESIDENT) is True
    assert rank_allows(Rank.SUPER_USER, Rank.ADMIN) is False


def test_saved_rank_keeps_an_unknown_word() -> None:
    """A saved word maps to its member. Any other string is resident plus that word."""
    assert saved_rank("admin") == (Rank.ADMIN, None)
    assert saved_rank("newbie") == (Rank.RESIDENT, "newbie")
    assert saved_rank("super") == (Rank.RESIDENT, "super")
    assert saved_rank("") == (Rank.RESIDENT, "")
    assert live_rank("newbie") is Rank.NEWBIE
    assert live_rank("super") is Rank.RESIDENT


def test_labels_and_plain_cells_for_the_new_ranks() -> None:
    """Coder is magenta, Super Admin is light blue, and the bracket cells use braces and parentheses."""
    assert rank_label(Rank.SU_CHANNEL) == "SU Channel"
    assert rank_label(Rank.CODER) == "Coder"
    assert rank_label(Rank.SUPER_USER) == "Super User"
    assert shown_rank(Rank.NEWBIE, True) == green("Newbie")
    assert shown_rank(Rank.RESIDENT, True) == "Resident"
    assert shown_rank(Rank.SU_CHANNEL, True) == "SU Channel"
    assert shown_rank(Rank.SUPER_USER, True) == cyan("Super User")
    assert shown_rank(Rank.ADMIN, True) == red("Admin")
    assert shown_rank(Rank.CODER, True) == magenta("Coder")
    assert shown_rank(Rank.CODER, False) == "Coder"
    assert shown_rank(Rank.SUPER_ADMIN, True) == light_blue("Super Admin")
    assert shown_rank(Rank.SUPER_ADMIN, False) == "Super Admin"
    assert shown_rank(Rank.SUPER_USER, False) == "Super User"
    assert rank_cell(Rank.SUPER_USER) == "< Super User >"
    assert rank_cell(Rank.CODER) == "{ Coder }"
    assert rank_cell(Rank.SUPER_ADMIN) == "( Super Admin )"
    assert rank_cell(Rank.ADMIN) == "[ Admin ]"
    assert rank_cell(Rank.SU_CHANNEL) == "SU Channel"
    assert shown_rank_cell(Rank.CODER, True) == magenta("{ Coder }")
    assert shown_rank_cell(Rank.SUPER_ADMIN, True) == light_blue("( Super Admin )")
    assert shown_rank_cell(Rank.CODER, False) == "{ Coder }"


def test_lsu_cells_keep_the_rank_colour_off_duty() -> None:
    """Off Duty and Off List keep the rank brackets and the rank colour."""
    from lupaxa.chat_server.ranks import lsu_rank_cell

    assert lsu_rank_cell(Rank.CODER, off_duty=True, off_list=False, colour=True) == magenta("{ Off Duty }")
    assert lsu_rank_cell(Rank.CODER, off_duty=False, off_list=True, colour=True) == magenta("{ Off List }")
    assert lsu_rank_cell(Rank.SUPER_ADMIN, off_duty=True, off_list=False, colour=True) == light_blue("( Off Duty )")
    assert lsu_rank_cell(Rank.SUPER_ADMIN, off_duty=False, off_list=True, colour=True) == light_blue("( Off List )")
    assert lsu_rank_cell(Rank.ADMIN, off_duty=True, off_list=False, colour=True) == red("[ Off Duty ]")
    assert lsu_rank_cell(Rank.SU_CHANNEL, off_duty=False, off_list=False, colour=True) == cyan("< Channel Only >")


def test_su_channel_is_resident_below_and_super_user_channel_above() -> None:
    """Lower viewers see Resident. SU Channel and above see Super User Channel."""
    from lupaxa.chat_server.ranks import shown_person_cell, shown_person_rank

    assert shown_person_rank(Rank.RESIDENT, Rank.SU_CHANNEL, False) == "Resident"
    assert shown_person_rank(Rank.NEWBIE, Rank.SU_CHANNEL, True) == "Resident"
    assert shown_person_rank(Rank.SU_CHANNEL, Rank.SU_CHANNEL, False) == "Super User Channel"
    assert shown_person_rank(Rank.ADMIN, Rank.SU_CHANNEL, True) == cyan("Super User Channel")
    assert shown_person_cell(Rank.RESIDENT, Rank.SU_CHANNEL, True) == "Resident"
    assert shown_person_cell(Rank.SUPER_USER, Rank.SU_CHANNEL, True) == cyan("Super User Channel")
    assert "<" not in shown_person_cell(Rank.CODER, Rank.SU_CHANNEL, False)
    assert shown_person_cell(Rank.RESIDENT, Rank.SUPER_USER, True) == cyan("< Super User >")
    assert rank_label(Rank.SU_CHANNEL) == "SU Channel"


def test_rank_label_rejects_a_newbie() -> None:
    """Newbie has no saved label."""
    with pytest.raises(ConfigError, match=r"Unknown rank Rank\.NEWBIE\."):
        rank_label(Rank.NEWBIE)


def test_a_known_rank_round_trips_as_the_file_word(tmp_path: Path) -> None:
    """Saving a member writes the file word, and loading returns the member."""
    save_player(tmp_path, SavedPlayer("Ada", Rank.SUPER_USER, hash_password("pw", rounds=4)))
    text = player_path(tmp_path, "Ada").read_text(encoding="utf-8")
    assert "rank: super_user" in text
    assert "unknown_rank" not in text
    loaded = load_saved(player_path(tmp_path, "Ada"))
    assert loaded is not None
    assert loaded.rank is Rank.SUPER_USER
    assert loaded.unknown_rank is None


def test_an_unknown_rank_word_loads_as_resident_and_is_written_back(tmp_path: Path) -> None:
    """super stays on disk until something clears unknown_rank."""  # noqa: D403
    path = player_path(tmp_path, "Ada")
    path.write_text("name: Ada\nlowercase_name: ada\nrank: super\npassword: x\n", encoding="utf-8")
    loaded = load_saved(path)
    assert loaded is not None
    assert loaded.rank is Rank.RESIDENT
    assert loaded.unknown_rank == "super"
    save_player(tmp_path, loaded)
    assert "rank: super\n" in path.read_text(encoding="utf-8")
    assert "rank: resident" not in path.read_text(encoding="utf-8")


def test_a_saved_snapshot_keeps_an_unknown_rank_word() -> None:
    """Reboot stores the unknown word and loads it back as resident."""
    from lupaxa.chat_server.reboot import decode_saved, encode_saved

    saved = SavedPlayer("Ada", Rank.RESIDENT, "hash", unknown_rank="super")
    raw = encode_saved(saved)
    assert raw["rank"] == "super"
    loaded = decode_saved(raw)
    assert loaded.rank is Rank.RESIDENT
    assert loaded.unknown_rank == "super"


def test_a_rank_that_is_not_a_string_is_rejected(tmp_path: Path) -> None:
    """A missing rank, a bool, and a number still make the file invalid."""
    path = player_path(tmp_path, "Ada")
    path.write_text("name: Ada\nlowercase_name: ada\npassword: x\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="rank must be resident, su_channel, super_user, admin, coder, or super_admin"):
        load_saved(path)
    path.write_text("name: Ada\nlowercase_name: ada\nrank: true\npassword: x\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="rank must be resident, su_channel, super_user, admin, coder, or super_admin"):
        load_saved(path)
