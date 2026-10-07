/* script.js - UI only: renders the board, handles clicks, calls the backend.
   All game rules live in game.py and all reasoning lives in solver.py. */
"use strict";

const $ = (id) => document.getElementById(id);
const boardEl = $("board");
const key = (r, c) => `${r},${c}`;

// ---- application state ----------------------------------------------------
let state = null;          // latest public board from the server (no mine info)
let solver = null;         // latest Z3 analysis: { entries: Map, counts }
let reasonKey = null;      // "r,c" of the cell whose proof is shown
let busy = false;
let cellEls = [];          // cellEls[r][c] -> <button>
let lastFocus = null;      // [r, c] so keyboard focus survives re-rendering

// ---- server calls -----------------------------------------------------------
async function api(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

// Run an async action with the buttons disabled, and show any error.
async function run(action) {
  if (busy) return;
  busy = true;
  updateControls();
  try {
    await action();
  } catch (err) {
    setMessage(err.message, "error");
  } finally {
    busy = false;
    updateControls();
  }
}

function setMessage(text, kind = "") {
  const el = $("message");
  el.textContent = text;
  el.className = "message " + kind;
}

function addLog(lines) {
  const log = $("log");
  if (log.firstElementChild && log.firstElementChild.classList.contains("muted")) log.replaceChildren();
  for (const line of lines.slice().reverse()) {
    const li = document.createElement("li");
    li.textContent = line;
    log.prepend(li);
  }
  while (log.children.length > 60) log.removeChild(log.lastChild);
}

// ---- game actions -------------------------------------------------------------
function newGame() {
  return run(async () => {
    const data = await api("/api/new", {
      rows: parseInt($("rows").value, 10),
      cols: parseInt($("cols").value, 10),
      mines: parseInt($("mines").value, 10),
    });
    state = data.state;
    solver = null;
    reasonKey = null;
    lastFocus = null;
    setMessage("New game. Click any cell to start. Right-click (or press F) to flag.");
    render();
  });
}

function afterMove(newState) {
  state = newState;
  solver = null;       // old solver marks are stale once the board changes
  reasonKey = null;
  if (state.status === "won") setMessage("You win! Every safe cell is revealed.", "success");
  else if (state.status === "lost") setMessage("GAME OVER. You hit a mine.", "error");
  else setMessage("");
  render();
}

function doReveal(r, c) {
  if (!state || isOver()) return;
  return run(async () => {
    const data = await api("/api/reveal", { game_id: state.game_id, row: r, col: c });
    afterMove(data.state);
  });
}

function doFlag(r, c) {
  if (!state || isOver()) return;
  return run(async () => {
    const data = await api("/api/flag", { game_id: state.game_id, row: r, col: c });
    afterMove(data.state);
  });
}

function isOver() {
  return state && (state.status === "won" || state.status === "lost");
}

// ---- solver: Analyze / Apply / AI -------------------------------------------------
function analyze() {
  if (!state || isOver()) return;
  return run(async () => {
    // Send only what the player can see: null = hidden (flags are not trusted), number = revealed.
    const board = state.cells.map((row) => row.map((c) => (c.state === "revealed" ? c.n : null)));
    const data = await api("/api/solve", { board, explain: $("show-reasoning").checked });

    const entries = new Map();
    for (const item of data.results) entries.set(key(item.row, item.col), item);
    solver = { entries, counts: data.summary };
    reasonKey = null;

    const s = data.summary;
    setMessage(`Z3 proved ${s.MINE} mine(s) and ${s.SAFE} safe cell(s); ${s.UNKNOWN} unknown.`);
    render();
  });
}

function applySafeMoves() {
  if (!solver || !state || isOver()) return;
  const targets = [...solver.entries.values()].filter((e) => e.status === "SAFE");
  return run(async () => {
    let count = 0;
    for (const t of targets) {
      const cell = state.cells[t.row][t.col];
      if (isOver()) break;
      if (cell.state === "revealed") continue;            // opened by an earlier flood fill
      if (cell.state === "flagged") {                     // a wrong flag: logic overrides it
        state = (await api("/api/flag", { game_id: state.game_id, row: t.row, col: t.col })).state;
      }
      state = (await api("/api/reveal", { game_id: state.game_id, row: t.row, col: t.col })).state;
      count++;
    }
    addLog([`Applied ${count} proved-safe move(s).`]);
    afterMove(state);
    if (!isOver()) setMessage(`Applied ${count} safe move(s). Click Analyze Board again for fresh results.`, "success");
  });
}

function aiMove() {
  if (!state || isOver()) return;
  return run(async () => {
    const data = await api("/api/ai-move", {
      game_id: state.game_id,
      allow_guessing: $("allow-guessing").checked,
    });
    addLog(data.log);
    afterMove(data.state);
    if (!isOver()) setMessage(data.log.length ? data.log[data.log.length - 1] : "AI had nothing to do.");
  });
}

// ---- rendering ---------------------------------------------------------------------
const GLYPH = { MINE: "\u2715", SAFE: "\u2713", UNKNOWN: "?" };
const TIP = {
  MINE: "Z3: MINE PROVED",
  SAFE: "Z3: SAFE PROVED",
  UNKNOWN: "Z3: UNKNOWN (cannot be determined)",
};

function buildCell(r, c, cell) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "cell";
  b.dataset.r = r;
  b.dataset.c = c;
  let text = "";
  let label = `Row ${r + 1}, column ${c + 1}: `;

  switch (cell.state) {
    case "revealed":
      b.classList.add("revealed");
      if (cell.n > 0) { text = String(cell.n); b.classList.add("n" + cell.n); }
      label += cell.n > 0 ? `${cell.n} adjacent mines` : "empty";
      break;
    case "flagged":
      b.classList.add("covered"); text = "\uD83D\uDEA9"; label += "flagged"; break;
    case "mine":
      b.classList.add("mine"); text = "\uD83D\uDCA3"; label += "mine"; break;
    case "exploded":
      b.classList.add("exploded"); text = "\uD83D\uDCA5"; label += "exploded mine"; break;
    case "wrong_flag":
      b.classList.add("wrong"); text = "\uD83D\uDEA9"; label += "incorrect flag"; break;
    default:
      b.classList.add("covered"); label += "hidden";
  }

  // Overlay the solver's verdict on hidden/flagged cells.
  const entry = solver && solver.entries.get(key(r, c));
  if (entry && (cell.state === "hidden" || cell.state === "flagged")) {
    b.classList.add("s-" + entry.status.toLowerCase());
    if (cell.state === "hidden") text = GLYPH[entry.status];
    b.dataset.tip = TIP[entry.status];   // shown by the CSS tooltip
    label += `, ${TIP[entry.status]}`;
  }
  b.textContent = text;
  b.setAttribute("aria-label", label);
  return b;
}

function render() {
  if (!state) return;
  const { rows, cols } = state;
  const hadFocus = boardEl.contains(document.activeElement);

  boardEl.style.setProperty("--cell", cellSize(cols) + "px");
  boardEl.style.gridTemplateColumns = `repeat(${cols}, var(--cell))`;
  boardEl.replaceChildren();
  cellEls = [];
  for (let r = 0; r < rows; r++) {
    const rowEls = [];
    for (let c = 0; c < cols; c++) {
      const b = buildCell(r, c, state.cells[r][c]);
      boardEl.appendChild(b);
      rowEls.push(b);
    }
    cellEls.push(rowEls);
  }
  if (hadFocus && lastFocus && cellEls[lastFocus[0]]) cellEls[lastFocus[0]][lastFocus[1]].focus();

  $("mines-left").textContent = state.mines_remaining;
  const statusEl = $("status");
  const names = { ready: "Ready", playing: "Playing", won: "YOU WIN", lost: "GAME OVER" };
  statusEl.textContent = names[state.status];
  statusEl.className = "stat-value " + state.status;

  applyHighlights();
  renderReasoning();
  updateControls();
}

function cellSize(cols) {
  const room = Math.min(window.innerWidth, 1100) - 72;
  return Math.max(24, Math.min(40, Math.floor(room / cols)));
}

function updateControls() {
  document.body.classList.toggle("is-busy", busy);   // drives the loading bar
  const over = !state || isOver();
  $("new-game").disabled = busy;
  $("analyze").disabled = busy || over;
  $("ai-move").disabled = busy || over;
  $("apply-safe").disabled = busy || over || !solver || solver.counts.SAFE === 0;
}

// ---- reasoning panel ---------------------------------------------------------------
function applyHighlights() {
  for (const row of cellEls) for (const b of row) b.classList.remove("hl-clue", "hl-target");
  const entry = solver && reasonKey && solver.entries.get(reasonKey);
  if (!entry || !entry.explanation) return;
  cellEls[entry.row][entry.col].classList.add("hl-target");
  for (const [r, c] of entry.explanation.clues) cellEls[r][c].classList.add("hl-clue");
}

function selectReason(k) {
  reasonKey = k;
  applyHighlights();
  renderReasoning();
}

function renderReasoning() {
  const panel = $("reasoning-panel");
  const body = $("reasoning-body");
  body.replaceChildren();
  panel.hidden = !$("show-reasoning").checked;
  if (panel.hidden) return;

  const para = (text) => {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = text;
    body.appendChild(p);
  };

  if (!solver) return para("Click Analyze Board. Then pick a proved cell here, or hover over it on the board, to see which clues Z3 used.");

  const proved = [...solver.entries.values()].filter((e) => e.status !== "UNKNOWN");
  if (!proved.length) return para("Z3 could not prove any cell to be a mine or safe. Every hidden cell is UNKNOWN.");
  if (!proved.some((e) => e.explanation)) return para("Run Analyze Board again to get explanations.");

  const list = document.createElement("div");
  list.className = "reason-list";
  for (const e of proved) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "reason-chip " + e.status.toLowerCase();
    chip.textContent = `(${e.row},${e.col}) ${e.status}`;
    chip.setAttribute("aria-pressed", String(key(e.row, e.col) === reasonKey));
    chip.addEventListener("click", () => selectReason(key(e.row, e.col)));
    list.appendChild(chip);
  }
  body.appendChild(list);

  const chosen = reasonKey && solver.entries.get(reasonKey);
  if (chosen && chosen.explanation) {
    const pre = document.createElement("pre");
    pre.className = "reason-text";
    pre.textContent = chosen.explanation.text;
    body.appendChild(pre);
  } else {
    para("Choose a cell above to see the proof.");
  }
}

// ---- board events ----------------------------------------------------------------
function cellFromEvent(e) {
  const b = e.target.closest(".cell");
  return b ? [Number(b.dataset.r), Number(b.dataset.c)] : null;
}

boardEl.addEventListener("click", (e) => {
  const rc = cellFromEvent(e);
  if (!rc) return;
  lastFocus = rc;
  if ($("flag-mode").checked) doFlag(...rc); else doReveal(...rc);
});

// Right click flags a cell; the browser's context menu is suppressed.
boardEl.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  const rc = cellFromEvent(e);
  if (rc) { lastFocus = rc; doFlag(...rc); }
});

boardEl.addEventListener("keydown", (e) => {
  const rc = cellFromEvent(e);
  if (!rc || !state) return;
  lastFocus = rc;
  const moves = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
  if (e.key === "f" || e.key === "F") {
    e.preventDefault();
    doFlag(...rc);
  } else if (moves[e.key]) {
    e.preventDefault();
    const r = rc[0] + moves[e.key][0], c = rc[1] + moves[e.key][1];
    if (cellEls[r] && cellEls[r][c]) { lastFocus = [r, c]; cellEls[r][c].focus(); }
  }
});

// While "Show reasoning" is on, hovering a proved cell shows its proof.
boardEl.addEventListener("mouseover", (e) => {
  if (!solver || !$("show-reasoning").checked) return;
  const rc = cellFromEvent(e);
  if (!rc) return;
  const k = key(...rc);
  const entry = solver.entries.get(k);
  if (entry && entry.explanation && k !== reasonKey) selectReason(k);
});

// ---- wiring ----------------------------------------------------------------------
$("new-game").addEventListener("click", newGame);
$("analyze").addEventListener("click", analyze);
$("apply-safe").addEventListener("click", applySafeMoves);
$("ai-move").addEventListener("click", aiMove);
$("show-reasoning").addEventListener("change", () => {
  // Explanations are only computed when requested, so re-analyze if we lack them.
  if ($("show-reasoning").checked && solver) analyze(); else renderReasoning();
});
window.addEventListener("resize", () => {
  if (state) boardEl.style.setProperty("--cell", cellSize(state.cols) + "px");
});

newGame();

// ---- nav: highlight the section currently in view ---------------------------------
(function navHighlight() {
  const links = [...document.querySelectorAll(".nav-link")];
  if (!("IntersectionObserver" in window)) return;
  const obs = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      links.forEach((a) => a.setAttribute("aria-current", String(a.dataset.section === e.target.id)));
    }
  }, { rootMargin: "-40% 0px -55% 0px" });
  for (const a of links) { const t = document.getElementById(a.dataset.section); if (t) obs.observe(t); }
})();
