"""Country names for a connection, from an MMDB file or a private address."""

from __future__ import annotations

import ipaddress
from pathlib import Path

import maxminddb

LOCAL_COUNTRY_NAME = "Localhost"
LOCAL_COUNTRY_CODE = "XL"
PRIVATE_COUNTRY_NAME = "Private Network"
PRIVATE_COUNTRY_CODE = "XP"
UNKNOWN_COUNTRY_NAME = "Unknown"
UNKNOWN_COUNTRY_CODE = "XU"
RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
ULA = ipaddress.ip_network("fc00::/7")


def database_readable(path: Path) -> bool:
    """Return whether ``path`` can be opened and has at least one byte."""
    try:
        with path.open("rb") as handle:
            return bool(handle.read(1))
    except OSError:
        return False


def close_country_database(reader: object | None) -> None:
    """Close an open country database."""
    if reader is None:
        return
    close = getattr(reader, "close", None)
    if close is not None:
        close()


def normalised_address(ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return ``ip``, unwrapping an IPv4-mapped IPv6 address to IPv4."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return mapped
    return address


def is_rfc1918(ip: str) -> bool:
    """Return whether ``ip`` is an IPv4 address in RFC 1918 space."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if not isinstance(address, ipaddress.IPv4Address):
        return False
    return any(address in network for network in RFC1918)


def is_local_address(ip: str) -> bool:
    """Return whether ``ip`` is an IPv4 or IPv6 loopback address."""
    address = normalised_address(ip)
    if address is None:
        return False
    return address.is_loopback


def is_private_address(ip: str) -> bool:
    """Return whether ``ip`` is RFC 1918 or an IPv6 unique-local address."""
    address = normalised_address(ip)
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in RFC1918)
    if isinstance(address, ipaddress.IPv6Address):
        return address in ULA
    return False


def address_text(ip: str) -> str:
    """Return the address string to look up, with a mapped IPv4 unwrapped."""
    address = normalised_address(ip)
    if address is None:
        return ip
    return str(address)


def open_country_database(path: Path) -> maxminddb.Reader | None:
    """Return a reader for ``path``, or None when the file is not an MMDB."""
    try:
        return maxminddb.open_database(str(path))
    except (OSError, ValueError, maxminddb.InvalidDatabaseError):
        return None


def country_from_record(record: object) -> tuple[str, str]:
    """Return a country name and code from an MMDB record, or Unknown."""
    found = record_country(record)
    if found is None:
        return UNKNOWN_COUNTRY_NAME, UNKNOWN_COUNTRY_CODE
    name, code = found
    cleaned = " ".join(name.split())
    letters = code.strip().upper()
    if cleaned == "" or len(letters) != 2 or not letters.isalpha():
        return UNKNOWN_COUNTRY_NAME, UNKNOWN_COUNTRY_CODE
    if len(cleaned) > 80:
        cleaned = cleaned[:80]
    return cleaned, letters


def record_country(record: object) -> tuple[str, str] | None:
    """Return a name and code from an IPinfo or MaxMind country record."""
    if not isinstance(record, dict):
        return None
    name = record.get("country")
    code = record.get("country_code")
    if isinstance(name, str) and isinstance(code, str):
        return name, code
    if isinstance(name, dict):
        iso = name.get("iso_code")
        names = name.get("names")
        if isinstance(iso, str) and isinstance(names, dict):
            english = names.get("en")
            if isinstance(english, str):
                return english, iso
    return None


def read_ipinfo(reader: object, ip: str) -> tuple[object, int | None]:
    """Return the MMDB record and prefix length for ``ip``, or an empty result."""
    getter = getattr(reader, "get_with_prefix_len", None)
    if getter is None:
        return None, None
    try:
        found = getter(address_text(ip))
    except (OSError, ValueError, maxminddb.InvalidDatabaseError):
        return None, None
    if not isinstance(found, tuple) or len(found) != 2:
        return None, None
    record, prefix = found
    if isinstance(prefix, int) and not isinstance(prefix, bool):
        return record, prefix
    return record, None


def ipinfo_fields(ip: str, hostname: str, record: object, prefix: int | None) -> list[tuple[str, str]]:
    """Return the examine rows for one address."""
    shown = address_text(ip)
    fields = [("IP address", shown), ("Hostname", hostname or shown), ("Country", country_text(ip, record))]
    if not is_local_address(ip) and not is_private_address(ip):
        fields.extend(database_fields(record))
    block = ip_block(ip, None if is_local_address(ip) or is_private_address(ip) else prefix)
    if block is not None:
        fields.append(("IP block", block))
    return fields


def country_text(ip: str, record: object) -> str:
    """Return the country name and code for an ipinfo row."""
    if is_local_address(ip):
        return f"{LOCAL_COUNTRY_NAME} ({LOCAL_COUNTRY_CODE})"
    if is_private_address(ip):
        return f"{PRIVATE_COUNTRY_NAME} ({PRIVATE_COUNTRY_CODE})"
    name, code = country_from_record(record)
    return f"{name} ({code})"


def ip_block(ip: str, prefix: int | None) -> str | None:
    """Return the network that contains ``ip``."""
    address = normalised_address(ip)
    if isinstance(address, ipaddress.IPv4Address) and address.is_loopback:
        return "127.0.0.0/8"
    if isinstance(address, ipaddress.IPv6Address) and address.is_loopback:
        return "::1/128"
    if isinstance(address, ipaddress.IPv4Address):
        for network in RFC1918:
            if address in network:
                return str(network)
    if isinstance(address, ipaddress.IPv6Address) and address in ULA:
        return str(ULA)
    if prefix is None or address is None:
        return None
    try:
        return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
    except ValueError:
        return None


def database_fields(record: object) -> list[tuple[str, str]]:
    """Return the extra MMDB rows, skipping the country already shown above."""
    if not isinstance(record, dict):
        return []
    rows: list[tuple[str, str]] = []
    continent = continent_text(record)
    if continent is not None:
        rows.append(("Continent", continent))
    asn = asn_text(record)
    if asn is not None:
        rows.append(("ASN", asn))
    as_name = named_text(record, "as_name", "autonomous_system_organization")
    if as_name is not None:
        rows.append(("AS name", as_name))
    domain = plain_text(record.get("as_domain"))
    if domain is not None:
        rows.append(("AS domain", domain))
    registered = place_text(record.get("registered_country"))
    if registered is not None:
        rows.append(("Registered country", registered))
    used = {"as_domain", "as_name", "asn", "continent", "continent_code", "country", "country_code", "registered_country", "traits"}
    for key in sorted(record):
        if not isinstance(key, str) or key in used:
            continue
        shown = plain_text(record[key])
        if shown is None:
            continue
        rows.append((field_label(key), shown))
    return rows


def continent_text(record: dict[str, object]) -> str | None:
    """Return a continent name and code from a flat or nested record."""
    continent = record.get("continent")
    if isinstance(continent, str):
        code = record.get("continent_code")
        letters = code.strip().upper() if isinstance(code, str) else ""
        return place_value(" ".join(continent.split()), letters if len(letters) == 2 and letters.isalpha() else "")
    return place_text(continent)


def asn_text(record: dict[str, object]) -> str | None:
    """Return the ASN, adding an AS prefix when the database stores only digits."""
    found = plain_text(record.get("asn"))
    if found is None:
        traits = record.get("traits")
        if isinstance(traits, dict):
            found = plain_text(traits.get("autonomous_system_number"))
    if found is None:
        return None
    if found.startswith("AS"):
        return found
    if found.isdigit():
        return f"AS{found}"
    return found


def named_text(record: dict[str, object], key: str, trait: str) -> str | None:
    """Return a flat string, or the same fact from a MaxMind traits dict."""
    found = plain_text(record.get(key))
    if found is not None:
        return found
    traits = record.get("traits")
    if isinstance(traits, dict):
        return plain_text(traits.get(trait))
    return None


def place_text(value: object) -> str | None:
    """Return an English place name and its code."""
    if not isinstance(value, dict):
        return None
    code = value.get("code") or value.get("iso_code")
    names = value.get("names")
    english = ""
    if isinstance(names, dict):
        found = names.get("en")
        if isinstance(found, str):
            english = " ".join(found.split())
    letters = code.strip().upper() if isinstance(code, str) else ""
    if len(letters) != 2 or not letters.isalpha():
        letters = ""
    return place_value(english, letters)


def place_value(name: str, code: str) -> str | None:
    """Return ``name (code)``, or whichever half is present."""
    if name and code:
        return f"{name} ({code})"
    if name:
        return name
    if code:
        return code
    return None


def plain_text(value: object) -> str | None:
    """Return a short display string, or None for a nested or empty value."""
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        return cleaned or None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    return None


def field_label(key: str) -> str:
    """Return a row label for an extra database key."""
    words = key.replace("_", " ")
    return words[:1].upper() + words[1:]
