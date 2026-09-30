#!/usr/bin/env python3
"""Headless check harness for GB-DRAUGHTS.

WHY THIS EXISTS: tools/shot.py takes a picture, which only tells you the screen
is not blank. This drives the game and asserts on what it did -- the BG tilemap
for anything you can see, and main.c's own statics (parsed out of draughts.map)
for the rules the screen cannot show. It is the emulator counterpart to
tests/test_sim.c: that one covers the maths in sim.h, this one covers the wiring
between the maths and main.c.

    make probe            # or: tools/probe.py draughts.gb draughts.map

EXIT CODES -- the driver's power gate depends on this split:
    0  every check passed
    1  a check failed   (including "a symbol this phase introduces is absent",
                         which is what a power gate on an older ROM looks like)
    2  the harness broke (ROM will not boot, map unreadable, PyBoy raised)

This is a DEV TOOL. Nothing in the build depends on it.
"""
import re
import sys

from pyboy import PyBoy

KEYS = {"R": "right", "L": "left", "U": "up", "D": "down",
        "A": "a", "B": "b", "S": "start", "T": "select"}

# --- tile ids: must match mkgfx.py (PLAN.md "Shared invariants") -------------
T_BLANK, T_DARK, T_DARK_HL = 0, 1, 2
T_PIECE0 = 3                    # 4 kinds x 4 quadrants: 3..18
T_DIGIT0, T_LETTER0 = 20, 30    # 20..29, 30..55

# BG colour -> rendered pixel, for BGP = 0xE4 (colour 0 is the lightest shade).
# PyBoy's DMG palette renders the four shades as 255 / 153 / 85 / 0 -- the two
# middle greys are 0x99 and 0x55, measured off the screen, not the 170 a naive
# ramp would suggest.  P1 only ever reads colours 0 and 2, which is why it got
# away with assuming; P2's pieces are the first thing to use colours 1 and 3.
SHADE = (255, 153, 85, 0)
LIGHT_BG = SHADE[0]             # T_BLANK
DARK_BG = SHADE[2]              # T_DARK

# --- layout ----------------------------------------------------------------
MAP0 = 0x9800                   # BG tilemap
MAP_W = 32
BOARD_TW = BOARD_TH = 16        # tiles; 8x8 cells of 2x2 tiles
BOARD_TILES = [(x, y) for y in range(BOARD_TH) for x in range(BOARD_TW)]


def glyph(c):
    if "A" <= c <= "Z":
        return ord(c) - ord("A") + T_LETTER0
    if "0" <= c <= "9":
        return ord(c) - ord("0") + T_DIGIT0
    if c == " ":
        return T_BLANK
    raise ValueError("no tile for %r" % c)


def cells(sq_row, sq_col):
    """Square -> the 2x2 tiles of its cell."""
    return [(2 * sq_col + dx, 2 * sq_row + dy) for dy in (0, 1) for dx in (0, 1)]


def parse_map(path):
    """`     0000C0B6  Fmain$board$0$0   main$` -> {'board': 0xC0B6}.

    Only file-scope objects of main (Fmain$...), which is all this needs."""
    out = {}
    for line in open(path):
        m = re.match(r"\s*([0-9A-Fa-f]{8})\s+Fmain\$(\w+?)(?:\$\d+_\d+)?\$\d+", line)
        if m:
            out[m.group(2)] = int(m.group(1), 16)
    return out


fails = []


def check(cond, msg):
    print("  %s  %s" % ("ok  " if cond else "FAIL", msg))
    if not cond:
        fails.append(msg)


class Game:
    def __init__(self, rom, mapfile):
        self.p = PyBoy(rom, window="null", sound_emulated=False)
        self.addr = parse_map(mapfile)

    def need(self, *names):
        """Record a failure for each symbol absent from the map.

        A missing symbol is an ASSERTION failure, not a harness error: it means
        the phase that introduces it has not run. That is exactly the question a
        power gate asks, and it must answer 1, not 2."""
        for n in names:
            check(n in self.addr, "symbol %s is in the map" % n)
        return all(n in self.addr for n in names)

    def var(self, name, n=1):
        b = self.p.memory[self.addr[name]:self.addr[name] + n]
        return b[0] if n == 1 else list(b)

    def u32(self, name):
        b = self.p.memory[self.addr[name]:self.addr[name] + 4]
        return b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)

    def tile(self, x, y):
        """BG tile id at map cell (x, y)."""
        return self.p.memory[MAP0 + y * MAP_W + x]

    def cell_flat(self, sq_row, sq_col, px):
        """True when every one of a cell's 16x16 rendered pixels is `px`."""
        a = self.p.screen.ndarray
        block = a[16 * sq_row:16 * sq_row + 16, 16 * sq_col:16 * sq_col + 16, 0]
        return bool((block == px).all())

    def px(self, x, y):
        """One rendered pixel of the BG (sprites aside -- see cell_flat)."""
        return int(self.p.screen.ndarray[y, x, 0])

    def run(self, frames, buttons=""):
        """Press -> tick -> release, PER FRAME. A press held across ticks is not
        the same input: the game samples `pressed` edge-per-frame."""
        ks = [KEYS[c] for c in buttons if c in KEYS]
        for _ in range(frames):
            for k in ks:
                self.p.button_press(k)
            self.p.tick(1, True)
            for k in ks:
                self.p.button_release(k)

    def start(self, boot=150, settle=30):
        """PyBoy swallows input for several ticks after its own boot splash, so
        START is held 20 frames rather than tapped."""
        self.run(boot)
        self.run(20, "S")
        self.run(settle)

    def board_tiles(self):
        """Every board-region tile id, as a dict."""
        return {(x, y): self.tile(x, y) for x, y in BOARD_TILES}

    def cell_is(self, sq_row, sq_col, tile_id):
        return all(self.tile(x, y) == tile_id for x, y in cells(sq_row, sq_col))

    def find_text(self, s):
        """Rows containing the string, as a list of (row, col)."""
        want = [glyph(c) for c in s]
        hits = []
        for y in range(18):
            row = [self.tile(x, y) for x in range(MAP_W)]
            for x in range(MAP_W - len(want) + 1):
                if row[x:x + len(want)] == want:
                    hits.append((y, x))
        return hits


# ---------------------------------------------------------------- P1 --------
def p1_boot_title_board(g):
    """Title drawn from font tiles, and START reaches an 8x8 checkerboard."""
    print("P1 title screen")
    g.run(150)          # the Game constructor ticks NOTHING: without this every
                        # read below is of an emulator that has not run main() yet
    t = g.find_text("GB DRAUGHTS")
    s = g.find_text("PRESS START")
    check(len(t) > 0, "the title spells GB DRAUGHTS at %r" % (t,))
    check(len(s) > 0, "the prompt spells PRESS START at %r" % (s,))
    check(bool(t) and bool(s) and s[0][0] > t[0][0],
          "the prompt sits below the title (%r vs %r)" % (t[:1], s[:1]))
    print("P1 board grid after START")
    g.start()
    # Counted per SQUARE, not per tile.  A square is a 2x2 block of tiles, so
    # the 32 dark squares are 128 dark tiles and a tile count can never be 32 --
    # counting tiles here would fail on a perfectly correct board.  A square
    # whose four tiles disagree is neither dark nor light.
    #
    # A cell may also carry a whole piece (from P2 on), which is why this
    # classifies rather than just counting fills: a light square is never
    # playable so it is never a piece, and a bare playable square is the dark
    # fill.  Everything else -- a piece on a light square, a half-drawn piece,
    # the grid off by one -- lands in `bad`.
    bad = []
    n_light = 0
    for r in range(8):
        for c in range(8):
            playable = bool((r + c) & 1)
            if g.cell_is(r, c, T_BLANK):
                n_light += 1
                if playable:
                    bad.append((r, c, "light fill on a playable square"))
            elif g.cell_is(r, c, T_DARK):
                if not playable:
                    bad.append((r, c, "dark fill off the playable squares"))
            elif not (playable and cell_kind(g, r, c) is not None):
                bad.append((r, c, "neither a board fill nor a whole piece"))
    check(not bad, "cells are the checkerboard, plus pieces only on playable "
                   "squares (%d wrong: %r)" % (len(bad), bad[:4]))
    check(n_light == 32, "exactly 32 light cells in the board region (%d)" % n_light)

    # The tilemap records WHICH id was written, never whether the bank behind
    # it holds the art.  GBDK's crt0 preloads its own ASCII font at 0x9000, so
    # a ROM that never loaded a tile bank still renders glyph-shaped pixels and
    # still spells the title in the map -- neither the checks above nor an
    # "is the tile data blank" check can tell the two apart.  The rendered
    # screen can: a board cell is a flat fill, so anything else behind the id
    # shows up.  This is what makes the tile-id budget (< 128, i.e. inside the
    # 0x9000 page) an assertion rather than a comment.
    #
    # Piece cells are excluded: they are deliberately not flat, and
    # p2_start_position does the same trick for them (tiles 3..18 are just as
    # able to hide the crt0 font behind their ids).
    #
    # It reads whole cells, sprites included, so it holds only while nothing is
    # drawn over the board.  The cursor sprite is parked off-screen at (0, 0)
    # here; a phase that moves it onto a cell has to park it in this state or
    # narrow this check to the pixels the cursor does not cover.
    print("P1 rendered board")
    bare = [(r, c) for r in range(8) for c in range(8)
            if g.cell_is(r, c, T_DARK) or g.cell_is(r, c, T_BLANK)]
    bad = [(r, c) for r, c in bare
           if not g.cell_flat(r, c, DARK_BG if (r + c) & 1 else LIGHT_BG)]
    check(not bad, "every bare board cell renders flat in its own colour (%d "
          "off: %r)" % (len(bad), bad[:4]))


# ---------------------------------------------------------------- P2 --------
# The engine's start position, and the square -> cell mapping board_geom.h
# emits as SQ_ROW/SQ_COL.  Board is three little-endian uint32s.
BLACK_START, WHITE_START = 0x0FFF, 0xFFF00000
QUADS = ((0, 0), (1, 0), (0, 1), (1, 1))    # in `cells()` order
BLACK_PIECE_PX, WHITE_PIECE_PX = SHADE[3], SHADE[1]   # the disc bodies
PIECE_ART_PX = (5, 5)                       # disc body, for all four kinds

WRAM0 = (0xC000, 0xE000)                    # the 8 KB the ROM addresses as WRAM


def T_PIECE(kind, dx, dy):
    """Mirrors gfx.h's macro: kind = {black, white} x {man, king}."""
    return T_PIECE0 + 4 * kind + 2 * dy + dx


def sq_row(sq):
    return sq >> 2


def sq_col(sq):
    return ((sq & 3) << 1) + (((sq >> 2) & 1) ^ 1)


def board_bytes(black, white, kings):
    return b"".join(w.to_bytes(4, "little") for w in (black, white, kings))


def cell_kind(g, r, c):
    """The piece kind drawn in a cell, or None if the cell is not a whole
    piece -- bare board, half-drawn, or garbage."""
    got = [g.tile(x, y) for x, y in cells(r, c)]
    for kind in range(4):
        if got == [T_PIECE(kind, dx, dy) for dx, dy in QUADS]:
            return kind
    return None


def p2_start_position(g):
    """24 men at the start position, drawn from the engine's own Board."""
    print("P2 start position")
    g.start()
    # A missing symbol is the power gate's answer (see Game.need): the phase
    # that introduces `board` has not run.  Return before anything reaches for
    # it, so this exits 1 rather than blowing up as a harness error.
    if not g.need("board"):
        return
    addr = g.addr["board"]

    # 1. what the SCREEN shows, read without consulting the engine at all.
    kinds = {(r, c): cell_kind(g, r, c) for r in range(8) for c in range(8)}
    black = sorted(k for k, v in kinds.items() if v == 0)
    white = sorted(k for k, v in kinds.items() if v == 1)
    check(len(black) == 12, "12 black men drawn (%d: %r)" % (len(black), black))
    check(len(white) == 12, "12 white men drawn (%d: %r)" % (len(white), white))
    check(all(r <= 2 for r, _ in black), "black men on rows 0-2 (%r)" % (black,))
    check(all(r >= 5 for r, _ in white), "white men on rows 5-7 (%r)" % (white,))
    check(not [k for k, v in kinds.items() if v in (2, 3)],
          "no kings at the start position")
    check(not [k for k, v in kinds.items() if v is not None and 3 <= k[0] <= 4],
          "rows 3-4 carry no pieces")

    # 2. the engine's own bytes, straight out of WRAM.
    raw = bytes(g.p.memory[addr:addr + 12])
    want = board_bytes(BLACK_START, WHITE_START, 0)
    check(raw == want, "board == {0x%04X, 0x%08X, 0} at 0x%04X (got %s)"
          % (BLACK_START, WHITE_START, addr, raw.hex()))

    # 3. THE assertion this phase exists for.  A renderer and a state can both
    # look right and disagree -- a piece one square over, or drawn from a copy
    # of the board that is no longer the live one -- and nothing above catches
    # it, because (1) never looked at the engine and (2) never looked at the
    # screen.  This is the seam.
    e_black = int.from_bytes(raw[0:4], "little")
    e_white = int.from_bytes(raw[4:8], "little")
    state = {}
    for sq in range(32):
        m = 1 << sq
        state[(sq_row(sq), sq_col(sq))] = (0 if e_black & m else
                                           1 if e_white & m else None)
    wrong = sorted((k, kinds[k], v) for k, v in state.items() if kinds[k] != v)
    check(not wrong, "the pieces drawn are the board, square for square "
                     "(cell, drawn, board): %r" % (wrong[:4],))

    # 4. tiles 3..18 have the same hole P1 describes for the board tiles: the
    # map records the id, never whether the bank behind it holds the art.  A
    # piece's corners are the bare square showing through and its body is at
    # (5, 5) of the cell, so the rendered screen tells a disc from the crt0
    # font glyph that would otherwise sit behind the same id.
    bad = []
    for (r, c), kind in sorted(kinds.items()):
        if kind is None:
            continue
        body = BLACK_PIECE_PX if kind in (0, 2) else WHITE_PIECE_PX
        got = (g.px(16 * c + PIECE_ART_PX[0], 16 * r + PIECE_ART_PX[1]),
               g.px(16 * c, 16 * r))
        if got != (body, DARK_BG):
            bad.append((r, c, kind, got, (body, DARK_BG)))
    check(not bad, "every piece cell renders as a disc on the bare square "
                   "(%d off: %r)" % (len(bad), bad[:4]))

    # 5. One board, not two.  The 12 bytes above are known a priori, so brute
    # scan WRAM for them: exactly one hit, at the address the map named.  A
    # second hit means main.c is drawing from a copy probe.py never looked at.
    wram = bytes(g.p.memory[WRAM0[0]:WRAM0[1]])
    hits = [WRAM0[0] + i for i in range(len(wram) - len(want) + 1)
            if wram[i:i + len(want)] == want]
    check(hits == [addr], "WRAM holds exactly one start position, at the map's "
                          "board 0x%04X (%r)" % (addr, [hex(h) for h in hits]))


CHECKS = [("p1_boot_title_board", p1_boot_title_board),
          ("p2_start_position", p2_start_position)]


def main(rom, mapfile):
    for name, fn in CHECKS:
        g = Game(rom, mapfile)
        try:
            fn(g)
        finally:
            g.p.stop()

    if fails:
        print("FAILED: %d of %d checks" % (len(fails), len(CHECKS)))
        return 1
    print("OK: %d checks passed" % len(CHECKS))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "draughts.gb",
                      sys.argv[2] if len(sys.argv) > 2 else "draughts.map"))
    except Exception as e:                      # noqa: BLE001 -- the exit-2 edge
        print("HARNESS ERROR: %s: %s" % (type(e).__name__, e))
        sys.exit(2)
