# Getting Started

The server will not start without a config file. There is no default path.

## Install

```bash
pip install lupaxa-chat-server
```

Requires Python 3.10 or newer. AsyncSSH, bcrypt, colorama, cryptography,
PyYAML, and qrcode are the runtime dependencies.

`config/config.yml`, `config/rooms.yml`, and `config/help.yml` in this
repository are templates. After `pip install lupaxa-chat-server`, create
the config, the rooms file, and the help file yourself. The install does
not provide them.

## Run

From a checkout:

```bash
chat-server -c config/config.yml
python -m lupaxa.chat_server -c config/config.yml
```

```yaml
host: 0.0.0.0
port: 9000
# ssh_port: 9022
# mfa_enabled: true
rooms: rooms.yml
start: reception
help: help.yml
players: players
admin: admin
login_timeout: 3
```

`port` is the telnet port. `ssh_port` is the SSH port. Either may be
omitted. At least one is required. A present port is an integer from 1
to 65535. The same number cannot be used for both. `rooms`, `start`, and
`help` are required. `host` is the IP address to bind. Leave it out and
the server binds `0.0.0.0`. `rooms` is a path to the rooms file. A
relative path is read from the directory that contains the config file.
`start` is the room id where a new player appears. It must be a room in
that file. `help` is a path to the help file. A relative path is read from
the directory that contains the config file. `players` is the directory
that holds one file per saved player. A relative path is read from the
directory that contains the config file, and leaving it out uses
`players` beside that file. The server creates the directory when it is
missing, and a path that is not a directory stops startup. `admin` is the
name of the admin. It defaults to `admin` and must be a name that can be
saved. `login_timeout` is how many minutes a connection may take to enter
a room. It defaults to 3 and must be a whole number of at least 1.

SSH listens on `ssh_port` when it is set. The first start writes
`ssh_host_key` beside the config file and reuses it. There is no SSH
password. The first prompt is still the chat name. See [SSH](security/ssh.md).

MFA is on unless `mfa_enabled` is false. A saved player with a secret is
asked for a code after the password. See [MFA](security/mfa.md).

The files under `config/` are one example: an office that starts in
`reception`. Replace the rooms file and `start` with any layout. A string,
a float, a boolean, a missing required key, or any other key stops
startup.

| Result                           | Where  | Exit |
| -------------------------------- | ------ | ---- |
| Missing `-c` / `--config`        | stderr | `2`  |
| Bad config, or the port is taken | stderr | `1`  |
| Clean shutdown                   |        | `0`  |

On startup the process prints the version and the listen address.
`background: true` forks after those lines. The parent exits 0. The child
keeps serving, and later output is discarded.
