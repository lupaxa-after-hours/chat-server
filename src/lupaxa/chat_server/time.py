"""Turn seconds and connect times into player-facing phrases."""

from __future__ import annotations

from datetime import datetime, timezone

_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _ordinal(day: int) -> str:
    """Return ``day`` with an English ending. 11, 12, and 13 use ``th``."""
    if day % 100 in (11, 12, 13):
        ending = "th"
    elif day % 10 == 1:
        ending = "st"
    elif day % 10 == 2:
        ending = "nd"
    elif day % 10 == 3:
        ending = "rd"
    else:
        ending = "th"
    return f"{day}{ending}"


def clock_phrase(moment: datetime) -> str:
    """Return ``moment`` as ``Monday 12th October 2026, 13:45:03 UTC``."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    weekday = _WEEKDAYS[moment.weekday()]
    month = _MONTHS[moment.month - 1]
    clock = moment.strftime("%H:%M:%S")
    return f"{weekday} {_ordinal(moment.day)} {month} {moment.year}, {clock} UTC"


def duration_phrase(seconds: int) -> str:
    """Return ``seconds`` in words, from seconds up to days."""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return _count(seconds, "second")
    minutes, _unused = divmod(seconds, 60)
    if minutes < 60:
        return _count(minutes, "minute")
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        if minutes == 0:
            return _count(hours, "hour")
        return f"{_count(hours, 'hour')}, {_count(minutes, 'minute')}"
    days, hours = divmod(hours, 24)
    if hours == 0:
        return _count(days, "day")
    return f"{_count(days, 'day')}, {_count(hours, 'hour')}"


def connected_phrase(connected_at: datetime, now: datetime) -> str:
    """Return how long a player has been connected, in words."""
    return duration_phrase(int((now - connected_at).total_seconds()))


def idle_clock(seconds: int) -> str:
    """Return ``HH:MM:SS`` when ``seconds`` is greater than 10, else an empty string."""
    if seconds <= 10:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _count(amount: int, unit: str) -> str:
    if amount == 1:
        return f"1 {unit}"
    return f"{amount} {unit}s"
