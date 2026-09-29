# GB-Draughts

English draughts (checkers) for the Game Boy (DMG), written in GBDK-2020 C
with no game engine. Same lineage as GB-Protector: procedural art via a
Python script, and hardware-independent logic in a `sim.h` that's tested
on the host before anything touches GBDK.

**Current stage: rules engine only.** There is no `main.c`, no rendering,
no `mkgfx.py` yet - just the board geometry and the move-generation rules,
with a test suite. `make test` is the target that works right now; `make`
(the ROM) will fail until the rendering stage adds `main.c`.

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

## Not implemented yet (by design, see the plan)

- `evaluate()` / `search_best()` - the AI opponent. That's Milestone 2;
  Milestone 1 is 2-player pass-and-play only.
- Rendering, input, title screen, SGB border, sound - all pending `main.c`.
- International/10x10 draughts or flying kings (different capture rules
  entirely - would touch move generation throughout, not attempted here).
- Draw detection (40-move/repetition rules).
- Save/load to cartridge SRAM (needs a battery-backed MBC - a hardware/BOM
  decision, not just a code change).

## Build

    make test     # host unit tests, plain gcc, no emulator - works now
    make geom     # regenerate board_geom.h from boardgen.py (committed)
    make          # draughts.gb - not yet buildable, no main.c
    make image    # build the Docker toolchain image (only needed once)
    make clean

## Layout

| File               | What it is                                                  |
|--------------------|--------------------------------------------------------------|
| `boardgen.py`      | Generates the 32-square diagonal neighbor table + self-checks |
| `board_geom.h`     | Generated, committed - a plain `make test` needs no Python   |
| `sim.h`            | Pure rules engine (move gen, capture, chains, promotion); no GBDK |
| `tests/test_sim.c` | Geometry checks, hand-built positions, and random self-play fuzzing |

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
kind of thing that's easy to reintroduce in `main.c` later.

## License

MIT
