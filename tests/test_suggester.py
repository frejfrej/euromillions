"""Tests for the suggestion strategies."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Iterable

import pytest

from euromillions import analyzer
from euromillions.models import Draw, Suggestion
from euromillions.suggester import (
    STRATEGIES,
    _common_values,
    _most_common,
    _rank_weights,
    _scaled,
    _weighted_sample,
    suggest,
)

ALL_STRATEGIES = list(STRATEGIES)

HOT_NUMBERS, HOT_STARS = (1, 2, 3, 4, 5), (1, 2)
COLD_NUMBERS, COLD_STARS = (46, 47, 48, 49, 50), (11, 12)


def average_hits(suggestions: Iterable[Suggestion], numbers: Iterable[int]) -> float:
    """Average count, per line, of main numbers that belong to ``numbers``."""
    lines = list(suggestions)
    return sum(len(set(line.numbers) & set(numbers)) for line in lines) / len(lines)


def average_star_hits(suggestions: Iterable[Suggestion], stars: Iterable[int]) -> float:
    lines = list(suggestions)
    return sum(len(set(line.stars) & set(stars)) for line in lines) / len(lines)


@pytest.fixture
def skewed_history(history: list[Draw]) -> list[Draw]:
    """A history with obvious hot and cold numbers.

    The last 150 draws never contain 46-50 or stars 11-12 (long overdue), and
    the last 30 all contain 1-5 and stars 1-2 (very hot).
    """
    rng = random.Random(99)
    reshaped = history[:150]
    for draw in history[150:270]:
        numbers = tuple(rng.sample(range(1, 46), 5))
        reshaped.append(Draw(draw.date, numbers, tuple(rng.sample(range(1, 11), 2))))
    reshaped += [Draw(draw.date, HOT_NUMBERS, HOT_STARS) for draw in history[270:]]
    return reshaped


class TestSuggest:
    def test_strategies_are_the_planned_ones(self) -> None:
        assert ALL_STRATEGIES == ["hot", "cold", "balanced", "weighted", "anti"]
        assert all(STRATEGIES.values())

    @pytest.mark.parametrize("strategy", ALL_STRATEGIES)
    def test_lines_follow_the_rules_and_are_distinct(
        self, strategy: str, history: list[Draw]
    ) -> None:
        suggestions = suggest(history, strategy, lines=40, seed=7)
        assert len(suggestions) == 40
        assert len(set(suggestions)) == 40
        for line in suggestions:
            assert isinstance(line, Suggestion)
            assert line.strategy == strategy
            assert len(line.numbers) == 5
            assert len(set(line.numbers)) == 5
            assert all(1 <= n <= 50 for n in line.numbers)
            assert list(line.numbers) == sorted(line.numbers)
            assert len(line.stars) == 2
            assert len(set(line.stars)) == 2
            assert all(1 <= s <= 12 for s in line.stars)
            assert list(line.stars) == sorted(line.stars)

    @pytest.mark.parametrize("strategy", ALL_STRATEGIES)
    def test_a_seed_makes_suggestions_reproducible(
        self, strategy: str, history: list[Draw]
    ) -> None:
        first = suggest(history, strategy, lines=5, seed=42)
        assert suggest(history, strategy, lines=5, seed=42) == first
        assert suggest(history, strategy, lines=5, seed=43) != first

    @pytest.mark.parametrize("strategy", ALL_STRATEGIES)
    def test_the_order_of_the_history_does_not_matter(
        self, strategy: str, history: list[Draw]
    ) -> None:
        shuffled = history[:]
        random.Random(0).shuffle(shuffled)
        assert suggest(shuffled, strategy, lines=5, seed=3) == suggest(
            history, strategy, lines=5, seed=3
        )

    @pytest.mark.parametrize("strategy", ALL_STRATEGIES)
    def test_works_without_a_seed(self, strategy: str, history: list[Draw]) -> None:
        assert len(suggest(history, strategy, lines=2)) == 2

    def test_defaults_to_one_balanced_line(self, history: list[Draw]) -> None:
        (line,) = suggest(history, seed=1)
        assert line.strategy == "balanced"

    def test_accepts_any_iterable_of_draws(self, history: list[Draw]) -> None:
        assert suggest(iter(history), "hot", seed=5) == suggest(history, "hot", seed=5)

    @pytest.mark.parametrize("strategy", ["hot", "cold", "balanced", "weighted"])
    @pytest.mark.parametrize("size", [1, 2, 5])
    def test_works_with_a_tiny_history(self, strategy: str, size: int, history: list[Draw]) -> None:
        assert len(suggest(history[:size], strategy, lines=3, seed=11)) == 3

    def test_window_larger_than_the_history_is_fine(self, history: list[Draw]) -> None:
        assert len(suggest(history[:10], "hot", lines=2, window=500, seed=1)) == 2


class TestArgumentValidation:
    def test_unknown_strategy(self, history: list[Draw]) -> None:
        with pytest.raises(ValueError, match=r"unknown strategy 'lucky'.*hot.*anti"):
            suggest(history, "lucky")

    @pytest.mark.parametrize("lines", [0, -1])
    def test_lines_must_be_positive(self, lines: int, history: list[Draw]) -> None:
        with pytest.raises(ValueError, match="lines"):
            suggest(history, "hot", lines=lines)

    @pytest.mark.parametrize("window", [0, -5])
    def test_window_must_be_positive(self, window: int, history: list[Draw]) -> None:
        with pytest.raises(ValueError, match="window"):
            suggest(history, "hot", window=window)

    @pytest.mark.parametrize("strategy", ALL_STRATEGIES)
    def test_empty_history(self, strategy: str) -> None:
        with pytest.raises(ValueError, match="history"):
            suggest([], strategy)

    def test_anti_needs_enough_history_to_know_what_is_rare(self, history: list[Draw]) -> None:
        with pytest.raises(ValueError, match="at least 20 draws"):
            suggest(history[:19], "anti")
        assert len(suggest(history[:20], "anti", lines=3, seed=1)) == 3


class TestStrategyBehaviour:
    """Each strategy must visibly favour what it claims to favour.

    A uniformly random line contains on average 0.5 of any five given numbers
    and 0.33 of any two given stars.
    """

    def test_hot_favours_recently_frequent_numbers(self, skewed_history: list[Draw]) -> None:
        lines = suggest(skewed_history, "hot", lines=300, seed=1)
        assert average_hits(lines, HOT_NUMBERS) > 1.0
        assert average_star_hits(lines, HOT_STARS) > 0.6
        assert average_hits(lines, COLD_NUMBERS) < 0.25

    def test_hot_only_looks_at_the_recent_window(self, skewed_history: list[Draw]) -> None:
        # Without its 30 most recent draws, the history has a window in which 1-5 are ordinary.
        with_streak = suggest(skewed_history, "hot", lines=300, seed=1)
        without_streak = suggest(skewed_history[:-30], "hot", lines=300, seed=1)
        assert (
            average_hits(with_streak, HOT_NUMBERS) > average_hits(without_streak, HOT_NUMBERS) + 0.5
        )

    def test_cold_favours_overdue_numbers(self, skewed_history: list[Draw]) -> None:
        lines = suggest(skewed_history, "cold", lines=300, seed=1)
        assert average_hits(lines, COLD_NUMBERS) > 1.0
        assert average_star_hits(lines, COLD_STARS) > 0.6
        assert average_hits(lines, HOT_NUMBERS) < 0.25

    def test_balanced_mixes_hot_and_overdue_numbers(self, skewed_history: list[Draw]) -> None:
        lines = suggest(skewed_history, "balanced", lines=300, seed=1)
        assert average_hits(lines, HOT_NUMBERS) > 0.6
        assert average_hits(lines, COLD_NUMBERS) > 0.6
        assert average_star_hits(lines, HOT_STARS) > 0.4
        assert average_star_hits(lines, COLD_STARS) > 0.4

    def test_balanced_keeps_the_most_common_splits(self, history: list[Draw]) -> None:
        def two_most_common(count: Callable[[Iterable[int]], int]) -> set[int]:
            seen = Counter(count(draw.numbers) for draw in history)
            return set(sorted(seen, key=lambda value: (-seen[value], value))[:2])

        typical_odd = two_most_common(lambda numbers: sum(n % 2 for n in numbers))
        typical_low = two_most_common(lambda numbers: sum(n <= 25 for n in numbers))
        assert typical_odd == {2, 3}
        assert typical_low == {2, 3}
        for line in suggest(history, "balanced", lines=200, seed=5):
            assert sum(n % 2 for n in line.numbers) in typical_odd
            assert sum(n <= 25 for n in line.numbers) in typical_low

    def test_weighted_favours_frequent_and_overdue_numbers(self, history: list[Draw]) -> None:
        # 7 is in every one of the first 200 draws, then absent for 100: both its
        # overall frequency and its current gap are the highest of all numbers.
        rng = random.Random(3)
        reshaped = []
        for index, draw in enumerate(history):
            others = rng.sample([n for n in range(1, 51) if n != 7], 5)
            numbers = (7, *others[:4]) if index < 200 else tuple(others)
            reshaped.append(Draw(draw.date, numbers, draw.stars))
        lines = suggest(reshaped, "weighted", lines=400, seed=2)
        share_with_seven = sum(7 in line.numbers for line in lines) / len(lines)
        assert share_with_seven > 0.25  # 0.10 for uniformly random lines

    def test_weighted_favours_numbers_often_drawn_together(self, history: list[Draw]) -> None:
        # 11 and 22 each come up in a third of the draws, either always together
        # or never together: only the pair affinity differs between the two histories.
        def share_of_lines_with_both(together: bool) -> float:
            rng = random.Random(4)
            reshaped = []
            for index, draw in enumerate(history):
                others = rng.sample([n for n in range(1, 51) if n not in (11, 22)], 5)
                forced = {0: (11, 22) if together else (11,), 1: () if together else (22,)}
                numbers = (*forced.get(index % 3, ()), *others)[:5]
                reshaped.append(Draw(draw.date, numbers, draw.stars))
            lines = suggest(reshaped, "weighted", lines=2000, seed=6)
            return sum({11, 22} <= set(line.numbers) for line in lines) / len(lines)

        assert share_of_lines_with_both(together=True) > 1.5 * share_of_lines_with_both(
            together=False
        )

    def test_anti_avoids_rare_patterns(self, history: list[Draw]) -> None:
        sums = analyzer.sum_distribution(history)
        ranges = analyzer.range_distribution(history)
        odd = analyzer.odd_count_distribution(history)
        low = analyzer.low_count_distribution(history)
        runs = analyzer.consecutive_stats(history).longest_run_distribution
        rare = len(history) * 0.05
        lines = suggest(history, "anti", lines=300, seed=8)
        for line in lines:
            numbers = line.numbers
            assert sums.quantile(0.05) <= sum(numbers) <= sums.quantile(0.95)
            assert ranges.quantile(0.05) <= numbers[-1] - numbers[0] <= ranges.quantile(0.95)
            assert odd[analyzer.odd_count(numbers)] >= rare
            assert low[analyzer.low_count(numbers)] >= rare
            assert runs[analyzer.longest_run(numbers)] >= rare
        # The plan's example of a pattern to avoid: all numbers above 40.
        assert not any(line.numbers[0] > 40 for line in lines)
        assert not any(analyzer.odd_count(line.numbers) in (0, 5) for line in lines)
        assert not any(analyzer.longest_run(line.numbers) >= 3 for line in lines)

    def test_anti_has_no_preference_between_numbers(self, history: list[Draw]) -> None:
        lines = suggest(history, "anti", lines=2000, seed=9)
        counts = Counter(n for line in lines for n in line.numbers)
        assert set(counts) == set(range(1, 51))
        # 200 expected per number; extreme numbers are a little less likely to fit the patterns.
        assert all(120 <= count <= 280 for count in counts.values())
        assert set(Counter(s for line in lines for s in line.stars)) == set(range(1, 13))


class TestHelpers:
    def test_rank_weights_depend_only_on_rank(self) -> None:
        assert _rank_weights({1: -3.5, 2: 0.0, 3: 120.0}) == {1: 1.0, 2: 8.0, 3: 27.0}

    def test_rank_weights_give_ties_the_same_weight(self) -> None:
        assert _rank_weights({1: 5, 2: 5, 3: 9, 4: 1}) == {4: 1.0, 1: 8.0, 2: 8.0, 3: 64.0}
        assert set(_rank_weights(dict.fromkeys(range(1, 13), 0.0)).values()) == {1.0}

    def test_scaled(self) -> None:
        assert _scaled({1: 10, 2: 20, 3: 15}) == {1: 0.0, 2: 1.0, 3: 0.5}
        assert _scaled({1: 4, 2: 4}) == {1: 0.0, 2: 0.0}

    def test_weighted_sample_returns_distinct_numbers(self) -> None:
        rng = random.Random(1)
        weights = {n: float(n) for n in range(1, 11)}
        for _ in range(200):
            picked = _weighted_sample(rng, weights, 5)
            assert len(set(picked)) == 5
            assert set(picked) <= set(weights)

    def test_weighted_sample_follows_the_weights(self) -> None:
        rng = random.Random(2)
        picks = Counter(_weighted_sample(rng, {1: 9.0, 2: 1.0}, 1)[0] for _ in range(2000))
        assert 1700 < picks[1] < 1900

    def test_weighted_sample_never_picks_a_zero_weight_or_excluded_number(self) -> None:
        rng = random.Random(3)
        weights = {1: 1.0, 2: 0.0, 3: 1.0, 4: 1.0}
        for _ in range(200):
            assert set(_weighted_sample(rng, weights, 2, exclude=[4])) == {1, 3}

    def test_most_common_ignores_values_never_observed(self) -> None:
        assert _most_common({0: 1, 1: 7, 2: 30, 3: 30, 4: 9, 5: 0}, 2) == {2, 3}
        assert _most_common({0: 0, 1: 0, 2: 0, 3: 4, 4: 0, 5: 0}, 2) == {3}

    def test_common_values_drops_the_rare_ones(self) -> None:
        distribution = {0: 4, 1: 15, 2: 31, 3: 30, 4: 15, 5: 5}  # out of 100 draws
        assert _common_values(distribution) == {1, 2, 3, 4, 5}
