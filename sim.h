/* sim.h - GB-Draughts's hardware-independent rules (English draughts).
 *
 * stdint.h + board_geom.h only: no gb/gb.h. tests/test_sim.c compiles this
 * exact file with plain gcc, so `make test` exercises the real code and can
 * genuinely fail.
 *
 * Board = three 32-bit masks (black, white, kings) over the 32 playable
 * squares from board_geom.h. Diagonal adjacency is a precomputed table
 * (NEIGH), never computed here - see boardgen.py for how it's built and
 * self-checked.
 *
 * Rules implemented: mandatory capture (whole side, at the start of a
 * turn), multi-jump chains (continue with the *same* piece only, capture
 * only), promotion on reaching the far row. A man that promotes mid-chain
 * stops immediately, even if the new king could technically continue -
 * a documented simplification (regional variants disagree on this anyway).
 *
 * Not here yet (M2): evaluate() / search_best() - the AI opponent.
 */
#ifndef SIM_H
#define SIM_H

#include <stdint.h>
#include "board_geom.h"

#define SIDE_BLACK 0u
#define SIDE_WHITE 1u

#define MAX_MOVES  48u    /* generous upper bound for one side's move list */
#define MAX_JUMPS  4u     /* a single piece has at most 4 possible jumps   */

typedef struct {
    uint32_t black;
    uint32_t white;
    uint32_t kings;       /* subset of (black | white) */
} Board;

typedef struct {
    uint8_t from;
    uint8_t to;
    uint8_t capture;      /* NO_SQ if this is a simple (non-capturing) move */
} Move;

/* ------------------------------------------------------------- board init */

static Board board_init(void)
{
    Board b;
    b.black = BLACK_START;
    b.white = WHITE_START;
    b.kings = 0;
    return b;
}

/* 0 = empty, 1 = black, 2 = white. */
static uint8_t sq_occupant(const Board *b, uint8_t sq)
{
    uint32_t m = (uint32_t)1 << sq;
    if (b->black & m) return 1;
    if (b->white & m) return 2;
    return 0;
}

static uint8_t sq_is_king(const Board *b, uint8_t sq)
{
    return (b->kings & ((uint32_t)1 << sq)) != 0;
}

/* ------------------------------------------------------------ move rules */

/* Bitmask of the (up to 4) directions a piece on `sq` may move in, given
 * side and king-ness. Men move only toward the far row. */
static uint8_t dir_mask(const Board *b, uint8_t sq, uint8_t side)
{
    if (sq_is_king(b, sq)) return (uint8_t)((1u << DIR_NW) | (1u << DIR_NE) |
                                             (1u << DIR_SW) | (1u << DIR_SE));
    return (side == SIDE_BLACK) ? (uint8_t)((1u << DIR_SW) | (1u << DIR_SE))
                                 : (uint8_t)((1u << DIR_NW) | (1u << DIR_NE));
}

static void copy_moves(Move *out, const Move *src, uint8_t n)
{
    uint8_t i;
    for (i = 0; i < n; i++) out[i] = src[i];
}

/* Capturing moves available to the single piece on `sq` (used both by
 * gen_moves and to drive multi-jump chain continuation). Does not apply
 * the mandatory-capture rule itself - that's the caller's job, since the
 * rule differs between "start of turn" (whole side) and "mid-chain"
 * (this piece must capture again, or the chain ends; it may never make a
 * quiet move instead). */
static uint8_t gen_captures_from(const Board *b, uint8_t side, uint8_t sq, Move *out)
{
    uint32_t own = (side == SIDE_BLACK) ? b->black : b->white;
    uint32_t opp = (side == SIDE_BLACK) ? b->white : b->black;
    uint8_t dm = dir_mask(b, sq, side);
    uint8_t d, n = 0;

    for (d = 0; d < 4; d++) {
        uint8_t mid, land;
        if (!(dm & (uint8_t)(1u << d))) continue;
        mid = NEIGH[sq][d];
        if (mid == NO_SQ || !(opp & ((uint32_t)1 << mid))) continue;
        land = NEIGH[mid][d];
        if (land == NO_SQ) continue;
        if ((own | opp) & ((uint32_t)1 << land)) continue;
        if (n < MAX_JUMPS) {
            out[n].from = sq; out[n].to = land; out[n].capture = mid;
            n++;
        }
    }
    return n;
}

/* All legal moves for `side` at the start of its turn: captures for the
 * whole side if any piece has one (mandatory capture), else every simple
 * move. This is the only entry point that should be used at turn start;
 * mid-chain continuation goes through gen_captures_from() instead, because
 * a chain may never fall back to a simple move. */
static uint8_t gen_moves(const Board *b, uint8_t side, Move *out)
{
    Move simple[MAX_MOVES], cap[MAX_MOVES];
    uint8_t n_simple = 0, n_cap = 0, sq;
    uint32_t own = (side == SIDE_BLACK) ? b->black : b->white;
    uint32_t opp = (side == SIDE_BLACK) ? b->white : b->black;

    for (sq = 0; sq < N_SQ; sq++) {
        uint32_t m = (uint32_t)1 << sq;
        uint8_t dm, d;
        Move jumps[MAX_JUMPS];
        uint8_t nj;

        if (!(own & m)) continue;

        nj = gen_captures_from(b, side, sq, jumps);
        if (nj) {
            uint8_t i;
            for (i = 0; i < nj && n_cap < MAX_MOVES; i++) cap[n_cap++] = jumps[i];
            continue;
        }

        dm = dir_mask(b, sq, side);
        for (d = 0; d < 4; d++) {
            uint8_t mid;
            if (!(dm & (uint8_t)(1u << d))) continue;
            mid = NEIGH[sq][d];
            if (mid == NO_SQ) continue;
            if ((own | opp) & ((uint32_t)1 << mid)) continue;
            if (n_simple < MAX_MOVES) {
                simple[n_simple].from = sq; simple[n_simple].to = mid;
                simple[n_simple].capture = NO_SQ;
                n_simple++;
            }
        }
    }

    if (n_cap) { copy_moves(out, cap, n_cap); return n_cap; }
    copy_moves(out, simple, n_simple);
    return n_simple;
}

/* True if the piece now sitting on `sq` has a mandatory follow-up capture
 * (i.e. a multi-jump chain must continue with this same piece). Side is
 * inferred from occupancy so the caller doesn't need to track it through
 * a chain. */
static uint8_t must_continue(const Board *b, uint8_t sq)
{
    Move tmp[MAX_JUMPS];
    uint8_t side = (b->black & ((uint32_t)1 << sq)) ? SIDE_BLACK : SIDE_WHITE;
    return gen_captures_from(b, side, sq, tmp) > 0;
}

/* Applies one hop (simple move or single capture) and returns the new
 * board: moves the piece, removes the captured piece if any, and promotes
 * on reaching the far row. Pure - does not mutate *b. */
static Board apply_move(Board b, Move m)
{
    uint32_t from_m = (uint32_t)1 << m.from;
    uint32_t to_m   = (uint32_t)1 << m.to;
    uint8_t side = (b.black & from_m) ? SIDE_BLACK : SIDE_WHITE;
    uint8_t was_king = (b.kings & from_m) != 0;

    if (side == SIDE_BLACK) { b.black &= ~from_m; b.black |= to_m; }
    else                    { b.white &= ~from_m; b.white |= to_m; }
    if (was_king) { b.kings &= ~from_m; b.kings |= to_m; }

    if (m.capture != NO_SQ) {
        uint32_t cap_m = (uint32_t)1 << m.capture;
        b.black &= ~cap_m;
        b.white &= ~cap_m;
        b.kings &= ~cap_m;
    }

    if (!was_king) {
        if (side == SIDE_BLACK && m.to >= BLACK_KING_ROW_MIN) b.kings |= to_m;
        if (side == SIDE_WHITE && m.to <= WHITE_KING_ROW_MAX) b.kings |= to_m;
    }
    return b;
}

/* True if applying `m` (played from `before`, i.e. call this BEFORE
 * apply_move) promotes the mover. The caller - the turn loop, not
 * must_continue() - is responsible for ending a capture chain here: once
 * apply_move has run, the piece already reads as a king, so must_continue()
 * on the resulting board would happily say "yes, this king can keep
 * capturing" even on the very hop that promoted it. must_continue() has no
 * way to know that on its own (it only sees a board, not move history), so
 * every turn loop - this file's fuzz test included - must check
 * move_causes_promotion() on the pre-move board *before* trusting
 * must_continue() on the post-move one. */
static uint8_t move_causes_promotion(const Board *before, Move m)
{
    uint8_t side = (before->black & ((uint32_t)1 << m.from)) ? SIDE_BLACK : SIDE_WHITE;
    if (sq_is_king(before, m.from)) return 0;
    if (side == SIDE_BLACK) return m.to >= BLACK_KING_ROW_MIN;
    return m.to <= WHITE_KING_ROW_MAX;
}

/* A side with no legal moves (no pieces, or every piece blocked) has lost. */
static uint8_t side_to_move_has_moves(const Board *b, uint8_t side)
{
    Move tmp[MAX_MOVES];
    return gen_moves(b, side, tmp) > 0;
}

#endif /* SIM_H */
