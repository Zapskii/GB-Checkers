/* Host tests for board_geom.h + sim.h. Build and run with `make test`
 * (plain gcc). No emulator involved. */
#include <stdio.h>
#include <stdlib.h>
#include "../sim.h"

static int checks = 0;

#define CHECK(cond) do { \
    checks++; \
    if (!(cond)) { \
        printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        exit(1); \
    } \
} while (0)

static int popcount32(uint32_t x)
{
    int n = 0;
    while (x) { n += (int)(x & 1u); x >>= 1; }
    return n;
}

/* ------------------------------------------------------------- geometry */

static void test_geometry_symmetry(void)
{
    static const uint8_t OPP[4] = { DIR_SE, DIR_SW, DIR_NE, DIR_NW };
    uint8_t sq, d;
    for (sq = 0; sq < N_SQ; sq++) {
        for (d = 0; d < 4; d++) {
            uint8_t other = NEIGH[sq][d];
            if (other == NO_SQ) continue;
            CHECK(other < N_SQ);
            CHECK(other != sq);
            CHECK(NEIGH[other][OPP[d]] == sq);
        }
    }
}

static void test_start_position(void)
{
    Board b = board_init();
    CHECK(popcount32(b.black) == 12);
    CHECK(popcount32(b.white) == 12);
    CHECK((b.black & b.white) == 0);
    CHECK(b.kings == 0);
    /* rows 3-4 (squares 12-19) are the empty middle */
    CHECK((b.black & 0x000FF000u) == 0);
    CHECK((b.white & 0x000FF000u) == 0);
}

/* ------------------------------------------------------------ move gen */

static Board empty_board(void)
{
    Board b;
    b.black = b.white = b.kings = 0;
    return b;
}

static void put(Board *b, uint8_t sq, uint8_t side, uint8_t king)
{
    uint32_t m = (uint32_t)1 << sq;
    if (side == SIDE_BLACK) b->black |= m; else b->white |= m;
    if (king) b->kings |= m;
}

/* A lone black man with one empty diagonal ahead: exactly one simple move,
 * no captures (nothing to capture). */
static void test_simple_move(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES];
    uint8_t n;
    put(&b, 9, SIDE_BLACK, 0);          /* row2,col5: SW=13, SE=14, both empty */
    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n == 2);
    CHECK(mv[0].capture == NO_SQ);
    CHECK(mv[1].capture == NO_SQ);
}

/* One capturable white piece with an empty landing square: mandatory
 * capture must be the only move offered, even though a simple move also
 * exists for a different black piece elsewhere on the board. */
static void test_mandatory_capture_whole_side(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES];
    uint8_t n, i;
    uint8_t saw_capture = 0;

    put(&b, 9, SIDE_BLACK, 0);           /* can simple-move: no capture here */
    put(&b, 18, SIDE_BLACK, 0);          /* row4,col6 */
    put(&b, 22, SIDE_WHITE, 0);          /* row5,col5: SE-adjacent to 18     */
    /* NEIGH[18][SE] should be 22, and NEIGH[22][SE] should be an empty landing */

    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n > 0);
    for (i = 0; i < n; i++) {
        CHECK(mv[i].capture != NO_SQ);   /* every offered move is a capture */
        if (mv[i].from == 18) saw_capture = 1;
    }
    CHECK(saw_capture);
}

/* No captures anywhere: simple moves are offered normally. */
static void test_no_capture_available(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES];
    uint8_t n, i;
    put(&b, 9, SIDE_BLACK, 0);
    put(&b, 21, SIDE_WHITE, 0);           /* far away, nothing adjacent */
    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n > 0);
    for (i = 0; i < n; i++) CHECK(mv[i].capture == NO_SQ);
}

/* A man fully boxed in by enemies with no jump possible has zero moves. */
static void test_blocked_piece_has_no_moves(void)
{
    Board b = empty_board();
    put(&b, 9, SIDE_BLACK, 0);            /* row2,col5; SW=13, SE=14 */
    put(&b, 13, SIDE_WHITE, 0);
    put(&b, 14, SIDE_WHITE, 0);
    put(&b, 16, SIDE_WHITE, 0);           /* landing square behind 13 (SW) */
    put(&b, 18, SIDE_WHITE, 0);           /* landing square behind 14 (SE) */
    CHECK(!side_to_move_has_moves(&b, SIDE_BLACK));
}

/* A side with no pieces at all has no moves (the "lost" condition). */
static void test_no_pieces_has_no_moves(void)
{
    Board b = empty_board();
    put(&b, 9, SIDE_WHITE, 0);
    CHECK(!side_to_move_has_moves(&b, SIDE_BLACK));
    CHECK(side_to_move_has_moves(&b, SIDE_WHITE));
}

/* -------------------------------------------------------------- capture */

/* Applying a capture removes the jumped piece and moves the jumper. */
static void test_apply_move_capture(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES];
    uint8_t n;
    Board b2;

    put(&b, 18, SIDE_BLACK, 0);
    put(&b, 22, SIDE_WHITE, 0);
    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n == 1);
    CHECK(mv[0].from == 18 && mv[0].capture == 22);

    b2 = apply_move(b, mv[0]);
    CHECK(sq_occupant(&b2, 18) == 0);           /* jumper left its square */
    CHECK(sq_occupant(&b2, 22) == 0);           /* captured piece gone    */
    CHECK(sq_occupant(&b2, mv[0].to) == 1);     /* jumper landed          */
    CHECK(popcount32(b2.black) + popcount32(b2.white) == 1);
}

/* A double jump: after the first capture, the piece must continue from
 * its landing square, and only via another capture. */
static void test_multi_jump_chain(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES], cont[MAX_JUMPS];
    uint8_t n, nc;
    Board b2;

    /* black at 1 jumps SW over white at 5, landing at 8; from 8 it must
     * then jump SE over white at 13, landing at 17. Coordinates read
     * directly off the generated NEIGH table, not guessed. */
    put(&b, 1, SIDE_BLACK, 0);
    put(&b, 5, SIDE_WHITE, 0);       /* NEIGH[1][SW]  == 5  */
    put(&b, 13, SIDE_WHITE, 0);      /* NEIGH[8][SE]  == 13 */

    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n == 1);
    CHECK(mv[0].from == 1 && mv[0].capture == 5 && mv[0].to == 8);

    b2 = apply_move(b, mv[0]);
    CHECK(must_continue(&b2, 8));

    nc = gen_captures_from(&b2, SIDE_BLACK, 8, cont);
    CHECK(nc == 1);
    CHECK(cont[0].from == 8 && cont[0].capture == 13);

    b2 = apply_move(b2, cont[0]);
    CHECK(!must_continue(&b2, cont[0].to));
    CHECK(popcount32(b2.black) == 1);
    CHECK(popcount32(b2.white) == 0);
}

/* -------------------------------------------------------------- kings */

static void test_king_moves_all_four_directions(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES];
    uint8_t n, i;
    uint8_t dirs_seen = 0;

    put(&b, 17, SIDE_BLACK, 1);      /* row4,col2: interior square, all 4 neighbors exist and are empty */
    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n == 4);
    for (i = 0; i < n; i++) {
        if (mv[i].to == NEIGH[17][DIR_NW]) dirs_seen |= 1;
        if (mv[i].to == NEIGH[17][DIR_NE]) dirs_seen |= 2;
        if (mv[i].to == NEIGH[17][DIR_SW]) dirs_seen |= 4;
        if (mv[i].to == NEIGH[17][DIR_SE]) dirs_seen |= 8;
    }
    CHECK(dirs_seen == 15);          /* all four directions represented */
}

/* A plain man cannot move backward (away from the far row). */
static void test_man_cannot_move_backward(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES];
    uint8_t n, i;
    put(&b, 17, SIDE_BLACK, 0);       /* not a king */
    n = gen_moves(&b, SIDE_BLACK, mv);
    for (i = 0; i < n; i++) {
        CHECK(mv[i].to != NEIGH[17][DIR_NW]);
        CHECK(mv[i].to != NEIGH[17][DIR_NE]);
    }
}

/* A black man reaching the last row promotes; a king moving does not
 * un-promote or re-promote. */
static void test_promotion(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES];
    Board b2;
    uint8_t n;

    put(&b, 24, SIDE_BLACK, 0);       /* row6,col1: SE/SW lead into row 7 */
    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n >= 1);
    b2 = apply_move(b, mv[0]);
    CHECK(mv[0].to >= BLACK_KING_ROW_MIN);
    CHECK(sq_is_king(&b2, mv[0].to));

    /* moving the now-king elsewhere keeps it a king */
    {
        Move mv2[MAX_MOVES];
        uint8_t n2 = gen_moves(&b2, SIDE_BLACK, mv2);
        Board b3;
        CHECK(n2 >= 1);
        b3 = apply_move(b2, mv2[0]);
        CHECK(sq_is_king(&b3, mv2[0].to));
    }
}

/* A man that promotes mid-capture stops immediately - documented
 * simplification (see sim.h) - even though must_continue() alone, which
 * only looks at board state and so sees an already-promoted king, would
 * say the chain *could* continue. move_causes_promotion() is what the
 * turn loop must check first; this test would fail to tell them apart if
 * it didn't set up a real further capture for the new king to (wrongly)
 * take, so must_continue() is CHECKed true here on purpose. */
static void test_promotion_stops_chain(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES];
    Board b2;
    uint8_t n;

    put(&b, 21, SIDE_BLACK, 0);       /* NEIGH[21][SE] == 25, lands on 30 (row7: promotes) */
    put(&b, 25, SIDE_WHITE, 0);
    /* a piece only a king could jump next (NE of the landing square, 30) */
    put(&b, 26, SIDE_WHITE, 0);       /* NEIGH[30][NE] == 26 */

    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n == 1);
    CHECK(mv[0].to == 30);

    CHECK(move_causes_promotion(&b, mv[0]));

    b2 = apply_move(b, mv[0]);
    CHECK(sq_is_king(&b2, mv[0].to));
    CHECK(must_continue(&b2, mv[0].to));     /* geometrically, yes it could */
    /* ...but the turn loop must not act on that: move_causes_promotion()
     * above is what actually ends the turn. */
}

/* -------------------------------------------------------------- search */
/* The AI (M2) may only ever return a move the engine handed it. These
 * check the contract, not the strength: whatever the weights say, a
 * search that invents a move is playing a different game from a human
 * and would break mandatory capture silently. */

static AiFrame arena[AI_MAX_PLY];

static Move mv_of(uint8_t from, uint8_t to, uint8_t capture)
{
    Move m;
    m.from = from; m.to = to; m.capture = capture;
    return m;
}

/* Whole-move equality. Comparing `.to` alone is not enough: two different
 * legal moves can share a destination (a man 9->13 and a king 18->13). */
static uint8_t same_move(Move a, Move b)
{
    return (uint8_t)(a.from == b.from && a.to == b.to && a.capture == b.capture);
}

/* The score has a sign and a size: black is two men up in a quiet
 * position, so evaluate() must say roughly +200, not 0 and not a material
 * figure with the wrong sign. This is the check that catches a stubbed or
 * zeroed evaluator. */
static void test_search_scores_material(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES], out;
    uint8_t n, i;
    int16_t sc;

    put(&b, 9, SIDE_BLACK, 0);
    put(&b, 13, SIDE_BLACK, 0);
    put(&b, 17, SIDE_BLACK, 0);
    put(&b, 27, SIDE_WHITE, 0);      /* far away: nothing can capture */

    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n == 4);
    for (i = 0; i < n; i++) CHECK(mv[i].capture == NO_SQ);   /* quiet: eval only */

    /* zero-sum: the same board seen from the other side negates */
    CHECK(evaluate(&b, SIDE_BLACK) == (int16_t)-evaluate(&b, SIDE_WHITE));

    sc = search_best(b, SIDE_BLACK, 1, arena, &out);
    CHECK(sc >= 200 && sc <= 215);   /* 3 men vs 1 = +200, plus mobility */
}

/* Two captures on offer: taking one man with the piece that gets
 * recaptured, or taking two with the other piece. The 2-for-1 must win -
 * and the cheap route is deliberately offered FIRST (square 9 before
 * square 15), so "return moves[0]" is not a passing answer. */
static void test_search_prefers_the_two_for_one(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES], out;
    uint8_t n, d;

    put(&b, 9, SIDE_BLACK, 0);
    put(&b, 15, SIDE_BLACK, 0);
    put(&b, 13, SIDE_WHITE, 0);      /* 9 jumps it, then white 20 recaptures 16 */
    put(&b, 18, SIDE_WHITE, 0);      /* 15 jumps 18, then 25: two for one */
    put(&b, 20, SIDE_WHITE, 0);
    put(&b, 25, SIDE_WHITE, 0);

    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n == 2);
    CHECK(same_move(mv[0], mv_of(9, 16, 13)));    /* the trap is offered first */
    CHECK(same_move(mv[1], mv_of(15, 22, 18)));

    for (d = 1; d <= 3; d++) {
        search_best(b, SIDE_BLACK, d, arena, &out);
        CHECK(same_move(out, mv_of(15, 22, 18)));
    }
}

/* A king is worth more than a man: given a choice of captures, take the
 * king. One black king, two white pieces next to it - a man NW, a king
 * SW, so the man's route comes first in direction order as well. */
static void test_search_takes_the_king(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES], out;
    uint8_t n, d;

    put(&b, 22, SIDE_BLACK, 1);      /* black king: NW=17, SW=25 */
    put(&b, 17, SIDE_WHITE, 0);
    put(&b, 25, SIDE_WHITE, 1);

    n = gen_moves(&b, SIDE_BLACK, mv);
    CHECK(n == 2);
    CHECK(same_move(mv[0], mv_of(22, 13, 17)));   /* man first */
    CHECK(same_move(mv[1], mv_of(22, 29, 25)));   /* king second */

    for (d = 1; d <= 3; d++) {
        search_best(b, SIDE_BLACK, d, arena, &out);
        CHECK(same_move(out, mv_of(22, 29, 25)));
    }
}

/* An out-move is only ever written when there is a move to write. */
static void test_search_no_moves_leaves_out_alone(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES];
    Move out = mv_of(7, 7, 7);
    int16_t sc;

    put(&b, 9, SIDE_WHITE, 0);       /* black has nothing at all */
    CHECK(gen_moves(&b, SIDE_BLACK, mv) == 0);
    sc = search_best(b, SIDE_BLACK, 2, arena, &out);
    CHECK(sc == (int16_t)-AI_WIN_SCORE);          /* a loss, scored at the root */
    CHECK(same_move(out, mv_of(7, 7, 7)));        /* untouched */
}

/* Depth 0 searches nothing, so it has no move to report either. Same
 * contract as a loss, and a different path to it: the root's move list is
 * never walked, so best_move is never assigned and must not be written out.
 * A caller wired straight to play() would otherwise play from/to/capture
 * that were never chosen - `to` can even be a square that does not exist. */
static void test_search_depth_zero_leaves_out_alone(void)
{
    Board b = empty_board();
    Move mv[MAX_MOVES];
    Move out = mv_of(NO_SQ, NO_SQ, NO_SQ);
    int16_t sc;

    put(&b, 9, SIDE_BLACK, 0);
    put(&b, 13, SIDE_BLACK, 0);
    put(&b, 17, SIDE_BLACK, 0);
    put(&b, 27, SIDE_WHITE, 0);

    CHECK(gen_moves(&b, SIDE_BLACK, mv) > 0);      /* a move does exist ... */
    sc = search_best(b, SIDE_BLACK, 0, arena, &out);
    CHECK(sc == evaluate(&b, SIDE_BLACK));         /* ... depth 0 is the eval */
    CHECK(sc > 0);                                 /* and a real one, not 0 */
    CHECK(same_move(out, mv_of(NO_SQ, NO_SQ, NO_SQ)));
}

/* --------------------------------------------------------- random fuzz */

/* Plays randomly-legal games to a ply cap, asserting invariants after
 * every hop (including mid-chain hops). This is the checkers equivalent
 * of GB-Protector's exhaustive torus_overlap sweep, but at the level of
 * whole games: it can't prove no illegal position is ever reachable, but
 * it exercises mandatory capture, chains, and promotion together across
 * thousands of random positions, which the hand-built tests above can't.
 *
 * With `depth` non-zero it also asks the search for a move at every turn
 * start and requires it to be one of the legal ones (all fields, not just
 * the destination). Turn start only: search_best() is a turn-start entry
 * point, and mid-chain the rule in force is the other one. */
static void check_invariants(const Board *b, int prev_pieces)
{
    int pieces = popcount32(b->black) + popcount32(b->white);
    CHECK((b->black & b->white) == 0);              /* no double-occupancy */
    CHECK((b->kings & ~(b->black | b->white)) == 0); /* kings subset of pieces */
    CHECK(pieces <= prev_pieces);                    /* never gains pieces */
}

static void fuzz_self_play(unsigned seed, int max_ply, uint8_t depth, int *searched)
{
    Board b = board_init();
    uint8_t side = SIDE_BLACK;
    int ply, pieces = popcount32(b.black) + popcount32(b.white);

    srand(seed);

    for (ply = 0; ply < max_ply; ply++) {
        Move mv[MAX_MOVES];
        uint8_t n = gen_moves(&b, side, mv);
        Move chosen;
        Board before;

        if (!n) break;                     /* side to move has lost */

        if (depth) {
            /* A move the caller can recognise as "never written". */
            Move out = mv_of(NO_SQ, NO_SQ, NO_SQ);
            int16_t sc = search_best(b, side, depth, arena, &out);
            uint8_t i, legal = 0;

            CHECK(sc > (int16_t)-AI_WIN_SCORE);   /* has moves, so not a loss */
            for (i = 0; i < n; i++) if (same_move(out, mv[i])) legal = 1;
            CHECK(legal);                         /* chose, never invented */
            if (searched) (*searched)++;
        }

        chosen = mv[rand() % n];
        before = b;
        b = apply_move(b, chosen);
        check_invariants(&b, pieces);
        pieces = popcount32(b.black) + popcount32(b.white);

        while (chosen.capture != NO_SQ &&
               !move_causes_promotion(&before, chosen) &&
               must_continue(&b, chosen.to)) {
            Move cont[MAX_JUMPS];
            uint8_t nc = gen_captures_from(&b, side, chosen.to, cont);
            CHECK(nc > 0);
            chosen = cont[rand() % nc];
            before = b;
            b = apply_move(b, chosen);
            check_invariants(&b, pieces);
            pieces = popcount32(b.black) + popcount32(b.white);
        }

        side = (uint8_t)(1u - side);
    }
}

static void test_fuzz_many_seeds(void)
{
    unsigned s;
    for (s = 1; s <= 200; s++) fuzz_self_play(s, 400, 0, (int *)0);
}

/* The same random games, with the search asked at every turn start. */
static void test_search_stays_legal(void)
{
    unsigned s;
    int searched = 0;
    for (s = 1; s <= 200; s++) fuzz_self_play(s, 24, 2, &searched);
    CHECK(searched >= 200);     /* the games really did reach positions */
}

int main(void)
{
    test_geometry_symmetry();
    test_start_position();
    test_simple_move();
    test_mandatory_capture_whole_side();
    test_no_capture_available();
    test_blocked_piece_has_no_moves();
    test_no_pieces_has_no_moves();
    test_apply_move_capture();
    test_multi_jump_chain();
    test_king_moves_all_four_directions();
    test_man_cannot_move_backward();
    test_promotion();
    test_promotion_stops_chain();
    test_search_scores_material();
    test_search_prefers_the_two_for_one();
    test_search_takes_the_king();
    test_search_no_moves_leaves_out_alone();
    test_search_depth_zero_leaves_out_alone();
    test_search_stays_legal();
    test_fuzz_many_seeds();
    printf("OK: %d checks passed\n", checks);
    return 0;
}
