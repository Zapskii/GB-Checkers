# GB-Draughts

English draughts (checkers) for the Game Boy (DMG), written in GBDK-2020 C
with no game engine. Same lineage as GB-Protector: procedural art via a
Python script, and hardware-independent logic in a `sim.h` that's tested
on the host before anything touches GBDK.

**Current stage: Milestone 2 in progress - there is a 1-player game.** Milestone 1
is complete: a full two-player pass-and-play game with title screen, board and
cursor, mandatory capture, multi-jump chains, promotion, win detection and
restart. Milestone 2 adds an opponent: pick **1 PLAYER** on the title screen and
the machine plays white, the human black. `make` builds the ROM, `make test` runs
the rules engine's unit tests on the host, and `make probe` drives the ROM in a
headless emulator and asserts on what it did.

## Controls

| Button | What it does |
|--------|--------------|
| SELECT | on the title screen, switch between **2 PLAYERS** and **1 PLAYER** |
| START  | new game - from the title screen, and from a finished game |
| d-pad  | move the cursor. Before a piece is picked up it walks square to square along the diagonals, resting only on squares you can actually use. Once a piece is up it cycles that piece's landing squares, one per press (hold to repeat) |
| A      | pick up the piece under the cursor; press again to play the move. Picking up *seats* the cursor on a landing square, so the second press always has somewhere to go |
| B      | put a picked-up piece back down; on a finished game, new game |

## Sound

A hop clicks (CH1), a capture adds a thud underneath it (CH2), and a hop that
crowns a man adds a crash (CH4) - so a capturing promotion plays two at once,
on two different channels. All three fire from `play()`, the one path both
sides' moves go through, so the machine's replies sound exactly like yours.
Every envelope decays to silence on its own, so nothing ever has to switch a
channel off afterwards.

## 1 player

The machine is always white, so the human always opens - black moves first. Its
turn is a blocking one: the status row says **THINKING**, the screen is blitted
with that line *before* the search starts, and the loop is frozen for as long as
the search takes. On the current build that is a few seconds at the start
position; P11 measures it properly and pins the worst case. Because the think is
blocking, START and B pressed during it are dropped - the game restarts a moment
later instead of not at all.

The machine chooses from the same legal move list a human does, at the same
depth every turn, and its move is applied through the same `play()` the human's
goes through - so mandatory capture, the chain rule and
promotion-stops-the-chain apply to it by construction rather than by a second
implementation. It never drives the cursor or animates.

The d-pad is rotated 45 degrees: **UP** and **RIGHT** both go up the screen,
**DOWN** and **LEFT** both go down, and nothing moves straight sideways. A
checker's neighbour is diagonal, so each button has to pick one of the four
diagonals, and this is the one the four buttons cover. (Which direction you
press only matters to the walk, before a piece is picked up. With a piece up,
any of the four cycles to the next landing square.)

    UP    up-left        RIGHT  up-right
    LEFT  down-left      DOWN   down-right

The cursor can step *over* squares it cannot land on, so a piece that cannot
move this turn is not something you can pick up by accident: with a capture
available, a piece that has no capture has no legal move at all and the cursor
will not settle on it. Bare board *is* a landing square, though, because the
four men that can move at the start sit one row apart in a straight line, and a
cursor that could rest only on pieces could never get from one to the next.

Once a piece is picked up, though, walking it is not a good way to choose where
it goes: a piece's landing squares are usually *not* neighbours of each other
(the man on 9 can go to 13 or 14, and 13's only neighbours are 5 and 17), so
"walk that way" has no path between them and a press between them is dead. So a
pickup seats the cursor on the first landing square, and each d-pad press then
moves it to the next one and wraps - which key you press makes no difference.
Two presses of A, picking up and playing, always move a piece.

Mid-chain there is nothing to put back - the capture is already on the board -
so B does nothing until the chain ends. Before a chain, B drops the piece and
leaves the cursor on the square it had seated on, so the next A picks that same
piece up again.

A game ends when the side to move has no legal move: no pieces left, or every
piece blocked. The status row says who won and START (or B) starts a new game.

## Rules implemented

- Standard 8x8 board, 32 playable (dark) squares.
- Men move diagonally forward only; kings move in all four directions.
- **Mandatory capture**: if any piece of the side to move can capture,
  only captures are legal that turn - no quiet move is offered instead.
- **Multi-jump chains**: after a capture, if the same piece can capture
  again, it must - the chain never falls back to a different piece or a
  simple move.
- **Promotion**: a man reaching the far row becomes a king. A man that
  promotes mid-chain stops immediately, even though the new king could
  geometrically continue - see `move_causes_promotion()` in `sim.h` for
  why this needs its own check rather than living inside `must_continue()`.
- **Win/loss**: the side to move with no legal move at all - no pieces, or
  every piece blocked - has lost. The question is asked when a turn
  completes and the other side is handed the move, so a chain that can
  continue has not ended a turn and cannot lose the game yet.

## Not implemented yet (by design, see the plan)

- Difficulty levels, or a machine that plays black. There is one depth, chosen
  by measuring the worst-case think (P11), and the machine is white only.
- Thinking that can be interrupted: the search is a blocking call, so the loop
  genuinely stops while the machine replies. Time-slicing it is the named
  upgrade path if the measurement ever demands one.
- International/10x10 draughts or flying kings (different capture rules
  entirely - would touch move generation throughout, not attempted here).
- Draw detection (40-move/repetition rules). Because there is none, a game
  between two players who keep shuffling can run forever - acceptable for
  this scope, and worth stating rather than leaving to be discovered.
- Save/load to cartridge SRAM (needs a battery-backed MBC - a hardware/BOM
  decision, not just a code change).
- An SGB border, and a win jingle. The moves themselves already make their
  three noises.

## Build

    make          # draughts.gb - the ROM
    make test     # host unit tests for sim.h, plain gcc, no emulator
    make probe    # headless PyBoy checks against the built ROM
    make geom     # regenerate board_geom.h from boardgen.py (committed)
    make gfx      # regenerate gfx.h from mkgfx.py (committed)
    make usage    # ROM/RAM headroom
    make image    # build the Docker toolchain image (only needed once)
    make clean

`make probe` builds a `-debug` ROM into `draughts.gb` (the linker map has to
list the statics the probe reads), so if you want the release binary, run
`make` *after* probing rather than before.

## Layout

| File               | What it is                                                  |
|--------------------|--------------------------------------------------------------|
| `main.c`           | Everything that touches hardware: state machine, input, rendering, and the machine's turn |
| `boardgen.py`      | Generates the 32-square diagonal neighbor table + self-checks |
| `board_geom.h`     | Generated, committed - a plain `make test` needs no Python   |
| `mkgfx.py`         | Generates `gfx.h` (tile art, ids, the cursor sprite)          |
| `gfx.h`            | Generated, committed. Included by `main.c` only               |
| `sim.h`            | Pure rules engine (move gen, capture, chains, promotion) and the search; no GBDK |
| `tests/test_sim.c` | Geometry checks, hand-built positions, and random self-play fuzzing |
| `tools/probe.py`   | Headless emulator checks: the tilemap, and `main.c`'s own statics |

## Headroom

`make usage` on the release ROM:

| Bank    | Size  | Used       | Free       |
|---------|-------|------------|------------|
| ROM_0   | 16384 | 9411 (57%) | 6973 (43%) |
| WRAM_LO | 4096  | 1556 (38%) | 2540 (62%) |

The AI is most of the WRAM: its search arena is `AI_MAX_PLY` `AiFrame`s, about a
kilobyte, held at file scope in `main.c` rather than on the stack - a kilobyte of
stack on a DMG is not "large", it is a crash.

## How the board is represented

Squares are numbered 0-31, row-major over just the dark squares (the
standard English-draughts numbering, 0-based). A `Board` is three 32-bit
masks - `black`, `white`, `kings` - rather than an 8x8 array; adjacency
comes from a precomputed `NEIGH[32][4]` table rather than row/col math at
runtime, since diagonal offsets aren't uniform (row parity shifts them).

## Testing strategy

This rules engine gets a different kind of test coverage than GB-Protector's
physics-driven `sim.h`, because "is this move legal" is a much more
checkable question than "does this feel right":

- **Geometry symmetry** - every `NEIGH` entry is checked against its
  reverse (if A's neighbor in direction D is B, B's neighbor in the
  opposite direction is A), the same style of exhaustive check as
  GB-Protector's `torus_dx` brute-force cross-check.
- **Hand-built positions** - one capture available and nothing else,
  mandatory capture across a whole side, a two-hop chain, promotion,
  promotion-stops-the-chain, a fully blocked piece, kings moving
  backward, men forbidden from moving backward.
- **Random self-play fuzzing** - thousands of random-legal-move games
  played to a ply cap, asserting after every hop (mid-chain included)
  that piece count never increases, the two sides never overlap a
  square, and every king is still a real piece. This can't prove no
  illegal position is ever reachable, but it exercises mandatory
  capture, chains, and promotion together across far more positions
  than the hand-built tests alone.

One real bug was caught this way during development: `must_continue()`
alone doesn't know a piece just promoted (it only sees board state, not
move history), so a naive turn loop would let a freshly-crowned king
keep capturing in the same turn it was promoted. The fix
(`move_causes_promotion()`, checked by the caller *before* trusting
`must_continue()`) is documented in `sim.h` precisely because it's the
kind of thing every turn loop reintroduces - `main.c`'s `play()` and
`tools/probe.py`'s `p5_promotion_stops_chain` both pin it now.

The unit tests cover the rules, but they cannot cover the *wiring*: whether
`main.c` asks the rules the right question at the right moment, and whether
the screen agrees with the board. `tools/probe.py` fills that gap - it boots
the ROM in a headless emulator, plays it with button presses, and asserts on
the BG tilemap and on `main.c`'s own variables (read out of the linker map).
It is where the promotion trap and the end-of-turn loss test are pinned: a
rules bug that only ever shows up through the turn loop is invisible to
`tests/test_sim.c`.

## License

MIT
