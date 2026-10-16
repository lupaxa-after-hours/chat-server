# Rooms

The office loads from `config/rooms/system.yml` at startup and again on `reload rooms`. A house file loads while its owner is inside it. The Void is built in.
Commands move a player by changing `client.player.room` and telling the people who can see it.

What a player sees in a room is in [Rooms](../mkdocs/usage/rooms.md).

## The Layout

`load_layout` reads the rooms file and returns a `Layout`. `ChatServer` keeps it as `layout`.
Each room has a description, the exits, and an optional enter sentence. `max_occupancy` is optional. When it is omitted the room has no limit. When it is present it is a whole number of at least 1.
The config `start` room must be one of those ids. `newbie_start` uses the same rule and falls back to `start`.
`display_name` turns an id such as `main_room` into the words players see.

`layout.brief` is the block a person receives on entering or on `look`: the description, who else is here, and the exits.

## Entering and Moving

`enter` puts a newbie in `newbie_start` and anyone else in the start room, or their home when that login is on. If that room has a capacity and is already full, the connection is dropped and no player is stored.
The room hears the login sentence, which defaults to `has connected.`. Staff are not given a copy of that line.
A newbie's staff alert is sent on the staff channel, not to the room. The staff landed line names that room, such as `Bea is in Reception.`

`go`, `main`, and `join` use the same shape. They refuse the move when the destination is full. Otherwise they remember the old room, set `client.player.room`, tell the old room, send the destination brief to the mover, and tell the new room.

`broadcast_room` in `broadcast.py` walks `get_clients` and skips the speaker. A line for SU Channel and above uses `broadcast_staff`, which paints it with the staff colour when that player has colour on.
