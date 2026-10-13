# Reference

## Config Keys

| Key             | Required                         | Meaning                                                                 |
| --------------- | -------------------------------- | ----------------------------------------------------------------------- |
| `host`          | No                               | Bind address. Defaults to `0.0.0.0`.                                    |
| `port`          | One of `port` or `ssh_port`      | Telnet port, 1 to 65535.                                                |
| `ssh_port`      | One of `port` or `ssh_port`      | SSH port, 1 to 65535. Omit to leave SSH off.                            |
| `ssh_host_key`  | No                               | Ed25519 key beside the config. Created on the first SSH start.          |
| `rooms`         | Yes                              | Rooms file. A relative path is read beside the config.                  |
| `start`         | Yes                              | Room id where a new player appears.                                     |
| `help`          | Yes                              | Help file. A relative path is read beside the config.                   |
| `players`       | No                               | Saved-player directory. Defaults to `players` beside the config.        |
| `admin`         | No                               | Admin name. Defaults to `admin`. Must be a name that can be saved.      |
| `login_timeout` | No                               | Minutes to enter a room. Defaults to 3. A whole number of at least 1.   |
| `background`    | No                               | `true` forks after the listen lines. Defaults to false.                 |

The same number cannot be used for `port` and `ssh_port`. A string, a
float, a boolean, a missing required key, or any other key stops startup.

| Result                           | Where  | Exit |
| -------------------------------- | ------ | ---- |
| Missing `-c` / `--config`        | stderr | `2`  |
| Bad config, or the port is taken | stderr | `1`  |
| Clean shutdown                   |        | `0`  |

## Names

A name is 3 to 20 ASCII letters or digits, and it cannot start with a
digit. `me`, `who`, and `quit` are reserved in any case. An unsaved name
is unique in lowercase. The spelling they type is the one shown. A saved
name must be typed in the spelling it was saved with.

## Ranks

Newbie, then resident, then super user, then admin.

A newbie is not saved. A resident is saved and has a password. A super
user can save a newbie, delete a saved player, disconnect a lower rank,
and use the staff channel. The admin can shut the server down, promote a
resident, and demote a super user. The admin cannot be removed.

`lsu` and `staff` are for every rank. `shout` and `prompt` start at
resident. `connections` starts at super user.

## Public Names

`load_config`, `Config`, `ChatServer`, `ChatError`, `ConfigError`,
`ServerError`, `__version__`, and `get_version()`.

## Development

```bash
make init
make python-check
```

`make python-check` runs ruff, mypy, and pytest. `make init` is required
once, so the Python makefile targets exist. The site is built with
`mkdocs build` from the pins in `requirements.txt`. The pages are the
files under `mkdocs/`.
