# EuroMillions Number Analyzer — Project Plan

## Goal

Build a Python tool that fetches historical EuroMillions draw results, performs statistical analysis, and suggests numbers for the next draw based on data-driven heuristics.

> **Disclaimer**: Lottery draws are random. No statistical method can predict or guarantee winning numbers. This project is for educational and entertainment purposes only.

---

## EuroMillions Rules (Quick Reference)

- **5 main numbers** from 1–50
- **2 Lucky Stars** from 1–12
- Draws happen **Tuesday and Friday**

---

## Project Structure

```
euromillions/
├── pyproject.toml          # Project metadata, dependencies
├── src/
│   └── euromillions/
│       ├── __init__.py
│       ├── fetcher.py      # Download / scrape historical draw data
│       ├── models.py       # Data models (Draw, Suggestion, etc.)
│       ├── analyzer.py     # Statistical analysis engine
│       ├── suggester.py    # Number suggestion strategies
│       └── cli.py          # Command-line interface
├── tests/
│   ├── __init__.py
│   ├── test_fetcher.py
│   ├── test_analyzer.py
│   └── test_suggester.py
├── data/                   # Cached CSV / JSON draw history
├── PLAN.md
└── README.md               (only if requested)
```

---

## Phases

### Phase 1 — Data Acquisition (`fetcher.py`, `models.py`)

- Define a `Draw` dataclass: date, 5 main numbers, 2 lucky stars, prize info (optional).
- Fetch historical results from a public source (e.g., the official euromillions.com results page, or a public CSV/API such as `lottolyzer` datasets).
- Cache results locally as JSON/CSV in `data/` so we don't re-fetch every run.
- Parse and validate data (numbers in range, no duplicates per draw, etc.).

### Phase 2 — Statistical Analysis (`analyzer.py`)

Compute the following statistics over the full history and configurable recent windows (e.g., last 50, 100, 200 draws):

| Metric | Description |
|---|---|
| **Frequency analysis** | How often each number (1–50) and star (1–12) has appeared |
| **Hot numbers** | Numbers appearing significantly more than average recently |
| **Cold numbers** | Numbers appearing significantly less than average recently |
| **Overdue numbers** | Numbers with the longest gap since last appearance |
| **Pair / triplet frequency** | Most common 2- and 3-number combinations |
| **Consecutive number analysis** | How often consecutive numbers appear together |
| **Sum & range analysis** | Distribution of the sum and range (max − min) of drawn numbers |
| **Odd/even & high/low ratios** | Distribution patterns (e.g., 3 odd + 2 even is most common) |
| **Gap analysis** | Average interval between appearances for each number |

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
python -m euromillions fetch

# Show analysis summary
python -m euromillions analyze [--last N]

# Suggest numbers
python -m euromillions suggest [--strategy hot|cold|balanced|weighted|anti] [--lines 5]

# Show next draw date
python -m euromillions next-draw
```

### Phase 5 — Tests

- Unit tests for parsing, analysis calculations, and suggestion constraints.
- Ensure suggested numbers always satisfy EuroMillions rules (5 unique numbers 1–50, 2 unique stars 1–12).

---

## Dependencies

| Package | Purpose |
|---|---|
| `requests` / `httpx` | HTTP fetching |
| `beautifulsoup4` | HTML parsing (if scraping) |
| `pandas` | Data analysis (optional, can use stdlib) |
| `click` | CLI framework |
| `pytest` | Testing |

---

## Implementation Order

1. `models.py` — data structures
2. `fetcher.py` — get historical data
3. `analyzer.py` — crunch the numbers
4. `suggester.py` — generate suggestions
5. `cli.py` — tie it all together
6. Tests throughout each step
