# Connect

## Chat Client

[Chat Client](https://github.com/lupaxa-after-hours/chat-client){ target="_blank" rel="noopener" }
is a terminal client for this server. Text from the server stays in the upper
part of the screen. The line you are typing stays at the bottom, and new
text does not clear it.

```bash
pip install lupaxa-chat-client
```

Requires Python 3.10 or newer. It only runs in a terminal. It stops
before connecting when standard input or standard output is not a
terminal.

The host and the port are required. Use the port the server is listening
on.

```bash
chat-client telnet 127.0.0.1 9000
chat-client ssh 127.0.0.1 9022
```

Telnet and SSH both show the server's normal text. On telnet the line is
edited locally and sent when you press Enter. On SSH each character is
sent as you type, and the server echoes it onto the prompt. SSH signs in
as the user `chat`. The first SSH host key is saved in
`~/.chat-client/known_hosts`. A key that no longer matches is refused.

A password prompt hides the characters you type. Ctrl-C leaves and
restores the terminal. Page Up, Page Down, and End scroll the history.
Left, Right, Backspace, and Delete edit the line. Those keys stay on the
client. They are not sent to the server. The name prompt is the same as
on a line client.

## Line Clients

Use any line-based TCP client. `nc` and `telnet` both work.

```bash
nc 127.0.0.1 9000
```

The first non-blank line is the name, except `who` and `quit`. `who` in any
case shows who is connected, then asks for a name again. `quit` in any case
says `Goodbye!` and disconnects. A name is 3 to 20 letters or digits, and it
cannot start with a digit. An unsaved name is unique in lowercase, and the
spelling they type is the one shown. The names `me`, `who`, and `quit` are
refused in any case, using the spelling they typed, and no player file is
opened. A blank line does nothing. A line longer than 1024 characters drops
that connection.

A connection that has not entered a room within `login_timeout` minutes is
told `Login timed out.` and dropped. Typing does not start that clock again.

SSH uses the same name prompt. There is no SSH password. See
[SSH](../security/ssh.md). A saved name then follows the same password and
[MFA](../security/mfa.md) path as telnet.
