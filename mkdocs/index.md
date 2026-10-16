# Chat Server

Time for a little nostalgia, from the 1980s through the 2000s. Those years were full of
[talkers](https://en.wikipedia.org/wiki/Talker), [MUDs](https://en.wikipedia.org/wiki/Multi-user_dungeon) and
[MUSHes](https://en.wikipedia.org/wiki/MUSH): real-time chat and games, all of it text, reached by telnet or a
client made for the job. Most of them have long since left the internet, replaced by newer games and messaging.

This server is a throwback to that. The days we grew up online and spent playing these games.

It is a bare-bones chat system, and it works. Use it straight out of the box, or do what we used to do: tinker
with it, customise it, add to it, and make it your own.

## Key Features

- Telnet, SSH, or both. SSH has no SSH password. The chat name is still the first prompt.
- IPv4, IPv6, or both. `stack` is `ipv4`, `ipv6`, or `dual`.
- Country lookup from an MMDB file. Loopback is Localhost, a private address is Private Network, and a miss is Unknown.
- Saved players, bcrypt passwords, and ranks from newbie through Super Admin.
- MFA with a six-digit code. The secret is sealed in the player file with the password.
- Rooms, a map, tells, shouts, and staff tools for residents, super users, and admins.
