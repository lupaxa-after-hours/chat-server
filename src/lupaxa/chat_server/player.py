"""A logged-in player. Further fields can be added here later."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .defaults import DEFAULT_PROMPT


@dataclass
class Player:
    """Name, room, connect time, and the counters the timer loop updates."""

    name: str
    room: str
    connected_at: datetime
    alarm_seconds: int | None = None
    idle_seconds: int = 0
    prompt: str = DEFAULT_PROMPT
    rank: str = "newbie"
    connected_seconds: int = 0
    flushed_seconds: int = 0
    lowercase_name: str = ""
    colour: bool = True
    description: str = ""
    status: str = ""
    auto_reply: str = ""

    def __post_init__(self) -> None:
        """Fill the key from the display spelling when the caller did not pass one."""
        if self.lowercase_name == "":
            self.lowercase_name = self.name.lower()
