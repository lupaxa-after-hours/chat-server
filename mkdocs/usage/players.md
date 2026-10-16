# Players

## Ranks

| Rank        | Saved | What they can do                                                                             |
| ----------- | ----- | -------------------------------------------------------------------------------------------- |
| Newbie      | No    | The room commands, except `shout` and `!`. No resident or staff commands.                    |
| Resident    | Yes   | `prompt`, `retire`, `save`, `set`, and `shout`.                                              |
| SU Channel  | Yes   | The staff channel, plus `connections`, `lsn`, `off_duty`, `on_duty`, `se`, `st`, and `su`.   |
| Super User  | Yes   | `disconnect`, `grab`, `nuke`, `resident`, and `warn`.                                        |
| Admin       | Yes   | `allow`, `appoint`, `demote`, `dismiss`, `promote`, and the admin channel.                   |
| Coder       | Yes   | The coder channel, `ce`, `ct`, and `cu`, and promote or demote between Super User and Admin. |
| Super Admin | Yes   | One account, created by setup. Outranks every other rank.                                    |

`cls`, `lsu`, `main`, `remote`, `rthink`, `term_width`, `testcard`, and
`time` are for every rank, including a newbie. `broadcast`, `network`,
`reboot`, `reload`, `shutdown`, and `system` are Coder, or the Site Admin
role. Those six are listed under Roles.

When colour is on, `examine`, `who`, and `lsu` show a super user in cyan,
an admin in red, a coder in magenta, and the Super Admin in light blue.
On `who` and `lsu`, Coder uses `{ }` and the Super Admin uses `( )`.
A resident or newbie sees an SU Channel player as Resident. SU Channel
and above see Super User Channel (cyan when colour is on).

## Login

A new name walks straight in. A saved name has to prove who they are.

- A name with no saved file enters at once as a newbie.
- A saved name asks for a password. A wrong password is `Wrong password.` The third wrong password disconnects.
- A saved name must be typed in the spelling it was saved with. Any other case is told how the name is saved and asked for again.
- `password` is for every rank. A newbie is told they have no saved password. A saved player types the current password, then the new one, twice.
- With MFA on, which is the default, a saved player with a secret is asked for `Code:` after the password. SU Channel and above must turn MFA on.
- Login walks them through setup when they have no secret yet. With MFA off, login never asks for a code and `mfa` does not exist. See [MFA](../security/mfa.md).
- A saved player who logs in while that name is already in a room takes over that session. The old connection is told it has been reconnected from elsewhere. The room hears `[Bea has reconnected]`.

## Player Files

The `players/` directory is one file per person, named by the lowercase
name, such as `players/bea.yml`.

Each file holds:

- the saved spelling, the rank, and a bcrypt password hash
- total seconds connected, and the prompt when it is not the usual one
- the enter, login, and logout sentences, the description, and the plan
- the last address, and the UTC logout time once they have left
- the country name and two-letter code, when lookup stored a result

The password is never stored as typed. A normal quit writes the logout time
and the total. The file is also written every five minutes while they are
here. A resident may run `save` to write it at once.

## Super Admin

The super admin account is created with `chat-server setup -c` and the config path
while the talker is stopped. Promote never grants that rank.
The super admin cannot retire.

## Roles

A role is a flag on the player. It does not change their rank. The player
file stores the integer under `roles`. A missing key means no roles. `1`
is Site Admin. The token you type is `site_admin`.

| Role       | What it unlocks                                                                      |
| ---------- | ------------------------------------------------------------------------------------ |
| Site Admin | `broadcast`, `network`, `reboot`, `reload`, `shutdown`, and `system`, or rank Coder. |

`appoint` and `dismiss` are admin commands. `roles` is resident and
above. An admin may look up another name. The Site Admin role does not
unlock appoint, dismiss, or that lookup.

You may give a role to yourself, including as the super admin. You may
not change roles on a different super admin.

## Staff

`resident <name>` is for a super user or an admin, and only on a connected
newbie. The newbie is asked for a password twice and saved as a resident.
The staff channel sees `< SU > <name> makes <name> a resident.` The super
user who asked also sees that the player `is now a resident`.

| Command      | Who        | What it does                                                                                                                                                                    |
| ------------ | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `allow`      | Admin      | `allow newbies on`, `allow newbies off`, `allow residents on`, or `allow residents off`, until the server stops. It does not rewrite the config file.                           |
| `demote`     | Admin      | One rank down. `demote <name> force` drops SU Channel, a super user, an admin, or a coder straight to resident.                                                                 |
| `disconnect` | Super user | Disconnects a player of lower rank.                                                                                                                                             |
| `nuke`       | Super user | Deletes a saved player and disconnects them if they are here.                                                                                                                   |
| `promote`    | Admin      | One rank up. The target must be in a room. Logged off is `No such player 'Bea'.`                                                                                                |
| `retire`     | Resident   | Deletes your own file after confirmation and the password.                                                                                                                      |
| `warn`       | Super user | A line with a blank line above and below. Cyan when colour is on, and the terminal beeps once.                                                                                  |

With MFA on, promote of someone with no secret replies `Bea must turn on MFA first.`
An admin may move someone among resident, SU Channel, and super user.
A coder or the super admin may move someone between super user and admin.
Only the super admin moves someone onto or off Coder. Every rank change is
told on the staff channel. The super admin cannot be demoted.
A player promoted to admin or coder may use the admin commands, including
`ae`, `at`, and `au`.

`allow residents off` blocks rank resident only. SU Channel and above
still connect. `allow` does not change `allow_newbies`, `allow_residents`,
or `mfa_enabled`. Those config keys decide the startup defaults.

`resident`, `warn`, `nuke`, `disconnect`, `promote`, and `demote` refuse
your own name with `You cannot do that to yourself.` `nuke`, `disconnect`,
`warn`, and `grab` also refuse someone of your rank or higher.

## Tells and Chains

`tell`, `remote`, and `rthink` refuse your own name with
`Talking to yourself is the first sign of madness.`

A comma-separated list sends to several people. Two or more other people
form a chain.

| Rule                                 | Limit                                 |
| ------------------------------------ | ------------------------------------- |
| People on one chain                  | 10                                    |
| Chain closes                         | Below three people                    |
| Idle chain                           | Deleted after 2 hours with no message |
| `chains`                             | Lists the ones you are on             |
| `chains leave` / `remove` / `delete` | Change one chain                      |
| Reboot                               | Chains are kept                       |
| Shutdown                             | Chains are dropped                    |

## Examine

`examine <name>` is open to everyone. `x Bea` is examine. `xBea` is not.
`me` is the player who typed it.

| They are…          | The frame shows                                                                                                                         |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| In a room          | Name, location, rank, roles when one is set, status when set, this login, total login, and idle. Yourself also shows the terminal size. |
| A connected newbie | Found only by the exact spelling.                                                                                                       |
| Logged out         | Rank, roles when one is set, the logout time as a calendar phrase, how long ago, and total login.                                       |
| No logout time     | `Logged out: unknown`, and no Ago line.                                                                                                 |
| Unknown            | Not found. Nobody here and no file.                                                                                                     |

A plan, when one is set, sits under the room sentences in both frames.
`Logged in from` and `Last from` show `hostname (address)`. A player in a
room also shows `[via telnet]` or `[via ssh]`.

## Staff Channels

The channel is SU Channel and above. Residents and newbies do not hear it.

| Command | Channel | Shown as                                            |
| ------- | ------- | --------------------------------------------------- |
| `ae`    | Admin   | Emote                                               |
| `at`    | Admin   | Think                                               |
| `au`    | Admin   | `[ Admin ] Ada says 'hello'`, red when colour is on |
| `se`    | Staff   | Emote on that channel                               |
| `st`    | Staff   | Think on that channel                               |
| `su`    | Staff   | `< SU > Ada says 'hello'`                           |

The sender sees `You` in place of their name. Other admins hear the admin
channel. No one else does.

`resident`, `nuke`, and `disconnect` use the staff channel. `promote` and
`demote` use the staff channel only. `appoint` and `dismiss` use the staff
channel, and tell the player when they would not have heard it.

`staff` runs `lsu`. It lists connected players at SU Channel and above, with
rank, room, and idle. An SU Channel row is hidden from residents and newbies,
and SU Channel and above see `< Channel Only >`. Every rank can run it.

| Command      | Who        | Effect                                                                                |
| ------------ | ---------- | ------------------------------------------------------------------------------------- |
| `lsn`        | SU Channel | Connected newbies, with hostname, room, and idle.                                     |
| `off_duty`   | SU Channel | Staff channel is told. Staff commands reply `You are off duty` until `on_duty`.       |
| `off_list`   | Admin      | Still working. `lsu` shows Off List, and people who are not staff do not see the row. |
| `on_duty`    | SU Channel | Asks for the saved password. A wrong one leaves them off duty and tells staff.        |
| `on_list`    | Admin      | Puts the public row back.                                                             |
| `screenlock` | Resident   | Locks the screen until the password. Staff who use it go off duty until it unlocks.   |

On `lsu`, Off Duty uses the same colour, and only staff still see that row.

A new name tells staff `[ Newbie Alert ]` as soon as it is known, again if
they refuse the disclaimer, and again when they reach the start room. Those
lines use the staff colour and do not start with `< SU >`.
