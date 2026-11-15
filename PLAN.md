# EuroMillions Number Analyzer — Project Plan

## Goal

Build a Python tool that fetches historical EuroMillions draw results, performs statistical analysis, and suggests numbers for the next draw based on data-driven heuristics.

> **Disclaimer**: Lottery draws are random. No statistical method can predict or guarantee winning numbers. This project is for educational and entertainment purposes only.

**Status**: all six phases below are implemented and tested.

---

## EuroMillions Rules (Quick Reference)

- **5 main numbers** from 1–50
- **2 Lucky Stars** from 1–12 (1–9 until May 2011, 1–11 until September 2016)
- Draws happen **Tuesday and Friday**, around 21:05 Paris time (Fridays only until May 2011)
- **13 prize ranks**, from 5 numbers + 2 stars (jackpot) down to 2 numbers; a line costs €2.50 in France

---

## Project Structure

```
euromillions/
├── pyproject.toml          # Project metadata, dependencies
├── src/
│   └── euromillions/
│       ├── __init__.py
│       ├── __main__.py     # `python -m euromillions`
│       ├── errors.py       # Errors raised on purpose (reported without a traceback)
│       ├── models.py       # Game rules and data models (Draw, PrizeTier, Suggestion)
│       ├── schedule.py     # Draw schedule (next / previous draw)
│       ├── fetcher.py      # Download, parse and cache the draw history
│       ├── analyzer.py     # Statistical analysis engine
│       ├── suggester.py    # Number suggestion strategies
│       ├── backtest.py     # Evaluation of the strategies on held-out draws
│       └── cli.py          # Command-line interface
├── tests/                  # One test module per source module, plus fixtures
├── data/                   # Cached draw history (draws.json, not committed)
├── PLAN.md
└── README.md               (only if requested)
```

---

## Phases

### Phase 1 — Data Acquisition (`fetcher.py`, `models.py`)

- Define a `Draw` dataclass: date, 5 main numbers, 2 lucky stars, prize info (optional).
- Fetch historical results from a public source: the zipped CSV archives published by the French operator FDJ, linked from its history page (every draw since 2004, with the winners and prize of each rank).
- Cache results locally as JSON/CSV in `data/` so we don't re-fetch every run.
- Parse and validate data (numbers in range, no duplicates per draw, etc.).

### Phase 2 — Statistical Analysis (`analyzer.py`)

Compute the following statistics over the full history and configurable recent windows (e.g., last 50, 100, 200 draws):

| Metric | Description |
|---|---|
| **Frequency analysis** | How often each number (1–50) and star (1–12) has appeared |
| **Hot numbers** | Numbers that came up more often than expected in the recent window, ranked by z-score (how many standard deviations above expectation) |
| **Cold numbers** | Numbers that came up less often than expected in the recent window, ranked the same way |
| **Overdue numbers** | Numbers with the longest gap since last appearance |
| **Pair / triplet frequency** | Most common 2- and 3-number combinations |
| **Consecutive number analysis** | How often consecutive numbers appear together |
| **Sum & range analysis** | Distribution of the sum and range (max − min) of drawn numbers |
| **Odd/even & high/low ratios** | Distribution patterns (e.g., 3 odd + 2 even is most common) |
| **Gap analysis** | Average number of draws without the number between two appearances, for each number |

Every per-number figure only counts the draws in which the number was in the pool, so that stars 10–12 are compared with what they could do. Every ranking comes with what fair draws alone typically produce (most extreme z-score, longest absence, luckiest pair), because extremes look meaningful even when they are not. `analyze --full` adds the table of every number and star, and the full distributions.

### Phase 3 — Number Suggestion (`suggester.py`)

Implement multiple suggestion strategies the user can choose from:

1. **Hot picks** — Favour numbers with high recent frequency.
2. **Cold / due picks** — Favour numbers that are statistically "overdue".
3. **Balanced picks** — Mix of hot and cold, respecting historical odd/even and high/low ratios.
4. **Weighted random** — Random selection weighted by a composite score (frequency + gap + pair affinity).
5. **Anti-pattern** — Avoid combinations that have never or rarely occurred (e.g., all numbers > 40).

Each strategy outputs one or more complete lines: 5 main numbers + 2 lucky stars.

### Phase 4 — CLI Interface (`cli.py`)

Simple command-line tool using `argparse` or `click`:

```
# Update local data cache
python -m euromillions fetch [--force]

# Show analysis summary
python -m euromillions analyze [--last N] [--window 50] [--top 10] [--full]

# Suggest numbers
python -m euromillions suggest [--strategy hot|cold|balanced|weighted|anti] [--lines 5]
                               [--window 50] [--seed N]

# Evaluate the strategies on held-out draws
python -m euromillions backtest [--strategy NAME ...] [--train-fraction 0.8] [--lines 5]
                                [--window 50] [--seed N] [--static] [--price 2.5]

# Show next draw date
python -m euromillions next-draw
```

The cache is `data/draws.json` under the current directory; another directory can be given
with `--data-dir DIR` (before the command) or the `EUROMILLIONS_DATA_DIR` variable.

### Phase 5 — Tests

- Unit tests for parsing, analysis calculations, and suggestion constraints.
- Ensure suggested numbers always satisfy EuroMillions rules (5 unique numbers 1–50, 2 unique stars 1–12).

### Phase 6 — Backtesting (`backtest.py`)

Check the hypotheses behind the strategies on data they have not seen, and measure what the suggested lines would have earned:

- Split the history: the oldest part (80% by default) only serves to build statistics, the rest is the evaluation set.
- Play every evaluated draw with lines suggested from the draws that precede it (statistics updated before each draw, or frozen on the training set with `--static`). No line ever depends on the draw it is scored on.
- Score the lines with the prizes really paid that day for each rank, shared with the actual winners; report cost, winnings, net result and return on stake per strategy.
- Compare every strategy with a uniformly random baseline and with pure chance: expected winnings of random lines on the same draws from prize ranks 4 to 13 (with their standard deviation), share of winning lines, average numbers and stars matched per line with exact z-scores, wins per prize rank. A win in the three top ranks is far too rare to be expected: it is reported apart, as luck.
- Say how to read the result: with a dozen z-scores on screen, one beyond ±2 is expected in about four backtests out of ten; only a much larger value that holds across seeds and periods would point to predictive power. On the real history, every strategy loses about 80% of the stakes, like random lines.
- Only draws under the current rules (since 27 September 2016) are evaluated, as earlier ones had other star pools and prize ranks.

---

## Dependencies

| Package | Purpose |
|---|---|
| `requests`, `urllib3` | HTTP fetching, with retries |
| `beautifulsoup4` | HTML parsing (archive links on the history page) |
| `click` | CLI framework |
| `tzdata` | Time zone data for the draw schedule (Paris time) |
| `pytest`, `ruff`, `mypy` | Testing, linting, type checking (dev) |

The analysis uses the standard library only (no `pandas`).

---

## Implementation Order

1. `models.py` — data structures
2. `fetcher.py` — get historical data
3. `analyzer.py` — crunch the numbers
4. `suggester.py` — generate suggestions
5. `cli.py` — tie it all together
6. `backtest.py` — evaluate the strategies on held-out draws
7. Tests throughout each step

---

## Changes to the Original Plan

Where the implementation departs from the plan as first written, the text above was updated; the original wording is kept here.

- **Data source**: the plan suggested the euromillions.com results page or a `lottolyzer` dataset. The official FDJ archives are used instead: complete since 2004, with the winners and prize of every rank, which the backtest needs.
- **Hot and cold numbers**: the plan said "appearing significantly more (less) than average recently". No threshold of significance is applied: numbers above (below) their expected count in the recent window are ranked by z-score, which is shown next to each. With 50 numbers on screen, a z-score beyond 2 is nothing unusual, and a threshold would call chance a signal.
- **Gap analysis**: the plan said "average interval between appearances". The figure reported is the average number of draws *without* the number between two appearances, i.e. that interval minus one, so that it compares directly with the current and the longest gap.
- **Structure**: `schedule.py`, `errors.py` and `__main__.py` were not in the planned structure; `pandas` is not used.
- **Backtesting** (phase 6) was added to the plan while it was being implemented.
