"""Chain names, ids, and the tell target split."""

from datetime import datetime, timezone

from test_commands import FakePlayer, FakeServer

from lupaxa.chat_server.chain import (  # noqa: I001
    MAX_CHAIN_PEOPLE,
    Chain,
    chain_number,
    close_idle_chains,
    delete_chain,
    deliver,
    depart,
    find_chain,
    find_same_chain,
    leave_chain,
    listed_names,
    lowest_free_number,
    note_chain_departure,
    prune_offline,
    remove_from_chain,
    split_target_message,
)


def _person(name: str, room: str = "hall") -> FakePlayer:
    """Return a logged-in stand-in with this name."""
    return FakePlayer(name=name, room=room)


def _server(*people: FakePlayer) -> FakeServer:
    """Return a stand-in server with these people."""
    return FakeServer(people=list(people))


def test_listed_names_sorts_and_says_you() -> None:
    """Names sort case-insensitively, and you replaces the viewer in place."""
    assert listed_names(["Bea", "Ade"]) == "Ade & Bea"
    assert listed_names(["Cyd", "Ade", "Bea"]) == "Ade, Bea & Cyd"
    assert listed_names(["Bea", "Ade"], you="Bea") == "Ade & you"
    assert listed_names(["Bea", "Ade"], you="Ade") == "you & Bea"


def test_lowest_free_number_reuses_a_gap() -> None:
    """The next id is the smallest positive integer that is free."""
    when = datetime(2026, 10, 15, tzinfo=timezone.utc)
    chains = [Chain(number, ["Ada"], when) for number in (1, 2, 3, 5)]
    assert lowest_free_number(chains) == 4
    assert lowest_free_number([]) == 1


def test_find_chain_matches_the_set_and_ignores_order() -> None:
    """Lookup is by id, or by the same people in any order and case."""
    when = datetime(2026, 10, 15, tzinfo=timezone.utc)
    chain = Chain(4, ["Ada", "Bea", "Cyd"], when)
    assert find_chain([chain], 4) is chain
    assert find_chain([chain], 1) is None
    assert find_same_chain([chain], ["cyd", "Ada", "Bea"]) is chain
    assert find_same_chain([chain], ["Ada", "Bea"]) is None


def test_split_target_message_allows_a_space_after_a_comma() -> None:
    """The last piece's first word is a target. The rest is the message."""
    assert split_target_message("Bea,Ade hello") == (["Bea", "Ade"], "hello")
    assert split_target_message("Bea,Ade, Cyd hello") == (["Bea", "Ade", "Cyd"], "hello")
    assert split_target_message("Bea, Ade hello there") == (["Bea", "Ade"], "hello there")
    assert split_target_message("1,Dee hello") == (["1", "Dee"], "hello")
    assert split_target_message("Bea hello") == (["Bea"], "hello")
    assert split_target_message("Bea hello, how are you") == (["Bea"], "hello, how are you")
    assert split_target_message("Bea hello, there") == (["Bea"], "hello, there")
    assert split_target_message("Bea, Ade hi, all") == (["Bea", "Ade"], "hi, all")
    assert split_target_message("1 yes, sure") == (["1"], "yes, sure")
    assert split_target_message("Bea") is None
    assert split_target_message("") is None
    assert split_target_message("Bea,,Ade hello") is None


def test_chain_number_rejects_non_ascii_digits() -> None:
    """Only ASCII digits form a chain id."""
    assert chain_number("²") is None
    assert chain_number("4") == 4


def test_deliver_treats_non_ascii_digit_as_a_name() -> None:
    """A superscript digit is matched as a player name, not a chain id."""
    ada = _person("Ada", "reception")
    server = _server(ada)
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    result = deliver(server, ada, ["²"], when)
    assert result.stopped is False
    assert result.sender_lines == ["Player ² not found.\n"]
    assert result.recipients == []


def test_two_names_create_the_lowest_free_chain() -> None:
    """Two other people create a chain that includes the sender."""
    ada = _person("Ada", "reception")
    bea = _person("Bea")
    cyd = _person("Cyd")
    server = _server(ada, bea, cyd)
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    result = deliver(server, ada, ["Bea", "Cyd"], when)
    assert result.stopped is False
    assert result.sender_lines == []
    assert result.chain is not None
    assert result.chain.number == 1
    assert {name.casefold() for name in result.chain.names} == {"ada", "bea", "cyd"}
    assert {person.name for person in result.recipients} == {"Bea", "Cyd"}
    assert server.chains == [result.chain]


def test_one_survivor_does_not_create_a_chain() -> None:
    """A missing name is reported, and one remaining person is an ordinary send."""
    ada = _person("Ada", "reception")
    bea = _person("Bea")
    server = _server(ada, bea)
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    result = deliver(server, ada, ["Bea", "Zed"], when)
    assert result.chain is None
    assert result.stopped is False
    assert result.sender_lines == ["Player Zed not found.\n"]
    assert [person.name for person in result.recipients] == ["Bea"]
    assert server.chains == []


def test_a_chain_you_are_not_on_stops_before_name_matching() -> None:
    """A bad chain id sends nothing, and the other piece is not matched."""
    ada = _person("Ada", "reception")
    server = _server(ada)
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    result = deliver(server, ada, ["4", "Zed"], when)
    assert result.stopped is True
    assert result.sender_lines == ["You are not on chain 4.\n"]
    assert result.recipients == []
    assert server.chains == []


def test_two_chain_ids_are_refused() -> None:
    """One command names one chain. Repeating that id is still one chain."""
    ada = _person("Ada", "reception")
    bea = _person("Bea")
    cyd = _person("Cyd")
    server = _server(ada, bea, cyd)
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    made = deliver(server, ada, ["Bea", "Cyd"], when)
    assert made.chain is not None
    again = deliver(server, ada, ["1", "1"], when)
    assert again.stopped is False
    assert again.chain is made.chain
    refused = deliver(server, ada, ["1", "2"], when)
    assert refused.stopped is True
    assert refused.sender_lines == ["Name one chain.\n"]


def test_adding_until_the_sets_match_removes_the_other_chain() -> None:
    """The addressed chain keeps the people. The duplicate chain is removed."""
    ada = _person("Ada", "reception")
    bea = _person("Bea")
    cyd = _person("Cyd")
    dee = _person("Dee")
    server = _server(ada, bea, cyd, dee)
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    first = deliver(server, ada, ["Bea", "Cyd"], when)
    second = deliver(server, ada, ["Bea", "Cyd", "Dee"], when)
    assert first.chain is not None
    assert second.chain is not None
    assert first.chain.number == 1
    assert second.chain.number == 2
    merged = deliver(server, ada, ["1", "Dee"], when)
    assert merged.stopped is False
    assert merged.chain is not None
    assert merged.chain.number == 1
    assert {name.casefold() for name in merged.chain.names} == {"ada", "bea", "cyd", "dee"}
    assert merged.removed_note == "Chain 2 has been removed. It is the same as chain 1.\n"
    assert {name.casefold() for name in merged.removed_names} == {"ada", "bea", "cyd", "dee"}
    assert [chain.number for chain in server.chains] == [1]


def test_eleven_people_are_refused_without_a_change() -> None:
    """The cap counts the sender. A refused send does not add anyone."""
    names = ["Ada", "Bea", "Cyd", "Dee", "Eve", "Fay", "Gus", "Hal", "Ian", "Joe", "Kay"]
    people = [_person(name, "reception" if name == "Ada" else "hall") for name in names]
    server = _server(*people)
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    result = deliver(server, people[0], names[1:], when)
    assert result.stopped is True
    assert result.sender_lines == [f"A chain can hold {MAX_CHAIN_PEOPLE} people. Get in a room and use say.\n"]
    assert server.chains == []


def test_one_departure_closes_a_chain_of_three() -> None:
    """Two people left are told, and the chain is gone."""
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    chains = [Chain(1, ["Ada", "Bea", "Cyd"], when)]
    notices = depart(chains, "Bea")
    assert chains == []
    assert notices == [(["Ada", "Cyd"], "(1) Chain closed because Bea has logged out.\n")]


def test_departure_of_the_only_member_leaves_no_close_line() -> None:
    """One person alone on a chain leaves nothing behind."""
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    chains = [Chain(1, ["Ada"], when)]
    assert depart(chains, "Ada") == []
    assert chains == []


def test_a_departure_that_leaves_three_tells_the_others() -> None:
    """Four people become three, and the people still on it are told."""
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    chains = [Chain(1, ["Ada", "Bea", "Cyd", "Dee"], when)]
    notices = depart(chains, "Dee")
    assert notices == [(["Ada", "Bea", "Cyd"], "(1) Dee has left the chain because they logged out.\n")]
    assert [name.casefold() for name in chains[0].names] == ["ada", "bea", "cyd"]


def test_a_smaller_chain_that_matches_another_deletes_the_higher_id() -> None:
    """A logout that copies an existing set removes the higher id."""
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    chains = [
        Chain(1, ["Ada", "Bea", "Cyd"], when),
        Chain(4, ["Ada", "Bea", "Cyd", "Dee"], when),
    ]
    notices = depart(chains, "Dee")
    assert [chain.number for chain in chains] == [1]
    assert notices[0] == (["Ada", "Bea", "Cyd"], "(4) Dee has left the chain because they logged out.\n")
    assert notices[1] == (["Ada", "Bea", "Cyd"], "Chain 4 has been removed. It is the same as chain 1.\n")


def test_note_chain_departure_tells_the_people_still_here() -> None:
    """The person leaving does not get the close line."""
    ada = _person("Ada", "reception")
    bea = _person("Bea")
    cyd = _person("Cyd")
    server = _server(ada, bea, cyd)
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    deliver(server, ada, ["Bea", "Cyd"], when)
    note_chain_departure(server, "Bea")
    assert server.chains == []
    assert ada.sent == ["(1) Chain closed because Bea has logged out.\n"]
    assert cyd.sent == ["(1) Chain closed because Bea has logged out.\n"]
    assert bea.sent == []


def test_prune_offline_closes_with_every_absent_name() -> None:
    """Several people missing at once share one close line."""
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    chains = [Chain(1, ["Ada", "Bea", "Cyd", "Dee"], when)]
    kept, notices = prune_offline(chains, {"ada", "bea"})
    assert kept == []
    assert notices == [(["Ada", "Bea"], "(1) Chain closed because Cyd & Dee have logged out.\n")]


def test_prune_offline_keeps_a_chain_of_three() -> None:
    """One absent name that leaves three people is announced."""
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    chains = [Chain(1, ["Ada", "Bea", "Cyd", "Dee"], when)]
    kept, notices = prune_offline(chains, {"ada", "bea", "cyd"})
    assert notices == [(["Ada", "Bea", "Cyd"], "(1) Dee has left the chain because they logged out.\n")]
    assert [chain.number for chain in kept] == [1]
    assert [name.casefold() for name in kept[0].names] == ["ada", "bea", "cyd"]
    assert kept[0].last_message_at == when


def test_addressing_the_higher_chain_still_deletes_the_higher_id() -> None:
    """A matching set always drops the higher id, even when that id was addressed."""
    ada = _person("Ada", "reception")
    bea = _person("Bea")
    cyd = _person("Cyd")
    dee = _person("Dee")
    server = _server(ada, bea, cyd, dee)
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    deliver(server, ada, ["Bea", "Cyd", "Dee"], when)
    deliver(server, ada, ["Bea", "Cyd"], when)
    merged = deliver(server, ada, ["2", "Dee"], when)
    assert merged.chain is not None
    assert merged.chain.number == 1
    assert merged.removed_note == "Chain 2 has been removed. It is the same as chain 1.\n"
    assert [chain.number for chain in server.chains] == [1]


def test_leave_remove_and_delete() -> None:
    """Leave and remove close a chain below three. Delete removes it at any size."""
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    chains = [Chain(1, ["Ada", "Bea", "Cyd", "Dee"], when)]
    error, notices = leave_chain(chains, 1, "Dee", "dinner")
    assert error is None
    assert notices == [(["Ada", "Bea", "Cyd"], "(1) Dee has left the chain. dinner\n")]
    errors, removed = remove_from_chain(chains, 1, "Ada", ["Bea"], "")
    assert errors == []
    assert removed == [(["Ada", "Cyd", "Bea"], "(1) Chain closed because Ada removed Bea from the chain.\n")]
    assert chains == []
    fresh = [Chain(2, ["Ada", "Bea", "Cyd", "Dee"], when)]
    problem, notice = delete_chain(fresh, 2, "Ada", "done")
    assert problem is None
    assert notice is not None
    assert notice[1] == "(2) Ada deleted the chain. done\n"
    assert fresh == []


def test_close_idle_chains_tells_the_members() -> None:
    """A chain with no message for two hours is deleted."""
    ada = _person("Ada", "reception")
    bea = _person("Bea")
    cyd = _person("Cyd")
    server = _server(ada, bea, cyd)
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    server.chains.append(Chain(1, ["Ada", "Bea", "Cyd"], when))
    close_idle_chains(server, when)
    assert len(server.chains) == 1
    close_idle_chains(server, datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc))
    assert server.chains == []
    assert ada.sent == ["(1) Chain closed. It was unused for 2 hours.\n"]
    assert bea.sent == ["(1) Chain closed. It was unused for 2 hours.\n"]


def test_prune_offline_with_no_one_online_leaves_no_close_line() -> None:
    """When nobody is connected, small chains vanish quietly."""
    when = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    chains = [Chain(1, ["Ada", "Bea"], when)]
    kept, notices = prune_offline(chains, set())
    assert kept == []
    assert notices == []
