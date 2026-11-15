# Local web UI — design

Date: 2026-11-15 · Status: draft for review

## Intent

The analyzer is command-line only. A web UI makes the same figures easier to browse (a grid of
the 50 numbers reads better than a table), makes the suggestions and the backtest easy to play
with without remembering flags, and puts charts where they help.

It is a **personal tool, run locally**. Deploying it (Netlify was considered) was dropped: Netlify
cannot run Python after the build, and a public site would need hosting, scheduled updates and a
job queue for the backtest, which is not worth it for one user.

The visual reference is the sketch on the design canvas
<https://claude.ai/artifact/EFoRiije7MvT7MW7sEP3U8> (four screens, filled with real figures from
the CLI). The screens below follow it; where this document and the sketch disagree, this document
wins.

## Goals

- `euromillions serve` starts a local web server and opens the UI in the browser.
- Four screens: **Numbers** (home), **Patterns**, **Suggest**, **Backtest**, as in the sketch.
- **Parity with the CLI's figures**: every figure `analyze --full`, `suggest`, `backtest`,
  `next-draw` and `fetch` report can be reached in the UI, with the meaning of the CLI's reading
  notes. The UI adds charts and interaction, not new statistics. Not every option is exposed:
  `analyze --last` and `--top` are not (rankings show the top 10); see Analysis options below.
- The project's stance carries over: every figure that could look meaningful is shown next to
  what chance alone produces, and every page carries the disclaimer.

## Non-goals

- Hosting, remote access, several users, accounts.
- New statistics or strategies (a strategy avoiding popular numbers is a separate project).
- Dark mode, a phone-first design. (Translations were a non-goal too; French was added afterwards,
  at the user's request.) Pages must still not
  break at phone width: the sketch's layouts already wrap.
- Any JavaScript tooling: no npm, no framework, no bundler, no build step.

## Anti-goals (a failure even if it works)

- A UI that suggests predictive power: "best numbers", hot numbers presented as advice, a
  strategy ranking without the chance band next to it.
- A new runtime dependency.
- A server reachable from other machines.
- Statistics computed in JavaScript. The browser formats and draws; every figure comes from the
  Python code, so the CLI and the UI can never disagree.

## Constraints

- Python ≥ 3.10, standard library for the server (`http.server.ThreadingHTTPServer`), the
  existing dependencies only.
- The existing bar: `ruff check`, `ruff format --check`, `mypy --strict` (sources and tests)
  clean; `pytest` with warnings as errors; tests offline.
- The page files ship inside the package: `pyproject.toml` package data and `MANIFEST.in`.
- Works offline once the cache exists: no CDN, no Google Fonts.

## Approaches considered

1. **Standard-library server + static HTML/JS pages calling a small JSON API** — chosen. No new
   dependency, fits the project's "standard library first" choice; the cost is hand-written
   fetch-and-render code, acceptable for four pages and a handful of endpoints.
2. Flask with server-rendered templates. Nicer routing and templating, but one more dependency
   for little gain: most of the interaction (period toggle, number details, strategy picker) is
   client-side anyway.
3. A static HTML report written to disk (`euromillions report`). No server, but Suggest and
   Backtest could only show precomputed results and "Update data" could not work.

(Earlier, for Netlify: a static site generated at build time, optionally with Python in the
browser via Pyodide, or a rewrite in TypeScript. All dropped with the deployment.)

## The design

### Command

```
euromillions [--data-dir DIR] serve [--port 8000] [--no-browser]
```

- Binds **127.0.0.1 only**. Prints the URL, opens it with `webbrowser` unless `--no-browser`,
  runs until Ctrl+C, then exits cleanly (no traceback).
- Port already in use: a `ClickException` saying so and suggesting `--port`.
- Uses the same cache as the other commands (`--data-dir`, `EUROMILLIONS_DATA_DIR`).
- Starts even when there is no cache yet: the pages then offer to download the history.
- Requests whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` are refused (403),
  so a web page in the browser cannot reach the server through DNS rebinding.

### Code layout

- `src/euromillions/web/__init__.py`
- `src/euromillions/web/api.py` — pure functions turning the domain objects (`AnalysisReport`,
  `NumberStats`, `Suggestion`, `Backtest`, the schedule) into JSON-ready dicts. No HTTP; unit
  tested on their own.
- `src/euromillions/web/server.py` — request handler: routing, JSON responses and errors, static
  files, the backtest job.
- `src/euromillions/web/static/` — `index.html` (Numbers), `patterns.html`, `suggest.html`,
  `backtest.html`, one shared stylesheet, one shared script (requests, header, formatting) and one
  script per page.
- `cli.py` — the `serve` command.
- `backtest.py` — a progress hook (below).

**Shared computations move out of `cli.py`.** Several figures the CLI prints are computed inside
its formatting code: the chance that a z-score lands beyond ±2, the threshold that would point to
a real edge, the odds of a top rank, the spread of the win rate, the wins expected per prize rank.
They move to `backtest.py` (or `analyzer.py` for analysis figures) so the CLI and the API call the
same functions. The CLI's output must not change: its existing tests are the check.

### JSON API

All responses are JSON. Errors are `{"error": "<message>"}`, with the same wording as the CLI:
400 for an invalid parameter (`ValueError`), 404 for an unknown path, 409 when there is no
history yet, 502 when a download fails (`FetchError`; the cache is left untouched, as today), 500
for a corrupt cache (with the CLI's "delete that file and fetch again" hint).

| Endpoint | Returns |
|---|---|
| `GET /api/status` | Whether a cache exists; number of draws, first and last date; the latest draw (numbers, stars, whether the jackpot was won); the next draw as an ISO datetime with its offset; whether the history is behind the latest scheduled draw; the strategies with their descriptions and whether they use the recent window. |
| `POST /api/fetch` | Runs `fetcher.update_cache` (body `{"force": false}`), returns what `fetch` prints — added, updated, already up to date, latest draw not yet published — plus the new status. |
| `GET /api/analysis?window=50&top=10` | Everything `analyze --full` computes: per-number and per-star stats over the whole history **and** over the `window` most recent draws (for the period toggle), the hot, cold and overdue lists, pair / triplet / star-pair rankings with `expected` and `typical_top`, consecutive numbers, sum and range distributions, odd/even and low/high splits, and the chance references (typical extreme z-score for 50 numbers and for 12 stars, typical longest current absence). |
| `GET /api/suggest?strategy=&lines=&window=&seed=` | The lines, each with its sum, odd/even and low/high split; the strategy and its description; the history it is based on; **the seed used** — when none is given the server picks one and returns it, so every result can be reproduced. |
| `POST /api/backtest` | Starts a backtest with the CLI's settings (strategies, train fraction, lines, window, seed, refresh or frozen, line price). 202, or 409 if one is already running. |
| `GET /api/backtest` | The job: `idle`, `running` (with `done` and `total`), `done` (with the full result), `failed` (with the error) or `cancelled`, and the settings it ran with. |
| `DELETE /api/backtest` | Cancels the running backtest. |

The Patterns screen also shows two exact fair-draw figures the CLI does not print: the
probability of each odd/even (and low/high) split, a hypergeometric law, and the probability that
a draw holds two consecutive numbers (1 − C(46,5)/C(50,5) ≈ 35.3%). They are computed in Python
and returned by `/api/analysis`.

The backtest result holds every figure `backtest` prints: the period and the training set, per
strategy the winnings, net, return on stake, share of winning lines, numbers and stars matched
per line with their z-scores, wins per prize rank, top-rank winnings kept apart, the biggest win;
the by-chance row with its standard deviation and the wins expected per rank; and the figures
behind the reading notes (chance of a z-score beyond ±2, the share of backtests with at least one
such z-score, the threshold that would point to a real edge, the odds of a top rank, estimated
wins, skipped draws).

**Reading notes: figures from Python, sentences in the page.** The API returns those figures and
the flags that decide which notes apply (top ranks won, fewer than 1,000 lines, estimated wins,
skipped draws). Each page writes its own sentences from them, worded for its own layout: the
CLI's text refers to terminal columns ("in brackets", "the by-chance row") that the pages do not
have. The CLI keeps its text as it is; only the computations move.

### Backtest job and progress

- `backtest()` gets an optional `progress: Callable[[int, int], None] | None = None`, called with
  `(done, total)` as draws are played, `total` being the number of strategies times the number of
  draws played. An exception raised by the callback propagates and stops the backtest: that is how
  cancellation works. The CLI does not use it.
- The server runs one backtest at a time in a background thread and keeps the last result in
  memory, so going back to the Backtest screen shows it again. A server restart forgets it.
- Timing on the real history: about 5 seconds with the default settings, about 24 seconds over
  every draw since 2016.

### Screens

Shared on every page: the header (name, navigation, number of draws and dates, **Update data**,
which calls `/api/fetch` and reports the outcome in a line), the disclaimer, and a footer naming
the data source.

- **Numbers** — next draw with a countdown (from `nextDraw`, refreshed each minute); the latest
  draw; a 10 × 5 grid of the main numbers and a 12-cell row of stars, coloured by z-score in five
  bins (palette below), with the z-score written in each cell; a **Full history / Last N draws**
  toggle (N = the analysis window, 50 by default); clicking a number shows its details (times
  drawn and expected, z-score, average / current / longest gap, last seen, and since when a star
  is in the pool); hot, cold and overdue lists, each with what chance alone gives.
- **Patterns** — histogram of the sum by bands of 10 with the central 80% marked; odd/even and
  low/high splits as bars with the fair-draw figure as a tick; consecutive numbers; range;
  pairs, triplets and star pairs with their expected count and the luckiest-by-chance figure.
- **Suggest** — strategy picker (descriptions from `suggester.STRATEGIES`); lines 1 to 10; the
  recent window (50 / 100 / 200) only for the strategies that use it; an editable seed with a
  **New seed** button; the lines update as the settings change; **Copy lines** puts them on the
  clipboard in the CLI's format (`models.format_line`). A short "what to expect" note links to the
  Backtest screen; when a backtest has run in this server session it quotes that run's return on
  stake for the chosen strategy, otherwise it says that in the backtest every strategy loses about
  as much as random lines.
- **Backtest** — the CLI's settings; the smallest train fraction the history allows is shown and
  enforced (the evaluation has to start under the current rules); **Run** shows a progress bar
  with `done` / `total` and **Cancel**. Results: a headline sentence **built from the result**,
  never hard-coded; stat tiles; a dot plot of each strategy's winnings over the chance band (±1
  and ±2 standard deviations, average line); the results table; wins per prize rank with the
  chance expectation; top-rank wins reported apart as luck; the biggest win; the reading notes.

  The headline and the dot plot apply the two tests the CLI's notes already state, and no other:
  - **Money**: each strategy's winnings **from the common ranks** (`winnings − top_winnings`,
    since the by-chance figure leaves the top ranks out) against `chance_winnings ± 2 ·
    chance_winnings_sd`. The dot plot plots that amount; any top-rank money is shown beside it,
    labelled as luck.
  - **Matches**: the `main_z` and `star_z` of each strategy against the threshold the CLI computes
    for a real edge (about ±2.9 with 12 z-scores), not against ±2.

  All inside → e.g. "Every strategy did what chance does: winnings within two standard deviations
  of random lines, no match count beyond ±2.9." Something outside → name it and the figure, then
  say, without a new statistic, that with this many strategies and z-scores chance puts one
  outside now and then, and that only a result that holds with other seeds and periods would
  mean something. The page never ranks the strategies as better or worse.

**Analysis options.** The Numbers screen has a window select (50 / 100 / 200 draws, default 50):
it sets N in the period toggle and the window of the hot and cold lists, like `analyze --window`.
Rankings show the top 10, like the CLI's default. `analyze --last` (every figure limited to the
last N draws) is not exposed; it goes in `PLAN.md` under "Changes to the original plan".

Empty and error states: no cache → every page shows "No draw history yet" with a **Download**
button; a failed download shows the error and keeps the current data; a stopped server →
"The server has stopped: run `euromillions serve` again."

### Look

As in the sketch: warm light ground, white cards, near-black text and buttons, colour only for
data. Diverging z-score palette, validated (each arm as an ordinal ramp, light end ≥ 2:1 on the
surface): red `#b8322f` (≤ −2, white text), `#ee9693` (−2 to −1), neutral `#f0efec` (−1 to +1),
`#86b6ef` (+1 to +2), blue `#1c5cab` (≥ +2, white text); the z-score is written in every cell, so
colour is never the only channel. Single-series bars `#2a78d6`; Lucky Stars amber `#f2b632` with
dark text. Fonts: the system UI stack (the sketch's Inter and Space Grotesk would need bundling or
a CDN). Real buttons, inputs and labels; touch targets ≥ 44 px; text contrast ≥ 4.5:1.

### Testing

- `web/api.py`: unit tests on the fixture histories in `tests/`.
- `web/server.py`: start the server on port 0 in a thread; every page and asset is served; each
  endpoint's happy path and errors; the `Host` check; the backtest job from start to result, and a
  cancellation (on a small history, to stay fast).
- `backtest()` progress: called with increasing values ending at `total`; an exception from the
  callback stops the run.
- `serve`: `--no-browser`, port already in use.
- The CLI's existing tests still pass unchanged after the shared computations move.
- JavaScript: no unit tests (no JS toolchain, by the non-goals). The screens are checked by hand
  in a browser against the sketch.

### Documentation

`PLAN.md`: a phase 7 for the web UI, the new modules in the structure, `serve` in the CLI section,
and any departure from this document in "Changes to the original plan".

## Left to the builder

The exact JSON field names; status codes not listed above; how often progress is reported; the
split of the JavaScript files; debounce timing on Suggest; chart technique (HTML/CSS as in the
sketch, or inline SVG); CSS details within the look above; the order of the work.

## Calls made without the user

- Binding to 127.0.0.1 and the `Host` check.
- System fonts instead of the sketch's web fonts.
- Lines on Suggest capped at 10.
- The two new fair-draw figures on Patterns.
- One backtest at a time, last result kept in memory only.
- Shared computations moved out of `cli.py`; the CLI's text stays as it is.
- Reading notes: figures from Python, sentences written by each page.
- The backtest headline and dot plot use the CLI's two tests (common-rank winnings within ±2 SD of
  chance; match z-scores against the real-edge threshold), with no new statistic.
- Window select on Numbers; `analyze --last` and `--top` not exposed.
