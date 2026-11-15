"""Number suggestion strategies.

Every strategy draws random lines with a bias derived from the draw history.
None of them improves the odds of winning: draws are independent, so each
line is exactly as likely to win as any other. The strategies only differ in
which numbers they favour.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable, Mapping
from typing import NamedTuple

from euromillions import analyzer
from euromillions.errors import HistoryError
from euromillions.models import (
    MAIN_COUNT,
    MAIN_MAX,
    POSSIBLE_LINES,
    STAR_COUNT,
    STAR_MAX,
    BallKind,
    Draw,
    Suggestion,
    picks_per_draw,
)

#: Rank-based sampling weights grow with this power of the rank, so the best-ranked
#: ten numbers of the main pool receive about 60% of the picks and the bottom half about 6%.
_RANK_POWER = 3

#: The `anti` strategy rejects pattern values seen in fewer than this share of past draws...
_RARE_SHARE = 0.05
#: ... and sums or ranges outside this central share of their historical distribution.
_CENTRAL_SHARE = 0.90
#: With fewer draws than this, 5% of the history is less than one draw and "rare" means nothing.
_MIN_PATTERN_HISTORY = 20

#: Weight added to every number in the `weighted` strategy, so none is ever excluded.
_BASE_WEIGHT = 0.1

#: Candidate lines tried before giving up on the pattern constraints of a strategy.
_MAX_ATTEMPTS = 1_000

#: Name of the baseline: uniformly random lines. It is no strategy worth suggesting, but
#: the yardstick that a backtest compares the strategies with.
BASELINE = "random"

_KINDS: tuple[BallKind, BallKind] = ("main", "star")

#: A line under construction: main numbers, then Lucky Stars.
_Line = tuple[list[int], list[int]]
_Generator = Callable[[random.Random], _Line]
_Weights = dict[int, float]


def _rank_weights(scores: Mapping[int, float]) -> _Weights:
    """Turn scores into sampling weights that depend only on their rank.

    The weight of a number is ``(1 + how many numbers score strictly lower) ** _RANK_POWER``:
    equal scores get equal weights and no number is ever excluded.
    """
    ordered = sorted(scores.values())
    first_rank = {score: rank for rank, score in reversed(list(enumerate(ordered, start=1)))}
    return {number: float(first_rank[score] ** _RANK_POWER) for number, score in scores.items()}


def _scaled(scores: Mapping[int, float]) -> _Weights:
    """Rescale scores linearly to ``[0, 1]`` (all zeros when they are all equal)."""
    low, high = min(scores.values()), max(scores.values())
    if high == low:
        return dict.fromkeys(scores, 0.0)
    return {number: (score - low) / (high - low) for number, score in scores.items()}


def _weighted_sample(
    rng: random.Random, weights: Mapping[int, float], count: int, *, exclude: Iterable[int] = ()
) -> list[int]:
    """Draw ``count`` distinct numbers, each pick proportional to the remaining weights."""
    excluded = set(exclude)
    remaining = {number: weight for number, weight in weights.items() if number not in excluded}
    picked: list[int] = []
    for _ in range(count):
        (choice,) = rng.choices(list(remaining), weights=list(remaining.values()))
        picked.append(choice)
        del remaining[choice]
    return picked


def _uniform_line(rng: random.Random) -> _Line:
    """Draw a line the way the machine does: every line is equally likely."""
    return (
        rng.sample(range(1, MAIN_MAX + 1), MAIN_COUNT),
        rng.sample(range(1, STAR_MAX + 1), STAR_COUNT),
    )


def _most_common(distribution: Mapping[int, int], count: int) -> frozenset[int]:
    """Return the ``count`` most frequent observed values of a distribution."""
    observed = [value for value, draws in distribution.items() if draws]
    return frozenset(sorted(observed, key=lambda value: (-distribution[value], value))[:count])


def _common_values(distribution: Mapping[int, int]) -> frozenset[int]:
    """Return the values seen in at least ``_RARE_SHARE`` of the draws."""
    total = sum(distribution.values())
    return frozenset(value for value, draws in distribution.items() if draws >= _RARE_SHARE * total)


def _central_interval(distribution: analyzer.Distribution) -> tuple[int, int]:
    """Return the bounds of the central ``_CENTRAL_SHARE`` of a distribution."""
    tail = (1 - _CENTRAL_SHARE) / 2
    return distribution.quantile(tail), distribution.quantile(1 - tail)


def _hot_weights(draws: list[Draw], window: int) -> dict[BallKind, _Weights]:
    """Weights favouring numbers drawn more often than expected in the recent window."""
    latest = analyzer.recent(draws, window)
    return {
        kind: _rank_weights(
            {n: stats.z_score for n, stats in analyzer.number_stats(latest, kind).items()}
        )
        for kind in _KINDS
    }


def _cold_weights(draws: list[Draw]) -> dict[BallKind, _Weights]:
    """Weights favouring numbers that have gone the longest without being drawn."""
    return {
        kind: _rank_weights(
            {n: stats.current_gap for n, stats in analyzer.number_stats(draws, kind).items()}
        )
        for kind in _KINDS
    }


def _from_weights(weights: Mapping[BallKind, _Weights]) -> _Generator:
    def generate(rng: random.Random) -> _Line:
        return (
            _weighted_sample(rng, weights["main"], MAIN_COUNT),
            _weighted_sample(rng, weights["star"], STAR_COUNT),
        )

    return generate


def _hot(draws: list[Draw], window: int) -> _Generator:
    return _from_weights(_hot_weights(draws, window))


def _cold(draws: list[Draw], _window: int) -> _Generator:
    return _from_weights(_cold_weights(draws))


def _balanced(draws: list[Draw], window: int) -> _Generator:
    hot = _hot_weights(draws, window)
    cold = _cold_weights(draws)
    typical_odd = _most_common(analyzer.odd_count_distribution(draws), 2)
    typical_low = _most_common(analyzer.low_count_distribution(draws), 2)

    def generate(rng: random.Random) -> _Line:
        for _ in range(_MAX_ATTEMPTS):
            hot_picks = rng.choice((MAIN_COUNT // 2, MAIN_COUNT - MAIN_COUNT // 2))
            numbers = _weighted_sample(rng, hot["main"], hot_picks)
            numbers += _weighted_sample(rng, cold["main"], MAIN_COUNT - hot_picks, exclude=numbers)
            if (
                analyzer.odd_count(numbers) in typical_odd
                and analyzer.low_count(numbers) in typical_low
            ):
                break
        # If nothing fitted, the history is so short or lopsided that its hot and overdue
        # numbers cannot form a line with its usual splits: the last candidate is kept.
        stars = _weighted_sample(rng, hot["star"], STAR_COUNT // 2)
        stars += _weighted_sample(rng, cold["star"], STAR_COUNT - len(stars), exclude=stars)
        return numbers, stars

    return generate


def _weighted(draws: list[Draw], _window: int) -> _Generator:
    base: dict[BallKind, _Weights] = {}
    pairs = {kind: analyzer.pair_counts(draws, kind) for kind in _KINDS}
    for kind in _KINDS:
        stats = analyzer.number_stats(draws, kind)
        frequency = _scaled({n: s.z_score for n, s in stats.items()})
        gap = _scaled({n: s.current_gap for n, s in stats.items()})
        base[kind] = {n: _BASE_WEIGHT + frequency[n] + gap[n] for n in stats}

    def pick(rng: random.Random, kind: BallKind) -> list[int]:
        chosen: list[int] = []
        for _ in range(picks_per_draw(kind)):
            candidates = [n for n in base[kind] if n not in chosen]
            # How often each candidate was drawn together with the numbers chosen so far.
            affinity = _scaled(
                {n: sum(pairs[kind][min(n, c), max(n, c)] for c in chosen) for n in candidates}
            )
            weights = {n: base[kind][n] + affinity[n] for n in candidates}
            chosen += _weighted_sample(rng, weights, 1)
        return chosen

    return lambda rng: (pick(rng, "main"), pick(rng, "star"))


def _anti(draws: list[Draw], _window: int) -> _Generator:
    if len(draws) < _MIN_PATTERN_HISTORY:
        raise HistoryError(
            f"the anti strategy needs at least {_MIN_PATTERN_HISTORY} draws of history, "
            f"got {len(draws)}"
        )
    low_sum, high_sum = _central_interval(analyzer.sum_distribution(draws))
    low_range, high_range = _central_interval(analyzer.range_distribution(draws))
    common_odd = _common_values(analyzer.odd_count_distribution(draws))
    common_low = _common_values(analyzer.low_count_distribution(draws))
    common_runs = _common_values(analyzer.consecutive_stats(draws).longest_run_distribution)

    def generate(rng: random.Random) -> _Line:
        for _ in range(_MAX_ATTEMPTS):
            numbers, stars = _uniform_line(rng)
            if (
                low_sum <= sum(numbers) <= high_sum
                and low_range <= max(numbers) - min(numbers) <= high_range
                and analyzer.odd_count(numbers) in common_odd
                and analyzer.low_count(numbers) in common_low
                and analyzer.longest_run(numbers) in common_runs
            ):
                return numbers, stars
        raise HistoryError("no line matches the usual patterns of the past draws")

    return generate


def _random(_draws: list[Draw], _window: int) -> _Generator:
    return _uniform_line


class _Strategy(NamedTuple):
    description: str
    build: Callable[[list[Draw], int], _Generator]
    #: Whether the lines depend on the recent window.
    uses_window: bool = False


_STRATEGIES: dict[str, _Strategy] = {
    "hot": _Strategy(
        "favour numbers drawn more often than expected in recent draws", _hot, uses_window=True
    ),
    "cold": _Strategy("favour overdue numbers, those not drawn for the longest time", _cold),
    "balanced": _Strategy(
        "hot and overdue numbers, usual odd/even and low/high mix", _balanced, uses_window=True
    ),
    "weighted": _Strategy("weight numbers by frequency, overdue gap and pair affinity", _weighted),
    "anti": _Strategy("uniformly random lines that avoid historically rare patterns", _anti),
    BASELINE: _Strategy("uniformly random lines", _random),
}

#: The strategies on offer: name -> one-line description.
STRATEGIES: dict[str, str] = {
    name: strategy.description for name, strategy in _STRATEGIES.items() if name != BASELINE
}

#: The strategies whose lines depend on the recent window (the others ignore it).
WINDOW_STRATEGIES: frozenset[str] = frozenset(
    name for name, strategy in _STRATEGIES.items() if strategy.uses_window
)


def suggest(
    draws: Iterable[Draw],
    strategy: str = "balanced",
    *,
    lines: int = 1,
    window: int = analyzer.DEFAULT_WINDOW,
    seed: int | None = None,
) -> list[Suggestion]:
    """Suggest ``lines`` distinct lines for the next draw.

    ``window`` is the number of most recent draws that define "hot" numbers.
    The same ``seed`` with the same history and arguments gives the same lines.

    * ``hot`` ranks numbers by their z-score over the recent window and samples
      them with a probability that decreases with rank.
    * ``cold`` does the same with the number of draws since each number last came up.
    * ``balanced`` takes about half of each line from the hot ranking and the rest
      from the cold one, and only keeps lines whose odd/even and low/high splits
      are among the two most common in the history (unless no such line turns up,
      which only happens with a handful of draws).
    * ``weighted`` picks numbers one at a time with a weight combining their overall
      frequency (z-score), their current gap and how often they were drawn together
      with the numbers already picked.
    * ``anti`` draws uniformly random lines and rejects those whose sum, range,
      odd/even split, low/high split or run of consecutive numbers is rare in
      the history.

    :data:`BASELINE` is accepted as well, for comparisons: uniformly random lines.

    Raises :class:`ValueError` for an invalid argument, and its subclass
    :class:`~euromillions.errors.HistoryError` when the history is empty or too
    short for the strategy.
    """
    if strategy not in _STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; choose from {', '.join(STRATEGIES)}")
    if lines < 1:
        raise ValueError(f"lines must be at least 1, got {lines}")
    if lines > POSSIBLE_LINES:  # the lines are all different
        raise ValueError(f"there are only {POSSIBLE_LINES:,} different lines, got {lines:,}")
    if window < 1:
        raise ValueError(f"window must be at least 1, got {window}")
    history = list(draws)
    if not history:
        raise HistoryError("cannot suggest numbers without any draw history")

    generate = _STRATEGIES[strategy].build(history, window)
    rng = random.Random(seed)
    suggestions: dict[Suggestion, None] = {}
    for _ in range(lines * _MAX_ATTEMPTS):
        numbers, stars = generate(rng)
        suggestions.setdefault(Suggestion(tuple(numbers), tuple(stars), strategy))
        if len(suggestions) == lines:
            return list(suggestions)
    raise HistoryError(f"could not find {lines} distinct lines with strategy {strategy!r}")
