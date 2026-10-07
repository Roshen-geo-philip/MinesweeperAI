"""
app.py - Flask application: connects the browser, game.py and solver.py.

Endpoints (all JSON):
  POST /api/new      {rows, cols, mines}          -> start a game
  POST /api/reveal   {game_id, row, col}          -> reveal a cell
  POST /api/flag     {game_id, row, col}          -> toggle a flag
  POST /api/solve    {board, explain}             -> Z3 analysis (stateless)
  POST /api/ai-move  {game_id, allow_guessing}    -> let the logic agent play

Real mine positions are never sent to the browser before the game ends.
"""

import threading
import uuid
from collections import OrderedDict

from flask import Flask, jsonify, render_template, request

from game import MAX_SIZE, MIN_SIZE, Game, GameError
from solver import SolverError, analyse_board

app = Flask(__name__)

GAMES = OrderedDict()   # game_id -> Game (in memory; fine for a local demo)
MAX_GAMES = 200
# Z3's default context is not thread-safe, and Flask's dev server is threaded.
Z3_LOCK = threading.Lock()


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify({"error": err.message}), err.status


@app.errorhandler(GameError)
@app.errorhandler(SolverError)
def handle_domain_error(err):
    return jsonify({"error": str(err)}), 400


# -- input validation helpers ----------------------------------------------
def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object.")
    return data


def int_field(data, key, lo, hi, default=None):
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(f"'{key}' must be an integer.")
    if not lo <= value <= hi:
        raise ApiError(f"'{key}' must be between {lo} and {hi}.")
    return value


def get_game(data):
    game = GAMES.get(data.get("game_id"))
    if game is None:
        raise ApiError("Unknown game. Start a new game.", 404)
    return game


def state_of(game, game_id):
    view = game.public_view()
    view["game_id"] = game_id
    return view


# -- routes -----------------------------------------------------------------
@app.get("/")
def index():
    return render_template("index.html", min_size=MIN_SIZE, max_size=MAX_SIZE)


@app.post("/api/new")
def api_new():
    data = body()
    rows = int_field(data, "rows", MIN_SIZE, MAX_SIZE, 9)
    cols = int_field(data, "cols", MIN_SIZE, MAX_SIZE, 9)
    mines = int_field(data, "mines", 1, rows * cols - 1, 10)
    game = Game(rows, cols, mines)
    game_id = uuid.uuid4().hex
    GAMES[game_id] = game
    while len(GAMES) > MAX_GAMES:   # forget the oldest games
        GAMES.popitem(last=False)
    return jsonify({"state": state_of(game, game_id)})


@app.post("/api/reveal")
def api_reveal():
    data = body()
    game = get_game(data)
    game.reveal(int_field(data, "row", 0, MAX_SIZE - 1), int_field(data, "col", 0, MAX_SIZE - 1))
    return jsonify({"state": state_of(game, data["game_id"])})


@app.post("/api/flag")
def api_flag():
    data = body()
    game = get_game(data)
    game.toggle_flag(int_field(data, "row", 0, MAX_SIZE - 1), int_field(data, "col", 0, MAX_SIZE - 1))
    return jsonify({"state": state_of(game, data["game_id"])})


@app.post("/api/solve")
def api_solve():
    """
    The browser sends the board it can see:
        {"board": [[null, 1, 0, ...], ...], "explain": true}
    null = hidden, 0-8 = revealed number. We return MINE / SAFE / UNKNOWN for
    every hidden cell.
    """
    data = body()
    board = data.get("board")
    if not isinstance(board, list) or not (MIN_SIZE <= len(board) <= MAX_SIZE):
        raise ApiError("'board' must be a list of rows.")
    for row in board:
        if not isinstance(row, list) or not (MIN_SIZE <= len(row) <= MAX_SIZE):
            raise ApiError("Every board row must be a list.")
    explain = bool(data.get("explain", False))

    with Z3_LOCK:
        results = analyse_board(board, explain=explain)

    summary = {"MINE": 0, "SAFE": 0, "UNKNOWN": 0}
    for item in results:
        summary[item["status"]] += 1
    return jsonify({"results": results, "summary": summary})


@app.post("/api/ai-move")
def api_ai_move():
    """
    The agent loop:  build KB -> find provably SAFE cells -> reveal them ->
    new numbers appear -> build KB again -> repeat.
    It never guesses unless allow_guessing is true, and then only once, when
    logic has run out of provable moves.
    """
    import random

    data = body()
    game = get_game(data)
    if game.is_over:
        raise ApiError("The game is over. Start a new game.")
    allow_guessing = bool(data.get("allow_guessing", False))

    log, moves = [], 0
    for _ in range(game.rows * game.cols):          # hard cap on rounds
        if game.is_over:
            break
        with Z3_LOCK:
            results = analyse_board(game.visible_board())
        safe = [(i["row"], i["col"]) for i in results if i["status"] == "SAFE"]

        if safe:
            for r, c in safe:
                cell = game.cells[r][c]
                if game.is_over or cell.is_revealed:
                    continue                          # already opened by a flood fill
                if cell.is_flagged:
                    game.toggle_flag(r, c)            # a wrong flag: logic overrides it
                    log.append(f"Removed a flag from ({r},{c}); Z3 proved it safe.")
                game.reveal(r, c)
                moves += 1
                log.append(f"Revealed ({r},{c}): proved SAFE by Z3.")
            continue                                  # rebuild the KB with the new numbers

        # No provably safe cell is left.
        if not allow_guessing:
            log.append("No cell can be proved safe. Enable 'Allow guessing' to let the AI guess.")
            break
        unknown = [(i["row"], i["col"]) for i in results
                   if i["status"] == "UNKNOWN" and not game.cells[i["row"]][i["col"]].is_flagged]
        if not unknown:
            log.append("Nothing left to try.")
            break
        r, c = random.choice(unknown)
        game.reveal(r, c)
        moves += 1
        log.append(f"Guessed ({r},{c}): logic could not decide, so this was a gamble.")
        break

    if game.status == "won":
        log.append("All safe cells revealed. You win!")
    elif game.status == "lost":
        log.append("The AI hit a mine.")

    return jsonify({"state": state_of(game, data["game_id"]), "log": log, "moves": moves})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
