"""Help file loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from lupaxa.chat_server.colour import green, plain_len
from lupaxa.chat_server.commands import by_name, command_words, ensure_commands, subcommand_words
from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.help import HelpBook, HelpEntry, format_page, load_help
from lupaxa.chat_server.newbie import visible_help
from lupaxa.chat_server.ranks import Rank, shown_rank
from lupaxa.chat_server.roles import Roles


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "help.yml"
    path.write_text(text, encoding="utf-8")
    return path


SAY_PAGE = """\
+----------------------------------------------------+
| Command    : say                                   |
| Syntax     : say <message>                         |
| Example    : say hello                             |
| Aliases    : ' " `                                 |
|                                                    |
| Speak to the other people in the room you are in.  |
|                                                    |
| See Also   : emote think shout                     |
+----------------------------------------------------+
"""

LOOK_PAGE = """\
+----------------------------------------------------+
| Command    : look                                  |
| Syntax     : look                                  |
|                                                    |
| Show this room's description, exits, and who else  |
| is here.                                           |
+----------------------------------------------------+
"""


def test_format_page_draws_the_say_box() -> None:
    """The say fixture is 54 columns, with the colon column and one-space inset."""
    entry = HelpEntry("say", ("'", '"', "`"), "Speak to the other people in the room you are in.", example="say hello", see=("emote", "think", "shout"))  # fmt: skip  # noqa: E501
    visible = frozenset({"say", "emote", "think", "shout"})
    assert format_page(entry, "say <message>", 54, visible=visible) == SAY_PAGE


def test_format_page_omits_empty_optional_lines() -> None:
    """Look has no example, no aliases, and no see names."""
    entry = HelpEntry("look", (), "Show this room's description, exits, and who else is here.")
    assert format_page(entry, "look", 54, visible=frozenset({"look"})) == LOOK_PAGE


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
    entry = HelpEntry("look", (), "Show this room's description, exits, and who else is here.")
    page = format_page(entry, "look", 10, visible=frozenset({"look"}))
    assert page.splitlines()[0] == "+" + ("-" * 18) + "+"


def test_format_page_wraps_inside_a_narrow_frame() -> None:
    """Thirty columns wrap the paragraph and keep the borders."""
    entry = HelpEntry("say", ("'", '"', "`"), "Speak to the other people in the room you are in.", example="say hello", see=("emote",))
    page = format_page(entry, "say <message>", 30, visible=frozenset({"say", "emote"}))
    assert page == ("+----------------------------+\n| Command    : say           |\n| Syntax     : say <message> |\n| Example    : say hello     |\n| Aliases    : ' \" `         |\n|                            |\n| Speak to the other people  |\n| in the room you are in.    |\n|                            |\n| See Also   : emote         |\n+----------------------------+\n")  # fmt: skip  # noqa: E501


def test_format_page_drops_a_hidden_see_name() -> None:
    """A name the player cannot open is left out. None left omits the line."""
    entry = HelpEntry("say", (), "Speak.", see=("emote", "shout"))
    kept = format_page(entry, "say <message>", 54, visible=frozenset({"say", "emote"}))
    assert "See Also   : emote" in kept
    assert "shout" not in kept
    gone = format_page(entry, "say <message>", 54, visible=frozenset({"say"}))
    assert "See Also" not in gone


def test_format_page_wraps_see_also_at_the_paragraph_column() -> None:
    """A long see list continues one space inside the border."""
    names = ("emote", "think", "shout", "tell", "remote", "go", "join", "look", "map", "who", "quit")
    entry = HelpEntry("say", (), "Speak.", see=names)
    page = format_page(entry, "say <message>", 54, visible=frozenset({"say", *names}))
    assert "See Also   : emote think shout tell remote go join" in page
    assert "| look map who quit" in page


def test_format_page_keeps_a_space_before_the_right_border() -> None:
    """A line that would fill the row wraps the last word instead."""
    entry = HelpEntry("say", (), "hello hello hello hello hello hello hello hello hey")
    page = format_page(entry, "say", 54, visible=frozenset({"say"}))
    assert page == ("+----------------------------------------------------+\n| Command    : say                                   |\n| Syntax     : say                                   |\n|                                                    |\n| hello hello hello hello hello hello hello hello    |\n| hey                                                |\n+----------------------------------------------------+\n")  # fmt: skip  # noqa: E501


def test_format_page_does_not_split_a_hyphenated_word() -> None:
    """A hyphen stays with the word, and the right border still lines up."""
    entry = HelpEntry("say", (), "Speak to the other people about the well-known room.")
    page = format_page(entry, "say", 46, visible=frozenset({"say"}))
    assert page == ("+--------------------------------------------+\n| Command    : say                           |\n| Syntax     : say                           |\n|                                            |\n| Speak to the other people about the        |\n| well-known room.                           |\n+--------------------------------------------+\n")  # fmt: skip  # noqa: E501


def test_format_page_grows_to_fit_a_word_wider_than_the_text_area() -> None:
    """A 60-character word widens a 54-column box and stays on one line."""
    word = "x" * 60
    entry = HelpEntry("say", (), f"Speak to the {word} today.")
    page = format_page(entry, "say <message>", 80, visible=frozenset({"say"}))
    lines = [line[8:] for line in page.splitlines()]
    assert lines == [
        "+--------------------------------------------------------------+",
        "| Command    : say                                             |",
        "| Syntax     : say <message>                                   |",
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
    """Lookup ignores case and keeps YAML bool words as strings."""
    path = write(tmp_path, "- name: say\n  aliases: ['on', \"'\"]\n  text: '  Speak.  '\n")
    book = load_help(path)
    say = book.find("say")
    assert say == HelpEntry("say", ("on", "'"), "Speak.")
    assert book.find("'") == say
    assert book.find("on") == say
    assert book.find("Say") == say
    assert book.find("say please") is None
    assert book.names() == ("say",)


def test_help_book_search_matches_name_alias_text_example_and_notes() -> None:
    """Search matches the name, an alias, the text, the example, and the notes."""
    book = HelpBook(
        entries=(
            HelpEntry("say", ("'",), "Speak."),
            HelpEntry("map", (), "Draw.", example="map here", notes="The house."),
            HelpEntry("quit", (), "Leave."),
        )
    )
    assert [entry.name for entry in book.search("say")] == ["say"]
    assert [entry.name for entry in book.search("'")] == ["say"]
    assert [entry.name for entry in book.search("DRAW")] == ["map"]
    assert [entry.name for entry in book.search("here")] == ["map"]
    assert [entry.name for entry in book.search("house")] == ["map"]
    assert book.search("missing") == ()


def test_missing_help_file(tmp_path: Path) -> None:
    """A path that does not exist names the file."""
    with pytest.raises(ConfigError, match="Cannot read help file"):
        load_help(tmp_path / "missing.yml")


def test_invalid_yaml(tmp_path: Path) -> None:
    """A broken document is a config error."""
    path = write(tmp_path, "aliases: [\n")
    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_help(path)


def test_help_file_must_be_a_list(tmp_path: Path) -> None:
    """A mapping, or an empty document, is not a help file. An empty list is valid."""
    path = write(tmp_path, "name: say\n")
    with pytest.raises(ConfigError, match="must be a YAML list"):
        load_help(path)
    empty = write(tmp_path, "")
    with pytest.raises(ConfigError, match="must be a YAML list"):
        load_help(empty)
    empty_book = load_help(write(tmp_path, "[]\n"))
    assert empty_book == HelpBook(entries=())


def test_entry_must_be_a_mapping(tmp_path: Path) -> None:
    """A bare string is not an entry."""
    path = write(tmp_path, "- say\n")
    with pytest.raises(ConfigError, match="Help entry must be a mapping"):
        load_help(path)


def test_entry_name_is_required(tmp_path: Path) -> None:
    """Name comes before the other fields."""
    missing = write(tmp_path, "- aliases: []\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="Help entry is missing name"):
        load_help(missing)
    blank = write(tmp_path, "- name: ''\n  aliases: []\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="name must be a non-empty string"):
        load_help(blank)


def test_unknown_help_key(tmp_path: Path) -> None:
    """An extra key names the entry."""
    path = write(tmp_path, "- name: say\n  aliases: []\n  text: Hi.\n  colour: 1\n")
    with pytest.raises(ConfigError, match="Unknown help key on say: colour"):
        load_help(path)


def test_aliases_may_be_omitted(tmp_path: Path) -> None:
    """A missing aliases key and null aliases are an empty list."""
    missing = write(tmp_path, "- name: hours\n  text: The office closes at six.\n")
    book = load_help(missing)
    entry = book.find("hours")
    assert entry is not None
    assert entry.aliases == ()
    empty = write(tmp_path, "- name: hours\n  aliases:\n  text: The office closes at six.\n")
    book = load_help(empty)
    entry = book.find("hours")
    assert entry is not None
    assert entry.aliases == ()


def test_aliases_must_be_strings(tmp_path: Path) -> None:
    """A number or an empty token is rejected."""
    number = write(tmp_path, "- name: say\n  aliases: [1]\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="aliases must be a list of strings"):
        load_help(number)
    blank = write(tmp_path, "- name: say\n  aliases: ['']\n  text: Hi.\n")
    with pytest.raises(ConfigError, match="has an empty alias"):
        load_help(blank)


PROSE = ("+----------------------------------------------------+\n| The office closes at six.                          |\n+----------------------------------------------------+\n")  # fmt: skip  # noqa: E501
PROSE_SEE = ("+----------------------------------------------------+\n| The office closes at six. The balcony stays open.  |\n|                                                    |\n| See Also   : look                                  |\n+----------------------------------------------------+\n")  # fmt: skip  # noqa: E501


def test_format_page_without_a_command_draws_the_paragraph() -> None:
    """No command means the box is the paragraph."""
    entry = HelpEntry("hours", (), "The office closes at six.", example="hours now")
    page = format_page(entry, None, 54, visible=frozenset({"hours"}))
    assert page == PROSE
    assert "Command:" not in page
    assert "Syntax" not in page
    assert "Example:" not in page


def test_format_page_without_a_command_draws_see_also() -> None:
    """See Also follows the paragraph when a listed name is visible."""
    entry = HelpEntry("hours", ("closing",), "The office closes at six. The balcony stays open.", see=("look", "reload"))
    shown = format_page(entry, None, 54, visible=frozenset({"hours", "look"}))
    assert shown == PROSE_SEE
    assert "Aliases:" not in shown
    hidden = format_page(entry, None, 54, visible=frozenset({"hours"}))
    assert "See Also:" not in hidden
    assert "closing" not in hidden


def test_text_must_be_non_empty(tmp_path: Path) -> None:
    """Missing text, and whitespace-only text, are the same error."""
    missing = write(tmp_path, "- name: say\n  aliases: []\n")
    with pytest.raises(ConfigError, match="say is missing text"):
        load_help(missing)
    blank = write(tmp_path, "- name: say\n  aliases: []\n  text: '   '\n")
    with pytest.raises(ConfigError, match="text must be a non-empty string"):
        load_help(blank)


def test_example_and_see_are_optional(tmp_path: Path) -> None:
    """A topic with neither key has no example and no see names."""
    path = write(tmp_path, "- name: say\n  aliases: []\n  text: Hi.\n")
    say = load_help(path).find("say")
    assert say is not None
    assert say.example is None
    assert say.see == ()


def test_example_and_see_round_trip(tmp_path: Path) -> None:
    """A later topic may be named, and a blank example is absent."""
    path = write(tmp_path, "- name: say\n  aliases: []\n  text: Hi.\n  example: '   '\n  see_also: [emote]\n- name: emote\n  aliases: []\n  text: Act.\n  example: emote waves\n")  # fmt: skip  # noqa: E501
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
    number = write(tmp_path, "- name: say\n  aliases: []\n  text: Hi.\n  example: 1\n")
    with pytest.raises(ConfigError, match="Help entry say example must be one line"):
        load_help(number)
    broken = write(tmp_path, "- name: say\n  aliases: []\n  text: Hi.\n  example: |\n    one\n    two\n")
    with pytest.raises(ConfigError, match="Help entry say example must be one line"):
        load_help(broken)


def test_see_also_cannot_name_itself(tmp_path: Path) -> None:
    """The topic's own name is a different error from an unknown name."""
    itself = write(tmp_path, "- name: say\n  text: Hi.\n  see_also: [say]\n")
    with pytest.raises(ConfigError, match="Help entry say see_also cannot name itself"):
        load_help(itself)
    both = write(tmp_path, "- name: say\n  text: Hi.\n  see_also: [say, dance]\n")
    with pytest.raises(ConfigError, match="Help entry say see_also cannot name itself"):
        load_help(both)


def test_see_also_must_name_a_topic(tmp_path: Path) -> None:
    """An alias, an unknown word, or a non-list fails."""
    alias = write(tmp_path, '- name: say\n  aliases: ["\'"]\n  text: Hi.\n  see_also: ["\'"]\n')
    with pytest.raises(ConfigError, match="Help entry say see_also must name a topic"):
        load_help(alias)
    missing = write(tmp_path, "- name: say\n  text: Hi.\n  see_also: [dance]\n")
    with pytest.raises(ConfigError, match="Help entry say see_also must name a topic"):
        load_help(missing)
    words = write(tmp_path, "- name: say\n  text: Hi.\n  see_also: emote\n")
    with pytest.raises(ConfigError, match="see_also must be a list of topic names"):
        load_help(words)


def test_see_is_an_unknown_key(tmp_path: Path) -> None:
    """The old key is rejected."""
    path = write(tmp_path, "- name: say\n  text: Hi.\n  see: [emote]\n- name: emote\n  text: Act.\n")
    with pytest.raises(ConfigError, match="Unknown help key on say: see"):
        load_help(path)


def test_notes_may_be_omitted(tmp_path: Path) -> None:
    """A missing notes key and a blank notes value are absent."""
    missing = write(tmp_path, "- name: say\n  text: Hi.\n")
    book = load_help(missing)
    entry = book.find("say")
    assert entry is not None
    assert entry.notes is None
    blank = write(tmp_path, "- name: say\n  notes:\n  text: Hi.\n")
    book = load_help(blank)
    entry = book.find("say")
    assert entry is not None
    assert entry.notes is None


def test_notes_must_be_one_line(tmp_path: Path) -> None:
    """A number or a newline is not notes."""
    number = write(tmp_path, "- name: say\n  text: Hi.\n  notes: 1\n")
    with pytest.raises(ConfigError, match="Help entry say notes must be one line"):
        load_help(number)
    broken = write(tmp_path, "- name: say\n  text: Hi.\n  notes: |\n    one\n    two\n")
    with pytest.raises(ConfigError, match="Help entry say notes must be one line"):
        load_help(broken)


def test_help_shows_permissions_before_the_paragraph() -> None:
    """A command page shows Permissions before the paragraph. The disclaimer does not."""
    book = load_help(Path("config/help.yml"))
    shout = book.find("shout")
    assert shout is not None
    page = format_page(shout, "shout <message>", 80, visible=frozenset(book.names()), permissions="Resident")
    body = shout.text.split("\n", 1)[0]
    lines = page.splitlines()
    rank_at = next(index for index, line in enumerate(lines) if "Permissions" in line and "Resident" in line)
    body_at = next(index for index, line in enumerate(lines) if body[:24] in line)
    assert rank_at < body_at
    assert "Permissions: Resident" in page
    assert "Rank" not in page
    disclaimer = book.find("disclaimer")
    assert disclaimer is not None
    plain = format_page(disclaimer, None, 80, visible=frozenset())
    assert "Permissions" not in plain
    assert "Rank" not in plain
    assert book.find("plan") is None
    moved = book.find("set plan")
    assert moved is not None
    assert "set plan" in moved.text


def test_a_coloured_rank_stays_inside_the_help_box() -> None:
    """Colour codes do not move the help box border."""
    book = load_help(Path("config/help.yml"))
    shout = book.find("shout")
    assert shout is not None
    page = format_page(shout, "shout <message>", 80, visible=frozenset(book.names()), permissions=shown_rank(Rank.NEWBIE, True))
    bordered = [line.strip() for line in page.splitlines() if line.strip().startswith("|")]
    widths = {plain_len(line) for line in bordered}
    assert len(widths) == 1
    assert "Permissions:" in page
    assert green("Newbie") in page


def test_format_page_draws_notes_before_the_paragraph() -> None:
    """Notes is the last heading, and See Also stays under the paragraph."""
    entry = HelpEntry("say", ("'", '"', "`"), "Speak to the other people in the room you are in.", example="say good morning", see=("emote", "think", "shout"), notes="The sentence is at most 80 characters.")  # fmt: skip  # noqa: E501
    visible = frozenset({"say", "emote", "think", "shout"})
    page = format_page(entry, "say <message>", 54, visible=visible)
    assert page == ("+----------------------------------------------------+\n| Command    : say                                   |\n| Syntax     : say <message>                         |\n| Example    : say good morning                      |\n| Aliases    : ' \" `                                 |\n| Notes      : The sentence is at most 80            |\n| characters.                                        |\n|                                                    |\n| Speak to the other people in the room you are in.  |\n|                                                    |\n| See Also   : emote think shout                     |\n+----------------------------------------------------+\n")  # fmt: skip  # noqa: E501


def test_format_page_hides_notes_when_there_is_no_command() -> None:
    """A topic that is not a command draws no headings."""
    entry = HelpEntry("hours", (), "The office closes at six.", notes="At most 80 characters.", see=("look",))
    page = format_page(entry, None, 54, visible=frozenset({"hours", "look"}))
    assert "Notes" not in page
    assert "Command" not in page
    assert "The office closes at six." in page
    assert "See Also   : look" in page


def test_duplicate_name_or_alias(tmp_path: Path) -> None:
    """A name and an alias each occupy one token."""
    names = write(tmp_path, "- name: say\n  aliases: []\n  text: Hi.\n- name: say\n  aliases: []\n  text: Again.\n")
    with pytest.raises(ConfigError, match="Duplicate help name: say"):
        load_help(names)
    used = write(tmp_path, "- name: say\n  aliases: []\n  text: Hi.\n- name: shout\n  aliases: [say]\n  text: Loud.\n")
    with pytest.raises(ConfigError, match="Help alias 'say' is already used"):
        load_help(used)
    repeated = write(tmp_path, '- name: say\n  aliases: ["\'", "\'"]\n  text: Hi.\n')
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
    assert "booted" in entry.text.lower()
    assert "rebooted" in entry.text.lower()
    lsn = book.find("lsn")
    assert lsn is not None
    assert "Room" in lsn.text
    assert "hostname" in lsn.text
    commands = book.find("commands")
    assert commands is not None
    assert "punctuation aliases" in commands.text
    assert "l, staff, w, and x" in commands.text
    assert "No commands found beginning with 'Z'." in commands.text
    assert "There are no Super User commands." in commands.text
    assert "There are no Admin commands." in commands.text
    assert "There are no Coder commands." in commands.text
    assert "There are no aliases." in commands.text
    assert "There are no Site Admin commands." in commands.text
    assert commands.notes == "SU Channel and above: commands su. Admins: commands admin. Coder and the super admin: commands coder. Site Admin: commands site_admin."
    who = book.find("who")
    assert who is not None
    assert "At the name prompt, who in any case shows this list" in who.text
    quit_entry = book.find("quit")
    assert quit_entry is not None
    assert "At the name prompt, quit in any case says Goodbye!" in quit_entry.text
    assert "It cannot be a name." in quit_entry.text


def test_commands_help_names_the_filters() -> None:
    """Usage and the developer docs describe the commands filters."""
    usage = Path("mkdocs/usage/commands.md").read_text()
    assert "`commands a` keeps the names from that list which start with a." in usage
    assert "`No commands found beginning with 'Z'.`" in usage
    assert "`There are no aliases.`" in usage
    assert "Staff with nothing to list get `There are no Super User commands.`" in usage
    assert "Admins with nothing to list get `There are no Admin commands.`" in usage
    assert "Coders with nothing to list get `There are no Coder commands.`" in usage
    assert "SU Channel and above can do `commands su`." in usage
    assert "Admins can do `commands admin`." in usage
    assert "A coder and the super admin do `commands coder`." in usage
    assert "`commands site_admin`" in usage
    assert "Only a holder runs it." in usage
    assert "A holder with nothing to list gets `There are no Site Admin commands.`" in usage
    row = "| `commands`    |                   | Newbie     | Every command word and alias                |"
    assert row in usage
    developer = Path("developer-docs/commands.md").read_text()
    assert "Syntax: commands [<letter>|aliases]" in developer
    assert "Syntax: commands [<letter>|aliases|su]" in developer
    assert "Syntax: commands [<letter>|aliases|su|admin]" in developer
    assert "Syntax: commands [<letter>|aliases|su|admin|coder]" in developer
    assert "Syntax: commands [<letter>|aliases|site_admin]" in developer
    assert "Syntax: commands [<letter>|aliases|su|site_admin]" in developer
    assert "Syntax: commands [<letter>|aliases|su|admin|site_admin]" in developer
    assert "Syntax: commands [<letter>|aliases|su|admin|coder|site_admin]" in developer
    assert "not cut down by the player's rank" in developer
    assert "There are no Site Admin commands." in developer
    assert "Reload's children are not in the list." in developer
    assert "No commands found beginning with 'Z'." in developer
    assert "The letter keeps the case the player typed." in developer
    assert "There are no Super User commands." in developer
    assert "There are no Admin commands." in developer
    assert "including while off duty" in developer


def test_shipped_help_covers_every_command() -> None:
    """The template names every command and keeps the say and examine aliases."""
    root = Path(__file__).resolve().parents[1]
    book = load_help(root / "config" / "help.yml")
    assert book.names() == ("ae", "alarm", "allow", "appoint", "at", "au", "broadcast", "ce", "chains", "cls", "colour", "commands", "connections", "ct", "cu", "demote", "disclaimer", "disconnect", "dismiss", "emote", "examine", "go", "grab", "help", "home", "ipinfo", "join", "look", "lsn", "lsu", "main", "map", "mfa", "network", "nuke", "off_duty", "off_list", "on_duty", "on_list", "password", "promote", "pstats", "quit", "reboot", "recap", "reload", "reload all", "reload commands", "reload config", "reload help", "reload messages", "reload rooms", "remote", "resident", "retire", "roles", "rthink", "save", "say", "screenlock", "se", "set", "set auto_reply", "set commands", "set description", "set entermsg", "set login", "set loginmsg", "set logoutmsg", "set plan", "set prompt", "set status", "shout", "shutdown", "st", "su", "system", "tell", "term_width", "testcard", "think", "time", "version", "warn", "who")  # fmt: skip  # noqa: E501
    say = book.find("say")
    assert say is not None
    assert say.aliases == ("'", '"', "`")
    assert book.find("'") == say
    examine = book.find("examine")
    assert examine is not None
    assert book.find("x") == examine
    look = book.find("look")
    assert look is not None
    assert look.aliases == ("l",)
    assert book.find("l") == look
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
    assert "help search <text>" in helper.text
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
    assert "reload commands" in reload_entry.text
    assert "The coder channel is told" in reload_entry.text
    rooms = book.find("reload rooms")
    assert rooms is not None
    assert "The Void" in rooms.text
    reloaded = book.find("reload help")
    assert reloaded is not None
    assert "old book" in reloaded.text
    messages = book.find("reload messages")
    assert messages is not None
    assert "banner.msg" in messages.text
    assert "Messages reloaded." in messages.text
    whole = book.find("reload all")
    assert whole is not None
    assert "reload config, then reload rooms, then reload help, then reload messages" in whole.text
    config_reload = book.find("reload config")
    assert config_reload is not None
    assert "hot values" in config_reload.text
    assert "cold file stays as it was at startup" in config_reload.text


def test_pstats_help_is_an_admin_topic() -> None:
    """A resident does not see pstats. An admin does."""
    book = load_help(Path("config/help.yml"))
    entry = book.find("pstats")
    assert entry is not None
    assert "newbies" in entry.text.lower()
    assert "role" in entry.text.lower()
    hidden = visible_help(book, Rank.RESIDENT, mfa_enabled=False)
    shown = visible_help(book, Rank.ADMIN, mfa_enabled=False)
    assert hidden.find("pstats") is None
    assert shown.find("pstats") is not None
    usage = Path("mkdocs/usage/commands.md").read_text()
    row = "| `pstats`      |                   | Admin      | Counts of newbies, ranks, and roles         |"
    assert row in usage
    assert len(row) == 96


def test_reload_docs_name_messages_and_all() -> None:
    """Usage, Getting Started, and the developer docs name the message reload."""
    usage = Path("mkdocs/usage/commands.md").read_text()
    row = "| `reload`      |                   | Coder      | Run one reload command                      |"
    assert row in usage
    assert len(row) == 96
    started = Path("mkdocs/getting-started.md").read_text()
    assert "`reload config` reads `hot.yml` again, and the five-minute check does too when `hot.yml` has changed." in started
    developer = Path("developer-docs/commands.md").read_text()
    assert "`reload messages` re-reads `banner.msg`, `full.msg`, and `no-more-newbies.msg` from `messages_path`." in developer
    assert "are read from `messages_path`" in started
    assert "The shipped value is `files`." in started
    assert "`reload all` runs config, then rooms, then help, then messages." in developer


def test_reboot_help_says_ssh_drops_and_telnet_stays() -> None:
    book = load_help(Path(__file__).resolve().parents[1] / "config" / "help.yml")
    entry = book.find("reboot")
    assert entry is not None
    assert "SSH" in entry.text
    assert "telnet" in entry.text
    assert "reboot" in book.names()


def test_network_help_is_visible_to_coders_and_site_admins() -> None:
    """A resident does not see the network topic. A coder does, and so does a site admin."""
    ensure_commands()
    root = Path(__file__).resolve().parents[1]
    book = load_help(root / "config" / "help.yml")
    resident = visible_help(book, Rank.RESIDENT, mfa_enabled=False)
    admin = visible_help(book, Rank.ADMIN, mfa_enabled=False)
    coder = visible_help(book, Rank.CODER, mfa_enabled=False)
    site = visible_help(book, Rank.RESIDENT, mfa_enabled=False, roles=int(Roles.SITE_ADMIN))
    assert resident.find("network") is None
    assert admin.find("network") is None
    assert coder.find("network") is not None
    assert site.find("network") is not None
    entry = coder.find("network")
    assert entry is not None
    assert entry.example == "network telnet"


def test_system_docs_and_help_topic() -> None:
    """Usage documents system and the help topic names context switches and reboot."""
    usage = Path("mkdocs/usage/commands.md").read_text()
    row = "| `system`      |                   | Coder      | Process CPU, memory, sockets, and disk      |"
    assert row in usage
    assert len(row) == 96
    book = load_help(Path(__file__).resolve().parents[1] / "config" / "help.yml")
    entry = book.find("system")
    assert entry is not None
    assert "context switches" in entry.text
    assert "reboot" in entry.text.lower()


def test_rank_docs_name_the_seven_rungs() -> None:
    """Players, reference, and the promote help name SU Channel and Coder."""
    root = Path(__file__).resolve().parents[1]
    players = (root / "mkdocs/usage/players.md").read_text(encoding="utf-8")
    reference = (root / "mkdocs/reference.md").read_text(encoding="utf-8")
    commands = (root / "mkdocs/usage/commands.md").read_text(encoding="utf-8")
    login = (root / "developer-docs/login.md").read_text(encoding="utf-8")
    developer = (root / "developer-docs/commands.md").read_text(encoding="utf-8")
    book = load_help(root / "config" / "help.yml")
    promote = book.find("promote")
    demote = book.find("demote")
    assert promote is not None
    assert demote is not None
    assert "SU Channel" in players
    assert "Coder" in players
    assert "Newbie, then resident, then SU Channel, then super user, then admin, then coder, then Super Admin." in reference
    assert "< SU > Ada promotes Bea to Coder." in commands
    assert "su_channel" in login
    assert "a coder" in developer
    assert "< SU > Ada promotes Bea to Coder." in promote.text
    assert "from Coder" in demote.text


def test_help_quotes_promote_demote_and_the_admin_channel() -> None:
    """Help names the one-step sentences and the admin channel."""
    root = Path(__file__).resolve().parents[1]
    book = load_help(root / "config" / "help.yml")
    promote = book.find("promote")
    demote = book.find("demote")
    retire = book.find("retire")
    au = book.find("au")
    assert promote is not None
    assert demote is not None
    assert retire is not None
    assert au is not None
    assert "< SU > Ada promotes Bea to Coder." in promote.text
    assert "< SU > Ada demotes Bea from Coder." in demote.text
    assert "force" in demote.text
    assert "The super admin cannot be demoted." in demote.text
    assert "Bea is the super admin." in promote.text
    assert "Only the super admin may promote an admin to coder." in promote.text
    assert "Only a coder or the super admin can promote Bea to Admin." in promote.text
    assert "Only the super admin may demote a coder" in demote.text
    assert "Only a coder or the super admin can demote Bea from Admin." in demote.text
    assert "The super admin cannot retire." in retire.text
    assert "The admin cannot retire." not in retire.text
    assert "Only the config account" not in promote.text
    assert "Only the config account" not in demote.text
    assert "[ Admin ]" in au.text
    assert book.find("ae") is not None
    assert book.find("at") is not None


def test_roles_docs_name_the_flag_and_the_warning() -> None:
    """Getting Started, Players, and the command table name roles and the warning."""
    usage = Path("mkdocs/usage/commands.md").read_text(encoding="utf-8")
    for row in (
        "| `appoint`     |                   | Admin      | Give a player a role                        |",
        "| `dismiss`     |                   | Admin      | Take a role from a player                   |",
        "| `roles`       |                   | Resident   | List your roles, or another player's        |",
    ):
        assert row in usage
        assert len(row) == 96
    started = Path("mkdocs/getting-started.md").read_text(encoding="utf-8")
    assert "There is no super admin. Run chat-server setup -c" in started
    assert "There is more than one super admin" in started
    assert "chat-server setup -c" in started
    players = Path("mkdocs/usage/players.md").read_text(encoding="utf-8")
    assert "site_admin" in players
    assert "Site Admin" in players
    assert "appoint" in players
    assert "roles when one is set" in players


def test_admin_docs_and_help_describe_super_admin_setup() -> None:
    """Docs and help name setup instead of a config account or localhost login."""
    started = Path("mkdocs/getting-started.md").read_text()
    assert "chat-server setup -c" in started
    assert "admin: admin" not in started
    assert "name of the admin" not in started
    players = Path("mkdocs/usage/players.md").read_text()
    commands = Path("mkdocs/usage/commands.md").read_text()
    mfa = Path("mkdocs/security/mfa.md").read_text()
    login = Path("developer-docs/login.md").read_text()
    for text in (players, commands, mfa, login):
        assert "chat-server setup -c" in text
        assert "Only the config account" not in text
        assert "named in the config" not in text
        assert "The first login of the config account still has to come from localhost." not in text
    assert "while the talker is stopped" in commands
    assert "2 Clear MFA" in mfa
    assert "by hand" not in mfa
    assert "The super admin cannot retire." in players
    assert "The admin cannot retire." not in players
    assert "The super admin cannot retire." in commands
    assert "The admin cannot retire." not in commands


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
    assert "roles when one is set" in examine.text.lower()
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
    assert "When country lookup is on" in listed.text


def test_help_covers_entermsg_and_hides_it_from_newbies() -> None:
    """Entermsg help quotes the default and limit, and resident rank lists it."""
    book = load_help(Path("config/help.yml"))
    entry = book.find("set entermsg")
    assert entry is not None
    assert entry.notes == "The sentence is at most 80 characters."
    assert "arrives." in entry.text
    assert "80" not in entry.text
    newbie_words = command_words(Rank.NEWBIE)
    assert "entermsg" not in newbie_words
    set_children = subcommand_words(by_name()["set"], Rank.RESIDENT)
    assert "entermsg" in set_children
    login = book.find("set loginmsg")
    logout = book.find("set logoutmsg")
    assert login is not None
    assert logout is not None
    assert "has connected." in login.text
    assert "has disconnected." in logout.text
    assert "loginmsg" not in newbie_words
    assert "logoutmsg" not in newbie_words
    assert "loginmsg" in set_children
    assert "logoutmsg" in set_children


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
    assert "chat-server setup -c" in mfa.text
    assert "2 Clear MFA" in mfa.text
    assert "by hand" not in mfa.text
    assert "Your MFA has been removed." in mfa.text


def test_help_covers_grab() -> None:
    """Grab is in the shipped help and is hidden below super user."""
    book = load_help(Path("config/help.yml"))
    entry = book.find("grab")
    assert entry is not None
    assert "stretches out and grabs you !!!!!!" in entry.text
    assert "already in the same room" in entry.text
    assert "system room" in entry.text
    resident = visible_help(book, Rank.RESIDENT, mfa_enabled=False)
    super_user = visible_help(book, Rank.SUPER_USER, mfa_enabled=False)
    assert resident.find("grab") is None
    assert super_user.find("grab") is not None


def test_help_says_a_locked_screen_hides_the_auto_reply() -> None:
    """Locking clears the screen, hides the auto-reply, and names the unlock line."""
    book = load_help(Path("config/help.yml"))
    locked = book.find("screenlock")
    assert locked is not None
    assert "Locking clears the screen." in locked.text
    assert "does not also send the auto-reply" in locked.text
    assert "Screen is now unlocked." in locked.text
    reply = book.find("set auto_reply")
    assert reply is not None
    assert "screen-lock line instead of this reply" in reply.text
    usage = Path("mkdocs/usage/commands.md").read_text()
    assert "Locking clears the screen." in usage
    assert "Screen is now unlocked." in usage
    developer = Path("developer-docs/commands.md").read_text()
    assert "send_lock_line" in developer
    assert "Screen is now unlocked." in developer


def test_reboot_help_and_config_docs_name_staff_lines_and_obsolete_keys() -> None:
    """Reboot help names the staff lines, and the config docs drop the admin key."""
    reboot = load_help(Path("config/help.yml")).find("reboot")
    assert reboot is not None
    assert "< SU > Ada has initiated a reboot." in reboot.text
    assert "< SU > Reboot completed." in reboot.text
    assert "Rebooting now!" in reboot.text
    started = Path("mkdocs/getting-started.md").read_text(encoding="utf-8")
    reference = Path("mkdocs/reference.md").read_text(encoding="utf-8")
    assert "| `admin`" not in started
    assert "A key the server does not use does not stop startup." in started
    assert "any other key stops startup" not in started
    assert "any other key stops startup" not in reference
    assert "A key the server does not use does not stop startup." in reference
    assert "hot: config/hot.yml" in started
    assert "cold: config/cold.yml" in started
    assert started.index("hot: config/hot.yml") < started.index("## Hot")
    assert "## Hot" in started
    assert "## Cold" in started
    assert "| `port`" in started
    assert "## Hot" in reference
    assert "## Cold" in reference


def test_appoint_and_dismiss_help_name_the_staff_channel() -> None:
    """Help and the command page name the staff sentence, not the admin channel."""
    book = load_help(Path("config/help.yml"))
    appoint = book.find("appoint")
    dismiss = book.find("dismiss")
    assert appoint is not None
    assert dismiss is not None
    assert "< SU > Ada assigns Bea to the role of 'Site Admin'" in appoint.text
    assert "no final period" in appoint.text
    assert "< SU > Ada dismisses Bea from the role of 'Site Admin'." in dismiss.text
    assert "[ Admin ]" not in appoint.text
    assert "[ Admin ]" not in dismiss.text
    commands = Path("mkdocs/usage/commands.md").read_text(encoding="utf-8")
    assert "< SU > Ada assigns Bea to the role of 'Site Admin'" in commands
    assert "< SU > Ada dismisses Bea from the role of 'Site Admin'." in commands
    assert "[ Admin ] Ada assigns" not in commands
