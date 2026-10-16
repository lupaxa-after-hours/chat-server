"""Saved players, one bcrypt file each."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
import yaml

from .defaults import DEFAULT_PASSWORD_ROUNDS
from .exceptions import ConfigError
from .mfa_seal import legacy_mfa_secret
from .player import Player
from .ranks import SAVED_RANKS, Rank, saved_rank

PLAYER_KEYS = frozenset({"address", "colour", "connected_seconds", "country_code", "country_name", "description", "entermsg", "home", "home_login", "hostname", "ip_address", "logged_out_at", "loginmsg", "logoutmsg", "lowercase_name", "mfa_on", "mfa_secret", "name", "password", "plan", "prompt", "rank", "roles",})  # fmt: skip  # noqa: E501
SAVABLE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{2,19}")
MAX_PASSWORD_BYTES = 72


@dataclass(frozen=True)
class SavedPlayer:
    """One player file: spelling, rank, bcrypt hash, and last logout."""

    name: str
    rank: Rank
    password_hash: str
    logged_out_at: datetime | None = None
    ip_address: str | None = None
    hostname: str = ""
    connected_seconds: int = 0
    prompt: str | None = None
    lowercase_name: str = ""
    colour: bool = True
    description: str = ""
    mfa_secret: str | None = None
    mfa_on: bool = False
    entermsg: str | None = None
    loginmsg: str | None = None
    logoutmsg: str | None = None
    plan: str = ""
    country_name: str = ""
    country_code: str = ""
    home: bool = False
    home_login: bool = False
    roles: int = 0
    unknown_rank: str | None = None

    def __post_init__(self) -> None:
        """Fill the key from the display spelling when the caller did not pass one."""
        if self.lowercase_name == "":
            object.__setattr__(self, "lowercase_name", self.name.lower())
        if self.hostname == "" and self.ip_address:
            object.__setattr__(self, "hostname", self.ip_address)


def has_set_mfa(saved: SavedPlayer | None) -> bool:
    """Return whether ``saved`` has an MFA secret."""
    return saved is not None and saved.mfa_secret is not None


def has_saved_rank(saved: SavedPlayer | None, rank: Rank, *ranks: Rank) -> SavedPlayer | None:
    """Return ``saved`` when it exists and its rank is one of the given ranks."""
    if saved is not None and saved.rank in (rank, *ranks):
        return saved
    return None


def savable_name(name: str) -> bool:
    """Return whether ``name`` can be stored as a player file.

    Surrounding space is left as given. A savable name is 3 to 20 ASCII letters
    or digits and cannot start with a digit. It does not contain ``..``.
    """
    if name == "" or ".." in name:
        return False
    return SAVABLE_NAME.fullmatch(name) is not None


def password_problem(password: str) -> str | None:
    """Return the sentence for a bad ``password``, or ``None`` when it is usable."""
    if password == "":
        return "Password cannot be empty.\n"
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return "Password is too long.\n"
    return None


def hash_password(password: str, *, rounds: int = DEFAULT_PASSWORD_ROUNDS) -> str:
    """Return a bcrypt hash of ``password``."""
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds))
    return hashed.decode("ascii")


def password_ok(password: str, password_hash: str) -> bool:
    """Return whether ``password`` matches ``password_hash``."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def player_path(directory: Path, name: str) -> Path:
    """Return the YAML path for ``name`` inside ``directory``."""
    return directory / f"{name.lower()}.yml"


def load_saved(path: Path) -> SavedPlayer | None:
    """Return the player stored at ``path``, or ``None`` when that file is missing.

    Raises
    ------
    ConfigError
        The file exists and is not a valid player file. The message names ``path``.
    """
    if not path.exists():
        return None
    try:
        raw_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Player file {path} is not UTF-8.") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read player file {path}: {exc.strerror}") from exc
    try:
        payload = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"Player file {path} must be a YAML mapping.")
    extra = sorted(str(key) for key in payload if key not in PLAYER_KEYS)
    if extra:
        raise ConfigError(f"Unknown player key in {path}: {', '.join(extra)}")
    name = player_name(path, payload.get("name"))
    lowercase_name = player_lowercase_name(path, name, payload.get("lowercase_name"))
    rank, unknown_rank = player_rank(path, payload.get("rank"))
    password_hash = player_hash(path, payload.get("password"))
    logged_out_at = player_logged_out_at(path, payload.get("logged_out_at"))
    ip_address = player_address(path, payload.get("ip_address"))
    if ip_address is None:
        ip_address = player_address(path, payload.get("address"))
    hostname = player_address(path, payload.get("hostname")) or ""
    connected_seconds = player_connected_seconds(path, payload.get("connected_seconds"))
    prompt = player_prompt(path, payload.get("prompt"))
    colour = player_colour(path, payload.get("colour"))
    description = player_description(path, payload.get("description"))
    plan = player_plan(path, payload.get("plan"))
    mfa_secret = player_mfa_secret(path, payload.get("mfa_secret"))
    mfa_on = player_mfa_on(path, payload.get("mfa_on"))
    entermsg = player_sentence(path, "entermsg", payload.get("entermsg"))
    loginmsg = player_sentence(path, "loginmsg", payload.get("loginmsg"))
    logoutmsg = player_sentence(path, "logoutmsg", payload.get("logoutmsg"))
    country_name = player_country_name(path, payload.get("country_name"))
    country_code = player_country_code(path, payload.get("country_code"))
    for bool_key in ("home", "home_login"):
        line = re.search(rf"^{bool_key}:\s*(.+)$", raw_text, re.MULTILINE)
        if line is not None and re.fullmatch(r"yes|no|on|off", line.group(1).strip(), re.IGNORECASE):
            raise ConfigError(f"Player file {path} {bool_key} must be true or false.")
    home = player_bool(path, "home", payload.get("home"))
    home_login = player_bool(path, "home_login", payload.get("home_login"))
    roles = player_roles(path, payload.get("roles"))
    return SavedPlayer(name, rank, password_hash, logged_out_at, ip_address, hostname, connected_seconds, prompt, lowercase_name, colour, description, mfa_secret, mfa_on, entermsg, loginmsg, logoutmsg, plan, country_name, country_code, home, home_login, roles=roles, unknown_rank=unknown_rank)  # fmt: skip  # noqa: E501


def save_player(directory: Path, player: SavedPlayer) -> None:
    """Write ``player`` as YAML in ``directory``.

    A temporary file in ``directory`` is renamed into place.

    Raises
    ------
    ConfigError
        ``player.name`` cannot be saved, or the rank is not a saved rank.
    """
    if not savable_name(player.name):
        raise ConfigError(f"{player.name} cannot be saved.")
    if player.unknown_rank is not None:
        stored_rank = player.unknown_rank
    elif player.rank not in SAVED_RANKS:
        raise ConfigError(f"Player file for {player.name} rank must be resident, su_channel, super_user, admin, coder, or super_admin.")
    else:
        stored_rank = player.rank.value
    if player.lowercase_name != player.name.lower():
        raise ConfigError(f"{player.name} cannot be saved.")
    path = player_path(directory, player.lowercase_name)
    payload: dict[str, str | int | bool] = {
        "name": player.name,
        "lowercase_name": player.lowercase_name,
        "rank": stored_rank,
        "password": player.password_hash,
    }
    if player.logged_out_at is not None:
        payload["logged_out_at"] = player.logged_out_at.isoformat()
    if player.ip_address is not None:
        payload["ip_address"] = player.ip_address
    if player.hostname != "":
        payload["hostname"] = player.hostname
    if player.connected_seconds != 0:
        payload["connected_seconds"] = player.connected_seconds
    if player.prompt is not None:
        payload["prompt"] = player.prompt
    if player.colour is False:
        payload["colour"] = False
    if player.description != "":
        payload["description"] = player.description
    if player.plan != "":
        payload["plan"] = player.plan
    if player.mfa_secret is not None:
        payload["mfa_secret"] = player.mfa_secret
    if player.mfa_on:
        payload["mfa_on"] = True
    if player.entermsg is not None:
        payload["entermsg"] = player.entermsg
    if player.loginmsg is not None:
        payload["loginmsg"] = player.loginmsg
    if player.logoutmsg is not None:
        payload["logoutmsg"] = player.logoutmsg
    if player.country_name != "":
        payload["country_name"] = player.country_name
    if player.country_code != "":
        payload["country_code"] = player.country_code
    if player.home:
        payload["home"] = True
    if player.home_login:
        payload["home_login"] = True
    if player.roles != 0:
        payload["roles"] = player.roles
    replace_file(path, yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def record_saved_logout(directory: Path, player: Player) -> None:
    """Write the logout time and the total seconds for a saved player."""
    if player.rank is Rank.NEWBIE:
        return
    try:
        saved = load_saved(player_path(directory, player.lowercase_name))
        if saved is None:
            return
        stamped = replace(saved, logged_out_at=datetime.now(timezone.utc), connected_seconds=player.connected_seconds)
        save_player(directory, stamped)
    except (ConfigError, OSError) as exc:
        print(exc)


def delete_player(directory: Path, name: str) -> bool:
    """Delete the file for ``name`` and return whether it was there."""
    if not savable_name(name):
        return False
    path = player_path(directory, name)
    if not path.is_file():
        return False
    path.unlink()
    return True


def ensure_players_directory(directory: Path) -> None:
    """Create ``directory`` when it is missing.

    Raises
    ------
    ConfigError
        ``directory`` exists and is not a directory.
    """
    if directory.exists() and not directory.is_dir():
        raise ConfigError("players must be a directory.")
    directory.mkdir(parents=True, exist_ok=True)


def super_admins(directory: Path) -> tuple[SavedPlayer, ...]:
    """Return saved super admins, sorted by name."""
    found: list[SavedPlayer] = []
    for path in directory.glob("*.yml"):
        saved = load_saved(path)
        if saved is not None and saved.rank == Rank.SUPER_ADMIN:
            found.append(saved)
    return tuple(sorted(found, key=lambda player: player.name.casefold()))


def player_lowercase_name(path: Path, name: str, raw: object) -> str:
    """Return the key, or the folded display spelling when the file has none."""
    if raw is None:
        return name.lower()
    if not isinstance(raw, str) or raw == "" or raw != name.lower():
        raise ConfigError(f"Player file {path} lowercase_name must match the name.")
    return raw


def player_name(path: Path, value: object) -> str:
    """Return the saved spelling, or reject a name that does not match the file."""
    if not isinstance(value, str) or not savable_name(value):
        raise ConfigError(f"Player file {path} name must be savable.")
    if path.name != f"{value.lower()}.yml":
        raise ConfigError(f"Player file {path} name does not match the file.")
    return value


def player_rank(path: Path, value: object) -> tuple[Rank, str | None]:
    """Return a saved rank, or resident plus an unknown word."""
    if not isinstance(value, str):
        raise ConfigError(f"Player file {path} rank must be resident, su_channel, super_user, admin, coder, or super_admin.")
    return saved_rank(value)


def player_hash(path: Path, value: object) -> str:
    """Return the stored password hash."""
    if not isinstance(value, str) or value == "":
        raise ConfigError(f"Player file {path} password must be a hash.")
    return value


def player_country_name(path: Path, value: object) -> str:
    """Return a country name, or an empty string when the file omits it."""
    if value is None:
        return ""
    if not isinstance(value, str) or value == "" or "\n" in value or len(value) > 80:
        raise ConfigError(f"Player file {path} country_name must be one line of at most 80 characters.")
    return value


def player_country_code(path: Path, value: object) -> str:
    """Return a two-letter country code, or an empty string when the file omits it."""
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) != 2 or not value.isalpha() or value != value.upper():
        raise ConfigError(f"Player file {path} country_code must be two uppercase letters.")
    return value


def player_address(path: Path, value: object) -> str | None:
    """Return an address string, or None when the file omits it."""
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise ConfigError(f"Player file {path} address must be a non-empty string.")
    return value


def player_prompt(path: Path, value: object) -> str | None:
    """Return a saved prompt, or None when the file omits it."""
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise ConfigError(f"Player file {path} prompt must be a non-empty string.")
    return value


def player_mfa_secret(path: Path, value: object) -> str | None:
    """Return a saved MFA secret, or None when the file omits it."""
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise ConfigError(f"Player file {path} mfa_secret must be a sealed secret or a base32 secret.")
    if value.startswith("enc1$") or legacy_mfa_secret(value):
        return value
    raise ConfigError(f"Player file {path} mfa_secret must be a sealed secret or a base32 secret.")


def player_mfa_on(path: Path, value: object) -> bool:
    """Return whether this file still shows that MFA was on."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise ConfigError(f"Player file {path} mfa_on must be true or false.")


def player_bool(path: Path, key: str, value: object) -> bool:
    """Return a saved flag. A missing key is false."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise ConfigError(f"Player file {path} {key} must be true or false.")


def player_sentence(path: Path, key: str, value: object) -> str | None:
    """Return a custom room sentence, or None for the default."""
    if value is None:
        return None
    if not isinstance(value, str) or value == "" or "\n" in value or len(value) > 80:
        raise ConfigError(f"Player file {path} {key} must be one line of at most 80 characters.")
    return value


def player_plan(path: Path, value: object) -> str:
    """Return a saved plan, or an empty string when the file omits it."""
    if value is None:
        return ""
    if not isinstance(value, str) or value == "" or "\n" in value or len(value) > 80:
        raise ConfigError(f"Player file {path} plan must be one line of at most 80 characters.")
    return value


def player_description(path: Path, value: object) -> str:
    """Return a saved description, or an empty string when the file omits it."""
    if value is None:
        return ""
    if not isinstance(value, str) or value == "" or "\n" in value or len(value) > 80:
        raise ConfigError(f"Player file {path} description must be one line of at most 80 characters.")
    return value


def player_colour(path: Path, value: object) -> bool:
    """Return the saved colour. A missing key means on."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    raise ConfigError(f"Player file {path} colour must be true or false.")


def player_connected_seconds(path: Path, value: object) -> int:
    """Return the saved connected seconds, or zero when the file omits them."""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"Player file {path} connected_seconds must be a whole number of seconds.")
    return value


def player_roles(path: Path, value: object) -> int:
    """Return ``roles`` when it is a non-negative integer, or 0 when it is omitted."""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"Player file {path} roles must be a whole number.")
    return value


def player_logged_out_at(path: Path, value: object) -> datetime | None:
    """Return the saved logout time, or None when the file omits it."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"Player file {path} logged_out_at must be a time.")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"Player file {path} logged_out_at must be a time.") from exc


def replace_file(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` by renaming a temporary file beside it."""
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
