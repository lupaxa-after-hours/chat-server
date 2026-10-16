"""Command help loaded from YAML. This module does not touch sockets."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from .colour import plain_len
from .config import Config
from .exceptions import ConfigError

HELP_KEYS = frozenset({"name", "aliases", "text", "example", "see_also", "notes"})
NO_IMPLICIT_SCALAR = frozenset({"tag:yaml.org,2002:bool", "tag:yaml.org,2002:null"})


class HelpYamlLoader(yaml.SafeLoader):
    """SafeLoader that keeps YAML 1.1 bool and null words as plain strings."""


HelpYamlLoader.yaml_implicit_resolvers = {char: [(tag, pattern) for tag, pattern in patterns if tag not in NO_IMPLICIT_SCALAR] for char, patterns in yaml.SafeLoader.yaml_implicit_resolvers.items()}
HelpYamlLoader.yaml_implicit_resolvers = {char: patterns for char, patterns in HelpYamlLoader.yaml_implicit_resolvers.items() if patterns}


@dataclass(frozen=True)
class HelpEntry:
    """One topic: its name, its aliases, and the paragraph the player sees."""

    name: str
    aliases: tuple[str, ...]
    text: str
    example: str | None = None
    see: tuple[str, ...] = ()
    notes: str | None = None


@dataclass(frozen=True)
class HelpBook:
    """Every help entry, in file order."""

    entries: tuple[HelpEntry, ...]

    def find(self, topic: str) -> HelpEntry | None:
        """Return the entry whose name or alias is ``topic``, in any case."""
        folded = topic.casefold()
        for entry in self.entries:
            names = (entry.name, *entry.aliases)
            if any(name.casefold() == folded for name in names):
                return entry
        return None

    def names(self) -> tuple[str, ...]:
        """Return the entry names in alphabetical order."""
        return tuple(sorted(entry.name for entry in self.entries))

    def search(self, phrase: str) -> tuple[HelpEntry, ...]:
        """Return the entries whose name, alias, text, example, or notes contain ``phrase``."""
        needle = phrase.casefold()
        found: list[HelpEntry] = []
        for entry in self.entries:
            parts = [entry.name, *entry.aliases, entry.text, entry.example or "", entry.notes or ""]
            if needle in "\n".join(parts).casefold():
                found.append(entry)
        return tuple(sorted(found, key=lambda entry: entry.name))


BOX_WIDTH = 54
BOX_MIN = 20


def outer_width(width: int, longest: int) -> int:
    """Return the box width, including borders."""
    outer = max(BOX_WIDTH, width // 2) if width >= BOX_WIDTH else max(width, BOX_MIN)
    return max(outer, longest + 4)


def longest_token(entry: HelpEntry, syntax: str | None, visible: frozenset[str], permissions: str | None = None) -> int:
    """Return the longest single word this page will draw."""
    if syntax is None:
        parts = [entry.text]
    else:
        parts = [entry.name, syntax, entry.text, *entry.aliases]
        if entry.example is not None:
            parts.append(entry.example)
        if entry.notes is not None:
            parts.append(entry.notes)
        if permissions is not None:
            parts.append(permissions)
    parts.extend(name for name in entry.see if name in visible)
    longest = 0
    for part in parts:
        for token in part.split():
            longest = max(longest, plain_len(token))
    return longest


def format_page(entry: HelpEntry, syntax: str | None, width: int, *, visible: frozenset[str], permissions: str | None = None) -> str:
    """Return one centered help box, ending with a newline."""
    from .utilities import center_block

    longest = longest_token(entry, syntax, visible, permissions)
    outer = outer_width(width, longest)
    inner = outer - 2
    rows = page_rows(entry, syntax, inner, visible, permissions)
    border = "+" + ("-" * inner) + "+"
    lines = [border, *(box_row(row, inner) for row in rows), border]
    return center_block("\n".join(lines) + "\n", width)


def heading(word: str) -> str:
    """Return a label padded to the Permissions column."""
    return f"{word.ljust(len('Permissions'))}:"


def box_row(text: str, inner: int) -> str:
    """Return one bordered line. The visible width of ``text`` is at most ``inner``."""
    gap = inner - plain_len(text)
    return "|" + text + (" " * gap if gap > 0 else "") + "|"


def page_rows(entry: HelpEntry, syntax: str | None, inner: int, visible: frozenset[str], permissions: str | None = None) -> list[str]:
    """Return the inset lines, without borders."""
    shown = [name for name in entry.see if name in visible]
    if syntax is None:
        rows = paragraph_rows(entry.text, inner)
        if shown:
            rows.append("")
            rows.extend(labeled_row(heading("See Also"), " ".join(shown), inner))
        return rows
    rows = []
    rows.extend(labeled_row(heading("Command"), entry.name, inner))
    rows.extend(labeled_row(heading("Syntax"), syntax, inner))
    if entry.example:
        rows.extend(labeled_row(heading("Example"), entry.example, inner))
    if entry.aliases:
        rows.extend(labeled_row(heading("Aliases"), " ".join(entry.aliases), inner))
    if entry.notes:
        rows.extend(labeled_row(heading("Notes"), entry.notes, inner))
    if permissions is not None:
        rows.extend(labeled_row(heading("Permissions"), permissions, inner))
    rows.append("")
    rows.extend(paragraph_rows(entry.text, inner))
    if shown:
        rows.append("")
        rows.extend(labeled_row(heading("See Also"), " ".join(shown), inner))
    return rows


def labeled_row(label: str, value: str, inner: int) -> list[str]:
    """Pack ``value`` after ``label``. Later lines start at the paragraph column."""
    words = value.split()
    rows: list[str] = []
    current = f" {label} "
    for word in words:
        sep = "" if current.endswith(" ") else " "
        trial = current + sep + word
        if plain_len(trial) <= inner - 1:
            current = trial
            continue
        rows.append(current.rstrip())
        current = " " + word
    if current.strip():
        rows.append(current.rstrip())
    return rows


def paragraph_rows(text: str, inner: int) -> list[str]:
    """Wrap the help text. A blank line in the text stays blank."""
    rows: list[str] = []
    for part in text.split("\n"):
        if part.strip() == "":
            rows.append("")
            continue
        chunks = textwrap.wrap(part, inner - 2, break_long_words=False, break_on_hyphens=False) or [part]
        rows.extend(" " + chunk for chunk in chunks)
    return rows


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
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Help file {path} is not valid UTF-8: {exc.reason}") from exc
    try:
        payload = yaml.load(raw_text, Loader=HelpYamlLoader)  # noqa: S506
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise ConfigError(f"Help file {path} must be a YAML list.")
    entries = tuple(entry(body) for body in payload)
    reject_duplicates(entries)
    reject_see(entries)
    return HelpBook(entries=entries)


@dataclass(frozen=True)
class HelpStamp:
    """The modification time and size of one help file."""

    mtime_ns: int
    size: int


MISSING_HELP = HelpStamp(-1, -1)


def help_stamp(path: Path) -> HelpStamp:
    """Return the file stamp, or the missing stamp when the file cannot be read."""
    try:
        info = path.stat()
    except OSError:
        return MISSING_HELP
    return HelpStamp(info.st_mtime_ns, info.st_size)


class HelpTarget(Protocol):
    """The server fields a help reload writes."""

    config: Config
    help: HelpBook
    help_stamp: HelpStamp
    help_failed: HelpStamp | None


def load_help_into(server: HelpTarget) -> str | None:
    """Replace the server book. Return the load error when the old book stays."""
    path = server.config.help
    stamp = help_stamp(path)
    try:
        book = load_help(path)
    except ConfigError as exc:
        server.help_failed = stamp
        return str(exc)
    server.help = book
    server.help_stamp = stamp
    server.help_failed = None
    return None


def entry(body: object) -> HelpEntry:
    """Return one help entry parsed from ``body``."""
    if not isinstance(body, dict):
        raise ConfigError("Help entry must be a mapping.")
    if "name" not in body:
        raise ConfigError("Help entry is missing name.")
    name = body["name"]
    if not isinstance(name, str) or name.strip() == "":
        raise ConfigError("Help entry name must be a non-empty string.")
    extra = sorted(str(key) for key in body if key not in HELP_KEYS)
    if extra:
        raise ConfigError(f"Unknown help key on {name}: {', '.join(extra)}")
    raw_aliases = body.get("aliases")
    if raw_aliases is None or raw_aliases == "":
        aliases: list[str] = []
    else:
        aliases = raw_aliases
    if not isinstance(aliases, list) or any(not isinstance(item, str) for item in aliases):
        raise ConfigError(f"Help entry {name} aliases must be a list of strings.")
    if any(item == "" for item in aliases):
        raise ConfigError(f"Help entry {name} has an empty alias.")
    if "text" not in body:
        raise ConfigError(f"Help entry {name} is missing text.")
    text = body["text"]
    if not isinstance(text, str) or text.strip() == "":
        raise ConfigError(f"Help entry {name} text must be a non-empty string.")
    example = optional_example(name, body.get("example"))
    notes = optional_notes(name, body.get("notes"))
    see = optional_see(name, body.get("see_also"))
    return HelpEntry(name=name, aliases=tuple(aliases), text=text.strip(), example=example, see=see, notes=notes)


def optional_example(name: str, value: object) -> str | None:
    """Return one example line, or None when the key is blank."""
    if value is None:
        return None
    if not isinstance(value, str) or "\n" in value:
        raise ConfigError(f"Help entry {name} example must be one line.")
    stripped = value.strip()
    if stripped == "":
        return None
    return stripped


def optional_notes(name: str, value: object) -> str | None:
    """Return one notes line, or None when the key is blank."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or "\n" in value:
        raise ConfigError(f"Help entry {name} notes must be one line.")
    stripped = value.strip()
    if stripped == "":
        return None
    return stripped


def optional_see(name: str, value: object) -> tuple[str, ...]:
    """Return see names. An empty list means the line is omitted later."""
    if value is None or value == "":
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"Help entry {name} see_also must be a list of topic names.")
    return tuple(value)


def reject_see(entries: tuple[HelpEntry, ...]) -> None:
    """Each see name must be a different topic in this file."""
    names = {entry.name for entry in entries}
    for entry in entries:
        for seen in entry.see:
            if seen == entry.name:
                raise ConfigError(f"Help entry {entry.name} see_also cannot name itself.")
            if seen not in names:
                raise ConfigError(f"Help entry {entry.name} see_also must name a topic.")


def reject_duplicates(entries: tuple[HelpEntry, ...]) -> None:
    """Reject a help book that names one topic twice."""
    seen: set[str] = set()
    for entry in entries:
        if entry.name in seen:
            raise ConfigError(f"Duplicate help name: {entry.name}.")
        seen.add(entry.name)
        for alias in entry.aliases:
            if alias in seen:
                raise ConfigError(f"Help alias '{alias}' is already used.")
            seen.add(alias)
