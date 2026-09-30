#!/usr/bin/env python3
"""Frame-rate check for GB DRAUGHTS.

The game must update once per vblank (60 fps).  main.c's loop does one
wait_vbl_done() per iteration and bumps `frame` once per iteration, so a build
whose build+blit no longer fits in a vblank slips frames instead of dropping
input -- it looks alive and plays at half speed, and neither make test nor make
probe can see it (every state those two read is still correct).  This reads the
game's own frame counter out of RAM each emulated frame: 1.0 frames/tick is a
green tree, 0.5 means every other frame slips.

    make sym && tools/fps.py draughts.gb draughts.map

Run it on the TITLE screen, which is where 700 idle frames of it land: a
machine think (1 PLAYER) is a blocking call and stalls `frame` on purpose, so a
rate measured across one is the think's length, not the loop's.

DEV TOOL.  Needs the -debug map, hence the `sym` dependency.
"""
import re
import sys

from pyboy import PyBoy


def parse_frame_addr(path):
    for line in open(path):
        m = re.match(r"\s*([0-9A-Fa-f]{8})\s+Fmain\$frame\$", line)
        if m:
            return int(m.group(1), 16)
    sys.exit("fps: `frame` missing from %s -- run `make sym`" % path)


rom, mapfile = sys.argv[1], sys.argv[2]
addr = parse_frame_addr(mapfile)
p = PyBoy(rom, window="null", sound_emulated=False)

prev, incs, ticks = None, 0, 0
for t in range(700):                        # ~12 s: the title, then some play
    p.tick(1, False)
    f = p.memory[addr]
    if t >= 120:                            # let the title settle
        if prev is not None:
            incs += (f - prev) & 255
        ticks += 1
    prev = f

rate = incs / ticks
print("%.3f frames/tick => ~%.0f fps" % (rate, rate * 60))
if rate < 0.98:
    sys.exit("fps: frame budget blown -- update+render no longer fits in vblank")
