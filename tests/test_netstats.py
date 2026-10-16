"""Counters, the 10-second roll, and the network box."""

from lupaxa.chat_server.netstats import NetStats, add_counts, format_amount, network_box, note_traffic, record, roll


def test_record_adds_one_packet_and_ignores_an_empty_one() -> None:
    """A positive size is one packet. Zero and negative are nothing."""
    counter = NetStats()
    record(counter, "in", 5)
    record(counter, "out", 2)
    record(counter, "in", 0)
    record(counter, "out", -1)
    assert counter.in_current == 5
    assert counter.in_pack_current == 1
    assert counter.out_current == 2
    assert counter.out_pack_current == 1


def test_note_traffic_updates_port_combined_and_connection() -> None:
    """One read lands on the port, the combined counter, and the connection."""
    telnet = NetStats()
    ssh = NetStats()
    combined = NetStats()
    connection = NetStats()
    note_traffic(telnet, combined, connection, "in", 8)
    note_traffic(ssh, combined, connection, "in", 3)
    assert telnet.in_current == 8
    assert telnet.in_pack_current == 1
    assert ssh.in_current == 3
    assert combined.in_current == 11
    assert combined.in_pack_current == 2
    assert connection.in_current == 11
    assert connection.in_pack_current == 2


def test_add_counts_keeps_the_live_rates() -> None:
    """A reconnect adds totals and the open window, and leaves rates."""
    live = NetStats(in_total=10, in_current=4, in_bps=2, in_average=1)
    extra = NetStats(in_total=7, in_current=3, in_bps=9, in_average=8)
    add_counts(live, extra)
    assert live.in_total == 17
    assert live.in_current == 7
    assert live.in_bps == 2
    assert live.in_average == 1


def test_roll_uses_integer_division_and_a_right_shift() -> None:
    """The window is divided by 10, then folded into the average."""
    counter = NetStats(in_current=25, out_current=9, in_pack_current=4)
    roll(counter)
    assert counter.in_total == 25
    assert counter.out_total == 9
    assert counter.in_pack_total == 4
    assert counter.in_bps == 2
    assert counter.out_bps == 0
    assert counter.in_pps == 0
    assert counter.in_average == 1
    assert counter.in_current == 0
    assert counter.out_current == 0
    assert counter.in_pack_current == 0
    roll(counter)
    assert counter.in_average == 0


def test_format_amount_scales_by_1000_and_rounds_half_up() -> None:
    """Units step up only when the shown number stays at least 1."""
    assert format_amount(0, packets=False) == ("0", "B")
    assert format_amount(999, packets=False) == ("999", "B")
    assert format_amount(1500, packets=False) == ("1.5", "KB")
    assert format_amount(300_000_000, packets=False) == ("300.0", "MB")
    assert format_amount(999_999, packets=False) == ("1.0", "MB")
    assert format_amount(0, packets=True) == ("0", "")
    assert format_amount(1500, packets=True) == ("1.5", "K")
    assert format_amount(999, packets=True) == ("999", "")


def test_network_box_right_aligns_numbers_and_units() -> None:
    """Digits share a column, and the box is only as wide as the rows."""
    counter = NetStats(in_total=1500, out_total=300_000_000, in_pack_total=40)
    counter.out_pack_total = 4
    text = network_box(counter)
    assert "Total bytes        : (In)     1.5 KB  (Out)  300.0 MB" in text
    assert "Total packets      : (In)      40     (Out)      4" in text
    lines = text.splitlines()
    content = next(line for line in lines if "Total bytes" in line)
    assert lines[0].startswith("+")
    assert lines[0].endswith("+")
    assert len(lines[0]) == len(content) == 57
    assert content.startswith("| ")
    assert text.endswith("+\n")
    assert "0.3 GB" not in text
