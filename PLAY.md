# How to play GB-Draughts

English draughts (checkers) for the Game Boy — the board game, the standard
rules, with an opponent built in.

`draughts.gb` is the whole game. Load it in an emulator, or flash it to a
cartridge, and it runs on an original Game Boy, a Game Boy Color, or any
emulator. On a Super Game Boy the board is framed by a decorative border; on a
plain Game Boy you just get the board.

## Controls

| Button | What it does |
|--------|--------------|
| START  | start a game — from the title screen, and from a finished game |
| SELECT | on the title screen, switch between **1 PLAYER** and **2 PLAYERS**. During a game, take a turn back |
| d-pad  | move the cursor; hold to keep moving. Once a piece is in your hand, cycle that piece's landing squares |
| A      | pick up the piece under the cursor; press again to play the move |
| B      | put a picked-up piece back down |

The game boots into **1 PLAYER** and you play black, so you move first. SELECT
on the title screen is the only thing that changes that, and the title says
which mode is coming.

## Playing a move

Every move is two presses of A: one to pick a piece up, one to put it down.
Picking a piece up seats the cursor straight onto a square it can go to, so the
second press always has somewhere to land — you never have to walk the cursor
from the piece to its destination.

The d-pad walks the cursor when nothing is picked up. It follows the diagonals,
and it steps *over* squares no piece can use, so it settles only where you can
actually do something.

Once a piece is in your hand, the d-pad changes job: each press moves the cursor
to the *next* square that piece can reach and wraps around. Which arrow you
press makes no difference — the landing squares of a piece are usually not
neighbours of each other, so "walk that way" wouldn't mean anything between
them. Hold to cycle faster.

The d-pad is rotated 45 degrees, because a checker's neighbour is diagonal:

    UP    up-left        RIGHT  up-right
    LEFT  down-left      DOWN   down-right

## The rules

- Standard 8x8 board, 32 playable squares, 12 pieces each, on the dark squares.
- Men move **one square diagonally forward**. Only kings move backwards.
- **Capturing is compulsory.** If any of your pieces can capture, only captures
  are legal that turn — a quiet move is not offered instead.
- **Multi-jumps**: if the piece you just captured with can capture again, it
  must, and it stays in your hand until the chain ends. It may not fall back to
  a different piece or a plain move.
- A man that reaches the far row becomes a **king**, and moves one square
  diagonally in any direction. There are no "flying" kings here — a king still
  moves one square at a time, it just isn't limited to forward. If it crowns
  part-way through a chain, the chain stops there.
- You **lose when you have no legal move at all** — no pieces left, or every
  piece blocked. The status row names the winner.
- There is **no draw rule**. Two players who only shuffle can keep going
  forever; nothing will call it.

## The machine

In 1 PLAYER the machine plays white. It moves by the same rules you do, so
mandatory capture and chains apply to it exactly as they do to you, and its
pieces move themselves the same way yours do. It takes its turn in one go: the
status row says **THINKING** while it decides, and the game is frozen for those
few seconds. Pressing buttons during that pause does nothing — try again a
moment later. Allow a few seconds, longer in a crowded midgame.

## Taking a move back

SELECT takes a turn back:

- In **2 PLAYERS**, one press undoes the last move.
- In **1 PLAYER**, one press undoes *your* last move — the machine's reply goes
  back with it, so it cannot simply replay the position you just rejected.
- A capture chain rewinds as a whole: press it mid-chain and you get the
  position before the chain's first jump, not one jump of it.
- The last 32 turns are remembered. A game longer than that keeps playing
  fine — it just stops remembering older turns.

It does nothing before anyone has moved, and nothing on a finished game.

## What you're looking at

- The small ring in the middle of a square is the **cursor** — where your next
  press lands, not where a piece is going.
- A second ring, on a piece you have picked up, shows which piece is in your
  hand. In a chain it follows the piece from square to square.
- The word down the side — **BLACK** or **WHITE** — is whose turn it is.
  Black always moves first. The board is not rotated: black sits at the top and
  moves down the screen, white sits at the bottom and moves up.
- **THINKING** across the board — the machine is deciding (see above).
- **`<colour> WINS`** — the game is over. START or B deals a new one.

## If a piece won't move

Almost always compulsory capture: if *any* of your pieces can capture, nothing
else is legal that turn. The move is not missing, it is forbidden. Look for the
jump you owe first.
