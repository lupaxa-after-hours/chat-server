# Commands

A logged-in line is matched against one catalogue, checked, then passed to a function with four arguments.

## The Catalogue

`Command` and `ensure_commands` live in `commands.py`.
`COMMANDS`, `commands_by_name`, `command_aliases`, and `LOADED` live in `globals.py`.
Importing `chatserver.py` calls `ensure_commands`.
That loads `newbie`, `resident`, `password`, `admin`, `su`, and `coder` once.
Each module appends `Command` rows to `COMMANDS`.
When MFA is enabled, `ChatServer` also registers the `mfa` command.
`reboot` lives in `coder.py` and the snapshot lives in `reboot.py`.

Two lookups are built from the list:

- `by_name()` is every command and alias, keyed by the word the player types.
- `aliases()` is only the aliases, longest name first.

The dict and the tuple behind those functions are `commands_by_name` and `command_aliases`. The functions exist so callers do not touch the dict before it has been filled.

A row is `Command(name, function, is_alias, syntax, needs_space, rank)`. The catalogue stores the tail and the player sees `shown_syntax`.
The missing-text reply and a bad-arguments reply both use it. `command.syntax` on its own is the tail. `say` is a normal command. `'` is an alias for the same function:

```python
Command("say", command_say, False, "<message>"),
Command("'", command_say, True, "<message>"),
```

`shout` also sets `rank` to resident.

## Rank and Role

`Command.role` defaults to `0`. `Command.combination` is `and` or `or` and is read only when
`role` is not `0`. The help page shows one Permissions line: the rank when `role` is `0`,
otherwise the rank, `command.combination`, and the role label.
`command_allowed` is the test for running a command, for the open
`commands` list, and for help visibility. `broadcast`, `reload` and its subcommands, `shutdown`, `reboot`, `network`, and `system` live in `coder.py`. They are rank Coder or `Roles.SITE_ADMIN`.

## Matching a Line

`match_command` in `parse.py` runs two passes.

Pass 1 walks aliases that do not set `needs_space` and tests `line.startswith(name)`.
The rest of the line, with leading spaces removed, is `text`.
`'hello there` matches `'`. `:waves` matches `:`.
Longest-first matters so a longer mark is tried before a shorter one that is its prefix.

Pass 2 splits the line once on whitespace and looks the first word up in `by_name()`, ignoring case. `SAY hello there` matches `say` with text `hello there`. `look` matches with text `None`.

`x` sets `needs_space`, so pass 1 skips it. `x Bea` is examine. `xBea` is not: the word `xBea` is not a command.

## The Gates

`process_commands` then decides whether to call the function.

1. A blank line returns immediately. Idle is left as it was.
2. Any other line clears `idle_seconds` before the command runs. Examine sees that zero when you look at yourself.
3. `mfa` is treated as unknown when `server.mfa_enabled` is false.
4. No match sends `No such command 'word'`.
5. If the player's rank is below `command.rank`, the reply is the same not-found line. A newbie who types `shout` is not told that the command exists.
6. If the command has a `syntax` and `text` is missing, the reply is `Syntax: ...`, unless the function is on the bare-ok list (`save`, `time`, `colour`, and the other commands that may be typed with no extra text).
7. When the command has subcommands, this is a parent. Missing text already replied with `Syntax: ...` in step 6. Otherwise `run_subcommand` matches the next word against that parent's children only. The parent's function is not called.
8. Otherwise it calls `command.function(server, client, command, text)`.

`run_subcommand` then decides the child:

1. No child sends `No such subcommand 'Foo'.` The word keeps the case the player typed.
2. A child above the player's rank sends `No such subcommand 'foo'.` using the registered name.
3. Off duty is checked again. A child above resident rank sends `You are off duty.` A resident child still runs.
4. A child with `syntax` and no further text sends `Syntax: ...`, unless its function is on the bare-ok list.
5. Otherwise it calls the child's function with the text after the child word.

The function does not repeat that missing-text check. `tell Bea` gets past the gate because `text` is `Bea`. That is not a name plus a message, so `command_tell` prints `shown_syntax(command)` itself.

## The Four Arguments

| Argument  | What it is                                                      |
| --------- | --------------------------------------------------------------- |
| `server`  | The running `ChatServer`, typed as `Server`.                    |
| `client`  | The connection. The person is `client.player`.                  |
| `command` | The matched `Command`.                                          |
| `text`    | Everything after the command word, or `None`.                   |

`command` stays in the signature because every command is called the same way. Read `shown_syntax(command)` when the rest of the line has the wrong shape. A function that does not read it leaves the name unused.

`server.send(client, text)` queues text for that connection. `server.drop(client, announce=True)` closes it and can tell the room.
Other people are found through `get_clients`, which returns connections in login order. `alphabetical=True` sorts them by name.

## Add a Command

Put the function in a module `ensure_commands` already imports (`newbie.py`, `resident.py`, `su.py`, `admin.py`, or `coder.py`, matching the rank). The signature is fixed:

```python
def command_wave(server: Server, client: Client, command: Command, text: str | None) -> None:
    """Send a wave to the player's room."""
    if not server.send(client, "You wave.\n"):
        return
    broadcast_room(server, client.room, f"- {client.name} waves.\n", skip=client)
```

Register it next to the other rows:

```python
Command("wave", command_wave, False, "<message>"),
```

A parent passes `subcommands=()`. `ensure_commands` adds the `commands` child, which lists the children this rank may use.
Children are registered with `register_subcommands`. One call takes the parent name and one or more commands. A later call for the same parent appends.
`ensure_commands` attaches the queued children after any children already in the tuple. The player-facing list stays alphabetical.
A child is another `Command` in that tuple. Its rank may be higher than the parent.
It may not be lower, it may not be named `commands`, and it may not own children.

`reload` is a parent. `reload messages` re-reads `banner.msg`, `full.msg`, and `no-more-newbies.msg` from `messages_path`.
The five-minute check does the same when a stamp changes, including `hot.yml`. `reload all` runs config, then rooms, then help, then messages.

Set `rank` when a newbie must not see it. Add an alias as another row with the same function and `is_alias` true. Set `needs_space` when the alias must be a whole word, as `x` is for examine.

The function may call only what `Server` lists. It must not call `strip_telnet`, `take_key`, or `flush`. Those stay on the server.

After the function is registered, update the places a player or a later edit will look:

| Also update                                         | Why                                                                 |
| --------------------------------------------------- | ------------------------------------------------------------------- |
| `config/help.yml`                                   | `help <name>` reads this file. Put the topic in alphabetical order. |
| `mkdocs/usage/commands.md`                          | The command table is alphabetical. Each row is 96 characters.       |
| The rank or role page, if it changes who can run it | Players read `mkdocs/usage/players.md`.                             |
| A test that runs the command                        | A new command with no test will not stay honest.                    |

A role on the command is `role=int(Roles.SITE_ADMIN)` and `combination="or"` or `"and"`. See [Roles](roles.md). A new config key is [Config](config.md), not a command.

## Chains

`tell`, `remote`, and `rthink` call `deliver` in `chain.py`.
A chain is an id, the display names, and the UTC time of the last message.
`ChatServer.chains` holds them.
The reboot snapshot stores that list. A shutdown does not.
`drop` removes the person and tells the others, including when three or more remain.
`replace_socket` does not.
When two chains hold the same people, the higher id is deleted.
`chains leave`, `chains remove`, and `chains delete` use that same close rule.
`process_timer` deletes a chain with no message for two hours.

## Duty and the Screen Lock

`off_duty` and `on_duty` live on the player for this session and are kept in the reboot snapshot.
They are not written to the player file.
`broadcast_staff` and `broadcast_admin` skip someone who is off duty.
`on_duty` asks for the saved password before clearing the flag.
A miss tells the staff channel they got their password wrong and failed to return to duty.
`off_list` and `on_list` only change who `lsu` shows. Off Duty is the label when both are set.

`screenlock` sets `screen_locked` and `screen_lock_reason` on the player for this session.
Those fields are kept in the reboot snapshot and are not written to the player file.
`output_hidden` drops every queued line except the unlock prompt and a wrong password.
Staff who lock are set off duty. Unlock clears that and tells the staff channel.
A direct message to a locked player also sends `[ Screen Locked ]`.
`send_auto_reply` returns without sending when the target's screen is locked. `screenlock` sends `cls()` through `send_lock_line` before the unlock prompt. Unlock sends `Screen is now unlocked.` and then the prompt.
`send_auto_reply` names the player: `[ Auto Reply from Ada ]`.

## The Commands List

`ipinfo` is registered as an admin command and removed in `process_commands` when `country_lookup` is false.
`command_words` and `visible_help` hide it the same way. `commands` with no word uses `command_words`. One ASCII letter keeps the names from that list whose first character is that letter, in either case. `aliases` keeps the aliases.
`su` lists the staff commands this rank can run. SU Channel and above can run it, including while off duty.
`admin` lists commands stored at admin rank. An admin, a coder, and the super admin can run it, including while off duty.
`coder` lists commands stored at coder rank. A coder and the super admin can run it, including while off duty.
A held role lists every top-level command with that role bit, not cut down by the player's rank. Only a holder can run it, including while off duty. An empty list says `There are no Site Admin commands.` Reload's children are not in the list.
An empty letter says `No commands found beginning with 'Z'.` The letter keeps the case the player typed.
An empty alias list says `There are no aliases.` An empty super-user list says `There are no Super User commands.` An empty admin list says `There are no Admin commands.` An empty coder list says `There are no Coder commands.`
The syntax line grows with rank, then with a held role:

```text
A newbie or a resident sees Syntax: commands [<letter>|aliases].
SU Channel or a super user sees Syntax: commands [<letter>|aliases|su].
An admin sees Syntax: commands [<letter>|aliases|su|admin].
A coder or the super admin sees Syntax: commands [<letter>|aliases|su|admin|coder].
A resident who holds site admin sees Syntax: commands [<letter>|aliases|site_admin].
SU Channel or a super user who holds it sees Syntax: commands [<letter>|aliases|su|site_admin].
An admin who holds it sees Syntax: commands [<letter>|aliases|su|admin|site_admin].
A coder or the super admin who holds it sees Syntax: commands [<letter>|aliases|su|admin|coder|site_admin].
```

`ipinfo_fields` builds the examine rows.

## Houses and Grab

`home` is a resident command. The room id is `<name>_home` in `config/rooms/<name>.yml`, loaded while the resident is inside it. `display_name` strips the owner prefix.
The player file stores `home` and `home_login` only when they are true. A fresh login with `home_login` enters that room. Reconnect keeps the current room.
`join` refuses any room in someone else's house. `map` draws the house.

`grab <name>` is a super-user command. It pulls a lower rank into the caller's system room.
The target is told <code> -=> &lt;name&gt; stretches out and grabs you !!!!!!</code> and then the brief.
The room they leave hears `<target> is grabbed by <caller>.` The caller's room hears the target's enter sentence.
Same rank or higher is refused. A house or The Void is not a landing room. A full room is refused.
An empty house is dropped. There is no staff-channel line.
