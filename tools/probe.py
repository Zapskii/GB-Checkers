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
import os
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
        # sound_emulated=True is REQUIRED by p12_sfx, and costs nothing else
        # (P11's frame counts are unmoved by it).  With sound off, PyBoy does
        # not implement the APU at all: every NRx register reads 0x00 forever
        # and writes to them are swallowed, so a register-based sound check on
        # that build fails on every ROM - the pre-phase one AND the phase's own
        # - which is a check that reads as "has power" and is really noise.
        self.p = PyBoy(rom, window="null", sound_emulated=True)
        self.addr = parse_map(mapfile)
        # p13 reads the ROM file and the map as bytes rather than the running
        # game, and PyBoy does not hand its own rom path back out.
        self.rom = rom
        self.mapfile = mapfile

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
        """The cursor sprite's 8x8 px box ON SCREEN, or None when it is parked.

        OAM coordinates are not screen coordinates: the DMG draws a sprite
        with its top-left at (OAM_x - 8, OAM_y - 16).  Measured off an
        otherwise-empty board here: the ring at OAM (48, 32) renders
        x 40..47, y 16..23 - one square up-left of where this box used to
        claim, which is how main.c's missing +8/+16 shipped a whole phase
        with the cursor on the wrong square.  OAM y = 0 is the DMG's "hide
        this sprite" position, where a parked ring sits; x = 0 or x >= 169,
        y >= 160 slide it off the screen."""
        y, x = self.p.memory[OAM], self.p.memory[OAM + 1]
        if y == 0 or y >= 160 or x == 0 or x >= 169:
            return None
        return (x - 8, y - 16, x, y - 8)

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
        START is held 20 frames rather than tapped.

        P14 made 1 PLAYER the boot default, which turned this single START into
        the whole 2-player blast radius: eleven of the checks below come through
        here, and five of them (p4_move_applied, p4_mandatory_capture,
        p5_chain_continues, p5_promotion_stops_chain, p12_sfx) hand the turn to
        white and assert it STAYS white.  Under the new default the machine
        takes that turn instead - and they would mostly still pass, because
        play() flips `side` to white before ai_turn() blocks and tap() only
        advances 38 frames of a 177-384 frame think, so a read taken right after
        a tap sees white mid-search.  Forcing 2P here is what makes those five
        about the rules rather than about the timing.

        Forced AFTER the boot frames and BEFORE the START hold, which is the
        only moment it is read: mode_1p is consulted by new_game()'s loop, not
        by the title.  Guarded by the map because a pre-P10 ROM has no such
        symbol, and an unguarded write would turn those ROMs' assertion failure
        (exit 1) into a KeyError (exit 2)."""
        self.run(boot)
        if "mode_1p" in self.addr:
            set_byte(self, "mode_1p", MODE_2P)
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


def ring_oam(sq):
    """OAM (x, y) of an 8x8 ring centred on square sq's cell.

    The cell's top-left corner is (16*col, 16*row); OAM is not screen
    coordinates - the DMG draws a sprite at (x-8, y-16) of the position
    written (see Game.cursor_box) - and +2/+2 puts the 8x8 in the middle of
    the 2x2-tile cell, so OAM = corner + (12, 20)."""
    return (16 * sq_col(sq) + 12, 16 * sq_row(sq) + 20)


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
    # From P3 the cursor ring is centred over the cell it sits on, with a
    # transparent interior, so the disc body under it is still the art's --
    # cursor_box() hands every check the sprite's real on-screen box either
    # way, and the masking below runs on that.
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
    # There are two by design now and both are named: the live board, and
    # main.c's snapshot of what the last build was made from (see
    # screen_stale()).  Anything ELSE holding these twelve bytes means main.c is
    # drawing from a copy probe.py never looked at, which is what this is for.
    if g.need("drawn_board"):
        check(sorted(hits) == sorted({addr, g.addr["drawn_board"]}),
              "WRAM holds the start position exactly twice: the board at 0x%04X "
              "and the drawn snapshot at 0x%04X (%r)"
              % (addr, g.addr["drawn_board"], [hex(h) for h in hits]))


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
    advances one iteration per ~3 emulator frames in play (~1 on the title
    screen), so `frame` goes up about 20 times a second.  (Counting it as a
    byte over a window wider than 256 iterations wraps it: that is how the rate
    here was first misread.)  A 1-2 frame tap can fall between two of the game's
    samples; 24 frames is ~8 samples, which always catches the edge, and a real
    human tap (~100 ms) is ~2 samples, so it lands too.
    Only edge-triggered buttons come through here.  A d-pad press held this long
    is long enough to catch a repeat, so the d-pad goes through step().
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
    """One d-pad press that the game certainly sampled, and sampled once.

    A fixed-length hold is a gamble on where the loop's frame counter sits, and
    both ways of losing it are real.  Too short and the press falls entirely
    between two of the game's samples and nothing happens at all.  Too long and
    it catches a repeat, which is a SECOND walk() along the same diagonal.  This
    file used to hold a calibrated 12 frames, measured when the loop ran at 2
    frames an iteration; the loop runs at 3 now, and at 3 that hold steps twice
    and every walk assertion below lands a square past its target.  (Before
    REPEAT_DIV was counted from the press it was worse than "too long": see
    p8_one_step_per_tap.)

    So: press for ONE frame and watch `frame`.  main.c reads the joypad and
    increments `frame` in the same iteration, with nothing in between, so the
    counter having moved during the pressed frame means that read saw the key
    down.  One frame is shorter than one turn of the loop, so it cannot be
    sampled twice - and with the key up the frame before, that one sample takes
    the `pressed` branch rather than the repeat branch, which is what makes this
    exactly one walk() call.
    One frame is shorter than a *person's* press, though, so nothing here covers
    what a tap does; p8 is the check for that.
    """
    for _ in range(60):
        before = g.var("frame")
        g.run(1, key)
        if g.var("frame") != before:
            break
    else:
        raise AssertionError("the game never sampled a %r press in 60 frames" % key)
    g.run(16)           # the tilemap trails main.c's statics by an iteration


def cycle_to(g, sq, limit=8):
    """With a piece picked up, press until the cursor is on the landing square
    `sq`.

    Picking a piece up SEATS the cursor on one of its landing squares, and the
    d-pad then cycles a press at a time, wrapping - which key it is does not
    matter, since a piece's landing squares are not neighbours of each other
    and so "walk that way" has no meaning between them.  So a check that wants
    the OTHER landing square presses; it does not walk.  Bounded, and it
    returns whether it got there, so a stuck cursor reads as a FAILED CHECK
    rather than a hang."""
    for _ in range(limit):
        if g.var("cursor") == sq:
            return True
        step(g, "D")
    return g.var("cursor") == sq


def tap_at_notch(g, key, tap=6):
    """A person's tap - 6 frames, ~100 ms - aimed at the one place the buggy
    repeat rule fired.

    The rule was `(frame & (REPEAT_DIV - 1)) == 0` with the key still down, and
    `frame` is at an arbitrary value when a player presses: so a tap whose
    SECOND turn of the loop landed on a multiple of REPEAT_DIV walked two
    squares, and which taps did depended on nothing the player could see.  Aim
    at `frame & 7 == 6` and the next turn of the loop is that multiple, so a
    perfectly ordinary tap does it every time instead of one time in eight.
    Measured on the build without the fix, this aim and this hold give 17 (two
    squares); with the fix, 13.

    Aiming first, with the key up, is what keeps this a tap: the aiming costs
    the player nothing, and 6 frames is a real press.  The aim does couple the
    check to the loop's rate - the notch has to still be ~2 turns away when the
    press starts - so if the loop rate changes, re-measure rather than assume.
    """
    for _ in range(40):                     # aiming: the key is UP
        if (g.var("frame") & 7) == 6:
            break
        g.run(1)
    g.run(tap, key)
    g.run(16)                               # the tilemap trails the statics
    return tap


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
    check((x, y) == ring_oam(c0),
          "the cursor's OAM is ring_oam(square %d) = cell(%d, %d) + (12, 20) -- "
          "OAM is not screen coords; the DMG draws at (x-8, y-16), so the raw "
          "corner reading means the ring was one square up-left (%r, want %r)"
          % (c0, sq_row(c0), sq_col(c0), (x, y), ring_oam(c0)))
    check(g.cursor_box() is not None,
          "and its rendered box is really on the screen - parked is OAM (0, 0) "
          "(%r)" % (g.cursor_box(),))
    check(8 <= x <= 168 and 16 <= y <= 159,
          "its rendered box lies inside the screen, so a cursor on any cell - "
          "row 0's and column 0's included - is actually drawn (%r)" % ((x, y),))
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
    # walk() call, and every square reached is expanded in turn.
    seen, frontier = {c0}, [c0]
    while frontier:
        nxt = []
        for s in frontier:
            for k in "UDLR":
                set_cursor(g, s)
                step(g, k)
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
    check(g.cursor_xy() == ring_oam(13),
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
    check(g.var("cursor") in (13, 14),
          "and the cursor stays where the pickup seated it - on a landing "
          "square, not back on the piece (%d)" % g.var("cursor"))
    check(g.cursor_xy() == ring_oam(g.var("cursor")),
          "with the sprite still under it (%r)" % (g.cursor_xy(),))

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
    # The walk may rest on bare board, but never on a piece it cannot use.  It
    # starts from 9, so put the cursor there: the drop above left it on the
    # landing square the pickup seated it on, which is the new behaviour and
    # not what this check is about.  UP from 9 is DIR_NW and lands on 5, which
    # is empty; UP again is 0, the black man who must not be pickable, so he is
    # stepped over and - the board ending there - the cursor holds on 5.  A on
    # 5, an empty square, then picks up nothing, and only then is the cursor
    # put back on 9.
    set_cursor(g, 9)
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

    # Picking it up SEATS the cursor on a landing square, so that the next A
    # always has something to do.  It used to stay on the piece, and the player
    # had to walk it to a destination - which is a dead press whenever the walk
    # ends on nothing, and it usually does: a piece's landing squares are not
    # neighbours of each other.  That silence is exactly what "A does nothing"
    # looked like from the sofa.  The d-pad then cycles, one landing square a
    # press, and which key it is does not matter.
    first = g.var("cursor")
    check(first in (16, 18),
          "picking the man up seats the cursor on a landing square (%d)" % first)
    check(g.cursor_xy() == ring_oam(first),
          "and the sprite has followed it off the piece (%r)" % (g.cursor_xy(),))
    step(g, "R")                # RIGHT is DIR_NE - and the cycle ignores it
    other = 18 if first == 16 else 16
    check(g.var("cursor") == other,
          "one press moves it to the other landing square, %d, whichever key "
          "was pressed (%d)" % (other, g.var("cursor")))
    g.run(90, "D")              # the repeat takes the same path
    check(g.var("cursor") in (16, 18),
          "and holding cycles round the landing squares rather than off the "
          "list (%d)" % g.var("cursor"))
    check(g.visible_sprites() == [0, 1],
          "the screen holds two rings: the cursor on the landing square, and "
          "second slot marking the picked-up piece (%r)"
          % (g.visible_sprites(),))
    sy, sx = g.p.memory[OAM + 4], g.p.memory[OAM + 5]
    check((sx, sy) == ring_oam(9),
          "and the second ring really marks the piece itself - OAM of square "
          "9's cell, with nothing over the piece it just left (%r, want %r)"
          % ((sx, sy), ring_oam(9)))


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
    # SE).  The pickup ALREADY seated the cursor on one of them - that is the
    # point of the seat - so a press moves it to the OTHER one, and which key
    # was pressed does not matter: "walk that way" has no meaning between two
    # squares that are not neighbours.  cycle_to() aims at a particular one.
    first = g.var("cursor")
    check(first in (13, 14),
          "the pickup seated the cursor on one of the two landing squares (%d)"
          % first)
    cycle_to(g, 13)
    check(g.var("cursor") == 13,
          "one press cycles it to the other landing square, 13 (%d)"
          % g.var("cursor"))
    check(g.cursor_xy() == ring_oam(13),
          "and the sprite followed it to cell(3, 2) = OAM (44, 68) (%r)"
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

    # A puts it on a landing square, and the jumps are not neighbours of the
    # piece or of each other, so the other one is a press away, not a walk.
    check(cycle_to(g, 18),
          "a press per landing square reaches 18, the jump that takes the man "
          "on 14 (%d)" % g.var("cursor"))

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

    check(cycle_to(g, 22), "and the cursor is on 22, its landing square (%d)"
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
    check(g.var("cursor") == 29,
          "and the cursor is already on the chain's next landing square, 29 (%d)"
          % g.var("cursor"))
    check(hud_word(g) == "BLACK", "the HUD still says BLACK (%r)" % hud_word(g))
    check(cell_kind(g, 5, 4) == 0,
          "the screen drew the capture: a black MAN on cell(5, 4) (%r)"
          % (cell_kind(g, 5, 4),))
    check(g.cell_is(4, 3, T_DARK),
          "and the taken man's cell is bare board again (%r)"
          % ([g.tile(x, y) for x, y in cells(4, 3)],))

    # --- hop two ---------------------------------------------------------
    # The chain hop landed the piece on 22 and SEATED the cursor on the next
    # hop's landing square, 29 - the same piece, still up, no press needed.
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

    check(cycle_to(g, 30), "and the cursor is on 30, its landing square (%d)"
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
    """Play 0 -> 4: A to pick the man on 0 up, which seats the cursor on 4, the
    only landing square it has, then A to move it."""
    tap(g, "A")
    check(cycle_to(g, 4), "A on 0 seats the cursor on 4 (%d)" % g.var("cursor"))
    tap(g, "A")


def p6_win_and_restart(g):
    """A turn that leaves the other side with nothing to play ends the game,
    and START (or B) starts a new one."""
    print("P6 win and restart")
    g.start()
    if not g.need("board", "side", "state", "cursor", "sel", "n_moves", "moves"):
        return
    # Two players, stated rather than relied on.  This check hands the turn to
    # white and needs it to stay there, and under P14's 1-Player default it
    # would only be safe because p6_setup_stuck's position leaves white with no
    # moves at all, so `state` is ST_OVER and machine_to_move() is false.  That
    # is an accident of this position, not a property of the check, and it ends
    # the moment someone picks a stuck position white can actually use.
    # Game.start() already forces 2P for every caller; this says so locally.
    if "mode_1p" in g.addr:
        set_byte(g, "mode_1p", MODE_2P)

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


# ---------------------------------------------------------------- P7 --------
def p7_responsiveness(g):
    """The loop turns over fast enough to catch a human keypress.

    This is the check the game shipped without, and its absence cost a whole
    play-test: rebuilding and blitting the screen on every frame - including the
    frames where the player was only thinking - put the loop at 8.6 emulator
    frames an iteration, so `pressed` was sampled once every 143 ms and an
    ordinary tap (~100 ms) fell between two samples and did nothing at all.
    Nothing else here can see that.  Every other check reads state, and the
    state was right the whole time; it was simply read too slowly to hear the
    buttons, and "I can't get a piece to move" is what that looks like.

    Iterations are counted by sampling `frame` once per tick and SUMMING the
    gaps.  Read as a byte across the window it wraps at 256, which is how this
    same loop was once measured as running at 30 a second when it was at 7.
    """
    print("P7 loop rate")
    g.start()
    if not g.need("frame"):
        return
    prev, total, ticks = g.var("frame"), 0, 60
    for _ in range(ticks):
        g.p.tick(1, True)
        cur = g.var("frame")
        total += (cur - prev) % 256
        prev = cur
    check(total > 0, "the main loop is running at all (%d iterations in %d "
                     "frames)" % (total, ticks))
    if not total:
        return
    per = ticks / total
    # A human tap is ~6 frames (100 ms) and a press is dropped only if it falls
    # entirely between two of the loop's samples, so the gap between samples is
    # the whole question.  Four frames is comfortably under a tap; the loop runs
    # at 3.  The always-redraw loop this replaced was at 8.6 and fails here by a
    # mile, which is the point of the number being 4 and not 9.
    check(per <= 4.0, "the loop turns over in %.2f emulator frames (%.0f Hz), so "
                      "input is sampled often enough to catch a tap - not the "
                      "8.6 frames (7 Hz) that dropped them (%d iterations in %d "
                      "frames)" % (per, total * 59.7 / ticks, total, ticks))


# ---------------------------------------------------------------- P8 --------
def p8_one_step_per_tap(g):
    """A tap walks one square.  Never two, never none.

    This pins one line of main.c, and it is the second half of "I can't get a
    piece to move".  The repeat was tested against the free-running `frame`
    counter rather than against how long the key had been held, so a press that
    happened to land near a multiple of REPEAT_DIV took the repeat branch on its
    very next turn of the loop and walked TWO squares.  A cursor that jumps two
    at a time is as unplayable as one that never moves - and it got worse the
    moment P7 made the loop turn over fast enough to sample a tap at all, which
    is why the check lives here and not in P3.
    """
    print("P8 one tap, one square")
    g.start()
    if not g.need("frame", "cursor"):
        return
    n = tap_at_notch(g, "D")
    check(g.var("cursor") == 13,
          "a %d-frame D tap from square 8 lands the cursor one square on, on "
          "13 - not two on 17, and not still on 8 (%d)" % (n, g.var("cursor")))
    # ... and holding is still a hold.  The fix must not have cured the double
    # step by capping the d-pad at one square per press: a cursor that has to
    # be tapped four times to cross the board is its own regression.
    g.run(90, "D")
    check(g.var("cursor") > 13,
          "and a held D still repeats past it (%d)" % g.var("cursor"))


# --------------------------------------------------------------- P10 -------
# main.c's mode global and the two mode lines the title offers.  `mode_1p` is
# the symbol this phase adds, so its absence from the map is what makes this
# check answer 1 on P9's ROM instead of the harness error 2.
MODE_2P, MODE_1P = 0, 1

DIR_NW, DIR_NE, DIR_SW, DIR_SE = 0, 1, 2, 3   # board_geom.h's direction order

# board_geom.h's BLACK_KING_ROW_MIN / WHITE_KING_ROW_MAX, written as they are
# used here (a black man promotes at >= 28, a white one at <= 3).
BLACK_KING_MIN, WHITE_KING_MAX = 28, 3


def neigh_table():
    """NEIGH, parsed out of the generated board_geom.h.

    A second copy of the diagonal table in this file would drift the moment
    `make geom` regenerated the real one - and these checks exist precisely to
    compute for themselves what sim.h would compute, rather than asking the ROM
    to vouch for its own answers."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "board_geom.h")
    rows = []
    for line in open(path):
        m = re.match(r"\s*\{\s*(.*?)\s*\},\s*/\*\s*\d+\s*\*/", line)
        if m:
            rows.append(tuple(NO_SQ if v.strip() == "NO_SQ" else int(v)
                              for v in m.group(1).split(",")))
    if len(rows) != 32:
        raise ValueError("board_geom.h: parsed %d NEIGH rows, wanted 32"
                         % len(rows))
    return rows


NEIGH = neigh_table()


def piece_dirs(kings, sq, side):
    """sim.h's dir_mask() - which of the four diagonals this piece may use."""
    if kings & (1 << sq):
        return (DIR_NW, DIR_NE, DIR_SW, DIR_SE)
    return (DIR_SW, DIR_SE) if side == SIDE_BLACK else (DIR_NW, DIR_NE)


def gen_captures_from(black, white, kings, side, sq):
    """sim.h's gen_captures_from(): this one piece's jumps, in the engine's
    order.  No mandatory-capture rule here - that is the caller's question."""
    own = black if side == SIDE_BLACK else white
    opp = white if side == SIDE_BLACK else black
    out = []
    for d in piece_dirs(kings, sq, side):
        mid = NEIGH[sq][d]
        if mid == NO_SQ or not opp & (1 << mid):
            continue
        land = NEIGH[mid][d]
        if land == NO_SQ or (own | opp) & (1 << land):
            continue
        out.append({"from": sq, "to": land, "capture": mid})
    return out


def gen_moves(black, white, kings, side):
    """sim.h's gen_moves(): the whole side's legal moves, mandatory capture
    already folded in - captures if any piece has one, else every quiet move.
    This is the list the machine has to choose from, computed here so the check
    does not have to take the ROM's word for what is legal."""
    own = black if side == SIDE_BLACK else white
    opp = white if side == SIDE_BLACK else black
    quiet, cap = [], []
    for sq in range(32):
        if not own & (1 << sq):
            continue
        jumps = gen_captures_from(black, white, kings, side, sq)
        if jumps:
            cap.extend(jumps)
            continue
        for d in piece_dirs(kings, sq, side):
            mid = NEIGH[sq][d]
            if mid == NO_SQ or (own | opp) & (1 << mid):
                continue
            quiet.append({"from": sq, "to": mid, "capture": NO_SQ})
    return cap or quiet


def apply_move(black, white, kings, mv):
    """sim.h's apply_move(): move, take, and promote.

    The promotion half is not optional - a "is this the board after that move"
    comparison that skipped it would call a crowning move wrong, and crowning
    is one of the things this check is here to see."""
    f, t = 1 << mv["from"], 1 << mv["to"]
    black_side = bool(black & f)
    was_king = bool(kings & f)
    if black_side:
        black = (black & ~f) | t
    else:
        white = (white & ~f) | t
    if was_king:
        kings = (kings & ~f) | t
    if mv["capture"] != NO_SQ:
        c = 1 << mv["capture"]
        black, white, kings = black & ~c, white & ~c, kings & ~c
    if not was_king:
        if black_side and mv["to"] >= BLACK_KING_MIN:
            kings |= t
        if not black_side and mv["to"] <= WHITE_KING_MAX:
            kings |= t
    return black, white, kings


def board_cells(black, white, kings):
    """{(row, col): kind} that the screen should be showing, from the masks.
    Kind 0..3 is main.c's T_PIECE order: {black, white} x {man, king}."""
    out = {}
    for sq in range(32):
        m = 1 << sq
        who = 0 if black & m else 1 if white & m else None
        if who is None:
            continue
        out[(sq_row(sq), sq_col(sq))] = who + (2 if kings & m else 0)
    return out


def maskstr(masks):
    return " ".join("0x%08X" % v for v in masks)


def set_byte(g, name, val):
    """One main.c static, written as one byte.

    PyBoyMemoryView takes an INT for a single address and a bytes-like only for
    a slice, so `mem[addr] = bytes([v])` is a TypeError, not a write."""
    a = g.addr[name]
    g.p.memory[a:a + 1] = bytes([val])


def force_position(g, black, white, kings, side, settle=6):
    """Put a whole position into main.c's statics, at a turn start.

    A midgame position with the MACHINE to move cannot be played into and cannot
    be caught on the way past: the reply happens inside the same loop iteration
    as the move before it, so there is no frame between them to look at.  So the
    board goes straight into WRAM - the trick p4/p5/p6 already use - widened
    here to every static a turn boundary parks, so the position is a real turn
    start and not half of one.

    `settle` is the frames to let the write take: the default is enough to see
    the position on screen, and P11 passes 0 because every one of those frames
    is a frame of the think it is timing."""
    a = g.addr["board"]
    g.p.memory[a:a + 12] = board_bytes(black, white, kings)
    for name, val in (("side", side), ("sel", NO_SQ), ("cursor", NO_SQ),
                      ("chain_sq", NO_SQ), ("state", ST_SELECT)):
        set_byte(g, name, val)
    g.run(settle)


def await_reply(g, limit=900, step=10):
    """Wait out the machine's turn, and let the screen catch up behind it.

    The whole think is a blocking call in the middle of one loop iteration, so
    the reply can only be waited for, never caught between frames.  Then the
    settle: `side` flips inside play(), several frames before that iteration's
    blit, and the tilemap the probe reads is whatever was blitted LAST - so
    without it a screen assertion reads the frame before the reply.  `limit`
    only has to be longer than P11's budget for the think."""
    for _ in range(limit // step):
        g.run(step)
        if g.var("side") == SIDE_BLACK:
            g.run(20)
            return True
    return False


def p10_ai_opponent(g):
    """1 PLAYER: the machine plays white, through the same play() the human's
    moves go through - and the 2-player game is what it always was."""
    print("P10 the machine takes white")
    # Boot the way p1 does: the Game constructor ticks nothing, so without this
    # every read below is of an emulator that has not run main() yet.
    g.run(150)
    if not g.need("mode_1p", "arena", "side", "board", "state", "cursor",
                  "sel", "n_moves", "moves", "chain_sq"):
        return

    # --- the title's mode line, and SELECT flipping it --------------------
    # The title screen is a function of nothing but mode_1p in this respect, and
    # `board`, `side`, `state`, `sel` and `chain_sq` are all unchanged by a
    # SELECT press - so a screen_stale() that does not watch mode_1p leaves the
    # old line sitting there and this is what catches it.
    #
    # P14 inverted these four: the boot default is 1 PLAYER now (it was 2), so
    # the first pair asserts the new default and the taps walk the other way.
    # The last one still has to land on 1 PLAYER, which is the game about to
    # start below - it takes two taps to get back there now, not three.
    check(g.find_text("1 PLAYER") != [] and g.find_text("2 PLAYERS") == [],
          "the title offers 1 PLAYER (%r / %r)"
          % (g.find_text("1 PLAYER"), g.find_text("2 PLAYERS")))
    check(g.var("mode_1p") == MODE_1P,
          "and 1 player is where the game starts (%d)" % g.var("mode_1p"))
    tap(g, "T")
    check(g.find_text("2 PLAYERS") != [] and g.find_text("1 PLAYER") == [],
          "SELECT flips the line to 2 PLAYERS, and the screen redrew for it "
          "(%r / %r)" % (g.find_text("2 PLAYERS"), g.find_text("1 PLAYER")))
    check(g.var("mode_1p") == MODE_2P,
          "mode_1p followed it (%d)" % g.var("mode_1p"))
    tap(g, "T")
    check(g.var("mode_1p") == MODE_1P and g.find_text("1 PLAYER") != []
          and g.find_text("2 PLAYERS") == [],
          "and SELECT flips it back - 1 PLAYER, for the game about to start "
          "(%d, %r)" % (g.var("mode_1p"), g.find_text("1 PLAYER")))

    # --- 1 player: the human opens as black -------------------------------
    g.run(20, "S")              # held, not tapped - see Game.start()
    g.run(30)
    check(g.var("state") == ST_SELECT and g.var("side") == SIDE_BLACK,
          "the game starts with the human to move: black, as always (%d, %d)"
          % (g.var("state"), g.var("side")))
    check(hud_word(g) == "BLACK", "the HUD says BLACK (%r)" % hud_word(g))
    check(g.cursor_box() is not None,
          "and the human's cursor is on the board, not parked (%r)"
          % (g.cursor_box(),))

    # The p4 flow, unchanged: pick the man on 9 up and play it to 13.  Nothing
    # here is new - p4 owns it - and that is the point: the human's half of a 1P
    # turn has to be exactly the 2P half.
    set_cursor(g, 9)
    tap(g, "A")
    check(g.var("sel") == 9 and g.var("state") == ST_TARGET,
          "A picks the man on 9 up (%d, state %d)"
          % (g.var("sel"), g.var("state")))
    check(cycle_to(g, 13), "and cycles the cursor to 13 (%d)" % g.var("cursor"))
    tap(g, "A")

    # --- and white replies -------------------------------------------------
    # The reply comes inside the same loop iteration the human's move was made
    # in, so the board it replied FROM is never on screen and never readable
    # between frames.  It is reproduced instead: the start position with the
    # human's 9 -> 13 played, which is exactly what p4 pins that tap to be.
    pre = apply_move(BLACK_START, WHITE_START, 0,
                     {"from": 9, "to": 13, "capture": NO_SQ})
    check(await_reply(g),
          "the machine replies: black is to move again (%d, state %d)"
          % (g.var("side"), g.var("state")))
    post = board_masks(g)
    allowed = gen_moves(pre[0], pre[1], pre[2], SIDE_WHITE)
    matched = [m for m in allowed if apply_move(*pre, m) == post]
    # Exactly one: no two legal moves produce the same board, so a single match
    # is the whole statement "the machine played a legal move".  White has no
    # capture here (its men are on 20-31, black's only forward piece is the man
    # on 13, and no white man is adjacent to it), so the reply is one quiet hop
    # and this comparison against a single move is complete.
    check(len(matched) == 1,
          "the machine's reply is ONE of white's legal moves on the pre-reply "
          "board - %s -> %s (legal set %r)"
          % (maskstr(pre), maskstr(post), allowed))
    check(bin(post[0]).count("1") == 12 and bin(post[1]).count("1") == 12,
          "and it was quiet - both sides still have twelve men (black %s, "
          "white %s)" % (bin(post[0]), bin(post[1])))
    check(piece_kinds(g) == board_cells(*post),
          "the machine's move reached the screen - what is drawn is the "
          "post-reply board, cell for cell")

    # --- the machine obeys mandatory capture ------------------------------
    print("P10 the machine takes a forced capture")
    # White men on 18 and 22, black men on 14 and 17.  Each white man can jump
    # one black man (18 -NW-> 9 over 14, 22 -NW-> 13 over 17), and neither has a
    # quiet move once a capture is on - so mandatory capture leaves the machine
    # exactly two moves to choose between, and both of them take a piece.  A
    # machine that quietly played a forward move here would be caught by the
    # "is the reply in the legal set" comparison, because the quiet moves are
    # not in that set.
    blk, wht = (1 << 14) | (1 << 17), (1 << 18) | (1 << 22)
    set_byte(g, "mode_1p", MODE_1P)
    force_position(g, blk, wht, 0, SIDE_WHITE)
    pre = (blk, wht, 0)
    allowed = gen_moves(blk, wht, 0, SIDE_WHITE)
    check(len(allowed) == 2 and all(m["capture"] != NO_SQ for m in allowed),
          "white is offered two captures and nothing else (%r)" % (allowed,))
    check(await_reply(g),
          "the machine takes one: black is to move again (%d)" % g.var("side"))
    post = board_masks(g)
    matched = [m for m in allowed if apply_move(*pre, m) == post]
    check(len(matched) == 1,
          "and what it played is one of those two captures - %s -> %s "
          "(captures %r)" % (maskstr(pre), maskstr(post), allowed))
    check(bin(post[0]).count("1") == 1,
          "black really is a man down (black %s)" % bin(post[0]))
    check(piece_kinds(g) == board_cells(*post),
          "and the taken man left the screen too")

    # --- the move is matched by (from, to), not by destination ------------
    print("P10 the machine's move is not another piece's move")
    # White king on 4 and white man on 5 can BOTH reach square 0, and 0 is on
    # white's promotion row, so the man's move is worth a whole king more than
    # the king's and the search takes it - while the king's move to that same
    # square comes FIRST in the move list (square 4 is scanned before 5).  A
    # play() that matched on the destination alone would find the king's move
    # first and play it: same square, still legal, wrong piece, and no crown.
    #
    # A black man on square 1 is what makes it decidable: it blocks the man's
    # other forward move (5 -NE-> 1), so 5 -> 0 is its only move and the
    # search's choice is forced rather than a tie broken by mobility.  Verified
    # against a from-scratch minimax of sim.h's own evaluation: 5 -> 0 is the
    # unique best at depths 1, 2 and 3.
    blk, wht, kng = (1 << 1) | (1 << 20) | (1 << 24), (1 << 4) | (1 << 5), 1 << 4
    force_position(g, blk, wht, kng, SIDE_WHITE)
    pre = (blk, wht, kng)
    allowed = gen_moves(blk, wht, kng, SIDE_WHITE)
    pairs = [(m["from"], m["to"]) for m in allowed]
    check(pairs == [(4, 0), (4, 8), (5, 0)],
          "white has three moves and two of them reach square 0, the king's "
          "first (%r)" % (pairs,))
    check(await_reply(g), "the machine replies (%d)" % g.var("side"))
    post = board_masks(g)
    exp = apply_move(blk, wht, kng, {"from": 5, "to": 0, "capture": NO_SQ})
    check(post == exp,
          "it played the MAN's move to 0 - crowned, with the king on 4 never "
          "moving - not the king's move to the same square (got %s, wanted %s)"
          % (maskstr(post), maskstr(exp)))
    check(bool(post[1] & (1 << 4)) and bool(post[2] & (1 << 0)),
          "the man crowned and the old king is still on 4 (white 0x%08X, "
          "kings 0x%08X)" % (post[1], post[2]))
    check(piece_kinds(g) == board_cells(*post),
          "and the screen shows two white kings and the three black men")

    # --- and the 2-player game is exactly what it was ---------------------
    print("P10 the 2-player game is untouched")
    set_byte(g, "mode_1p", MODE_2P)
    set_byte(g, "state", ST_TITLE)
    g.run(30)
    check(g.find_text("2 PLAYERS") != [] and g.find_text("1 PLAYER") == [],
          "back at the title with 2 PLAYERS (%r)" % (g.find_text("2 PLAYERS"),))
    g.run(20, "S")
    g.run(30)
    check(g.var("side") == SIDE_BLACK and g.var("state") == ST_SELECT,
          "a 2-player game starts with black to move (%d, %d)"
          % (g.var("side"), g.var("state")))
    set_cursor(g, 9)
    tap(g, "A")
    check(cycle_to(g, 13), "the cursor cycles to 13 (%d)" % g.var("cursor"))
    tap(g, "A")
    check(g.var("side") == SIDE_WHITE,
          "black's move hands the turn to white, exactly as before (%d)"
          % g.var("side"))
    g.run(150)
    check(g.var("side") == SIDE_WHITE and g.var("state") == ST_SELECT,
          "and 150 frames later white is still waiting for a person - no "
          "machine took the turn (%d, %d)"
          % (g.var("side"), g.var("state")))
    human = apply_move(BLACK_START, WHITE_START, 0,
                       {"from": 9, "to": 13, "capture": NO_SQ})
    check(board_masks(g) == human,
          "the board is exactly the one human move (%s)" % maskstr(board_masks(g)))
    check(piece_kinds(g) == board_cells(*human),
          "and the screen is that board - 23 men and the square it left bare")


# --------------------------------------------------------------- P11 -------
# The think budget, in EMULATOR FRAMES - the number the player waits.  600
# frames is ~10 s of DMG time (a frame is 16.7 ms), which is the cost of the
# deepest search that does not leave the machine sitting on a still screen for
# long enough to look hung.  THINKING is blitted BEFORE the search and stays up
# for all of it, so the player is never looking at a frozen board.
THINK_BUDGET = 600

# The depth main.c is built at (AI_DEPTH / the ai_depth symbol), and the number
# main.c's AI_DEPTH comment records.  Held to the ROM under test because a
# budget check is a claim about ONE depth: on a shallower build it would pass
# with nothing measured.
MEASURED_DEPTH = 2

# The three positions the depth table was measured at, one per game phase, and
# the WORST draw of each phase rather than a typical one.  Cost is driven by
# branching - white has 12 moves in the midgame below against 7 at the start -
# and a think that fits at the start position while a 12-piece midgame runs
# four times as long is a frozen game exactly when the player has the most to
# look at.  The other draws measured (two midgames, one endgame), and every
# depth, are in that same table in main.c.
THINK_POSITIONS = [
    ("opening (the start)",  (BLACK_START, WHITE_START, 0)),
    ("midgame, 12 pieces",   (0x02384004, 0x2440A400, 0x0000A000)),
    ("endgame, 4 pieces",    (0x09000000, 0x00004040, 0x00000040)),
]


def decode_tile(t):
    """glyph()'s inverse: a tile id back to the character it draws."""
    if t == T_BLANK:
        return " "
    if T_DIGIT0 <= t < T_DIGIT0 + 10:
        return chr(ord("0") + t - T_DIGIT0)
    if T_LETTER0 <= t < T_LETTER0 + 26:
        return chr(ord("A") + t - T_LETTER0)
    return "."


def screen_rows(g, nrows=18):
    """The visible tilemap as text, one row per line.

    One slice read rather than find_text()'s tile-by-tile scan: this runs once
    per emulated frame of a think, and 576 individual reads per frame is the
    difference between a check that takes a second and one that takes a minute."""
    raw = bytes(g.p.memory[MAP0:MAP0 + MAP_W * nrows])
    return ["".join(decode_tile(t) for t in raw[y * MAP_W:(y + 1) * MAP_W])
            for y in range(nrows)]


def shows(g, s):
    return any(s in row for row in screen_rows(g))


def think_frames(g, cap):
    """Emulator frames from here until the machine has replied.

    One tick at a time, counted here - NOT the game's own `frame` counter, which
    is the trap this phase is about.  `frame` advances once per turn of the main
    loop and the think is a blocking call inside one of those turns, so across a
    900-frame think `frame` advances by ONE: a check written on it would read
    every depth as instantaneous and pass on all of them.

    Returns (frames spent, the last frame THINKING was on the screen, replied).
    `cap` is the budget: past it there is nothing left to learn, and stopping
    means an over-budget depth is caught in 600 frames instead of however many
    thousand it would have taken to finish."""
    n, seen = 0, 0
    while n < cap:
        g.p.tick(1, True)
        n += 1
        if shows(g, "THINKING"):
            seen = n
        if g.var("side") == SIDE_BLACK:
            return n, seen, True
    return n, 0, False


def p11_think_budget(g):
    """The machine's worst think fits the budget the depth was chosen for.

    The whole phase in one check: a depth picked by measurement is only worth
    anything if the ROM can be held to it, and the number that matters is the
    WORST case, not the average - a think that fits at the start position and
    runs for six seconds out of a 12-piece midgame is a frozen game exactly
    when the player has the most to look at.
    """
    print("P11 think budget")
    g.run(150)
    if not g.need("frame", "side", "board", "state", "sel", "cursor",
                  "chain_sq", "mode_1p", "ai_depth"):
        return
    check(g.var("ai_depth") == MEASURED_DEPTH,
          "the ROM was built at the measured depth (%d) and not some other - "
          "this is the depth the budget below was measured for (%d)"
          % (MEASURED_DEPTH, g.var("ai_depth")))

    set_byte(g, "mode_1p", MODE_1P)
    set_byte(g, "state", ST_SELECT)
    g.run(10)

    for label, pre in THINK_POSITIONS:
        force_position(g, pre[0], pre[1], pre[2], SIDE_WHITE, settle=0)
        f0 = g.var("frame")
        n, seen, replied = think_frames(g, THINK_BUDGET)
        stall = (g.var("frame") - f0) & 255

        # Nothing below can be trusted once the cap is hit: the emulator is
        # still inside the search, `side` is white for as long as it lasts, and
        # the next position's write would be swallowed by it.  So a blown
        # budget ends the check here, with the one assertion that says why.
        check(replied,
              "%s: the machine replied inside the budget - still searching "
              "after %d emulator frames (%.1f s)"
              % (label, n, n / 59.7))
        if not replied:
            return

        check(n <= THINK_BUDGET,
              "%s: the think cost %d emulator frames (%.2f s), inside the %d "
              "budget" % (label, n, n / 59.7, THINK_BUDGET))
        print("     %s: %d frames (%.2f s)" % (label, n, n / 59.7))
        # The think is a blocking call inside ONE turn of the main loop, which
        # is why it cannot be timed with `frame`: at most a turn or two of the
        # loop happens across the whole search, however long that is.
        check(stall <= 4,
              "%s: and it was one blocked turn of the loop, not a slow one "
              "(`frame` advanced %d across %d frames)" % (label, stall, n))
        # THINKING is blitted before the search and stays up for it: the frame
        # it stops being drawn on is the frame the answer is already in, so it
        # covers the whole wait bar the one or two frames the blit itself takes.
        check(seen >= n // 2 and seen > 0,
              "%s: THINKING was on the screen through the think (%d of %d "
              "frames)" % (label, seen, n))

        post = board_masks(g)
        allowed = gen_moves(pre[0], pre[1], pre[2], SIDE_WHITE)
        matched = [m for m in allowed if apply_move(*pre, m) == post]
        check(len(matched) == 1,
              "%s: and the reply is one of white's legal moves - %s -> %s"
              % (label, maskstr(pre), maskstr(post)))
        check(g.var("side") == SIDE_BLACK and g.var("state") != ST_OVER,
              "%s: black is to move again in a live game (%d, state %d)"
              % (label, g.var("side"), g.var("state")))
        # `side` flips inside play(), in the middle of the reply's turn of the
        # loop -- the rebuild and blit that take THINKING back off the screen
        # are still to come, and they cost several emulator frames.  So the
        # settle is measured in the loop's own pace, not in one frame.
        g.run(30)
        check(not shows(g, "THINKING"),
              "%s: and THINKING is off the screen once the move is played"
              % (label,))


# --------------------------------------------------------------- P12 -------
# The APU's registers, straight out of the hardware memory map.  Two things
# about reading them here:
#
#   - PyBoy only implements them when it was built with sound_emulated=True
#     (see the Game constructor).  With sound off every one of these reads
#     0x00 forever and writes vanish, so a register check written against that
#     build fails on EVERY ROM.  It would gate as "has power" and then fail
#     the phase it was written for.  The switch went on first, and P11's frame
#     counts are unchanged by it.
#   - The DMG boot ROM plays its own jingle on CH1/CH2 for ~60 frames, so a
#     reading taken just after START is the emulator's boot noise, not this
#     ROM's writes.  Every comparison below is a before/after pair taken
#     around a hop, hundreds of frames after boot, and never an absolute
#     reading of a register the boot ROM itself drives.
NR12, NR22, NR42, NR52 = 0xFF12, 0xFF17, 0xFF21, 0xFF26

# main.c's envelope bytes, mirrored here the way T_PIECE and the State enum
# already are.  Retuning a sound means moving both, which is deliberate: this
# is the contract for what one hop writes, not an independent opinion.
MOVE_ENV, CAPTURE_ENV, PROMOTE_ENV = 0x93, 0x81, 0x53


def p12_sfx(g):
    """A hop clicks, a capture thuds, a crown crashes - and play() is the only
    place any of them is made.

    PyBoy has no ears, so nothing here can hear anything.  What it can read is
    the APU's registers, and those ARE the sound: each of main.c's three
    sounds is one register set with its envelope written into it, so "the byte
    is what main.c wrote" is the whole of the noise.  The other half of the
    deliverable - that the three are audibly distinct - is confirmed once by
    ear in mGBA, not here; that half is an eyeball, and the gate is the bytes.

    Every part pairs a positive control with its assertion.  "CH2 did not
    change on a quiet move" is true of a ROM with no sound code in it at all,
    so the quiet move also has to prove CH1 DID change; likewise each sound is
    only claimed once the hop that should have made it is shown to have
    happened, by the board.
    """
    print("P12 a quiet hop clicks on CH1 and leaves CH2 alone")
    g.start()
    if not g.need("board", "side", "cursor", "sel", "state", "n_moves", "moves"):
        return

    # --- a quiet move -----------------------------------------------------
    # p4's move played the p4 way: pick the man on 9 up, cycle to 13, A.
    ch1_0, ch2_0 = g.p.memory[NR12], g.p.memory[NR22]
    set_cursor(g, 9)
    tap(g, "A")
    check(cycle_to(g, 13), "the cursor cycles to 13 (%d)" % g.var("cursor"))
    tap(g, "A")
    ch1_1, ch2_1 = g.p.memory[NR12], g.p.memory[NR22]
    check(g.var("side") == SIDE_WHITE,
          "the quiet move really was played - the turn passed (%d)"
          % g.var("side"))
    check(ch1_1 == MOVE_ENV and ch1_1 != ch1_0,
          "the hop wrote CH1's envelope: NR12 0x%02X -> 0x%02X, main.c's "
          "0x%02X" % (ch1_0, ch1_1, MOVE_ENV))
    check(ch2_1 == ch2_0,
          "and left CH2 alone - a quiet move is not a capture: NR22 unchanged "
          "at 0x%02X" % ch2_1)

    # --- a capture --------------------------------------------------------
    print("P12 a capture thuds on CH2")
    # p4's mandatory-capture position: a black man on 9 able to jump either of
    # 13 and 14, and a black man on 0 with quiet moves it must not be offered.
    # The jump played here is the one over 14, landing on 18.
    force_position(g, (1 << 9) | (1 << 0), (1 << 13) | (1 << 14), 0, SIDE_BLACK)
    set_cursor(g, 9)
    ch2_0 = g.p.memory[NR22]
    tap(g, "A")
    check(cycle_to(g, 18), "the cursor cycles to the jump onto 18 (%d)"
          % g.var("cursor"))
    tap(g, "A")
    ch2_1 = g.p.memory[NR22]
    blk, wht, kng = board_masks(g)
    check(not (wht & (1 << 14)) and bool(blk & (1 << 18)),
          "the jump really was played - the black man is on 18 and the white "
          "man on 14 is gone (black 0x%08X, white 0x%08X)" % (blk, wht))
    check(ch2_1 == CAPTURE_ENV and ch2_1 != ch2_0,
          "and CH2's envelope was rewritten for it: NR22 0x%02X -> 0x%02X, "
          "main.c's 0x%02X" % (ch2_0, ch2_1, CAPTURE_ENV))
    check(bool(g.p.memory[NR52] & 0x80),
          "with the APU on, so the writes were not swallowed (NR52 0x%02X)"
          % g.p.memory[NR52])

    # --- a promotion ------------------------------------------------------
    print("P12 a crown crashes on CH4")
    # p5's promotion position: 21 -SE-> 30 over 25, and 30 is in row 7, so the
    # man crowns on landing.  A promoting CAPTURE on purpose - it is the hop
    # that fires two of the three sounds, on two different channels, and this
    # is where "one register set per sound, split by channel" pays off.
    force_position(g, 1 << 21, (1 << 25) | (1 << 26), 0, SIDE_BLACK)
    set_cursor(g, 21)
    ch4_0 = g.p.memory[NR42]
    tap(g, "A")
    check(cycle_to(g, 30), "the cursor cycles to the crowning square, 30 (%d)"
          % g.var("cursor"))
    tap(g, "A")
    ch4_1 = g.p.memory[NR42]
    blk, wht, kng = board_masks(g)
    check(bool(kng & (1 << 30)),
          "the hop really crowned - the piece on 30 is a king (kings 0x%08X)"
          % kng)
    check(ch4_1 == PROMOTE_ENV and ch4_1 != ch4_0,
          "and CH4's envelope was rewritten for it: NR42 0x%02X -> 0x%02X, "
          "main.c's 0x%02X" % (ch4_0, ch4_1, PROMOTE_ENV))


# ---------------------------------------------------------------- P13 -------
SGB_FLAG = 0x0146               # 0x03 = the cart claims SGB support
OLD_LICENSEE = 0x014B           # 0x33 = "SGB"
# The sizes the border blobs are linked at, from border_data.h.  Used to prove
# the data is in the ROM at FULL length rather than just named in the map.
BORDER_TILES_BYTES = 5920       # border_data_tiles[]
BORDER_PAL_BYTES = 32           # border_data_palettes: 16 palette_color_t
BORDER_MAP_BYTES = 1792         # border_data_map[] (and its attribute alias)
# P15: bank 1, the 0x4000 bank window.  The cart is ROM-only, so bank 1 is
# permanently mapped and main.c's plain pointers read it -- which is why the
# blobs being ABOVE 0x4000 is a placement that can be asserted and not just a
# bank number that a switch would have to select at runtime.
BANK1_BASE = 0x4000
BANK1_END = 0x8000


def parse_border_map(path):
    """`_border_data_tiles` and friends out of the linker map.

    These live in their own translation unit, so parse_map() -- which matches
    `Fmain$...` only -- never sees them.  The area listing gives the symbol and
    the object it came from, which is enough: a module that was NOT linked in
    has no line at all, which is exactly what P12's ROM looks like."""
    out = {}
    for line in open(path):
        m = re.match(r"\s*([0-9A-Fa-f]{8})\s+_border_data_(\w+)\s+border_data\s*$",
                     line)
        if m:
            out[m.group(2)] = int(m.group(1), 16)
    return out


def p13_sgb_border(g):
    """The border data is linked into the ROM, and the ROM declares itself SGB.

    THIS CHECK IS BYTES, NOT PIXELS.  PyBoy shows nothing of an SGB border --
    it emulates a DMG, where sgb_check() is false and every line of this is
    skipped -- and a ROM built without -Wm-ys has its SGB packets silently
    discarded, so mGBA emulates a plain DMG too.  A border can therefore look
    "done" and render nowhere.  That the artwork actually frames the board is
    an EYEBALL, a human in mGBA with SGB emulation on, and it is named as an
    eyeball in the phase evidence.  What is asserted here is the three things
    that can be read:

      * 0x0146 == 0x03, the SGB flag.
      * 0x014B == 0x33, old licensee "SGB".
      * border_data is linked in at its full length.
      * and all three blobs sit in bank 1, at 0x4000 or above (P15).

    The gate is the third: P12's ROM has no border_data module at all, so the
    map parse comes back empty and this exits 1 (measured against a P12-shaped
    map: "FAIL border_data_tiles is linked in (found at nowhere)" x3).

    P15's gate is the fourth, and it is the ADDRESS, never a missing symbol:
    P14's ROM links the same three symbols with the same sizes and the same
    spacing, only in bank 0, so every check above it passes.  Reverting the
    placement -- `#pragma bank 1` out of border_data.c, -Wl-b_CODE_1=0x4000 out
    of LCCFLAGS -- reproduces P14's ROM byte for byte (its release md5 comes
    back, 73a78cf37d7babac102072de24a76657), and the map then reads palettes
    0x2778, tiles 0x2798, map 0x3EB8, so this exits 1 with the three MEASURED
    lines below and not with "symbol absent":

        FAIL  border_data_palettes is in bank 1 (0x2778 < 0x4000)
        FAIL  border_data_tiles is in bank 1 (0x2798 < 0x4000)
        FAIL  border_data_map is in bank 1 (0x3EB8 < 0x4000)

    0x0146 is the MUTATION CONTROL.  -Wm-ys has been in LCCFLAGS since P1, so
    the byte is already 0x03 on P12's ROM and cannot gate anything -- deleting
    -Wm-ys is what makes it fail.  MEASURED: with the flag removed the ROM's
    0x0146 is 0x00.  0x014B does NOT move with it -- it reads 0x33 either way,
    which is the toolchain's own default old-licensee byte, not something
    -Wm-ys writes.  It is asserted because an SGB cartridge should carry it,
    and it is honest about being a companion rather than the control."""
    print("P13 the ROM declares SGB and carries the border data")

    rom = open(g.rom, "rb").read()
    check(rom[SGB_FLAG] == 0x03,
          "the SGB flag is set: ROM[0x0146] = 0x%02X" % rom[SGB_FLAG])
    check(rom[OLD_LICENSEE] == 0x33,
          "the old-licensee byte says SGB: ROM[0x014B] = 0x%02X"
          % rom[OLD_LICENSEE])

    b = parse_border_map(g.mapfile)
    for name in ("tiles", "map", "palettes"):
        check(name in b,
              "border_data_%s is linked in (found at %s)"
              % (name, "0x%04X" % b[name] if name in b else "nowhere"))
    if not all(n in b for n in ("tiles", "map", "palettes")):
        return

    # P15.  The three blobs are in bank 1, not beside the code in bank 0.  The
    # comparison is spelled out rather than implied so that the failure reads
    # as the address it is -- "0x2798 < 0x4000" -- and not as a symbol that a
    # later phase might have renamed.
    for name in ("palettes", "tiles", "map"):
        check(b[name] >= BANK1_BASE,
              "border_data_%s is in bank 1 (0x%04X %s 0x%04X)"
              % (name, b[name], ">=" if b[name] >= BANK1_BASE else "<",
                 BANK1_BASE))

    # Non-empty means at full length: the tile blob runs right up to the map
    # blob in the map's own listing, and a truncated or stubbed array would
    # leave a gap that is not 5920.
    check(b["map"] - b["tiles"] == BORDER_TILES_BYTES,
          "the tile blob is all %d bytes on the ROM (tiles 0x%04X -> map "
          "0x%04X)" % (BORDER_TILES_BYTES, b["tiles"], b["map"]))
    check(b["tiles"] - b["palettes"] == BORDER_PAL_BYTES,
          "the palette blob is all %d bytes (palettes 0x%04X -> tiles 0x%04X)"
          % (BORDER_PAL_BYTES, b["palettes"], b["tiles"]))
    # Stronger than "inside the ROM": the payload ends below 0x8000, so nothing
    # spills past the 32 KB a ROM-only cart maps, and the ROM is still the
    # 32768 bytes such an image pads to.  Deliberately says nothing about WHICH
    # bank -- on P14's ROM this one passes with the blobs at 0x3EB8, exactly as
    # it should, and the address checks above are the ones carrying the claim.
    check(b["map"] + BORDER_MAP_BYTES <= BANK1_END and len(rom) == 0x8000,
          "nothing spills past 0x%04X, and the ROM is still 32768 bytes "
          "(map ends 0x%04X, ROM %d bytes)"
          % (BANK1_END, b["map"] + BORDER_MAP_BYTES, len(rom)))


# ---------------------------------------------------------------- P14 -------
def p14_one_player_default(g):
    """The title opens on 1 PLAYER, and SELECT still flips it both ways.

    ONE INITIALISER IS THE WHOLE GAME-SIDE CHANGE (main.c: `mode_1p = 1u`), so
    there is nothing here to compute and no rules to re-derive - the check is
    that the title a player is actually shown, on a boot that nobody has
    touched, is the 1-Player one.

    BOOTED RAW - `g.run(150)`, exactly as p10 does - and NOT through
    Game.start().  P14 taught start() to force MODE_2P so the eleven checks
    that come through it keep playing two players; calling it here would force
    the very mode this check exists to observe and it would fail on this
    phase's own ROM.  That is the trap this comment is guarding.

    The rendered line is read as well as the variable: the title is what a
    player sees, and mode_1p alone cannot tell "the default changed" from "the
    default changed and the screen behind it did not".

    POWER GATE: on a build with the initialiser reverted this exits 1 with
    `mode_1p` reading 0 and the title reading "2 PLAYERS" - measured, not
    assumed; the message below is what the gate log shows."""
    print("P14 1 PLAYER is the boot default")
    g.run(150)          # the Game constructor ticks nothing - see p1
    if not g.need("mode_1p"):
        return

    check(g.var("mode_1p") == MODE_1P,
          "a boot nobody touched is in 1-player mode (mode_1p %d, wanted %d)"
          % (g.var("mode_1p"), MODE_1P))
    check(g.find_text("1 PLAYER") != [] and g.find_text("2 PLAYERS") == [],
          "and the title says so - 1 PLAYER, and not 2 PLAYERS (%r / %r)"
          % (g.find_text("1 PLAYER"), g.find_text("2 PLAYERS")))

    tap(g, "T")
    check(g.var("mode_1p") == MODE_2P and g.find_text("2 PLAYERS") != []
          and g.find_text("1 PLAYER") == [],
          "SELECT still flips it to 2 PLAYERS, and redraws for it (%d, %r)"
          % (g.var("mode_1p"), g.find_text("2 PLAYERS")))
    tap(g, "T")
    check(g.var("mode_1p") == MODE_1P and g.find_text("1 PLAYER") != []
          and g.find_text("2 PLAYERS") == [],
          "and back to 1 PLAYER (%d, %r)"
          % (g.var("mode_1p"), g.find_text("1 PLAYER")))


CHECKS = [("p1_boot_title_board", p1_boot_title_board),
          ("p2_start_position", p2_start_position),
          ("p3_select_and_highlight", p3_select_and_highlight),
          ("p4_move_applied", p4_move_applied),
          ("p4_mandatory_capture", p4_mandatory_capture),
          ("p5_chain_continues", p5_chain_continues),
          ("p5_promotion_stops_chain", p5_promotion_stops_chain),
          ("p6_win_and_restart", p6_win_and_restart),
          ("p7_responsiveness", p7_responsiveness),
          ("p8_one_step_per_tap", p8_one_step_per_tap),
          ("p10_ai_opponent", p10_ai_opponent),
          ("p11_think_budget", p11_think_budget),
          ("p12_sfx", p12_sfx),
          ("p13_sgb_border", p13_sgb_border),
          ("p14_one_player_default", p14_one_player_default)]


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
