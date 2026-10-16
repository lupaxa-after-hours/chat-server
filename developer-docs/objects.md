# Objects

One process has one server. Every connection hangs off that object. A person exists only after login, and only on their connection.

## One Server

`cli.py` loads the config, the room layout, and the help book, then constructs one `ChatServer`.
`start` binds one telnet socket and one SSH socket for each address `listen_hosts` returns.
Those lists are `listeners` and `ssh_listen_socks`. The first of each list is also `listener` and `ssh_listen_sock`.
`serve_forever` is the loop that accepts sockets.

The server keeps the live connections in `clients`, a dict keyed by socket. It also keeps `config`, `layout`, `help`, and `roster_dir` (the `players/` directory).

## Server Is a Type

`Server` in `server.py` is a `typing.Protocol`. Nothing constructs a `Server`, and `ChatServer` does not subclass it.

The protocol is the list of names command code may use: `send`, `drop`, `clients`, `layout`, and the rest written on the class.
The type checker treats the server as a `Server` from the moment it is passed into login.
At runtime the object is still the full `ChatServer`.
Methods that read sockets, such as `accept`, `strip_telnet`, `take_key`, and `flush`, are not on `Server`, so a command cannot call them through `server`.

The link is one call. When a line is ready, `handle_line` passes the server itself:

```python
process_input(self, client, line)
```

`process_input` names that argument `server`. The same object is handed to the command.

## Client, Player, and Saved Player

A `Client` is one accepted socket. It exists from accept until `drop`.
It holds the buffers, the address, the auth stage, and, once login finishes, a `Player`. Until then `client.player` is `None`.
After the hostname thread finishes it can also hold `hostname`, `country_name`, and `country_code`.

`enter` in `entry.py` creates the `Player` and stores it on the client.
The player has the name, the room, the rank, the session counters, and the enter, login, and logout sentences.
`client.name` and `client.room` read through to that object.
`sync_country` copies a country from the client onto the player, or the other way when the client does not have one yet.

A `SavedPlayer` is the YAML file under `players/`, one file per saved name.
Login copies rank, prompt, sentences, plan, country, and total seconds onto the `Player`. Logout writes them back.
A newbie has a `Player` and no file. Empty country fields are left out of the file.

```text
ChatServer          one object; this is the Server
  clients           socket -> Client
    Client          one connection
      player        Player or None
        Player      the person in a room
```

## The Call

`handle_line` does not interpret the text. It calls `process_input`. That function sends the line to login while `client.player` is `None`, or while auth is a password or MFA stage. Otherwise it calls `process_commands`, which ends with:

```python
command.function(server, client, command, text)
```

`server` is the `ChatServer`. `client` is the connection that typed the line. `command` is the catalogue row. `text` is the rest of the line, or `None`.
