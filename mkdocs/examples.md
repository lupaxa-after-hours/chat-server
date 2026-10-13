# Examples

The checkout's `config/` directory is an office that starts in Reception.
Telnet is on port 9000 and SSH is on port 9022.

## Telnet

```text
$ nc 127.0.0.1 9000
Welcome to Chat Server
Version <version>

Please enter your name: Ada
The front desk faces the street. A bell sits on the counter.

Nobody else is here.

Exits:
  north -> Open Office
  south -> Hallway
  east  -> Meeting Room
  west  -> Kitchen
What? > 'hello
You say 'hello'
What? > go west
The kitchen smells of coffee. Mugs crowd the sink.

Nobody else is here.

Exits:
  north -> Staff Room
  east  -> Reception
What? > quit
Goodbye!
```

The room Ada left sees `Ada goes west to Kitchen.` The kitchen, if anyone
is already there, sees `Ada arrives from the east.`

## SSH

```bash
ssh -p 9022 127.0.0.1
```

There is no SSH password. The first prompt is still the chat name. A
terminal shows what you type, except a password.
