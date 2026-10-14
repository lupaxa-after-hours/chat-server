"""Byte and packet counters for one port or one connection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_STEP = Decimal(1000)
_TENTH = Decimal("0.1")
_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB")
_PACKET_UNITS = ("", "K", "M", "G", "T")


@dataclass
class NetStats:
    """Totals, the open window, the last rates, and the averages."""

    in_total: int = 0
    out_total: int = 0
    in_pack_total: int = 0
    out_pack_total: int = 0
    in_current: int = 0
    out_current: int = 0
    in_pack_current: int = 0
    out_pack_current: int = 0
    in_bps: int = 0
    out_bps: int = 0
    in_pps: int = 0
    out_pps: int = 0
    in_average: int = 0
    out_average: int = 0
    in_pack_average: int = 0
    out_pack_average: int = 0


def record(counter: NetStats, direction: str, size: int) -> None:
    """Add one packet of ``size`` bytes. ``size`` below 1 is ignored."""
    if size <= 0:
        return
    if direction == "in":
        counter.in_current += size
        counter.in_pack_current += 1
        return
    counter.out_current += size
    counter.out_pack_current += 1


def note_traffic(
    port: NetStats,
    combined: NetStats,
    connection: NetStats,
    direction: str,
    size: int,
) -> None:
    """Record ``size`` on the port, the combined counter, and the connection."""
    record(port, direction, size)
    record(combined, direction, size)
    record(connection, direction, size)


def add_counts(live: NetStats, extra: NetStats) -> None:
    """Add ``extra`` totals and open window onto ``live``. Rates stay."""
    live.in_total += extra.in_total
    live.out_total += extra.out_total
    live.in_pack_total += extra.in_pack_total
    live.out_pack_total += extra.out_pack_total
    live.in_current += extra.in_current
    live.out_current += extra.out_current
    live.in_pack_current += extra.in_pack_current
    live.out_pack_current += extra.out_pack_current


def roll(counter: NetStats) -> None:
    """Close one 10-second window. Divide by 10 even when the window was short."""
    counter.in_total += counter.in_current
    counter.out_total += counter.out_current
    counter.in_pack_total += counter.in_pack_current
    counter.out_pack_total += counter.out_pack_current
    counter.in_bps = counter.in_current // 10
    counter.out_bps = counter.out_current // 10
    counter.in_pps = counter.in_pack_current // 10
    counter.out_pps = counter.out_pack_current // 10
    counter.in_average = (counter.in_average + counter.in_bps) >> 1
    counter.out_average = (counter.out_average + counter.out_bps) >> 1
    counter.in_pack_average = (counter.in_pack_average + counter.in_pps) >> 1
    counter.out_pack_average = (counter.out_pack_average + counter.out_pps) >> 1
    counter.in_current = 0
    counter.out_current = 0
    counter.in_pack_current = 0
    counter.out_pack_current = 0


def format_amount(value: int, *, packets: bool) -> tuple[str, str]:
    """Return the number and unit. Scaled units use one decimal, half up."""
    units = _PACKET_UNITS if packets else _BYTE_UNITS
    scaled = Decimal(value)
    index = 0
    while index < len(units) - 1 and scaled >= _STEP:
        scaled /= _STEP
        index += 1
    if index == 0:
        return str(value), units[0]
    shown = scaled.quantize(_TENTH, rounding=ROUND_HALF_UP)
    while shown >= _STEP and index < len(units) - 1:
        shown = (shown / _STEP).quantize(_TENTH, rounding=ROUND_HALF_UP)
        index += 1
    return f"{shown:.1f}", units[index]


def _cell(value: int, *, packets: bool) -> str:
    """Return the number in six columns and the unit in the next two."""
    number, unit = format_amount(value, packets=packets)
    return f"{number:>6} {unit:<2}"


def _row(label: str, left: str, right: str) -> str:
    """Return one inset stats line."""
    padded = f"{label:<18}"
    return f" {padded} : (In)  {left}  (Out) {right}"


def network_rows(counter: NetStats) -> tuple[str, ...]:
    """Return the six stats lines, without a border."""
    pairs = (
        ("Total bytes", counter.in_total, counter.out_total, False),
        ("Average bytes", counter.in_average, counter.out_average, False),
        ("Bytes per second", counter.in_bps, counter.out_bps, False),
        ("Total packets", counter.in_pack_total, counter.out_pack_total, True),
        (
            "Average packets",
            counter.in_pack_average,
            counter.out_pack_average,
            True,
        ),
        ("Packets per second", counter.in_pps, counter.out_pps, True),
    )
    rows: list[str] = []
    for label, incoming, outgoing, packets in pairs:
        left = _cell(incoming, packets=packets)
        right = _cell(outgoing, packets=packets)
        rows.append(_row(label, left, right))
    return tuple(rows)


def network_box(counter: NetStats) -> str:
    """Return the stats box, only as wide as its rows, ending with a newline."""
    rows = network_rows(counter)
    longest = max(len(row) for row in rows)
    inner = longest + 1
    border = "+" + ("-" * inner) + "+"
    body = ["|" + row.ljust(inner) + "|" for row in rows]
    return "\n".join((border, *body, border)) + "\n"
