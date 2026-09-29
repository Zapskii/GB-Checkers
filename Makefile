# GB-Draughts build.
#   make          build draughts.gb   (GBDK if GBDK_HOME is set, else Docker)
#   make test     host unit tests for sim.h (plain gcc, no emulator)
#   make geom     regenerate board_geom.h from boardgen.py
#   make usage    ROM/RAM headroom
#   make image    build the gbdk-dev Docker image used when GBDK_HOME is absent
#   make clean
#
# main.c (the actual game/rendering) doesn't exist yet - this is the rules
# engine stage. `make test` is the target that matters right now.

ROM  = draughts.gb
SRCS = sim.h board_geom.h

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

LCCFLAGS = -Wm-ys -Wm-yn"DRAUGHTS" -Wl-m -Wl-j

all: $(ROM)

$(ROM): main.c $(SRCS)
	$(RUN) $(LCC) $(LCCFLAGS) -o $@ main.c

test: tests/test_sim
	./tests/test_sim

tests/test_sim: tests/test_sim.c sim.h board_geom.h
	gcc -std=c99 -Wall -Wextra -o $@ tests/test_sim.c

geom:
	python3 boardgen.py

usage: $(ROM)
	$(RUN) $(USAGE) $(ROM:.gb=.map) -g

image:
	docker build -t gbdk-dev .

clean:
	rm -f $(ROM) *.map *.noi *.o *.lst *.sym *.ihx *.asm *.adb tests/test_sim

.PHONY: all test geom usage image clean
