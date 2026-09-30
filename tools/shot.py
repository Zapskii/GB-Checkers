#!/usr/bin/env python3
"""Headless screenshot + scripted input for GB DRAUGHTS.

WHY THIS EXISTS: mGBA is the emulator you play the game in, but it cannot be
driven from a script on this machine -- it has no screenshot flag, and macOS
denies both window introspection (Terminal lacks assistive access) and screen
capture (no screen-recording permission). So the dev loop runs PyBoy headless
instead: no window, no permissions, and it can press buttons, which means a build
can be checked without a human watching it.

This is a DEV TOOL. It is not part of the ROM and nothing in the build depends
on it.

    tools/shot.py draughts.gb /tmp/out.png [frames] [script]

`script` is one character per frame, so input can be scripted across a run:
    .       no input          R/L/U/D   d-pad
    A B     A / B             S T       START / SELECT
e.g. the 120th frame of "flying right while holding fire" is
    tools/shot.py draughts.gb /tmp/out.png 120 ...(117 dots)...RRA
"""
import sys

from pyboy import PyBoy

KEYS = {"R": "right", "L": "left", "U": "up", "D": "down",
        "A": "a", "B": "b", "S": "start", "T": "select"}


def main():
    rom, out = sys.argv[1], sys.argv[2]
    frames = int(sys.argv[3]) if len(sys.argv) > 3 else 120
    script = sys.argv[4] if len(sys.argv) > 4 else ""

    pyboy = PyBoy(rom, window="null", sound_emulated=False)
    for i in range(frames):
        ch = script[i] if i < len(script) else "."
        key = KEYS.get(ch)
        if key:
            pyboy.button_press(key)
        pyboy.tick(1, True)
        if key:
            pyboy.button_release(key)

    pyboy.screen.image.save(out)
    pyboy.stop(save=False)
    print("wrote %s after %d frames" % (out, frames))


if __name__ == "__main__":
    main()
