"""
game.py - Minesweeper rules and board state.

Knows NOTHING about Z3. It creates the board, places mines, counts adjacent
mines, handles reveal/flag, and decides win/lose.

Mines are placed lazily on the FIRST reveal so that the first click (and, when
possible, its neighbours) is always safe.
"""

import random
from dataclasses import dataclass

MIN_SIZE = 2
MAX_SIZE = 30


class GameError(ValueError):
    """Raised for invalid moves or invalid game settings."""


@dataclass
class Cell:
    is_mine: bool = False
    is_revealed: bool = False
    is_flagged: bool = False
    adjacent_mines: int = 0


class Game:
    def __init__(self, rows=9, cols=9, mines=10):
        if not (MIN_SIZE <= rows <= MAX_SIZE and MIN_SIZE <= cols <= MAX_SIZE):
            raise GameError(f"Rows and columns must be between {MIN_SIZE} and {MAX_SIZE}.")
        if not (1 <= mines <= rows * cols - 1):
            raise GameError(f"Mines must be between 1 and {rows * cols - 1}.")
        self.rows, self.cols, self.mines = rows, cols, mines
        self.cells = [[Cell() for _ in range(cols)] for _ in range(rows)]
        self.status = "ready"       # ready -> playing -> won | lost
        self.exploded = None        # (r, c) of the mine the player hit
        self.mines_placed = False
        self.revealed_count = 0

    # -- helpers ------------------------------------------------------------
    @property
    def is_over(self):
        return self.status in ("won", "lost")

    def neighbours(self, r, c):
        """All valid cells around (r, c); never (r, c) itself."""
        out = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if (dr, dc) == (0, 0):
                    continue
                nr, nc = r + dr, c + dc
                if 0 <= nr < self.rows and 0 <= nc < self.cols:
                    out.append((nr, nc))
        return out

    def check_coords(self, r, c):
        if not (0 <= r < self.rows and 0 <= c < self.cols):
            raise GameError("Cell is outside the board.")

    # -- board generation ---------------------------------------------------
    def _place_mines(self, safe_r, safe_c):
        """Randomly place mines, then compute every cell's adjacent-mine count."""
        # Keep the first click and its neighbours mine-free so it opens up.
        excluded = {(safe_r, safe_c), *self.neighbours(safe_r, safe_c)}
        candidates = [(r, c) for r in range(self.rows) for c in range(self.cols)
                      if (r, c) not in excluded]
        if len(candidates) < self.mines:  # crowded board: only protect the clicked cell
            candidates = [(r, c) for r in range(self.rows) for c in range(self.cols)
                          if (r, c) != (safe_r, safe_c)]
        for r, c in random.sample(candidates, self.mines):
            self.cells[r][c].is_mine = True

        for r in range(self.rows):
            for c in range(self.cols):
                if not self.cells[r][c].is_mine:
                    self.cells[r][c].adjacent_mines = sum(
                        self.cells[nr][nc].is_mine for nr, nc in self.neighbours(r, c))
        self.mines_placed = True

    # -- moves --------------------------------------------------------------
    def reveal(self, r, c):
        """Reveal a cell. Returns how many cells were opened."""
        self.check_coords(r, c)
        if self.is_over:
            raise GameError("The game is over. Start a new game.")
        cell = self.cells[r][c]
        if cell.is_revealed or cell.is_flagged:
            return 0  # flagged cells are protected from accidental clicks

        if not self.mines_placed:
            self._place_mines(r, c)
            self.status = "playing"

        if cell.is_mine:
            cell.is_revealed = True
            self.exploded = (r, c)
            self.status = "lost"
            return 0

        # Flood fill (iterative): a 0 opens all its neighbours, recursively.
        opened, stack = 0, [(r, c)]
        while stack:
            cr, cc = stack.pop()
            cur = self.cells[cr][cc]
            if cur.is_revealed or cur.is_flagged:
                continue
            cur.is_revealed = True
            opened += 1
            if cur.adjacent_mines == 0:
                stack.extend(self.neighbours(cr, cc))

        self.revealed_count += opened
        if self.revealed_count == self.rows * self.cols - self.mines:
            self.status = "won"  # every safe cell is open (flags don't matter)
        return opened

    def toggle_flag(self, r, c):
        self.check_coords(r, c)
        if self.is_over:
            raise GameError("The game is over. Start a new game.")
        cell = self.cells[r][c]
        if not cell.is_revealed:
            cell.is_flagged = not cell.is_flagged

    # -- views --------------------------------------------------------------
    def visible_board(self):
        """What the solver may see: None = hidden (flags ignored), int = number."""
        return [[cell.adjacent_mines if cell.is_revealed else None for cell in row]
                for row in self.cells]

    def public_view(self):
        """
        Everything the browser is allowed to know. Real mine positions are only
        included after the game has ended.
        """
        flags = 0
        grid = []
        for r in range(self.rows):
            row = []
            for c in range(self.cols):
                cell = self.cells[r][c]
                if cell.is_revealed and not cell.is_mine:
                    out = {"state": "revealed", "n": cell.adjacent_mines}
                elif self.status == "lost":
                    if (r, c) == self.exploded:
                        out = {"state": "exploded"}
                    elif cell.is_mine:
                        out = {"state": "flagged" if cell.is_flagged else "mine"}
                    elif cell.is_flagged:
                        out = {"state": "wrong_flag"}
                    else:
                        out = {"state": "hidden"}
                elif self.status == "won":
                    out = {"state": "flagged"}  # all remaining hidden cells are mines
                else:
                    out = {"state": "flagged" if cell.is_flagged else "hidden"}
                if out["state"] in ("flagged", "wrong_flag"):
                    flags += 1
                row.append(out)
            grid.append(row)

        remaining = 0 if self.status == "won" else self.mines - flags
        return {
            "rows": self.rows, "cols": self.cols, "mines": self.mines,
            "status": self.status, "mines_remaining": remaining, "cells": grid,
        }
