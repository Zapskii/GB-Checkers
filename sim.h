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
 * The AI (M2) lives in this same header: evaluate() and search_best() at the
 * bottom. main.c does not call them yet - wiring the opponent into the turn
 * loop is the next phase, and until then this is host-tested code only.
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

/* ============================================================== AI search */
/* The opponent (M2). It plays the *same* engine as a human: it may only
 * ever return a move gen_moves()/gen_captures_from() handed it, so
 * mandatory capture, the chain rule and promotion-stops-chain all hold for
 * the machine too. It chooses within the rules; it never edits them.
 *
 * No C recursion anywhere: plain negamax with alpha-beta over an explicit
 * per-ply frame arena the caller supplies. main.c already owns the move
 * arrays, so handing over the arena too keeps sim.h pure - and it means the
 * DMG's real stack never nests, however deep the search goes.
 *
 * Every score is int16_t. GBDK's int is 16-bit and the host gcc's is
 * 32-bit, so a bare int here would diverge from the host tests at exactly
 * the values they cannot see. */

#define AI_MAX_PLY   6u      /* frames in the caller's arena = ply limit  */
#define AI_INF       30000   /* above any reachable score, below INT16_MAX */
#define AI_WIN_SCORE 20000   /* a loss at ply n scores -AI_WIN_SCORE + n  */

#define AI_MAN_VALUE   100   /* a king is worth more than a man and less  */
#define AI_KING_VALUE  175   /* than two: the classical checkers ratio    */
#define AI_MOBILITY_W  2     /* small enough to only break material ties  */

typedef struct {
    Board   board;                /* position at this node                */
    Move    moves[MAX_MOVES];     /* its legal moves (jump list mid-chain) */
    Move    entry;                /* the move that reached this node      */
    Move    best_move;            /* root only: the move search_best() returns */
    int16_t best;                 /* best value so far, this side's view  */
    int16_t alpha, beta;
    uint8_t n, index;
    uint8_t side;
    uint8_t depth_left;
} AiFrame;

static uint8_t pc32(uint32_t x)
{
    uint8_t n = 0;
    while (x) { n = (uint8_t)(n + (x & 1u)); x >>= 1; }
    return n;
}

/* Material + a small mobility term, from `side`'s point of view. Weighted so
 * that it can't reach int16_t: 12 kings is 2100 and the most mobility can
 * swing is 12 pieces x 4 moves x 2 = 96, both far under AI_INF. */
static int16_t evaluate(const Board *b, uint8_t side)
{
    Move tmp[MAX_MOVES];
    uint32_t own = (side == SIDE_BLACK) ? b->black : b->white;
    uint32_t opp = (side == SIDE_BLACK) ? b->white : b->black;
    uint8_t own_k = pc32(own & b->kings), opp_k = pc32(opp & b->kings);
    int16_t mat = (int16_t)((pc32(own) - own_k) * AI_MAN_VALUE + own_k * AI_KING_VALUE)
                - (int16_t)((pc32(opp) - opp_k) * AI_MAN_VALUE + opp_k * AI_KING_VALUE);
    int16_t mob = (int16_t)gen_moves(b, side, tmp)
                - (int16_t)gen_moves(b, (uint8_t)(1u - side), tmp);
    return (int16_t)(mat + AI_MOBILITY_W * mob);
}

/* Fold one child's value into frame `f` (the side to move at `ply`). `ply`
 * is 0 only at the root, which is the one frame whose chosen move is the
 * answer. A fail-high (alpha >= beta) stops this node by running it out of
 * moves; fail-soft, so `best` stays a usable bound. */
static void ai_fold(AiFrame *f, uint8_t ply, int16_t sc, Move m)
{
    if (sc > f->best) {
        f->best = sc;
        if (ply == 0u) f->best_move = m;
    }
    if (f->best > f->alpha) f->alpha = f->best;
    if (ply != 0u && f->alpha >= f->beta) f->index = f->n;
}

/* Best move for `side` at `b` to `depth` plies, by negamax with alpha-beta
 * over `arena` (an array of AI_MAX_PLY AiFrame). Returns the score from
 * `side`'s point of view and writes the chosen move to *out by identity -
 * from, to and capture, all three: two different legal moves can share a
 * destination (a man 9->13 and a king 18->13).
 *
 * A side with no moves has lost: that scores -AI_WIN_SCORE + ply, so a loss
 * is put off as long as possible, and *out is left alone - there is no move
 * to report. */
static int16_t search_best(Board b, uint8_t side, uint8_t depth,
                           AiFrame *arena, Move *out)
{
    uint8_t ply = 0;
    AiFrame *root;

    if (depth >= AI_MAX_PLY) depth = (uint8_t)(AI_MAX_PLY - 1u);  /* arena bound */

    root = &arena[0];
    root->board = b;
    root->side = side;
    root->depth_left = depth;
    root->index = 0;
    root->best = (int16_t)-AI_INF;
    root->alpha = (int16_t)-AI_INF;
    root->beta = AI_INF;
    root->entry.from = root->entry.to = root->entry.capture = NO_SQ;

    if (depth == 0u) {
        root->n = 0;
        root->best = evaluate(&b, side);
    } else {
        root->n = gen_moves(&b, side, root->moves);
        if (root->n == 0u) return (int16_t)-AI_WIN_SCORE;   /* no move: lost */
    }

    for (;;) {
        AiFrame *f = &arena[ply];

        if (f->index >= f->n) {                       /* node done */
            if (ply == 0u) {
                /* `n == 0` here means depth 0 (no child was ever searched,
                 * so best_move was never set) -- or a loss, which returned
                 * above. Writing an unset best_move would hand the caller
                 * an indeterminate from/to/capture. */
                if (out && f->n != 0u) *out = f->best_move;
                return f->best;
            }
            {
                AiFrame *p = &arena[ply - 1u];
                ai_fold(p, (uint8_t)(ply - 1u), (int16_t)(-f->best), f->entry);
                ply--;
            }
            continue;
        }

        {
            Move m = f->moves[f->index++];
            Board before = f->board;
            Board child = apply_move(before, m);
            uint8_t cside;
            AiFrame *c;

            /* The chain rule, exactly as the turn loop has it: this piece
             * captures again only if it just captured, did not promote on
             * this hop, and has a further capture from where it landed. */
            if (m.capture != NO_SQ && !move_causes_promotion(&before, m) &&
                must_continue(&child, m.to)) {
                cside = f->side;
                c = &arena[ply + 1u];
                c->n = gen_captures_from(&child, cside, m.to, c->moves);
            } else {
                cside = (uint8_t)(1u - f->side);
                c = &arena[ply + 1u];
                c->n = gen_moves(&child, cside, c->moves);
            }

            c->board = child;
            c->side = cside;
            c->entry = m;
            c->depth_left = (uint8_t)(f->depth_left - 1u);
            c->index = 0;
            c->best = (int16_t)-AI_INF;
            c->alpha = (int16_t)(-f->beta);
            c->beta  = (int16_t)(-f->alpha);

            if (c->n == 0u) {
                /* Handed the move with nothing to play: a loss, and one
                 * found nearer the root is worse, so subtract the ply. */
                ai_fold(f, ply, (int16_t)(AI_WIN_SCORE - (int16_t)(ply + 1u)), m);
            } else if (c->depth_left == 0u) {
                ai_fold(f, ply, (int16_t)(-evaluate(&child, cside)), m);
            } else {
                ply++;                                       /* descend */
            }
        }
    }
}

#endif /* SIM_H */
