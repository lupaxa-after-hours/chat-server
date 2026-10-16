"""Grab pulls a lower-rank player into the caller's office room."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from test_commands import FakePlayer, FakeServer, process_commands, staff

from lupaxa.chat_server.player import Player
from lupaxa.chat_server.ranks import Rank
from lupaxa.chat_server.rooms import Room, house_room_id


def resident(name: str, room: str) -> FakePlayer:
    """Return a resident standing in ``room``."""
    user = Player(name, room, datetime.now(timezone.utc), rank=Rank.RESIDENT)
    return FakePlayer(name=name, room=room, player=user)


def test_a_resident_cannot_grab() -> None:
    """Below super user, grab is not a command."""
    ada = staff("Ada", Rank.RESIDENT)
    bea = resident("Bea", "hall")
    server = FakeServer(people=[ada, bea])
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert bea.room == "hall"
    assert server.sent == ["No such command 'grab'\n"]


def test_grab_needs_a_name() -> None:
    """A bare grab replies with the syntax line."""
    ada = staff("Ada", Rank.SUPER_USER)
    server = FakeServer(people=[ada])
    process_commands(server, ada, "grab")  # type: ignore[arg-type]
    assert server.sent == ["Syntax: grab <name>\n"]


def test_grab_moves_a_resident_and_tells_the_rooms() -> None:
    """The old room, the target, and the caller each hear their own line."""
    ada = staff("Ada", Rank.SUPER_USER)
    bea = resident("Bea", "hall")
    assert bea.player is not None
    bea.player.entermsg = "crawls into the room"
    cyd = resident("Cyd", "hall")
    server = FakeServer(people=[ada, bea, cyd])
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert bea.room == "reception"
    assert cyd.sent == ["Bea is grabbed by Ada.\n"]
    assert bea.sent[0] == " -=> Ada stretches out and grabs you !!!!!!\n"
    assert bea.sent[1] == server.layout.brief("reception", ["Ada"])
    assert "Bea is grabbed by Ada.\n" not in bea.sent
    assert "Bea crawls into the room.\n" not in bea.sent
    assert ada.sent == ["Bea crawls into the room.\n"]
    assert server.staff_lines == []
    assert server.room_lines == ["Bea is grabbed by Ada.\n", "Bea crawls into the room.\n"]


def test_grab_pulls_someone_out_of_a_house() -> None:
    """A target in their own home, or in someone else's, is pulled out and the empty house is dropped."""
    ada = staff("Ada", Rank.SUPER_USER)
    bea = resident("Bea", "bea_home")
    server = FakeServer(people=[ada, bea])
    server.layout = server.layout.with_house("Bea", {house_room_id("Bea"): Room("You are home.", 1, {})})
    process_commands(server, ada, "grab bea")  # type: ignore[arg-type]
    assert bea.room == "reception"
    assert "bea_home" not in server.layout.rooms
    assert bea.sent[0] == " -=> Ada stretches out and grabs you !!!!!!\n"
    assert ada.sent == ["Bea arrives.\n"]

    ada.sent.clear()
    cyd = resident("Cyd", "bea_home")
    server = FakeServer(people=[ada, cyd])
    server.layout = server.layout.with_house("Bea", {house_room_id("Bea"): Room("You are home.", 1, {})})
    process_commands(server, ada, "grab Cyd")  # type: ignore[arg-type]
    assert cyd.room == "reception"
    assert "bea_home" not in server.layout.rooms


def test_grab_refuses_the_listed_cases() -> None:
    """Rank, self, place, capacity, and a missing name do not move anyone."""
    ada = staff("Ada", Rank.SUPER_USER)
    bea = resident("Bea", "reception")
    server = FakeServer(people=[ada, bea])
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert server.sent == ["But Bea is already in the same room as you!\n"]
    assert server.room_lines == []

    peer = staff("Bea", Rank.SUPER_USER)
    peer.room = "hall"
    assert peer.player is not None
    peer.player.room = "hall"
    server = FakeServer(people=[ada, peer])
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert peer.room == "hall"
    assert server.sent == ["You cannot grab Bea.\n"]

    boss = staff("Bea", Rank.ADMIN)
    boss.room = "hall"
    assert boss.player is not None
    boss.player.room = "hall"
    server = FakeServer(people=[ada, boss])
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert server.sent == ["You cannot grab Bea.\n"]

    server = FakeServer(people=[ada])
    process_commands(server, ada, "grab Ada")  # type: ignore[arg-type]
    assert server.sent == ["You cannot do that to yourself.\n"]
    process_commands(server, ada, "grab ad")  # type: ignore[arg-type]
    assert server.sent[-1] == "You cannot do that to yourself.\n"

    bea = resident("Bea", "hall")
    ada.room = "ada_home"
    assert ada.player is not None
    ada.player.room = "ada_home"
    server = FakeServer(people=[ada, bea])
    server.layout = server.layout.with_house("Ada", {house_room_id("Ada"): Room("You are home.", 1, {})})
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert bea.room == "hall"
    assert server.sent == ["You can only grab someone into a system room.\n"]

    ada.room = "void"
    ada.player.room = "void"
    server = FakeServer(people=[ada, bea])
    server.layout = server.layout.with_void()
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert bea.room == "hall"
    assert server.sent == ["You can only grab someone into a system room.\n"]

    bea.room = "void"
    assert bea.player is not None
    bea.player.room = "void"
    server = FakeServer(people=[ada, bea])
    server.layout = server.layout.with_void()
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert server.sent == ["But Bea is already in the same room as you!\n"]

    ada.room = "reception"
    ada.player.room = "reception"
    bea = resident("Bea", "hall")
    crowd = [resident(name, "reception") for name in ("Cyd", "Dee", "Eve")]
    server = FakeServer(people=[ada, bea, *crowd])
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert bea.room == "hall"
    assert server.sent == ["Reception is full.\n"]

    server = FakeServer(people=[ada])
    process_commands(server, ada, "grab Nobody")  # type: ignore[arg-type]
    assert server.sent == ["Player Nobody not found.\n"]


def test_grab_uses_one_partial_name_and_refuses_several() -> None:
    """Grab be follows Bea. Grab b names both Bea and Ben."""
    ada = staff("Ada", Rank.SUPER_USER)
    bea = resident("Bea", "hall")
    server = FakeServer(people=[ada, bea])
    process_commands(server, ada, "grab be")  # type: ignore[arg-type]
    assert bea.room == "reception"

    ben = resident("Ben", "hall")
    bea = resident("Bea", "hall")
    server = FakeServer(people=[ada, bea, ben])
    process_commands(server, ada, "grab b")  # type: ignore[arg-type]
    assert bea.room == "hall"
    assert ben.room == "hall"
    assert server.sent == ["multiple Matches: Bea & Ben\n"]


def test_a_higher_rank_can_grab_the_rank_below() -> None:
    """An admin can grab a super user, and the super admin can grab an admin."""
    ada = staff("Ada", Rank.ADMIN)
    bea = staff("Bea", Rank.SUPER_USER)
    bea.room = "hall"
    assert bea.player is not None
    bea.player.room = "hall"
    server = FakeServer(people=[ada, bea])
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert bea.room == "reception"

    ada = staff("Ada", Rank.SUPER_ADMIN)
    bea = staff("Bea", Rank.ADMIN)
    bea.room = "hall"
    assert bea.player is not None
    bea.player.room = "hall"
    server = FakeServer(people=[ada, bea])
    process_commands(server, ada, "grab Bea")  # type: ignore[arg-type]
    assert bea.room == "reception"


@pytest.fixture(autouse=True)
def patch_su_broadcast_room(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record room broadcasts on ``FakeServer`` without stubbing occupancy or others."""
    import lupaxa.chat_server.broadcast as broadcast_api
    import lupaxa.chat_server.su as su_api

    def broadcast_room(server: object, room_id: str | None, text: str, *, skip: object) -> None:
        if isinstance(server, FakeServer):
            server.broadcast_room(room_id, text, skip=skip)  # type: ignore[arg-type]
            return
        broadcast_api.broadcast_room(server, room_id, text, skip=skip)  # type: ignore[arg-type]

    monkeypatch.setattr(su_api, "broadcast_room", broadcast_room)
