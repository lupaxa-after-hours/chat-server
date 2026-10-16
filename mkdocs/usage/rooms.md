# Rooms

## Where People Start

Players start in the room named by `start`. Newbies appear in `newbie_start`
when that key is set, and otherwise in `start`. In the example office that
room is Reception.

## Moving

`go west` moves through an exit. The mover sees the description, who else
is there, and the exits.

The destination room may set optional `enter` in the rooms file.

| The destination has `enter` | The room you leave hears      |
| --------------------------- | ----------------------------- |
| Yes                         | Your name, then that sentence |
| No                          | `Ada goes west to Kitchen.`   |

The new room is told `Ada arrives.` Residents may change their own arrive
line with `entermsg`.

## A Full Room

A full room refuses the move. The player stays put, and only they see
`Kitchen is full.`

If the starting room is full when a name is chosen, the server tells them
that room's name and disconnects. The name is not kept.

A room may omit `max_occupancy`. That room has no limit. A present capacity
is a whole number of at least 1.

## The Map

`map` prints a grid with the current room in the centre.

| Your frame  | The map |
| ----------- | ------- |
| Wide enough | 5 by 5  |
| Narrower    | 3 by 3  |

One blank line appears above the map and one below it. Every room inside
that window is shown, including one reached by walking through other rooms.
The room you are in is green when colour is on. A line is drawn only where
two shown rooms connect. Up and down are listed under the grid when the
current room has those exits.

## Join and Main

`join <name>` moves you into that player's room when the room has space.
The room you leave hears `Ada goes to join Bea.`

`main` moves you to the start room.

| You are…           | What happens                                                  |
| ------------------ | ------------------------------------------------------------- |
| Somewhere else     | The room you leave hears `enter`, or `Ada goes to Reception.` |
| Already there      | Nothing happens                                               |
| Facing a full room | You stay, and you are told it is full                         |

The start room hears `Ada arrives.`

## Look

`look` shows the description, who else is there, and the exits. Each real
exit is `direction -> room`. The arrows line up on the longest direction
that room actually has. The player who typed it is not listed, and the room
is not told anyone arrived.

## Descriptions

A room may set optional `enter`. When someone moves into that room, the
room they leave hears your name and that sentence.

```yaml
enter: opens the door and enters the server room
```

A description is one string. One line is enough.

```yaml
description: The front desk faces the street. A bell sits on the counter.
```

A literal block keeps the line breaks, and `look` prints them as they are.

```yaml
description: |-
  The front desk faces the street.
  A bell sits on the counter.
```

- A `|-` block strips the final newline. One blank line stays before the company line.
- A `|` block keeps that newline, so a second blank line appears there.
- A `>` block joins the lines into one paragraph.

`Exits` always follows the company line by one blank line. The company line
is `Also here` or `Nobody else is here.`
