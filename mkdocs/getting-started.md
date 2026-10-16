# Getting Started

The server will not start without a config file. There is no default path.

## Install

```bash
pip install lupaxa-chat-server
```

Requires Python 3.10 or newer. AsyncSSH, bcrypt, colorama, cryptography,
maxminddb, PyYAML, and qrcode are the runtime dependencies.

`config/config.yml`, `config/rooms/system.yml`, and `config/help.yml` in this
repository are templates. After `pip install lupaxa-chat-server`, create
the config, the rooms file, and the help file yourself. The install does
not provide them.

## Run

From a checkout, setup runs once while the talker is stopped:

```bash
chat-server setup -c config/config.yml
chat-server -c config/config.yml
python -m lupaxa.chat_server -c config/config.yml
```

The files under `config/` are one example: an office that starts in
`reception`. Replace the rooms file and `start` with any layout.

```yaml
hot: config/hot.yml
cold: config/cold.yml
```

## Config

`config.yml` is only a pointer. It has two required keys, `hot` and `cold`, and both paths are read from the wrapper's directory.
The shipped files are `config/hot.yml` and `config/cold.yml` from there, so they live in a `config` directory below `config.yml`.
A key in the wrong file stops startup.
A key the server does not use does not stop startup. The value is ignored. On login, a Site Admin is told which file still has that old key. Every connected Site Admin hears the same note after a reboot.

A string, a float, a boolean, or a missing required key stops startup.
A path that is not a directory stops startup for `players`.

## Hot

These values can be reloaded.

| Key                | Required          | Meaning                                                                                  |
| ------------------ | ----------------- | ---------------------------------------------------------------------------------------- |
| `allow_newbies`    | No                | `false` blocks new players. Defaults to true. `reload config` overwrites a live `allow`. |
| `allow_residents`  | No                | `false` blocks residents. Defaults to true. `reload config` overwrites a live `allow`.   |
| `country_database` | When lookup is on | An `.mmdb` file. A relative path is read beside this file.                               |
| `country_lookup`   | No                | `true` looks up each connection. Defaults to false.                                      |
| `login_timeout`    | No                | Minutes to enter a room. Defaults to 3. A whole number of at least 1.                    |
| `max_connections`  | Yes               | Open connections at once, including the name prompt. A whole number of at least 1.       |
| `max_newbies`      | Yes               | Newbie cap. A whole number from 1 to `max_connections`, or a percentage such as `25%`.   |
| `mfa_enabled`      | No                | `false` skips the login code. Defaults to true. See [MFA](security/mfa.md).              |
| `newbie_start`     | No                | Room id for a newbie. Defaults to `start`. It must be a room in the rooms file.          |
| `start`            | Yes               | Room id where a saved player appears. It must be a room in the rooms file.               |

## Cold

A reboot reads `cold.yml` again, and the sockets that are already open stay.

| Key             | Required             | Meaning                                                                                |
| --------------- | -------------------- | -------------------------------------------------------------------------------------- |
| `background`    | No                   | `true` forks after the listen lines. Defaults to false.                                |
| `help`          | Yes                  | Help file. A relative path is read beside this file.                                   |
| `host`          | No                   | Bind address. Defaults to `0.0.0.0`.                                                   |
| `messages_path` | Yes                  | Directory for the three message files. A relative path is read beside this file.       |
| `players`       | No                   | Saved-player directory. Defaults to `players` beside this file. The server creates it. |
| `port`          | One of the two ports | Telnet port, 1 to 65535.                                                               |
| `rooms`         | Yes                  | Rooms file. A relative path is read beside this file.                                  |
| `ssh_port`      | One of the two ports | SSH port, 1 to 65535. Omit to leave SSH off. See [SSH](security/ssh.md).               |
| `stack`         | No                   | `ipv4`, `ipv6`, or `dual`. Defaults to `ipv4`.                                         |

## Paths

A relative path inside `hot.yml` or `cold.yml` is read from that file's directory. `hot` and `cold` themselves are read from the wrapper's directory. `../` walks up from the directory that applies.
The shipped value is `files`.

## Messages

`banner.msg`, `full.msg`, and `no-more-newbies.msg` are read from `messages_path`.
All three are required. A missing file or a file that is not UTF-8 stops
startup. They are centered like the login banner when a connection is
refused.
`reload messages` reads the three files again. `reload config` reads `hot.yml` again, and the five-minute check does too when `hot.yml` has changed.

## Ports and Stack

At least one of `port` and `ssh_port` is required. The same number cannot be
used for both. `ipv6` with host `0.0.0.0` listens on `::`. `dual` listens
on `0.0.0.0` and `::`, on two sockets. The IPv6 socket does not also accept
IPv4, so an IPv4 peer stays an IPv4 address.

`max_newbies` as a percentage rounds up. `25%` of 128 is 32. `10%` of 128
is 13.

## Super Admin

Zero super admin files stop startup. Stderr is
`There is no super admin. Run chat-server setup -c` and the config path.

One super admin file starts with no extra line.

More than one does not stop startup or reboot. Stderr prints
`There is more than one super admin:` and the file spellings, then
`Run chat-server setup -c` and the config path. Two names use `&`.
Three or more use commas, with `&` before the last name.

Create and maintain the account with `chat-server setup -c` and the
config path while the talker is stopped.

## Country Lookup

Loopback is Localhost (`XL`). A private address is Private Network (`XP`).
A public address uses the file. No record is Unknown (`XU`). The database
is not part of this package. If the file cannot be read, lookup is turned
off and startup says so. `connections` gains a Country column while lookup
is on.

## SSH

SSH listens on `ssh_port` when it is set. The first start writes
`ssh_host_key` beside the config file and reuses it. There is no SSH
password. The first prompt is still the chat name.

## Exit Codes

| Result                           | Where  | Exit |
| -------------------------------- | ------ | ---- |
| Missing `-c` / `--config`        | stderr | `2`  |
| Bad config, or the port is taken | stderr | `1`  |
| Clean shutdown                   |        | `0`  |

On startup the process prints the version and the listen address.
`background: true` forks after those lines. The parent exits 0. The child
keeps serving, and later output is discarded.
