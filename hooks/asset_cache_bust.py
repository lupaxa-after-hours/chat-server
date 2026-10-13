"""Append ?v=<git-sha> to extra CSS/JS so phones fetch new files after a publish."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def asset_version(*, cwd: Path | None = None) -> str:
    """Return a short git SHA, or ``dev`` when git is unavailable."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd or REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "dev"


def extra_asset_paths(config: dict[str, Any]) -> list[str]:
    """Clean extra_css / extra_javascript paths (no query string)."""
    paths: list[str] = []
    for item in config.get("extra_css") or []:
        paths.append(str(item).split("?", 1)[0])
    for item in config.get("extra_javascript") or []:
        raw = item.get("path") if isinstance(item, dict) else item
        paths.append(str(raw or "").split("?", 1)[0])
    return [path for path in paths if path]


def apply_asset_version(html: str, paths: list[str], version: str) -> str:
    """Rewrite extra asset href/src values to include ``?v=<version>``."""
    if not html or not paths or not version:
        return html
    unique = sorted({path for path in paths if path}, key=len, reverse=True)
    joined = "|".join(re.escape(path) for path in unique)
    pattern = re.compile(
        rf'(?P<attr>href|src)="(?P<before>[^"?]*?)(?P<path>{joined})(?:\?[^"]*)?"'
    )
    return pattern.sub(
        rf'\g<attr>="\g<before>\g<path>?v={version}"',
        html,
    )


def on_post_page(output: str, page: object, config: dict[str, Any]) -> str:
    del page
    version = asset_version()
    return apply_asset_version(output, extra_asset_paths(config), version)
