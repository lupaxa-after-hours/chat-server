"""Country names from an address and an MMDB record."""

from __future__ import annotations

from pathlib import Path

import pytest

from lupaxa.chat_server.country import (
    LOCAL_COUNTRY_CODE,
    LOCAL_COUNTRY_NAME,
    PRIVATE_COUNTRY_CODE,
    PRIVATE_COUNTRY_NAME,
    UNKNOWN_COUNTRY_CODE,
    UNKNOWN_COUNTRY_NAME,
    country_from_record,
    database_readable,
    is_local_address,
    is_private_address,
    is_rfc1918,
)

DATABASE = Path("config/countries/ipinfo_lite.mmdb")


def test_rfc1918_is_private_ipv4_space() -> None:
    """The three private blocks count. Loopback and the neighbours do not."""
    assert is_rfc1918("10.0.0.1")
    assert is_rfc1918("172.16.5.5")
    assert is_rfc1918("172.31.255.1")
    assert is_rfc1918("192.168.0.9")
    assert not is_rfc1918("172.15.0.1")
    assert not is_rfc1918("172.32.0.1")
    assert not is_rfc1918("8.8.8.8")
    assert not is_rfc1918("127.0.0.1")
    assert not is_rfc1918("not-an-ip")


def test_ipv6_unique_local_and_mapped_private_addresses_are_private() -> None:
    """Unique-local IPv6 and an IPv4-mapped private address count as private."""
    assert is_private_address("10.0.0.1")
    assert is_private_address("fd00::1")
    assert is_private_address("fc00::1")
    assert is_private_address("::ffff:192.168.1.1")
    assert not is_private_address("2001:4860:4860::8888")
    assert not is_private_address("fe80::1")
    assert not is_private_address("::1")
    assert not is_private_address("::ffff:8.8.8.8")


def test_country_from_record_reads_ipinfo_and_maxmind_shapes() -> None:
    """A flat IPinfo record and a nested MaxMind record both yield a name and code."""
    assert country_from_record({"country": "United States", "country_code": "us"}) == ("United States", "US")
    nested = {"country": {"iso_code": "gb", "names": {"en": "United Kingdom"}}}
    assert country_from_record(nested) == ("United Kingdom", "GB")
    assert country_from_record(None) == (UNKNOWN_COUNTRY_NAME, UNKNOWN_COUNTRY_CODE)
    assert country_from_record({"country": "Nope", "country_code": "U"}) == (UNKNOWN_COUNTRY_NAME, UNKNOWN_COUNTRY_CODE)


def test_database_readable_needs_one_byte(tmp_path: Path) -> None:
    """An empty or missing file cannot be used. One byte can."""
    missing = tmp_path / "missing.mmdb"
    empty = tmp_path / "empty.mmdb"
    empty.write_bytes(b"")
    present = tmp_path / "present.mmdb"
    present.write_bytes(b"x")
    assert database_readable(missing) is False
    assert database_readable(empty) is False
    assert database_readable(present) is True


def test_loopback_is_a_local_address() -> None:
    """Localhost is loopback, including an IPv4 address carried as IPv6."""
    assert is_local_address("127.0.0.1")
    assert is_local_address("127.1.2.3")
    assert is_local_address("::1")
    assert is_local_address("::ffff:127.0.0.1")
    assert not is_local_address("192.168.1.1")
    assert not is_local_address("fe80::1")
    assert not is_local_address("8.8.8.8")


def test_ipinfo_fields_include_the_network_and_asn() -> None:
    """A public record adds the continent, ASN, and the prefix as an IP block."""
    from lupaxa.chat_server.country import ipinfo_fields

    record = {"country": "United States", "country_code": "US", "continent": "North America", "continent_code": "NA", "asn": "AS15169", "as_name": "Google LLC", "as_domain": "google.com", "city": "Mountain View"}
    fields = dict(ipinfo_fields("8.8.8.8", "dns.google", record, 24))
    assert fields["Country"] == "United States (US)"
    assert fields["Continent"] == "North America (NA)"
    assert fields["ASN"] == "AS15169"
    assert fields["AS name"] == "Google LLC"
    assert fields["IP block"] == "8.8.8.0/24"
    assert fields["City"] == "Mountain View"
    private = dict(ipinfo_fields("10.1.2.3", "lan", record, 24))
    assert private["Country"] == "Private Network (XP)"
    assert private["IP block"] == "10.0.0.0/8"
    assert "ASN" not in private


def test_private_network_constants() -> None:
    """An X prefix means the code is not a real country. L, P, and U say which."""
    assert LOCAL_COUNTRY_NAME == "Localhost"
    assert LOCAL_COUNTRY_CODE == "XL"
    assert PRIVATE_COUNTRY_NAME == "Private Network"
    assert PRIVATE_COUNTRY_CODE == "XP"
    assert UNKNOWN_COUNTRY_CODE == "XU"


@pytest.mark.skipif(not DATABASE.is_file(), reason="country database is local only")
def test_the_local_database_names_a_public_address() -> None:
    """The installed MMDB returns a country for a public address."""
    from lupaxa.chat_server.country import open_country_database

    reader = open_country_database(DATABASE)
    assert reader is not None
    try:
        name, code = country_from_record(reader.get("8.8.8.8"))
        v6_name, v6_code = country_from_record(reader.get("2001:4860:4860::8888"))
        mapped_name, mapped_code = country_from_record(reader.get("::ffff:8.8.8.8"))
    finally:
        reader.close()
    assert name != UNKNOWN_COUNTRY_NAME
    assert len(code) == 2
    assert (v6_name, v6_code) == (name, code)
    assert (mapped_name, mapped_code) == (name, code)
