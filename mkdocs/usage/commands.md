# Commands

The first word is the command. Matching ignores case. A command above the
player's rank replies `No such command '<word>'`, the same as an unknown word.

## How a Line is Read

| You type      | It means                                       |
| ------------- | ---------------------------------------------- |
| `!hello`      | `shout`                                        |
| `'hello`      | `say`, alias against the text                  |
| `,Bea smiles` | `remote`. `<` is the same command              |
| `.Bea hello`  | `tell`. `>` is the same command                |
| `?say`        | `help say`. `?` needs no space                 |
| `x Bea`       | `examine`. `x` needs a space, so `xBea` is not |

A command name still needs a space before its text.

## The Commands List

`commands` lists every alias first, then every command name, each group in
alphabetical order. It and `help` show only what the player's rank may use.
`set commands` lists the set commands that rank may use.

| You type              | You see                                                             |
| --------------------- | ------------------------------------------------------------------- |
| `commands a`          | Names from that list which start with a                             |
| `commands admin`      | Admin commands. Admins can do `commands admin`.                     |
| `commands aliases`    | The aliases                                                         |
| `commands coder`      | Coder commands. A coder and the super admin do `commands coder`.    |
| `commands site_admin` | Role commands, coloured by rank. Only a holder runs it.             |
| `commands su`         | Staff commands. SU Channel and above can do `commands su`.          |

A letter with nothing to list says `No commands found beginning with 'Z'.`
`commands a` keeps the names from that list which start with a.
With no aliases it says `There are no aliases.`
Staff with nothing to list get `There are no Super User commands.`
Admins with nothing to list get `There are no Admin commands.`
Coders with nothing to list get `There are no Coder commands.`
A holder with nothing to list gets `There are no Site Admin commands.`

## Talking

`say`, `emote`, and `think` go to the other people in the same room.
The sender gets their own copy. Others see a line starting with `-` and
your name.

| Command | You see                                |
| ------- | -------------------------------------- |
| `shout` | Every room. Their line starts with `!` |

`tell <name> <message>` goes to that one player, in whichever room they are
in. They see a line starting with `>`.

`remote <name> <message>` emotes to that one player. You see
`You emote: Ada smiles to Bea`. They see a line starting with `>` and
`Ada smiles to you`.

`rthink` does the same with a thought.
You see `You think . o O ( hmm ) to Bea`.

Naming yourself on `tell`, `remote`, or `rthink` replies
`Talking to yourself is the first sign of madness.`

## Chains

A comma-separated list names several people. `tell Bea, Ade hello` reaches
both. Two or more other people start a chain, shown as `(1)` before the line.

| You type                    | What happens                                  |
| --------------------------- | --------------------------------------------- |
| `chains delete <id>`        | Deletes one                                   |
| `chains leave <id>`         | Leaves a chain. An optional reason is allowed |
| `chains remove <id> <name>` | Removes people                                |
| `tell 1 hello`              | Sends to that chain                           |
| `tell 1, Dee hello`         | Adds Dee                                      |

You can send only to a chain you are on. A chain holds at most 10 people.
When someone logs out, the others are told. A chain closes when fewer than
three people remain. If that smaller set matches another chain, the higher
id is removed. A chain with no message for 2 hours is deleted, and everyone
on it is told. A player who has not joined a room is not found.

## Who is Here

`who` lists each player who is in a room: name, rank, room, status, and idle.
`w` is the same command. `wplease` is not.

| Idle               | The cell                    |
| ------------------ | --------------------------- |
| Under five minutes | `[ Active ]` in green       |
| Five to 30 minutes | `[HH:MM:SS]` in yellow      |
| 30 to 60 minutes   | `[HH:MM:SS]` in red         |
| From 60 minutes    | `[--:--:--]` in light black |

When nobody is in a room it replies `-=> There is currently no one connected`.

`set status` sets the status others see on examine, `who`, and `lsu`. At
most 20 characters. Bare `set status` clears it. It is not saved.

`set auto_reply` sets a line returned to anyone who `tell`s, `remote`s,
or `rthink`s you. Bare `set auto_reply` clears it. It
is not saved. After a successful one of those, the sender sees
`[ Auto Reply from Ada ]` and your message.

## Connections

`connections` lists every open socket for SU Channel and above.
`conns` is the same command.

| Column     | When                                                             |
| ---------- | ---------------------------------------------------------------- |
| Name       | `<pending>` until a name is typed, including during the password |
| Hostname   | The resolved name                                                |
| IP Address | The peer address                                                 |
| Country    | Added before Type when country lookup is on                      |
| Type       | `telnet` or `ssh`                                                |

`ipinfo <player>` or `ipinfo <address>` shows that database record in a
frame like examine. `ipinfo me` uses your connection. A logged-out player
uses the address saved in their file. The frame shows the address, hostname,
country, and the rest of the record, such as the continent, ASN, and IP
block. The command does not exist when country lookup is off. Loopback is
Localhost. A private address is Private Network. No record is Unknown.

## Moving

`look` shows the description, who else is there, and the exits as
`direction -> room`. `l` is the same command. `lplease` is not.

`home` goes to your private room. Residents and above. The first time
creates it. The room you leave hears that you go home. Lists say Home.
`map` there draws your house.

`set login` reports the current choice for the next fresh login.
`set login home` starts that login at home and does not move you now.
`set login main` starts it in the main room. `join` of someone at home
tells you that you are not permitted to their home.

`grab <name>` pulls a lower-rank player into your office room. Super users
and above. They are told you grabbed them, then they see the room. A house
or The Void cannot be the landing room, and a full room refuses.

## Examine and Set

`examine <name>` shows a framed profile.

| They are…  | The frame shows                                                                 |
| ---------- | ------------------------------------------------------------------------------- |
| In a room  | Rank, status when set, this login, total login, and idle                        |
| Idle ≤ 10s | `Not idle at all`                                                               |
| Logged out | `Last from`, when they left, how long ago, total login, then the three messages |
| Yourself   | Also the terminal size                                                          |

`Logged in from` is the hostname and the IP, then `[via telnet]` or
`[via ssh]`. `me` is yourself. A name matches in any case, including a
connected newbie.

The enter, login, and logout messages sit in their own block.
`set plan <line>` sets the line under that block. `set description` sits
under the name while you are in a room. Both are for residents and above,
at most 80 characters, and a bare command clears the saved line.

`recap <name>` changes the capitalisation of your own name.

## Screen, Prompt, and Colour

`alarm <minutes>` counts down in seconds and tells you when it reaches zero.
Typing a command sets that idle time back to zero.

After each command the player gets `What? >`, including when the command
failed. `set prompt <prompt>` replaces that line. A custom prompt is stored
with a space at the end. It is for residents and above, and a resident's
prompt is kept for the next login.

`set entermsg`, `set loginmsg`, and `set logoutmsg` set the lines others
hear. They are for residents and above. The defaults are `arrives.`,
`has connected.`, and `has disconnected.`. Each sentence is at most 80
characters. A bare command clears a saved line.

`colour on` and `colour off` turn colour on and off. `colour` on its own
tells you which it is. `color` is the same command. It is on by default, a
newbie can use it, and a saved player keeps the choice.

A newbie starts as `I am new here`. That line is cleared when they become
a resident.

`screenlock` locks the screen until the saved password is typed. A reason
is optional. Locking clears the screen.
SU Channel and above go off duty, and the staff channel hears that they
have locked their screen. While locked, the only lines shown are
`Enter password to unlock:` and `Wrong password.`
A tell, remote, or rthink tells the sender `[ Screen Locked ]`
and that the screen is currently locked. A locked screen does not also send
the auto-reply. The right password shows `Screen is now unlocked.` before
the prompt. Staff return to duty, and the staff channel hears that they have
unlocked their screen.

## Help

`help` replies `Syntax: help <topic>`. `help <topic>` shows a centered page,
with a blank line above the box and below it. A command page shows
Permissions before the paragraph. No role shows the rank. A role shows the
rank, the combination, and the role label, as in `Coder or Site Admin`.
The Syntax line is the command name plus its tail, so `!` is `!<message>`.
A topic that is not a command shows the paragraph, and See Also when one is
set. A missing topic replies `help for <topic> not found`.

## Command List

| Command       | Aliases           | Rank       | Effect                                      |
| ------------- | ----------------- | ---------- | ------------------------------------------- |
| `ae`          |                   | Admin      | Emote on the admin channel                  |
| `alarm`       |                   | Resident   | Countdown in minutes                        |
| `allow`       |                   | Admin      | Open or close newbies or residents          |
| `appoint`     |                   | Admin      | Give a player a role                        |
| `at`          |                   | Admin      | Think on the admin channel                  |
| `au`          |                   | Admin      | Speak on the admin channel                  |
| `broadcast`   |                   | Coder      | Centered message to every room              |
| `ce`          |                   | Coder      | Emote on the coder channel                  |
| `chains`      |                   | Newbie     | List your chains                            |
| `cls`         |                   | Newbie     | Clear your screen                           |
| `colour`      | `color`           | Newbie     | Turn colour on or off                       |
| `commands`    |                   | Newbie     | Every command word and alias                |
| `connections` | `conns`           | SU Channel | Every socket                                |
| `ct`          |                   | Coder      | Think on the coder channel                  |
| `cu`          |                   | Coder      | Speak on the coder channel                  |
| `demote`      |                   | Admin      | Move an admin or super user down one rank   |
| `disconnect`  |                   | Super User | Disconnect a player of lower rank           |
| `dismiss`     |                   | Admin      | Take a role from a player                   |
| `emote`       | `:`, `;`          | Newbie     | Emote to the room                           |
| `examine`     | `x`               | Newbie     | Framed profile, here or logged out          |
| `go`          |                   | Newbie     | Move through an exit                        |
| `grab`        |                   | Super User | Pull a player into your room                |
| `help`        | `?`               | Newbie     | List commands, or show one write-up         |
| `home`        |                   | Resident   | Go to your home                             |
| `ipinfo`      |                   | Admin      | Country record for a player or address      |
| `join`        |                   | Newbie     | Move to that player's room                  |
| `look`        | `l`               | Newbie     | Description, exits, and who else here       |
| `lsn`         |                   | SU Channel | Connected newbies                           |
| `lsu`         | `staff`           | Newbie     | Staff with status and idle                  |
| `main`        |                   | Newbie     | Move to the start room                      |
| `map`         |                   | Newbie     | 5 by 5 map when it fits, else 3 by 3        |
| `mfa`         |                   | Resident   | Set up or remove MFA                        |
| `nuke`        |                   | Super User | Delete a saved player                       |
| `off_duty`    |                   | SU Channel | Step away from staff work                   |
| `off_list`    |                   | Admin      | Hide on the public staff list               |
| `on_duty`     |                   | SU Channel | Return after the right password             |
| `on_list`     |                   | Admin      | Return to the public staff list             |
| `password`    |                   | Newbie     | Replace your saved password                 |
| `promote`     |                   | Admin      | Move a resident or super user up one rank   |
| `pstats`      |                   | Admin      | Counts of newbies, ranks, and roles         |
| `quit`        |                   | Newbie     | `Goodbye!`, then disconnect                 |
| `reboot`      |                   | Coder      | Warn SSH, then keep telnet                  |
| `recap`       |                   | Resident   | Change the capitalisation of your name      |
| `reload`      |                   | Coder      | Run one reload command                      |
| `remote`      | `,`, `<`          | Newbie     | Emote to one player                         |
| `resident`    |                   | Super User | Save a connected newbie as a resident       |
| `retire`      |                   | Resident   | Delete your saved account                   |
| `roles`       |                   | Resident   | List your roles, or another player's        |
| `rthink`      |                   | Newbie     | Think to one player                         |
| `save`        |                   | Resident   | Write the player file now                   |
| `say`         | `'`, `"`, `` ` `` | Newbie     | Say to the room                             |
| `screenlock`  |                   | Resident   | Lock the screen until the password          |
| `se`          |                   | SU Channel | Emote on the super-user channel             |
| `set`         |                   | Resident   | Run one set command                         |
| `shout`       | `!`               | Resident   | Shout to every room                         |
| `shutdown`    |                   | Coder      | Count down, then stop the server            |
| `st`          |                   | SU Channel | Think on the super-user channel             |
| `su`          |                   | SU Channel | Speak on the super-user channel             |
| `system`      |                   | Coder      | Process CPU, memory, sockets, and disk      |
| `tell`        | `.`, `>`          | Newbie     | Tell one player                             |
| `term_width`  |                   | Newbie     | Set this connection's frame width           |
| `testcard`    |                   | Newbie     | Show every foreground and background colour |
| `think`       | `~`               | Newbie     | Think to the room                           |
| `time`        |                   | Newbie     | Current time, boot, reboot, and uptime      |
| `version`     |                   | Newbie     | The package version                         |
| `warn`        |                   | Super User | Warn one player with a reason               |
| `who`         | `w`               | Newbie     | Names, ranks, rooms, status, and idle       |

A command with no text, where text is required, replies with that command's
syntax, such as `Syntax: tell <name> <message>`. An unknown word replies
`No such command '<word>'`.

## Saving and Retiring

`save` writes the player file at once and replies `Player Saved.`
`retire` asks for confirmation and the password, then deletes the saved file
and disconnects. The super admin cannot retire.

## Warn and Allow

`warn <name> <reason>` sends a line with a blank line above and below. When
colour is on the line is cyan, and the terminal beeps once.

`allow newbies|residents on|off` opens or closes logins for that rank until
the server stops. Resident off blocks rank resident only; super users and
the admin still connect. It does not change `allow_newbies`,
`allow_residents`, or `mfa_enabled` in the config file.

## MFA

MFA is on unless `mfa_enabled` is false. Residents use `mfa setup` and
`mfa remove`. Staff may run `mfa remove <name>` on a lower rank. See
[MFA](../security/mfa.md).

## Promote and Demote

`resident`, `warn`, `nuke`, `disconnect`, `promote`, and `demote` refuse your own
name with `You cannot do that to yourself.` `nuke`, `disconnect`, `warn`, and `grab` also refuse someone of your rank or higher.
A successful `resident` adds
`< SU > <name> makes <name> a resident.` to the staff channel and tells the
super user who asked that the player `is now a resident`.

`promote <name>` moves one saved rank up: resident, SU Channel, super user, admin, coder.
The target must be in a room. Logged off replies `No such player 'Bea'.`
With MFA on, no secret replies `Bea must turn on MFA first.`
An admin may move someone among resident, SU Channel, and super user.
A coder or the super admin may move someone between super user and admin.
Only the super admin moves someone onto or off Coder.
The super admin account is created with `chat-server setup -c` and the config path
while the talker is stopped.
Every rank change is told on the staff channel. Staff hear
`< SU > Ada promotes Bea to Coder.` and `< SU > Ada demotes Bea from Coder.`
There is no admin-channel copy.

`demote <name>` moves one saved rank down. `demote <name> force` drops SU Channel,
a super user, an admin, or a coder straight to resident. One step down from a
super user writes `su_channel`.
Staff hear `< SU > Ada demotes Bea from Super User.`
The super admin cannot be demoted.

## Roles

`appoint <name> <role>` and `dismiss <name> <role>` are admin commands.
The role is the rest of the line. `site_admin` is the only name. Bare
`appoint` or `dismiss` lists the typed names.

A successful appoint tells the staff channel
`< SU > Ada assigns Bea to the role of 'Site Admin'` with no final
period. Dismiss ends with a period:
`< SU > Ada dismisses Bea from the role of 'Site Admin'.`
The same sentence is sent to that player when they are logged in and
would not have heard the staff channel. An on-duty member of SU Channel
or above already heard it, so they are not told again. A locked screen
swallows the direct sentence.

Already held replies `Bea already has the Site Admin role.` Not held replies
`Bea does not have the Site Admin role.`

`roles` lists your own roles. `roles <name>` is admin only. A resident
who adds a name is told `Only an admin can look up someone else's roles.`

`broadcast`, `reload`, `shutdown`, `reboot`, `network`, and `system` run for rank Coder or for the Site Admin role.

## Arrivals

When a named player joins or leaves, that room sees their login or logout
sentence, such as `Bea has connected.` Staff are not told about every
connection. A newbie tells staff, in the staff colour and with no `< SU >`
prefix, as soon as the name is known:

- `[ Newbie Alert ] AWOOGA AWOOGA!!`
- `<name> is connecting from <hostname>`
- Refusing the disclaimer: `[ Newbie Alert ] <name> quit at the disclaimer`
- Reaching the main room: `[ Newbie Alert ] <name> is in the main room.`
