"""Help file loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from lupaxa.chat_server.commands import _visible_help
from lupaxa.chat_server.commands_list import command_words, ensure_commands
from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.help import (
    HelpBook,
    HelpEntry,
    format_page,
    load_help,
)
from lupaxa.chat_server.saved_player import RANK_NEWBIE, RANK_RESIDENT


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "help.yml"
    path.write_text(text, encoding="utf-8")
    return path


_SAY_PAGE = """\
+----------------------------------------------------+
| Command : say                                      |
| Syntax  : say <message>                            |
| Example : say hello                                |
| Aliases : ' " `                                    |
|                                                    |
| Speak to the other people in the room you are in.  |
|                                                    |
| See Also: emote think shout                        |
+----------------------------------------------------+
"""

_LOOK_PAGE = """\
+----------------------------------------------------+
| Command : look                                     |
| Syntax  : look                                     |
|                                                    |
| Show this room's description, exits, and who else  |
| is here.                                           |
+----------------------------------------------------+
"""


def test_format_page_draws_the_say_box() -> None:
    """The say fixture is 54 columns, with the colon column and one-space inset."""
    entry = HelpEntry(
        "say",
        ("'", '"', "`"),
        "Speak to the other people in the room you are in.",
        example="say hello",
        see=("emote", "think", "shout"),
    )
    visible = frozenset({"say", "emote", "think", "shout"})
    assert format_page(entry, "say <message>", 54, visible=visible) == _SAY_PAGE


def test_format_page_omits_empty_optional_lines() -> None:
    """Look has no example, no aliases, and no see names."""
    entry = HelpEntry(
        "look",
        (),
        "Show this room's description, exits, and who else is here.",
    )
    assert format_page(entry, "look", 54, visible=frozenset({"look"})) == _LOOK_PAGE


def test_format_page_centers_a_54_column_box_in_a_wider_frame() -> None:
    """Eighty columns pad a 54-column box by 13 spaces."""
    entry = HelpEntry("look", (), "Show this room.")
    page = format_page(entry, "look", 80, visible=frozenset({"look"}))
    assert page.splitlines()[0] == (" " * 13) + ("+" + ("-" * 52) + "+")


def test_format_page_uses_half_the_frame_when_that_is_wider_than_54() -> None:
    """A wide frame uses half its columns. The 54-column floor still holds."""
    entry = HelpEntry("look", (), "Show this room.")
    visible = frozenset({"look"})
    wide = format_page(entry, "look", 300, visible=visible)
    assert wide.splitlines()[0] == (" " * 75) + ("+" + ("-" * 148) + "+")
    odd = format_page(entry, "look", 301, visible=visible)
    assert odd.splitlines()[0] == (" " * 75) + ("+" + ("-" * 148) + "+")
    grown = format_page(entry, "look", 110, visible=visible)
    assert grown.splitlines()[0] == (" " * 27) + ("+" + ("-" * 53) + "+")
    held = format_page(entry, "look", 109, visible=visible)
    assert held.splitlines()[0] == (" " * 27) + ("+" + ("-" * 52) + "+")


def test_format_page_never_draws_a_box_narrower_than_20_columns() -> None:
    """A frame below 20 columns still uses an 18-dash inner border."""
    entry = HelpEntry(
        "look",
        (),
        "Show this room's description, exits, and who else is here.",
    )
    page = format_page(entry, "look", 10, visible=frozenset({"look"}))
    assert page.splitlines()[0] == "+" + ("-" * 18) + "+"


def test_format_page_wraps_inside_a_narrow_frame() -> None:
    """Thirty columns wrap the paragraph and keep the borders."""
    entry = HelpEntry(
        "say",
        ("'", '"', "`"),
        "Speak to the other people in the room you are in.",
        example="say hello",
        see=("emote",),
    )
    page = format_page(entry, "say <message>", 30, visible=frozenset({"say", "emote"}))
    assert page == (
        "+----------------------------+\n"
        "| Command : say              |\n"
        "| Syntax  : say <message>    |\n"
        "| Example : say hello        |\n"
        "| Aliases : ' \" `            |\n"
        "|                            |\n"
        "| Speak to the other people  |\n"
        "| in the room you are in.    |\n"
        "|                            |\n"
        "| See Also: emote            |\n"
        "+----------------------------+\n"
    )


def test_format_page_drops_a_hidden_see_name() -> None:
    """A name the player cannot open is left out. None left omits the line."""
    entry = HelpEntry("say", (), "Speak.", see=("emote", "shout"))
    kept = format_page(entry, "say <message>", 54, visible=frozenset({"say", "emote"}))
    assert "See Also: emote" in kept
    assert "shout" not in kept
    gone = format_page(entry, "say <message>", 54, visible=frozenset({"say"}))
    assert "See Also" not in gone


def test_format_page_wraps_see_also_at_the_paragraph_column() -> None:
    """A long see list continues one space inside the border."""
    names = (
        "emote",
        "think",
        "shout",
        "tell",
        "remote",
        "go",
        "join",
        "look",
        "map",
        "who",
        "quit",
    )
    entry = HelpEntry("say", (), "Speak.", see=names)
    page = format_page(entry, "say <message>", 54, visible=frozenset({"say", *names}))
    assert "See Also: emote think shout tell remote go join" in page
    assert "| look map who quit" in page


def test_format_page_keeps_a_space_before_the_right_border() -> None:
    """A line that would fill the row wraps the last word instead."""
    entry = HelpEntry(
        "say",
        (),
        "hello hello hello hello hello hello hello hello hey",
    )
    page = format_page(entry, "say", 54, visible=frozenset({"say"}))
    assert page == (
        "+----------------------------------------------------+\n"
        "| Command : say                                      |\n"
        "| Syntax  : say                                      |\n"
        "|                                                    |\n"
        "| hello hello hello hello hello hello hello hello    |\n"
        "| hey                                                |\n"
        "+----------------------------------------------------+\n"
    )


def test_format_page_does_not_split_a_hyphenated_word() -> None:
    """A hyphen stays with the word, and the right border still lines up."""
    entry = HelpEntry(
        "say",
        (),
        "Speak to the other people about the well-known room.",
    )
    page = format_page(entry, "say", 46, visible=frozenset({"say"}))
    assert page == (
        "+--------------------------------------------+\n"
        "| Command : say                              |\n"
        "| Syntax  : say                              |\n"
        "|                                            |\n"
        "| Speak to the other people about the        |\n"
        "| well-known room.                           |\n"
        "+--------------------------------------------+\n"
    )


def test_format_page_grows_to_fit_a_word_wider_than_the_text_area() -> None:
    """A 60-character word widens a 54-column box and stays on one line."""
    word = "x" * 60
    entry = HelpEntry("say", (), f"Speak to the {word} today.")
    page = format_page(entry, "say <message>", 80, visible=frozenset({"say"}))
    lines = [line[8:] for line in page.splitlines()]
    assert lines == [
        "+--------------------------------------------------------------+",
        "| Command : say                                                |",
        "| Syntax  : say <message>                                      |",
        "|                                                              |",
        "| Speak to the                                                 |",
        f"| {word} |",
        "| today.                                                       |",
        "+--------------------------------------------------------------+",
    ]
    wider = "z" * 90
    entry = HelpEntry("say", (), wider)
    past = format_page(entry, "say", 80, visible=frozenset({"say"}))
    assert past.splitlines()[0].startswith("+")
    assert len(past.splitlines()[0]) == 94


def test_format_page_grows_for_a_visible_see_name_only() -> None:
    """A long see name widens the box. A hidden one does not."""
    name = "y" * 60
    entry = HelpEntry("say", (), "Speak.", see=(name,))
    shown = format_page(entry, "say", 80, visible=frozenset({"say", name}))
    assert f"| {name} |" in shown
    assert shown.splitlines()[0] == (" " * 8) + ("+" + ("-" * 62) + "+")
    hidden = format_page(entry, "say", 80, visible=frozenset({"say"}))
    assert hidden.splitlines()[0] == (" " * 13) + ("+" + ("-" * 52) + "+")


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


def test_aliases_may_be_omitted(tmp_path: Path) -> None:
    """A missing aliases key and null aliases are an empty list."""
    missing = _write(tmp_path, "- name: hours\n  text: The office closes at six.\n")
    book = load_help(missing)
    entry = book.find("hours")
    assert entry is not None
    assert entry.aliases == ()
    empty = _write(tmp_path, "- name: hours\n  aliases:\n  text: The office closes at six.\n")
    book = load_help(empty)
    entry = book.find("hours")
    assert entry is not None
    assert entry.aliases == ()


def test_aliases_must_be_strings(tmp_path: Path) -> None:
    """A number or an empty token is rejected."""
    number = _write(tmp_path, "- name: say\n  aliases: [1]\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="aliases must be a list of strings"):
        load_help(number)
    blank = _write(tmp_path, "- name: say\n  aliases: ['']\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="has an empty alias"):
        load_help(blank)


_PROSE = (
    "+----------------------------------------------------+\n"
    "| The office closes at six.                          |\n"
    "+----------------------------------------------------+\n"
)
_PROSE_SEE = (
    "+----------------------------------------------------+\n"
    "| The office closes at six. The balcony stays open.  |\n"
    "|                                                    |\n"
    "| See Also: look                                     |\n"
    "+----------------------------------------------------+\n"
)


def test_format_page_without_a_command_draws_the_paragraph() -> None:
    """No command means the box is the paragraph."""
    entry = HelpEntry("hours", (), "The office closes at six.", example="hours now")
    page = format_page(entry, None, 54, visible=frozenset({"hours"}))
    assert page == _PROSE
    assert "Command:" not in page
    assert "Syntax" not in page
    assert "Example:" not in page


def test_format_page_without_a_command_draws_see_also() -> None:
    """See Also follows the paragraph when a listed name is visible."""
    entry = HelpEntry(
        "hours",
        ("closing",),
        "The office closes at six. The balcony stays open.",
        see=("look", "reload"),
    )
    shown = format_page(entry, None, 54, visible=frozenset({"hours", "look"}))
    assert shown == _PROSE_SEE
    assert "Aliases:" not in shown
    hidden = format_page(entry, None, 54, visible=frozenset({"hours"}))
    assert "See Also:" not in hidden
    assert "closing" not in hidden


def test_text_must_be_non_empty(tmp_path: Path) -> None:
    """Missing text, and whitespace-only text, are the same error."""
    missing = _write(tmp_path, "- name: say\n  aliases: []\n")
    with pytest.raises(ConfigError, match="say is missing text"):
        load_help(missing)
    blank = _write(tmp_path, "- name: say\n  aliases: []\n  text: '   '\n")
    with pytest.raises(ConfigError, match="text must be a non-empty string"):
        load_help(blank)


def test_example_and_see_are_optional(tmp_path: Path) -> None:
    """A topic with neither key has no example and no see names."""
    path = _write(tmp_path, "- name: say\n  aliases: []\n  text: Hi.\n")
    say = load_help(path).find("say")
    assert say is not None
    assert say.example is None
    assert say.see == ()


def test_example_and_see_round_trip(tmp_path: Path) -> None:
    """A later topic may be named, and a blank example is absent."""
    path = _write(
        tmp_path,
        "- name: say\n"
        "  aliases: []\n"
        "  text: Hi.\n"
        "  example: '   '\n"
        "  see_also: [emote]\n"
        "- name: emote\n"
        "  aliases: []\n"
        "  text: Act.\n"
        "  example: emote waves\n",
    )
    book = load_help(path)
    say = book.find("say")
    emote = book.find("emote")
    assert say is not None
    assert say.example is None
    assert say.see == ("emote",)
    assert emote is not None
    assert emote.example == "emote waves"


def test_example_must_be_one_line(tmp_path: Path) -> None:
    """A number or a newline is not an example."""
    number = _write(tmp_path, "- name: say\n  aliases: []\n  text: Hi.\n  example: 1\n")
    with pytest.raises(ConfigError, match="Help entry say example must be one line"):
        load_help(number)
    broken = _write(
        tmp_path,
        "- name: say\n  aliases: []\n  text: Hi.\n  example: |\n    one\n    two\n",
    )
    with pytest.raises(ConfigError, match="Help entry say example must be one line"):
        load_help(broken)


def test_see_also_cannot_name_itself(tmp_path: Path) -> None:
    """The topic's own name is a different error from an unknown name."""
    itself = _write(tmp_path, "- name: say\n  text: Hi.\n  see_also: [say]\n")
    with pytest.raises(ConfigError, match="Help entry say see_also cannot name itself"):
        load_help(itself)
    both = _write(tmp_path, "- name: say\n  text: Hi.\n  see_also: [say, dance]\n")
    with pytest.raises(ConfigError, match="Help entry say see_also cannot name itself"):
        load_help(both)


def test_see_also_must_name_a_topic(tmp_path: Path) -> None:
    """An alias, an unknown word, or a non-list fails."""
    alias = _write(
        tmp_path,
        '- name: say\n  aliases: ["\'"]\n  text: Hi.\n  see_also: ["\'"]\n',
    )
    with pytest.raises(ConfigError, match="Help entry say see_also must name a topic"):
        load_help(alias)
    missing = _write(tmp_path, "- name: say\n  text: Hi.\n  see_also: [dance]\n")
    with pytest.raises(ConfigError, match="Help entry say see_also must name a topic"):
        load_help(missing)
    words = _write(tmp_path, "- name: say\n  text: Hi.\n  see_also: emote\n")
    with pytest.raises(ConfigError, match="see_also must be a list of topic names"):
        load_help(words)


def test_see_is_an_unknown_key(tmp_path: Path) -> None:
    """The old key is rejected."""
    path = _write(
        tmp_path,
        "- name: say\n  text: Hi.\n  see: [emote]\n- name: emote\n  text: Act.\n",
    )
    with pytest.raises(ConfigError, match="Unknown help key on say: see"):
        load_help(path)


def test_notes_may_be_omitted(tmp_path: Path) -> None:
    """A missing notes key and a blank notes value are absent."""
    missing = _write(tmp_path, "- name: say\n  text: Hi.\n")
    book = load_help(missing)
    entry = book.find("say")
    assert entry is not None
    assert entry.notes is None
    blank = _write(tmp_path, "- name: say\n  notes:\n  text: Hi.\n")
    book = load_help(blank)
    entry = book.find("say")
    assert entry is not None
    assert entry.notes is None


def test_notes_must_be_one_line(tmp_path: Path) -> None:
    """A number or a newline is not notes."""
    number = _write(tmp_path, "- name: say\n  text: Hi.\n  notes: 1\n")
    with pytest.raises(ConfigError, match="Help entry say notes must be one line"):
        load_help(number)
    broken = _write(
        tmp_path,
        "- name: say\n  text: Hi.\n  notes: |\n    one\n    two\n",
    )
    with pytest.raises(ConfigError, match="Help entry say notes must be one line"):
        load_help(broken)


def test_format_page_draws_notes_before_the_paragraph() -> None:
    """Notes is the last heading, and See Also stays under the paragraph."""
    entry = HelpEntry(
        "say",
        ("'", '"', "`"),
        "Speak to the other people in the room you are in.",
        example="say good morning",
        see=("emote", "think", "shout"),
        notes="The sentence is at most 80 characters.",
    )
    visible = frozenset({"say", "emote", "think", "shout"})
    page = format_page(entry, "say <message>", 54, visible=visible)
    assert page == (
        "+----------------------------------------------------+\n"
        "| Command : say                                      |\n"
        "| Syntax  : say <message>                            |\n"
        "| Example : say good morning                         |\n"
        "| Aliases : ' \" `                                    |\n"
        "| Notes   : The sentence is at most 80 characters.   |\n"
        "|                                                    |\n"
        "| Speak to the other people in the room you are in.  |\n"
        "|                                                    |\n"
        "| See Also: emote think shout                        |\n"
        "+----------------------------------------------------+\n"
    )


def test_format_page_hides_notes_when_there_is_no_command() -> None:
    """A topic that is not a command draws no headings."""
    entry = HelpEntry(
        "hours",
        (),
        "The office closes at six.",
        notes="At most 80 characters.",
        see=("look",),
    )
    page = format_page(entry, None, 54, visible=frozenset({"hours", "look"}))
    assert "Notes" not in page
    assert "Command" not in page
    assert "The office closes at six." in page
    assert "See Also: look" in page


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


def test_help_disclaimer_is_the_shipped_notice() -> None:
    """The disclaimer topic is the notice, and it has no aliases."""
    book = load_help(Path("config/help.yml"))
    entry = book.find("disclaimer")
    assert entry is not None
    assert entry.aliases == ()
    assert "consquential damages" in entry.text
    assert "not necessarily the view of the administrators" in entry.text
    assert "anybody elses" in entry.text


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
        "disclaimer",
        "disconnect",
        "emote",
        "entermsg",
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
        "network",
        "nuke",
        "password",
        "promote",
        "prompt",
        "quit",
        "recap",
        "reload",
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
    reload_entry = book.find("reload")
    assert reload_entry is not None
    assert reload_entry.aliases == ()
    assert "reload help" in reload_entry.text


def test_network_help_is_visible_to_admins() -> None:
    """A resident does not see the network topic. An admin does."""
    ensure_commands()
    root = Path(__file__).resolve().parents[1]
    book = load_help(root / "config" / "help.yml")
    resident = _visible_help(book, "resident", mfa_enabled=False)
    admin = _visible_help(book, "admin", mfa_enabled=False)
    assert resident.find("network") is None
    assert admin.find("network") is not None
    entry = admin.find("network")
    assert entry is not None
    assert entry.example == "network telnet"


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


def test_help_covers_entermsg_and_hides_it_from_newbies() -> None:
    """Entermsg help quotes the default and limit, and resident rank lists it."""
    book = load_help(Path("config/help.yml"))
    entry = book.find("entermsg")
    assert entry is not None
    assert entry.notes == "The sentence is at most 80 characters."
    assert "arrives." in entry.text
    assert "80" not in entry.text
    newbie_words = command_words(RANK_NEWBIE)
    assert "entermsg" not in newbie_words
    resident_words = command_words(RANK_RESIDENT)
    assert "entermsg" in resident_words


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
    assert "sealed with your password" in mfa.text
    assert "clearing mfa_secret" in mfa.text.lower()
    assert "Your MFA has been removed." in mfa.text
