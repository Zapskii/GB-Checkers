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
OAM = 0xFE00                    # 40 sprite entries of 4 bytes (y, x, tile, attr)
MAP_W = 32
BOARD_TW = BOARD_TH = 16        # tiles; 8x8 cells of 2x2 tiles
BOARD_TILES = [(x, y) for y in range(BOARD_TH) for x in range(BOARD_TW)]

# A d-pad hold long enough for the game to sample it exactly ONCE, i.e. one
# walk() call and no repeat notch.  Measured against this ROM: 1..7 frames are
# missed entirely, 9..25 all give exactly one step (8 -> 13 from the start
# square).  12 sits in the middle of that plateau.  PyBoy's clocks are
# unfaithful, so this is calibrated, not derived -- re-measure it if the main
# loop's timing changes.
STEP_HOLD = 12


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

    def cursor_box(self):
        """The cursor sprite's 8x8 px box on the screen, or None when it is
        parked.  OAM y = 0 is the DMG's "hide this sprite" position, which is
        where it boots (P1 relies on it) and where a y >= 144 or x >= 168
        parks it too."""
        y, x = self.p.memory[OAM], self.p.memory[OAM + 1]
        if y == 0 or y >= 144 or x >= 168:
            return None
        return (x, y, x + 8, y + 8)

    def cursor_xy(self):
        """The cursor sprite's OAM (x, y) -- the raw bytes, in x-then-y order
        so it reads like a screen coordinate."""
        return (self.p.memory[OAM + 1], self.p.memory[OAM])

    def visible_sprites(self):
        """The OAM slots with a sprite on screen.  A DMG hides a sprite by
        parking it (y = 0 or y >= 144), which is also what an untouched OAM
        slot looks like."""
        out = []
        for i in range(40):
            y, x = self.p.memory[OAM + 4 * i], self.p.memory[OAM + 4 * i + 1]
            if 0 < y < 144 and x < 168:
                out.append(i)
        return out

    def cell_flat(self, sq_row, sq_col, px):
        """True when every one of a cell's 16x16 rendered pixels is `px`.

        A visible sprite is drawn OVER the background, so the pixels inside
        the cursor's own 8x8 box are the sprite's, not the tile's.  Those are
        masked out here rather than read: p3_select_and_highlight owns the
        cursor and asserts where it is, and every other pixel of the cell is
        still the BG's."""
        a = self.p.screen.ndarray
        x0, y0 = 16 * sq_col, 16 * sq_row
        block = a[y0:y0 + 16, x0:x0 + 16, 0].copy()
        box = self.cursor_box()
        if box:
            bx0, by0, bx1, by1 = box
            cx0, cy0 = max(bx0, x0), max(by0, y0)
            cx1, cy1 = min(bx1, x0 + 16), min(by1, y0 + 16)
            if cx0 < cx1 and cy0 < cy1:
                block[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] = px
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
    #
    # From P3 the cursor sprite is drawn over the top-left 8x8 of the cell it
    # sits on, so that one corner pixel is the sprite's and not the art's.
    # p3_select_and_highlight owns the cursor; this check owns the art, so it
    # reads the body only on that cell.
    box = g.cursor_box()
    bad = []
    for (r, c), kind in sorted(kinds.items()):
        if kind is None:
            continue
        body = BLACK_PIECE_PX if kind in (0, 2) else WHITE_PIECE_PX
        expect = (body, DARK_BG)
        got = (g.px(16 * c + PIECE_ART_PX[0], 16 * r + PIECE_ART_PX[1]),
               g.px(16 * c, 16 * r))
        if box and box[0] <= 16 * c < box[2] and box[1] <= 16 * r < box[3]:
            got, expect = got[:1], expect[:1]
        if got != expect:
            bad.append((r, c, kind, got, expect))
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


# ---------------------------------------------------------------- P3 --------
# main.c's `State` enum, sim.h's NO_SQ, and the move struct (three bytes:
# from, to, capture - all uint8_t, so no padding to model).
ST_TITLE, ST_SELECT, ST_TARGET = 0, 1, 2
NO_SQ = 0xFF
MOVE_BYTES = 3
HL_BG = SHADE[1]                # T_DARK_HL is colour 1, so a lit cell is
                                # LIGHTER than T_DARK's colour 2 -- which is
                                # what makes "is it really lit" a rendered check.


def legal_moves(g):
    """The game's own move list, read out of WRAM."""
    a, n = g.addr["moves"], g.var("n_moves")
    if not n:
        return []       # PyBoy refuses a zero-length slice, and a broken ROM
                        # that offers no moves at all has to read as a FAILED
                        # CHECK, not as a harness error (exit 2 would let the
                        # driver's power gate pass on a broken build)
    raw = bytes(g.p.memory[a:a + MOVE_BYTES * n])
    return [{"from": raw[3 * i], "to": raw[3 * i + 1], "capture": raw[3 * i + 2]}
            for i in range(n)]


def hl_cells(g):
    """Cells lit up in full."""
    return sorted((r, c) for r in range(8) for c in range(8)
                  if g.cell_is(r, c, T_DARK_HL))


def hl_partial(g):
    """Cells with some lit tiles and some not - a highlight painted at the
    wrong granularity, which no whole-cell check would ever see."""
    return sorted((r, c) for r in range(8) for c in range(8)
                  if not g.cell_is(r, c, T_DARK_HL)
                  and any(g.tile(x, y) == T_DARK_HL for x, y in cells(r, c)))


def piece_kinds(g):
    """{(row, col): kind} for every cell holding a whole piece."""
    return {(r, c): cell_kind(g, r, c) for r in range(8) for c in range(8)
            if cell_kind(g, r, c) is not None}


def tap(g, buttons, hold=24, settle=14):
    """Hold a button for a while, then let the screen catch up.

    Both numbers are measured against this ROM, not guessed.  main.c's loop
    advances ONE iteration per ~8.6 emulator frames: read `frame` over 60
    emulator frames and it goes up by 7.  (PyBoy's clocks are unfaithful --
    CLAUDE.md says so and this is what it looks like from here; p1 already hit
    it, which is why it holds START for 20 frames instead of tapping it.)
    So a 1-2 frame tap falls between two of the game's samples and is never
    seen at all; a 24 frame hold is 2-3 samples, which always catches the edge.
    The second number is the same fact seen from the other side: the tilemap
    the probe reads is whatever was blitted LAST, so it trails main.c's statics
    by an iteration -- WRAM is the fresh view, the screen needs settling."""
    g.run(hold, buttons)
    g.run(settle)


def set_cursor(g, sq):
    """Park the cursor on a square, and let the loop redraw around it.

    Writing `cursor` directly is only sound because main.c recomputes
    everything that depends on it (the move list, the sprite OAM, the
    highlights) from the top of the loop each iteration; nothing caches it.
    A test that had to *walk* to every square would need the walk correct
    before it could check anything else, and the walk is one of the things
    under test.
    """
    a = g.addr["cursor"]
    g.p.memory[a:a + 1] = bytes([sq])
    g.run(6)


def step(g, key):
    """One d-pad press, one walk() call - the calibrated hold, plus a redraw.

    Use this and not a longer hold.  main.c walks again every REPEAT_DIV (8)
    game iterations, which is ~69 emulator frames, so a 30 frame hold catches
    one repeat about 40% of the time - and a repeat is a SECOND step along the
    same diagonal.  That used to be harmless only because a repeat could not
    land on bare board; now that it can, a 30 frame hold walks off the square
    under test whenever the divider happens to fall inside the hold.
    """
    g.run(STEP_HOLD, key)
    g.run(16)


def p3_select_and_highlight(g):
    """Cursor, two-phase select, destination highlights, B backs out."""
    print("P3 cursor")
    g.start()
    if not g.need("board", "state", "cursor", "sel", "n_moves", "moves"):
        return

    # --- the sprite ------------------------------------------------------
    movable = sorted({m["from"] for m in legal_moves(g)})
    c0 = g.var("cursor")

    # The cursor has to start somewhere the side to move can actually use.  It
    # used to start on square 0 - a back-rank man with no legal move in any
    # direction - so the first press of every game did nothing; and because
    # square 0 is cell row 0 its sprite sat at OAM y = 0, which on the DMG
    # means "hidden".  Both are asserted here.
    check(c0 in movable,
          "the cursor starts on a square that can move (%d, movable %r)"
          % (c0, movable))
    x, y = g.cursor_xy()
    check((x, y) == (16 * sq_col(c0), 16 * sq_row(c0)),
          "the cursor sprite is on square %d = cell(%d, %d) (%r)"
          % (c0, sq_row(c0), sq_col(c0), (x, y)))
    check(g.cursor_box() is not None,
          "and it is really drawn - a sprite at OAM y=0 would be hidden (%r)"
          % (g.cursor_box(),))
    check(0 <= x <= 127 and 0 <= y <= 127,
          "its OAM x/y are inside 0..127 (%r)" % ((x, y),))
    check(g.p.memory[OAM + 2] == 0, "sprite tile 0 is the cursor (%d)"
          % g.p.memory[OAM + 2])
    check(g.var("sel") == NO_SQ,
          "nothing is picked up yet: sel == NO_SQ (%#x)" % g.var("sel"))
    check(g.var("state") == ST_SELECT,
          "state is ST_SELECT (%d)" % g.var("state"))

    # Black's whole legal set at the start position is SEVEN moves, all of
    # them from the row-2 men on squares 8..11.  Squares 0..7 (rows 0-1) are
    # each blocked by their own rank -- NEIGH says square 0 reaches 4 and 5,
    # but 4 and 5 hold black men, so square 0 has no move at all.  Counting
    # NEIGH hops instead of reading gen_moves() is exactly how a check ends up
    # asserting a move that is not legal.
    check(g.var("n_moves") == 7,
          "black's whole legal set at the start position is 7 moves (%d)"
          % g.var("n_moves"))
    check(sorted({m["from"] for m in legal_moves(g)}) == [8, 9, 10, 11],
          "and every one of them is played by a row-2 man (%r)"
          % (sorted({m["from"] for m in legal_moves(g)}),))

    # --- every movable man has to be reachable ---------------------------
    # This is the check whose absence let an unplayable ROM through.  The walk
    # lands only on a square that is empty or in the move list, and it may
    # only travel along ONE diagonal.  The four movable men sit on row 2, two
    # columns apart (squares 8, 9, 10, 11), so no straight diagonal joins any
    # two of them: while the walk landed ONLY on move-list squares the cursor
    # was trapped on whichever man it first reached, and three of the side's
    # four movable pieces could not be played at all.
    #
    # So walk the graph for real and require all four.  One press is one
    # walk() call (STEP_HOLD), and every square reached is expanded in turn.
    seen, frontier = {c0}, [c0]
    while frontier:
        nxt = []
        for s in frontier:
            for k in "UDLR":
                set_cursor(g, s)
                g.run(STEP_HOLD, k)
                g.run(16)
                t = g.var("cursor")
                if t not in seen:
                    seen.add(t)
                    nxt.append(t)
        frontier = nxt
    unreachable = [m for m in movable if m not in seen]
    check(not unreachable,
          "every man the side to move may play is reachable from the start "
          "square - unreachable %r (reached %r)" % (unreachable, sorted(seen)))

    # --- the walk lands on bare board ------------------------------------
    # Square 8 is cell(2, 1).  DOWN is DIR_SE, and the first square along it is
    # 13, which is empty - so that is where the cursor rests, not on the man on
    # 9 (a different diagonal, and occupied anyway).
    set_cursor(g, 8)
    step(g, "D")
    check(g.var("cursor") == 13,
          "DOWN from the start square steps onto bare board, square 13 (%d)"
          % g.var("cursor"))
    check(g.cursor_xy() == (16 * sq_col(13), 16 * sq_row(13)),
          "and the sprite followed it to cell(3, 2) (%r)" % (g.cursor_xy(),))
    step(g, "U")
    check(g.var("cursor") == 8,
          "and UP takes it back onto the man on square 8 (%d)" % g.var("cursor"))

    # A walk still refuses to REST on a piece it cannot use - it steps over it.
    # Square 4 is a back-rank black man (black holds 0..11 at the start), and
    # being neither empty nor in the move list he is not a landing square, so
    # UP from 8 (DIR_NW) goes right past him and off the board, leaving the
    # cursor on 8.  Landing on "my man anyway" is the bug this phase exists to
    # prevent, and a walk that steps over him is where it shows.
    step(g, "U")
    check(g.var("cursor") == 8,
          "UP from 8 steps over the black man on square 4 - no legal move, so "
          "not a landing square - and, the board ending there, stays on 8 (%d)"
          % g.var("cursor"))
    # The same rule on empty board, two hops deep: 9 -SE-> 14 -SE-> 18 are both
    # empty, and from 18 the next square SE is 23, a WHITE man - occupied, and
    # not in black's move list because black has no capture on, so it is
    # stepped over too (and 27 after it, also white) and the cursor holds on 18.
    set_cursor(g, 9)
    for want in (14, 18):
        step(g, "D")
        check(g.var("cursor") == want,
              "DOWN walks 9 -> 14 -> 18 onto bare board, now square %d (%d)"
              % (want, g.var("cursor")))
    step(g, "D")
    check(g.var("cursor") == 18,
          "and a third DOWN steps over the white men on 23 and 27, neither of "
          "which is a capture target, and stays on 18 (%d)" % g.var("cursor"))

    # --- "selectable" means legal, not mine ------------------------------
    # Force the cursor onto that same unusable man and press A: nothing may
    # happen.  A cursor that picks up "my man" is the bug this phase exists to
    # prevent, and A here is where it shows.
    set_cursor(g, 0)
    before = piece_kinds(g)
    tap(g, "A")
    check(g.var("sel") == NO_SQ and g.var("state") == ST_SELECT,
          "A on square 0 does nothing - that man is not in the legal set "
          "(sel %#x, state %d)" % (g.var("sel"), g.var("state")))
    check(hl_cells(g) == [], "and lights nothing up (%r)" % (hl_cells(g),))
    check(piece_kinds(g) == before, "the pieces on screen did not change")

    # --- select ----------------------------------------------------------
    set_cursor(g, 9)
    tap(g, "A")
    check(g.var("sel") == 9, "A picks up square 9 (%d)" % g.var("sel"))
    check(g.var("state") == ST_TARGET,
          "state is ST_TARGET (%d)" % g.var("state"))
    check(g.var("n_moves") == 2,
          "the list is now that one piece's 2 moves (%d)" % g.var("n_moves"))
    hl = hl_cells(g)
    check(hl == [(3, 2), (3, 4)],
          "exactly the two destinations light up - sq 13 and sq 14, both in "
          "row 3 (%r)" % (hl,))
    check(hl_partial(g) == [], "no cell is half lit (%r)" % (hl_partial(g),))
    dests = sorted((sq_row(m["to"]), sq_col(m["to"])) for m in legal_moves(g))
    check(dests == hl, "the lit cells are exactly the move list's m.to - "
                       "screen and engine agree (%r vs %r)" % (dests, hl))
    off = [rc for rc in hl if not g.cell_flat(rc[0], rc[1], HL_BG)]
    check(not off, "a lit cell renders flat in the highlight shade, and is not "
                   "just the id (%r)" % (off,))

    # --- B backs out -----------------------------------------------------
    tap(g, "B")
    check(g.var("sel") == NO_SQ and g.var("state") == ST_SELECT,
          "B drops the piece (%#x, state %d)"
          % (g.var("sel"), g.var("state")))
    check(hl_cells(g) == [], "0 highlights after B (%r)" % (hl_cells(g),))
    check(g.var("cursor") == 9 and g.cursor_xy() == (16 * sq_col(9), 16 * sq_row(9)),
          "the cursor never left the piece it was on - square 9 (%r)"
          % (g.cursor_xy(),))

    # --- mandatory capture: a piece to move is not a piece to pick --------
    print("P3 selectable means legal, not mine")
    # Black's man on 9 can jump; the man on 0 has a perfectly good empty
    # diagonal ahead of it and still must not be offered, or pickable.
    blk, wht = (1 << 9) | (1 << 0), (1 << 13) | (1 << 14)
    a = g.addr["board"]
    g.p.memory[a:a + 12] = board_bytes(blk, wht, 0)
    g.run(20)                     # a couple of game iterations, so the screen
    ms = legal_moves(g)           # has redrawn from the new board too
    check(len(ms) == 2 and all(m["capture"] != NO_SQ and m["from"] == 9
                               for m in ms),
          "with a jump on, the legal set is 9's two jumps and nothing else "
          "(%r)" % (ms,))
    check(piece_kinds(g) == {(0, 1): 0, (2, 3): 0, (3, 2): 1, (3, 4): 1},
          "the screen followed the board write - black men on sq 0 and 9, "
          "white on sq 13 and 14 (%r)" % (sorted(piece_kinds(g).items()),))
    # The walk may rest on bare board, but never on a piece it cannot use.  UP
    # from 9 is DIR_NW and lands on 5, which is empty; UP again is 0, the black
    # man who must not be pickable, so he is stepped over and - the board
    # ending there - the cursor holds on 5.  A on 5, an empty square, then
    # picks up nothing, and only then is the cursor put back on 9.
    g.run(150, "U")
    check(g.var("cursor") == 5,
          "UP from 9 lands on the empty square 5 (%d)" % g.var("cursor"))
    g.run(150, "U")
    check(g.var("cursor") == 5,
          "UP again steps over the man on 0 - not selectable, so not a landing "
          "square - rather than resting on 'my man' (%d)" % g.var("cursor"))
    tap(g, "A")
    check(g.var("sel") == NO_SQ and g.var("state") == ST_SELECT,
          "and A on the bare square picks nothing up (%#x, %d)"
          % (g.var("sel"), g.var("state")))
    set_cursor(g, 9)
    tap(g, "A")
    hl = hl_cells(g)
    check(g.var("sel") == 9 and g.var("state") == ST_TARGET,
          "A on 9 picks it up (%d, state %d)"
          % (g.var("sel"), g.var("state")))
    check(hl == [(4, 1), (4, 5)],
          "the two landing squares light up - sq 16 and sq 18, behind the "
          "jumped men (%r)" % (hl,))
    check(sorted((sq_row(m["to"]), sq_col(m["to"])) for m in legal_moves(g))
          == hl, "... and they are the move list's m.to again")
    check(g.visible_sprites() == [0],
          "there is exactly one sprite on the screen, the cursor (%r)"
          % (g.visible_sprites(),))


# ---------------------------------------------------------------- P4 --------
# sim.h's SIDE_BLACK / SIDE_WHITE.  `side` is the symbol this phase adds to
# main.c's state, and its absence from the map is what makes these two checks
# fail on P3's ROM (Game.need records that as an assertion failure, not a
# harness error, so the power gate's exit code is 1 and not 2).
SIDE_BLACK, SIDE_WHITE = 0, 1

HUD_X0, HUD_X1 = 16, 20          # PLAN.md: the HUD strip is tiles x 16..19


def hud_word(g):
    """The HUD strip's word, or "" when the strip carries no letters.

    main.c stacks the letters down a tile column rather than writing them
    across: the strip is 4 tiles wide and "BLACK" is 5, so sideways it would
    run off the 20-tile screen.  Scanning every column of the strip keeps this
    check from hardcoding which one main.c picked."""
    for x in range(HUD_X0, HUD_X1):
        letters = []
        for y in range(BOARD_TH):
            t = g.tile(x, y)
            if t == T_BLANK:
                continue
            if not (T_LETTER0 <= t < T_LETTER0 + 26):
                break
            letters.append(chr(ord("A") + t - T_LETTER0))
        else:
            if letters:
                return "".join(letters)
    return ""


def board_masks(g):
    """main.c's `board`, as the three little-endian uint32s it really is."""
    raw = bytes(g.p.memory[g.addr["board"]:g.addr["board"] + 12])
    return tuple(int.from_bytes(raw[4 * i:4 * i + 4], "little") for i in range(3))


def p4_move_applied(g):
    """A on a highlighted destination moves the piece and hands the turn over."""
    print("P4 A on a destination plays the move")
    g.start()
    if not g.need("board", "side", "cursor", "sel", "state", "n_moves", "moves"):
        return

    check(g.var("side") == SIDE_BLACK,
          "black moves first (side %d)" % g.var("side"))
    check(hud_word(g) == "BLACK",
          "the HUD strip names the side to move: BLACK (%r)" % hud_word(g))

    # Square 0 holds a black man with no legal move at the start position -
    # p3_select_and_highlight exists to make exactly that point - so the cursor
    # is placed rather than walked there: this check is about playing a move,
    # and p3 is the one that owns the walk.
    set_cursor(g, 9)
    check(g.var("cursor") == 9, "the cursor is on square 9 (%d)" % g.var("cursor"))
    tap(g, "A")
    check(g.var("sel") == 9 and g.var("state") == ST_TARGET,
          "A picks square 9 up (%d, state %d)" % (g.var("sel"), g.var("state")))

    # 9's legal moves are to 13 and 14 (NEIGH[9] = {5, 6, 13, 14} for NW NE SW
    # SE), so LEFT - main.c's DIR_SW - lands on 13 in one hop.
    step(g, "L")
    check(g.var("cursor") == 13,
          "LEFT walks the cursor from 9 onto square 13 (%d)" % g.var("cursor"))
    check(g.cursor_xy() == (16 * sq_col(13), 16 * sq_row(13)),
          "and the sprite followed it to cell(3, 2) = OAM (32, 48) (%r)"
          % (g.cursor_xy(),))

    tap(g, "A")

    # --- the engine, straight out of WRAM --------------------------------
    blk, wht, kng = board_masks(g)
    check(not (blk & (1 << 9)), "black bit 9 - the square it left - is clear "
                                "(black 0x%08X)" % blk)
    check(bool(blk & (1 << 13)), "black bit 13 - the square it went to - is set "
                                 "(black 0x%08X)" % blk)
    check(bin(blk).count("1") == 12, "black still has 12 pieces (%d)"
          % bin(blk).count("1"))
    check(wht == WHITE_START, "white's mask is untouched (0x%08X)" % wht)
    check(kng == 0, "a move into row 3 does not promote (kings 0x%08X)" % kng)
    check(g.var("side") == SIDE_WHITE,
          "the turn passed to white (side %d)" % g.var("side"))
    check(g.var("sel") == NO_SQ and g.var("state") == ST_SELECT,
          "the piece was let go and the state is back to ST_SELECT (%#x, %d)"
          % (g.var("sel"), g.var("state")))

    # --- and the screen followed -----------------------------------------
    check(cell_kind(g, 3, 2) == 0,
          "the tilemap shows a black man at cell(3, 2) (%r)" % (cell_kind(g, 3, 2),))
    check(g.cell_is(2, 3, T_DARK),
          "cell(2, 3), the square it came from, is bare dark board again (%r)"
          % ([g.tile(x, y) for x, y in cells(2, 3)],))
    check(hl_cells(g) == [], "and nothing is highlighted with nothing picked up "
                             "(%r)" % (hl_cells(g),))
    check(hud_word(g) == "WHITE",
          "the HUD strip followed the turn: WHITE (%r)" % hud_word(g))
    # main.c blits all four columns of the strip, not just the one the word
    # sits in.  Half a strip written would leave the rest as whatever VRAM held
    # at boot, so this is the assertion that the whole thing is defined.
    stray = [(x, y) for x in range(HUD_X0, HUD_X1) for y in range(BOARD_TH)
             if g.tile(x, y) != T_BLANK
             and not (T_LETTER0 <= g.tile(x, y) < T_LETTER0 + 26)]
    check(not stray, "the HUD strip holds nothing but blanks and letters (%r)"
          % (stray[:4],))
    flat = [(x, y) for x in range(HUD_X0, HUD_X1) for y in range(BOARD_TH)
            if g.tile(x, y) == T_BLANK and g.px(8 * x, 8 * y) != LIGHT_BG]
    check(not flat, "and its blank tiles render as the light background, so the "
                    "whole strip was written and not left as boot VRAM (%r)"
          % (flat[:4],))
    # The drawn pieces are the engine's board, square for square - the same
    # seam p2 asserts, but now across a move rather than across a boot.  Both
    # directions are checked, so a piece drawn on a square the board says is
    # empty is caught as surely as one missing from a square it says is not.
    drawn = piece_kinds(g)
    engine = {}
    for sq in range(32):
        m = 1 << sq
        v = 0 if blk & m else 1 if wht & m else None
        if v is not None:
            engine[(sq_row(sq), sq_col(sq))] = v
    wrong = sorted(k for k in set(drawn) | set(engine)
                   if drawn.get(k) != engine.get(k))
    check(not wrong, "what is drawn is the board, square for square (%d pieces "
                     "drawn, %d on the board, %r wrong)"
          % (len(drawn), len(engine), wrong[:4]))


def p4_mandatory_capture(g):
    """A side with a capture available is offered only captures - and playing
    one really takes the piece."""
    print("P4 mandatory capture")
    g.start()
    if not g.need("board", "side", "cursor", "sel", "state", "n_moves", "moves"):
        return
    a = g.addr["board"]

    # Park the cursor on square 9 first, from the start position, so the board
    # write below does not also have to move it.
    set_cursor(g, 9)
    check(g.var("cursor") == 9, "the cursor is parked on square 9 (%d)"
          % g.var("cursor"))

    # A black man on 9 with a jump over each of 13 and 14 - and a black man on
    # 0 that has two perfectly good QUIET moves (0 -SW-> 4, 0 -SE-> 5) and no
    # capture at all.  That second man is the point: with a bare single-man
    # board "every legal move is a capture" would hold even with no
    # mandatory-capture rule anywhere, because there would be no quiet move to
    # offer.  The quiet moves existing and still not being offered is the rule.
    blk, wht = (1 << 9) | (1 << 0), (1 << 13) | (1 << 14)
    g.p.memory[a:a + 12] = board_bytes(blk, wht, 0)
    g.p.memory[g.addr["side"]:g.addr["side"] + 1] = bytes([SIDE_BLACK])
    g.run(20)                     # a couple of game iterations, so the screen
                                  # has redrawn from the written board too
    ms = legal_moves(g)
    check(len(ms) == 2, "the legal set is 9's two jumps (%r)" % (ms,))
    check(all(m["capture"] != NO_SQ for m in ms),
          "every move in it is a capture - the man on 0's two quiet moves are "
          "not offered at all (%r)" % (ms,))
    check(all(m["from"] == 9 for m in ms),
          "and every one of them is played by the man on 9 (%r)"
          % (sorted({m["from"] for m in ms}),))
    check(sorted(m["to"] for m in ms) == [16, 18],
          "landing on 16 (over 13) and 18 (over 14) (%r)"
          % (sorted(m["to"] for m in ms),))
    check(piece_kinds(g) == {(0, 1): 0, (2, 3): 0, (3, 2): 1, (3, 4): 1},
          "the screen followed the board write - black men on sq 0 and 9, "
          "white on sq 13 and 14 (%r)" % (sorted(piece_kinds(g).items()),))
    check(g.var("n_moves") == 2, "and main.c's own move list agrees (%d)"
          % g.var("n_moves"))

    tap(g, "A")
    check(g.var("sel") == 9 and g.var("state") == ST_TARGET,
          "A picks the man on 9 up (%d, state %d)"
          % (g.var("sel"), g.var("state")))
    hl = hl_cells(g)
    check(hl == [(4, 1), (4, 5)],
          "the two landing squares light up - sq 16 and sq 18, behind the men "
          "they take (%r)" % (hl,))

    step(g, "D")                # DOWN is main.c's DIR_SE: 9 -SE-> 14 -SE-> 18
    check(g.var("cursor") == 18,
          "DOWN steps over the man on 14 and lands the cursor on 18 (%d)"
          % g.var("cursor"))

    tap(g, "A")
    blk, wht, kng = board_masks(g)
    check(not (blk & (1 << 9)) and bool(blk & (1 << 18)),
          "the man landed on 18 (black 0x%08X)" % blk)
    check(bool(blk & (1 << 0)), "and the man on 0, which never moved, is still "
                                "there (black 0x%08X)" % blk)
    check(not (wht & (1 << 14)), "the jumped man on 14 is gone (white 0x%08X)"
          % wht)
    check(bool(wht & (1 << 13)), "the man on 13, which this jump did not take, "
                                 "is still there (white 0x%08X)" % wht)
    check(g.var("side") == SIDE_WHITE,
          "the turn passed to white (side %d)" % g.var("side"))
    check(cell_kind(g, 4, 5) == 0, "the tilemap shows the black man on cell(4, 5) "
                                   "(%r)" % (cell_kind(g, 4, 5),))
    check(g.cell_is(3, 4, T_DARK),
          "and cell(3, 4), where the taken man stood, is bare board (%r)"
          % ([g.tile(x, y) for x, y in cells(3, 4)],))


# ---------------------------------------------------------------- P5 --------
def p5_chain_continues(g):
    """A capture that can continue does: the SAME piece goes again, the turn
    does not pass, and the screen offers only that piece's next hop."""
    print("P5 a capture chain continues with the same piece")
    g.start()
    if not g.need("board", "side", "cursor", "sel", "state", "n_moves", "moves",
                  "chain_sq"):
        return

    # 13 -SE-> 17 -SE-> 22, so 17 is the man taken; then 22 -SW-> 25 -SW-> 29,
    # taking 25.  Both hops are captures and the second lands in row 7.  NEIGH
    # says so; the board goes straight into WRAM and main.c redraws from it,
    # the same trick the P4 checks use.
    blk, wht = 1 << 13, (1 << 17) | (1 << 25)
    a = g.addr["board"]
    g.p.memory[a:a + 12] = board_bytes(blk, wht, 0)
    g.p.memory[g.addr["side"]:g.addr["side"] + 1] = bytes([SIDE_BLACK])
    g.p.memory[g.addr["cursor"]:g.addr["cursor"] + 1] = bytes([13])
    g.run(20)                     # a couple of game iterations, so the screen
                                  # has redrawn from the written board too

    check(g.var("cursor") == 13, "the cursor is on square 13 (%d)"
          % g.var("cursor"))
    check(piece_kinds(g) == {(3, 2): 0, (4, 3): 1, (6, 3): 1},
          "the screen followed the board write - a black man on cell(3, 2) "
          "(sq 13), white men on cell(4, 3) and cell(6, 3) (%r)"
          % (sorted(piece_kinds(g).items()),))
    ms = legal_moves(g)
    check(len(ms) == 1 and ms[0] == {"from": 13, "to": 22, "capture": 17},
          "black's only legal move is the jump 13 -> 22 over 17 (%r)" % (ms,))

    # --- hop one ---------------------------------------------------------
    tap(g, "A")
    check(g.var("sel") == 13 and g.var("state") == ST_TARGET,
          "A picks the man on 13 up (%d, state %d)"
          % (g.var("sel"), g.var("state")))
    check(hl_cells(g) == [(5, 4)], "its landing square, 22, lights up (%r)"
          % (hl_cells(g),))

    step(g, "D")                # DOWN is DIR_SE: 13 -SE-> 17 -SE-> 22
    check(g.var("cursor") == 22,
          "DOWN steps over the man on 17 and lands the cursor on 22 (%d)"
          % g.var("cursor"))
    tap(g, "A")

    blk, wht, kng = board_masks(g)
    check(blk == 1 << 22, "the man is now on 22 (black 0x%08X)" % blk)
    check(not (wht & (1 << 17)),
          "the man it jumped on 17 is gone (white 0x%08X)" % wht)
    check(bool(wht & (1 << 25)),
          "and the man on 25, which this hop did not take, is STILL there "
          "(white 0x%08X)" % wht)

    # THE rule.  must_continue() is true here - 22 has another jump - so the
    # turn must not pass: same side to move, same piece still up, and only that
    # piece's next hop on offer.  A turn loop that switches sides after every
    # hop fails every one of these.
    check(g.var("side") == SIDE_BLACK,
          "the turn did NOT pass - black is still to move (side %d)"
          % g.var("side"))
    check(g.var("chain_sq") == 22, "main.c knows it is mid-chain, on square 22 "
                                   "(%#x)" % g.var("chain_sq"))
    check(g.var("sel") == 22 and g.var("state") == ST_TARGET,
          "the same piece is still picked up (%#x, state %d)"
          % (g.var("sel"), g.var("state")))
    ms = legal_moves(g)
    check(len(ms) == 1 and ms[0] == {"from": 22, "to": 29, "capture": 25},
          "the legal set is that piece's next jump and nothing else (%r)"
          % (ms,))
    check({m["from"] for m in ms} == {22},
          "and every move in it is played by the piece on 22 - not another "
          "piece's, and not a quiet move (%r)"
          % (sorted({m["from"] for m in ms}),))
    check(hl_cells(g) == [(7, 2)],
          "exactly one square is lit - its landing square, 29 (%r)"
          % (hl_cells(g),))
    check(g.var("cursor") == 22,
          "and the cursor is on the piece, ready for the next hop (%d)"
          % g.var("cursor"))
    check(hud_word(g) == "BLACK", "the HUD still says BLACK (%r)" % hud_word(g))
    check(cell_kind(g, 5, 4) == 0,
          "the screen drew the capture: a black MAN on cell(5, 4) (%r)"
          % (cell_kind(g, 5, 4),))
    check(g.cell_is(4, 3, T_DARK),
          "and the taken man's cell is bare board again (%r)"
          % ([g.tile(x, y) for x, y in cells(4, 3)],))

    # --- hop two ---------------------------------------------------------
    step(g, "L")                # LEFT is DIR_SW: 22 -SW-> 25 -SW-> 29
    check(g.var("cursor") == 29,
          "LEFT steps over the man on 25 and lands the cursor on 29 (%d)"
          % g.var("cursor"))
    tap(g, "A")

    blk, wht, kng = board_masks(g)
    check(bool(blk & (1 << 29)), "the piece is on 29 (black 0x%08X)" % blk)
    check(not (wht & (1 << 25)), "the man on 25 is gone (white 0x%08X)" % wht)
    check(g.var("side") == SIDE_WHITE,
          "and with nothing left to capture the turn passes: white to move "
          "(side %d)" % g.var("side"))
    check(g.var("chain_sq") == NO_SQ, "the chain is over (%#x)"
          % g.var("chain_sq"))
    check(hl_cells(g) == [], "nothing is highlighted (%r)" % (hl_cells(g),))
    check(hud_word(g) == "WHITE", "the HUD followed the turn: WHITE (%r)"
          % hud_word(g))


def p5_promotion_stops_chain(g):
    """A hop that promotes ends the turn on the spot - even though the new king
    could geometrically keep capturing."""
    print("P5 promotion stops the chain")
    g.start()
    if not g.need("board", "side", "cursor", "sel", "state", "n_moves", "moves",
                  "chain_sq"):
        return

    # 21 -SE-> 25 -SE-> 30.  30 is in row 7, so the man CROWNS on landing.
    # White also holds 26, NE of the landing square: NEIGH[30][NE] == 26, and
    # NEIGH[26][NE] == 23 is empty, so a bare must_continue(&board, 30) on the
    # post-move board answers yes - that king really could jump the man on 26.
    # That answer is what must not be acted on: the promotion ends the turn.
    blk, wht = 1 << 21, (1 << 25) | (1 << 26)
    a = g.addr["board"]
    g.p.memory[a:a + 12] = board_bytes(blk, wht, 0)
    g.p.memory[g.addr["side"]:g.addr["side"] + 1] = bytes([SIDE_BLACK])
    g.p.memory[g.addr["cursor"]:g.addr["cursor"] + 1] = bytes([21])
    g.run(20)

    check(piece_kinds(g) == {(5, 2): 0, (6, 3): 1, (6, 5): 1},
          "the screen followed the board write - a black man on cell(5, 2) "
          "(sq 21), white men on cell(6, 3) and cell(6, 5) (%r)"
          % (sorted(piece_kinds(g).items()),))
    ms = legal_moves(g)
    check(len(ms) == 1 and ms[0] == {"from": 21, "to": 30, "capture": 25},
          "black's only legal move is the jump 21 -> 30 over 25 (%r)" % (ms,))

    tap(g, "A")
    check(g.var("sel") == 21 and g.var("state") == ST_TARGET,
          "A picks the man on 21 up (%d, state %d)"
          % (g.var("sel"), g.var("state")))
    check(hl_cells(g) == [(7, 4)], "its landing square, 30, lights up (%r)"
          % (hl_cells(g),))

    step(g, "D")                # DOWN is DIR_SE: 21 -SE-> 25 -SE-> 30
    check(g.var("cursor") == 30,
          "DOWN steps over the man on 25 and lands the cursor on 30 (%d)"
          % g.var("cursor"))
    tap(g, "A")

    blk, wht, kng = board_masks(g)
    check(bool(blk & (1 << 30)), "the piece is on 30 (black 0x%08X)" % blk)
    check(bool(kng & (1 << 30)),
          "and it crowned on landing - kings bit 30 (kings 0x%08X)" % kng)
    check(not (wht & (1 << 25)), "the man it jumped on 25 is gone (white "
                                 "0x%08X)" % wht)

    # The assertion this phase exists for.  26 is the man a bare
    # must_continue() would want to take next; it must survive.
    check(bool(wht & (1 << 26)),
          "the man on 26 is STILL THERE - the new king does not capture again "
          "in the turn it was crowned (white 0x%08X)" % wht)
    check(g.var("side") == SIDE_WHITE,
          "the turn passed on the promoting hop (side %d)" % g.var("side"))
    check(g.var("chain_sq") == NO_SQ,
          "no chain is open (%#x)" % g.var("chain_sq"))
    check(g.var("sel") == NO_SQ and g.var("state") == ST_SELECT,
          "the piece was let go (%#x, state %d)"
          % (g.var("sel"), g.var("state")))
    check(hl_cells(g) == [], "0 highlights (%r)" % (hl_cells(g),))
    check(hud_word(g) == "WHITE", "the HUD followed the turn: WHITE (%r)"
          % hud_word(g))
    check(cell_kind(g, 7, 4) == 2,
          "and the screen draws a black KING on cell(7, 4), not a man (%r)"
          % (cell_kind(g, 7, 4),))
    check(g.cell_is(6, 3, T_DARK),
          "the taken man's cell is bare board again (%r)"
          % ([g.tile(x, y) for x, y in cells(6, 3)],))


# ---------------------------------------------------------------- P6 --------
# main.c's State enum grows a fourth value.  ST_OVER is a local name here (the
# enum is a #define-land detail, the map holds no symbol for it), so this
# check has no `need()` to ask for: on P5's ROM every symbol it touches exists
# and it fails on BEHAVIOUR instead - the position below is still ST_SELECT,
# carries no result banner, and START does nothing at all.
ST_OVER = 3


def p6_setup_stuck(g):
    """Black man on square 0, white man on square 1, black to move.

    Square 1 is row 0 (SQ_ROW(1) == 0) and NEIGH[1][NW] = NEIGH[1][NE] = NO_SQ,
    so a WHITE man parked there is on its own far edge and can never move
    again - not "cannot move right now", cannot ever.  Black's man on square 0
    has two perfectly good quiet moves (NEIGH[0][SW] = 4, NEIGH[0][SE] = 5).
    So black has a move and white, once it is white's turn, has none at all."""
    a = g.addr["board"]
    g.p.memory[a:a + 12] = board_bytes(1 << 0, 1 << 1, 0)
    g.p.memory[g.addr["side"]:g.addr["side"] + 1] = bytes([SIDE_BLACK])
    g.p.memory[g.addr["cursor"]:g.addr["cursor"] + 1] = bytes([0])
    g.run(20)                     # a couple of game iterations, so the screen
                                  # has redrawn from the written board too


def p6_play_it_out(g):
    """Play 0 -> 4: A to pick the man on 0 up, LEFT (main.c's DIR_SW) to walk
    the cursor onto its landing square, A to move it."""
    tap(g, "A")
    step(g, "L")
    tap(g, "A")


def p6_win_and_restart(g):
    """A turn that leaves the other side with nothing to play ends the game,
    and START (or B) starts a new one."""
    print("P6 win and restart")
    g.start()
    if not g.need("board", "side", "state", "cursor", "sel", "n_moves", "moves"):
        return

    p6_setup_stuck(g)
    ms = legal_moves(g)
    check(len(ms) == 2 and all(m["from"] == 0 and m["capture"] == NO_SQ
                               for m in ms),
          "black's whole legal set is the man on 0's two quiet moves (%r)"
          % (ms,))
    check(g.var("state") == ST_SELECT,
          "the game is running before the move (state %d)" % g.var("state"))
    check(g.find_text("WINS") == [],
          "and no banner is on the screen yet (%r)" % (g.find_text("WINS"),))

    p6_play_it_out(g)

    # --- the move landed, and the turn it handed over is a dead end ---------
    blk, wht, kng = board_masks(g)
    check(blk == 1 << 4 and wht == 1 << 1 and kng == 0,
          "the man moved 0 -> 4 and nothing was taken or crowned (black "
          "0x%08X, white 0x%08X, kings 0x%08X)" % (blk, wht, kng))
    check(g.var("side") == SIDE_WHITE,
          "the turn passed to white (side %d)" % g.var("side"))
    check(g.var("n_moves") == 0,
          "and white has NO move at all - the premise of the whole check (%d)"
          % g.var("n_moves"))

    # --- the assertion this phase exists for ------------------------------
    # side_to_move_has_moves() is asked at the turn boundary, so the side that
    # has just been handed the turn and cannot use it has lost.  A turn loop
    # that never asks sits here in ST_SELECT and the game cannot be finished.
    check(g.var("state") == ST_OVER,
          "the game is over: state is ST_OVER (%d)" % g.var("state"))
    # The brief for this phase words the expected banner as "WHITE WINS", but
    # that is the side left with nothing to play, and the rule it states in the
    # same breath - winner = side ^ 1, side being the side to move - makes the
    # winner BLACK.  The stuck man has to be white either way (a man on row 0
    # is immobile only in white's direction of travel), so the two halves of
    # that sentence cannot both hold and the banner below is the one the rule
    # produces.  p6 asserts the rule.
    banner = g.find_text("BLACK WINS")
    check(bool(banner), "the status row spells BLACK WINS (%r)" % (banner,))
    check(g.find_text("WHITE WINS") == [],
          "and not WHITE WINS - the banner follows the winner, it is not one "
          "frozen string (%r)" % (g.find_text("WHITE WINS"),))
    # The HUD keeps its one meaning - the side to move - which in ST_OVER is
    # the side that has just been handed the turn and cannot use it.  It reads
    # opposite to the banner on purpose: "WHITE to move" and "BLACK WINS" are
    # the two halves of why the game stopped.
    check(hud_word(g) == "WHITE",
          "the HUD still names the side to move, white - the side that cannot "
          "(%r)" % hud_word(g))
    check(g.var("sel") == NO_SQ and hl_cells(g) == [],
          "nothing is picked up or highlighted (%#x)" % g.var("sel"))
    check(g.cursor_box() is None,
          "and the cursor sprite is parked off-screen (%r)" % (g.cursor_box(),))
    check(piece_kinds(g) == {(1, 0): 0, (0, 3): 1},
          "the screen still shows the final position - the black man on "
          "cell(1, 0) (sq 4), the stuck white man on cell(0, 3) (sq 1) (%r)"
          % (sorted(piece_kinds(g).items()),))

    # The board is frozen: the d-pad and A do nothing at all now.
    g.run(150, "D")
    g.run(14)
    tap(g, "A")
    check(board_masks(g) == (1 << 4, 1 << 1, 0) and g.var("state") == ST_OVER,
          "d-pad and A do nothing on a finished game (0x%08X, 0x%08X, 0x%08X)"
          % board_masks(g))

    # --- START starts a new game ------------------------------------------
    tap(g, "S")
    check(g.var("side") == SIDE_BLACK,
          "START restarts: black is to move again (side %d)" % g.var("side"))
    check(board_masks(g) == (BLACK_START, WHITE_START, 0),
          "and the board is the start position again (0x%08X, 0x%08X, 0x%08X)"
          % board_masks(g))
    check(g.var("state") == ST_SELECT,
          "the state machine is back in play (state %d)" % g.var("state"))
    check(g.var("cursor") == 8,
          "the cursor is back on the start square, 8 - black's first man with "
          "something to play (%d)" % g.var("cursor"))
    check(len(piece_kinds(g)) == 24,
          "all 24 men are drawn again (%d)" % len(piece_kinds(g)))
    check(g.find_text("BLACK WINS") == [],
          "and the banner is off the screen (%r)" % (g.find_text("BLACK WINS"),))
    check(hud_word(g) == "BLACK",
          "the HUD names the new turn again: BLACK (%r)" % hud_word(g))

    # --- and so does B ----------------------------------------------------
    # A second game played out the same way, so "B restarts" is asserted on a
    # real ST_OVER and not on the state the restart above left behind.
    p6_setup_stuck(g)
    p6_play_it_out(g)
    check(g.var("state") == ST_OVER,
          "played out to a second ST_OVER (state %d)" % g.var("state"))
    tap(g, "B")
    check(board_masks(g) == (BLACK_START, WHITE_START, 0)
          and g.var("side") == SIDE_BLACK and g.var("state") == ST_SELECT,
          "B restarts exactly as START does (0x%08X, 0x%08X, 0x%08X, side %d, "
          "state %d)" % (board_masks(g) + (g.var("side"), g.var("state"))))


CHECKS = [("p1_boot_title_board", p1_boot_title_board),
          ("p2_start_position", p2_start_position),
          ("p3_select_and_highlight", p3_select_and_highlight),
          ("p4_move_applied", p4_move_applied),
          ("p4_mandatory_capture", p4_mandatory_capture),
          ("p5_chain_continues", p5_chain_continues),
          ("p5_promotion_stops_chain", p5_promotion_stops_chain),
          ("p6_win_and_restart", p6_win_and_restart)]


def main(rom, mapfile):
    # `fails` counts ASSERTIONS; a check group holds many. Reporting one against
    # the other printed nonsense like "FAILED: 10 of 8 checks" in the driver's
    # gate logs, so track the two separately.
    groups = 0
    for name, fn in CHECKS:
        g = Game(rom, mapfile)
        before = len(fails)
        try:
            fn(g)
        finally:
            g.p.stop()
        if len(fails) > before:
            groups += 1

    if fails:
        print("FAILED: %d assertions in %d of %d checks"
              % (len(fails), groups, len(CHECKS)))
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
