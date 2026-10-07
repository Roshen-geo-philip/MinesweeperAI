# Minesweeper - Logic-Based AI

A playable Minesweeper game whose AI reasons with **predicate logic and the Z3 theorem prover**.
Instead of guessing, it turns the visible numbers into logical constraints and asks Z3 which hidden
cells are **provably a mine**, **provably safe**, or **unknown**.

- Backend: Python, Flask, `z3-solver`
- Frontend: plain HTML, CSS and JavaScript (dark theme, no frameworks)

## Project structure

```text
minesweeper/
├── app.py              Flask app and JSON API
├── game.py             Game rules: board, mines, reveal, flags, win/lose
├── solver.py           Z3 knowledge base, entailment, MINE/SAFE/UNKNOWN, explanations
├── requirements.txt
├── README.md
├── templates/
│   └── index.html      UI structure
└── static/
    ├── style.css       Design system and styling
    └── script.js       Rendering, interaction, API calls
```

The folder names `templates` and `static` must be exactly these (lowercase), or Flask will raise
`TemplateNotFound` or the page will load without styling.

## Installation

Requires Python 3.9 or newer.

```bash
cd minesweeper
pip install -r requirements.txt
```

or `pip install flask z3-solver`. A virtual environment is optional but recommended:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

## Running

```bash
python app.py
```

Open http://127.0.0.1:5000 in your browser. Stop the server with `Ctrl+C`.

## How to play

| Action | Mouse | Keyboard |
| --- | --- | --- |
| Reveal a cell | Left click | Enter / Space on a focused cell |
| Flag / unflag | Right click | `F` on a focused cell |
| Move between cells | | Arrow keys |

On touch screens, switch on **Flag mode** in Settings so a tap places a flag.

- The first click is always safe, and mines are only placed after it.
- Revealing a `0` opens the surrounding empty cells automatically.
- You win when every non-mine cell is revealed. Flags don't have to be correct.
- Board size and mine count are set under **Settings** and apply when you press **New game**
  (2-30 rows and columns, at least 1 mine, at most one fewer than the number of cells).

## Using the AI

| Control | What it does |
| --- | --- |
| **Analyze Board** | Sends the visible board to Z3 and marks every hidden cell. Nothing is revealed. |
| **Apply Safe Moves** | Reveals the cells Z3 proved safe. Enabled only after an analysis finds some. |
| **AI MOVE** | Runs the agent loop: build KB, reveal provably safe cells, rebuild, repeat until none are left. |
| **Allow guessing** | Lets AI MOVE make one random guess when logic has run out. Off by default. |
| **Show reasoning** | Shows the proof for each proved cell (see below). |

Solver colours (each also has a symbol, so colour is not the only signal):

| Colour | Symbol | Meaning |
| --- | --- | --- |
| Red | ✕ | Mine proved by logic |
| Green | ✓ | Safe proved by logic |
| Yellow | ? | Cannot determine |

Hover a marked cell for a tooltip such as `Z3: MINE PROVED`. Any move you make clears the marks,
because they describe the old board. Click Analyze Board again.

With **Show reasoning** on, pick a proved cell in the Reasoning panel (or hover it on the board)
to see which clues the proof used. Those clue cells are highlighted on the board. The text is
built from the constraints Z3 actually needed (an unsat core, shrunk to a minimal set), not from
hand-written rules.

## How the board becomes Z3 constraints

1. Every cell `(r, c)` becomes a Boolean variable `Mine_r_c`.
2. Every revealed cell showing `n` adds two facts to the knowledge base (KB):

   ```text
   Not Mine(r, c)                           the revealed cell is not a mine
   Sum( If(Mine(nb), 1, 0) for nb ) == n    exactly n neighbours are mines
   ```

3. Hidden cells add no facts of their own. Flags are ignored, because a flag might be wrong.

## How MINE / SAFE / UNKNOWN inference works

Inference uses **proof by refutation**: to show something follows from the KB, assume its opposite
and check for a contradiction.

```python
def entails(kb, formula):
    kb.push()
    kb.add(Not(formula))
    result = kb.check() == unsat
    kb.pop()               # the assumption is temporary
    return result
```

| Query | If UNSAT | Result |
| --- | --- | --- |
| KB and Not Mine(c) | Mine(c) is forced | **MINE** |
| KB and Mine(c) | Not Mine(c) is forced | **SAFE** |
| neither is UNSAT | nothing is forced | **UNKNOWN** |

Hidden cells that touch no number appear in no constraint, so they are reported as UNKNOWN
without calling Z3. The solver does not use the total mine count, so it never over-claims, but
it can miss a few end-game deductions that depend on the global count.

## API

All endpoints accept and return JSON. Real mine positions are never sent before the game ends.

| Endpoint | Body | Returns |
| --- | --- | --- |
| `POST /api/new` | `{rows, cols, mines}` | `{state}` including a `game_id` |
| `POST /api/reveal` | `{game_id, row, col}` | `{state}` |
| `POST /api/flag` | `{game_id, row, col}` | `{state}` |
| `POST /api/solve` | `{board, explain}` | `{results, summary}` |
| `POST /api/ai-move` | `{game_id, allow_guessing}` | `{state, log, moves}` |

`/api/solve` is stateless. `board` is a 2-D list where `null` is a hidden cell and `0`-`8` is a
revealed number:

```json
{
  "results": [
    {"row": 0, "col": 1, "status": "MINE"},
    {"row": 1, "col": 2, "status": "SAFE"},
    {"row": 2, "col": 2, "status": "UNKNOWN"}
  ]
}
```

With `"explain": true`, MINE and SAFE entries also carry an `explanation` with `text` and the
`clues` used.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `TemplateNotFound: index.html` | Put `index.html` in a folder named `templates` next to `app.py`. |
| Page loads but is unstyled or buttons do nothing | Put `style.css` and `script.js` in a folder named `static`. Check the browser's Network tab (F12) for 404s. |
| `ModuleNotFoundError: No module named 'z3'` | Run `pip install z3-solver`. |
| "Unknown game. Start a new game." | The server restarted and forgot the game. Press New game. |
| Font looks different | Inter loads from Google Fonts. Offline, the page uses your system font. |

## Notes

- Games are stored in server memory (up to 200 recent ones), so this is a local demo, not a
  multi-user deployment.
- Z3 calls are wrapped in a lock because Z3's default context is not thread-safe.
