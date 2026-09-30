/* GB-DRAUGHTS - English draughts for the Game Boy (GBDK-2020).
 *
 * The rules engine lives in sim.h and is unit tested on the host; this file
 * owns everything that touches hardware.  P2: a real Board, the start position
 * on it, and a renderer that reads that Board square by square.
 *
 * Controls: START = new game, leaving the title screen.
 */
#include <gb/gb.h>
#include <stdint.h>
#include "gfx.h"
#include "sim.h"

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
 * The state machine and the rules engine's board.  The screen has no state of
 * its own: draw_pieces() rebuilds every square from `board` every frame, so
 * there is nothing to keep in sync and nothing for a mid-chain capture to
 * leave stale. */
static State   state;
static uint8_t keys, pressed, prev_keys;
static Board   board;

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

/* Every square, every frame, straight out of `board` - no cache.  The engine
 * is the only state and the screen is a function of it, so a rule change (a
 * mandatory capture, a mid-chain hop) can never leave a stale piece behind. */
static void draw_pieces(void)
{
    uint8_t sq;
    for (sq = 0; sq < N_SQ; sq++) {
        uint8_t who = sq_occupant(&board, sq);
        uint8_t kind, row, col, dx, dy;

        if (!who) continue;

        /* T_PIECE's kind order is {black, white} x {man, king}. */
        kind = (uint8_t)((who - 1u) + (sq_is_king(&board, sq) ? 2u : 0u));
        row  = SQ_ROW(sq);
        col  = SQ_COL(sq);

        for (dy = 0; dy < 2; dy++) {
            for (dx = 0; dx < 2; dx++) {
                mapbuf[(2u * row + dy) * BOARD_TW + (2u * col + dx)] =
                    T_PIECE(kind, dx, dy);
            }
        }
    }
}

static void new_game(void)
{
    board = board_init();
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

        if (state == ST_TITLE && (pressed & J_START)) {
            new_game();
            state = ST_PLAY;
        }

        /* Build the frame first, blit it second: the VRAM writes then land in
         * the VBlank window instead of being dropped by the PPU. */
        if (state == ST_TITLE) {
            build_title();
        } else {
            build_board();
            draw_pieces();
        }
        wait_vbl_done();
        set_bkg_tiles(0, 0, BOARD_TW, BOARD_TH, mapbuf);
    }
}
