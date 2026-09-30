/* GB-DRAUGHTS - English draughts for the Game Boy (GBDK-2020).
 *
 * The rules engine lives in sim.h and is unit tested on the host; this file
 * owns everything that touches hardware.  P1 is boot + title + the empty board
 * grid, so nothing here needs sim.h yet - P2 adds it, along with the pieces.
 *
 * Controls: START = leave the title screen.
 */
#include <gb/gb.h>
#include <stdint.h>
#include "gfx.h"

/* ---------------------------------------------------------------- layout
 * From PLAN.md's data contract: the board is 8x8 cells of 2x2 tiles, at tiles
 * (0,0)-(15,15); a cell (row, col) is playable (dark) when (row + col) is odd.
 * Text goes inside the same 16x16 region - the states are exclusive, and one
 * blit of that region is what the whole screen is ever made of. */
#define BOARD_TW  16u
#define BOARD_TH  16u

#define TITLE_ROW  6u
#define PROMPT_ROW 10u
#define TEXT_COL   4u    /* both strings are 11 tiles wide: (20 - 11) / 2 */

typedef enum { ST_TITLE, ST_PLAY } State;

/* ----------------------------------------------------------------- state
 * Only the state machine: the screen has no state of its own, it is rebuilt
 * from scratch every frame (P2 draws the pieces the same way). */
static State   state;
static uint8_t keys, pressed, prev_keys;

/* The frame's whole tilemap, built then blitted in one write inside VBlank. */
static uint8_t mapbuf[BOARD_TW * BOARD_TH];

/* ------------------------------------------------------------------ draw */
static uint8_t glyph(char c)
{
    if (c >= 'A' && c <= 'Z') return (uint8_t)(T_LETTER0 + (c - 'A'));
    if (c >= '0' && c <= '9') return (uint8_t)(T_DIGIT0 + (c - '0'));
    return T_BLANK;                 /* space, and anything unmapped */
}

static void text(uint8_t col, uint8_t row, const char *s)
{
    uint8_t *p = &mapbuf[row * BOARD_TW + col];
    while (*s) *p++ = glyph(*s++);
}

static void build_title(void)
{
    uint16_t i;
    for (i = 0; i < BOARD_TW * BOARD_TH; i++) mapbuf[i] = T_BLANK;
    text(TEXT_COL, TITLE_ROW, "GB DRAUGHTS");
    text(TEXT_COL, PROMPT_ROW, "PRESS START");
}

static void build_board(void)
{
    uint8_t tx, ty;
    for (ty = 0; ty < BOARD_TH; ty++) {
        for (tx = 0; tx < BOARD_TW; tx++) {
            mapbuf[ty * BOARD_TW + tx] =
                ((ty >> 1) + (tx >> 1)) & 1 ? T_DARK : T_BLANK;
        }
    }
}

/* ------------------------------------------------------------------ main */
void main(void)
{
    DISPLAY_OFF;
    /* BG tiles and OBJ tiles are different VRAM, so the cursor is its own
     * little array rather than a slice of the BG bank. */
    set_bkg_data(0, GFX_TILE_COUNT, gfx_tiles);
    set_sprite_data(0, GFX_SPRITE_COUNT, gfx_sprites);
    BGP_REG  = 0xE4;                /* 3 2 1 0, darkest to lightest */
    OBP0_REG = 0xE4;
    SPRITES_8x8;
    SHOW_BKG;
    SHOW_SPRITES;                   /* nothing positioned: the cursor sprite
                                     * stays parked off-screen until P3 */
    DISPLAY_ON;

    state = ST_TITLE;
    prev_keys = 0;

    while (1) {
        keys = joypad();
        pressed = keys & ~prev_keys;
        prev_keys = keys;

        if (state == ST_TITLE && (pressed & J_START)) state = ST_PLAY;

        /* Build the frame first, blit it second: the VRAM writes then land in
         * the VBlank window instead of being dropped by the PPU. */
        if (state == ST_TITLE) build_title(); else build_board();
        wait_vbl_done();
        set_bkg_tiles(0, 0, BOARD_TW, BOARD_TH, mapbuf);
    }
}
