# GB-Draughts build.
#   make          build draughts.gb   (GBDK if GBDK_HOME is set, else Docker)
#   make test     host unit tests for sim.h (plain gcc, no emulator)
#   make geom     regenerate board_geom.h from boardgen.py
#   make gfx      regenerate gfx.h from mkgfx.py
#   make sym      -debug build: draughts.map carries every symbol, not just globals
#   make probe    headless PyBoy checks (implies sym; see .claude/PLAN.md)
#   make usage    ROM/RAM headroom
#   make image    build the gbdk-dev Docker image used when GBDK_HOME is absent
#   make clean

ROM    = draughts.gb
CFILES = main.c
SRCS   = $(CFILES) sim.h board_geom.h gfx.h

ifneq ($(wildcard $(GBDK_HOME)/bin/lcc),)
  RUN   :=
  LCC   := $(GBDK_HOME)/bin/lcc
  USAGE := $(GBDK_HOME)/bin/romusage
else
  # GBDK is not installed on this host, so run the toolchain out of the image.
  # lcc must be the FULL PATH: it is not on PATH inside gbdk-dev, and a bare
  # `lcc` fails with "executable file not found".
  # -u keeps build artefacts owned by the user rather than root.
  RUN   := docker run --rm -u $(shell id -u):$(shell id -g) -v "$(CURDIR)":/work -w /work gbdk-dev
  LCC   := /opt/gbdk/bin/lcc
  USAGE := /opt/gbdk/bin/romusage
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

LCCFLAGS = -Wm-ys -Wm-yn"DRAUGHTS" -Wl-m -Wl-j

all: $(ROM)

$(ROM): $(SRCS)
	$(RUN) $(LCC) $(LCCFLAGS) -o $@ $(CFILES)

# -debug so the linker map lists statics too, which is what probe.py reads.
# NOTE: this overwrites $(ROM) with the debug build, so record the release md5
# (`make`) BEFORE running this, not after.
sym: $(SRCS)
	$(RUN) $(LCC) $(LCCFLAGS) -debug -o $(ROM) $(CFILES)

probe: sym
	$(PY) tools/probe.py $(ROM) $(ROM:.gb=.map)

test: tests/test_sim
	./tests/test_sim

tests/test_sim: tests/test_sim.c sim.h board_geom.h
	gcc -std=c99 -Wall -Wextra -o $@ tests/test_sim.c

geom:
	python3 boardgen.py

gfx:
	python3 mkgfx.py

usage: $(ROM)
	$(RUN) $(USAGE) $(ROM:.gb=.map) -g

image:
	docker build -t gbdk-dev .

clean:
	rm -f $(ROM) *.map *.noi *.o *.lst *.sym *.ihx *.asm *.adb tests/test_sim

.PHONY: all test geom gfx sym probe usage image clean
