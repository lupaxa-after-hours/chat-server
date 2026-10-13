"""Process exit codes for the console script."""

from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import pytest

from lupaxa.chat_server.cli import enter_background, main
from lupaxa.chat_server.defaults import DEFAULT_HOST


def test_config_flag_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    """Omitting -c is argparse's error and exits 2."""
    code = main([])
    assert code == 2


def test_bad_config_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A config error is printed to stderr and exits 1."""
    path = tmp_path / "chat.yaml"
    path.write_text("port: true\n", encoding="utf-8")
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
    path = tmp_path / "chat.yaml"
    (tmp_path / "rooms.yml").write_text(
        "reception:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n",
        encoding="utf-8",
    )
    (tmp_path / "help.yml").write_text("[]\n", encoding="utf-8")
    path.write_text(
        f"port: {port}\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n",
        encoding="utf-8",
    )
    try:
        code = main(["--config", str(path)])
    finally:
        held.close()
    assert code == 1
    captured = capsys.readouterr()
    assert "Could not listen" in captured.err
    assert (tmp_path / "players").is_dir()


def _ready(tmp_path: Path, text: str) -> Path:
    (tmp_path / "rooms.yml").write_text(
        "reception:\n  description: Hi.\n  max_occupancy: 1\n  exits: {}\n",
        encoding="utf-8",
    )
    (tmp_path / "help.yml").write_text("[]\n", encoding="utf-8")
    path = tmp_path / "chat.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind((DEFAULT_HOST, 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def test_background_with_ssh_forks_before_ssh_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Background start binds SSH in the main thread and forks before the SSH thread."""
    telnet_port = _free_port()
    ssh_port = _free_port()
    path = _ready(
        tmp_path,
        (
            f"port: {telnet_port}\n"
            f"ssh_port: {ssh_port}\n"
            "rooms: rooms.yml\n"
            "start: reception\n"
            "help: help.yml\n"
            "background: true\n"
        ),
    )
    threads_at_fork: list[list[str]] = []
    served: list[bool] = []

    def _fork() -> int:
        threads_at_fork.append([thread.name for thread in threading.enumerate()])
        return 1

    monkeypatch.setattr(os, "fork", _fork)

    def _stop(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", _stop)
    monkeypatch.setattr(
        "lupaxa.chat_server.cli.ChatServer.serve_forever",
        lambda self: served.append(True),
    )
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
    path = _ready(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n",
    )
    started: list[bool] = []
    served: list[bool] = []
    monkeypatch.setattr(
        "lupaxa.chat_server.cli.ChatServer.start",
        lambda self: started.append(True),
    )
    monkeypatch.setattr(
        "lupaxa.chat_server.cli.ChatServer.serve_forever",
        lambda self: served.append(True),
    )
    monkeypatch.setattr(os, "fork", lambda: 1)

    def _stop(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", _stop)
    with pytest.raises(SystemExit) as caught:
        main(["-c", str(path)])
    assert caught.value.code == 0
    assert started == [True]
    assert served == []


def test_background_child_discards_output_and_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child leaves the terminal, discards output, and serves."""
    path = _ready(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n",
    )
    served: list[bool] = []
    dupes: list[tuple[int, int]] = []
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.start", lambda self: None)
    monkeypatch.setattr(
        "lupaxa.chat_server.cli.ChatServer.serve_forever",
        lambda self: served.append(True),
    )
    monkeypatch.setattr(os, "fork", lambda: 0)
    monkeypatch.setattr(os, "setsid", lambda: 0)
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: 99)
    monkeypatch.setattr(os, "dup2", lambda src, dst: dupes.append((src, dst)))
    monkeypatch.setattr(os, "close", lambda fd: None)
    code = main(["-c", str(path)])
    assert code == 0
    assert served == [True]
    assert dupes == [(99, 0), (99, 1), (99, 2)]


def test_background_fork_failure_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed fork prints to stderr and does not serve."""
    path = _ready(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n",
    )
    served: list[bool] = []
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.start", lambda self: None)
    monkeypatch.setattr(
        "lupaxa.chat_server.cli.ChatServer.serve_forever",
        lambda self: served.append(True),
    )

    def _boom() -> int:
        raise OSError("fork failed")

    monkeypatch.setattr(os, "fork", _boom)
    code = main(["-c", str(path)])
    assert code == 1
    assert served == []
    captured = capsys.readouterr()
    assert captured.err == "This platform cannot run in the background.\n"


def test_background_without_fork_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A platform with no fork prints to stderr and does not serve."""
    path = _ready(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n",
    )
    served: list[bool] = []
    monkeypatch.setattr("lupaxa.chat_server.cli.ChatServer.start", lambda self: None)
    monkeypatch.setattr(
        "lupaxa.chat_server.cli.ChatServer.serve_forever",
        lambda self: served.append(True),
    )
    monkeypatch.setattr(os, "fork", None)
    code = main(["-c", str(path)])
    assert code == 1
    assert served == []
    captured = capsys.readouterr()
    assert captured.err == "This platform cannot run in the background.\n"
