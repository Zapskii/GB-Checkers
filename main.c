/* GB-DRAUGHTS - English draughts for the Game Boy (GBDK-2020).
 *
 * The rules engine lives in sim.h and is unit tested on the host; this file
 * owns everything that touches hardware.  P3: a cursor, a two-phase select (walk
 * to a piece, A picks it up, its destinations light up) and B to back out.
 * Nothing moves yet - A on a destination is P4.
 *
 * Controls: START = new game, d-pad = move the cursor, A = select, B = deselect.
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

/* The cursor walks square to square along NEIGH, so it can step over squares
 * that cannot be picked up right now - with mandatory capture most of them
 * cannot - and WALK_STEPS caps how far one press may travel.  A held direction
 * steps again every REPEAT_DIV frames. */
#define WALK_STEPS 4u
#define REPEAT_DIV 8u

typedef enum { ST_TITLE, ST_SELECT, ST_TARGET } State;

/* ----------------------------------------------------------------- state
 * The state machine and the rules engine's board.  The screen has no state of
 * its own: every frame rebuilds it from `board` and from the move list below,
 * so there is nothing to keep in sync and nothing a stale cache can leave
 * behind. */
static State   state;
static uint8_t keys, pressed, prev_keys, frame;
static Board   board;

/* The moves on offer right now, rebuilt from `board` EVERY frame and never
 * carried over from the last one.  With nothing picked up this is every legal
 * move for the side to move, which is what decides whose piece may be picked
 * up at all; with a piece picked up it is narrowed to that piece's moves, which
 * is what gets highlighted.  gen_moves() folds in mandatory capture, so both
 * forms are the legal set and never "the squares holding my men". */
static Move    moves[MAX_MOVES];
static uint8_t n_moves;

static uint8_t sel;       /* the picked-up square, NO_SQ when none */
static uint8_t cursor;    /* the square the cursor sprite sits on */

/* The frame's whole tilemap, built then blitted in one write inside VBlank. */
static uint8_t mapbuf[BOARD_TW * BOARD_TH];

/* ------------------------------------------------------------------ rules
 * Nothing below touches hardware: it is board -> move list -> selection, which
 * is the part the emulator probe can hold still and assert on. */
static void compute_moves(void)
{
    uint8_t i, n = 0;

    /* P3 is one-sided: black is always the side to move.  P4 replaces the
     * constant with a real side counter. */
    n_moves = gen_moves(&board, SIDE_BLACK, moves);

    if (sel == NO_SQ) return;                 /* nothing picked up: keep all */
    for (i = 0; i < n_moves; i++) {
        if (moves[i].from == sel) moves[n++] = moves[i];
    }
    n_moves = n;
}

/* Membership of the move list, and *not* "is this a square holding one of my
 * men".  That distinction is the whole point of the phase: under mandatory
 * capture a piece of the side to move that cannot capture has no moves, so it
 * must not be pickable either.  Deriving this from the legal set is what keeps
 * the two in step. */
static uint8_t selectable(uint8_t sq)
{
    uint8_t i;
    for (i = 0; i < n_moves; i++) {
        if (moves[i].from == sq) return 1;
    }
    return 0;
}

/* One d-pad step: hop along NEIGH in `dir` until a square that can be picked
 * up, at most WALK_STEPS hops.  Squares that cannot be picked up are stepped
 * *over* rather than landed on, and running out of board or of steps leaves the
 * cursor exactly where it was - so the cursor is never on a square it could not
 * have been put on deliberately. */
static uint8_t walk(uint8_t from, uint8_t dir)
{
    uint8_t sq = from, i;

    for (i = 0; i < WALK_STEPS; i++) {
        sq = NEIGH[sq][dir];
        if (sq == NO_SQ) break;               /* off the board: stay put */
        if (selectable(sq)) return sq;
    }
    return from;
}

/* U/D choose their diagonal by the vertical half and L/R by the horizontal
 * one: the board only joins squares diagonally, so every press has to pick a
 * diagonal, and these are the four.  A cell is never stepped into off-board
 * because NEIGH says NO_SQ there - this file does no row/col arithmetic. */
static uint8_t dir_of(uint8_t k)
{
    if (k & J_UP)    return DIR_NW;
    if (k & J_DOWN)  return DIR_SE;
    if (k & J_LEFT)  return DIR_SW;
    if (k & J_RIGHT) return DIR_NE;
    return NO_SQ;
}

static void refresh_moves(void)
{
    sel = NO_SQ;
    state = ST_SELECT;
    compute_moves();
}

static void handle_input(void)
{
    uint8_t dir = dir_of(keys);

    /* A fresh press steps once, at once; a held direction steps again on the
     * divider.  Splitting them keeps one press to one step whatever the frame
     * counter happens to be. */
    if (dir != NO_SQ) {
        if (pressed & (J_UP | J_DOWN | J_LEFT | J_RIGHT)) cursor = walk(cursor, dir);
        else if ((frame & (REPEAT_DIV - 1u)) == 0)       cursor = walk(cursor, dir);
    }

    if (pressed & J_A) {
        /* A piece that cannot legally move (a man left behind by mandatory
         * capture, or one boxed in) is not selectable, and A on it does
         * nothing at all - not even a flicker of a highlight. */
        if (state == ST_SELECT && selectable(cursor)) {
            sel = cursor;
            state = ST_TARGET;
            compute_moves();                  /* narrow to this piece */
        }
    }
    if (pressed & J_B) {
        if (state == ST_TARGET) refresh_moves();
    }
}

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

/* Where the picked-up piece could go.  Only ever called with something picked
 * up: with nothing picked up `moves` is the whole side's list, and lighting all
 * of it up would say "any of these is yours to play" when the answer is really
 * "pick a piece up first".  Destinations are always unoccupied squares (a
 * capture lands *behind* the piece it takes), so this paints bare board. */
static void draw_highlights(void)
{
    uint8_t i;
    for (i = 0; i < n_moves; i++) {
        uint8_t row = SQ_ROW(moves[i].to), col = SQ_COL(moves[i].to), dx, dy;
        for (dy = 0; dy < 2; dy++) {
            for (dx = 0; dx < 2; dx++) {
                mapbuf[(2u * row + dy) * BOARD_TW + (2u * col + dx)] = T_DARK_HL;
            }
        }
    }
}

/* Follows the selection, so it is repositioned every frame like everything
 * else on the screen - the sprite is the one piece of the screen not made of
 * tiles, and a cursor that lags the thing it points at is worse than none. */
static void move_cursor(void)
{
    move_sprite(SPR_CURSOR, (uint8_t)(16u * SQ_COL(cursor)),
                            (uint8_t)(16u * SQ_ROW(cursor)));
}

static void new_game(void)
{
    board = board_init();
    cursor = 0;               /* row 0, col 1 - a back-rank man, see probe P3 */
    refresh_moves();
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
    SHOW_SPRITES;                   /* nothing positioned yet: the cursor
                                     * sprite sits at OAM (0, 0) - off-screen -
                                     * until START enters ST_SELECT */
    DISPLAY_ON;

    state = ST_TITLE;
    prev_keys = 0;
    frame = 0;

    while (1) {
        keys = joypad();
        pressed = keys & ~prev_keys;
        prev_keys = keys;
        frame++;

        if (state == ST_TITLE) {
            if (pressed & J_START) {
                new_game();
                state = ST_SELECT;
            }
        } else {
            /* This frame's move list, from this frame's board, before input
             * or the screen reads any of it. */
            compute_moves();
            handle_input();
        }

        /* Build the frame first, blit it second: the VRAM writes then land in
         * the VBlank window instead of being dropped by the PPU. */
        if (state == ST_TITLE) {
            build_title();
        } else {
            build_board();
            if (sel != NO_SQ) draw_highlights();
            draw_pieces();
            move_cursor();
        }
        wait_vbl_done();
        set_bkg_tiles(0, 0, BOARD_TW, BOARD_TH, mapbuf);
    }
}
