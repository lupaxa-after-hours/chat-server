<p align="center">
  <a href="https://github.com/lupaxa-after-hours">
    <img src="https://raw.githubusercontent.com/the-lupaxa-project/brand-assets/master/logos/organisations/after-hours/readme-logo.png" alt="After Hours" />
  </a>
</p>

<h1 align="center">Chat Server</h1>

Time for a little nostalgia, from the 1980s through the 2000s. Those years were full of
[talkers](https://en.wikipedia.org/wiki/Talker), [MUDs](https://en.wikipedia.org/wiki/Multi-user_dungeon) and
[MUSHes](https://en.wikipedia.org/wiki/MUSH): real-time chat and games, all of it text, reached by telnet or a
client made for the job. Most of them have long since left the internet, replaced by newer games and messaging.

This server is a throwback to that. The days we grew up online and spent playing these games.

It is a bare-bones chat system, and it works. Use it straight out of the box, or do what we used to do: tinker
with it, customise it, add to it, and make it your own.

A multi-room TCP chat server. Each player picks a name, starts in the
room named by `start`, and talks to the other people in that room.

## Install

```bash
pip install lupaxa-chat-server
```

Requires Python 3.10+. PyYAML, bcrypt, and AsyncSSH are the runtime
dependencies.

## Run

The server will not start without a config file. There is no default path.
From a checkout:

```bash
chat-server -c config/config.yml
python -m lupaxa.chat_server -c config/config.yml
```

The files under `config/` are one example. After
`pip install lupaxa-chat-server`, create the config, the rooms file, and
the help file yourself.

## Documentation

The site source is in `mkdocs/`.

- [Getting Started](mkdocs/getting-started.md) covers the config file and how to run.
- [Connect](mkdocs/usage/index.md), [Players](mkdocs/usage/players.md), [Rooms](mkdocs/usage/rooms.md), and [Commands](mkdocs/usage/commands.md) are how a player uses it.
- [Reference](mkdocs/reference.md) is the config keys, ranks, and public names.
- [Examples](mkdocs/examples.md) is a short telnet session and the SSH command.

<a href="https://github.com/the-lupaxa-project">
    <img src="https://raw.githubusercontent.com/the-lupaxa-project/brand-assets/master/logos/components/footer-for-child-orgs.svg" alt="The Lupaxa Project Footer" width="100%" />
</a>
