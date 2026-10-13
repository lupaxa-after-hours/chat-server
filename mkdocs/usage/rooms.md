# Rooms

Players start in the room named by `start`. In the example office that
room is Reception. `go west` moves through an exit. The old room is told
`Ada goes west to Kitchen.` The new room is told `Ada arrives from the east.`
The mover sees the description, who else is there, and the exits.

A full room refuses the move. The player stays put, and only they see
`Kitchen is full.` If the starting room is full when a name is chosen, the
server tells them that room's name and disconnects. The name is not kept.

`map` prints a 5 by 5 map with the current room in the centre when that
drawing fits your frame width, and a 3 by 3 map when it does not. One blank
line appears above the map and one below it. Every room that sits inside
that window is shown, including one reached by walking through other rooms.
The room you are in is green when colour is on.
A line is drawn only where two shown rooms connect. Up and down are listed
under the grid when the current room has those exits.

`join <name>` moves you into that player's room when the room has space.
`main` moves you to the start room. The room you leave hears
`Ada goes to Reception.` The start room hears `Ada arrives.` If you are
already there, nothing happens. If it is full, you stay and are told it
is full.

`look` shows the description, who else is there, and the exits. Each real
exit is `direction -> room`. The arrows line up on the longest direction
that room actually has.
The player who typed it is not listed, and the room is not told anyone
arrived.

## Descriptions

A description is one string in the rooms file. One line is enough.

```yaml
description: The front desk faces the street. A bell sits on the counter.
```

A literal block keeps the line breaks, and `look` prints them as they are.

```yaml
description: |-
  The front desk faces the street.
  A bell sits on the counter.
```

`|-` strips the final newline, so one blank line stays between the
description and `Also here` / `Nobody else is here.` A `|` block keeps that
newline and adds a second blank line there. `Exits` always follows the
company line by one blank line. A folded block (`>`) joins the lines into one
paragraph.
