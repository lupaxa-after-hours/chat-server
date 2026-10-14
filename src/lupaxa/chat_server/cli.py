"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import load_banner, load_config
from .exceptions import ChatError
from .help import load_help
from .rooms import load_layout
from .saved_player import ensure_players_directory
from .server import ChatServer


def enter_background() -> bool | None:
    """Fork. Return True in the child, False in the parent, None on failure."""
    fork = getattr(os, "fork", None)
    if fork is None:
        print("This platform cannot run in the background.", file=sys.stderr)
        return None
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        pid = fork()
    except OSError:
        print("This platform cannot run in the background.", file=sys.stderr)
        return None
    if pid != 0:
        return False
    try:
        os.setsid()
        null = os.open(os.devnull, os.O_RDWR)
    except OSError:
        print("This platform cannot run in the background.", file=sys.stderr)
        os._exit(1)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    os.close(null)
    return True


def main(argv: list[str] | None = None) -> int:
    """Run the server.

    Parameters
    ----------
    argv :
        Arguments after the program name. ``None`` uses the process arguments.

    Returns
    -------
    int
        ``2`` when ``-c`` is missing, ``1`` when config or bind fails, ``0``
        after a clean shutdown.
    """
    parser = argparse.ArgumentParser(prog="chat-server")
    parser.add_argument(
        "-c",
        "--config",
        required=True,
        help="Path to the YAML config file.",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None or code == 0:
            return 0
        if isinstance(code, int):
            return code
        return 1

    try:
        config = load_config(Path(args.config))
        layout = load_layout(config.rooms)
        help_book = load_help(config.help)
        ensure_players_directory(config.players)
        banner = load_banner(Path(args.config))
        server = ChatServer(config, layout, help_book, banner)
        try:
            server.start()
            if config.background:
                child = enter_background()
                if child is None:
                    return 1
                if not child:
                    os._exit(0)
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
            return 0
    except ChatError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0
