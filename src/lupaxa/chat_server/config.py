"""Load the YAML config file."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from .defaults import DEFAULT_HOST, DEFAULT_LOGIN_TIMEOUT
from .exceptions import ConfigError

HOT_KEYS = frozenset({"allow_newbies", "allow_residents", "country_database", "country_lookup", "login_timeout", "max_connections", "max_newbies", "mfa_enabled", "newbie_start", "start"})
COLD_KEYS = frozenset({"background", "help", "host", "messages_path", "players", "port", "rooms", "ssh_port", "stack"})
WRAPPER_KEYS = frozenset({"hot", "cold"})
CONFIG_KEYS = HOT_KEYS | COLD_KEYS
ROOM_ID = re.compile(r"^[a-z0-9_]+$")
PERCENT = re.compile(r"^(100|[1-9][0-9]?)%$")


@dataclass(frozen=True)
class HotConfig:
    """The values reload config is allowed to replace."""

    start: str
    login_timeout: int
    allow_newbies: bool
    allow_residents: bool
    mfa_enabled: bool
    country_lookup: bool
    country_database: Path | None
    newbie_start: str
    max_connections: int
    max_newbies: int
    obsolete_keys: tuple[str, ...]


@dataclass(frozen=True)
class ColdConfig:
    """The values read only at process start."""

    port: int | None
    ssh_port: int | None
    rooms: Path
    help: Path
    players: Path
    host: str
    background: bool
    stack: str
    messages_path: Path


@dataclass(frozen=True)
class Config:
    """Settings the server reads at startup."""

    port: int | None
    rooms: Path
    start: str
    help: Path
    players: Path
    host: str = DEFAULT_HOST
    login_timeout: int = DEFAULT_LOGIN_TIMEOUT
    ssh_port: int | None = None
    ssh_host_key: Path = Path("ssh_host_key")
    background: bool = False
    allow_newbies: bool = True
    allow_residents: bool = True
    mfa_enabled: bool = True
    country_lookup: bool = False
    country_database: Path | None = None
    stack: str = "ipv4"
    newbie_start: str = ""
    max_connections: int = 128
    max_newbies: int = 20
    config_dir: Path = Path(".")
    messages_path: Path = Path(".")
    hot_path: Path = Path("hot.yml")
    cold_path: Path = Path("cold.yml")
    obsolete_hot: tuple[str, ...] = ()
    obsolete_cold: tuple[str, ...] = ()


def load_banner(messages_path: Path) -> str:
    """Return banner.msg from the messages directory."""
    path = messages_path / "banner.msg"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read banner file {path}: {exc.strerror}.") from exc
    except UnicodeDecodeError as exc:
        message = f"Banner file {path} is not valid UTF-8: {exc.reason}."
        raise ConfigError(message) from exc


def load_gate_file(messages_path: Path, filename: str, missing_name: str, utf8_name: str) -> str:
    """Return one UTF-8 file from the messages directory."""
    path = messages_path / filename
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read {missing_name} file {path}: {exc.strerror}.") from exc
    except UnicodeDecodeError as exc:
        message = f"{utf8_name} file {path} is not valid UTF-8: {exc.reason}."
        raise ConfigError(message) from exc


def read_mapping(path: Path) -> dict[str, object]:
    """Return the YAML mapping at path."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc.strerror}") from exc
    try:
        payload = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"Config file {path} must be a YAML mapping.")
    return payload


def named_file(path: Path, value: object, key: str) -> Path:
    """Return a path named by the wrapper, relative to the wrapper's directory."""
    if not isinstance(value, str) or value == "":
        raise ConfigError(f"{key} must be a path.")
    found = Path(value)
    if not found.is_absolute():
        found = path.parent / found
    return found


def wrapper_paths(path: Path, payload: dict[str, object]) -> tuple[Path, Path]:
    """Return the hot and cold paths. Any other wrapper key is an error."""
    if "hot" not in payload:
        raise ConfigError("Config is missing hot.")
    if "cold" not in payload:
        raise ConfigError("Config is missing cold.")
    extras = [str(key) for key in payload if str(key) not in WRAPPER_KEYS]
    if extras:
        name = sorted(extras, key=str.casefold)[0]
        if name in COLD_KEYS:
            raise ConfigError(f"{name} belongs in the cold config.")
        if name in HOT_KEYS:
            raise ConfigError(f"{name} belongs in the hot config.")
        raise ConfigError(f"{name} does not belong in config.yml.")
    return named_file(path, payload["hot"], "hot"), named_file(path, payload["cold"], "cold")


def side_payload(path: Path, allowed: frozenset[str], other: str) -> tuple[dict[str, object], tuple[str, ...]]:
    """Return the allowed keys and the obsolete names. A key for the other file is an error."""
    payload = read_mapping(path)
    kept: dict[str, object] = {}
    obsolete: list[str] = []
    for key, value in payload.items():
        name = str(key)
        if name in allowed:
            kept[name] = value
            continue
        if name in CONFIG_KEYS:
            raise ConfigError(f"{name} belongs in the {other} config.")
        obsolete.append(name)
    return kept, tuple(sorted(obsolete, key=str.casefold))


def connection_cap(payload: dict[str, object]) -> int:
    """Return max_connections when it is a whole number of at least 1."""
    if "max_connections" not in payload:
        raise ConfigError("Config is missing max_connections.")
    value = payload["max_connections"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("max_connections must be an integer.")
    if value < 1:
        raise ConfigError("max_connections must be at least 1.")
    return value


def newbie_cap(value: object, connections: int) -> int:
    """Return the newbie cap from a whole number or a percentage of connections."""
    if isinstance(value, bool):
        raise ConfigError("max_newbies must be an integer or a percentage.")
    if isinstance(value, int):
        if value < 1:
            raise ConfigError("max_newbies must be at least 1.")
        if value > connections:
            raise ConfigError("max_newbies must be at most max_connections.")
        return value
    if isinstance(value, str):
        matched = PERCENT.fullmatch(value)
        if matched is not None:
            percent = int(value[:-1])
            count = (connections * percent + 99) // 100
            if count < 1:
                raise ConfigError("max_newbies must be at least 1.")
            return count
    raise ConfigError("max_newbies must be an integer or a percentage.")


def hot_fields(payload: dict[str, object], base: Path) -> HotConfig:
    """Return hot settings from payload. Relative paths join to base."""
    if "start" not in payload:
        raise ConfigError("Config is missing start.")
    start = payload["start"]
    if not isinstance(start, str) or ROOM_ID.fullmatch(start) is None:
        raise ConfigError("start must be a room id of lowercase letters, digits, and underscores.")
    raw_newbie = payload.get("newbie_start", start)
    if not isinstance(raw_newbie, str) or ROOM_ID.fullmatch(raw_newbie) is None:
        raise ConfigError("newbie_start must be a room id of lowercase letters, digits, and underscores.")
    newbie_start = raw_newbie
    login_timeout = payload.get("login_timeout", DEFAULT_LOGIN_TIMEOUT)
    if isinstance(login_timeout, bool) or not isinstance(login_timeout, int):
        raise ConfigError("login_timeout must be an integer.")
    if login_timeout < 1:
        raise ConfigError("login_timeout must be at least 1.")
    allow_newbies = config_bool(payload, "allow_newbies", True)
    allow_residents = config_bool(payload, "allow_residents", True)
    mfa_enabled = config_bool(payload, "mfa_enabled", True)
    country_lookup, country_database = country_database_path(payload, base)
    max_connections = connection_cap(payload)
    if "max_newbies" not in payload:
        raise ConfigError("Config is missing max_newbies.")
    max_newbies = newbie_cap(payload["max_newbies"], max_connections)
    return HotConfig(
        start=start,
        login_timeout=login_timeout,
        allow_newbies=allow_newbies,
        allow_residents=allow_residents,
        mfa_enabled=mfa_enabled,
        country_lookup=country_lookup,
        country_database=country_database,
        newbie_start=newbie_start,
        max_connections=max_connections,
        max_newbies=max_newbies,
        obsolete_keys=(),
    )


def cold_fields(payload: dict[str, object], base: Path) -> ColdConfig:
    """Return cold settings from payload. Relative paths join to base."""
    port = listen_port(payload, "port")
    ssh_port = listen_port(payload, "ssh_port")
    if port is None and ssh_port is None:
        raise ConfigError("Config needs a port or an ssh_port.")
    if port is not None and ssh_port is not None and port == ssh_port:
        raise ConfigError("port and ssh_port must be different.")
    if "rooms" not in payload:
        raise ConfigError("Config is missing rooms.")
    rooms_value = payload["rooms"]
    if not isinstance(rooms_value, str):
        raise ConfigError("rooms must be a string.")
    rooms_path = Path(rooms_value)
    if not rooms_path.is_absolute():
        rooms_path = base / rooms_path
    if "help" not in payload:
        raise ConfigError("Config is missing help.")
    help_value = payload["help"]
    if not isinstance(help_value, str):
        raise ConfigError("help must be a string.")
    help_path = Path(help_value)
    if not help_path.is_absolute():
        help_path = base / help_path
    players_value = payload.get("players", "players")
    if not isinstance(players_value, str):
        raise ConfigError("players must be a string.")
    players_path = Path(players_value)
    if not players_path.is_absolute():
        players_path = base / players_path
    host = host_address(payload.get("host", DEFAULT_HOST))
    background = config_bool(payload, "background", False)
    stack = stack_name(payload)
    listen_hosts(host, stack)
    if "messages_path" not in payload:
        raise ConfigError("Config is missing messages_path.")
    messages_value = payload["messages_path"]
    if not isinstance(messages_value, str):
        raise ConfigError("messages_path must be a string.")
    messages_path = Path(messages_value)
    if not messages_path.is_absolute():
        messages_path = base / messages_path
    return ColdConfig(
        port=port,
        ssh_port=ssh_port,
        rooms=rooms_path.resolve(),
        help=help_path.resolve(),
        players=players_path.resolve(),
        host=host,
        background=background,
        stack=stack,
        messages_path=messages_path.resolve(),
    )


def load_hot_file(path: Path) -> HotConfig:
    """Return the hot values from path."""
    payload, obsolete = side_payload(path, HOT_KEYS, "cold")
    hot = hot_fields(payload, path.parent)
    return replace(hot, obsolete_keys=obsolete)


def load_config(path: Path) -> Config:
    """Return a config loaded from the wrapper at path.

    Parameters
    ----------
    path :
        UTF-8 YAML wrapper with ``hot`` and ``cold`` paths read from this
        file's directory. Each side file holds its own keys. A relative path
        inside ``hot.yml`` is read from that file's directory. A relative path
        inside ``cold.yml`` is read from that file's directory. ``port`` may be
        omitted when ``ssh_port`` is set. ``host`` defaults to ``0.0.0.0`` when
        it is omitted. ``players`` defaults to a ``players`` directory beside
        the cold file. ``login_timeout`` defaults to 3 and is a whole number of
        minutes, at least 1. ``background`` defaults to false. ``allow_newbies``
        and ``allow_residents`` default to true. ``mfa_enabled`` defaults to
        true. ``country_lookup`` defaults to false. When it is true,
        ``country_database`` is required and must be an ``.mmdb`` path,
        relative to the hot file or absolute. ``stack`` is ``ipv4``, ``ipv6``,
        or ``dual`` and defaults to ``ipv4``. Each flag is written ``true`` or
        ``false``, the same way as ``background``. ``max_connections`` is a
        whole number of at least 1. ``max_newbies`` is a whole number from 1
        to ``max_connections`` or a percentage from 1 to 100 that rounds up.
        ``messages_path`` is required. An omitted ``newbie_start`` copies
        ``start``.

    Raises
    ------
    ConfigError
        The file cannot be read, is not a mapping, or has a bad value.
    """
    hot_path, cold_path = wrapper_paths(path, read_mapping(path))
    hot = load_hot_file(hot_path)
    cold_payload, obsolete_cold = side_payload(cold_path, COLD_KEYS, "hot")
    cold = cold_fields(cold_payload, cold_path.parent)
    return Config(
        port=cold.port,
        rooms=cold.rooms,
        start=hot.start,
        help=cold.help,
        players=cold.players,
        host=cold.host,
        login_timeout=hot.login_timeout,
        ssh_port=cold.ssh_port,
        ssh_host_key=(path.parent / "ssh_host_key").resolve(),
        background=cold.background,
        allow_newbies=hot.allow_newbies,
        allow_residents=hot.allow_residents,
        mfa_enabled=hot.mfa_enabled,
        country_lookup=hot.country_lookup,
        country_database=hot.country_database,
        stack=cold.stack,
        newbie_start=hot.newbie_start,
        max_connections=hot.max_connections,
        max_newbies=hot.max_newbies,
        config_dir=path.parent.resolve(),
        messages_path=cold.messages_path,
        hot_path=hot_path.resolve(),
        cold_path=cold_path.resolve(),
        obsolete_hot=hot.obsolete_keys,
        obsolete_cold=obsolete_cold,
    )


def config_bool(payload: dict[str, object], key: str, default: bool) -> bool:
    """Return ``key`` when it is a boolean, or ``default`` when it is omitted."""
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false.")
    return value


STACKS = frozenset({"ipv4", "ipv6", "dual"})


def stack_name(payload: dict[str, object]) -> str:
    """Return ``stack`` when it is ipv4, ipv6, or dual."""
    value = payload.get("stack", "ipv4")
    if not isinstance(value, str) or value not in STACKS:
        raise ConfigError("stack must be ipv4, ipv6, or dual.")
    return value


def listen_hosts(host: str, stack: str) -> list[str]:
    """Return the addresses ``stack`` should bind for ``host``.

    ``ipv4`` binds ``host``. ``ipv6`` binds ``host`` when it is IPv6, or ``::``
    when ``host`` is ``0.0.0.0``. ``dual`` binds an IPv4 address and an IPv6
    address. A specific address of one family is kept, and the other family
    listens on every interface.
    """
    address = ipaddress.ip_address(host)
    if stack == "ipv4":
        if address.version != 4:
            raise ConfigError("host must be an IPv4 address when stack is ipv4.")
        return [str(address)]
    if stack == "ipv6":
        if address.version == 6:
            return [str(address)]
        if address == ipaddress.ip_address("0.0.0.0"):  # noqa: S104
            return ["::"]
        raise ConfigError("host must be an IPv6 address when stack is ipv6.")
    if address.version == 4:
        return [str(address), "::"]
    return ["0.0.0.0", str(address)]  # noqa: S104


def country_database_path(payload: dict[str, object], config_path: Path) -> tuple[bool, Path | None]:
    """Return the lookup flag and the resolved MMDB path."""
    enabled = config_bool(payload, "country_lookup", False)
    if "country_database" not in payload:
        if enabled:
            raise ConfigError("Config is missing country_database.")
        return False, None
    value = payload["country_database"]
    if not isinstance(value, str) or value == "":
        raise ConfigError("country_database must be a path to an mmdb file.")
    database = Path(value)
    if database.suffix.lower() != ".mmdb":
        raise ConfigError("country_database must be an mmdb file.")
    if not database.is_absolute():
        database = config_path / database
    return enabled, database.resolve()


def listen_port(payload: dict[str, object], key: str) -> int | None:
    """Return ``key`` when it is a port, or None when it is omitted."""
    if key not in payload:
        return None
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer.")
    if value < 1 or value > 65535:
        raise ConfigError(f"{key} must be from 1 to 65535.")
    return value


def host_address(value: object) -> str:
    """Return ``value`` when it is an IP address."""
    if not isinstance(value, str):
        raise ConfigError("host must be an IP address.")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ConfigError("host must be an IP address.") from exc
