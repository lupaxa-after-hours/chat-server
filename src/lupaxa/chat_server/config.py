"""Load the YAML config file."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .defaults import DEFAULT_HOST, DEFAULT_LOGIN_TIMEOUT
from .exceptions import ConfigError
from .saved_player import savable_name

_CONFIG_KEYS = frozenset(
    {
        "admin",
        "allow_newbies",
        "allow_residents",
        "background",
        "help",
        "host",
        "login_timeout",
        "players",
        "port",
        "rooms",
        "ssh_port",
        "start",
    }
)
_ROOM_ID = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class Config:
    """Settings the server reads at startup."""

    port: int | None
    rooms: Path
    start: str
    help: Path
    players: Path
    admin: str = "admin"
    host: str = DEFAULT_HOST
    login_timeout: int = DEFAULT_LOGIN_TIMEOUT
    ssh_port: int | None = None
    ssh_host_key: Path = Path("ssh_host_key")
    background: bool = False
    allow_newbies: bool = True
    allow_residents: bool = True


def load_config(path: Path) -> Config:
    """Return a config loaded from ``path``.

    Parameters
    ----------
    path :
        UTF-8 YAML file. The allowed keys are ``admin``, ``allow_newbies``,
        ``allow_residents``, ``background``, ``help``, ``host``,
        ``login_timeout``, ``players``, ``port``, ``rooms``, ``ssh_port``, and
        ``start``. ``port`` may be omitted when ``ssh_port`` is set. ``host``
        defaults to ``0.0.0.0`` when it is omitted. ``players`` defaults to a
        ``players`` directory beside the file. ``admin`` defaults to ``admin``.
        ``login_timeout`` defaults to 3 and is a whole number of minutes, at
        least 1. ``background`` defaults to false. ``allow_newbies`` and
        ``allow_residents`` default to true. Each is written ``true`` or
        ``false``, the same way as ``background``.

    Raises
    ------
    ConfigError
        The file cannot be read, is not a mapping, or has a bad value.
    """
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
    extra = sorted(str(key) for key in payload if key not in _CONFIG_KEYS)
    if extra:
        raise ConfigError(f"Unknown config key: {', '.join(extra)}")
    port = _listen_port(payload, "port")
    ssh_port = _listen_port(payload, "ssh_port")
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
        rooms_path = path.parent / rooms_path
    if "start" not in payload:
        raise ConfigError("Config is missing start.")
    start = payload["start"]
    if not isinstance(start, str) or _ROOM_ID.fullmatch(start) is None:
        raise ConfigError("start must be a room id of lowercase letters, digits, and underscores.")
    if "help" not in payload:
        raise ConfigError("Config is missing help.")
    help_value = payload["help"]
    if not isinstance(help_value, str):
        raise ConfigError("help must be a string.")
    help_path = Path(help_value)
    if not help_path.is_absolute():
        help_path = path.parent / help_path
    players_value = payload.get("players", "players")
    if not isinstance(players_value, str):
        raise ConfigError("players must be a string.")
    players_path = Path(players_value)
    if not players_path.is_absolute():
        players_path = path.parent / players_path
    admin = payload.get("admin", "admin")
    if not isinstance(admin, str):
        raise ConfigError("admin must be a string.")
    if not savable_name(admin):
        raise ConfigError("admin must be a savable name.")
    host = _host_address(payload.get("host", DEFAULT_HOST))
    login_timeout = payload.get("login_timeout", DEFAULT_LOGIN_TIMEOUT)
    if isinstance(login_timeout, bool) or not isinstance(login_timeout, int):
        raise ConfigError("login_timeout must be an integer.")
    if login_timeout < 1:
        raise ConfigError("login_timeout must be at least 1.")
    background = _config_bool(payload, "background", False)
    allow_newbies = _config_bool(payload, "allow_newbies", True)
    allow_residents = _config_bool(payload, "allow_residents", True)
    return Config(
        port=port,
        rooms=rooms_path.resolve(),
        start=start,
        help=help_path.resolve(),
        players=players_path.resolve(),
        admin=admin,
        host=host,
        login_timeout=login_timeout,
        ssh_port=ssh_port,
        ssh_host_key=(path.parent / "ssh_host_key").resolve(),
        background=background,
        allow_newbies=allow_newbies,
        allow_residents=allow_residents,
    )


def _config_bool(payload: dict[str, object], key: str, default: bool) -> bool:
    """Return ``key`` when it is a boolean, or ``default`` when it is omitted."""
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false.")
    return value


def _listen_port(payload: dict[str, object], key: str) -> int | None:
    """Return ``key`` when it is a port, or None when it is omitted."""
    if key not in payload:
        return None
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer.")
    if value < 1 or value > 65535:
        raise ConfigError(f"{key} must be from 1 to 65535.")
    return value


def _host_address(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigError("host must be an IP address.")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ConfigError("host must be an IP address.") from exc
