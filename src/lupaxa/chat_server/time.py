"""Turn seconds and connect times into player-facing phrases."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def ordinal(day: int) -> str:
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
    weekday = WEEKDAYS[moment.weekday()]
    month = MONTHS[moment.month - 1]
    clock = moment.strftime("%H:%M:%S")
    return f"{weekday} {ordinal(moment.day)} {month} {moment.year}, {clock} UTC"


def duration_phrase(seconds: int) -> str:
    """Return ``seconds`` in words, from seconds up to days."""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return count(seconds, "second")
    minutes, unused = divmod(seconds, 60)
    if minutes < 60:
        return count(minutes, "minute")
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        if minutes == 0:
            return count(hours, "hour")
        return f"{count(hours, 'hour')}, {count(minutes, 'minute')}"
    days, hours = divmod(hours, 24)
    if hours == 0:
        return count(days, "day")
    return f"{count(days, 'day')}, {count(hours, 'hour')}"


def cpu_phrase(seconds: float) -> str:
    """Return CPU seconds in words, keeping a fractional second."""
    if seconds < 0:
        seconds = 0.0
    rounded = Decimal(str(seconds)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    tenths = int(rounded * 10)
    if tenths == 0:
        return "0 seconds"
    days, rest = divmod(tenths, 864000)
    hours, rest = divmod(rest, 36000)
    minutes, rest = divmod(rest, 600)
    whole, frac = divmod(rest, 10)
    parts: list[str] = []
    if days:
        parts.append(count(days, "day"))
    if hours:
        parts.append(count(hours, "hour"))
    if minutes:
        parts.append(count(minutes, "minute"))
    if frac:
        parts.append(f"{whole}.{frac} seconds")
    elif whole:
        parts.append(count(whole, "second"))
    return ", ".join(parts)


def connected_phrase(connected_at: datetime, now: datetime) -> str:
    """Return how long a player has been connected, in words."""
    return duration_phrase(int((now - connected_at).total_seconds()))


IDLE_ACTIVE_SECONDS = 5 * 60
IDLE_RED_SECONDS = 30 * 60
IDLE_BLANK_SECONDS = 60 * 60


def idle_clock(seconds: int) -> str:
    """Return ``[ Active ]`` under five minutes, ``[--:--:--]`` from one hour, else ``[HH:MM:SS]``."""
    if seconds < IDLE_ACTIVE_SECONDS:
        return "[ Active ]"
    if seconds >= IDLE_BLANK_SECONDS:
        return "[--:--:--]"
    hours, rest = divmod(max(seconds, 0), 3600)
    minutes, secs = divmod(rest, 60)
    return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"


def count(amount: int, unit: str) -> str:
    """Return ``amount`` and ``unit``, with a plural s when needed."""
    if amount == 1:
        return f"1 {unit}"
    return f"{amount} {unit}s"
