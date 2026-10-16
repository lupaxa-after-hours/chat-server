# Reference

## Config Keys

The same list is split into hot and cold in [Getting Started](getting-started.md).

`config.yml` itself has required keys `hot` and `cold`.

`ssh_host_key` is written beside the config when SSH is on. It is not a key.
A bad value or a missing required key stops startup. A key the server does not use does not stop startup. A key in the wrong file stops startup.

| Result                           | Where  | Exit |
| -------------------------------- | ------ | ---- |
| Missing `-c` / `--config`        | stderr | `2`  |
| Bad config, or the port is taken | stderr | `1`  |
| Clean shutdown                   |        | `0`  |

## Hot

| Key                | Required          | Default     |
| ------------------ | ----------------- | ----------- |
| `allow_newbies`    | No                | true        |
| `allow_residents`  | No                | true        |
| `country_database` | When lookup is on |             |
| `country_lookup`   | No                | false       |
| `login_timeout`    | No                | 3           |
| `max_connections`  | Yes               |             |
| `max_newbies`      | Yes               |             |
| `mfa_enabled`      | No                | true        |
| `newbie_start`     | No                | `start`     |
| `start`            | Yes               |             |

## Cold

| Key             | Required             | Default                     |
| --------------- | -------------------- | --------------------------- |
| `background`    | No                   | false                       |
| `help`          | Yes                  |                             |
| `host`          | No                   | `0.0.0.0`                   |
| `messages_path` | Yes                  |                             |
| `players`       | No                   | `players` beside this file  |
| `port`          | One of the two ports |                             |
| `rooms`         | Yes                  |                             |
| `ssh_port`      | One of the two ports | SSH off when omitted        |
| `stack`         | No                   | `ipv4`                      |

## Names

A name is 3 to 20 ASCII letters or digits, and it cannot start with a
digit. `me`, `who`, `quit`, `ssh`, `telnet`, and `system` are reserved in any
case. A name is also refused when its house file would be the rooms file or
the help file. An unsaved name is unique in lowercase. The spelling they type
is the one shown. A saved name must be typed in the spelling it was saved with.

## Ranks

Newbie, then resident, then SU Channel, then super user, then admin, then coder, then Super Admin.
What each rank can run is on [Players](usage/players.md).

`roles` on a player file is a non-negative integer. `1` is the Site Admin role. `broadcast`, `reload`, `shutdown`, `reboot`, `network`, and `system` are rank Coder or that role.

## Public Names

`load_config`, `Config`, `ChatServer`, `ChatError`, `ConfigError`,
`ServerError`, `__version__`, and `get_version()`.

## Development

These pages are for someone using the talker. If you are changing the
server, adding a command, a config key, or a role, start in
`developer-docs/` in the repository. That folder is not part of this site.

```bash
make init
make python-check
```

`make python-check` runs ruff, mypy, and pytest. `make init` is required
once, so the Python makefile targets exist. The site is built with
`mkdocs build` from the pins in `requirements.txt`. The pages are the
files under `mkdocs/`.
