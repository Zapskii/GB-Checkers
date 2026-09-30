/* GB-DRAUGHTS - English draughts for the Game Boy (GBDK-2020).
 *
 * The rules engine lives in sim.h and is unit tested on the host; this file
 * owns everything that touches hardware.  P5: a capture that can continue
 * does - the SAME piece captures again and the turn does not pass - and a hop
 * that promotes ends the turn on the spot, even though the new king could
 * geometrically keep going.  Which of those two it is is decided by
 * move_causes_promotion() on the PRE-move board; see play().
 *
 * P6 closes Milestone 1.  A turn that leaves the other side with no legal move
 * ends the game: ST_OVER, and the result on the status row.  The question is
 * asked at the turn boundary and nowhere else - mid-chain is not a boundary,
 * and the side that has to lose is the one that has just been handed the turn,
 * never the one that just moved.  START (or B) starts a new game from there.
 *
 * P10 puts an opponent in the white chair.  SELECT on the title screen picks
 * 2 PLAYERS or 1 PLAYER; in 1 PLAYER the human is black (black moves first, so
 * the human always opens) and every white turn is the machine's.  The machine
 * does not get a turn loop of its own: it draws THINKING, calls search_best()
 * from sim.h, and hands the answer to the SAME play() the human's moves go
 * through - so the chain rule, promotion-stops-the-chain and the end-of-game
 * question have one implementation, not two.
 *
 * Controls: START = new game, SELECT = 2/1 PLAYER (title screen only),
 * d-pad = move the cursor, A = select/play, B = deselect (and, in ST_OVER,
 * also new game).
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
#define MODE_ROW   8u    /* the title's 2/1 PLAYER line, between the two below */
#define STATUS_ROW 10u   /* the one centred line of text inside the board
                          * region: the title's prompt, the machine's THINKING,
                          * and the result in ST_OVER - the three are mutually
                          * exclusive states of the one line */
#define TEXT_COL   4u    /* the longest strings are 11 tiles wide: (20-11)/2 */

/* The HUD strip.  PLAN.md's data contract gives it tiles x 16..19, y 0..15 - a
 * 32 px column down the right-hand edge, outside the board's own 16x16.  The
 * word is STACKED down one tile column rather than written across the strip:
 * "BLACK" is five tiles and the strip is four, so sideways it would run off the
 * 20-tile screen.  One blank tile clear of the board edge, (16 - 5) / 2 rows
 * down. */
#define HUD_X    16u
#define HUD_TW   4u
#define HUD_TH   16u
#define HUD_COL  1u
#define HUD_ROW0 5u

/* The cursor walks square to square along NEIGH, so it can step over squares
 * that cannot be picked up right now - with mandatory capture most of them
 * cannot - and WALK_STEPS caps how far one press may travel.  A held direction
 * steps again every REPEAT_DIV turns of the loop. */
#define WALK_STEPS 4u
#define REPEAT_DIV 8u

typedef enum { ST_TITLE, ST_SELECT, ST_TARGET, ST_OVER } State;

/* ----------------------------------------------------------------- state
 * The state machine and the rules engine's board.  The screen holds no state of
 * its own: it is a function of these, rebuilt from `board` and from the move
 * list below whenever either could have changed, so there is nothing to keep in
 * sync and nothing a stale cache can leave behind.  The one thing remembered is
 * what the last build was made FROM - see screen_stale(), and the probe check
 * that holds it honest. */
static State   state;
static uint8_t keys, pressed, prev_keys, frame, repeat;
static Board   board;
static uint8_t side;          /* whose turn it is: SIDE_BLACK / SIDE_WHITE */

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

/* Set when a capture can continue with the SAME piece, NO_SQ otherwise, and
 * the one thing that decides which entry point compute_legal() asks.  It is a
 * rule input, not a cache: play() sets it from `board` after every hop and
 * nothing else, so it cannot outlive the position it describes. */
static uint8_t chain_sq;

/* The frame's whole tilemap, built then blitted in one write inside VBlank.
 * The HUD is a second, much smaller blit: it is the one thing on the screen
 * that lives outside the board's 16x16 region. */
static uint8_t mapbuf[BOARD_TW * BOARD_TH];
static uint8_t hudbuf[HUD_TW * HUD_TH];

/* ---------------------------------------------------------------- the AI
 * Two players, or the machine playing white.  One global, flipped by SELECT on
 * the title screen - the only place it can change, so a game never swaps
 * opponent halfway through.  It is part of what the screen is built from (the
 * title says which mode you are about to start), so screen_stale() watches it
 * like everything else. */
static uint8_t mode_1p;

/* How deep the machine searches, in plies.  MEASURED, not guessed - P11
 * (tools/probe.py's p11_think_budget, which walks the same three positions):
 *
 *   position                     depth 2   depth 3   depth 4
 *   opening (the start)            177      1395      4236
 *   midgame, 12 pieces             384      1046      2712
 *   endgame, 4 pieces               80       304       317
 *
 * The numbers are EMULATOR FRAMES (16.7 ms each) of the whole think, worst of
 * the fuzz draws tried, on the SDCC build.  The budget is 600 frames; the
 * worst think at depth 2 is 384 (6.4 s), at depth 3 it is 1395 (23 s), so 2 is
 * the largest depth that fits and this stays where P10 left it - now for a
 * measured reason instead of a guessed one.  Two things about the shape of the
 * table: cost is not monotone in pieces (the start position has 24 of them but
 * only 7 moves each, and it is the WORST position of all once the search is
 * deep enough to run out of alpha-beta cutoffs), and one ply is not a fixed
 * multiplier (a deeper search prunes differently; midgame1 is 861 at depth 3
 * and 804 at depth 4).
 *
 * The search is a blocking call, so an over-optimistic value here is a frozen
 * screen, not a merely slow one.  The check fails on a budget overrun rather
 * than on this number, so raising it means re-measuring. */
#define AI_DEPTH 2u

/* The depth the machine actually searches, as a SYMBOL rather than the macro
 * alone.  "The worst think fits the budget" is a claim about one depth, so the
 * ROM has to be able to say which one it was built at: P11's budget check reads
 * this out of the linker map and holds it to the measured number above, so a
 * build that has drifted off its measurement fails the check that was measured
 * for it instead of passing quietly on a shallower search. */
static uint8_t ai_depth = AI_DEPTH;

/* P9's search arena, one frame per ply.  FILE SCOPE, and that is load-bearing:
 * AI_MAX_PLY * sizeof(AiFrame) is about a kilobyte, and on the DMG's stack that
 * is not "large", it is a crash.  There is one machine and one position at a
 * time, so one arena is the whole need. */
static AiFrame arena[AI_MAX_PLY];

/* ------------------------------------------------------------------ rules
 * Nothing below touches hardware: it is board -> move list -> selection, which
 * is the part the emulator probe can hold still and assert on. */
static void compute_legal(void)
{
    uint8_t i, n = 0;

    /* The side to move, out of the same `board` the pieces are drawn from, and
     * rebuilt here every frame - never carried over from the last one.  Mandatory
     * capture costs nothing at this point: gen_moves() already returns captures
     * and nothing else when any piece of this side has one, so the legal set IS
     * the enforcement and there is no cache behind it to go stale.
     *
     * Mid-chain the rule is a different one, so the entry point is a different
     * one.  gen_moves() is the turn-START entry point - whole side, mandatory
     * capture - and asking it again halfway through a chain is the quiet way
     * this breaks: the piece that just captured is no longer the only piece on
     * offer, so a second piece's capture (or, with no capture left anywhere, a
     * quiet move) becomes playable and the mandatory continuation is gone
     * without anything looking wrong on screen.  gen_captures_from() is the
     * mid-chain rule entire: this piece, capturing, or nothing at all. */
    if (chain_sq != NO_SQ) n_moves = gen_captures_from(&board, side, chain_sq, moves);
    else                   n_moves = gen_moves(&board, side, moves);

    if (sel == NO_SQ) return;                 /* nothing picked up: keep all */
    for (i = 0; i < n_moves; i++) {
        if (moves[i].from == sel) moves[n++] = moves[i];
    }
    n_moves = n;
}

/* Where the cursor may come to rest, and *not* "is this a square holding one of
 * my men".  It is a piece that can legally move: under mandatory capture a piece
 * of the side to move that cannot capture has no moves at all, so it must not be
 * pickable either.  That is membership of the move list the screen is drawing
 * from, which is what keeps the two in step.
 *
 * ST_SELECT only: this is the walk's question.  With a piece up there is no
 * walking (see cycle_target), and the only squares in play are the destinations
 * the list itself holds. */
static uint8_t landable(uint8_t sq)
{
    uint8_t i;
    for (i = 0; i < n_moves; i++) {
        if (moves[i].from == sq) return 1;
    }
    return 0;
}

/* One d-pad step with nothing picked up: hop along NEIGH in `dir`, landing on
 * the first square that is EMPTY or that is in the current move list, at most
 * WALK_STEPS hops.  Squares that are neither are stepped *over*; running out of
 * board or of steps leaves the cursor where it was.
 *
 * Empty squares are landable, and that is not a nicety - without it the game is
 * unplayable.  The move list holds only the side's movable MEN, and a walk may
 * only travel along a single diagonal.  The four movable men at the start
 * position sit on squares 8, 9, 10 and 11 - one row, two columns apart - so no
 * straight diagonal line joins any two of them, and a cursor that could land
 * only on the move list was trapped on whichever man it first reached: pick one
 * piece or pick none.  Letting it rest on bare board makes the whole board
 * navigable while keeping the property that matters - it can never come to rest
 * on a piece it cannot use, because those are occupied and not in the move list,
 * so they are stepped over.  Two hops is what a capture needs (over the taken
 * piece and onto the square behind it); four leaves room to spare. */
static uint8_t walk(uint8_t from, uint8_t dir)
{
    uint8_t sq, i;

    /* The turn just changed and the cursor was parked.  NO_SQ is not a square -
     * NEIGH[NO_SQ] reads off the end of the table - so a press from there
     * re-seats the cursor on the new side's first legal square instead of
     * stepping from it. */
    if (from == NO_SQ) return n_moves ? moves[0].from : NO_SQ;

    sq = from;
    for (i = 0; i < WALK_STEPS; i++) {
        sq = NEIGH[sq][dir];
        if (sq == NO_SQ) break;               /* off the board: stay put */
        if (landable(sq) || !sq_occupant(&board, sq)) return sq;
    }
    return from;
}

/* Where the cursor is put the moment a piece is picked up, and after every hop
 * of a chain: on a destination, so that A always has something to do.
 *
 * It used to stay on the piece, and the player had to walk it to a destination.
 * That reads as "A does nothing" whenever the walk ends somewhere with nothing
 * on it - which is easy to do, since a piece's destinations are usually NOT
 * neighbours of each other (9's are 13 and 14, and 13's only neighbours are 5
 * and 17), so the walk between them has to pass through a square that is not
 * one of them and therefore a dead press.  Seating it removes the whole class:
 * from here every press cycles to a destination and every press of A plays. */
static void seat_cursor(void)
{
    /* Both callers narrow the list to the picked-up piece first - that is what
     * compute_legal() does with `sel` set - so every entry is a move of that
     * piece and the first one's destination is a landing square.  Nothing to
     * search for: the work was already done one call ago.
     *
     * Do NOT put a loop back here.  `moves[i].to` after a compare against a
     * global is miscompiled by this toolchain: SDCC builds &moves[i] in HL, then
     * reloads HL with #sel for the compare, then emits `inc hl` to reach .to -
     * which now reads sel + 1.  That byte is `cursor` (declared right after
     * `sel`), so the store is a no-op and the bug looks exactly like "the
     * function ran but its write vanished". */
    if (n_moves) cursor = moves[0].to;
}

/* A d-pad press with a piece up steps to the next destination, and WHICH key
 * was pressed does not matter, for the reason above: "walk that way" has no
 * meaning between squares that are not neighbours.  One press per destination,
 * wrapping, so every destination is reachable and no press can do nothing. */
static void cycle_target(void)
{
    uint8_t i;
    for (i = 0; i < n_moves; i++) {
        if (moves[i].to == cursor) {
            cursor = moves[i + 1 < n_moves ? (uint8_t)(i + 1) : 0].to;
            return;
        }
    }
    if (n_moves) cursor = moves[0].to;      /* not on one: seat it */
}

/* Each d-pad key is one diagonal - the board has no other kind of neighbour, so
 * every press has to pick one, and these are the four.  The mapping is the
 * d-pad turned 45 degrees anticlockwise, which means UP and RIGHT both go UP
 * the screen and DOWN and LEFT both go DOWN:
 *
 *        UP  up-left        RIGHT  up-right
 *       LEFT down-left       DOWN  down-right
 *
 * and nothing moves straight sideways.  A player cannot guess this, so the
 * README's controls table spells it out; keep the two in step.  A cell is never
 * stepped into off-board because NEIGH says NO_SQ there - this file does no
 * row/col arithmetic. */
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
    chain_sq = NO_SQ;         /* back to a turn start */
    sel = NO_SQ;
    state = ST_SELECT;
    compute_legal();
}

/* Plays the move that goes from `from` to `to`.  The move comes straight out of
 * the list the screen is showing, so what gets applied is exactly what was
 * highlighted - and for the machine, exactly what the search chose.
 *
 * Matched on BOTH squares, not on the destination alone.  Destination-only was
 * fine while the only caller was a human with one piece picked up (every entry
 * in the list is that piece's, so a destination names one move), but the
 * machine calls this with a move out of the WHOLE side's list, where two legal
 * moves can share a destination - a man 9->13 and a king 18->13 in the same
 * position.  First-match on the destination would then silently apply a
 * different piece's move than the one the search picked, and the board would
 * look perfectly legal while being wrong.  (from, to) is unique: the jumped
 * square is the midpoint of the two, so it is determined by them.
 *
 * Then the turn does one of two things, and telling them apart is what this
 * phase is about:
 *
 *   - the piece captured and can capture AGAIN: it stays up, the same piece is
 *     still selected, the turn does NOT pass, and `chain_sq` records where it
 *     is so compute_legal() asks gen_captures_from() from now on.
 *   - otherwise (a quiet move, a final capture, or a promotion): the piece
 *     parks and the turn passes - sel and cursor go to NO_SQ, and the next
 *     d-pad press re-seats the cursor on the new side's first legal square.
 *
 * THE TRAP: must_continue() sees only a board.  On the very hop that crowns a
 * man, apply_move() has already promoted it, so must_continue() finds a king
 * where a man was and reports "yes, this one can capture again" - which is
 * wrong, because a promotion ends the turn.  Only move_causes_promotion() on
 * the PRE-move board knows, which is why it is checked here, on `before`,
 * before must_continue() on `board` is trusted.  This exact bug shipped in
 * this repo once; the wrong form is:
 *     if (m.capture != NO_SQ && must_continue(&board, m.to))
 *
 * A no-op when (from, to) is not a move in the list - picking a piece up and
 * pressing A again without walking anywhere - which is why this searches the
 * list rather than indexing into it.  From the human it never runs with nothing
 * picked up: that caller only gets here from ST_TARGET.
 *
 * board = apply_move(...) and not a hand-rolled copy of the mask arithmetic:
 * tests/test_sim.c covers apply_move on the host, and a second implementation
 * living here would be covered by nothing at all. */
static void play(uint8_t from, uint8_t to)
{
    uint8_t i;
    for (i = 0; i < n_moves; i++) {
        Move m = moves[i];
        Board before;

        if (m.from != from || m.to != to) continue;

        before = board;
        board = apply_move(board, m);
        if (m.capture != NO_SQ && !move_causes_promotion(&before, m) &&
            must_continue(&board, m.to)) {
            chain_sq = m.to;
            sel = m.to;               /* the same piece is still up */
            cursor = m.to;
            state = ST_TARGET;        /* and its next hops are the targets */
            /* The next hops, and the cursor put on one of them, so the player
             * is not left on the piece with nothing to press.  The list has to
             * be recomputed here rather than at the top of the next iteration:
             * it still holds the moves from BEFORE this hop, and seating the
             * cursor out of that would put it on a square that is not on offer
             * - which A would then ignore, the very thing being fixed. */
            compute_legal();
            seat_cursor();
        } else {
            chain_sq = NO_SQ;
            sel = NO_SQ;
            cursor = NO_SQ;
            side = (uint8_t)(side ^ 1u);

            /* The turn really is over - a quiet move, a final capture, or a
             * hop that promoted - so this is a turn boundary, and the only
             * place the end-of-game question belongs.  Mid-chain it would be
             * plain wrong twice over: the chain has not ended at all, and
             * gen_moves() over a whole side is not the rule in force there.
             *
             * The side to move has just been handed the turn, so it is the one
             * that can lose here - asking about the side that just moved would
             * end the game on every move.  side_to_move_has_moves() is
             * gen_moves() > 0, so "no moves" covers both an empty side and one
             * that is merely boxed in, and it folds in mandatory capture for
             * free: a side whose only piece can capture has a move. */
            state = side_to_move_has_moves(&board, side) ? ST_SELECT : ST_OVER;
        }
        return;
    }
}

static void new_game(void);

static void handle_input(void)
{
    uint8_t dir;

    /* The game is over: the board is frozen and the only thing left to press
     * is a restart.  START and B both do it, and both do it through the very
     * new_game() the title screen uses - a restart is not a second
     * initialisation path that can quietly drift from the first. */
    if (state == ST_OVER) {
        if (pressed & (J_START | J_B)) new_game();
        return;
    }

    dir = dir_of(keys);

    /* A fresh press steps once, at once; a direction held for REPEAT_DIV turns
     * of the loop steps again, and keeps stepping for as long as it is held.
     * The count is TURNS SINCE THE PRESS, not the frame counter: `frame` is
     * free-running, so testing `(frame & (REPEAT_DIV - 1)) == 0` fires whenever
     * the counter happens to land on a multiple - which a press can hit on its
     * very next turn.  That made one tap step two squares, about one tap in
     * eight, and more often once the loop got faster.  Counting from the press
     * makes a tap exactly one step, always. */
    if (dir != NO_SQ) {
        if ((pressed & (J_UP | J_DOWN | J_LEFT | J_RIGHT)) ||
            ++repeat >= REPEAT_DIV) {
            if (state == ST_TARGET) cycle_target();   /* pick a destination */
            else                    cursor = walk(cursor, dir);
            repeat = 0;
        }
    } else {
        repeat = 0;             /* nothing held: the next press starts clean */
    }

    if (pressed & J_A) {
        if (state == ST_SELECT) {
            /* A piece that cannot legally move (a man left behind by mandatory
             * capture, or one boxed in) is not selectable, and A on it does
             * nothing at all - not even a flicker of a highlight. */
            if (landable(cursor)) {
                sel = cursor;
                state = ST_TARGET;
                compute_legal();              /* narrow to this piece */
                seat_cursor();                /* and stand on a destination */
            }
        } else {
            play(sel, cursor);                /* A on a destination: move it */
        }
    }
    /* B drops a piece you picked up.  Mid-chain there is nothing to drop: the
     * capture is already on the board and cannot be taken back, so "refresh"
     * here would clear chain_sq and hand the turn back to gen_moves() - the
     * mandatory continuation quietly gone, which is the second trap. */
    if (pressed & J_B) {
        if (state == ST_TARGET && chain_sq == NO_SQ) refresh_moves();
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

/* Same, but stacked down a column of the HUD strip: it walks the buffer by row
 * rather than along one. */
static void text_v(uint8_t col, uint8_t row, const char *s)
{
    uint8_t *p = &hudbuf[row * HUD_TW + col];
    while (*s) { *p = glyph(*s++); p += HUD_TW; }
}

/* ------------------------------------------------------------- what's drawn
 * Still no state of the screen's own: this only remembers the values the LAST
 * build was made from, so a frame whose build would write the same bytes again
 * can skip writing them.  Measured on the DMG, rebuilding and blitting the
 * whole screen is about five of the eight emulator frames an iteration used to
 * take, and doing that on the frames where the player is only thinking put a
 * 135 ms gap between input samples - long enough that an ordinary tap fell
 * between two of them and did nothing at all.  That is the whole of the
 * complaint this exists to fix.
 *
 * It is not a cache of a RULE.  compute_legal() is still called from scratch
 * every frame, and the move list it fills is never carried over; what is being
 * skipped here is writing bytes that are already in VRAM.  Every value the
 * build_* functions read is in this list, so the check cannot miss a change:
 * add a global to the screen and it has to be added here too.  The probe's
 * tilemap checks are what pin it - they read the screen and the statics
 * together. */
static Board   drawn_board;
static uint8_t drawn_side, drawn_state, drawn_sel, drawn_chain, drawn_mode;
static uint8_t drawn_once;      /* 0 until the first build has happened */

static uint8_t screen_stale(void)
{
    /* The screen has to be painted once before "nothing changed" means
     * anything: at boot every static above is zero, and so are `board`, `side`,
     * `state`, `sel` and `chain_sq`, so the comparison would call the title
     * screen already drawn and leave the tilemap as the PPU powered up with.
     * That is not hypothetical - it is what the first version of this did, and
     * the probe's title-screen check is what caught it. */
    if (!drawn_once) return 1;
    if (drawn_board.black != board.black) return 1;
    if (drawn_board.white != board.white) return 1;
    if (drawn_board.kings != board.kings) return 1;
    if (drawn_side != side || drawn_state != state) return 1;
    if (drawn_sel != sel || drawn_chain != chain_sq) return 1;
    /* mode_1p too: the title screen spells it, and SELECT can flip it while
     * nothing else above has moved, which is exactly the frame this gate would
     * otherwise skip. */
    if (drawn_mode != mode_1p) return 1;
    return 0;
}

static void screen_drawn(void)
{
    drawn_board = board;
    drawn_side  = side;
    drawn_state = state;
    drawn_sel   = sel;
    drawn_chain = chain_sq;
    drawn_mode  = mode_1p;
    drawn_once  = 1;
}

static void build_title(void)
{
    uint16_t i;
    for (i = 0; i < BOARD_TW * BOARD_TH; i++) mapbuf[i] = T_BLANK;
    text(TEXT_COL, TITLE_ROW, "GB DRAUGHTS");
    /* "2 PLAYERS" is nine tiles and "1 PLAYER" eight, so one column cannot
     * centre both; TEXT_COL centres the shorter and leaves the longer half a
     * tile left of centre, which is what a font this size can do. */
    text(TEXT_COL, MODE_ROW, mode_1p ? "1 PLAYER" : "2 PLAYERS");
    text(TEXT_COL, STATUS_ROW, "PRESS START");
}

/* The result, over the middle of the board in ST_OVER.  `side` is the side to
 * move, which is the side that has just been found to have no move at all, so
 * the winner is the other one.  Derived from `side` rather than latched at the
 * moment of the loss: the screen is rebuilt every frame like everything else,
 * so a ternary is the whole of the state this needs. */
static void build_status(void)
{
    text(TEXT_COL, STATUS_ROW, side == SIDE_BLACK ? "WHITE WINS" : "BLACK WINS");
}

/* Whose turn it is, straight from `side` - and blank on the title screen, where
 * there is no turn yet.  In ST_OVER it still names `side`, which is the side
 * that has just been handed the turn and cannot use it: the HUD keeps its one
 * meaning ("the side to move"), the status row carries the result, and the two
 * together say exactly why the game ended. */
static void build_hud(void)
{
    uint16_t i;
    for (i = 0; i < HUD_TW * HUD_TH; i++) hudbuf[i] = T_BLANK;
    if (state != ST_TITLE) {
        text_v(HUD_COL, HUD_ROW0, side == SIDE_BLACK ? "BLACK" : "WHITE");
    }
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

/* The whole game screen, in one place: board, then the highlights under the
 * pieces, then the pieces, then the result on top if the game is over.  Both
 * the ordinary frame and the machine's THINKING frame come through here, so the
 * two can never draw a different screen from the same state. */
static void build_game(void)
{
    build_board();
    if (sel != NO_SQ) draw_highlights();
    draw_pieces();
    /* Over the top of the drawn pieces: the result is the one thing on the
     * board in ST_OVER, and the pieces are still worth seeing underneath it. */
    if (state == ST_OVER) build_status();
}

/* OAM coordinates are not screen coordinates: the DMG draws an 8x8 sprite
 * with its TOP-LEFT at (OAM_x - 8, OAM_y - 16), and move_sprite() writes OAM
 * raw.  So both offsets go in here, or the ring lands one square up-left of
 * the square it is on - and entirely off-screen (or hidden at OAM y = 0) on
 * the left column and in row 0.  Measured in PyBoy: OAM (48, 32) for
 * cell(2, 3) renders at (40, 16).  Parked at (0, 0), the hidden position. */
static void put_ring(uint8_t spr, uint8_t sq)
{
    if (sq == NO_SQ) {
        move_sprite(spr, 0, 0);
        return;
    }
    move_sprite(spr, (uint8_t)(16u * SQ_COL(sq) + 12u),
                     (uint8_t)(16u * SQ_ROW(sq) + 20u));
}

/* Follows the selection, so it is repositioned every frame like everything
 * else on the screen - the sprite is the one piece of the screen not made of
 * tiles, and a cursor that lags the thing it points at is worse than none.
 * Two rings: SPR_CURSOR at `cursor`, and - in ST_TARGET only - SPR_SEL over
 * the picked-up piece itself.  seat_cursor() puts the cursor on a landing
 * square the moment a piece is picked up, and the highlights show all of
 * them, so without its own ring the piece that will move is the one thing
 * on the board with no mark on it. */
static void move_cursor(void)
{
    put_ring(SPR_CURSOR, cursor);
    put_ring(SPR_SEL, state == ST_TARGET ? sel : (uint8_t)NO_SQ);
}

static void new_game(void)
{
    board = board_init();
    side = SIDE_BLACK;        /* black moves first, as at the start position */
    /* Parked, not placed: the main loop seats it on a square this side can
     * actually use.  Square 0 holds a back-rank man with no legal move, so
     * starting there means the first press of a new game does nothing. */
    cursor = NO_SQ;
    refresh_moves();
}

/* Whose turn this is, and whether a person or the machine plays it.
 *
 * Black moves first, so in 1 PLAYER the human is black and "white to move" IS
 * "the machine's turn" - there is no second flag to keep in step with `side`.
 * ST_SELECT and ST_TARGET are the live states; ST_OVER and ST_TITLE are not
 * turns at all, and asking this there would let the machine play on at a
 * finished game.
 *
 * ST_TARGET counts because the machine's move may be the first hop of a chain:
 * play() leaves the piece up exactly as it does for a human, and the next hop
 * is the machine's too. */
static uint8_t machine_to_move(void)
{
    return (uint8_t)(mode_1p && side == SIDE_WHITE &&
                     (state == ST_SELECT || state == ST_TARGET));
}

/* The machine's turn: say so, think, and play - through play(), the same path
 * a human's move takes.
 *
 * THINKING is BLITTED before the search, not merely built.  The search is a
 * blocking call, so without this the player would sit looking at the previous
 * frame for as long as it takes, with nothing on screen to say the game is
 * alive rather than hung.  The status row is where it goes - the same line the
 * result banner uses, and the game over and the machine thinking never
 * overlap.  The blit needs its own wait_vbl_done(): VRAM is only writable in
 * VBlank, and this write happens in the middle of a frame, not at the end of
 * one like the main loop's.
 *
 * The move itself is one hop.  A chain's next hop arrives on the next frame,
 * through this same function, because play() has left chain_sq set - there is
 * no "AI chain" here to fork the promotion rule. */
static void ai_turn(void)
{
    Move m;

    /* The move list is rebuilt HERE rather than taken from the loop's own call
     * at the top of this iteration.  On the frame the human's move handed the
     * turn over, that list is still black's - the loop has to fill it before
     * handle_input() for the selection's sake, which is exactly why it cannot
     * be reused after - and play() would look for the machine's move in it and
     * find nothing, leaving the machine to think the whole thing over again on
     * the next frame. */
    compute_legal();

    build_game();
    text(TEXT_COL, STATUS_ROW, "THINKING");
    build_hud();
    wait_vbl_done();
    set_bkg_tiles(0, 0, BOARD_TW, BOARD_TH, mapbuf);
    set_bkg_tiles(HUD_X, 0, HUD_TW, HUD_TH, hudbuf);
    screen_drawn();

    /* search_best() writes *out only when it has a move to report - a loss and
     * a depth of 0 leave it alone (P9's contract, pinned host-side by
     * test_search_depth_zero_leaves_out_alone).  So the move starts as "no
     * move" and the returned score is checked as well: both say the same thing
     * here, and between them the machine cannot play an indeterminate square -
     * which is what an uninitialised out would leave it doing. */
    m.from = m.to = m.capture = NO_SQ;

    if (chain_sq != NO_SQ) {
        /* Mid-chain the rule is already decided: chain_sq is set, and the move
         * list is exactly the piece on it.  Searching HERE would be wrong twice
         * over - gen_moves() would offer a different piece's move, which
         * play() then would not find, and the mandatory continuation would be
         * gone.  A hop off the list the screen is already showing is what the
         * human's path does too. */
        if (n_moves) m = moves[0];
    } else if (search_best(board, SIDE_WHITE, ai_depth, arena, &m) <= -AI_WIN_SCORE) {
        m.from = NO_SQ;                 /* no move to report: nothing to play */
    }

    if (m.from != NO_SQ) play(m.from, m.to);
}

/* ------------------------------------------------------------------ main */
void main(void)
{
    DISPLAY_OFF;
    /* BG tiles and OBJ tiles are different VRAM, so the cursor is its own
     * little array rather than a slice of the BG bank. */
    set_bkg_data(0, GFX_TILE_COUNT, gfx_tiles);
    set_sprite_data(0, GFX_SPRITE_COUNT, gfx_sprites);
    set_sprite_tile(SPR_CURSOR, 0);
    set_sprite_tile(SPR_SEL, 0);    /* both rings are the one ring tile */
    put_ring(SPR_CURSOR, NO_SQ);    /* parked at OAM (0, 0) = hidden */
    put_ring(SPR_SEL, NO_SQ);
    BGP_REG  = 0xE4;                /* 3 2 1 0, darkest to lightest */
    OBP0_REG = 0xE4;
    SPRITES_8x8;
    SHOW_BKG;
    SHOW_SPRITES;                   /* nothing marked yet: both rings parked
                                     * until START enters ST_SELECT */
    DISPLAY_ON;

    state = ST_TITLE;
    prev_keys = 0;
    frame = 0;

    while (1) {
        uint8_t stale;
        keys = joypad();
        pressed = keys & ~prev_keys;
        prev_keys = keys;
        frame++;

        if (state == ST_TITLE) {
            /* SELECT is the mode toggle, and the title is the only place it
             * exists: a game never swaps opponent halfway through. */
            if (pressed & J_SELECT) mode_1p = (uint8_t)(mode_1p ^ 1u);
            if (pressed & J_START) {
                new_game();
                state = ST_SELECT;
            }
        } else {
            /* This frame's legal moves, from this frame's board and this
             * frame's side, before input or the screen reads any of it.  The
             * machine's turn needs this list as much as the human's does, so it
             * is filled either way. */
            compute_legal();
            /* Everything below is the HUMAN's half of the frame, and it is
             * skipped whole on the machine's turn: no cursor seating, no
             * walking, no A.  The cursor stays parked at NO_SQ, which is where
             * play() leaves it at the end of a turn, so the ring is off the
             * board while the machine plays - the machine never drives the
             * cursor, and a human pressing A must not play the machine's
             * piece.  START and B go with it: on the machine's turn they are
             * dropped, which lasts as long as one blocking think (P11). */
            if (!machine_to_move()) {
                /* A parked cursor has nowhere to be: the first frame of a game,
                 * and the frame a turn passes on.  Seat it on the first square
                 * the side to move can actually use, so the player never has to
                 * press a direction just to find a piece.  It is set after
                 * compute_legal() because that is what fills the move list. */
                if (state == ST_SELECT && cursor == NO_SQ && n_moves)
                    cursor = moves[0].from;
                handle_input();
            }
        }

        /* The machine's turn - which the human's move may have just handed it,
         * so this is after handle_input(), not before: the reply to a move
         * belongs to the same iteration the move was made in. */
        if (machine_to_move()) ai_turn();

        /* Build the frame first, blit it second: the VRAM writes then land in
         * the VBlank window instead of being dropped by the PPU.  Skipped
         * entirely on a frame that would build what is already there - see
         * screen_stale(). */
        stale = screen_stale();
        if (stale) {
            /* `moves` was filled at the top of this iteration, from the
             * position BEFORE handle_input ran - that is the order the
             * selection needs, so it cannot move - and on a frame where input
             * changed the board it is now one position out of date.  The
             * highlights are read off it, so without this they light the square
             * the piece has just LEFT: mid-chain, a hop from 13 to 22 leaves
             * `moves` still holding 13->22 and draw_highlights() paints the
             * destination of a move that has already been played.  A loop that
             * redrew every frame never had to say this out loud - the next
             * frame's compute_legal() was the repair, one frame late. */
            compute_legal();
            if (state == ST_TITLE) build_title();
            else                   build_game();
            build_hud();
        }
        /* The rings are four OAM bytes and are written every frame whatever
         * the tilemap is doing, so a write the PPU drops mid-scanline is
         * corrected on the next one rather than leaving a ring behind.  Not
         * on the title screen, where there is nothing to mark. */
        if (state != ST_TITLE) move_cursor();

        /* One wait per iteration, whether or not anything was drawn: `frame`
         * divides REPEAT_DIV, so it has to keep counting frames.  A held
         * direction that repeated once per loop turn would cross the whole
         * board on one press. */
        wait_vbl_done();

        if (stale) {
            set_bkg_tiles(0, 0, BOARD_TW, BOARD_TH, mapbuf);
            set_bkg_tiles(HUD_X, 0, HUD_TW, HUD_TH, hudbuf);
            screen_drawn();
        }
    }
}
