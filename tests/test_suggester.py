"""Tests for the suggestion strategies."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Iterable

import pytest

from euromillions import analyzer
from euromillions import suggester as suggester_module
from euromillions.errors import HistoryError
from euromillions.models import Draw, Suggestion
from euromillions.suggester import BASELINE, STRATEGIES, _rank_weights, _weighted_sample, suggest

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

    def test_cold_looks_at_the_whole_history(self, skewed_history: list[Draw]) -> None:
        # How long 46-50 have been away only shows beyond the last 50 draws: with that window
        # alone, they would tie with every other number that is absent from it.
        whole = suggest(skewed_history, "cold", lines=20, seed=1)
        assert whole != suggest(skewed_history[-50:], "cold", lines=20, seed=1)
        assert whole == suggest(skewed_history, "cold", lines=20, window=7, seed=1)

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

    def test_balanced_drops_the_usual_splits_when_they_are_out_of_reach(
        self, history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester_module, "_MAX_ATTEMPTS", 50)
        dates = [draw.date for draw in history[:5]]
        # Most of these draws have no odd number or a single one, but the last, hence
        # the only hot numbers of a one-draw window, are all odd.
        lopsided = [
            Draw(dates[0], (2, 4, 6, 8, 10), (1, 2)),
            Draw(dates[1], (12, 14, 16, 18, 20), (1, 2)),
            Draw(dates[2], (22, 24, 26, 28, 30), (1, 2)),
            Draw(dates[3], (1, 32, 34, 36, 38), (1, 2)),
            Draw(dates[4], (41, 43, 45, 47, 49), (1, 2)),
        ]
        lines = suggest(lopsided, "balanced", lines=3, window=1, seed=1)
        assert len(lines) == 3
        # Two or three numbers of each line are hot: none can have a usual odd/even split.
        assert all(sum(n % 2 for n in line.numbers) >= 2 for line in lines)

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
        # 200 expected per number; the extreme ones are less likely to fit the patterns.
        assert all(100 <= count <= 300 for count in counts.values())
        assert set(Counter(s for line in lines for s in line.stars)) == set(range(1, 13))


def with_an_old_streak(history: list[Draw]) -> list[Draw]:
    """Make 1-5 and stars 1-2 come up 30 times in a row, ending 70 draws before the last."""
    return [
        Draw(draw.date, HOT_NUMBERS, HOT_STARS) if 200 <= index < 230 else draw
        for index, draw in enumerate(history)
    ]


class TestWindow:
    def test_hot_forgets_a_streak_older_than_the_window(self, history: list[Draw]) -> None:
        streaky = with_an_old_streak(history)
        recent = suggest(streaky, "hot", lines=300, window=50, seed=1)
        whole = suggest(streaky, "hot", lines=300, window=300, seed=1)
        assert average_hits(whole, HOT_NUMBERS) > 1.0
        assert average_hits(recent, HOT_NUMBERS) < 0.9
        assert average_star_hits(whole, HOT_STARS) > 0.6
        assert average_star_hits(recent, HOT_STARS) < 0.6

    @pytest.mark.parametrize("strategy", ["cold", "weighted", "anti", BASELINE])
    def test_the_window_only_matters_to_hot_numbers(
        self, strategy: str, history: list[Draw]
    ) -> None:
        streaky = with_an_old_streak(history)
        assert suggest(streaky, strategy, lines=5, window=10, seed=1) == suggest(
            streaky, strategy, lines=5, window=300, seed=1
        )

    def test_balanced_takes_its_hot_numbers_from_the_window(self, history: list[Draw]) -> None:
        streaky = with_an_old_streak(history)
        recent = suggest(streaky, "balanced", lines=300, window=50, seed=1)
        whole = suggest(streaky, "balanced", lines=300, window=300, seed=1)
        assert average_hits(whole, HOT_NUMBERS) > average_hits(recent, HOT_NUMBERS) + 0.2


class TestWeighted:
    """Each term of the composite score must count on its own."""

    def test_frequency(self, history: list[Draw]) -> None:
        # 7 is in every draw, the latest included: top frequency, but the shortest gap of all.
        rng = random.Random(3)
        reshaped = []
        for draw in history:
            others = rng.sample([n for n in range(1, 51) if n != 7], 4)
            reshaped.append(Draw(draw.date, (7, *others), draw.stars))
        lines = suggest(reshaped, "weighted", lines=400, seed=2)
        assert sum(7 in line.numbers for line in lines) / len(lines) > 0.35  # 0.10 by chance

    def test_gap(self, history: list[Draw]) -> None:
        # 7 comes up in 30 of the 300 draws either way, as expected of any number: only the
        # time since its last appearance differs between the two histories.
        def share_with_seven(last_seen: int) -> float:
            rng = random.Random(3)
            positions = set(range(last_seen - 29 * 6, last_seen + 1, 6))
            reshaped = []
            for index, draw in enumerate(history):
                others = rng.sample([n for n in range(1, 51) if n != 7], 5)
                numbers = (7, *others[:4]) if index in positions else tuple(others)
                reshaped.append(Draw(draw.date, numbers, draw.stars))
            lines = suggest(reshaped, "weighted", lines=1000, seed=2)
            return sum(7 in line.numbers for line in lines) / len(lines)

        assert share_with_seven(last_seen=199) > 1.5 * share_with_seven(last_seen=299)

    def test_stars_are_weighted_too(self, history: list[Draw]) -> None:
        # Star 1 is in every one of the first 200 draws, then absent for 100.
        reshaped = []
        for index, draw in enumerate(history):
            other = next(star for star in draw.stars if star != 1)
            absent = tuple(star if star != 1 else 2 + (other == 2) for star in draw.stars)
            reshaped.append(Draw(draw.date, draw.numbers, (1, other) if index < 200 else absent))
        lines = suggest(reshaped, "weighted", lines=400, seed=2)
        assert sum(1 in line.stars for line in lines) / len(lines) > 0.3  # 1/6 by chance


class TestDistinctLines:
    @pytest.mark.parametrize("strategy", ["hot", "cold", "balanced", "weighted"])
    @pytest.mark.parametrize("size", [1, 2, 5])
    def test_a_tiny_history_still_gives_distinct_lines(
        self, strategy: str, size: int, history: list[Draw]
    ) -> None:
        # With so little history the weights concentrate on a few numbers: without care,
        # the same line would come back again and again.
        lines = suggest(history[:size], strategy, lines=30, seed=11)
        assert len(set(lines)) == 30

    def test_giving_up_on_distinct_lines_is_an_error(
        self, history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester_module, "_MAX_ATTEMPTS", 0)
        with pytest.raises(HistoryError, match="could not find 3 distinct lines"):
            suggest(history, "hot", lines=3, seed=1)

    def test_anti_gives_up_when_no_line_fits_the_patterns(
        self, history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Twenty times the same draw: only its own sum, range and splits are "usual".
        same = [Draw(draw.date, (1, 2, 3, 4, 5), (1, 2)) for draw in history[:20]]
        monkeypatch.setattr(suggester_module, "_MAX_ATTEMPTS", 20)
        with pytest.raises(HistoryError, match="no line matches the usual patterns"):
            suggest(same, "anti", seed=1)


class TestBaseline:
    def test_is_not_offered_as_a_strategy(self) -> None:
        assert BASELINE == "random"
        assert BASELINE not in STRATEGIES

    def test_draws_valid_distinct_reproducible_lines(self, history: list[Draw]) -> None:
        lines = suggest(history, BASELINE, lines=40, seed=7)
        assert len(set(lines)) == 40
        assert {line.strategy for line in lines} == {BASELINE}
        assert suggest(history, BASELINE, lines=40, seed=7) == lines

    def test_does_not_look_at_the_history(self, history: list[Draw]) -> None:
        assert suggest(history[:3], BASELINE, lines=5, seed=7) == suggest(
            history, BASELINE, lines=5, seed=7
        )


class TestHelpers:
    def test_rank_weights_depend_only_on_rank(self) -> None:
        assert _rank_weights({1: -3.5, 2: 0.0, 3: 120.0}) == {1: 1.0, 2: 8.0, 3: 27.0}

    def test_rank_weights_give_ties_the_same_weight(self) -> None:
        assert _rank_weights({1: 5, 2: 5, 3: 9, 4: 1}) == {4: 1.0, 1: 8.0, 2: 8.0, 3: 64.0}
        assert set(_rank_weights(dict.fromkeys(range(1, 13), 0.0)).values()) == {1.0}

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
