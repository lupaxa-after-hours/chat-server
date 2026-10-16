"""Process exit codes for the console script."""

from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import pytest
from test_config import write_at

from lupaxa.chat_server.cli import enter_background, main
from lupaxa.chat_server.defaults import DEFAULT_HOST
from lupaxa.chat_server.ranks import Rank
from lupaxa.chat_server.saved_player import SavedPlayer, hash_password, save_player, super_admins

FULL_MSG = "The server is full.\nPlease try again later.\n"
NEWBIE_MSG = "No more new players can enter right now.\nPlease try again later.\n"


def write_gate_files(files: Path) -> None:
    """Write the refusal messages beside the banner for CLI tests."""
    files.mkdir(exist_ok=True)
    (files / "full.msg").write_text(FULL_MSG, encoding="utf-8")
    (files / "no-more-newbies.msg").write_text(NEWBIE_MSG, encoding="utf-8")


def seed_super_admin(players: Path) -> None:
    """Write one super admin player file so main passes the roster check."""
    players.mkdir(parents=True, exist_ok=True)
    save_player(players, SavedPlayer("Admin", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))


def test_config_flag_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    """Omitting -c is argparse's error and exits 2."""
    code = main([])
    assert code == 2


def test_setup_without_config_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """Setup without -c is argparse's error and exits 2."""
    code = main(["setup"])
    captured = capsys.readouterr()
    assert code == 2
    assert "unrecognized arguments" not in captured.err
    assert "-c/--config" in captured.err


def empty_players_config(tmp_path: Path) -> tuple[Path, Path]:
    """Return a config path and an empty players directory."""
    players = tmp_path / "players"
    players.mkdir()
    path = write_at(
        tmp_path,
        "chat.yaml",
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmax_connections: 128\nmax_newbies: 20\nmessages_path: files\nplayers: players\n",
        caps=False,
    )
    return path, players


def test_setup_command_creates_one_super_admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup with -c creates one super admin when the players directory is empty."""
    path, players = empty_players_config(tmp_path)
    names = ["Ada"]
    secrets = ["secret", "secret"]
    monkeypatch.setattr("builtins.input", lambda _prompt="": names.pop(0))
    monkeypatch.setattr("lupaxa.chat_server.cli.getpass.getpass", lambda _prompt="": secrets.pop(0))
    assert list(players.glob("*.yml")) == []
    code = main(["setup", "-c", str(path)])
    files = list(players.glob("*.yml"))
    found = super_admins(players)
    assert code == 0
    assert names == []
    assert secrets == []
    assert len(files) == 1
    assert len(found) == 1
    assert found[0].name == "Ada"
    assert found[0].rank == Rank.SUPER_ADMIN


@pytest.mark.parametrize("stopped", [KeyboardInterrupt, EOFError])
def test_setup_stops_without_a_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stopped: type[BaseException]) -> None:
    """Ctrl-C and end of input on setup print a newline and return 1."""
    path, players = empty_players_config(tmp_path)

    def stop(_prompt: str = "") -> str:
        raise stopped

    monkeypatch.setattr("builtins.input", stop)
    code = main(["setup", "-c", str(path)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == "Name:\n"
    assert captured.err == ""
    assert "Traceback" not in captured.out
    assert list(players.glob("*.yml")) == []


def test_setup_save_error_prints_like_chat_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A save error on setup is printed to stderr and returns 1."""
    path, players = empty_players_config(tmp_path)
    names = ["Ada"]
    secrets = ["secret", "secret"]
    monkeypatch.setattr("builtins.input", lambda _prompt="": names.pop(0))
    monkeypatch.setattr("lupaxa.chat_server.cli.getpass.getpass", lambda _prompt="": secrets.pop(0))

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("lupaxa.chat_server.setup.save_player", fail)
    code = main(["setup", "-c", str(path)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == "disk full\n"
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert list(players.glob("*.yml")) == []


def test_bad_config_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A config error is printed to stderr and exits 1."""
    path = write_at(tmp_path, "chat.yaml", "port: true\n")
    code = main(["-c", str(path)])
    assert code == 1
    captured = capsys.readouterr()
    assert "integer" in captured.err


def test_bind_failure_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A port that is already taken exits 1."""
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind((DEFAULT_HOST, 0))
    held.listen(1)
    port = int(held.getsockname()[1])
    (tmp_path / "rooms.yml").write_text("reception:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n", encoding="utf-8")
    (tmp_path / "help.yml").write_text("[]\n", encoding="utf-8")
    files = tmp_path / "files"
    files.mkdir()
    (files / "banner.msg").write_text("Hello\n", encoding="utf-8")
    write_gate_files(files)
    path = write_at(
        tmp_path,
        "chat.yaml",
        f"port: {port}\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmax_connections: 128\nmax_newbies: 20\nmessages_path: files\n",
        caps=False,
    )
    seed_super_admin(tmp_path / "players")
    try:
        code = main(["--config", str(path)])
    finally:
        held.close()
    assert code == 1
    captured = capsys.readouterr()
    assert "Could not listen" in captured.err
    assert (tmp_path / "players").is_dir()


def ready(tmp_path: Path, text: str) -> Path:
    (tmp_path / "rooms.yml").write_text("reception:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n", encoding="utf-8")
    (tmp_path / "help.yml").write_text("[]\n", encoding="utf-8")
    files = tmp_path / "files"
    files.mkdir()
    (files / "banner.msg").write_text("Hello\n", encoding="utf-8")
    write_gate_files(files)
    path = write_at(tmp_path, "chat.yaml", text)
    seed_super_admin(tmp_path / "players")
    return path


def free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind((DEFAULT_HOST, 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def listen_config(tmp_path: Path) -> tuple[str, Path]:
    """Return a listen-ready config path and the players directory."""
    port = free_port()
    (tmp_path / "rooms.yml").write_text("reception:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n", encoding="utf-8")
    (tmp_path / "help.yml").write_text("[]\n", encoding="utf-8")
    files = tmp_path / "files"
    files.mkdir()
    (files / "banner.msg").write_text("Hello\n", encoding="utf-8")
    write_gate_files(files)
    path = write_at(
        tmp_path,
        "chat.yaml",
        f"port: {port}\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmax_connections: 128\nmax_newbies: 20\nmessages_path: files\n",
        caps=False,
    )
    config_path = str(path)
    players = tmp_path / "players"
    players.mkdir(parents=True, exist_ok=True)
    return config_path, players


def test_startup_requires_one_super_admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Listen refuses to start with no super admin and starts with exactly one."""
    config_path, players = listen_config(tmp_path)
    code = main(["-c", config_path])
    assert code == 1
    captured = capsys.readouterr()
    assert captured.err == f"There is no super admin. Run chat-server setup -c {config_path}\n"
    save_player(players, SavedPlayer("Ada", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.start", lambda self: None)
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.serve_forever", lambda self: None)
    code = main(["-c", config_path])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""


def test_more_than_one_super_admin_warns_and_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Two or more super admins print the names and the setup line, then start."""
    config_path, path = listen_config(tmp_path)
    save_player(path, SavedPlayer("Cora", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(path, SavedPlayer("ada", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(path, SavedPlayer("Bea", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.start", lambda self: None)
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.serve_forever", lambda self: None)
    code = main(["-c", config_path])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == f"There is more than one super admin: ada, Bea & Cora\nRun chat-server setup -c {config_path}\n"


def test_reboot_warns_and_still_reads_the_snapshot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Reboot is not refused because more than one super admin exists."""
    config_path, path = listen_config(tmp_path)
    save_player(path, SavedPlayer("Ada", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    save_player(path, SavedPlayer("Bea", Rank.SUPER_ADMIN, hash_password("pw", rounds=4)))
    code = main(["-c", config_path, "--reboot", str(tmp_path / "missing-reboot.json")])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.startswith(f"There is more than one super admin: Ada & Bea\nRun chat-server setup -c {config_path}\n")
    assert "There is more than one super admin. Run" not in captured.err


def test_background_with_ssh_forks_before_ssh_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Background start binds SSH in the main thread and forks before the SSH thread."""
    telnet_port = free_port()
    ssh_port = free_port()
    path = ready(tmp_path, (f"port: {telnet_port}\nssh_port: {ssh_port}\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n"))  # fmt: skip  # noqa: E501
    threads_at_fork: list[list[str]] = []
    served: list[bool] = []

    def fork() -> int:
        threads_at_fork.append([thread.name for thread in threading.enumerate()])
        return 1

    monkeypatch.setattr(os, "fork", fork)

    def stop(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", stop)
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.serve_forever", lambda self: served.append(True))
    with pytest.raises(SystemExit) as caught:
        main(["-c", str(path)])
    assert caught.value.code == 0
    assert served == []
    assert len(threads_at_fork) == 1
    assert threads_at_fork[0] == ["MainThread"]


def test_enter_background_flushes_before_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stdout and stderr are flushed before ``os.fork`` runs."""
    flushed: list[str] = []
    monkeypatch.setattr(sys.stdout, "flush", lambda: flushed.append("stdout"))
    monkeypatch.setattr(sys.stderr, "flush", lambda: flushed.append("stderr"))
    monkeypatch.setattr(os, "fork", lambda: 1)
    assert enter_background() is False
    assert flushed == ["stdout", "stderr"]


def test_background_parent_does_not_serve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The parent exits 0 after start and does not serve."""
    path = ready(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n")
    started: list[bool] = []
    served: list[bool] = []
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.start", lambda self: started.append(True))
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.serve_forever", lambda self: served.append(True))
    monkeypatch.setattr(os, "fork", lambda: 1)

    def stop(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", stop)
    with pytest.raises(SystemExit) as caught:
        main(["-c", str(path)])
    assert caught.value.code == 0
    assert started == [True]
    assert served == []


def test_background_child_discards_output_and_serves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The child leaves the terminal, discards output, and serves."""
    path = ready(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n")
    served: list[bool] = []
    dupes: list[tuple[int, int]] = []
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.start", lambda self: None)
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.serve_forever", lambda self: served.append(True))
    monkeypatch.setattr(os, "fork", lambda: 0)
    monkeypatch.setattr(os, "setsid", lambda: 0)
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: 99)
    monkeypatch.setattr(os, "dup2", lambda src, dst: dupes.append((src, dst)))
    monkeypatch.setattr(os, "close", lambda fd: None)
    code = main(["-c", str(path)])
    assert code == 0
    assert served == [True]
    assert dupes == [(99, 0), (99, 1), (99, 2)]


def test_background_fork_failure_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A failed fork prints to stderr and does not serve."""
    path = ready(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n")
    served: list[bool] = []
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.start", lambda self: None)
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.serve_forever", lambda self: served.append(True))

    def boom() -> int:
        raise OSError("fork failed")

    monkeypatch.setattr(os, "fork", boom)
    code = main(["-c", str(path)])
    assert code == 1
    assert served == []
    captured = capsys.readouterr()
    assert captured.err == "This platform cannot run in the background.\n"


def test_background_without_fork_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A platform with no fork prints to stderr and does not serve."""
    path = ready(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n")
    served: list[bool] = []
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.start", lambda self: None)
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.serve_forever", lambda self: served.append(True))
    monkeypatch.setattr(os, "fork", None)
    code = main(["-c", str(path)])
    assert code == 1
    assert served == []
    captured = capsys.readouterr()
    assert captured.err == "This platform cannot run in the background.\n"
