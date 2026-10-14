# MFA

MFA is a six-digit code from an authenticator, checked after the password.
It is on unless `mfa_enabled` is `false`. Omitting the key means true. The
value must be true or false. `allow` does not change it. The server reads
it at startup.

## Who Must Use it

Residents may turn it on. Super users, admins, and the Super Admin must
have it when `mfa_enabled` is true. A resident with no secret enters after
the password. Staff with no secret are shown a new secret and cannot enter
until the code matches.

When `mfa_enabled` is false, login never asks for a code. `mfa` replies
`No such command 'mfa'`, and help hides the topic. A stored secret is still
opened and sealed again after a good password, so turning MFA back on later
keeps the same token.

## Login

A saved name with a secret is asked `Code:` after the password. The code is
six digits. The server accepts the current 30-second step and one step
either side. The same code cannot be used again. Three wrong codes
disconnect, the same limit as a wrong password. `quit` on that prompt
disconnects.

The first login of the config account still has to come from localhost.
With MFA on, the two passwords are followed by enrolment, and the player
file is written only after the code matches. With MFA off, the two
passwords save the Super Admin and let them in.

## Setup and Removal

`mfa` is for residents and above, and only while MFA is on.

`mfa setup` asks `Current password:`, then shows the base32 secret and a
QR when the terminal is wide enough. The authenticator issuer is
`Chat Server`. Nothing is stored until `Code:` matches. `mfa setup` when a
secret already exists replies `MFA is already on.`

`mfa remove` asks for the current password and then a code. A resident sees
`Your MFA has been removed.` Staff stay in the room and see this line:

`Your MFA has been removed. Set it up again before you log out. The next login will make you set it up before you can enter.`

`mfa remove <name>` is for a lower rank. It asks
`Are you sure you want to remove MFA for Bea?` The word `yes` in any case
clears it. Anything else is `MFA remove cancelled.` Same rank or higher is
`You cannot remove MFA for Bea.` No secret is `Bea has no MFA.` The admin
channel sees `[ Admin ] Ada removes MFA for Bea.`

Promote still needs a stored secret when MFA is on. No secret replies
`Bea must turn on MFA first.` The target must be in a room.

## The Player File

The password stays a bcrypt hash. The MFA secret is sealed with a key
derived from that password, so a copy of the player directory without the
password does not reveal the token. The file stores `mfa_on: true` when MFA
is on.

A secret that is still the old base32 text is sealed on the next good
password, before `Code:`. The authenticator does not change.

Changing the password opens the seal with the current password and seals it
again with the new one. A seal that will not open is left as it is, and the
new password hash is still saved.

Clearing `mfa_secret` in the player file turns MFA off. If `mfa_on` is still
true, the next login says `Your MFA has been removed.` A resident is then
let in, and the flag is cleared. Staff hear that line and must finish setup
before they can enter. The flag stays true until that setup saves. Deleting
both `mfa_secret` and `mfa_on` is a first-time login: a resident enters with
no extra line, and staff still cannot enter until setup finishes.

If the Super Admin loses their phone, edit their player file and clear
`mfa_secret` by hand.
