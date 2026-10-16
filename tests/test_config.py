"""YAML config loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lupaxa.chat_server.config import DEFAULT_HOST, HOT_KEYS, Config, listen_hosts, load_banner, load_config, load_gate_file
from lupaxa.chat_server.exceptions import ConfigError
from lupaxa.chat_server.globals import NAME_PROMPT
from lupaxa.chat_server.login import refusal_text, welcome_text


def write_at(directory: Path, name: str, text: str, *, caps: bool = True) -> Path:
    """Write a wrapper plus hot.yml and cold.yml, and return the wrapper path."""
    if caps and "start:" in text and "max_connections:" not in text:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "max_connections: 128\nmax_newbies: 20\n"
    if "messages_path:" not in text:
        if text.strip() == "{}":
            text = "{messages_path: files}\n"
        elif not text.strip().startswith("-"):
            if text and not text.endswith("\n"):
                text += "\n"
            text += "messages_path: files\n"
    path = directory / name
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError:
        path.write_text(text, encoding="utf-8")
        return path
    if not isinstance(payload, dict):
        path.write_text(text, encoding="utf-8")
        return path
    hot = {key: value for key, value in payload.items() if key in HOT_KEYS}
    cold = {key: value for key, value in payload.items() if key not in HOT_KEYS}
    if caps and not hot and "start" not in payload:
        rooms = payload.get("rooms")
        if rooms is None or not isinstance(rooms, str):
            hot = {"start": "reception", "max_connections": 128, "max_newbies": 20}
    (directory / "hot.yml").write_text(yaml.safe_dump(hot), encoding="utf-8")
    (directory / "cold.yml").write_text(yaml.safe_dump(cold), encoding="utf-8")
    path.write_text("hot: hot.yml\ncold: cold.yml\n", encoding="utf-8")
    return path


def write(tmp_path: Path, text: str, *, caps: bool = True) -> Path:
    return write_at(tmp_path, "chat.yaml", text, caps=caps)


def test_a_wrapper_loads_hot_and_cold(tmp_path: Path) -> None:
    """Hot and cold files beside the wrapper become one config."""
    (tmp_path / "hot.yml").write_text("start: arena\nmax_connections: 64\nmax_newbies: 8\nlogin_timeout: 5\n", encoding="utf-8")
    (tmp_path / "cold.yml").write_text("port: 9000\nrooms: hall/rooms.yml\nhelp: notes/help.yml\nmessages_path: files\n", encoding="utf-8")
    path = tmp_path / "config.yml"
    path.write_text("hot: hot.yml\ncold: cold.yml\n", encoding="utf-8")
    loaded = load_config(path)
    assert loaded.port == 9000
    assert loaded.start == "arena"
    assert loaded.max_connections == 64
    assert loaded.max_newbies == 8
    assert loaded.login_timeout == 5
    assert loaded.rooms == (tmp_path / "hall" / "rooms.yml").resolve()
    assert loaded.hot_path == (tmp_path / "hot.yml").resolve()
    assert loaded.cold_path == (tmp_path / "cold.yml").resolve()
    assert loaded.config_dir == tmp_path.resolve()


def test_a_country_database_path_is_read_beside_the_hot_file(tmp_path: Path) -> None:
    """A relative country database is resolved from the hot file's directory."""
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "hot.yml").write_text("start: arena\nmax_connections: 64\nmax_newbies: 8\ncountry_lookup: true\ncountry_database: data/countries.mmdb\n", encoding="utf-8")
    (tmp_path / "cold.yml").write_text("port: 9000\nrooms: rooms.yml\nhelp: help.yml\nmessages_path: files\n", encoding="utf-8")
    path = tmp_path / "config.yml"
    path.write_text("hot: nested/hot.yml\ncold: cold.yml\n", encoding="utf-8")
    loaded = load_config(path)
    assert loaded.country_database == (nested / "data" / "countries.mmdb").resolve()


def test_a_cold_key_in_the_hot_file_is_rejected(tmp_path: Path) -> None:
    """A port in the hot file does not start."""
    (tmp_path / "hot.yml").write_text("port: 8000\nstart: arena\nmax_connections: 64\nmax_newbies: 8\n", encoding="utf-8")
    (tmp_path / "cold.yml").write_text("port: 9000\nrooms: rooms.yml\nhelp: help.yml\nmessages_path: files\n", encoding="utf-8")
    path = tmp_path / "config.yml"
    path.write_text("hot: hot.yml\ncold: cold.yml\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="port belongs in the cold config."):
        load_config(path)


def test_a_hot_key_in_the_cold_file_is_rejected(tmp_path: Path) -> None:
    """A newbie cap in the cold file does not start."""
    (tmp_path / "hot.yml").write_text("start: arena\nmax_connections: 64\nmax_newbies: 8\n", encoding="utf-8")
    (tmp_path / "cold.yml").write_text("port: 9000\nrooms: rooms.yml\nhelp: help.yml\nmessages_path: files\nmax_newbies: 4\n", encoding="utf-8")
    path = tmp_path / "config.yml"
    path.write_text("hot: hot.yml\ncold: cold.yml\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_newbies belongs in the hot config."):
        load_config(path)


def test_a_single_file_config_refuses_to_start(tmp_path: Path) -> None:
    """A wrapper that still holds port does not start."""
    path = tmp_path / "config.yml"
    path.write_text("port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmessages_path: files\nmax_connections: 128\nmax_newbies: 20\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Config is missing hot."):
        load_config(path)


def test_an_extra_known_key_on_the_wrapper_names_its_file(tmp_path: Path) -> None:
    """Port beside hot and cold is a cold key, not an obsolete key."""
    path = tmp_path / "config.yml"
    path.write_text("hot: hot.yml\ncold: cold.yml\nport: 9000\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="port belongs in the cold config."):
        load_config(path)


def test_an_unknown_wrapper_key_refuses_startup(tmp_path: Path) -> None:
    """A leftover key on the wrapper does not start."""
    path = tmp_path / "config.yml"
    path.write_text("hot: hot.yml\ncold: cold.yml\nadmin: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="admin does not belong in config.yml."):
        load_config(path)


def test_load_port_rooms_and_start(tmp_path: Path) -> None:
    """A relative rooms path is resolved from the config file's directory."""
    path = write(tmp_path, "port: 9000\nrooms: hall/rooms.yml\nstart: arena\nhelp: notes/help.yml\n")
    loaded = load_config(path)
    assert loaded == Config(port=9000, rooms=(tmp_path / "hall" / "rooms.yml").resolve(), start="arena", help=(tmp_path / "notes" / "help.yml").resolve(), players=(tmp_path / "players").resolve(), ssh_host_key=(tmp_path / "ssh_host_key").resolve(), newbie_start="arena", config_dir=tmp_path.resolve(), messages_path=(tmp_path / "files").resolve(), hot_path=(tmp_path / "hot.yml").resolve(), cold_path=(tmp_path / "cold.yml").resolve())  # fmt: skip  # noqa: E501


def test_load_config_sets_config_dir_to_the_config_file_parent(tmp_path: Path) -> None:
    """config_dir is the resolved directory that contains the config file."""
    nested = tmp_path / "etc" / "chat"
    nested.mkdir(parents=True)
    path = write_at(nested, "config.yml", "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmax_connections: 128\nmax_newbies: 20\nmessages_path: files\n")
    assert load_config(path).config_dir == nested.resolve()


def test_missing_messages_path(tmp_path: Path) -> None:
    """A config without messages_path does not start."""
    (tmp_path / "hot.yml").write_text("start: reception\nmax_connections: 128\nmax_newbies: 20\n", encoding="utf-8")
    (tmp_path / "cold.yml").write_text("port: 9000\nrooms: rooms.yml\nhelp: help.yml\n", encoding="utf-8")
    path = tmp_path / "chat.yaml"
    path.write_text("hot: hot.yml\ncold: cold.yml\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Config is missing messages_path."):
        load_config(path)


def test_messages_path_must_be_a_string(tmp_path: Path) -> None:
    """A numeric messages_path is rejected."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmessages_path: 1\n")
    with pytest.raises(ConfigError, match="messages_path must be a string."):
        load_config(path)


def test_messages_path_files_sits_beside_the_config_file(tmp_path: Path) -> None:
    """A messages_path of files resolves beside the config file."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmessages_path: files\n")
    assert load_config(path).messages_path == (tmp_path / "files").resolve()


def test_an_empty_messages_path_is_the_config_directory(tmp_path: Path) -> None:
    """An empty messages_path resolves to the directory that contains the config file."""
    path = write(tmp_path, 'port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmessages_path: ""\n')
    assert load_config(path).messages_path == tmp_path.resolve()


def test_messages_path_parent_segment_walks_up(tmp_path: Path) -> None:
    """A messages_path of ../files is the files directory beside the config directory."""
    nested = tmp_path / "etc" / "chat"
    nested.mkdir(parents=True)
    path = write_at(nested, "config.yml", "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmax_connections: 128\nmax_newbies: 20\nmessages_path: ../files\n")
    assert load_config(path).messages_path == (tmp_path / "etc" / "files").resolve()


def test_an_absolute_messages_path_stays(tmp_path: Path) -> None:
    """A messages_path that starts with / stays that path after resolve."""
    directory = tmp_path / "elsewhere"
    path = write(tmp_path, f"port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmessages_path: {directory}\n")
    assert load_config(path).messages_path == directory.resolve()


def test_rooms_parent_segment_walks_up(tmp_path: Path) -> None:
    """Rooms ../rooms/system.yml resolves to rooms/system.yml beside the config directory."""
    nested = tmp_path / "etc" / "chat"
    nested.mkdir(parents=True)
    path = write_at(nested, "config.yml", "port: 9000\nrooms: ../rooms/system.yml\nstart: reception\nhelp: help.yml\nmax_connections: 128\nmax_newbies: 20\nmessages_path: files\n")
    assert load_config(path).rooms == (tmp_path / "etc" / "rooms" / "system.yml").resolve()


def test_shipped_config_sets_messages_path_to_files() -> None:
    """The shipped config reads login messages from config/files."""
    loaded = load_config(Path("config/config.yml"))
    assert loaded.messages_path == (Path("config") / "files").resolve()


def test_missing_help(tmp_path: Path) -> None:
    """The help file is not invented."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\n")
    with pytest.raises(ConfigError, match="missing help"):
        load_config(path)


def test_help_must_be_a_string(tmp_path: Path) -> None:
    """A numeric help value is rejected."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: 1\n")
    with pytest.raises(ConfigError, match="help must be a string"):
        load_config(path)


def test_absolute_help_path_is_kept(tmp_path: Path) -> None:
    """An absolute help path is not joined to the config directory."""
    help_path = tmp_path / "elsewhere" / "help.yml"
    path = write(tmp_path, f"port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: {help_path}\n")
    assert load_config(path).help == help_path


def test_missing_start(tmp_path: Path) -> None:
    """The starting room is not invented."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\n")
    with pytest.raises(ConfigError, match="start"):
        load_config(path)


@pytest.mark.parametrize("text", ["start: true\n", "start: Lobby\n", "start: 1\n"])
def test_start_must_be_a_room_id(tmp_path: Path, text: str) -> None:
    """The starting room is a room id, not a display name or a bool."""
    path = write(tmp_path, f"port: 9000\nrooms: rooms.yml\n{text}")
    with pytest.raises(ConfigError, match="room id"):
        load_config(path)


def test_missing_rooms(tmp_path: Path) -> None:
    """A config with only a port does not invent a rooms file."""
    path = write(tmp_path, "port: 9000\n")
    with pytest.raises(ConfigError, match="rooms"):
        load_config(path)


def test_rooms_must_be_a_string(tmp_path: Path) -> None:
    """A numeric rooms value is rejected."""
    path = write(tmp_path, "port: 9000\nrooms: 1\n")
    with pytest.raises(ConfigError, match="string"):
        load_config(path)


def test_missing_file(tmp_path: Path) -> None:
    """A path that does not exist raises ConfigError."""
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match="Cannot read"):
        load_config(missing)


def test_yaml_must_be_a_mapping(tmp_path: Path) -> None:
    """A list document is rejected."""
    path = write(tmp_path, "- 1\n")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load_config(path)


def test_missing_port(tmp_path: Path) -> None:
    """Neither listener is invented."""
    path = write(tmp_path, "{}\n")
    with pytest.raises(ConfigError, match="Config needs a port or an ssh_port"):
        load_config(path)


def test_ssh_port_only(tmp_path: Path) -> None:
    """SSH can listen when telnet is omitted."""
    path = write(tmp_path, "ssh_port: 9022\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    loaded = load_config(path)
    assert loaded.port is None
    assert loaded.ssh_port == 9022
    assert loaded.ssh_host_key == (tmp_path / "ssh_host_key").resolve()


def test_both_ports(tmp_path: Path) -> None:
    """Telnet and SSH can listen together."""
    path = write(tmp_path, "port: 9000\nssh_port: 9022\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    loaded = load_config(path)
    assert loaded.port == 9000
    assert loaded.ssh_port == 9022


def test_ports_must_differ(tmp_path: Path) -> None:
    """One number cannot be both listeners."""
    path = write(tmp_path, "port: 9000\nssh_port: 9000\n")
    with pytest.raises(ConfigError, match="port and ssh_port must be different"):
        load_config(path)


@pytest.mark.parametrize("text", ["ssh_port: '9022'\n", "ssh_port: 1.5\n", "ssh_port: true\n", "ssh_port:\n"])
def test_ssh_port_must_be_an_int(tmp_path: Path, text: str) -> None:
    """Strings, floats, bools, and null are not SSH ports."""
    path = write(tmp_path, text)
    with pytest.raises(ConfigError, match="ssh_port must be an integer"):
        load_config(path)


@pytest.mark.parametrize("text", ["ssh_port: 0\n", "ssh_port: 65536\n"])
def test_ssh_port_range(tmp_path: Path, text: str) -> None:
    """SSH ports outside 1 to 65535 are rejected."""
    path = write(tmp_path, text)
    with pytest.raises(ConfigError, match="ssh_port must be from 1 to 65535"):
        load_config(path)


@pytest.mark.parametrize("text", ["port: '9000'\n", "port: 1.5\n", "port: true\n"])
def test_port_must_be_an_int(tmp_path: Path, text: str) -> None:
    """Strings, floats, and bools are not ports."""
    path = write(tmp_path, text)
    with pytest.raises(ConfigError, match="integer"):
        load_config(path)


@pytest.mark.parametrize("text", ["port: 0\n", "port: 65536\n"])
def test_port_range(tmp_path: Path, text: str) -> None:
    """Ports outside 1 to 65535 are rejected."""
    path = write(tmp_path, text)
    with pytest.raises(ConfigError, match="1 to 65535"):
        load_config(path)


def test_obsolete_keys_load_and_a_missing_required_key_still_raises(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A leftover key is stored on the file that held it. A missing required key still stops startup."""
    path = write(
        tmp_path,
        "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nadmin: legacy\ncolour: 1\nbeta: 1\nZeta: 1\n",
    )
    loaded = load_config(path)
    assert loaded.obsolete_cold == ("admin", "beta", "colour", "Zeta")
    assert loaded.obsolete_hot == ()
    assert not hasattr(loaded, "obsolete_keys")
    assert capsys.readouterr().err == ""
    missing = write(tmp_path, "port: 9000\nrooms: rooms.yml\nhelp: help.yml\ncolour: 1\n")
    with pytest.raises(ConfigError, match="start"):
        load_config(missing)


def test_a_config_without_obsolete_keys_stores_an_empty_tuple(tmp_path: Path) -> None:
    """No leftover key stores an empty tuple on each file."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    loaded = load_config(path)
    assert loaded.obsolete_hot == ()
    assert loaded.obsolete_cold == ()


def test_an_obsolete_key_in_the_hot_file_is_stored_there(tmp_path: Path) -> None:
    """A leftover key in hot.yml is stored on the hot side only."""
    (tmp_path / "hot.yml").write_text("start: arena\nmax_connections: 64\nmax_newbies: 8\nadmin: 1\n", encoding="utf-8")
    (tmp_path / "cold.yml").write_text("port: 9000\nrooms: rooms.yml\nhelp: help.yml\nmessages_path: files\n", encoding="utf-8")
    path = tmp_path / "config.yml"
    path.write_text("hot: hot.yml\ncold: cold.yml\n", encoding="utf-8")
    loaded = load_config(path)
    assert loaded.obsolete_hot == ("admin",)
    assert loaded.obsolete_cold == ()


def test_stack_defaults_to_ipv4(tmp_path: Path) -> None:
    """No stack key keeps the IPv4 listener."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    assert load_config(path).stack == "ipv4"


def test_listen_hosts_follows_the_stack() -> None:
    """Each stack names the addresses bind should open."""
    assert listen_hosts("127.0.0.1", "ipv4") == ["127.0.0.1"]
    assert listen_hosts("::1", "ipv6") == ["::1"]
    assert listen_hosts(DEFAULT_HOST, "ipv6") == ["::"]
    assert listen_hosts(DEFAULT_HOST, "dual") == [DEFAULT_HOST, "::"]
    assert listen_hosts("::", "dual") == [DEFAULT_HOST, "::"]


def test_stack_must_be_a_known_name(tmp_path: Path) -> None:
    """Anything other than ipv4, ipv6, or dual is rejected."""
    path = write(tmp_path, "stack: both\nport: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    with pytest.raises(ConfigError, match="stack must be ipv4, ipv6, or dual"):
        load_config(path)


def test_stack_and_host_must_agree(tmp_path: Path) -> None:
    """An IPv6 host cannot sit on an IPv4 stack, and a specific IPv4 host cannot sit on IPv6."""
    path = write(tmp_path, "stack: ipv4\nhost: ::1\nport: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    with pytest.raises(ConfigError, match="host must be an IPv4 address"):
        load_config(path)
    path = write(tmp_path, "stack: ipv6\nhost: 127.0.0.1\nport: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    with pytest.raises(ConfigError, match="host must be an IPv6 address"):
        load_config(path)


def test_host_defaults_when_omitted(tmp_path: Path) -> None:
    """No host key binds every interface."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    assert load_config(path).host == DEFAULT_HOST


def test_host_is_an_ip_address(tmp_path: Path) -> None:
    """Host is the address passed to bind."""
    path = write(tmp_path, "host: 127.0.0.1\nport: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    assert load_config(path).host == "127.0.0.1"


@pytest.mark.parametrize("text", ["host: true\n", "host: localhost\n", "host: 1\n"])
def test_host_must_be_an_ip_address(tmp_path: Path, text: str) -> None:
    """A bool, a name, or a bare number is not a bind address."""
    path = write(tmp_path, f"{text}port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    with pytest.raises(ConfigError, match="IP address"):
        load_config(path)


def test_players_defaults_beside_the_config(tmp_path: Path) -> None:
    """No players key uses a players directory next to the config file."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    loaded = load_config(path)
    assert loaded.players == (tmp_path / "players").resolve()


def test_players_path_is_resolved(tmp_path: Path) -> None:
    """A relative players path is joined to the config directory."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nplayers: save/here\n")
    loaded = load_config(path)
    assert loaded.players == (tmp_path / "save" / "here").resolve()


def test_players_must_be_a_string(tmp_path: Path) -> None:
    """A numeric players value is rejected."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nplayers: 1\n")
    with pytest.raises(ConfigError, match="players must be a string"):
        load_config(path)


def test_login_timeout_defaults_to_3_minutes(tmp_path: Path) -> None:
    """Leaving the key out gives a connection 3 minutes."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    assert load_config(path).login_timeout == 3


def test_login_timeout_reads_a_whole_number_of_minutes(tmp_path: Path) -> None:
    """The template value is a positive number of minutes."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nlogin_timeout: 5\n")
    assert load_config(path).login_timeout == 5


@pytest.mark.parametrize("text", ["login_timeout: true\n", "login_timeout: 1.5\n", "login_timeout: soon\n"])
def test_login_timeout_must_be_an_integer(tmp_path: Path, text: str) -> None:
    """A boolean, a float, or a word is rejected."""
    path = write(tmp_path, f"port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n{text}")
    with pytest.raises(ConfigError, match="login_timeout must be an integer."):
        load_config(path)


def test_login_timeout_must_be_at_least_1(tmp_path: Path) -> None:
    """Zero minutes is rejected."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nlogin_timeout: 0\n")
    with pytest.raises(ConfigError, match="login_timeout must be at least 1."):
        load_config(path)


def test_background_defaults_to_false(tmp_path: Path) -> None:
    """Omitting background stays in the foreground."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    assert load_config(path).background is False


def test_background_true_and_false(tmp_path: Path) -> None:
    """True and false load as booleans."""
    on = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: true\n")
    assert load_config(on).background is True
    off = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: false\n")
    assert load_config(off).background is False


def test_background_must_be_a_boolean(tmp_path: Path) -> None:
    """A number is not a boolean."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nbackground: 1\n")
    with pytest.raises(ConfigError, match="background must be true or false"):
        load_config(path)


def test_allow_flags_default_to_true(tmp_path: Path) -> None:
    """Omitted allow flags are open."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    loaded = load_config(path)
    assert loaded.allow_newbies is True
    assert loaded.allow_residents is True


def test_allow_flags_true_and_false(tmp_path: Path) -> None:
    """True and false load as booleans, the same way background does."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nallow_newbies: false\nallow_residents: true\n")
    loaded = load_config(path)
    assert loaded.allow_newbies is False
    assert loaded.allow_residents is True


def test_allow_flags_must_be_booleans(tmp_path: Path) -> None:
    """A number is not a boolean."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nallow_newbies: 1\n")
    with pytest.raises(ConfigError, match="allow_newbies must be true or false"):
        load_config(path)


def test_load_banner_reads_the_file_beside_the_config(tmp_path: Path) -> None:
    """The banner is banner.msg in the messages directory."""
    files = tmp_path / "files"
    files.mkdir()
    (files / "banner.msg").write_text("Hello\n", encoding="utf-8")
    assert load_banner(files) == "Hello\n"


def test_load_banner_rejects_a_missing_file(tmp_path: Path) -> None:
    """A missing banner stops startup."""
    banner = tmp_path / "files" / "banner.msg"
    with pytest.raises(ConfigError, match=rf"Cannot read banner file {banner}: .+\."):
        load_banner(tmp_path / "files")


def test_load_gate_file_reads_utf8(tmp_path: Path) -> None:
    """A gate file is read from the messages directory."""
    files = tmp_path / "files"
    files.mkdir()
    (files / "full.msg").write_text("Hello\n", encoding="utf-8")
    assert load_gate_file(files, "full.msg", "full", "Full") == "Hello\n"


def test_load_gate_file_rejects_a_missing_file(tmp_path: Path) -> None:
    """A missing full file stops startup."""
    path = tmp_path / "files" / "full.msg"
    with pytest.raises(ConfigError, match=rf"Cannot read full file {path}: .+\."):
        load_gate_file(tmp_path / "files", "full.msg", "full", "Full")


def test_load_gate_file_rejects_text_that_is_not_utf8(tmp_path: Path) -> None:
    """A gate file that is not UTF-8 stops startup."""
    files = tmp_path / "files"
    files.mkdir()
    target = files / "no-more-newbies.msg"
    target.write_bytes(b"caf\xe9\n")
    with pytest.raises(ConfigError, match=rf"No-more-newbies file {target} is not valid UTF-8: .+\."):
        load_gate_file(files, "no-more-newbies.msg", "no-more-newbies", "No-more-newbies")


def test_refusal_text_matches_the_banner_without_the_name_prompt() -> None:
    """A refusal is the centered banner block and has no name prompt."""
    message = "The server is full.\nPlease try again later.\n"
    shown = refusal_text(message, 40)
    assert shown == welcome_text(message, 40).removesuffix(NAME_PROMPT)
    assert NAME_PROMPT not in shown
    assert shown.startswith("\n\n")
    assert shown.endswith("\n\n")


def test_load_banner_rejects_text_that_is_not_utf8(tmp_path: Path) -> None:
    """A banner that is not UTF-8 stops startup."""
    files = tmp_path / "files"
    files.mkdir()
    (files / "banner.msg").write_bytes(b"caf\xe9\n")
    banner = files / "banner.msg"
    with pytest.raises(ConfigError, match=rf"Banner file {banner} is not valid UTF-8: .+\."):
        load_banner(files)


def test_mfa_enabled_defaults_to_true_and_rejects_a_number(tmp_path: Path) -> None:
    """The MFA flag is on unless the file says false."""
    base = "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n"
    path = write(tmp_path, base)
    assert load_config(path).mfa_enabled is True
    path = write(tmp_path, f"{base}mfa_enabled: false\n")
    assert load_config(path).mfa_enabled is False
    path = write(tmp_path, f"{base}mfa_enabled: 1\n")
    with pytest.raises(ConfigError, match="mfa_enabled must be true or false"):
        load_config(path)


def test_country_lookup_needs_an_mmdb_path(tmp_path: Path) -> None:
    """Lookup is off unless the file asks, and then the database must be an mmdb."""
    base = "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n"
    path = write(tmp_path, base)
    loaded = load_config(path)
    assert loaded.country_lookup is False
    assert loaded.country_database is None
    path = write(tmp_path, f"{base}country_lookup: true\n")
    with pytest.raises(ConfigError, match="missing country_database"):
        load_config(path)
    path = write(tmp_path, f"{base}country_lookup: true\ncountry_database: countries/places.csv\n")
    with pytest.raises(ConfigError, match="must be an mmdb file"):
        load_config(path)
    path = write(tmp_path, f"{base}country_lookup: true\ncountry_database: countries/places.mmdb\n")
    loaded = load_config(path)
    assert loaded.country_lookup is True
    assert loaded.country_database == (tmp_path / "countries" / "places.mmdb").resolve()
    absolute = tmp_path / "elsewhere" / "geo.MMDB"
    path = write(tmp_path, f"{base}country_lookup: false\ncountry_database: {absolute}\n")
    loaded = load_config(path)
    assert loaded.country_lookup is False
    assert loaded.country_database == absolute.resolve()


def test_caps_are_required(tmp_path: Path) -> None:
    """Both caps must be present."""
    base = "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n"
    path = write(tmp_path, f"{base}max_newbies: 20\n", caps=False)
    with pytest.raises(ConfigError, match="Config is missing max_connections."):
        load_config(path)
    path = write(tmp_path, f"{base}max_connections: 128\n", caps=False)
    with pytest.raises(ConfigError, match="Config is missing max_newbies."):
        load_config(path)


def test_max_connections_must_be_a_positive_integer(tmp_path: Path) -> None:
    """Zero, a bool, and a word are not a connection cap."""
    base = "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmax_newbies: 1\n"
    path = write(tmp_path, f"{base}max_connections: 0\n", caps=False)
    with pytest.raises(ConfigError, match="max_connections must be at least 1."):
        load_config(path)
    path = write(tmp_path, f"{base}max_connections: true\n", caps=False)
    with pytest.raises(ConfigError, match="max_connections must be an integer."):
        load_config(path)


def test_max_newbies_must_not_exceed_connections(tmp_path: Path) -> None:
    """A newbie cap above the connection cap is rejected."""
    text = "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmax_connections: 128\nmax_newbies: 129\n"
    path = write(tmp_path, text, caps=False)
    with pytest.raises(ConfigError, match="max_newbies must be at most max_connections."):
        load_config(path)


def test_max_newbies_percentage_rounds_up(tmp_path: Path) -> None:
    """25% of 128 is 32 and 10% of 128 is 13."""
    base = "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmax_connections: 128\n"
    path = write(tmp_path, f"{base}max_newbies: 25%\n", caps=False)
    assert load_config(path).max_newbies == 32
    path = write(tmp_path, f"{base}max_newbies: 10%\n", caps=False)
    assert load_config(path).max_newbies == 13


@pytest.mark.parametrize("text", ["max_newbies: 0%\n", "max_newbies: 12.5%\n", "max_newbies: true\n", 'max_newbies: "20"\n'])
def test_max_newbies_rejects_a_bad_percentage(tmp_path: Path, text: str) -> None:
    """Zero percent, a fraction, a bool, and a quoted number are rejected."""
    base = "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nmax_connections: 128\n"
    path = write(tmp_path, f"{base}{text}", caps=False)
    with pytest.raises(ConfigError, match="max_newbies must be an integer or a percentage."):
        load_config(path)


def test_omitted_newbie_start_copies_start(tmp_path: Path) -> None:
    """No newbie_start key uses the start room."""
    path = write(tmp_path, "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\n")
    loaded = load_config(path)
    assert loaded.newbie_start == "reception"
    assert loaded.max_connections == 128
    assert loaded.max_newbies == 20


def test_newbie_start_must_be_a_room_id(tmp_path: Path) -> None:
    """A display name is not a newbie start room."""
    text = "port: 9000\nrooms: rooms.yml\nstart: reception\nhelp: help.yml\nnewbie_start: Lobby\n"
    path = write(tmp_path, text)
    with pytest.raises(ConfigError, match="newbie_start must be a room id of lowercase letters, digits, and underscores."):
        load_config(path)
