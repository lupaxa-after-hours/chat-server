# Roles

A role is a bit on the player. It is not a rank. Rank still decides the ladder from newbie to Super Admin. A role lets one saved player run a few commands their rank would otherwise hide.

The only role today is Site Admin. The saved integer is `1`. The word a player types is `site_admin`. The label on examine and help is `Site Admin`.

## Where a Role Lives

| Piece          | File        | What it is                                      |
| -------------- | ----------- | ----------------------------------------------- |
| `Roles`        | `roles.py`  | An `IntFlag`. `SITE_ADMIN` is the first bit.    |
| `ROLE_WORDS`   | `roles.py`  | The token stored in help and typed to appoint.  |
| `ROLE_LABELS`  | `roles.py`  | The words a player sees.                        |
| `player.roles` | player file | The integer. A missing key means no roles.      |

`appoint` and `dismiss` look the token up with `role_from_name`. Examine and the help Permissions line use `ROLE_LABELS`. You do not register the name anywhere else.

## How a Command Uses It

`Command.role` defaults to `0`, which means rank only. When it is not `0`, `command_allowed` in `commands.py` applies `combination`:

| `combination` | The player can run it when…                          |
| ------------- | ---------------------------------------------------- |
| `or`          | Their rank is high enough, or they hold the bit.     |
| `and`         | Their rank is high enough, and they hold the bit.    |

`broadcast`, `reload` and its children, `shutdown`, `reboot`, `network`, and `system` are `rank=Rank.CODER`, `role=int(Roles.SITE_ADMIN)`, `combination="or"`. A coder can run them. So can a resident who has been appointed Site Admin.

Help shows one Permissions line. No role shows the rank. A role shows the rank, the combination, and the label, as in `Coder or Site Admin`.

## Add a Role

`auto()` assigns the next bit. Leave `SITE_ADMIN` where it is. Files already on disk store `1` for that role, and changing the bit would appoint the wrong people.

1. Add the member on `Roles` under `SITE_ADMIN`. Do not pass an explicit number unless you have a reason to skip a bit.
2. Add the token to `ROLE_WORDS` and the display label to `ROLE_LABELS`. The token is what `appoint` accepts. The label is what players read.
3. Put `role=int(Roles.YOUR_ROLE)` and `combination="or"` or `"and"` on each `Command` that should honour it. `"or"` is the combination the shipped commands use.
4. Write the help topic so the Permissions line is worth reading. The label comes from `ROLE_LABELS` on its own.
5. Add a row to the Roles table in `mkdocs/usage/players.md`, and say which commands the bit unlocks. Keep that command list alphabetical.
6. Test appoint, dismiss, a player below the command rank who holds the bit, and a player who does not. `tests/test_roles.py` is the place.

`listed_commands` for `commands <token>` keeps a command when its role bit overlaps the filter. It does not also check rank. Say that in the developer notes for the command if the new list should behave the same way.

An empty list already has a sentence per role. Add one for the new label, or the player will see the Site Admin sentence for the wrong name.

Do not store the new role as another rank, and do not reuse `1`.
