"""Tests for the statistical analysis of the draw history.

The small histories below are hand-made. Expected values are worked out by hand from them
(the arithmetic is spelled out next to the figures), never by re-running the analyzer's own
formulas.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

import pytest

from euromillions.analyzer import (
    LOW_MAX,
    ConsecutiveStats,
    Distribution,
    NumberStats,
    analyze,
    cold_numbers,
    consecutive_pairs,
    consecutive_stats,
    hot_numbers,
    longest_run,
    low_count,
    low_count_distribution,
    number_stats,
    odd_count,
    odd_count_distribution,
    overdue_numbers,
    pair_counts,
    range_distribution,
    recent,
    sum_distribution,
    triplet_counts,
)
from euromillions.models import BallKind, Draw


def make_draw(date: str, numbers: Iterable[int], stars: Iterable[int] = (1, 2)) -> Draw:
    return Draw(dt.date.fromisoformat(date), tuple(numbers), tuple(stars))


def star_draw(date: str, stars: Iterable[int]) -> Draw:
    """Return a draw whose main numbers do not matter to the test."""
    return make_draw(date, (10, 20, 30, 40, 50), stars)


def numbers_of(stats: Iterable[NumberStats]) -> list[int]:
    return [entry.number for entry in stats]


def assert_stats(actual: NumberStats, **expected: Any) -> None:
    """Check the given fields of ``actual``, floats to within rounding errors."""
    assert {name: getattr(actual, name) for name in expected} == pytest.approx(expected)


#: Six draws under the current rules (12 stars), called positions 0 to 5 in the comments.
HISTORY = (
    make_draw("2024-01-02", (1, 2, 3, 9, 20), (1, 2)),
    make_draw("2024-01-05", (7, 14, 21, 28, 35), (3, 4)),
    make_draw("2024-01-09", (7, 8, 21, 40, 50), (1, 5)),
    make_draw("2024-01-12", (10, 11, 12, 13, 49), (2, 12)),
    make_draw("2024-01-16", (5, 15, 25, 35, 45), (1, 2)),
    make_draw("2024-01-19", (7, 20, 21, 35, 48), (2, 3)),
)

# In each of these six draws a main number has a 5/50 chance and a star a 2/12 chance:
# 0.6 and 1.0 appearances expected, with variances 6 * 0.1 * 0.9 and 6 * 1/6 * 5/6.
MAIN_SIGMA = math.sqrt(0.54)
STAR_SIGMA = math.sqrt(5 / 6)

#: Lucky Stars under the three successive pool sizes.
ERAS = (
    star_draw("2010-03-05", (5, 9)),  # 9 stars in the pool: a 2/9 chance each
    star_draw("2012-06-12", (3, 10)),  # 11 stars: 2/11
    star_draw("2020-01-03", (5, 12)),  # 12 stars: 2/12
    star_draw("2020-01-07", (1, 2)),
    star_draw("2020-01-10", (10, 12)),
)

#: Twelve weekly draws. In the last ten, every main number comes up exactly once, except that
#: 1 also takes the place of 6 and 11, 2 the place of 16 and 45 the place of 46.
WINDOWED = (
    make_draw("2024-03-01", (6, 11, 16, 46, 50)),
    make_draw("2024-03-08", (6, 11, 16, 46, 49)),
    make_draw("2024-03-15", (1, 2, 3, 4, 5)),
    make_draw("2024-03-22", (1, 7, 8, 9, 10)),
    make_draw("2024-03-29", (1, 12, 13, 14, 15)),
    make_draw("2024-04-05", (2, 17, 18, 19, 20)),
    make_draw("2024-04-12", (21, 22, 23, 24, 25)),
    make_draw("2024-04-19", (26, 27, 28, 29, 30)),
    make_draw("2024-04-26", (31, 32, 33, 34, 35)),
    make_draw("2024-05-03", (36, 37, 38, 39, 40)),
    make_draw("2024-05-10", (41, 42, 43, 44, 45)),
    make_draw("2024-05-17", (45, 47, 48, 49, 50)),
)

#: Five draws with 9 stars in the pool, then eight with 12. Appearances per star:
#: 1: 6, 2: 3, 3: 3, 4: 0, 5: 2, 6: 2, 7: 1, 8: 1, 9: 2, 10: 1, 11: 4, 12: 1.
TIES = (
    star_draw("2010-03-05", (1, 2)),
    star_draw("2010-03-12", (1, 3)),
    star_draw("2010-03-19", (1, 5)),
    star_draw("2010-03-26", (2, 6)),
    star_draw("2010-04-02", (3, 9)),
    star_draw("2020-01-03", (1, 11)),
    star_draw("2020-01-07", (1, 11)),
    star_draw("2020-01-10", (1, 11)),
    star_draw("2020-01-14", (2, 11)),
    star_draw("2020-01-17", (3, 10)),
    star_draw("2020-01-21", (5, 12)),
    star_draw("2020-01-24", (6, 7)),
    star_draw("2020-01-28", (8, 9)),
)

#: First draw and Lucky Star pool size of the three eras of :data:`SYNTHETIC`.
SYNTHETIC_ERAS = ((dt.date(2009, 1, 2), 9), (dt.date(2012, 1, 3), 11), (dt.date(2020, 1, 3), 12))

_rng = random.Random(20040213)

#: 300 random valid draws: 100 weekly ones under each Lucky Star pool size.
SYNTHETIC = tuple(
    Draw(
        start + dt.timedelta(weeks=week),
        tuple(_rng.sample(range(1, 51), 5)),
        tuple(_rng.sample(range(1, pool + 1), 2)),
    )
    for start, pool in SYNTHETIC_ERAS
    for week in range(100)
)

#: Every public function that takes nothing but a draw history.
HISTORY_FUNCTIONS: list[Callable[[Iterable[Draw]], Any]] = [
    number_stats,
    hot_numbers,
    cold_numbers,
    overdue_numbers,
    pair_counts,
    triplet_counts,
    consecutive_stats,
    sum_distribution,
    range_distribution,
    odd_count_distribution,
    low_count_distribution,
    analyze,
]


class TestLineHelpers:
    def test_odd_count(self) -> None:
        assert odd_count((1, 2, 3, 9, 20)) == 3
        assert odd_count((2, 4, 50)) == 0
        assert odd_count(()) == 0

    def test_low_count_splits_the_pool_in_two_halves(self) -> None:
        assert LOW_MAX == 25
        assert low_count((1, 25, 26, 50, 13)) == 3
        assert low_count(range(1, 51)) == 25
        assert low_count(()) == 0

    @pytest.mark.parametrize(
        ("numbers", "pairs", "run"),
        [
            ((1, 2, 3, 9, 20), 2, 3),
            ((20, 3, 1, 9, 2), 2, 3),  # the order does not matter
            ((4, 5, 20, 21, 40), 2, 2),  # two separate pairs
            ((1, 2, 10, 11, 12), 3, 3),  # the longest run is not the first one
            ((10, 11, 12, 13, 14), 4, 5),
            ((1, 3, 5, 7, 9), 0, 1),
            ((49, 50), 1, 2),
            ((7,), 0, 1),
            ((), 0, 0),
        ],
    )
    def test_consecutive_pairs_and_longest_run(
        self, numbers: tuple[int, ...], pairs: int, run: int
    ) -> None:
        assert consecutive_pairs(numbers) == pairs
        assert longest_run(numbers) == run

    def test_helpers_accept_one_shot_iterables(self) -> None:
        assert odd_count(iter((1, 2, 3))) == 2
        assert low_count(iter((1, 2, 30))) == 2
        assert consecutive_pairs(iter((1, 2, 3))) == 2
        assert longest_run(iter((1, 2, 3))) == 3


class TestRecent:
    def test_keeps_the_latest_draws_in_chronological_order(self) -> None:
        shuffled = [HISTORY[i] for i in (3, 5, 0, 4, 1, 2)]
        assert recent(shuffled, 2) == [HISTORY[4], HISTORY[5]]
        assert recent(shuffled, 1) == [HISTORY[5]]

    def test_none_keeps_everything(self) -> None:
        assert recent(reversed(HISTORY), None) == list(HISTORY)

    def test_count_beyond_the_history_keeps_everything(self) -> None:
        assert recent(reversed(HISTORY), 7) == list(HISTORY)

    def test_input_is_left_untouched(self) -> None:
        shuffled = [HISTORY[i] for i in (3, 5, 0, 4, 1, 2)]
        recent(shuffled, None)
        assert shuffled == [HISTORY[i] for i in (3, 5, 0, 4, 1, 2)]

    def test_entries_sharing_a_date_do_not_make_the_result_order_dependent(self) -> None:
        one = make_draw("2024-01-02", (1, 2, 3, 4, 5))
        other = make_draw("2024-01-02", (6, 7, 8, 9, 10))
        assert recent([one, other], 1) == recent([other, one], 1)

    @pytest.mark.parametrize("count", [0, -1])
    def test_count_must_be_positive(self, count: int) -> None:
        with pytest.raises(ValueError, match="count"):
            recent(HISTORY, count)


class TestNumberStats:
    def test_one_entry_per_number_of_the_current_pool(self) -> None:
        assert list(number_stats(HISTORY)) == list(range(1, 51))
        assert list(number_stats(HISTORY, "star")) == list(range(1, 13))
        assert all(n == entry.number for n, entry in number_stats(HISTORY, "star").items())

    def test_number_drawn_in_back_to_back_draws(self) -> None:
        # 7 came up at positions 1, 2 and 5: gaps of 0 and 2 draws between its appearances.
        assert_stats(
            number_stats(HISTORY)[7],
            count=3,
            eligible_draws=6,
            expected=0.6,
            z_score=(3 - 0.6) / MAIN_SIGMA,  # +3.27
            current_gap=0,
            average_gap=1.0,
            longest_gap=2,
            last_seen=dt.date(2024, 1, 19),
            frequency=0.5,
        )

    def test_number_drawn_in_the_first_and_last_draws(self) -> None:
        assert_stats(
            number_stats(HISTORY)[20],
            count=2,
            expected=0.6,
            z_score=(2 - 0.6) / MAIN_SIGMA,  # +1.91
            current_gap=0,
            average_gap=4.0,
            longest_gap=4,
            last_seen=dt.date(2024, 1, 19),
        )

    def test_number_drawn_once(self) -> None:
        stats = number_stats(HISTORY)
        # 1 only came up at position 0: its current gap is also its longest one.
        assert_stats(
            stats[1],
            count=1,
            z_score=(1 - 0.6) / MAIN_SIGMA,  # +0.54
            current_gap=5,
            average_gap=None,
            longest_gap=5,
            last_seen=dt.date(2024, 1, 2),
        )
        # 49 only came up at position 3: the 3 draws before count as a gap, like the 2 after.
        assert_stats(
            stats[49],
            count=1,
            current_gap=2,
            average_gap=None,
            longest_gap=3,
            last_seen=dt.date(2024, 1, 12),
        )

    def test_number_never_drawn(self) -> None:
        assert_stats(
            number_stats(HISTORY)[4],
            count=0,
            eligible_draws=6,
            expected=0.6,
            z_score=(0 - 0.6) / MAIN_SIGMA,  # -0.82
            current_gap=6,
            average_gap=None,
            longest_gap=6,
            last_seen=None,
            frequency=0.0,
        )

    def test_stars_use_their_own_odds(self) -> None:
        stats = number_stats(HISTORY, "star")
        # Star 2 came up at positions 0, 3, 4 and 5: gaps of 2, 0 and 0 draws.
        assert_stats(
            stats[2],
            count=4,
            eligible_draws=6,
            expected=1.0,
            z_score=(4 - 1) / STAR_SIGMA,  # +3.29
            current_gap=0,
            average_gap=2 / 3,
            longest_gap=2,
            last_seen=dt.date(2024, 1, 19),
            frequency=4 / 6,
        )
        # Star 1 came up at positions 0, 2 and 4.
        assert_stats(
            stats[1],
            count=3,
            z_score=(3 - 1) / STAR_SIGMA,  # +2.19
            current_gap=1,
            average_gap=1.0,
            longest_gap=1,
            last_seen=dt.date(2024, 1, 16),
        )
        assert_stats(stats[6], count=0, z_score=(0 - 1) / STAR_SIGMA, current_gap=6, longest_gap=6)

    def test_z_score_is_exactly_zero_when_count_equals_expected(self) -> None:
        # Stars 4, 5 and 12 came up once, exactly the 6 * 2/12 expected. Summing six float
        # sixths term by term gives 0.9999999999999999, which would make them look "hot".
        stats = number_stats(HISTORY, "star")
        assert [stats[star].z_score for star in (4, 5, 12)] == [0.0, 0.0, 0.0]
        assert [stats[star].expected for star in (4, 5, 12)] == [1.0, 1.0, 1.0]

    def test_empty_history_gives_blank_stats_for_the_whole_pool(self) -> None:
        for kind, pool in (("main", 50), ("star", 12)):
            stats = number_stats([], kind)  # type: ignore[arg-type]
            assert list(stats) == list(range(1, pool + 1))
            for number, entry in stats.items():
                assert entry == NumberStats(
                    number=number,
                    count=0,
                    eligible_draws=0,
                    expected=0.0,
                    z_score=0.0,
                    current_gap=0,
                    average_gap=None,
                    longest_gap=0,
                    last_seen=None,
                )
                assert entry.frequency == 0.0


class TestStarEligibility:
    def test_star_of_the_original_pool_is_eligible_in_every_draw(self) -> None:
        # Star 5 came up at positions 0 and 2 of the 5 draws.
        assert_stats(
            number_stats(ERAS, "star")[5],
            count=2,
            eligible_draws=5,
            expected=2 / 9 + 2 / 11 + 3 * 2 / 12,  # 0.904
            z_score=(2 - (2 / 9 + 2 / 11 + 3 * 2 / 12))
            / math.sqrt(2 / 9 * 7 / 9 + 2 / 11 * 9 / 11 + 3 * 2 / 12 * 10 / 12),  # +1.28
            current_gap=2,
            average_gap=1.0,
            longest_gap=2,
            last_seen=dt.date(2020, 1, 3),
            frequency=2 / 5,
        )

    def test_star_added_in_2011_ignores_the_earlier_draw(self) -> None:
        stats = number_stats(ERAS, "star")
        # Star 10 came up in the first and the last of the 4 draws held since it exists.
        assert_stats(
            stats[10],
            count=2,
            eligible_draws=4,
            expected=2 / 11 + 3 * 2 / 12,  # 0.682
            z_score=(2 - (2 / 11 + 3 * 2 / 12))
            / math.sqrt(2 / 11 * 9 / 11 + 3 * 2 / 12 * 10 / 12),  # +1.75
            current_gap=0,
            average_gap=2.0,
            longest_gap=2,
            last_seen=dt.date(2020, 1, 10),
            frequency=2 / 4,
        )
        # Star 11 never came up: its gap is 4 draws long, not 5.
        assert_stats(
            stats[11],
            count=0,
            eligible_draws=4,
            expected=2 / 11 + 3 * 2 / 12,
            z_score=(0 - (2 / 11 + 3 * 2 / 12))
            / math.sqrt(2 / 11 * 9 / 11 + 3 * 2 / 12 * 10 / 12),  # -0.91
            current_gap=4,
            average_gap=None,
            longest_gap=4,
            last_seen=None,
        )

    def test_star_added_in_2016_only_counts_the_draws_since(self) -> None:
        # Star 12 came up in the first and the last of the 3 draws held since it exists: the
        # two earlier draws are no gap before its first appearance.
        assert_stats(
            number_stats(ERAS, "star")[12],
            count=2,
            eligible_draws=3,
            expected=3 * 2 / 12,
            z_score=(2 - 0.5) / math.sqrt(3 * 2 / 12 * 10 / 12),  # +2.32
            current_gap=0,
            average_gap=1.0,
            longest_gap=1,
            last_seen=dt.date(2020, 1, 10),
            frequency=2 / 3,
        )

    def test_star_that_was_never_eligible(self) -> None:
        stats = number_stats(ERAS[:1], "star")
        assert list(stats) == list(range(1, 13))
        for star in (10, 11, 12):
            assert_stats(
                stats[star],
                count=0,
                eligible_draws=0,
                expected=0.0,
                z_score=0.0,
                current_gap=0,
                average_gap=None,
                longest_gap=0,
                last_seen=None,
                frequency=0.0,
            )
        assert_stats(stats[9], count=1, eligible_draws=1, expected=2 / 9, current_gap=0)
        assert_stats(stats[8], count=0, eligible_draws=1, expected=2 / 9, current_gap=1)

    def test_main_numbers_are_eligible_in_every_era(self) -> None:
        stats = number_stats(ERAS)
        assert {entry.eligible_draws for entry in stats.values()} == {5}
        assert_stats(stats[50], count=5, expected=0.5, current_gap=0, average_gap=0.0)


class TestHotAndCold:
    def test_only_the_window_counts(self) -> None:
        # Last ten draws: 1.0 appearance expected, variance 10 * 0.1 * 0.9.
        hot = hot_numbers(WINDOWED, window=10)
        assert numbers_of(hot) == [1, 2, 45]
        assert [entry.count for entry in hot] == [3, 2, 2]
        assert {entry.eligible_draws for entry in hot} == {10}
        assert [entry.z_score for entry in hot] == pytest.approx(
            [2 / math.sqrt(0.9), 1 / math.sqrt(0.9), 1 / math.sqrt(0.9)]  # +2.11, +1.05, +1.05
        )
        cold = cold_numbers(WINDOWED, window=10)
        assert numbers_of(cold) == [6, 11, 16, 46]
        assert [entry.count for entry in cold] == [0, 0, 0, 0]
        assert [entry.z_score for entry in cold] == pytest.approx([-1 / math.sqrt(0.9)] * 4)

    def test_whole_history_when_the_window_is_larger(self) -> None:
        # All twelve draws: 1.2 appearances expected, so the numbers drawn twice or more are
        # hot (6, 11, 16 and 46 included) and the 41 numbers drawn once are cold.
        hot = hot_numbers(WINDOWED)
        assert numbers_of(hot) == [1, 2, 6, 11, 16, 45, 46, 49, 50]
        assert {entry.eligible_draws for entry in hot} == {12}
        assert hot == hot_numbers(WINDOWED, window=12)
        assert numbers_of(cold_numbers(WINDOWED)) == [3, 4, 5, 7, 8, 9, 10, 12, 13, 14]
        assert len(cold_numbers(WINDOWED, top=50)) == 41

    def test_window_is_taken_by_date_not_by_input_order(self) -> None:
        assert numbers_of(hot_numbers(reversed(WINDOWED), window=10)) == [1, 2, 45]
        assert numbers_of(cold_numbers(reversed(WINDOWED), window=10)) == [6, 11, 16, 46]

    def test_numbers_drawn_exactly_as_expected_are_neither_hot_nor_cold(self) -> None:
        # In the last ten draws 43 main numbers came up exactly once, as expected.
        assert len(hot_numbers(WINDOWED, window=10, top=50)) == 3
        assert len(cold_numbers(WINDOWED, window=10, top=50)) == 4
        # Stars 4, 5 and 12 came up once in the six draws, as expected.
        assert numbers_of(hot_numbers(HISTORY, "star", top=12)) == [2, 1, 3]
        assert numbers_of(cold_numbers(HISTORY, "star", top=12)) == [6, 7, 8, 9, 10, 11]

    def test_top_limits_the_lists(self) -> None:
        assert numbers_of(hot_numbers(WINDOWED, window=10, top=2)) == [1, 2]
        assert numbers_of(cold_numbers(WINDOWED, window=10, top=3)) == [6, 11, 16]
        assert numbers_of(hot_numbers(HISTORY)) == [7, 21, 35, 20, 1, 2, 3, 5, 8, 9]
        assert numbers_of(cold_numbers(HISTORY)) == [4, 6, 16, 17, 18, 19, 22, 23, 24, 26]

    def test_order_and_tie_breaks(self) -> None:
        # Stars 1-9 were eligible in all 13 draws: 5 * 2/9 + 8 * 2/12 = 22/9 appearances
        # expected, variance 5 * 14/81 + 8 * 5/36 = 160/81. Stars 10-12 were eligible in the
        # last 8 only: 4/3 expected, variance 10/9. Hence these z-scores:
        #   star 1 (6 times):         (32/9) / (sqrt(160)/9) = +2.530
        #   star 11 (4 times):        (8/3) / (sqrt(10)/3)   = +2.530, the same
        #   stars 2, 3 (3 times):     5 / sqrt(160)          = +0.395
        #   stars 5, 6, 9 (twice):    -4 / sqrt(160)         = -0.316
        #   stars 10, 12 (once):      (-1/3) / (sqrt(10)/3)  = -0.316, the same
        #   stars 7, 8 (once):        -13 / sqrt(160)        = -1.028
        #   star 4 (never):           -22 / sqrt(160)        = -1.739
        stats = number_stats(TIES, "star")
        assert stats[1].z_score == stats[11].z_score == pytest.approx(8 / math.sqrt(10))
        assert stats[9].z_score == stats[12].z_score == pytest.approx(-1 / math.sqrt(10))

        hot = hot_numbers(TIES, "star", top=12)
        # Highest z-score first; equal z-scores: higher count first, then lower number.
        assert numbers_of(hot) == [1, 11, 2, 3]
        assert [entry.count for entry in hot] == [6, 4, 3, 3]

        cold = cold_numbers(TIES, "star", top=12)
        # Lowest z-score first; equal z-scores: lower count first, then lower number.
        assert numbers_of(cold) == [4, 7, 8, 10, 12, 5, 6, 9]
        assert [entry.count for entry in cold] == [0, 1, 1, 1, 1, 2, 2, 2]
        assert [entry.z_score for entry in cold] == pytest.approx(
            [-22 / math.sqrt(160), *[-13 / math.sqrt(160)] * 2, *[-1 / math.sqrt(10)] * 5]
        )

    @pytest.mark.parametrize("function", [hot_numbers, cold_numbers])
    def test_empty_history_and_bad_arguments(self, function: Callable[..., Any]) -> None:
        assert function([]) == []
        assert function([], "star") == []
        for bad in (0, -1):
            with pytest.raises(ValueError, match="window"):
                function(HISTORY, window=bad)
            with pytest.raises(ValueError, match="top"):
                function(HISTORY, top=bad)


class TestOverdue:
    def test_longest_current_gap_first_then_lowest_number(self) -> None:
        overdue = overdue_numbers(HISTORY, top=50)
        never_drawn = [4, 6, 16, 17, 18, 19, 22, 23, 24, 26, 27, 29, 30, 31, 32, 33, 34]
        never_drawn += [36, 37, 38, 39, 41, 42, 43, 44, 46, 47]
        assert numbers_of(overdue) == [
            *never_drawn,  # gap of 6
            *(1, 2, 3, 9),  # last drawn at position 0: gap of 5
            *(14, 28),  # position 1
            *(8, 40, 50),  # position 2
            *(10, 11, 12, 13, 49),  # position 3
            *(5, 15, 25, 45),  # position 4
            *(7, 20, 21, 35, 48),  # position 5: gap of 0
        ]
        assert [entry.current_gap for entry in overdue] == (
            [6] * 27 + [5] * 4 + [4] * 2 + [3] * 3 + [2] * 5 + [1] * 4 + [0] * 5
        )

    def test_covers_all_the_draws_and_top_limits_the_list(self) -> None:
        assert numbers_of(overdue_numbers(HISTORY)) == [4, 6, 16, 17, 18, 19, 22, 23, 24, 26]
        assert numbers_of(overdue_numbers(HISTORY, top=2)) == [4, 6]
        assert numbers_of(overdue_numbers(HISTORY, "star", top=12)) == [
            *(6, 7, 8, 9, 10, 11),  # never drawn
            *(4, 5, 12, 1),  # last drawn at positions 1, 2, 3 and 4
            *(2, 3),  # in the latest draw
        ]

    def test_gaps_are_counted_in_eligible_draws(self) -> None:
        # Star 11 never came up, but in 4 eligible draws only: it ranks behind the stars
        # that missed all 5 draws, level with star 9 (last drawn 4 draws ago).
        overdue = overdue_numbers(ERAS, "star", top=12)
        assert numbers_of(overdue) == [4, 6, 7, 8, 9, 11, 3, 5, 1, 2, 10, 12]
        assert [entry.current_gap for entry in overdue] == [5, 5, 5, 5, 4, 4, 3, 2, 1, 1, 0, 0]

    def test_stars_that_were_never_eligible_are_left_out(self) -> None:
        overdue = overdue_numbers(ERAS[:1], "star", top=12)
        assert numbers_of(overdue) == [1, 2, 3, 4, 6, 7, 8, 5, 9]
        assert numbers_of(overdue_numbers(ERAS[:2], "star", top=12)) == [
            *(1, 2, 4, 6, 7, 8),  # never drawn in 2 draws
            *(5, 9, 11),  # 5 and 9 one draw ago; 11 never drawn in 1 eligible draw
            *(3, 10),
        ]

    def test_empty_history_and_bad_arguments(self) -> None:
        assert overdue_numbers([]) == []
        assert overdue_numbers([], "star") == []
        for bad in (0, -1):
            with pytest.raises(ValueError, match="top"):
                overdue_numbers(HISTORY, top=bad)


class TestCombinations:
    def test_pair_counts_of_main_numbers(self) -> None:
        pairs = pair_counts(HISTORY)
        # 7 and 21 came up together at positions 1, 2 and 5, with 35 at positions 1 and 5.
        assert pairs[7, 21] == 3
        assert pairs[7, 35] == 2
        assert pairs[21, 35] == 2
        assert pairs[1, 2] == 1
        assert pairs[1, 7] == 0
        assert all(a < b for a, b in pairs)
        assert sum(pairs.values()) == 6 * 10  # C(5, 2) pairs per draw
        assert len(pairs) == 60 - 2 - 1 - 1

    def test_pair_counts_of_stars(self) -> None:
        assert pair_counts(HISTORY, "star") == Counter(
            {(1, 2): 2, (3, 4): 1, (1, 5): 1, (2, 12): 1, (2, 3): 1}
        )

    def test_triplet_counts(self) -> None:
        triplets = triplet_counts(HISTORY)
        assert triplets[7, 21, 35] == 2  # positions 1 and 5
        assert triplets[7, 8, 21] == 1
        assert triplets[1, 2, 3] == 1
        assert triplets[1, 2, 7] == 0
        assert all(a < b < c for a, b, c in triplets)
        assert sum(triplets.values()) == 6 * 10  # C(5, 3) triplets per draw
        assert len(triplets) == 60 - 1

    def test_empty_history(self) -> None:
        assert pair_counts([]) == Counter()
        assert pair_counts([], "star") == Counter()
        assert triplet_counts([]) == Counter()


class TestConsecutive:
    def test_consecutive_stats(self) -> None:
        stats = consecutive_stats(HISTORY)
        # Consecutive pairs / longest run by position: 2/3 (1 2 3), 0/1, 1/2 (7 8),
        # 3/4 (10 11 12 13), 0/1 and 1/2 (20 21).
        assert stats == ConsecutiveStats(
            draws=6,
            draws_with_consecutive=4,
            pair_count_distribution={0: 2, 1: 2, 2: 1, 3: 1, 4: 0},
            longest_run_distribution={1: 2, 2: 2, 3: 1, 4: 1, 5: 0},
        )
        assert list(stats.pair_count_distribution) == [0, 1, 2, 3, 4]
        assert list(stats.longest_run_distribution) == [1, 2, 3, 4, 5]
        assert stats.share == pytest.approx(4 / 6)

    def test_empty_history(self) -> None:
        stats = consecutive_stats([])
        assert stats == ConsecutiveStats(
            draws=0,
            draws_with_consecutive=0,
            pair_count_distribution=dict.fromkeys(range(5), 0),
            longest_run_distribution=dict.fromkeys(range(1, 6), 0),
        )
        assert stats.share == 0.0


class TestDistribution:
    #: Mean 5 and population standard deviation exactly 2 (the sample one would be 2.14).
    SAMPLE = Distribution((2, 4, 4, 4, 5, 5, 7, 9))

    def test_summary_properties(self) -> None:
        assert self.SAMPLE.count == 8
        assert self.SAMPLE.minimum == 2
        assert self.SAMPLE.maximum == 9
        assert self.SAMPLE.mean == pytest.approx(5.0)
        assert self.SAMPLE.median == pytest.approx(4.5)  # between the 4th and 5th values
        assert self.SAMPLE.stdev == pytest.approx(2.0)

    def test_median_of_an_odd_number_of_values_is_a_float_too(self) -> None:
        median = Distribution((1, 2, 4)).median
        assert median == 2.0
        assert isinstance(median, float)

    def test_values_are_sorted(self) -> None:
        assert Distribution((3, 1, 2)).values == (1, 2, 3)

    def test_empty_distribution_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="observation"):
            Distribution(())

    @pytest.mark.parametrize(
        ("q", "expected"),
        [
            (0, 2),  # the minimum
            (0.125, 2),  # 1 observation of 8 is enough
            (0.13, 4),  # more than 1: the 2nd value
            (0.5, 4),  # the 4th value, although the median is 4.5
            (0.51, 5),
            (0.75, 5),  # the 6th value
            (0.76, 7),
            (0.875, 7),
            (0.9, 9),
            (1, 9),  # the maximum
        ],
    )
    def test_nearest_rank_quantile(self, q: float, expected: int) -> None:
        assert self.SAMPLE.quantile(q) == expected

    def test_quantile_of_a_single_value(self) -> None:
        single = Distribution((42,))
        assert [single.quantile(q) for q in (0, 0.3, 1)] == [42, 42, 42]

    def test_quantile_is_immune_to_float_noise(self) -> None:
        # The k-th percentile of 1..100 is k, yet 0.07 * 100 == 7.000000000000001 in floats.
        percent = Distribution(tuple(range(1, 101)))
        assert [percent.quantile(k / 100) for k in range(1, 101)] == list(range(1, 101))

    @pytest.mark.parametrize("q", [-0.01, 1.01, 2, float("nan")])
    def test_quantile_rejects_q_outside_0_1(self, q: float) -> None:
        with pytest.raises(ValueError, match="between 0 and 1"):
            self.SAMPLE.quantile(q)

    def test_histogram(self) -> None:
        assert list(self.SAMPLE.histogram().items()) == [(2, 1), (4, 3), (5, 2), (7, 1), (9, 1)]
        assert list(Distribution((3, 1, 3, 2)).histogram().items()) == [(1, 1), (2, 1), (3, 2)]

    def test_share_between_includes_both_bounds(self) -> None:
        assert self.SAMPLE.share_between(4, 5) == pytest.approx(5 / 8)
        assert self.SAMPLE.share_between(5, 7) == pytest.approx(3 / 8)
        assert self.SAMPLE.share_between(3, 3) == 0.0
        assert self.SAMPLE.share_between(0, 100) == 1.0
        assert self.SAMPLE.share_between(5, 4) == 0.0


class TestDrawMeasures:
    def test_sum_distribution(self) -> None:
        sums = sum_distribution(HISTORY)
        assert sums.values == (35, 95, 105, 125, 126, 131)  # positions 0, 3, 1, 4, 2 and 5
        assert sums.mean == pytest.approx(617 / 6)
        assert sums.median == pytest.approx(115.0)
        assert sums.share_between(100, 130) == pytest.approx(3 / 6)

    def test_range_distribution(self) -> None:
        ranges = range_distribution(HISTORY)
        assert ranges.values == (19, 28, 39, 40, 41, 43)  # positions 0, 1, 3, 4, 5 and 2
        assert ranges.quantile(0.5) == 39

    def test_odd_count_distribution(self) -> None:
        # Odd numbers by position: 3, 3, 2, 3, 5 and 3.
        assert list(odd_count_distribution(HISTORY).items()) == [
            *((0, 0), (1, 0), (2, 1)),
            *((3, 4), (4, 0), (5, 1)),
        ]

    def test_low_count_distribution(self) -> None:
        # Low numbers by position: 5, 3, 3, 4, 3 (25 is low) and 3.
        assert list(low_count_distribution(HISTORY).items()) == [
            *((0, 0), (1, 0), (2, 0)),
            *((3, 4), (4, 1), (5, 1)),
        ]

    def test_empty_history(self) -> None:
        assert list(odd_count_distribution([]).items()) == [(k, 0) for k in range(6)]
        assert list(low_count_distribution([]).items()) == [(k, 0) for k in range(6)]
        with pytest.raises(ValueError, match="observation"):
            sum_distribution([])
        with pytest.raises(ValueError, match="observation"):
            range_distribution([])


class TestAnalyze:
    def test_report_of_the_small_history(self) -> None:
        report = analyze(HISTORY, window=4, top=3)
        assert report.total_draws == 6
        assert report.first_date == dt.date(2024, 1, 2)
        assert report.last_date == dt.date(2024, 1, 19)
        assert report.window == 4
        # Per-number statistics cover the six draws...
        assert list(report.number_stats) == list(range(1, 51))
        assert list(report.star_stats) == list(range(1, 13))
        assert report.number_stats[7].count == 3
        assert report.star_stats[2].count == 4
        # ... but the hot and cold lists only positions 2 to 5, where 7, 21 and 35 came up
        # twice, star 2 three times and star 1 twice.
        assert numbers_of(report.hot_numbers) == [7, 21, 35]
        assert [entry.count for entry in report.hot_numbers] == [2, 2, 2]
        assert numbers_of(report.cold_numbers) == [1, 2, 3]
        assert numbers_of(report.hot_stars) == [2, 1, 3]
        assert numbers_of(report.cold_stars) == [4, 6, 7]
        assert numbers_of(report.overdue_numbers) == [4, 6, 16]
        assert numbers_of(report.overdue_stars) == [6, 7, 8]
        # Most frequent first, then in ascending order.
        assert report.top_pairs == [((7, 21), 3), ((7, 35), 2), ((21, 35), 2)]
        assert report.top_triplets == [((7, 21, 35), 2), ((1, 2, 3), 1), ((1, 2, 9), 1)]
        assert report.top_star_pairs == [((1, 2), 2), ((1, 5), 1), ((2, 3), 1)]
        assert report.consecutive.draws_with_consecutive == 4
        assert report.sums.values == (35, 95, 105, 125, 126, 131)
        assert report.ranges.values == (19, 28, 39, 40, 41, 43)
        assert report.odd_counts == {0: 0, 1: 0, 2: 1, 3: 4, 4: 0, 5: 1}
        assert report.low_counts == {0: 0, 1: 0, 2: 0, 3: 4, 4: 1, 5: 1}

    def test_window_is_capped_by_the_history(self) -> None:
        assert analyze(HISTORY).window == 6
        assert analyze(HISTORY, window=6).window == 6
        assert analyze(HISTORY, window=1).window == 1

    def test_top_bounds_every_ranking(self) -> None:
        report = analyze(SYNTHETIC, top=4)
        rankings = (
            *(report.hot_numbers, report.cold_numbers, report.overdue_numbers),
            *(report.hot_stars, report.cold_stars, report.overdue_stars),
            *(report.top_pairs, report.top_triplets, report.top_star_pairs),
        )
        assert [len(ranking) for ranking in rankings] == [4] * 9

    def test_agrees_with_the_individual_functions(self) -> None:
        report = analyze(SYNTHETIC, window=80, top=7)
        assert report.total_draws == 300
        assert report.first_date == dt.date(2009, 1, 2)
        assert report.last_date == dt.date(2021, 11, 26)
        assert report.window == 80
        assert report.number_stats == number_stats(SYNTHETIC)
        assert report.star_stats == number_stats(SYNTHETIC, "star")
        assert report.hot_numbers == hot_numbers(SYNTHETIC, window=80, top=7)
        assert report.cold_numbers == cold_numbers(SYNTHETIC, window=80, top=7)
        assert report.overdue_numbers == overdue_numbers(SYNTHETIC, top=7)
        assert report.hot_stars == hot_numbers(SYNTHETIC, "star", window=80, top=7)
        assert report.cold_stars == cold_numbers(SYNTHETIC, "star", window=80, top=7)
        assert report.overdue_stars == overdue_numbers(SYNTHETIC, "star", top=7)
        assert report.consecutive == consecutive_stats(SYNTHETIC)
        assert report.sums == sum_distribution(SYNTHETIC)
        assert report.ranges == range_distribution(SYNTHETIC)
        assert report.odd_counts == odd_count_distribution(SYNTHETIC)
        assert report.low_counts == low_count_distribution(SYNTHETIC)
        for ranking, counts in (
            (report.top_pairs, pair_counts(SYNTHETIC)),
            (report.top_triplets, triplet_counts(SYNTHETIC)),
            (report.top_star_pairs, pair_counts(SYNTHETIC, "star")),
        ):
            assert len(ranking) == 7
            assert all(counts[combination] == count for combination, count in ranking)
            # Sorted by decreasing count, then ascending combination...
            assert ranking == sorted(ranking, key=lambda item: (-item[1], item[0]))
            # ... and nothing left out ranks before the last entry.
            last_combination, last_count = ranking[-1]
            assert not any(
                (count, last_combination) > (last_count, combination)
                for combination, count in counts.items()
                if (combination, count) not in ranking
            )

    def test_empty_history_and_bad_arguments(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            analyze([])
        for bad in (0, -1):
            with pytest.raises(ValueError, match="window"):
                analyze(HISTORY, window=bad)
            with pytest.raises(ValueError, match="top"):
                analyze(HISTORY, top=bad)


class TestInputHandling:
    @pytest.mark.parametrize("function", HISTORY_FUNCTIONS)
    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_input_order_does_not_matter(
        self, function: Callable[[Iterable[Draw]], Any], seed: int
    ) -> None:
        shuffled = list(SYNTHETIC)
        random.Random(seed).shuffle(shuffled)
        before = list(shuffled)
        assert function(shuffled) == function(SYNTHETIC)
        assert shuffled == before

    @pytest.mark.parametrize("function", [pair_counts, triplet_counts])
    def test_counters_are_filled_in_chronological_order(
        self, function: Callable[[Iterable[Draw]], Counter[tuple[int, ...]]]
    ) -> None:
        # Insertion order decides how Counter.most_common() breaks ties.
        assert list(function(reversed(SYNTHETIC))) == list(function(SYNTHETIC))
        assert function(reversed(HISTORY)).most_common(2) == function(HISTORY).most_common(2)

    @pytest.mark.parametrize("function", HISTORY_FUNCTIONS)
    def test_one_shot_iterables_are_accepted(
        self, function: Callable[[Iterable[Draw]], Any]
    ) -> None:
        assert function(iter(HISTORY)) == function(HISTORY)


class TestSyntheticHistory:
    """Invariants over 300 random draws spanning the three Lucky Star pool sizes."""

    def test_main_numbers_add_up(self) -> None:
        stats = number_stats(SYNTHETIC)
        assert sum(entry.count for entry in stats.values()) == 5 * 300
        assert sum(entry.expected for entry in stats.values()) == pytest.approx(5 * 300)
        assert {entry.eligible_draws for entry in stats.values()} == {300}
        assert all(entry.expected == pytest.approx(30.0) for entry in stats.values())

    def test_stars_add_up(self) -> None:
        stats = number_stats(SYNTHETIC, "star")
        assert sum(entry.count for entry in stats.values()) == 2 * 300
        assert sum(entry.expected for entry in stats.values()) == pytest.approx(2 * 300)
        assert [entry.eligible_draws for entry in stats.values()] == [300] * 9 + [200] * 2 + [100]
        assert stats[1].expected == pytest.approx(100 * (2 / 9 + 2 / 11 + 2 / 12))
        assert stats[10].expected == pytest.approx(100 * (2 / 11 + 2 / 12))
        assert stats[12].expected == pytest.approx(100 * 2 / 12)

    @pytest.mark.parametrize("kind", ["main", "star"])
    def test_stats_match_a_scan_of_each_number(self, kind: BallKind) -> None:
        for number, entry in number_stats(SYNTHETIC, kind).items():
            # The pool only grew: a star is eligible from the first draw of its era onwards.
            first = 0 if kind == "main" or number <= 9 else 100 if number <= 11 else 200
            eligible = SYNTHETIC[first:]
            drawn = [number in (d.numbers if kind == "main" else d.stars) for d in eligible]
            # One run of dots per gap: before the first "x", between two of them, after the last.
            gaps = [len(run) for run in "".join(".x"[hit] for hit in drawn).split("x")]
            assert entry.count == sum(drawn) > 0
            assert entry.eligible_draws == len(eligible)
            assert entry.frequency == pytest.approx(sum(drawn) / len(eligible))
            assert entry.current_gap == gaps[-1]
            assert entry.longest_gap == max(gaps)
            assert entry.average_gap == pytest.approx(sum(gaps[1:-1]) / len(gaps[1:-1]))
            last_hit = max(index for index, hit in enumerate(drawn) if hit)
            assert entry.last_seen == eligible[last_hit].date
            assert (entry.z_score > 0) == (entry.count > entry.expected)

    def test_z_scores_use_the_odds_of_each_era(self) -> None:
        main = number_stats(SYNTHETIC)
        assert all(
            entry.z_score == pytest.approx((entry.count - 30) / math.sqrt(300 * 0.1 * 0.9))
            for entry in main.values()
        )
        # Variance of one draw with 9, 11 and 12 stars in the pool.
        nine, eleven, twelve = 2 / 9 * 7 / 9, 2 / 11 * 9 / 11, 2 / 12 * 10 / 12
        stars = number_stats(SYNTHETIC, "star")
        for star, variance in (
            (1, 100 * (nine + eleven + twelve)),
            (11, 100 * (eleven + twelve)),
            (12, 100 * twelve),
        ):
            deviation = stars[star].count - stars[star].expected
            assert stars[star].z_score == pytest.approx(deviation / math.sqrt(variance))

    def test_every_draw_is_counted_once(self) -> None:
        report = analyze(SYNTHETIC)
        assert report.sums.count == report.ranges.count == 300
        assert sum(report.sums.histogram().values()) == 300
        assert sum(report.ranges.histogram().values()) == 300
        assert sum(report.odd_counts.values()) == 300
        assert sum(report.low_counts.values()) == 300
        assert report.consecutive.draws == 300
        assert sum(report.consecutive.pair_count_distribution.values()) == 300
        assert sum(report.consecutive.longest_run_distribution.values()) == 300
        assert sum(pair_counts(SYNTHETIC).values()) == 10 * 300
        assert sum(triplet_counts(SYNTHETIC).values()) == 10 * 300
        assert sum(pair_counts(SYNTHETIC, "star").values()) == 300

    def test_measures_stay_within_their_bounds(self) -> None:
        report = analyze(SYNTHETIC)
        assert 1 + 2 + 3 + 4 + 5 <= report.sums.minimum <= report.sums.maximum <= 240
        assert 4 <= report.ranges.minimum <= report.ranges.maximum <= 49
        assert report.sums.minimum <= report.sums.quantile(0.5) <= report.sums.maximum
        # A draw without consecutive numbers has a longest run of 1, and conversely.
        consecutive = report.consecutive
        assert consecutive.pair_count_distribution[0] == consecutive.longest_run_distribution[1]
        assert consecutive.draws_with_consecutive == 300 - consecutive.pair_count_distribution[0]

    def test_hot_and_cold_lists_are_consistent(self) -> None:
        for kind in ("main", "star"):
            hot = hot_numbers(SYNTHETIC, kind, window=60, top=50)  # type: ignore[arg-type]
            cold = cold_numbers(SYNTHETIC, kind, window=60, top=50)  # type: ignore[arg-type]
            assert all(entry.z_score > 0 and entry.count > entry.expected for entry in hot)
            assert all(entry.z_score < 0 and entry.count < entry.expected for entry in cold)
            assert [e.z_score for e in hot] == sorted((e.z_score for e in hot), reverse=True)
            assert [e.z_score for e in cold] == sorted(e.z_score for e in cold)
            assert not set(numbers_of(hot)) & set(numbers_of(cold))
            assert {entry.eligible_draws for entry in hot + cold} == {60}
