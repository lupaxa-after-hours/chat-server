# SSH

SSH is optional. Set `ssh_port` and the server listens for SSH as well as
telnet. Omit `ssh_port` and only telnet listens. At least one of `port` and
`ssh_port` is required. The two numbers cannot be the same.

## Listen

```yaml
port: 9000
ssh_port: 9022
```

Startup prints `Listening for SSH on 0.0.0.0:9022` when that port is set.
A port that is already taken stops startup.

```bash
ssh -p 9022 127.0.0.1
```

[Chat Client](https://github.com/lupaxa-after-hours/chat-client) connects
with `chat-client ssh 127.0.0.1 9022`. It signs in as the user `chat`. The
first host key is saved in `~/.chat-client/known_hosts`. A key that no
longer matches is refused.

## Host Key

The first start that has `ssh_port` writes an Ed25519 private key to
`ssh_host_key` beside the config file, mode `0600`. Later starts reuse that
file. The path is not a config key. A file that cannot be read, or that is
not a key, stops startup.

Keep the file. Replacing it changes the host key that clients already trust.

## Sign In

There is no SSH password and no SSH public key. Any username is accepted.
That username is not the chat name. The first prompt is still
`Please enter your name:`. A saved name then follows the same password and
[MFA](mfa.md) path as telnet.

## The Terminal

A terminal shows what you type, except a password or a code. Telnet hides
those the same way when the client honours echo control. SSH reports the
terminal size, so frames and the login QR use that width. `connections`
shows the socket type as `ssh`.
