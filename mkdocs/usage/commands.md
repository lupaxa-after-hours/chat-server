# Commands

The first word is the command. Aliases may sit against the text, so
`'hello` is say and `!hello` is shout. `x` needs a space, so `x Bea` is
examine and `xBea` is not. A command name still needs a space before its
text. Matching is case-sensitive. `commands` lists every alias first, then
every command name, each group in alphabetical order. It and `help` show
only what the player's rank may use. A command above the player's rank
replies `No such command '<word>'`, the same as an unknown word.

`say`, `emote`, and `think` go to the other people in the same room, and
the sender gets their own copy. Others see a line starting with `-` and your
name. `shout` goes to every player who is in a room. They see a line starting
with `!` and your name. `tell <name> <message>` goes to that one player, in
whichever room they are in. They see a line starting with `>`. `.` and `>` are
the same command. `.Bea hello` and `> Bea hello` are tell. `remote <name>
<message>` emotes to that one player. You see `You emote: Ada smiles to Bea`.
They see a line starting with `>` and `Ada smiles to you`.
`,` and `<` are the same command. `,Bea smiles` and `< Bea smiles` are
remote. Naming yourself on `tell` or `remote` replies
`Talking to yourself is the first sign of madness.` A player who has not
joined a room is not found.

`who` lists each player who is in a room, in a frame with the name, rank,
room players see, status, and an idle clock after 10 seconds. When nobody
is in a room it replies `-=> There is currently no one connected`. `w` is
the same command. `wplease` is not. `status` sets the status others see on
examine, `who`, and `lsu`. At most 20 characters. `status` on its own
clears it. It is not saved. `auto_reply` sets a line returned to anyone who
`tell`s or `remote`s you. `auto_reply` on its own clears it. It is not saved.
After a successful tell or remote, the sender sees `[ Auto Reply ]` and your
message. `connections` lists every open socket for super
users and the admin. The columns are Name, Hostname, IP Address, and Type.
A socket with no name yet shows `<pending>`. A typed name is shown,
including during the password. Type is `telnet` or `ssh`. `conns` is the
same command.

`look` shows the description, who else is there, and the exits as `direction -> room`.
`examine <name>` shows a framed profile. Someone in a room gets their
rank, then status when one is set, this login, total login, and idle.
Idle of 10 seconds or less says `Not idle at all`. `me` is yourself. A name
matches in any case, including
a connected newbie. A saved player who is not here gets the logout frame.
Examining yourself also adds the terminal size. A super user or the admin
also sees `Logged in from` or `Last from`. `recap <name>` changes the
capitalisation of your own name.

`alarm <minutes>` counts down in seconds and tells you when it reaches
zero. Typing a command sets that idle time back to zero. After each
command, the player gets a prompt, `What? >`, including when the command
failed. `prompt <prompt>` replaces that line. A custom prompt is stored
with a space at the end. It is for residents and above, and a resident's
prompt is kept for the next login. `entermsg <sentence>` sets the line
others hear when you enter a room. It is for residents and above. The
default is `arrives.` The sentence is at most 80 characters. Bare
`entermsg` clears a saved line. `colour on`
and `colour off` turn colour on and off. `colour` on its own tells you
which it is. `color` is the same command. It is on by default, a newbie
can use it, and a saved player keeps the choice. `description`
sets the one line others see on examine. A newbie starts as
`I am new here`, and that line is cleared when they become a resident.
`help` replies `Syntax: help <topic>`. `?` is the same command with no
space after it, so `?say` is `help say`. `help <topic>` shows a centered
page, with a blank line above the box and below it. A command page includes
the syntax. A topic that is not a command shows the paragraph, and See Also
when one is set. A missing topic replies `help for <topic> not found`.

| Command       | Aliases           | Rank       | Effect                                      |
| ------------- | ----------------- | ---------- | ------------------------------------------- |
| `say`         | `'`, `"`, `` ` `` | Newbie     | Say to the room                             |
| `emote`       | `:`, `;`          | Newbie     | Emote to the room                           |
| `entermsg`    |                   | Resident   | Set the arrive sentence                     |
| `think`       | `~`               | Newbie     | Think to the room                           |
| `shout`       | `!`               | Resident   | Shout to every room                         |
| `tell`        | `.`, `>`          | Newbie     | Tell one player                             |
| `remote`      | `,`, `<`          | Newbie     | Emote to one player                         |
| `go`          |                   | Newbie     | Move through an exit                        |
| `join`        |                   | Newbie     | Move to that player's room                  |
| `main`        |                   | Newbie     | Move to the start room                      |
| `map`         |                   | Newbie     | 5 by 5 map when it fits, else 3 by 3        |
| `mfa`         |                   | Resident   | Set up or remove MFA                        |
| `look`        |                   | Newbie     | Description, exits, and who else here       |
| `password`    |                   | Newbie     | Replace your saved password                 |
| `who`         | `w`               | Newbie     | Names, ranks, rooms, status, and idle       |
| `examine`     | `x`               | Newbie     | Framed profile, here or logged out          |
| `recap`       |                   | Newbie     | Change the capitalisation of your name      |
| `alarm`       |                   | Newbie     | Countdown in minutes                        |
| `prompt`      |                   | Resident   | Set the line you type at                    |
| `save`        |                   | Resident   | Write the player file now                   |
| `retire`      |                   | Resident   | Delete your saved account                   |
| `term_width`  |                   | Newbie     | Set this connection's frame width           |
| `time`        |                   | Newbie     | Current time and server uptime              |
| `help`        | `?`               | Newbie     | List commands, or show one write-up         |
| `version`     |                   | Newbie     | The package version                         |
| `commands`    |                   | Newbie     | Every command word and alias                |
| `colour`      | `color`           | Newbie     | Turn colour on or off                       |
| `description` |                   | Newbie     | Set the examine line                        |
| `status`      |                   | Newbie     | Set the session status                      |
| `auto_reply`  |                   | Newbie     | Reply line for tell and remote              |
| `quit`        |                   | Newbie     | `Goodbye!`, then disconnect                 |
| `resident`    |                   | Super User | Save a connected newbie as a resident       |
| `warn`        |                   | Super User | Warn one player with a reason               |
| `nuke`        |                   | Super User | Delete a saved player                       |
| `disconnect`  |                   | Super User | Disconnect a player of lower rank           |
| `su`          |                   | Super User | Speak on the super-user channel             |
| `se`          |                   | Super User | Emote on the super-user channel             |
| `st`          |                   | Super User | Think on the super-user channel             |
| `au`          |                   | Admin      | Speak on the admin channel                  |
| `ae`          |                   | Admin      | Emote on the admin channel                  |
| `at`          |                   | Admin      | Think on the admin channel                  |
| `lsu`         | `staff`           | Newbie     | Staff with status and idle                  |
| `lsn`         |                   | Super User | Connected newbies                           |
| `connections` | `conns`           | Super User | Every socket                                |
| `broadcast`   |                   | Admin      | Centered message to every room              |
| `allow`       |                   | Admin      | Open or close newbies or residents          |
| `promote`     |                   | Admin      | Move a resident or super user up one rank   |
| `demote`      |                   | Admin      | Move an admin or super user down one rank   |
| `shutdown`    |                   | Admin      | Count down, then stop the server            |
| `reload`      |                   | Admin      | Read the help file again                    |

A command with no text, where text is required, replies with that
command's syntax, such as `Syntax: tell <name> <message>`. An unknown word
replies `No such command '<word>'`.

`save` writes the player file at once and replies `Player Saved.` `retire`
asks for confirmation and the password, then deletes the saved file and
disconnects. The admin cannot retire. `warn <name> <reason>` sends a line
with a blank line above and below. When colour is on the line is cyan, and
the terminal beeps once. `allow newbies|residents on|off` opens or closes
logins for that rank until the server stops. Resident off blocks rank
resident only; super users and the admin still connect. It does not change
`allow_newbies`, `allow_residents`, or `mfa_enabled` in the config file.

MFA is on unless `mfa_enabled` is false. Residents use `mfa setup` and
`mfa remove`. Staff may run `mfa remove <name>` on a lower rank. See
[MFA](../security/mfa.md).

`resident`, `warn`, `nuke`, `disconnect`, `promote`, and `demote` refuse your own
name with `You cannot do that to yourself.` `nuke` and `disconnect` also refuse someone of your rank or higher.
A successful `resident` adds
`< SU > <name> makes <name> a resident.` to the staff channel and tells the
super user who asked that the player `is now a resident`.

`promote <name>` moves a resident to super user or a super user to admin.
The target must be in a room. Logged off replies `Bea is not connected.`
With MFA on, no secret replies `Bea must turn on MFA first.`
Any admin may promote to super user. Only the Super Admin named in the config
may promote to admin. Staff hear `< SU > Ada promotes Bea to Super User.`
Admins hear `[ Admin ] Ada promotes Bea to Admin.` `demote <name>` moves an
admin to super user or a super user to resident. Any admin may demote a
super user. Only the Super Admin may demote an admin. Admins hear
`[ Admin ] Ada demotes Bea from Admin.` Staff hear
`< SU > Ada demotes Bea from Super User.` `demote <name> force` drops an
admin or super user straight to resident; from admin the force line is sent
on both channels. The super admin cannot be demoted.

When a named player joins or leaves, that room sees
`[<name> has connected]` or `[<name> has disconnected]`.
