# GB-Draughts build.
#   make          build draughts.gb   (GBDK if GBDK_HOME is set, else Docker)
#   make test     host unit tests for sim.h (plain gcc, no emulator)
#   make geom     regenerate board_geom.h from boardgen.py
#   make gfx      regenerate gfx.h from mkgfx.py
#   make border   regenerate border_data.c from art/border_sgb.png (SGB border)
#   make sym      -debug build: draughts.map carries every symbol, not just globals
#   make probe    headless PyBoy checks (implies sym; see .claude/PLAN.md)
#   make usage    ROM/RAM headroom
#   make image    build the gbdk-dev Docker image used when GBDK_HOME is absent
#   make clean

ROM    = draughts.gb
CFILES = main.c sgb_border.c border_data.c
SRCS   = $(CFILES) sim.h board_geom.h gfx.h sgb_border.h border_data.h

ifneq ($(wildcard $(GBDK_HOME)/bin/lcc),)
  RUN   :=
  LCC   := $(GBDK_HOME)/bin/lcc
  USAGE := $(GBDK_HOME)/bin/romusage
  P2A   := $(GBDK_HOME)/bin/png2asset
else
  # GBDK is not installed on this host, so run the toolchain out of the image.
  # lcc must be the FULL PATH: it is not on PATH inside gbdk-dev, and a bare
  # `lcc` fails with "executable file not found".
  # -u keeps build artefacts owned by the user rather than root.
  RUN   := docker run --rm -u $(shell id -u):$(shell id -g) -v "$(CURDIR)":/work -w /work gbdk-dev
  LCC   := /opt/gbdk/bin/lcc
  USAGE := /opt/gbdk/bin/romusage
  P2A   := /opt/gbdk/bin/png2asset
endif

# PyBoy lives in a venv, not on PATH (and not in this repo). The tiers matter:
# the third one silently gives you a python3 with no pyboy, which fails at
# `import pyboy` rather than at anything informative.
ifneq ($(wildcard .venv/bin/python),)
  PY ?= .venv/bin/python
else ifneq ($(wildcard ../GB-Protector/.venv/bin/python),)
  PY ?= ../GB-Protector/.venv/bin/python
else
  PY ?= python3
endif

# -Wl-b_CODE_1=0x4000 defines a second code area, bank 1, at the 0x4000 bank
# window.  The cart is ROM-only (no -Wl-yt), so bank 1 is permanently mapped
# there and a plain 16-bit pointer reads it: no SWITCH_ROM, no BANKED, no MBC.
# border_data.c carries `#pragma bank 1`, which is what puts its arrays in
# _CODE_1 rather than _CODE.  That pragma is what defines the area -- take it
# out and the linker warns "No definition of area _CODE_1".
LCCFLAGS = -Wm-ys -Wm-yn"DRAUGHTS" -Wl-m -Wl-j -Wl-b_CODE_1=0x4000

all: $(ROM)

# The linker always writes a .map, but a release build's map holds globals only
# -- no Fmain$ statics. Left in place it makes a later probe.py report every
# static as MISSING, which reads exactly like a broken phase. Delete it, so
# probing a release ROM fails loudly instead. `sym` writes the real one.
$(ROM): $(SRCS)
	$(RUN) $(LCC) $(LCCFLAGS) -o $@ $(CFILES)
	rm -f $(ROM:.gb=.map)

# -debug so the linker map lists statics too, which is what probe.py reads.
# NOTE: this overwrites $(ROM) with the debug build, so record the release md5
# (`make`) BEFORE running this, not after.
sym: $(SRCS)
	$(RUN) $(LCC) $(LCCFLAGS) -debug -o $(ROM) $(CFILES)

probe: sym
	$(PY) tools/probe.py $(ROM) $(ROM:.gb=.map)

# Screenshot, for the things the tilemap cannot show. Writes to /tmp: anything
# left in the repo root is an untracked file, and an untracked file outside a
# phase's allowlist fails that phase.
FRAMES ?= 120
SCRIPT ?=
shot: $(ROM)
	$(PY) tools/shot.py $(ROM) /tmp/$(ROM:.gb=.png) $(FRAMES) "$(SCRIPT)"

test: tests/test_sim
	./tests/test_sim

tests/test_sim: tests/test_sim.c sim.h board_geom.h
	gcc -std=c99 -Wall -Wextra -o $@ tests/test_sim.c

geom:
	python3 boardgen.py

gfx:
	python3 mkgfx.py

# Super Game Boy border packing (slot 0 reserved, limits checked) comes from
# https://github.com/Zapskii/GB-SGB-Tooling, checked out beside this repo.
SGB_TOOLING ?= ../GB-SGB-Tooling

# The Super Game Boy border: art/border_sgb.png (256x224; the 160x144 game area
# at x=48,y=40 is transparent) -> border_data.c/.h, committed like gfx.h so a
# plain `make` needs no Python.  The PNG is itself generated -- tools/mkborder.py
# draws it, the way mkgfx.py draws the tiles -- so an art change is that script
# plus this recipe, in that order.  -pack_mode sgb is what gets the SGB layout --
# 4bpp tiles, a 256x224 map, one attribute byte per cell -- instead of a GB
# screen; -use_map_attributes keeps that byte, which is the per-cell palette.
# -b 1 is what makes png2asset write `#pragma bank 1` at the top of the output;
# without it the blobs land back in bank 0 and the probe's p13 address check
# fails.  Regenerating is therefore safe -- the pragma comes from here, not
# from hand-editing the generated file.
border:
	python3 $(SGB_TOOLING)/mkborder.py .border_packed.png --art art/border_sgb.png
	$(RUN) $(P2A) .border_packed.png -map -bpp 4 -max_palettes 3 -keep_palette_order \
	      -pack_mode sgb -use_map_attributes -b 1 -c border_data.c
	rm -f .border_packed.png

# romusage reads the .map, which only `sym` writes -- so it depends on sym, not
# on $(ROM).
usage: sym
	$(RUN) $(USAGE) $(ROM:.gb=.map) -g

image:
	docker build -t gbdk-dev .

clean:
	rm -f $(ROM) *.map *.noi *.o *.lst *.sym *.ihx *.asm *.adb tests/test_sim

.PHONY: all test geom gfx border sym probe shot usage image clean
