# Login

A new socket is a `Client` with no player. Login is a series of auth stages on that client.
A `Player` is created only when the person is allowed into the start room.

What a player is told, and which commands each rank may use, is in [Players](../mkdocs/usage/players.md). This page is the path through the code.

## Before a Room

A new client starts at auth stage `name`.
`process_input` in `login.py` sends the line to `process_login` while `client.player` is `None`, or while `client.auth` is a password, code, or confirm stage.
After the person is in a room, the same function runs commands instead.

Accept starts `start_hostname_lookup` before the name is known. The address stored on the client is already unwrapped, so the lookup and the country use that same string.

A name with no file is a newbie. Staff hear `[ Newbie Alert ]` at that moment, with the hostname known so far.
An explicit no at the disclaimer tells staff the newbie quit.
`enter` builds a `Player` in the config start room, stores it on the client, and tells staff the newbie is in the main room.
Those three lines go through `broadcast_staff`. They use the staff colour and do not include `< SU >`.
A saved name asks for a password. The third wrong password disconnects.
When MFA is enabled, a saved player who has a secret is asked for a code after the password.
SU Channel and above must enrol when they have no secret yet.

The held name, the saved record, and the password in progress live on the client (`pending_name`, `pending_saved`, and the other `pending_` fields). They are cleared when the person enters or when login starts over.

## Ranks and Files

The ranks are newbie, resident, su channel, super user, admin, coder, and super admin.
The file words are `newbie`, `resident`, `su_channel`, `super_user`, `admin`, `coder`, and `super_admin`.
The super admin is the one saved player whose rank is `super_admin`, created
with `chat-server setup -c` and the config path while the talker is stopped.

Each saved person is one YAML file in the `players/` directory, named by the lowercase name, such as `players/bea.yml`.
The file holds the saved spelling, the rank, the password hash, the prompt, the total connected seconds, the logout time, the room sentences, the description, the plan, the last address, and a country name and code when one was stored.
The password is never stored as typed. `roster_dir` on the server is that directory.

A newbie is not written there. `resident` asks a connected newbie for a password and then saves them.

## Reconnect

A saved player who logs in while that name is already in a room does not get a second `Player`.
`replace_socket` keeps the existing client and player, moves the new socket onto them, and closes the old one.
The room hears a reconnect, not a quit and a join. Logout is not written.
