# Gomoku Rules For This Codebase

This document describes the rules implemented by this project, based on the current Python engine and CLI behavior.

## Symbols

- `X` = Black
- `O` = White
- `.` = Empty

In the CLI, Black and White are shown as `X` and `O`.

## Objective

The goal is to be the first player to make five stones in a continuous line.

A winning line may be:

- Horizontal
- Vertical
- Diagonal

## Board

- The default board size in this project is `19 x 19`.
- The CLI also allows other square sizes through `--size`.
- Moves are entered as `x,y` coordinates.

## Turn Order

- By default, the human player moves first.
- The `--ai-first` flag lets the AI move first instead.
- Players alternate turns and place exactly one stone each turn.

## Placement Rules

- A stone may only be placed on an empty position.
- Stones are never moved.
- Stones are never removed.
- There is no capture rule in this code.

## Winning

A player wins as soon as they form a line of five stones.

Important detail from this code:

- A line of `5 or more` counts as a win.
- That means an overline, such as `6` or more in a row, also wins.

Examples:

- `X X X X X`
- `O O O O O`
- `X X X X X X`

Because the engine searches for any consecutive run of five inside a line, longer runs also count as winning.

## Draw

If the board becomes full and neither side has made five in a row, the game is a draw.

## Rule Variant Implemented Here

This project implements **Free-style Gomoku**.

Why:

- Five in a row wins.
- More than five in a row also wins.
- There are no forbidden-move restrictions for Black.
- There are no Renju restrictions such as:
  - double-three bans
  - double-four bans
  - overline bans

## Rule Variants Not Implemented

The code does **not** implement these stricter variants:

### Standard Gomoku

Not implemented, because Standard Gomoku may require **exactly five** and may reject overlines.

### Renju

Not implemented, because this code does not check forbidden Black patterns such as:

- Overlines
- Double-threes
- Double-fours

## Opening Rules

- There is no special tournament opening rule in the engine.
- There is no swap rule.
- There is no forced opening pattern.
- The game simply starts from an empty board, and the first side places one stone.

## Simple Summary

1. `X` and `O` take turns placing one stone on an empty position.
2. By default, the human goes first unless `--ai-first` is used.
3. The first side to make `five or more` in a row wins.
4. If the board fills with no winner, the game is a draw.
5. This code matches **Free-style Gomoku**, not Renju or exact-five Standard Gomoku.
