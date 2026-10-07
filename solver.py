"""
solver.py - Minesweeper as a logic agent (Predicate Logic + Z3)

This module ONLY does logical reasoning. It knows nothing about how the game
is stored, where the real mines are, or how the UI looks.

Input : the board the player can SEE.
        A 2-D list where each entry is either
          - None  -> the cell is still hidden (flags are NOT trusted, so they
                     are treated as hidden too), or
          - 0..8  -> the cell is revealed and shows that number.

Output: for every hidden cell, one of
          "MINE"    - KB |= Mine(c)       (true in every consistent world)
          "SAFE"    - KB |= Not Mine(c)   (false in every consistent world)
          "UNKNOWN" - neither can be proved

How the board becomes logic
---------------------------
* Every cell (r, c) becomes one Boolean variable  Mine_r_c.
* For every revealed cell showing n we add two facts to the knowledge base:
      Not Mine(r, c)                        -- you can't reveal a mine and live
      Sum( If(Mine(nb), 1, 0) ) == n        -- exactly n neighbours are mines
* To ask "must (r, c) be a mine?" we use PROOF BY REFUTATION:
      KB |= Mine(c)   <=>   KB and Not Mine(c)   is UNSATISFIABLE
"""

from z3 import Bool, If, Not, Solver, Sum, sat, unsat


class SolverError(ValueError):
    """Raised when the board given to the solver is malformed or contradictory."""


# --------------------------------------------------------------------------- #
# Neighbours
# --------------------------------------------------------------------------- #
def neighbours(r, c, rows, cols):
    """
    All valid cells touching (r, c), including diagonals, never (r, c) itself.
    Corners have 3 neighbours, edges 5, interior cells 8.

    (game.py has its own copy on purpose: game logic and solver logic are kept
    completely independent of each other.)
    """
    result = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue  # a cell is not its own neighbour
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols:  # stay on the board
                result.append((nr, nc))
    return result


# --------------------------------------------------------------------------- #
# Entailment by refutation
# --------------------------------------------------------------------------- #
def entails(kb, formula):
    """
    Does the knowledge base logically entail `formula`?

    Assume the opposite (Not formula) and see whether the KB breaks.
    push()/pop() make the assumption temporary, so the KB is left unchanged.
    """
    kb.push()
    kb.add(Not(formula))
    result = kb.check() == unsat  # UNSAT  =>  formula is forced to be true
    kb.pop()
    return result


# --------------------------------------------------------------------------- #
# Knowledge base
# --------------------------------------------------------------------------- #
class MinesweeperKB:
    def __init__(self, board):
        self.board = self._validate(board)
        self.rows = len(self.board)
        self.cols = len(self.board[0])

        # One Boolean variable per cell: Mine_r_c is True if that cell is a mine.
        self.mine = {
            (r, c): Bool(f"Mine_{r}_{c}")
            for r in range(self.rows)
            for c in range(self.cols)
        }

        self.kb = Solver()          # the knowledge base used for MINE/SAFE queries
        self.constraints = {}       # name -> Z3 formula (kept so we can explain proofs)
        self.labels = {}            # name -> ("safe" | "clue", row, col)
        self.frontier = set()       # hidden cells that touch at least one number

        self._build()

        # A real board is always consistent. If not, the input was bogus.
        if self.kb.check() != sat:
            raise SolverError("The numbers on this board contradict each other.")

    # -- validation ---------------------------------------------------------
    @staticmethod
    def _validate(board):
        if not isinstance(board, list) or not board:
            raise SolverError("Board must be a non-empty list of rows.")
        width = None
        for row in board:
            if not isinstance(row, list) or not row:
                raise SolverError("Every board row must be a non-empty list.")
            if width is None:
                width = len(row)
            elif len(row) != width:
                raise SolverError("All board rows must have the same length.")
            for v in row:
                if v is not None and (isinstance(v, bool) or not isinstance(v, int)
                                      or not 0 <= v <= 8):
                    raise SolverError("Cells must be null (hidden) or an integer 0-8.")
        if len(board) * width < 2:
            raise SolverError("Board is too small.")
        rows, cols = len(board), width
        for r in range(rows):
            for c in range(cols):
                v = board[r][c]
                if v is not None and v > len(neighbours(r, c, rows, cols)):
                    raise SolverError(f"Cell ({r},{c}) shows {v}, more than its neighbour count.")
        return board

    # -- building the knowledge base ---------------------------------------
    def _add(self, name, label, formula):
        self.constraints[name] = formula
        self.labels[name] = label
        self.kb.add(formula)

    def _build(self):
        for r in range(self.rows):
            for c in range(self.cols):
                n = self.board[r][c]
                if n is None:
                    continue  # hidden cell: no information about it directly

                nbs = neighbours(r, c, self.rows, self.cols)

                # Fact 1: a revealed cell is not a mine.
                self._add(f"safe_{r}_{c}", ("safe", r, c), Not(self.mine[(r, c)]))

                # Fact 2: exactly n of its neighbours are mines.
                # If(m, 1, 0) turns a Boolean into 0/1 so Sum can count mines.
                self._add(
                    f"clue_{r}_{c}",
                    ("clue", r, c),
                    Sum([If(self.mine[nb], 1, 0) for nb in nbs]) == n,
                )

                for nb in nbs:
                    if self.board[nb[0]][nb[1]] is None:
                        self.frontier.add(nb)

    # -- inference ----------------------------------------------------------
    def classify(self, r, c):
        """Return "MINE", "SAFE" or "UNKNOWN" for one hidden cell."""
        # Optimisation: a hidden cell that touches no number appears in no
        # constraint, so Z3 can always make it either True or False.
        # The answer is UNKNOWN; no need to ask the solver.
        if (r, c) not in self.frontier:
            return "UNKNOWN"

        m = self.mine[(r, c)]
        if entails(self.kb, m):        # KB and Not Mine(c)  is UNSAT
            return "MINE"
        if entails(self.kb, Not(m)):   # KB and Mine(c)      is UNSAT
            return "SAFE"
        return "UNKNOWN"

    def analyse(self, explain=False):
        """Classify every hidden cell. Optionally attach an explanation."""
        results = []
        for r in range(self.rows):
            for c in range(self.cols):
                if self.board[r][c] is not None:
                    continue
                status = self.classify(r, c)
                item = {"row": r, "col": c, "status": status}
                if explain and status != "UNKNOWN":
                    item["explanation"] = self.explain(r, c, status)
                results.append(item)
        return results

    # -- explanations -------------------------------------------------------
    def _unsat_with(self, names, extra):
        """Is (the named constraints) and `extra` unsatisfiable?"""
        s = Solver()
        for n in names:
            s.add(self.constraints[n])
        s.add(extra)
        return s.check() == unsat

    def _minimal_core(self, extra):
        """
        Find a small set of constraints that, together with `extra`, are
        contradictory. This is exactly the set of facts the proof depends on.

        1. Ask Z3 for an unsat core (a subset of the tracked constraints).
        2. Shrink it: drop any constraint whose removal keeps it UNSAT.
        """
        s = Solver()
        for name, formula in self.constraints.items():
            s.assert_and_track(formula, name)
        s.add(extra)
        if s.check() != unsat:
            raise SolverError("Cannot explain a conclusion that is not entailed.")
        core = [str(b) for b in s.unsat_core()]
        for name in list(core):
            trial = [n for n in core if n != name]
            if self._unsat_with(trial, extra):
                core = trial
        return core

    def explain(self, r, c, status):
        """
        Build a human-readable proof for a MINE/SAFE conclusion, based on the
        constraints Z3 actually needed (not a hand-written rule).
        """
        m = self.mine[(r, c)]
        claim = m if status == "MINE" else Not(m)
        opposite = "SAFE" if status == "MINE" else "a MINE"

        # Refutation: Not(claim) + the core constraints cannot all be true.
        core = self._minimal_core(Not(claim))
        clues = sorted(self.labels[n][1:] for n in core if self.labels[n][0] == "clue")

        def fmt(cells):
            return ", ".join(f"({a},{b})" for a, b in cells) or "none"

        lines = [f"Cell ({r},{c}) \u2192 {status} (proved)", "", "Reason:"]
        info = []
        for cr, cc in clues:
            n = self.board[cr][cc]
            nbs = neighbours(cr, cc, self.rows, self.cols)
            hidden = [x for x in nbs if self.board[x[0]][x[1]] is None]
            shown = [x for x in nbs if self.board[x[0]][x[1]] is not None]
            info.append((n, hidden))
            lines.append(f"\u2022 Cell ({cr},{cc}) shows {n}, so exactly {n} of its "
                         f"neighbours are mines: {fmt(nbs)}.")
            if shown:
                lines.append(f"  Revealed neighbours are safe: {fmt(shown)}.")
            lines.append(f"  Hidden candidates: {fmt(hidden)}.")

        lines.append("")
        if len(clues) == 1:
            n, hidden = info[0]
            if n == len(hidden):
                lines.append(f"It needs {n} mine(s) and has only {len(hidden)} hidden "
                             f"neighbour(s), so every hidden neighbour is a mine, "
                             f"including ({r},{c}).")
            elif n == 0:
                lines.append(f"The clue is 0, so none of its neighbours is a mine; "
                             f"({r},{c}) is safe.")
            else:
                lines.append(f"Assuming ({r},{c}) is {opposite} contradicts this clue.")
        else:
            lines.append(f"Z3 combined these {len(clues)} clues. Assuming ({r},{c}) is "
                         f"{opposite} leaves no way to place mines that satisfies all of "
                         f"them (UNSAT).")
        lines.append(f"Therefore ({r},{c}) must be {'a MINE' if status == 'MINE' else 'SAFE'}.")

        return {"text": "\n".join(lines), "clues": [list(x) for x in clues]}


def analyse_board(board, explain=False):
    """Convenience wrapper: build the KB for `board` and classify hidden cells."""
    return MinesweeperKB(board).analyse(explain=explain)
