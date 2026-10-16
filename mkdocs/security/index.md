# Security

A saved player is checked in more than one place before they enter a room:
the connection, the password, and a second factor. Fail any one of those
and they stay outside.

## Core Features

| Feature   | What it Does                                                                           |
| --------- | -------------------------------------------------------------------------------------- |
| SSH       | Optional encrypted listener. An Ed25519 host key is saved once and reused.             |
| Passwords | A saved player has a bcrypt hash. The password is never stored as typed.               |
| MFA       | A six-digit authenticator code after the password. On unless `mfa_enabled` is false.   |
| Sign-in   | Three wrong passwords or codes disconnect. A login that never enters a room times out. |

## SSH

SSH is the encrypted way in. The host key is written once, mode `0600`, and
later starts reuse it. A client that has saved the key refuses one that no
longer matches. There is no SSH password. The first prompt is still the chat
name, and a saved name then follows the same password and MFA path as telnet.

See [SSH](ssh.md).

## MFA

MFA is a six-digit code from an authenticator, checked after the password.
Residents may turn it on. SU Channel and above must have
it while `mfa_enabled` is true. The secret in the player file is sealed with
a key derived from the password, so a copy of that directory without the
password does not reveal the token.

See [MFA](mfa.md).
