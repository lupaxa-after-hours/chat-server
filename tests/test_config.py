"""YAML config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from lupaxa.chat_server.config import DEFAULT_HOST, Config, load_banner, load_config
from lupaxa.chat_server.exceptions import ConfigError


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "chat.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_port_rooms_and_start(tmp_path: Path) -> None:
    """A relative rooms path is resolved from the config file's directory."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: hall/rooms.yml\nstart: arena\nhelp: notes/help.yml\n",
    )
    loaded = load_config(path)
    assert loaded == Config(
        port=9000,
        rooms=(tmp_path / "hall" / "rooms.yml").resolve(),
        start="arena",
        help=(tmp_path / "notes" / "help.yml").resolve(),
        players=(tmp_path / "players").resolve(),
        admin="admin",
        ssh_host_key=(tmp_path / "ssh_host_key").resolve(),
    )


def test_missing_help(tmp_path: Path) -> None:
    """The help file is not invented."""
    path = _write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\n")
    with pytest.raises(ConfigError, match="missing help"):
        load_config(path)


def test_help_must_be_a_string(tmp_path: Path) -> None:
    """A numeric help value is rejected."""
    path = _write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: 1\n")
    with pytest.raises(ConfigError, match="help must be a string"):
        load_config(path)


def test_absolute_help_path_is_kept(tmp_path: Path) -> None:
    """An absolute help path is not joined to the config directory."""
    help_path = tmp_path / "elsewhere" / "help.yml"
    path = _write(
        tmp_path,
        f"port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: {help_path}\n",
    )
    assert load_config(path).help == help_path


def test_missing_start(tmp_path: Path) -> None:
    """The starting room is not invented."""
    path = _write(tmp_path, "port: 9000\nrooms: rooms.yml\n")
    with pytest.raises(ConfigError, match="start"):
        load_config(path)


@pytest.mark.parametrize("text", ["start: true\n", "start: Lobby\n", "start: 1\n"])
def test_start_must_be_a_room_id(tmp_path: Path, text: str) -> None:
    """The starting room is a room id, not a display name or a bool."""
    path = _write(tmp_path, f"port: 9000\nrooms: rooms.yml\n{text}")
    with pytest.raises(ConfigError, match="room id"):
        load_config(path)


def test_missing_rooms(tmp_path: Path) -> None:
    """A config with only a port does not invent a rooms file."""
    path = _write(tmp_path, "port: 9000\n")
    with pytest.raises(ConfigError, match="rooms"):
        load_config(path)


def test_rooms_must_be_a_string(tmp_path: Path) -> None:
    """A numeric rooms value is rejected."""
    path = _write(tmp_path, "port: 9000\nrooms: 1\n")
    with pytest.raises(ConfigError, match="string"):
        load_config(path)


def test_missing_file(tmp_path: Path) -> None:
    """A path that does not exist raises ConfigError."""
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match="Cannot read"):
        load_config(missing)


def test_yaml_must_be_a_mapping(tmp_path: Path) -> None:
    """A list document is rejected."""
    path = _write(tmp_path, "- 1\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(path)


def test_missing_port(tmp_path: Path) -> None:
    """Neither listener is invented."""
    path = _write(tmp_path, "{}\n")
    with pytest.raises(ConfigError, match="Config needs a port or an ssh_port"):
        load_config(path)


def test_ssh_port_only(tmp_path: Path) -> None:
    """SSH can listen when telnet is omitted."""
    path = _write(
        tmp_path,
        "ssh_port: 9022\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n",
    )
    loaded = load_config(path)
    assert loaded.port is None
    assert loaded.ssh_port == 9022
    assert loaded.ssh_host_key == (tmp_path / "ssh_host_key").resolve()


def test_both_ports(tmp_path: Path) -> None:
    """Telnet and SSH can listen together."""
    path = _write(
        tmp_path,
        "port: 9000\nssh_port: 9022\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n",
    )
    loaded = load_config(path)
    assert loaded.port == 9000
    assert loaded.ssh_port == 9022


def test_ports_must_differ(tmp_path: Path) -> None:
    """One number cannot be both listeners."""
    path = _write(tmp_path, "port: 9000\nssh_port: 9000\n")
    with pytest.raises(ConfigError, match="port and ssh_port must be different"):
        load_config(path)


@pytest.mark.parametrize(
    "text",
    ["ssh_port: '9022'\n", "ssh_port: 1.5\n", "ssh_port: true\n", "ssh_port:\n"],
)
def test_ssh_port_must_be_an_int(tmp_path: Path, text: str) -> None:
    """Strings, floats, bools, and null are not SSH ports."""
    path = _write(tmp_path, text)
    with pytest.raises(ConfigError, match="ssh_port must be an integer"):
        load_config(path)


@pytest.mark.parametrize("text", ["ssh_port: 0\n", "ssh_port: 65536\n"])
def test_ssh_port_range(tmp_path: Path, text: str) -> None:
    """SSH ports outside 1 to 65535 are rejected."""
    path = _write(tmp_path, text)
    with pytest.raises(ConfigError, match="ssh_port must be from 1 to 65535"):
        load_config(path)


@pytest.mark.parametrize("text", ["port: '9000'\n", "port: 1.5\n", "port: true\n"])
def test_port_must_be_an_int(tmp_path: Path, text: str) -> None:
    """Strings, floats, and bools are not ports."""
    path = _write(tmp_path, text)
    with pytest.raises(ConfigError, match="integer"):
        load_config(path)


@pytest.mark.parametrize("text", ["port: 0\n", "port: 65536\n"])
def test_port_range(tmp_path: Path, text: str) -> None:
    """Ports outside 1 to 65535 are rejected."""
    path = _write(tmp_path, text)
    with pytest.raises(ConfigError, match="1 to 65535"):
        load_config(path)


def test_unknown_key(tmp_path: Path) -> None:
    """An extra key is named in the error."""
    path = _write(tmp_path, "port: 9000\ncolour: 1\n")
    with pytest.raises(ConfigError, match="colour"):
        load_config(path)


def test_host_defaults_when_omitted(tmp_path: Path) -> None:
    """No host key binds every interface."""
    path = _write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    assert load_config(path).host == DEFAULT_HOST


def test_host_is_an_ip_address(tmp_path: Path) -> None:
    """Host is the address passed to bind."""
    path = _write(
        tmp_path,
        "host: 127.0.0.1\nport: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n",
    )
    assert load_config(path).host == "127.0.0.1"


@pytest.mark.parametrize("text", ["host: true\n", "host: localhost\n", "host: 1\n"])
def test_host_must_be_an_ip_address(tmp_path: Path, text: str) -> None:
    """A bool, a name, or a bare number is not a bind address."""
    path = _write(
        tmp_path,
        f"{text}port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n",
    )
    with pytest.raises(ConfigError, match="IP address"):
        load_config(path)


def test_players_defaults_beside_the_config(tmp_path: Path) -> None:
    """No players key uses a players directory next to the config file."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n",
    )
    loaded = load_config(path)
    assert loaded.players == (tmp_path / "players").resolve()
    assert loaded.admin == "admin"


def test_players_path_is_resolved(tmp_path: Path) -> None:
    """A relative players path is joined to the config directory."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n"
        "players: save/here\nadmin: Boss\n",
    )
    loaded = load_config(path)
    assert loaded.players == (tmp_path / "save" / "here").resolve()
    assert loaded.admin == "Boss"


def test_players_must_be_a_string(tmp_path: Path) -> None:
    """A numeric players value is rejected."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nplayers: 1\n",
    )
    with pytest.raises(ConfigError, match="players must be a string"):
        load_config(path)


def test_admin_must_be_savable(tmp_path: Path) -> None:
    """The admin name has to be a legal saved-player name."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nadmin: '../x'\n",
    )
    with pytest.raises(ConfigError, match="admin must be a savable name"):
        load_config(path)


def test_login_timeout_defaults_to_3_minutes(tmp_path: Path) -> None:
    """Leaving the key out gives a connection 3 minutes."""
    path = _write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    assert load_config(path).login_timeout == 3


def test_login_timeout_reads_a_whole_number_of_minutes(tmp_path: Path) -> None:
    """The template value is a positive number of minutes."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nlogin_timeout: 5\n",
    )
    assert load_config(path).login_timeout == 5


@pytest.mark.parametrize(
    "text",
    ["login_timeout: true\n", "login_timeout: 1.5\n", "login_timeout: soon\n"],
)
def test_login_timeout_must_be_an_integer(tmp_path: Path, text: str) -> None:
    """A boolean, a float, or a word is rejected."""
    path = _write(
        tmp_path,
        f"port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n{text}",
    )
    with pytest.raises(ConfigError, match="login_timeout must be an integer."):
        load_config(path)


def test_login_timeout_must_be_at_least_1(tmp_path: Path) -> None:
    """Zero minutes is rejected."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nlogin_timeout: 0\n",
    )
    with pytest.raises(ConfigError, match="login_timeout must be at least 1."):
        load_config(path)


def test_background_defaults_to_false(tmp_path: Path) -> None:
    """Omitting background stays in the foreground."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n",
    )
    assert load_config(path).background is False


def test_background_true_and_false(tmp_path: Path) -> None:
    """True and false load as booleans."""
    on = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n",
    )
    assert load_config(on).background is True
    off = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: false\n",
    )
    assert load_config(off).background is False


def test_background_must_be_a_boolean(tmp_path: Path) -> None:
    """A number is not a boolean."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: 1\n",
    )
    with pytest.raises(ConfigError, match="background must be true or false"):
        load_config(path)


def test_allow_flags_default_to_true(tmp_path: Path) -> None:
    """Omitted allow flags are open."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n",
    )
    loaded = load_config(path)
    assert loaded.allow_newbies is True
    assert loaded.allow_residents is True


def test_allow_flags_true_and_false(tmp_path: Path) -> None:
    """True and false load as booleans, the same way background does."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n"
        "allow_newbies: false\nallow_residents: true\n",
    )
    loaded = load_config(path)
    assert loaded.allow_newbies is False
    assert loaded.allow_residents is True


def test_allow_flags_must_be_booleans(tmp_path: Path) -> None:
    """A number is not a boolean."""
    path = _write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nallow_newbies: 1\n",
    )
    with pytest.raises(ConfigError, match="allow_newbies must be true or false"):
        load_config(path)


def test_load_banner_reads_the_file_beside_the_config(tmp_path: Path) -> None:
    """The banner is files/banner.msg next to the config file."""
    files = tmp_path / "files"
    files.mkdir()
    (files / "banner.msg").write_text("Hello\n", encoding="utf-8")
    assert load_banner(tmp_path / "config.yml") == "Hello\n"


def test_load_banner_rejects_a_missing_file(tmp_path: Path) -> None:
    """A missing banner stops startup."""
    config = tmp_path / "config.yml"
    banner = tmp_path / "files" / "banner.msg"
    with pytest.raises(ConfigError, match=rf"Cannot read banner file {banner}: .+\."):
        load_banner(config)


def test_load_banner_rejects_text_that_is_not_utf8(tmp_path: Path) -> None:
    """A banner that is not UTF-8 stops startup."""
    files = tmp_path / "files"
    files.mkdir()
    (files / "banner.msg").write_bytes(b"caf\xe9\n")
    banner = files / "banner.msg"
    with pytest.raises(ConfigError, match=rf"Banner file {banner} is not valid UTF-8: .+\."):
        load_banner(tmp_path / "config.yml")


def test_mfa_enabled_defaults_to_true_and_rejects_a_number(tmp_path: Path) -> None:
    """The MFA flag is on unless the file says false."""
    base = "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n"
    path = _write(tmp_path, base)
    assert load_config(path).mfa_enabled is True
    path = _write(tmp_path, f"{base}mfa_enabled: false\n")
    assert load_config(path).mfa_enabled is False
    path = _write(tmp_path, f"{base}mfa_enabled: 1\n")
    with pytest.raises(ConfigError, match="mfa_enabled must be true or false"):
        load_config(path)
