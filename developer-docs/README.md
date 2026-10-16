# Developer Docs

These pages are for someone who has the source and wants to extend the talker: a command, a config key, a role, a room rule, or the way a connection is handled.

Someone who only wants to log in and use a running talker should read the site built from `mkdocs/`. This folder is not on that site.

## Start Here

| You want to…               | Read                         |
| -------------------------- | ---------------------------- |
| Add a command              | [Commands](commands.md)      |
| Add a hot or cold key      | [Config](config.md)          |
| Add a role                 | [Roles](roles.md)            |
| See the objects you hold   | [Objects](objects.md)        |
| Follow login               | [Login](login.md)            |
| Move someone between rooms | [Rooms](rooms.md)            |
| See who owns the sockets   | [Network](network.md)        |

Command code talks to `Server`. It does not read sockets. Network is here so you know which object does.

## The Pages

- [Commands](commands.md) is how a typed line becomes a function, and the checklist for adding one.
- [Config](config.md) is the wrapper, hot, and cold files, and how a new key gets into a running server.
- [Roles](roles.md) is the bit flag, the saved integer, and how a command honours it.
- [Objects](objects.md) is `ChatServer`, `Server`, `Client`, `Player`, and `SavedPlayer`.
- [Login](login.md) is the path from a new socket to a person in a room.
- [Rooms](rooms.md) is the layout and how a command moves someone.
- [Network](network.md) is the socket loop, which addresses listen, and how a hostname and country are stored.
