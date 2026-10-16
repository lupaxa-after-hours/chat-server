# Network

The socket loop lives on `ChatServer`. It is not on `Server`. A command asks the server to send or disconnect. The server does the bytes.

## The Loop

`serve_forever` waits on one selector.
A telnet accept builds a `Client`, stores it in `clients`, registers the socket, and sends DO NAWS.
The welcome waits for that window size, a refusal, the first typed bytes, or half a second, so the banner uses the reported width.
SSH sessions are accepted on another thread, queued, and then registered on this same loop as clients with `ssh` set.
Both kinds of connection then share the read path.

## A Line In

A readable socket is read, counted, and passed through `strip_telnet`.
That removes telnet negotiation and, when the client reports a window size, stores the columns and rows on the client.
The remaining bytes are edited in the client's buffer.
Enter completes a line. `handle_line` strips the return and calls `process_input`.

Backspace and echo for an SSH terminal are handled here, before any command sees the text. Password stages also turn local echo off from `send`, not from the command.

## A Line Out

`send` encodes the text, appends it to `client.outgoing`, and calls `flush`. `flush` writes until the buffer is empty or the socket would block. A client that cannot be written is dropped.

`drop` writes a saved logout when the client has a player, removes the client, closes the socket, and, when asked, tells the room. A reconnect does not use `drop` for the old socket. `replace_socket` closes that socket without a logout.

## Which Addresses Listen

`stack` on the config is `ipv4`, `ipv6`, or `dual`. `listen_hosts` in `config.py` turns `host` and `stack` into the addresses to bind.
`ipv4` binds that host. `ipv6` binds it when it is IPv6, or `::` when the host is `0.0.0.0`. `dual` binds one IPv4 address and one IPv6 address.

`bind_listener` opens one socket. An IPv6 socket sets `IPV6_V6ONLY` before bind, so it does not also accept IPv4.
`start` keeps every telnet socket in `listeners` and the first in `listener`. SSH does the same with `ssh_listen_socks`.
`serve_forever` accepts when the readable socket is any telnet listener.
`SSHListener` in `sshd.py` calls `asyncssh.listen` once per socket, on one loop.

A failed bind closes the sockets already opened and stops startup.

`peer_address` is what gets stored. A zone id is removed. An IPv4-mapped IPv6 address is stored as the IPv4 address. A reboot snapshot records every listener. An older snapshot that has only one socket still adopts that socket.

## Hostname and Country

`start_hostname_lookup` runs on a daemon thread named `hostname`. It calls `lookup_hostname`.
When `country_lookup` is on it also calls `country_of`.
The selector applies the result in `apply_hostnames` when it arrives within 2 seconds, then `remember_address` can write it to the player file.

`country_of` does not ask the database for a loopback or a private address.
Loopback is Localhost, `XL`. RFC 1918 and IPv6 unique-local are Private Network, `XP`.
Anything else is a lookup in the MMDB reader. No record is Unknown, `XU`.
The reader is opened on the first lookup and closed in `close`.
`ipinfo` is an admin command and is omitted from the catalogue when `country_lookup` is false.
It reads the same reader with `get_with_prefix_len` and prints every scalar the record holds, plus the network prefix as the IP block.
Loopback and private addresses skip the reader, the same way `country_of` does.
A database that cannot be read at startup sets `ChatServer.country_lookup` to false. The config value is left as it was.
