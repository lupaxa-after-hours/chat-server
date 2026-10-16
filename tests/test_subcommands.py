"""Parents own child commands. The commands child lists them."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from test_commands import FakePlayer, FakeServer

from lupaxa.chat_server.client import Client
from lupaxa.chat_server.colour import cyan, red
from lupaxa.chat_server.commands import Command, CommandError, attach_subcommands, by_name, command_subcommands, command_words, register_subcommands, subcommand_words
from lupaxa.chat_server.globals import registered_subcommands
from lupaxa.chat_server.help import HelpBook, HelpEntry, load_help
from lupaxa.chat_server.newbie import command_help, help_syntax, visible_help
from lupaxa.chat_server.parse import process_commands
from lupaxa.chat_server.player import Player
from lupaxa.chat_server.ranks import Rank
from lupaxa.chat_server.server import Server


def unused(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Satisfy the command signature. These tests do not call it."""
    del server, client, command, text


def parent_of(*children: Command, rank: Rank = Rank.RESIDENT) -> Command:
    """Return a set parent with these children and no list child yet."""
    return Command("set", unused, False, "set <command>", rank=rank, subcommands=children)


@contextmanager
def isolated_registration() -> Iterator[None]:
    """Save the subcommand queue, clear it, and restore it afterwards."""
    saved = {key: (spelling, list(children)) for key, (spelling, children) in registered_subcommands.items()}
    registered_subcommands.clear()
    try:
        yield
    finally:
        registered_subcommands.clear()
        for key, (spelling, children) in saved.items():
            registered_subcommands[key] = (spelling, list(children))


def test_a_queued_child_follows_a_tuple_child() -> None:
    """A queued child is attached after a tuple child, and the queue is cleared."""
    alpha = Command("alpha", unused, False, None, rank=Rank.RESIDENT)
    beta = Command("beta", unused, False, "<name>", rank=Rank.RESIDENT)
    gamma = Command("gamma", unused, False, None, rank=Rank.RESIDENT)
    commands = [parent_of(alpha)]
    with isolated_registration():
        register_subcommands("Set", beta)
        register_subcommands("set", gamma)
        attach_subcommands(commands, registered=True)
        children = commands[0].subcommands
        assert children is not None
        assert [child.name for child in children] == ["alpha", "beta", "gamma", "commands"]
        assert children[1].parent == "set"
        assert children[1].syntax == "<name>"
        assert children[2].parent == "set"
        assert children[3].parent == "set"
        assert registered_subcommands == {}


def test_the_default_attach_leaves_the_queue_alone() -> None:
    """The default attach leaves queued children unattached."""
    beta = Command("beta", unused, False, None, rank=Rank.RESIDENT)
    commands = [parent_of()]
    with isolated_registration():
        register_subcommands("set", beta)
        attach_subcommands(commands)
        children = commands[0].subcommands
        assert children is not None
        assert [child.name for child in children] == ["commands"]
        assert registered_subcommands["set"][1] == [beta]


def test_register_subcommands_needs_a_subcommand() -> None:
    """A call with no children raises and leaves the queue unchanged."""
    beta = Command("beta", unused, False, None, rank=Rank.RESIDENT)
    with isolated_registration():
        register_subcommands("set", beta)
        with pytest.raises(CommandError) as caught:
            register_subcommands("set")
        assert str(caught.value) == "register_subcommands needs a subcommand."
        assert [child.name for child in registered_subcommands["set"][1]] == ["beta"]


def test_a_missing_parent_is_refused() -> None:
    """A queued name that is not in the list raises and changes nothing."""
    say = Command("say", unused, False, None, rank=Rank.RESIDENT)
    beta = Command("beta", unused, False, None, rank=Rank.RESIDENT)
    gamma = Command("gamma", unused, False, None, rank=Rank.RESIDENT)
    commands = [say]
    with isolated_registration():
        register_subcommands("sett", beta)
        register_subcommands("say", gamma)
        with pytest.raises(CommandError) as caught:
            attach_subcommands(commands, registered=True)
        assert str(caught.value) == "No such command 'sett'."
        assert commands == [say]
        assert say.subcommands is None
        assert [child.name for child in registered_subcommands["sett"][1]] == ["beta"]
        assert [child.name for child in registered_subcommands["say"][1]] == ["gamma"]


def test_a_command_that_is_not_a_parent_is_refused() -> None:
    """A queued child for an ordinary command raises and changes nothing."""
    say = Command("say", unused, False, None, rank=Rank.RESIDENT)
    gamma = Command("gamma", unused, False, None, rank=Rank.RESIDENT)
    commands = [say]
    with isolated_registration():
        register_subcommands("Say", gamma)
        with pytest.raises(CommandError) as caught:
            attach_subcommands(commands, registered=True)
        assert str(caught.value) == "Command say is not a parent."
        assert commands == [say]
        assert say.subcommands is None
        assert registered_subcommands["say"][0] == "Say"
        assert registered_subcommands["say"][1] == [gamma]


def test_a_registered_repeat_uses_the_later_spelling() -> None:
    """A tuple child and a registered child with the same name raise."""
    first = Command("foo", unused, False, None, rank=Rank.RESIDENT)
    second = Command("Foo", unused, False, None, rank=Rank.RESIDENT)
    commands = [parent_of(first)]
    with isolated_registration():
        register_subcommands("set", second)
        with pytest.raises(CommandError) as caught:
            attach_subcommands(commands, registered=True)
        assert str(caught.value) == "Subcommand set Foo is already registered."
        assert commands[0].subcommands == (first,)
        assert registered_subcommands["set"][1] == [second]


def test_a_parent_gains_the_commands_child() -> None:
    """An empty parent gets the list child after the checks."""
    commands = [parent_of()]
    attach_subcommands(commands)
    children = commands[0].subcommands
    assert children is not None
    assert [child.name for child in children] == ["commands"]
    listed = children[0]
    assert listed.parent == "set"
    assert listed.rank == Rank.RESIDENT
    assert listed.syntax is None
    assert listed.subcommands is None
    assert subcommand_words(commands[0], Rank.RESIDENT) == "commands"


def test_a_nested_child_is_refused() -> None:
    """A child cannot own children."""
    inner = Command("foo", unused, False, None, rank=Rank.RESIDENT, subcommands=())
    commands = [parent_of(inner)]
    with pytest.raises(CommandError) as caught:
        attach_subcommands(commands)
    assert str(caught.value) == "Subcommand set foo cannot have subcommands."
    assert commands[0].subcommands == (inner,)


def test_a_child_named_commands_is_reserved() -> None:
    """The list child is added by the catalogue."""
    child = Command("commands", unused, False, None, rank=Rank.RESIDENT)
    commands = [parent_of(child)]
    with pytest.raises(CommandError) as caught:
        attach_subcommands(commands)
    assert str(caught.value) == "Subcommand set commands is reserved."


def test_a_repeated_child_uses_the_later_spelling() -> None:
    """Foo repeats foo. The error names Foo."""
    first = Command("foo", unused, False, None, rank=Rank.RESIDENT)
    second = Command("Foo", unused, False, None, rank=Rank.RESIDENT)
    commands = [parent_of(first, second)]
    with pytest.raises(CommandError) as caught:
        attach_subcommands(commands)
    assert str(caught.value) == "Subcommand set Foo is already registered."


def test_a_child_below_the_parent_rank_is_refused() -> None:
    """A resident parent cannot own a newbie child."""
    child = Command("foo", unused, False, None, rank=Rank.NEWBIE)
    commands = [parent_of(child)]
    with pytest.raises(CommandError) as caught:
        attach_subcommands(commands)
    assert str(caught.value) == "Subcommand set foo is below set's rank."


def test_child_names_follow_rank_and_colour() -> None:
    """A lower rank misses higher children. Colour matches the command list."""
    foo = Command("foo", unused, False, None, rank=Rank.SUPER_USER)
    zeta = Command("zeta", unused, False, None, rank=Rank.ADMIN)
    bang = Command("!", unused, True, None, rank=Rank.RESIDENT)
    commands = [parent_of(foo, zeta, bang)]
    attach_subcommands(commands)
    parent = commands[0]
    assert subcommand_words(parent, Rank.RESIDENT) == "! commands"
    assert subcommand_words(parent, Rank.SUPER_USER) == "! commands foo"
    coloured = subcommand_words(parent, Rank.ADMIN, colour=True)
    assert cyan("foo") in coloured
    assert red("zeta") in coloured
    assert coloured.startswith("!")


def test_the_lister_sends_the_heading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set commands prints the heading and the child names."""
    commands = [parent_of()]
    attach_subcommands(commands)
    parent = commands[0]
    monkeypatch.setattr("lupaxa.chat_server.commands.by_name", lambda: {"set": parent})
    user = Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT)
    ada = FakePlayer(name="Ada", player=user)
    server = FakeServer(people=[ada])
    listed = parent.subcommands[-1]  # type: ignore[index]
    command_subcommands(server, ada, listed, "extra")  # type: ignore[arg-type]
    assert server.sent == ["Subcommands available:\ncommands\n"]


CALLED = {"parent": 0}


def parent_ran(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Count a parent call. Dispatch must leave this at zero."""
    del server, client, command, text
    CALLED["parent"] += 1


def test_set_lists_its_commands() -> None:
    """A resident sees the list. A newbie does not see set."""
    user = Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT, idle_seconds=5)
    ada = FakePlayer(name="Ada", player=user)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "set")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: set <command>\n"]
    server.sent.clear()
    process_commands(server, ada, "set commands")  # type: ignore[arg-type]
    assert server.sent == ["Subcommands available:\nauto_reply commands description entermsg login loginmsg logoutmsg plan\nprompt status\n"]
    assert user.idle_seconds == 0
    server.sent.clear()
    process_commands(server, ada, "set Commands extra")  # type: ignore[arg-type]
    assert server.sent == ["Subcommands available:\nauto_reply commands description entermsg login loginmsg logoutmsg plan\nprompt status\n"]
    newbie = FakePlayer(name="Bea", player=Player("Bea", "reception", datetime.now(timezone.utc), rank=Rank.NEWBIE))
    server.sent.clear()
    process_commands(server, newbie, "set commands")  # type: ignore[arg-type]
    assert server.sent == ["No such command 'set'\n"]
    assert "set" in command_words(Rank.RESIDENT).split()
    assert "set" not in command_words(Rank.NEWBIE).split()
    assert by_name()["commands"].parent == ""


def test_an_unknown_child_keeps_the_typed_case() -> None:
    """Foo is not a child. The reply uses the typed word."""
    ada = FakePlayer(name="Ada", player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server = FakeServer(people=[ada])
    process_commands(server, ada, "set Foo")  # type: ignore[arg-type]
    assert server.sent == ["No such subcommand 'Foo'.\n"]


def test_the_parent_function_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child runs. The parent function stays unused."""
    CALLED["parent"] = 0
    foo = Command("foo", unused, False, "<name>", rank=Rank.SUPER_USER)
    commands = [Command("widget", parent_ran, False, "widget <command>", rank=Rank.RESIDENT, subcommands=(foo,))]
    attach_subcommands(commands)
    parent = commands[0]
    monkeypatch.setattr("lupaxa.chat_server.parse.by_name", lambda: {"widget": parent})
    monkeypatch.setattr("lupaxa.chat_server.parse.aliases", lambda: ())
    ada = FakePlayer(name="Ada", player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.SUPER_USER))
    server = FakeServer(people=[ada])
    process_commands(server, ada, "widget foo")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: widget foo <name>\n"]
    assert CALLED["parent"] == 0
    resident = FakePlayer(name="Bea", player=Player("Bea", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server.sent.clear()
    process_commands(server, resident, "widget foo")  # type: ignore[arg-type]
    assert server.sent == ["No such subcommand 'foo'.\n"]


def test_off_duty_blocks_a_super_parent_and_a_super_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off duty stops a super parent, and a super child, before they run."""
    user = Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.SUPER_USER, off_duty=True)
    ada = FakePlayer(name="Ada", player=user)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "set commands")  # type: ignore[arg-type]
    assert server.sent == ["Subcommands available:\nauto_reply commands description entermsg login loginmsg logoutmsg plan\nprompt status\n"]
    server.help = load_help(Path("config/help.yml"))
    server.sent.clear()
    process_commands(server, ada, "help set")  # type: ignore[arg-type]
    assert "Run one set command. Residents and above." in server.sent[-1]
    foo = Command("foo", unused, False, None, rank=Rank.SUPER_USER)
    commands = [Command("widget", parent_ran, False, "widget <command>", rank=Rank.SUPER_USER, subcommands=(foo,))]
    attach_subcommands(commands)
    monkeypatch.setattr("lupaxa.chat_server.parse.by_name", lambda: {"widget": commands[0]})
    monkeypatch.setattr("lupaxa.chat_server.parse.aliases", lambda: ())
    server.sent.clear()
    process_commands(server, ada, "widget foo")  # type: ignore[arg-type]
    assert server.sent == ["You are off duty.\n"]
    resident_parent = attach_ready_parent()
    monkeypatch.setattr("lupaxa.chat_server.parse.by_name", lambda: {"widget": resident_parent})
    server.sent.clear()
    process_commands(server, ada, "widget foo")  # type: ignore[arg-type]
    assert server.sent == ["You are off duty.\n"]


def attach_ready_parent() -> Command:
    """Return a resident parent whose super child is named foo."""
    foo = Command("foo", unused, False, None, rank=Rank.SUPER_USER)
    commands = [Command("widget", parent_ran, False, "widget <command>", rank=Rank.RESIDENT, subcommands=(foo,))]
    attach_subcommands(commands)
    return commands[0]


def test_help_set_pages_follow_rank() -> None:
    """Residents see both topics. Newbies see neither."""
    book = load_help(Path("config/help.yml"))
    entry = book.find("set")
    assert entry is not None
    assert "Run one set command. Residents and above." in entry.text
    assert "set commands" in entry.see
    listed = book.find("set commands")
    assert listed is not None
    assert listed.text.strip() == "List the set commands you can use."
    assert help_syntax("set") == "set <command>"
    assert help_syntax("set commands") == "set commands"
    resident = visible_help(book, Rank.RESIDENT, mfa_enabled=False)
    newbie = visible_help(book, Rank.NEWBIE, mfa_enabled=False)
    assert resident.find("set") is not None
    assert resident.find("set commands") is not None
    assert newbie.find("set") is None
    assert newbie.find("set commands") is None
    ada = FakePlayer(name="Ada", player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.NEWBIE))
    server = FakeServer(people=[ada], help=book)
    command_help(server, ada, by_name()["help"], "set")  # type: ignore[arg-type]
    command_help(server, ada, by_name()["help"], "set commands")  # type: ignore[arg-type]
    command_help(server, ada, by_name()["help"], "set foo")  # type: ignore[arg-type]
    assert server.sent == ["help for set not found\n", "help for set commands not found\n", "help for set foo not found\n"]


def test_a_hidden_child_is_missing_from_help(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resident does not see a super-user child's help topic."""
    parent = attach_ready_parent()
    monkeypatch.setattr("lupaxa.chat_server.newbie.by_name", lambda: {"widget": parent})
    book = HelpBook(entries=(HelpEntry("widget foo", (), "Secret."),))
    assert visible_help(book, Rank.RESIDENT, mfa_enabled=False).find("widget foo") is None
    assert visible_help(book, Rank.SUPER_USER, mfa_enabled=False).find("widget foo") is not None
    ada = FakePlayer(name="Ada", player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server = FakeServer(people=[ada], help=book)
    command_help(server, ada, by_name()["help"], "widget foo")  # type: ignore[arg-type]
    assert server.sent == ["help for widget foo not found\n"]


RAN_CHILD: list[str] = []


def record_child(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Record which child ran."""
    del server, client, text
    RAN_CHILD.append(command.name)


def test_child_aliases_match_whole_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child alias matches only the first word, in any case."""
    RAN_CHILD.clear()
    alias_c = Command("c", record_child, True, None, rank=Rank.RESIDENT)
    alias_bang = Command("!", record_child, True, None, rank=Rank.RESIDENT)
    commands = [
        Command(
            "widget",
            parent_ran,
            False,
            "widget <command>",
            rank=Rank.RESIDENT,
            subcommands=(alias_c, alias_bang),
        )
    ]
    attach_subcommands(commands)
    parent = commands[0]

    def by_widget() -> dict[str, Command]:
        return {"widget": parent}

    monkeypatch.setattr("lupaxa.chat_server.parse.by_name", by_widget)
    monkeypatch.setattr("lupaxa.chat_server.commands.by_name", by_widget)
    monkeypatch.setattr("lupaxa.chat_server.parse.aliases", lambda: ())
    ada = FakePlayer(name="Ada", player=Player("Ada", "reception", datetime.now(timezone.utc), rank=Rank.RESIDENT))
    server = FakeServer(people=[ada])
    process_commands(server, ada, "widget commands")  # type: ignore[arg-type]
    assert server.sent == ["Subcommands available:\n! c commands\n"]
    assert RAN_CHILD == []
    server.sent.clear()
    process_commands(server, ada, "widget c")  # type: ignore[arg-type]
    assert RAN_CHILD == ["c"]
    RAN_CHILD.clear()
    server.sent.clear()
    process_commands(server, ada, "widget C")  # type: ignore[arg-type]
    assert RAN_CHILD == ["c"]
    RAN_CHILD.clear()
    server.sent.clear()
    process_commands(server, ada, "widget !foo")  # type: ignore[arg-type]
    assert server.sent == ["No such subcommand '!foo'.\n"]
    assert RAN_CHILD == []
    server.sent.clear()
    process_commands(server, ada, "widget !")  # type: ignore[arg-type]
    assert RAN_CHILD == ["!"]


def test_the_commands_table_lists_set() -> None:
    """The commands page lists set, and the developer docs describe children."""
    usage = Path("mkdocs/usage/commands.md").read_text()
    row = "| `set`         |                   | Resident   | Run one set command                         |"
    assert len(row) == 96
    assert row in usage
    assert "`set commands` lists the set commands that rank may use." in usage
    developer = Path("developer-docs/commands.md").read_text()
    assert "run_subcommand" in developer
    assert "No such subcommand" in developer
    assert "subcommands=()" in developer
    assert "register_subcommands" in developer
    assert "A later call for the same parent appends." in developer
    assert "attaches the queued children after any children already in the tuple" in developer
    assert "The player-facing list stays alphabetical." in developer


def test_set_children_are_registered_after_the_extend() -> None:
    """Set's children are registered, and the extend row stays one parent."""
    source = Path("src/lupaxa/chat_server/resident.py").read_text()
    assert 'Command("set", command_set, False, "<command>", rank=Rank.RESIDENT, subcommands=()),' in source
    assert 'register_subcommands(\n    "set",' in source
