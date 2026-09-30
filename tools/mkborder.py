#!/usr/bin/env python3
"""Generate art/border_sgb.png -- the Super Game Boy border for GB-DRAUGHTS.

The border is a 256x224 image; the 160x144 game window (x 48..207, y 40..183)
stays transparent, because that is where the SGB puts the Game Boy's screen.
`make border` then turns the PNG into border_data.c with png2asset.

Modelled on ../GB-Puzzle-Balloon/tools/mkborder.py, which is the family's
reference for this: drawn rather than hand-painted, lettering taken from the
game's OWN font so the border is the same hand as the title screen, colours
capped at 16 so the whole thing packs into one SGB palette.

Two differences from that one.  The font is parsed out of mkgfx.py rather than
main.c (this game's glyphs are tile art, not a C table), and the motif is
draughts: a checkerboard bezel around the screen, with the game's own disc --
outline, body, groove ring, and a crown for kings -- as the ornament.

Everything is placed on an 8-pixel grid on purpose.  The discs then repeat
themselves mod 8 and share tile patterns, which is what keeps a 256-tile
budget comfortable; discs off the grid are all subtly different tiles.

    tools/mkborder.py && make border && make
"""
import re

from PIL import Image, ImageDraw

OUT_PNG = "art/border_sgb.png"
MKGFX = "mkgfx.py"

W, H = 256, 224
WIN = (48, 40, 208, 184)                # game window: x0, y0, x1, y1 (exclusive)

# The palette, capped at 16 because that is what one SGB border palette holds
# (four sub-palettes of four, chosen per 8x8 tile).  Baize for the surround, the
# board's own two square colours for the bezel, and the pieces' two colours.
BAIZE = (18, 62, 42, 255)               # dark green table
BAIZE_L = (30, 88, 58, 255)             # ...lighter, for the vignette
SQ_LIGHT = (232, 214, 168, 255)         # the board's light square
SQ_DARK = (120, 74, 42, 255)            # ...and its dark one, as wood
EDGE = (8, 12, 10, 255)                 # outlines: squares, bezel, discs
BEZEL_IN = (10, 22, 16, 255)            # inner shadow inside the bezel

DISC_DARK = (26, 26, 30, 255)           # a black man's core
DISC_DARK_R = (78, 78, 88, 255)         # ...its groove ring
DISC_LIGHT = (238, 234, 224, 255)       # a white man's core
DISC_LIGHT_R = (168, 150, 124, 255)     # ...its groove ring
CROWN = (222, 176, 64, 255)             # the king's cross, and the title

TITLE = CROWN
SUBTLE = (150, 214, 168, 255)           # byline: reads as a caption, not a title

R = 12                                  # disc radius: 24px inside a 32px cell


def read_font():
    """The game's own 5x7 glyphs, straight out of mkgfx.py.

    FONT maps a character to seven 5-bit rows.  Parsing rather than copying
    means the border says 'DRAUGHTS' and 'CHECKERS' in exactly the letters the
    board's status row does, and follows if the font ever grows.
    """
    src = open(MKGFX).read()
    body = re.search(r'^FONT = \{(.*?)^\}', src, re.S | re.M).group(1)
    return {ch: rows.split("|")
            for ch, rows in re.findall(r"'(\w)': \"([^\"]*)\"", body)}


def draw_text(d, font, x, y, s, scale, col):
    for ch in s:
        if ch != " ":
            for r, row in enumerate(font[ch]):
                for c, bit in enumerate(row):
                    if bit == "#":
                        d.rectangle([x + c * scale, y + r * scale,
                                     x + c * scale + scale - 1,
                                     y + r * scale + scale - 1], fill=col)
        x += 6 * scale                  # 5px ink + 1px gap


def disc(d, cx, cy, light, king):
    """One draught, shaded the way the game's tiles are: an outline, a body,
    a groove ring cut into it, and -- for a king -- the crown's cross.

    The crown takes the outline colour on a white man and gold on a black one:
    gold on ivory is nearly invisible, which is exactly how the first draft of
    this looked.  Same cross the board draws, `piece_rows`' CROWN in mkgfx.py.
    """
    body = DISC_LIGHT if light else DISC_DARK
    ring = DISC_LIGHT_R if light else DISC_DARK_R
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=EDGE)
    d.ellipse([cx - R + 1, cy - R + 1, cx + R - 1, cy + R - 1], fill=body)
    d.ellipse([cx - R + 4, cy - R + 4, cx + R - 4, cy + R - 4], outline=ring)
    if king:
        ink = EDGE if light else CROWN
        d.rectangle([cx - 1, cy - 7, cx + 1, cy + 7], fill=ink)
        d.rectangle([cx - 7, cy - 1, cx + 7, cy + 1], fill=ink)


def main():
    font = read_font()
    im = Image.new("RGBA", (W, H), BAIZE)
    d = ImageDraw.Draw(im)

    # A soft vignette so the surround is not one flat slab of green.
    d.rectangle([WIN[0] - 24, WIN[1] - 24, WIN[2] + 23, WIN[3] + 23], fill=BAIZE_L)
    d.rectangle([WIN[0] - 16, WIN[1] - 16, WIN[2] + 15, WIN[3] + 15], fill=BAIZE)

    # The checkerboard bezel: one 8px square all the way round the window, so
    # the screen is framed as the board's edge rather than sitting in a box.
    for i, x in enumerate(range(40, 216, 8)):
        col = SQ_LIGHT if i % 2 == 0 else SQ_DARK
        d.rectangle([x, 32, x + 7, 39], fill=col)
        col = SQ_LIGHT if i % 2 else SQ_DARK
        d.rectangle([x, 184, x + 7, 191], fill=col)
    for i, y in enumerate(range(40, 184, 8)):
        col = SQ_LIGHT if i % 2 else SQ_DARK
        d.rectangle([40, y, 47, y + 7], fill=col)
        d.rectangle([208, y, 215, y + 7], fill=col)
    d.rectangle([39, 31, 216, 192], outline=EDGE)
    d.rectangle([WIN[0] - 1, WIN[1] - 1, WIN[2], WIN[3]], outline=BEZEL_IN)

    # The pieces, on a 32px pitch: a man at each end of the side columns, and a
    # crowned king at every corner.  No bottom row of men -- the wordmark has
    # that strip, since there are only 32px of border under the window.
    n = 0
    for cx in (20, 236):
        for cy in (20, 204):
            disc(d, cx, cy, n % 2 == 0, True)
            n += 1
        for cy in range(52, 181, 32):
            disc(d, cx, cy, n % 2 == 0, False)
            n += 1

    # "DRAUGHTS" at 2x, centred, with a rule and the byline under it: the
    # title is 2x and the credit 1x, so the name reads as a caption rather than
    # as a second title.  The game's other name sits at the bottom at the same
    # weight, so the two edges balance.
    text = "DRAUGHTS"
    scale = 2
    tw = len(text) * 6 * scale - scale
    tx = (W - tw) // 2
    draw_text(d, font, tx, 4, text, scale, TITLE)
    d.rectangle([tx, 20, tx + tw - 1, 20], fill=SUBTLE)

    credit = "BY ZAPSKI"
    cw = len(credit) * 6 - 1
    draw_text(d, font, (W - cw) // 2, 24, credit, 1, SUBTLE)

    other = "CHECKERS"
    ow = len(other) * 6 * scale - scale
    draw_text(d, font, (W - ow) // 2, 200, other, scale, TITLE)

    # The game window: the SGB shows the GB screen here, so it stays empty.
    d.rectangle([WIN[0], WIN[1], WIN[2] - 1, WIN[3] - 1], fill=(0, 0, 0, 0))

    im.save(OUT_PNG)
    colours = {c for _, c in im.getcolors(1 << 16)}
    print("%s: %dx%d, %d colours (limit 16)" % (OUT_PNG, W, H, len(colours)))
    assert len(colours) <= 16, "one SGB border palette holds 16 colours"
    # The window is the hole the SGB shows the Game Boy's screen through, and it
    # is the ONLY transparent part: any other transparent pixel is a hole in the
    # border, whatever happens to be behind it.  So the clear pixels must count
    # out to the window exactly.
    clear = sum(n for n, c in im.getcolors(1 << 16) if c[3] == 0)
    want = (WIN[2] - WIN[0]) * (WIN[3] - WIN[1])
    assert clear == want, "%d transparent pixels, window is %d" % (clear, want)

    # Easy to think this script is the whole job: it only writes the PNG, and
    # `make` alone will not pick it up (border_data.c is generated, and only by
    # `make border`).  Skip the second step and the ROM keeps the old border,
    # which looks exactly like the change not working.
    print("next: make border && make   # PNG -> border_data.c -> ROM")


if __name__ == "__main__":
    main()
