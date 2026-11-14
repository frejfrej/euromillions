"""Descriptive statistics over the EuroMillions draw history.

Every draw is an independent random event: the balls have no memory. The
figures computed here describe past draws and nothing else. "Hot", "cold" and
"overdue" are labels for what already happened; they have no predictive power
whatsoever over future draws.

All functions are pure and ignore the order of their input (draws are sorted
by date internally).

Per-number statistics only count the draws in which the number was *eligible*,
i.e. part of the pool on that draw's date, and weigh each of them with its own
probability ``picks / pool size``. This matters for Lucky Stars, whose pool
grew from 9 to 11 and then 12: star 12 could only come up in about half of all
draws. Main numbers go through the same code; they are eligible in every draw.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations, pairwise

from euromillions.models import (
    MAIN_COUNT,
    MAIN_MAX,
    STAR_MAX,
    BallKind,
    Draw,
    picks_per_draw,
    pool_size,
)

#: Main numbers up to this value are "low" (1-25), the others "high" (26-50).
LOW_MAX = 25

#: One draw as seen by one kind of ball: ``(date, balls drawn, pool size)``.
_Row = tuple[dt.date, tuple[int, ...], int]


def _require_positive(name: str, value: int) -> None:
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")


def _balls(draw: Draw, kind: BallKind) -> tuple[int, ...]:
    """Return the balls of ``kind`` drawn in ``draw``."""
    return draw.numbers if kind == "main" else draw.stars


def _tally(observations: Iterable[int], keys: range) -> dict[int, int]:
    """Count the observations per value, with every value of ``keys`` present, in order."""
    counts = Counter(observations)
    return {key: counts[key] for key in keys}


# --- Single lines -------------------------------------------------------------------------------


def odd_count(numbers: Iterable[int]) -> int:
    """Return how many of ``numbers`` are odd."""
    return sum(number % 2 for number in numbers)


def low_count(numbers: Iterable[int]) -> int:
    """Return how many of ``numbers`` are low, i.e. at most :data:`LOW_MAX`."""
    return sum(number <= LOW_MAX for number in numbers)


def consecutive_pairs(numbers: Iterable[int]) -> int:
    """Return how many pairs ``(n, n + 1)`` are both in ``numbers``: 2 for ``1 2 3 9 20``."""
    present = set(numbers)
    return sum(number + 1 in present for number in present)


def longest_run(numbers: Iterable[int]) -> int:
    """Return the length of the longest run of consecutive values in ``numbers``.

    ``1 2 3 9 20`` gives 3 and a line without any consecutive numbers 1 (no numbers at all: 0).
    """
    present = set(numbers)
    longest = 0
    for start in present:
        if start - 1 not in present:
            length = 1
            while start + length in present:
                length += 1
            longest = max(longest, length)
    return longest


# --- Per-number statistics ----------------------------------------------------------------------


def recent(draws: Iterable[Draw], count: int | None) -> list[Draw]:
    """Return the ``count`` most recent draws in chronological order (``None``: all of them)."""
    if count is not None:
        _require_positive("count", count)
    # Numbers and stars only break ties between entries sharing a date, so that the result
    # never depends on the input order.
    history = sorted(draws, key=lambda draw: (draw.date, draw.numbers, draw.stars))
    return history if count is None else history[-count:]


@dataclass(frozen=True, slots=True)
class NumberStats:
    """How one main number or Lucky Star behaved over the analysed draws.

    Only the draws in which the number was eligible count. Number them ``0..m-1`` in
    chronological order and let the number come up at positions ``p1 < ... < pc``. A *gap* is
    a run of consecutive eligible draws without the number: ``p1`` draws before its first
    appearance, ``p(i+1) - p(i) - 1`` between two appearances (0 when they are back to back)
    and ``m - 1 - pc`` since the last one.
    """

    number: int
    #: Appearances in the analysed draws (``c``).
    count: int
    #: Analysed draws in which the number was in the pool (``m``).
    eligible_draws: int
    #: Appearances expected from fair draws: the sum of ``p = picks / pool size`` over the
    #: eligible draws.
    expected: float
    #: ``(count - expected) / sqrt(sum of p * (1 - p))``; 0.0 without eligible draws.
    z_score: float
    #: Eligible draws held since the last appearance (0: it was in the latest one);
    #: ``eligible_draws`` if the number never came up.
    current_gap: int
    #: Mean gap between two consecutive appearances; ``None`` with fewer than two appearances.
    #: Fair draws average about 9 for main numbers and 5 for stars under the current rules.
    average_gap: float | None
    #: Longest gap, counting the one before the first appearance and the current one.
    longest_gap: int
    #: Date of the last appearance, if any.
    last_seen: dt.date | None

    @property
    def frequency(self) -> float:
        """Share of the eligible draws in which the number came up (0.0 without any)."""
        return self.count / self.eligible_draws if self.eligible_draws else 0.0


def _fair_moments(picks: int, eligible: Sequence[_Row]) -> tuple[Fraction, Fraction]:
    """Return the mean and variance of a number's appearances if every draw were fair.

    ``picks`` balls are drawn each time, so a number of the pool has a ``picks / pool size``
    chance of coming up in each of the ``eligible`` draws.

    Exact fractions instead of floats (2/9, 2/11 and 1/6 all have rounding errors): a number
    drawn exactly as often as expected gets a z-score of exactly 0, and mathematically equal
    z-scores compare equal even between stars that were not eligible in the same draws.
    """
    expected = variance = Fraction(0)
    for pool, draws in Counter(pool for _, _, pool in eligible).items():
        chance = Fraction(picks, pool)
        expected += draws * chance
        variance += draws * chance * (1 - chance)
    return expected, variance


def _number_stats(
    number: int,
    positions: Sequence[int],
    eligible: Sequence[_Row],
    expected: Fraction,
    variance: Fraction,
) -> NumberStats:
    """Build the statistics of ``number`` from the draws in which it was eligible, oldest first.

    ``positions`` are the indexes, in ``eligible``, of the draws in which it came up.
    """
    between = [later - earlier - 1 for earlier, later in pairwise(positions)]
    leading = positions[0] if positions else len(eligible)
    current = len(eligible) - 1 - positions[-1] if positions else len(eligible)
    deviation = len(positions) - expected
    z_score = math.copysign(math.sqrt(deviation**2 / variance), deviation) if variance else 0.0
    return NumberStats(
        number=number,
        count=len(positions),
        eligible_draws=len(eligible),
        expected=float(expected),
        z_score=z_score,
        current_gap=current,
        average_gap=statistics.fmean(between) if between else None,
        longest_gap=max(leading, current, *between),
        last_seen=eligible[positions[-1]][0] if positions else None,
    )


def number_stats(draws: Iterable[Draw], kind: BallKind = "main") -> dict[int, NumberStats]:
    """Return the statistics of every number of the current ``kind`` pool, keyed by number.

    All numbers of today's pool (1-50, or 1-12 for stars) are present in ascending order, even
    for an empty history. A star that was not in the pool yet in any of the draws has
    ``eligible_draws == 0``.
    """
    picks = picks_per_draw(kind)
    rows: list[_Row] = [
        (draw.date, _balls(draw, kind), pool_size(kind, draw.date)) for draw in recent(draws, None)
    ]
    stats: dict[int, NumberStats] = {}
    # Numbers that joined the pool together were eligible in exactly the same draws: handle
    # each such group in one pass, from the numbers of the smallest pool to today's newest.
    smaller_pool = 0
    for size in sorted({pool for _, _, pool in rows} | {MAIN_MAX if kind == "main" else STAR_MAX}):
        eligible = [row for row in rows if row[2] >= size]
        positions: dict[int, list[int]] = {n: [] for n in range(smaller_pool + 1, size + 1)}
        for index, (_, balls, _) in enumerate(eligible):
            for ball in balls:
                if ball in positions:
                    positions[ball].append(index)
        expected, variance = _fair_moments(picks, eligible)
        for number, hits in positions.items():
            stats[number] = _number_stats(number, hits, eligible, expected, variance)
        smaller_pool = size
    return stats


def hot_numbers(
    draws: Iterable[Draw], kind: BallKind = "main", *, window: int = 50, top: int = 10
) -> list[NumberStats]:
    """Return up to ``top`` numbers that came up more often than expected recently.

    The statistics cover the ``window`` most recent draws only (all of them if there are
    fewer). Numbers with a positive z-score are listed by decreasing z-score; ties go to the
    higher count, then to the lower number.
    """
    _require_positive("window", window)
    _require_positive("top", top)
    stats = number_stats(recent(draws, window), kind).values()
    hot = [entry for entry in stats if entry.z_score > 0]
    return sorted(hot, key=lambda entry: (-entry.z_score, -entry.count, entry.number))[:top]


def cold_numbers(
    draws: Iterable[Draw], kind: BallKind = "main", *, window: int = 50, top: int = 10
) -> list[NumberStats]:
    """Return up to ``top`` numbers that came up less often than expected recently.

    The statistics cover the ``window`` most recent draws only (all of them if there are
    fewer). Numbers with a negative z-score are listed by increasing z-score; ties go to the
    lower count, then to the lower number.
    """
    _require_positive("window", window)
    _require_positive("top", top)
    stats = number_stats(recent(draws, window), kind).values()
    cold = [entry for entry in stats if entry.z_score < 0]
    return sorted(cold, key=lambda entry: (entry.z_score, entry.count, entry.number))[:top]


def overdue_numbers(
    draws: Iterable[Draw], kind: BallKind = "main", *, top: int = 10
) -> list[NumberStats]:
    """Return up to ``top`` numbers that have gone the longest without coming up.

    The statistics cover all the draws. Numbers are listed by decreasing ``current_gap``, then
    by number; stars that were never in the pool of the given draws are left out.
    """
    _require_positive("top", top)
    stats = [entry for entry in number_stats(draws, kind).values() if entry.eligible_draws]
    return sorted(stats, key=lambda entry: (-entry.current_gap, entry.number))[:top]


# --- Combinations -------------------------------------------------------------------------------


def pair_counts(draws: Iterable[Draw], kind: BallKind = "main") -> Counter[tuple[int, int]]:
    """Count how often each pair ``(a, b)``, ``a < b``, of ``kind`` balls was drawn together.

    Only the draws held with today's pool are counted, so that every pair had the same
    chance in each of them: older draws would favour the pairs of the stars that were
    already in the smaller pools. For main numbers that is every draw.
    """
    pool = MAIN_MAX if kind == "main" else STAR_MAX
    return Counter(
        pair
        for draw in recent(draws, None)
        if pool_size(kind, draw.date) == pool
        for pair in combinations(_balls(draw, kind), 2)
    )


def triplet_counts(draws: Iterable[Draw]) -> Counter[tuple[int, int, int]]:
    """Count how often each ascending triplet of main numbers was drawn together."""
    return Counter(
        triplet for draw in recent(draws, None) for triplet in combinations(draw.numbers, 3)
    )


@dataclass(frozen=True, slots=True)
class ConsecutiveStats:
    """How often consecutive main numbers (such as 23 and 24) were drawn together."""

    draws: int
    #: Draws with at least one pair of consecutive main numbers.
    draws_with_consecutive: int
    #: :func:`consecutive_pairs` value (0 to 4, all present) -> number of draws.
    pair_count_distribution: dict[int, int]
    #: :func:`longest_run` value (1 to 5, all present) -> number of draws.
    longest_run_distribution: dict[int, int]

    @property
    def share(self) -> float:
        """Share of the draws with at least one consecutive pair (0.0 without draws)."""
        return self.draws_with_consecutive / self.draws if self.draws else 0.0


def consecutive_stats(draws: Iterable[Draw]) -> ConsecutiveStats:
    """Summarise the consecutive main numbers of each draw."""
    lines = [draw.numbers for draw in draws]
    pairs = _tally((consecutive_pairs(line) for line in lines), range(MAIN_COUNT))
    return ConsecutiveStats(
        draws=len(lines),
        draws_with_consecutive=len(lines) - pairs[0],
        pair_count_distribution=pairs,
        longest_run_distribution=_tally(
            (longest_run(line) for line in lines), range(1, MAIN_COUNT + 1)
        ),
    )


# --- Per-draw measures --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Distribution:
    """The observed values of a per-draw measure, one per draw."""

    #: The observations in ascending order; never empty.
    values: tuple[int, ...]

    def __post_init__(self) -> None:
        values = tuple(sorted(self.values))
        if not values:
            raise ValueError("a distribution needs at least one observation")
        object.__setattr__(self, "values", values)

    @property
    def count(self) -> int:
        return len(self.values)

    @property
    def minimum(self) -> int:
        return self.values[0]

    @property
    def maximum(self) -> int:
        return self.values[-1]

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values)

    @property
    def median(self) -> float:
        return float(statistics.median(self.values))

    @property
    def stdev(self) -> float:
        """Population standard deviation."""
        return statistics.pstdev(self.values)

    def quantile(self, q: float) -> int:
        """Return the nearest-rank ``q``-quantile, for ``q`` between 0 and 1.

        This is the smallest observed value ``v`` such that at least ``q * count``
        observations are ``<= v``: ``quantile(0)`` is the minimum, ``quantile(1)`` the maximum.
        """
        if not 0 <= q <= 1:
            raise ValueError(f"q must be between 0 and 1, got {q}")
        # Rounding absorbs float noise such as 0.07 * 100 == 7.000000000000001, which would
        # otherwise select the 8th value instead of the 7th.
        rank = math.ceil(round(q * self.count, 9))
        return self.values[max(rank, 1) - 1]

    def histogram(self) -> dict[int, int]:
        """Return observed value -> occurrences, in ascending order of value."""
        return dict(Counter(self.values))

    def share_between(self, low: int, high: int) -> float:
        """Return the fraction of the observations with ``low <= value <= high``."""
        return sum(low <= value <= high for value in self.values) / self.count


def sum_distribution(draws: Iterable[Draw]) -> Distribution:
    """Return the distribution of the sum of the main numbers of each draw.

    Raises :class:`ValueError` for an empty history.
    """
    return Distribution(tuple(sum(draw.numbers) for draw in draws))


def range_distribution(draws: Iterable[Draw]) -> Distribution:
    """Return the distribution of the highest minus the lowest main number of each draw.

    Raises :class:`ValueError` for an empty history.
    """
    return Distribution(tuple(max(draw.numbers) - min(draw.numbers) for draw in draws))


def odd_count_distribution(draws: Iterable[Draw]) -> dict[int, int]:
    """Return how many draws had 0, 1, ... 5 odd main numbers (all six keys present)."""
    return _tally((odd_count(draw.numbers) for draw in draws), range(MAIN_COUNT + 1))


def low_count_distribution(draws: Iterable[Draw]) -> dict[int, int]:
    """Return how many draws had 0, 1, ... 5 low main numbers (all six keys present)."""
    return _tally((low_count(draw.numbers) for draw in draws), range(MAIN_COUNT + 1))


# --- What chance alone produces -----------------------------------------------------------------
#
# Rankings show extremes, and extremes look meaningful even when they are not. These
# yardsticks say how far fair draws typically push the most extreme of many items: each is
# the median of that extreme, i.e. the level it reaches half of the time.


def typical_extreme_z(count: int) -> float:
    """Return the largest absolute z-score typically found among ``count`` of them."""
    _require_positive("count", count)
    return statistics.NormalDist().inv_cdf((1 + 0.5 ** (1 / count)) / 2)


def typical_longest_gap(kind: BallKind = "main") -> int:
    """Return the longest current gap typically found among the ``kind`` pool, in draws.

    Under the current rules a number is absent from a draw with probability
    ``1 - picks / pool``, so each gap follows a geometric distribution.
    """
    pool = MAIN_MAX if kind == "main" else STAR_MAX
    absent = 1 - picks_per_draw(kind) / pool
    return math.floor(math.log(1 - 0.5 ** (1 / pool)) / math.log(absent))


def typical_top_count(draws: int, chance: float, candidates: int) -> int:
    """Return the count typically reached by the most frequent of ``candidates`` items.

    Each item comes up in each of the ``draws`` with probability ``chance``. The result is
    the highest count that at least one of them is more likely than not to reach, treating
    the items as independent (a good approximation when ``chance`` is small).
    """
    if draws < 1 or not 0 < chance < 1:
        return 0
    needed = 1 - 0.5 ** (1 / candidates)  # tail probability one item needs for that
    probability = (1 - chance) ** draws  # of exactly `count` occurrences, starting at 0
    below = 0.0
    for count in range(draws + 1):
        if 1 - below < needed:
            return count - 1
        below += probability
        probability *= (draws - count) / (count + 1) * chance / (1 - chance)
    return draws


# --- Full report --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ranking:
    """The combinations most often drawn together, with what chance alone would give."""

    #: ``(combination, occurrences)``, most frequent first; ties go to the lowest combination.
    entries: list[tuple[tuple[int, ...], int]]
    #: Further combinations that occurred as often as the last entry but did not fit.
    more: int
    #: Occurrences expected for any single combination if every counted draw were fair.
    expected: float
    #: Occurrences that the luckiest combination typically reaches in as many fair draws.
    typical_top: int


def _ranking(
    counts: Counter[tuple[int, int]] | Counter[tuple[int, int, int]], top: int, kind: BallKind
) -> Ranking:
    """Rank the ``top`` most frequent combinations of ``kind`` balls counted in ``counts``."""
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    entries: list[tuple[tuple[int, ...], int]] = list(ordered[:top])
    more = sum(count == entries[-1][1] for _, count in ordered[top:]) if entries else 0

    size = len(next(iter(counts), ()))
    pool = MAIN_MAX if kind == "main" else STAR_MAX
    possible = math.comb(pool, size)
    per_draw = math.comb(picks_per_draw(kind), size)  # combinations held by each counted draw
    draws = sum(counts.values()) // per_draw if counts else 0
    chance = per_draw / possible
    return Ranking(entries, more, draws * chance, typical_top_count(draws, chance, possible))


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Everything :func:`analyze` computes about a draw history.

    The hot and cold lists describe the ``window`` most recent draws; all the other fields
    cover the whole history.
    """

    total_draws: int
    first_date: dt.date
    last_date: dt.date
    #: Number of recent draws actually used for the hot and cold lists.
    window: int
    number_stats: dict[int, NumberStats]
    star_stats: dict[int, NumberStats]
    hot_numbers: list[NumberStats]
    cold_numbers: list[NumberStats]
    overdue_numbers: list[NumberStats]
    hot_stars: list[NumberStats]
    cold_stars: list[NumberStats]
    overdue_stars: list[NumberStats]
    top_pairs: Ranking
    top_triplets: Ranking
    #: Like :func:`pair_counts`, only covers the draws held with today's twelve stars.
    top_star_pairs: Ranking
    consecutive: ConsecutiveStats
    sums: Distribution
    ranges: Distribution
    odd_counts: dict[int, int]
    low_counts: dict[int, int]


def analyze(draws: Iterable[Draw], *, window: int = 50, top: int = 10) -> AnalysisReport:
    """Run every analysis of this module on ``draws``.

    ``window`` is the number of recent draws used for the hot and cold lists, ``top`` the
    maximum length of every ranking. Raises :class:`ValueError` for an empty history.
    """
    _require_positive("window", window)
    _require_positive("top", top)
    history = recent(draws, None)
    if not history:
        raise ValueError("cannot analyse an empty draw history")
    return AnalysisReport(
        total_draws=len(history),
        first_date=history[0].date,
        last_date=history[-1].date,
        window=min(window, len(history)),
        number_stats=number_stats(history, "main"),
        star_stats=number_stats(history, "star"),
        hot_numbers=hot_numbers(history, "main", window=window, top=top),
        cold_numbers=cold_numbers(history, "main", window=window, top=top),
        overdue_numbers=overdue_numbers(history, "main", top=top),
        hot_stars=hot_numbers(history, "star", window=window, top=top),
        cold_stars=cold_numbers(history, "star", window=window, top=top),
        overdue_stars=overdue_numbers(history, "star", top=top),
        top_pairs=_ranking(pair_counts(history, "main"), top, "main"),
        top_triplets=_ranking(triplet_counts(history), top, "main"),
        top_star_pairs=_ranking(pair_counts(history, "star"), top, "star"),
        consecutive=consecutive_stats(history),
        sums=sum_distribution(history),
        ranges=range_distribution(history),
        odd_counts=odd_count_distribution(history),
        low_counts=low_count_distribution(history),
    )
