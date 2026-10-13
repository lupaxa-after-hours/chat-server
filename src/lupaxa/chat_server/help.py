"""Command help loaded from YAML. This module does not touch sockets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .exceptions import ConfigError

_HELP_KEYS = frozenset({"name", "aliases", "text"})
_NO_IMPLICIT_SCALAR = frozenset(
    {"tag:yaml.org,2002:bool", "tag:yaml.org,2002:null"},
)


class _HelpYamlLoader(yaml.SafeLoader):
    """SafeLoader that keeps YAML 1.1 bool and null words as plain strings."""


_HelpYamlLoader.yaml_implicit_resolvers = {
    char: [(tag, pattern) for tag, pattern in patterns if tag not in _NO_IMPLICIT_SCALAR]
    for char, patterns in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_HelpYamlLoader.yaml_implicit_resolvers = {
    char: patterns for char, patterns in _HelpYamlLoader.yaml_implicit_resolvers.items() if patterns
}


@dataclass(frozen=True)
class HelpEntry:
    """One topic: its name, its aliases, and the paragraph the player sees."""

    name: str
    aliases: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class HelpBook:
    """Every help entry, in file order."""

    entries: tuple[HelpEntry, ...]

    def find(self, topic: str) -> HelpEntry | None:
        """Return the entry whose name or alias is ``topic``."""
        for entry in self.entries:
            if entry.name == topic or topic in entry.aliases:
                return entry
        return None

    def names(self) -> tuple[str, ...]:
        """Return the entry names in alphabetical order."""
        return tuple(sorted(entry.name for entry in self.entries))


def format_index(book: HelpBook) -> str:
    """Return the bare-help listing, ending with one newline."""
    listed = " ".join(book.names())
    header = f"Commands: {listed}\n" if listed else "Commands:\n"
    return header + "Type help <command> for more.\n"


def format_entry(entry: HelpEntry) -> str:
    """Return one topic, ending with one newline."""
    lines = [entry.name]
    if entry.aliases:
        lines.append("Aliases: " + " ".join(entry.aliases))
    lines.append(entry.text)
    return "\n".join(lines) + "\n"


def load_help(path: Path) -> HelpBook:
    """Return the help entries stored at ``path``.

    Raises
    ------
    ConfigError
        The file cannot be read or does not describe a valid help list.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read help file {path}: {exc.strerror}") from exc
    try:
        payload = yaml.load(raw_text, Loader=_HelpYamlLoader)  # noqa: S506
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise ConfigError(f"Help file {path} must be a YAML list.")
    entries = tuple(_entry(body) for body in payload)
    _reject_duplicates(entries)
    return HelpBook(entries=entries)


def _entry(body: object) -> HelpEntry:
    if not isinstance(body, dict):
        raise ConfigError("Help entry must be a mapping.")
    if "name" not in body:
        raise ConfigError("Help entry is missing name.")
    name = body["name"]
    if not isinstance(name, str) or name.strip() == "":
        raise ConfigError("Help entry name must be a non-empty string.")
    extra = sorted(str(key) for key in body if key not in _HELP_KEYS)
    if extra:
        raise ConfigError(f"Unknown help key on {name}: {', '.join(extra)}")
    if "aliases" not in body:
        raise ConfigError(f"Help entry {name} is missing aliases.")
    aliases = body["aliases"]
    if not isinstance(aliases, list) or any(not isinstance(item, str) for item in aliases):
        raise ConfigError(f"Help entry {name} aliases must be a list of strings.")
    if any(item == "" for item in aliases):
        raise ConfigError(f"Help entry {name} has an empty alias.")
    if "text" not in body:
        raise ConfigError(f"Help entry {name} is missing text.")
    text = body["text"]
    if not isinstance(text, str) or text.strip() == "":
        raise ConfigError(f"Help entry {name} text must be a non-empty string.")
    return HelpEntry(name=name, aliases=tuple(aliases), text=text.strip())


def _reject_duplicates(entries: tuple[HelpEntry, ...]) -> None:
    seen: set[str] = set()
    for entry in entries:
        if entry.name in seen:
            raise ConfigError(f"Duplicate help name: {entry.name}.")
        seen.add(entry.name)
        for alias in entry.aliases:
            if alias in seen:
                raise ConfigError(f"Help alias '{alias}' is already used.")
            seen.add(alias)
