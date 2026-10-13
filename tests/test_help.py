"""Help file loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.help import (
    HelpBook,
    HelpEntry,
    format_entry,
    format_index,
    load_help,
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "help.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_format_index_sorts_names_and_skips_empty_aliases() -> None:
    """The index is alphabetical, and a command with no aliases has no aliases line."""
    book = HelpBook(
        entries=(
            HelpEntry("say", ("'", '"', "`"), "Speak."),
            HelpEntry("quit", (), "Leave."),
        )
    )
    assert format_index(book) == ("Commands: quit say\nType help <command> for more.\n")
    assert format_entry(book.entries[1]) == "quit\nLeave.\n"
    assert format_index(HelpBook(entries=())) == ("Commands:\nType help <command> for more.\n")


def test_format_entry_lists_aliases_in_file_order() -> None:
    """Aliases stay in the order the file gave them."""
    entry = HelpEntry("say", ("'", '"', "`"), "Speak.")
    assert format_entry(entry) == "say\nAliases: ' \" `\nSpeak.\n"


def test_load_finds_a_name_or_an_alias(tmp_path: Path) -> None:
    """Lookup is case-sensitive and keeps YAML bool words as strings."""
    path = _write(
        tmp_path,
        "- name: say\n  aliases: ['on', \"'\"]\n  text: '  Speak.  '\n",
    )
    book = load_help(path)
    say = book.find("say")
    assert say == HelpEntry("say", ("on", "'"), "Speak.")
    assert book.find("'") == say
    assert book.find("on") == say
    assert book.find("Say") is None
    assert book.find("say please") is None
    assert book.names() == ("say",)


def test_missing_help_file(tmp_path: Path) -> None:
    """A path that does not exist names the file."""
    with pytest.raises(ConfigError, match="Cannot read help file"):
        load_help(tmp_path / "missing.yml")


def test_invalid_yaml(tmp_path: Path) -> None:
    """A broken document is a config error."""
    path = _write(tmp_path, "aliases: [\n")
    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_help(path)


def test_help_file_must_be_a_list(tmp_path: Path) -> None:
    """A mapping, or an empty document, is not a help file. An empty list is valid."""
    path = _write(tmp_path, "name: say\n")
    with pytest.raises(ConfigError, match="must be a YAML list"):
        load_help(path)
    empty = _write(tmp_path, "")
    with pytest.raises(ConfigError, match="must be a YAML list"):
        load_help(empty)
    assert load_help(_write(tmp_path, "[]\n")) == HelpBook(entries=())


def test_entry_must_be_a_mapping(tmp_path: Path) -> None:
    """A bare string is not an entry."""
    path = _write(tmp_path, "- say\n")
    with pytest.raises(ConfigError, match="Help entry must be a mapping"):
        load_help(path)


def test_entry_name_is_required(tmp_path: Path) -> None:
    """Name comes before the other fields."""
    missing = _write(tmp_path, "- aliases: []\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="Help entry is missing name"):
        load_help(missing)
    blank = _write(tmp_path, "- name: ''\n  aliases: []\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="name must be a non-empty string"):
        load_help(blank)


def test_unknown_help_key(tmp_path: Path) -> None:
    """An extra key names the entry."""
    path = _write(tmp_path, "- name: say\n  aliases: []\n  text: Hi.\n  colour: 1\n")
    with pytest.raises(ConfigError, match="Unknown help key on say: colour"):
        load_help(path)


def test_aliases_must_be_strings(tmp_path: Path) -> None:
    """A missing list, a number, or an empty token is rejected."""
    missing = _write(tmp_path, "- name: say\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="say is missing aliases"):
        load_help(missing)
    number = _write(tmp_path, "- name: say\n  aliases: [1]\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="aliases must be a list of strings"):
        load_help(number)
    blank = _write(tmp_path, "- name: say\n  aliases: ['']\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="has an empty alias"):
        load_help(blank)


def test_text_must_be_non_empty(tmp_path: Path) -> None:
    """Missing text, and whitespace-only text, are the same error."""
    missing = _write(tmp_path, "- name: say\n  aliases: []\n")
    with pytest.raises(ConfigError, match="say is missing text"):
        load_help(missing)
    blank = _write(tmp_path, "- name: say\n  aliases: []\n  text: '   '\n")
    with pytest.raises(ConfigError, match="text must be a non-empty string"):
        load_help(blank)


def test_duplicate_name_or_alias(tmp_path: Path) -> None:
    """A name and an alias each occupy one token."""
    names = _write(
        tmp_path,
        "- name: say\n  aliases: []\n  text: Hi.\n- name: say\n  aliases: []\n  text: Again.\n",
    )
    with pytest.raises(ConfigError, match="Duplicate help name: say"):
        load_help(names)
    used = _write(
        tmp_path,
        "- name: say\n  aliases: []\n  text: Hi.\n- name: shout\n  aliases: [say]\n  text: Loud.\n",
    )
    with pytest.raises(ConfigError, match="Help alias 'say' is already used"):
        load_help(used)
    repeated = _write(
        tmp_path,
        '- name: say\n  aliases: ["\'", "\'"]\n  text: Hi.\n',
    )
    with pytest.raises(ConfigError, match="Help alias ''' is already used"):
        load_help(repeated)


def test_help_lists_staff_and_term_width() -> None:
    """Lsu's alias is staff, and term_width has its own write-up."""
    book = load_help(Path("config/help.yml"))
    lsu = book.find("lsu")
    assert lsu is not None
    assert lsu.aliases == ("staff",)
    assert book.find("staff") == lsu
    term = book.find("term_width")
    assert term is not None
    assert "60" in term.text
    assert "auto" in term.text
    entry = book.find("time")
    assert entry is not None
    assert "Monday 12th October 2026, 13:45:03 UTC" in entry.text
    assert "uptime" in entry.text.lower() or "running" in entry.text.lower()
    lsn = book.find("lsn")
    assert lsn is not None
    assert "Room" in lsn.text
    assert "hostname" in lsn.text
    commands = book.find("commands")
    assert commands is not None
    assert "punctuation aliases" in commands.text
    assert "staff, w, and x" in commands.text
    who = book.find("who")
    assert who is not None
    assert "At the name prompt, who in any case shows this list" in who.text
    quit_entry = book.find("quit")
    assert quit_entry is not None
    assert "At the name prompt, quit in any case says Goodbye!" in quit_entry.text
    assert "It cannot be a name." in quit_entry.text


def test_shipped_help_covers_every_command() -> None:
    """The template names every command and keeps the say and examine aliases."""
    root = Path(__file__).resolve().parents[1]
    book = load_help(root / "config" / "help.yml")
    assert book.names() == (
        "ae",
        "alarm",
        "allow",
        "at",
        "au",
        "auto_reply",
        "broadcast",
        "colour",
        "commands",
        "connections",
        "demote",
        "description",
        "disconnect",
        "emote",
        "examine",
        "go",
        "help",
        "join",
        "look",
        "lsn",
        "lsu",
        "main",
        "map",
        "mfa",
        "nuke",
        "password",
        "promote",
        "prompt",
        "quit",
        "recap",
        "remote",
        "resident",
        "retire",
        "save",
        "say",
        "se",
        "shout",
        "shutdown",
        "st",
        "status",
        "su",
        "tell",
        "term_width",
        "think",
        "time",
        "version",
        "warn",
        "who",
    )
    say = book.find("say")
    assert say is not None
    assert say.aliases == ("'", '"', "`")
    assert book.find("'") == say
    examine = book.find("examine")
    assert examine is not None
    assert book.find("x") == examine
    look = book.find("look")
    assert look is not None
    assert look.aliases == ()
    assert book.find("finger") is None
    assert book.find("f") is None
    assert book.find("idle") is None
    assert book.find("!") == book.find("shout")
    told = book.find("tell")
    assert told is not None
    assert told.aliases == (".", ">")
    assert book.find(".") == told
    assert book.find(">") == told
    remote = book.find("remote")
    assert remote is not None
    assert remote.aliases == (",", "<")
    assert book.find(",") == remote
    assert book.find("<") == remote
    helper = book.find("help")
    assert helper is not None
    assert helper.aliases == ("?",)
    assert book.find("?") == helper
    who = book.find("who")
    assert who is not None
    assert who.aliases == ("w",)
    assert book.find("w") == who
    recap = book.find("recap")
    assert recap is not None
    assert recap.aliases == ()
    assert "capitalisation" in recap.text


def test_help_quotes_promote_demote_and_the_admin_channel() -> None:
    """Help names the one-step sentences and the admin channel."""
    root = Path(__file__).resolve().parents[1]
    book = load_help(root / "config" / "help.yml")
    promote = book.find("promote")
    demote = book.find("demote")
    au = book.find("au")
    assert promote is not None
    assert demote is not None
    assert au is not None
    assert "to Super User" in promote.text
    assert "to Admin" in promote.text
    assert "from Super User" in demote.text
    assert "from Admin" in demote.text
    assert "force" in demote.text
    assert "The super admin cannot be demoted." in demote.text
    assert "Bea is the super admin." in promote.text
    assert "[ Admin ]" in au.text
    assert book.find("ae") is not None
    assert book.find("at") is not None


def test_help_covers_save_retire_warn_allow_and_resident_staff_line() -> None:
    """New staff commands and the resident staff line are in the shipped help."""
    book = load_help(Path("config/help.yml"))
    for name in ("save", "retire", "warn", "allow"):
        assert book.find(name) is not None, name
    resident = book.find("resident")
    assert resident is not None
    assert "makes" in resident.text
    assert "residents" not in resident.text
    examine = book.find("examine")
    assert examine is not None
    assert "status" in examine.text.lower()
    tell = book.find("tell")
    assert tell is not None
    assert "Talking to yourself is the first sign of madness." in tell.text
    remote = book.find("remote")
    assert remote is not None
    assert "Talking to yourself is the first sign of madness." in remote.text
    for name in ("resident", "nuke", "disconnect", "promote", "demote"):
        entry = book.find(name)
        assert entry is not None
        assert "You cannot do that to yourself." in entry.text


def test_help_covers_main_and_connections() -> None:
    """Main and connections have the agreed write-ups. conns is the alias."""
    book = load_help(Path("config/help.yml"))
    main = book.find("main")
    assert main is not None
    assert main.aliases == ()
    assert "start room" in main.text
    assert "nothing is said" in main.text
    assert "full" in main.text
    listed = book.find("connections")
    assert listed is not None
    assert listed.aliases == ("conns",)
    assert book.find("conns") == listed
    assert "Name, Hostname, IP Address, and Type" in listed.text
    assert "<pending>" in listed.text
    assert "telnet or ssh" in listed.text
    assert "including during the password" in listed.text


def test_help_mfa_quotes_setup_removal_and_staff_warning() -> None:
    """Help for mfa quotes syntax, prompts, and removal lines."""
    book = load_help(Path("config/help.yml"))
    mfa = book.find("mfa")
    assert mfa is not None
    assert "mfa setup" in mfa.text
    assert "Current password:" in mfa.text
    assert "Code:" in mfa.text
    assert "Are you sure you want to remove MFA for Bea?" in mfa.text
    assert "MFA remove cancelled." in mfa.text
