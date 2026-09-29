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
    t = g.find_text("GB DRAUGHTS")
    s = g.find_text("PRESS START")
    check(len(t) > 0, "the title spells GB DRAUGHTS at %r" % (t,))
    check(len(s) > 0, "the prompt spells PRESS START at %r" % (s,))
    check(bool(t) and bool(s) and s[0][0] > t[0][0],
          "the prompt sits below the title (%r vs %r)" % (t[:1], s[:1]))

    print("P1 board grid after START")
    g.start()
    tiles = g.board_tiles()
    dark = sorted(k for k, v in tiles.items() if v == T_DARK)
    light = sorted(k for k, v in tiles.items() if v == T_BLANK)
    check(len(dark) == 32, "exactly 32 dark tiles in the board region (%d)" % len(dark))
    check(len(light) == 32, "exactly 32 light tiles in the board region (%d)" % len(light))

    bad = []
    for row in range(8):
        for col in range(8):
            want = T_DARK if (row + col) & 1 else T_BLANK
            if not g.cell_is(row, col, want):
                bad.append((row, col, want))
    check(not bad, "every cell is dark iff (row+col) is odd (%d wrong: %r)"
          % (len(bad), bad[:4]))


CHECKS = [("p1_boot_title_board", p1_boot_title_board)]


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
