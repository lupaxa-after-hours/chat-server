"""Command-line entry point."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from .chatserver import ChatServer
from .config import load_banner, load_config, load_gate_file
from .exceptions import ChatError, ConfigError
from .help import load_help
from .reboot import discard_leftover, read_reboot
from .roles import join_names
from .rooms import load_layout
from .saved_player import ensure_players_directory, super_admins
from .setup import run_setup


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
        ``2`` when ``-c`` is missing, ``1`` when config, setup, or bind fails,
        ``0`` after setup or a clean shutdown.
    """
    parser = argparse.ArgumentParser(prog="chat-server")
    parser.add_argument("-c", "--config", required=True, help="Path to the YAML config file.")
    parser.add_argument("--reboot", type=Path)
    parser.add_argument("command", nargs="?", choices=("setup",))
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
        if args.command == "setup":
            try:
                ensure_players_directory(config.players)
                # Prompts are already written by run_setup. Empty strings keep input and getpass from printing them again.
                run_setup(config.players, config.rooms, config.help, read_line=lambda prompt: input(""), read_secret=lambda prompt: getpass.getpass(""), write=lambda text: print(text, end="", flush=True))
            except (KeyboardInterrupt, EOFError):
                print()
                return 1
            except OSError as exc:
                print(exc, file=sys.stderr)
                return 1
            return 0
        layout = load_layout(config.rooms).with_void()
        help_book = load_help(config.help)
        ensure_players_directory(config.players)
        found = super_admins(config.players)
        if len(found) == 0:
            raise ConfigError(f"There is no super admin. Run chat-server setup -c {args.config}")
        if len(found) > 1:
            names = join_names(player.name for player in found)
            print(f"There is more than one super admin: {names}", file=sys.stderr)
            print(f"Run chat-server setup -c {args.config}", file=sys.stderr)
        banner = load_banner(config.messages_path)
        full_message = load_gate_file(config.messages_path, "full.msg", "full", "Full")
        newbie_message = load_gate_file(config.messages_path, "no-more-newbies.msg", "no-more-newbies", "No-more-newbies")
        server = ChatServer(config, layout, help_book, banner, full_message, newbie_message)
        try:
            if args.reboot is not None:
                try:
                    state = read_reboot(args.reboot)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    print(exc, file=sys.stderr)
                    return 1
                server.adopt(state)
            else:
                discard_leftover(config.players)
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
