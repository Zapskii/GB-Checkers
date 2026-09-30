#!/usr/bin/env python3
"""Procedural tiles and sprites for GB-DRAUGHTS -> gfx.h

The tile ids emitted here are a three-way contract (.claude/PLAN.md "Shared
invariants", this script, tools/probe.py).  All of them stay < 128 because
LCDC.4 = 0 puts BG tile data in the 0x9000 page: an id of 128 or more reads
0x8800.. instead, which is where the sprite tiles live.

Every tile is drawn as ASCII - '.' = colour 0, '1' = light, '2' = dark,
'#' = black - so there are no dependencies and `python3 mkgfx.py` runs
anywhere.  gfx.h is committed, so a plain `make` needs no Python at all.
"""

TILES = []          # TILES[i] is 16 bytes; the index IS the BG tile id
NAMES = []          # (define name, tile id), for the generated #defines
DRAWN = set()       # ids put() was actually asked for

BLANK = ["........"] * 8


def enc(rows):
    """8 rows of 8 chars -> 16 bytes of 2bpp tile data."""
    assert len(rows) == 8, rows
    out = []
    for r in rows:
        assert len(r) == 8, r
        lo = hi = 0
        for ch in r:
            c = {'.': 0, '1': 1, '2': 2, '#': 3}[ch]
            lo = (lo << 1) | (c & 1)
            hi = (hi << 1) | (c >> 1)
        out += [lo, hi]
    return out


def put(index, rows, name=None):
    """Draw one tile.  Gaps on the way are filled with blanks - which is exactly
    why the self-check at the bottom does not trust `len(TILES)`: a typo'd id
    would otherwise grow the array, and the numbering would shift, in silence."""
    while len(TILES) <= index:
        TILES.append(enc(BLANK))
    TILES[index] = enc(rows)
    DRAWN.add(index)
    if name:
        NAMES.append((name, index))


# ------------------------------------------------------------- board tiles
# A playable (dark) square is a flat mid tone; the light squares and the whole
# status area are tile 0, so nothing has to be cleared to get rid of text.
put(0, BLANK, "T_BLANK")
put(1, ["22222222"] * 8, "T_DARK")
put(2, ["11111111"] * 8, "T_DARK_HL")

# ------------------------------------------------------------------ pieces
# A man is a disc in three concentric bands (outer, groove, core); a king is
# the same disc with a cross in the core.  Drawn once at 16x16 - the size of a
# board cell - and cut into the four 8x8 quadrants the cell is made of, so
# quadrant q sits at (dx, dy) = (q & 1, q >> 1), matching probe.py's `cells()`.
# '2' is the dark square showing through, the only place a piece ever stands.
PIECE_KINDS = [
    ("black man",  "#", "1", False),
    ("white man",  "1", "#", False),
    ("black king", "#", "1", True),
    ("white king", "1", "#", True),
]

DISC = 225           # squared radius, in doubled px: a 15 px disc
GROOVE = (81, 169)   # ...the ring between these two squared radii
CROWN = 72           # ...and how far the king's cross reaches from the centre


def piece_rows(body, detail, king):
    rows = []
    for y in range(16):
        row = ""
        for x in range(16):
            dx, dy = 2 * x - 15, 2 * y - 15    # doubled: centre is (7.5, 7.5)
            d2 = dx * dx + dy * dy
            if d2 > DISC:
                row += "2"                     # off the disc: bare square
            elif king and d2 <= CROWN and (abs(dx) <= 2 or abs(dy) <= 2):
                row += detail                  # crown
            elif GROOVE[0] < d2 <= GROOVE[1]:
                row += detail                  # groove
            else:
                row += body
        rows.append(row)
    return rows


for kind, (label, body, detail, king) in enumerate(PIECE_KINDS):
    art = piece_rows(body, detail, king)
    for q in range(4):
        dx, dy = q & 1, q >> 1
        put(3 + 4 * kind + q,
            [r[8 * dx:8 * dx + 8] for r in art[8 * dy:8 * dy + 8]])
NAMES.append(("T_PIECE0", 3))

# ------------------------------------------------------------------- font
# 5x7 glyphs, inset one column from the left and two from the right, so a row
# of them is legible on a blank background and "A" of a string is one tile.
FONT = {
 '0': ".###.|#...#|#..##|#.#.#|##..#|#...#|.###.",
 '1': "..#..|.##..|..#..|..#..|..#..|..#..|.###.",
 '2': ".###.|#...#|....#|...#.|..#..|.#...|#####",
 '3': ".###.|#...#|....#|..##.|....#|#...#|.###.",
 '4': "...#.|..##.|.#.#.|#..#.|#####|...#.|...#.",
 '5': "#####|#....|####.|....#|....#|#...#|.###.",
 '6': ".###.|#....|#....|####.|#...#|#...#|.###.",
 '7': "#####|....#|...#.|..#..|.#...|.#...|.#...",
 '8': ".###.|#...#|#...#|.###.|#...#|#...#|.###.",
 '9': ".###.|#...#|#...#|.####|....#|...#.|.##..",
 'A': ".###.|#...#|#...#|#####|#...#|#...#|#...#",
 'B': "####.|#...#|#...#|####.|#...#|#...#|####.",
 'C': ".###.|#...#|#....|#....|#....|#...#|.###.",
 'D': "####.|#...#|#...#|#...#|#...#|#...#|####.",
 'E': "#####|#....|#....|####.|#....|#....|#####",
 'F': "#####|#....|#....|####.|#....|#....|#....",
 'G': ".###.|#...#|#....|#.###|#...#|#...#|.###.",
 'H': "#...#|#...#|#...#|#####|#...#|#...#|#...#",
 'I': ".###.|..#..|..#..|..#..|..#..|..#..|.###.",
 'J': "..###|...#.|...#.|...#.|...#.|#..#.|.##..",
 'K': "#...#|#..#.|#.#..|##...|#.#..|#..#.|#...#",
 'L': "#....|#....|#....|#....|#....|#....|#####",
 'M': "#...#|##.##|#.#.#|#.#.#|#...#|#...#|#...#",
 'N': "#...#|##..#|#.#.#|#..##|#...#|#...#|#...#",
 'O': ".###.|#...#|#...#|#...#|#...#|#...#|.###.",
 'P': "####.|#...#|#...#|####.|#....|#....|#....",
 'Q': ".###.|#...#|#...#|#...#|#.#.#|#..#.|.##.#",
 'R': "####.|#...#|#...#|####.|#.#..|#..#.|#...#",
 'S': ".####|#....|#....|.###.|....#|....#|####.",
 'T': "#####|..#..|..#..|..#..|..#..|..#..|..#..",
 'U': "#...#|#...#|#...#|#...#|#...#|#...#|.###.",
 'V': "#...#|#...#|#...#|#...#|#...#|.#.#.|..#..",
 'W': "#...#|#...#|#...#|#.#.#|#.#.#|##.##|#...#",
 'X': "#...#|#...#|.#.#.|..#..|.#.#.|#...#|#...#",
 'Y': "#...#|#...#|.#.#.|..#..|..#..|..#..|..#..",
 'Z': "#####|....#|...#.|..#..|.#...|#....|#####",
}

# 19 is a deliberate blank: it puts the digits on 20 and the letters on 30,
# which is the numbering probe.py and PLAN.md both spell out.
put(19, BLANK)
for i, ch in enumerate("0123456789"):
    put(20 + i, ["." + r + ".." for r in FONT[ch].split("|")] + ["........"])
for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    put(30 + i, ["." + r + ".." for r in FONT[ch].split("|")] + ["........"])
NAMES.append(("T_DIGIT0", 20))
NAMES.append(("T_LETTER0", 30))

# ------------------------------------------------------------- sprite data
# OBJ tiles are a VRAM bank of their own, so sprite tile 0 has nothing to do
# with BG tile 0 and this is a separate array.  Colour 0 is transparent on a
# sprite, so '.' here is a hole rather than a colour.  SPRITES_8x8: one ring
# tile, TWO OAM slots sharing it - SPR_CURSOR where the cursor rests, SPR_SEL
# marking the picked-up piece while one is up.  OAM coordinates are not
# screen ones: the DMG draws an 8x8 sprite 8 px left of and 16 px above the
# position written, so the placing lives in main.c and adds both offsets.
# Parked at (0, 0) = hidden until something is to be marked.
CURSOR = ["########",
          "#......#",
          "#......#",
          "#......#",
          "#......#",
          "#......#",
          "#......#",
          "########"]
SPRITES = [enc(CURSOR)]

# ------------------------------------------------------------- self-check
TILE_IDS = sorted(DRAWN)
assert len(TILES) == 56, "expected 56 tiles, got %d" % len(TILES)
assert TILE_IDS == list(range(56)), "unaccounted tile ids: %r" % (
    sorted(set(range(56)) - DRAWN))
assert max(TILE_IDS) < 128, "tile id %d >= 128 reads the sprite bank" % max(TILE_IDS)

# ----------------------------------------------------------------- output
lines = [
    "/* Generated by mkgfx.py - do not edit.  Run `make gfx` to regenerate. */",
    "#ifndef GFX_H",
    "#define GFX_H",
    "",
    "#include <stdint.h>",
    "",
    "/* BG tile ids, all < 128: LCDC.4 = 0 puts BG tile data in the 0x9000 page,",
    " * and an id >= 128 would alias into the sprite bank at 0x8000 instead. */",
    "#define GFX_TILE_COUNT %d" % len(TILES),
]
for name, val in NAMES:
    lines.append("#define %-11s %d" % (name, val))
lines += [
    "",
    "/* 3..18: four kinds x four cell quadrants.  kind = {black, white} x",
    " * {man, king}; quadrant (dx, dy) of a 2x2-tile cell. */",
    "#define T_PIECE(kind, dx, dy) (T_PIECE0 + 4 * (kind) + 2 * (dy) + (dx))",
    "",
    "/* Two OAM slots share the one 8x8 ring tile: SPR_CURSOR is the cursor,",
    " * SPR_SEL marks the picked-up piece in ST_TARGET.  OAM is not screen",
    " * coordinates - the DMG draws a sprite at (x-8, y-16) of what is written",
    " * - so the placing lives in main.c and adds both offsets.  Parked at",
    " * (0, 0) = hidden. */",
    "#define GFX_SPRITE_COUNT %d" % len(SPRITES),
    "#define SPR_CURSOR 0",
    "#define SPR_SEL 1",
    "",
    "static const uint8_t gfx_tiles[] = {",
]
for i, t in enumerate(TILES):
    lines.append("    " + ",".join("0x%02X" % b for b in t) + ",  /* %d */" % i)
lines += ["};", "", "static const uint8_t gfx_sprites[] = {"]
for i, t in enumerate(SPRITES):
    lines.append("    " + ",".join("0x%02X" % b for b in t) + ",  /* %d */" % i)
lines += ["};", "", "#endif", ""]

with open("gfx.h", "w") as f:
    f.write("\n".join(lines))
print("gfx.h: %d tiles (%d bytes), %d sprite tiles" %
      (len(TILES), len(TILES) * 16, len(SPRITES)))
