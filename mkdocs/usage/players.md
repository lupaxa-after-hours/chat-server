# Players

A name with no saved file enters at once as a newbie. A newbie can use
the commands that were already in the game, except `shout`, `!`, and
`prompt`. `lsu`, `term_width`, `time`, `main`, and `remote` are for every
rank, including a newbie. A newbie cannot use `prompt`, `save`, `retire`,
`resident`, `warn`, `nuke`,
`disconnect`, `su`, `se`, `st`, `au`, `ae`, `at`, `lsn`, `connections`, `promote`, `demote`,
`allow`, or `shutdown`.

A saved name asks for a password. A wrong password is answered with
`Wrong password.` and asked again. The third wrong password disconnects.
`password` is for every rank. A newbie is told they have no saved
password. A saved player is asked for the current password, then the new
one, twice. A saved name must be typed in the spelling it was saved with.
Any other case is told how the name is saved and asked for again.

A saved player who logs in while that name is already in a room takes
over that session. The old connection is told it has been reconnected
from elsewhere. The room hears `[Bea has reconnected]`, not a disconnect
and a connect. A super user or the admin in that room sees the address
on that line.

| Rank       | What they can do                                                                     |
| ---------- | ------------------------------------------------------------------------------------ |
| Newbie     | Not saved. Game commands except `shout` and `!`. No `prompt` or super-user commands. |
| Resident   | Saved, with a password. May `shout`, `prompt`, `save`, `retire`, and `lsu`.          |
| Super User | `resident`, `warn`, `nuke`, `disconnect`, `su`, `se`, `st`, `lsn`, `connections`.    |
| Admin      | Also `allow`, `shutdown`, `promote`, `demote`, `au`, `ae`, and `at`.                 |

The `players/` directory is one file per person, named by the lowercase
name, such as `players/bea.yml`. Each file holds the saved spelling of the
name, the rank, a bcrypt password hash, the total seconds they have been
connected, the prompt they chose with `prompt` when it is not the usual one,
and, once they have left, the UTC time they logged out. A normal quit writes
the logout time and the total. The file is also written every five minutes
while they are here, and a resident may run `save` to write it at once. The
password is never stored as typed.

The first time the admin name is typed, it has to come from localhost, and
no other name can log in until that password is saved. The server asks for
a password twice, saves the admin, and lets them in. After that, the admin
can connect from any address. A super user or the admin runs
`resident <name>` on a connected newbie, who is then asked for a password
twice and is saved as a resident. The staff
channel sees `< SU > <name> makes <name> a resident.` The super user who
asked also sees that the player `is now a resident`. `warn <name> <reason>`
sends a line with a blank line above and below. When colour is on the line is cyan, and
the terminal beeps once. `nuke <name> <reason>` deletes a saved player and
disconnects them if they are here. A saved player may `retire` to delete
their own file after confirmation and the password. The admin cannot
retire. Only an admin runs `promote <name>` and `demote <name>`. Any admin
may move someone to or from super user. Only the account named in the config
may promote to admin or demote from admin. A player promoted to admin may
use the admin commands, including `au`, `ae`, and `at`, and may be demoted
again by that account. The account named in the config cannot be demoted.
An admin also runs
`allow newbies|residents on|off`, to open or close logins until the server
stops. Resident off blocks rank resident only; super users and the admin
still connect. That command does not rewrite `allow_newbies` or
`allow_residents` in the config file. Those two settings in the config file
decide whether newbies and residents may connect when the server starts.

`resident`, `warn`, `nuke`, `disconnect`, `promote`, and `demote` refuse your own
name with `You cannot do that to yourself.` `nuke` and `disconnect` also
refuse someone of your rank or higher, unless you are the account named in
the config. `tell` and `remote` refuse your
own name with `Talking to yourself is the first sign of madness.`

`examine <name>` is open to everyone. Someone in a room is shown with
their name, location, rank, status when one is set, this login, total login,
and idle. Examining yourself
also adds the terminal size. `me` is the player who typed it. A connected
newbie is found only by the exact spelling. A saved name shows that same
block when they are here. When they are logged out it shows rank, the
logged-out time as a calendar phrase such as
`Monday 12th October 2026, 13:45:03 UTC`, how long ago that was, and their
total login. `Logged out: unknown` has no Ago line when the file has no
logout time. A name with nobody here and no file is not found. `x Bea` is
examine, and `xBea` is not.

Super users and the admin share a channel. `su <message>` speaks on it,
`se <message>` emotes, and `st <message>` thinks, each shown as
`< SU > Ada says 'hello'`. The sender sees `You` in place of their name.
Residents and newbies hear nothing, and for them these commands do not
exist. Admins also have `au`, `ae`, and `at` on a separate admin channel,
shown as `[ Admin ] Ada says 'hello'` and red when colour is on. Other
admins hear the line; no one else does.
`resident`, `nuke`, `disconnect` use staff; to/from admin, promote/demote use `[ Admin ]`.
`staff` runs `lsu`, which lists the connected
super users and the admin in a frame with their rank, room, and idle.
Every rank can run it. `lsn` lists the connected newbies in a frame with
the hostname, the room, and idle. A super user or the admin in the room
sees the address on connect, disconnect, reconnect, and `examine`. On
examine, that line is `Logged in from` when the player is here and
`Last from` when they are logged out.
