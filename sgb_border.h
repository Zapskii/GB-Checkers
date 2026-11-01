#ifndef __SGB_BORDER_H_INCLUDE
#define __SGB_BORDER_H_INCLUDE

#include <stddef.h>

/** Installs the Super Game Boy border.

    Gated on @ref sgb_check(), so on a DMG this does nothing at all and the
    call costs one probe. PyBoy does answer that probe yes -- its MLT_REQ
    emulation perturbs P1 enough to satisfy GBDK's handshake -- which is what
    lets sgb_border_test.py watch the transfer in the emulator.

    The display must be turned on before calling this function
    (with @ref DISPLAY_ON).

    It costs VRAM: the transfer works by rendering the payload on the Game Boy
    screen and letting the SGB read it back, so afterwards the tile bank and
    the background map are full of border and have to be reloaded.

    When using the SGB with a PAL SNES, a delay should be added
    just after program startup such as:

    \code{.c}
    // Wait 4 frames
    // For PAL SNES this delay is required on startup
    for (uint8_t i = 4; i != 0; i--) vsync();
    \endcode
*/
void set_sgb_border(const unsigned char *tiledata, size_t tiledata_size,
                    const unsigned char *tilemap, size_t tilemap_size,
                    const unsigned char *palette, size_t palette_size);

#endif
