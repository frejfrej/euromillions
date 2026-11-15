"""Tests for the backtest."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable
from typing import Any, cast

import pytest

from euromillions import backtest as backtest_module
from euromillions import suggester
from euromillions.backtest import (
    DEFAULT_STRATEGIES,
    EXPECTED_MATCHES,
    RANDOM,
    RANK_PROBABILITIES,
    WIN_PROBABILITY,
    Win,
    backtest,
)
from euromillions.errors import HistoryError
from euromillions.models import Draw, PrizeTier, Suggestion

from .conftest import LINE_A, LINE_B, TYPICAL_PRIZES, SuggestSpy, draw_dates, random_history

#: The numbers of ``LINE_A`` with other stars: both can match five numbers of the same draw.
LINE_C = Suggestion((1, 2, 3, 4, 5), (3, 4), "hot")


#: The prizes of a draw whose jackpot was shared by two winners: a pool of EUR 60 million.
JACKPOT_WON = (PrizeTier(2, 30_000_000.0), *TYPICAL_PRIZES[1:])


def approx(amount: float) -> float:
    """``pytest.approx`` for an amount inside an expected object, where a float is declared."""
    return cast(float, pytest.approx(amount))


def with_tier(prizes: tuple[PrizeTier, ...], rank: int, tier: PrizeTier) -> tuple[PrizeTier, ...]:
    """Return ``prizes`` with the tier of ``rank`` replaced."""
    return (*prizes[: rank - 1], tier, *prizes[rank:])


class TestChance:
    def test_rank_probabilities_match_the_official_odds(self) -> None:
        odds = {rank: 1 / probability for rank, probability in RANK_PROBABILITIES.items()}
        assert odds[1] == pytest.approx(139_838_160)
        assert odds[2] == pytest.approx(6_991_908)
        assert odds[3] == pytest.approx(3_107_515, abs=1)
        assert odds[4] == pytest.approx(621_503, abs=1)
        assert odds[5] == pytest.approx(31_075, abs=1)
        assert odds[6] == pytest.approx(14_125, abs=1)
        assert odds[7] == pytest.approx(13_811, abs=1)
        assert odds[8] == pytest.approx(985, abs=1)
        assert odds[9] == pytest.approx(706, abs=1)
        assert odds[10] == pytest.approx(314, abs=1)
        assert odds[11] == pytest.approx(188, abs=1)
        assert odds[12] == pytest.approx(49, abs=1)
        assert odds[13] == pytest.approx(22, abs=1)
        assert sorted(RANK_PROBABILITIES) == list(range(1, 14))

    def test_one_line_in_thirteen_wins_something(self) -> None:
        assert 1 / WIN_PROBABILITY == pytest.approx(12.97, abs=0.01)

    def test_expected_matches(self) -> None:
        assert EXPECTED_MATCHES == {"main": pytest.approx(0.5), "star": pytest.approx(1 / 3)}


class TestScoring:
    def test_known_lines_are_scored_with_the_prizes_of_each_draw(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A, LINE_B]))
        report = backtest(scenario, ["hot"], train_fraction=0.6, lines=2, refresh=False)
        (result,) = report.results

        rank_13 = 4.30 * 1_035_060 / (1_035_060 + 2)  # two of our lines share that pool
        rank_6 = 78.70 * 1_263 / (1_263 + 1)
        rank_9 = 13.60 * 28_558 / (28_558 + 1)
        rank_5 = 131.50 * 715 / (715 + 1)
        jackpot = 2 * 30_000_000.0  # estimated: the only pool known for an unclaimed jackpot
        assert result.strategy == "hot"
        assert result.lines == 6
        assert result.cost == pytest.approx(15.0)
        assert result.winnings == pytest.approx(2 * rank_13 + rank_6 + rank_9 + rank_5 + jackpot)
        # To the cent, which the jackpot would otherwise hide.
        small_prizes = 2 * rank_13 + rank_6 + rank_9 + rank_5
        assert result.winnings - jackpot == pytest.approx(small_prizes, abs=1e-6)
        # What was won in the top ranks is also kept apart: chance is not compared with it.
        assert result.top_winnings == pytest.approx(jackpot)
        assert result.top_wins == 1
        assert result.net == pytest.approx(result.winnings - 15.0)
        assert result.roi == pytest.approx(result.net / 15.0)
        assert result.wins_by_rank == {
            **dict.fromkeys(range(1, 14), 0),
            1: 1,
            5: 1,
            6: 1,
            9: 1,
            13: 2,
        }
        assert result.winning_lines == 6
        assert result.win_rate == pytest.approx(1.0)
        assert result.estimated_wins == 1
        assert result.best_win == Win(scenario[-1].date, 1, approx(jackpot))
        assert result.main_matches == (2 + 2) + (3 + 3) + (5 + 4)
        assert result.star_matches == (0 + 0) + (2 + 1) + (2 + 1)
        assert result.main_matches_per_line == pytest.approx(19 / 6)
        assert result.star_matches_per_line == pytest.approx(1.0)

    def test_prizes_are_shared_with_the_real_winners_and_paid_to_every_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A, LINE_B]))
        dates = draw_dates(5, dt.date(2020, 1, 3))
        # Two numbers (rank 13) were matched by a single real line, which received EUR 100.
        paid = with_tier(TYPICAL_PRIZES, 13, PrizeTier(1, 100.0))
        history = [
            *(Draw(day, (11, 21, 31, 41, 49), (7, 8), TYPICAL_PRIZES) for day in dates[:4]),
            Draw(dates[4], (1, 2, 10, 20, 30), (5, 6), paid),  # A and B: two numbers each
        ]
        (result,) = backtest(history, ["hot"], train_fraction=0.8, lines=2).results
        assert result.wins_by_rank[13] == 2
        assert result.winnings == pytest.approx(2 * 100.0 / 3)  # the pool of 100 split in three
        assert (result.top_winnings, result.top_wins) == (0.0, 0)
        assert result.estimated_wins == 0
        assert result.best_win == Win(dates[4], 13, approx(100.0 / 3))

    def test_an_estimated_pool_is_split_between_the_lines_that_win_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A, LINE_C]))
        dates = draw_dates(5, dt.date(2020, 1, 3))
        # Five numbers without a star (rank 3): won by ten lines at EUR 30 in the past, a
        # pool of EUR 300, and by nobody on the day both of our lines hit it.
        known = with_tier(TYPICAL_PRIZES, 3, PrizeTier(10, 30.0))
        unclaimed = with_tier(TYPICAL_PRIZES, 3, PrizeTier(0, 0.0))
        history = [
            *(Draw(day, (11, 21, 31, 41, 49), (7, 8), known) for day in dates[:4]),
            Draw(dates[4], (1, 2, 3, 4, 5), (9, 10), unclaimed),
        ]
        (result,) = backtest(history, ["hot"], train_fraction=0.8, lines=2).results
        assert result.wins_by_rank[3] == 2
        assert result.winnings == pytest.approx(300.0)
        assert result.top_winnings == pytest.approx(300.0)
        assert result.top_wins == 2
        assert result.estimated_wins == 2

    def test_estimates_ignore_the_draws_played_under_other_rules(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A]))
        # A jackpot won in 2015, when the ranks and the stakes were not the same.
        old = Draw(dt.date(2015, 6, 2), (11, 21, 31, 41, 49), (7, 8), JACKPOT_WON)
        dates = draw_dates(4, dt.date(2020, 1, 3))
        history = [
            old,
            *(Draw(day, (11, 21, 31, 41, 49), (7, 8), TYPICAL_PRIZES) for day in dates[:3]),
            Draw(dates[3], (1, 2, 3, 4, 5), (1, 2), TYPICAL_PRIZES),
        ]
        (result,) = backtest(history, ["hot"], train_fraction=0.8, lines=1).results
        assert result.wins_by_rank[1] == 1
        assert result.winnings == 0.0
        # The same win under the current rules would have been used.
        history[0] = Draw(dt.date(2019, 6, 4), (11, 21, 31, 41, 49), (7, 8), JACKPOT_WON)
        (result,) = backtest(history, ["hot"], train_fraction=0.8, lines=1).results
        assert result.winnings == pytest.approx(60_000_000.0)

    def test_z_scores_use_the_exact_variance_of_a_fair_draw(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A, LINE_B]))
        (result,) = backtest(scenario, ["hot"], train_fraction=0.6, lines=2, refresh=False).results
        # Numbers 1-4 are on both lines, 5 and 6 on one: sum of squares 18, total 10.
        main_variance = 3 * 0.1 * 0.9 * (18 - (10 * 10 - 18) / 49)
        assert result.main_z == pytest.approx((19 - 6 * 0.5) / math.sqrt(main_variance))
        # Star 1 is on both lines, 2 and 3 on one: sum of squares 6, total 4.
        star_variance = 3 * (1 / 6) * (5 / 6) * (6 - (4 * 4 - 6) / 11)
        assert result.star_z == pytest.approx((6 - 6 / 3) / math.sqrt(star_variance))

    def test_a_single_line_has_the_hypergeometric_variance(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A]))
        (result,) = backtest(scenario, ["hot"], train_fraction=0.6, lines=1, refresh=False).results
        per_draw = 5 * (5 / 50) * (45 / 50) * (45 / 49)  # hypergeometric(50, 5, 5)
        assert result.main_matches == 2 + 3 + 5
        assert result.main_z == pytest.approx((10 - 3 * 0.5) / math.sqrt(3 * per_draw))

    def test_an_unclaimed_rank_without_any_known_pool_pays_nothing(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A]))
        never_won = [Draw(d.date, d.numbers, d.stars, TYPICAL_PRIZES) for d in scenario]
        (result,) = backtest(never_won, ["hot"], train_fraction=0.6, lines=1, refresh=False).results
        assert result.wins_by_rank[1] == 1
        assert (result.top_winnings, result.top_wins) == (0.0, 1)
        assert result.estimated_wins == 1
        assert result.best_win is not None
        assert result.best_win.rank == 6  # the jackpot hit is worth nothing here

    def test_an_unclaimed_rank_is_valued_from_earlier_draws_only(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A jackpot hit on the first evaluated draw, where nobody won it. Jackpots were won
        # two draws earlier (2 x 30M) and three draws later (1 x 90M): only the former was
        # known at the time.
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A]))
        earlier_win = (PrizeTier(2, 30_000_000.0), *TYPICAL_PRIZES[1:])
        later_win = (PrizeTier(1, 90_000_000.0), *TYPICAL_PRIZES[1:])
        dates = [draw.date for draw in scenario]
        history = [
            Draw(dates[0], (11, 21, 31, 41, 49), (7, 8), TYPICAL_PRIZES),
            Draw(dates[1], (11, 21, 31, 41, 49), (7, 8), earlier_win),
            Draw(dates[2], (11, 21, 31, 41, 49), (7, 8), TYPICAL_PRIZES),
            Draw(dates[3], (1, 2, 3, 4, 5), (1, 2), TYPICAL_PRIZES),
            Draw(dates[4], (11, 21, 31, 41, 49), (7, 8), TYPICAL_PRIZES),
            Draw(dates[5], (11, 21, 31, 41, 49), (7, 8), TYPICAL_PRIZES),
            Draw(dates[6], (11, 21, 31, 41, 49), (7, 8), later_win),
        ]
        (result,) = backtest(history, ["hot"], train_fraction=0.43, lines=1).results
        assert result.wins_by_rank[1] == 1
        assert result.estimated_wins == 1
        assert result.winnings == pytest.approx(60_000_000.0)

    def test_a_published_prize_without_winner_count_is_paid_as_is(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The source has a few draws where the European winner counts are all zero: the
        # amount cannot be shared with winners nobody counted, and is flagged as an estimate.
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_B]))
        no_counts = tuple(PrizeTier(0, tier.prize) for tier in TYPICAL_PRIZES)
        patched = [*scenario[:-1], Draw(scenario[-1].date, (1, 2, 3, 4, 5), (1, 2), no_counts)]
        (result,) = backtest(patched, ["hot"], train_fraction=0.6, lines=1, refresh=False).results
        assert result.best_win == Win(scenario[-1].date, 5, approx(131.50))
        assert result.estimated_wins == 1

    def test_line_price(self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A, LINE_B]))
        report = backtest(scenario, ["hot"], train_fraction=0.6, lines=2, line_price=2.2)
        assert report.line_price == 2.2
        assert report.results[0].cost == pytest.approx(6 * 2.2)

    def test_random_lines_are_the_yardstick_for_winnings(self, paid_history: list[Draw]) -> None:
        report = backtest(paid_history[:100], [RANDOM], lines=4, seed=1)
        # 20 draws x 4 lines, all paying TYPICAL_PRIZES. Ranks 4 to 13, with the number of
        # winning lines out of C(50,5) x C(12,2) = 139,838,160, each prize being shared with
        # one more winner.
        ways = [225, 4_500, 9_900, 10_125, 141_900, 198_000, 445_500, 744_975, 2_838_000, 6_385_500]
        chances = [way / 139_838_160 for way in ways]
        amounts = [tier.prize * tier.winners / (tier.winners + 1) for tier in TYPICAL_PRIZES[3:]]
        mean = sum(chance * amount for chance, amount in zip(chances, amounts, strict=True))
        square = sum(chance * amount**2 for chance, amount in zip(chances, amounts, strict=True))
        assert report.chance_winnings == pytest.approx(80 * mean)
        assert report.chance_winnings_sd == pytest.approx(math.sqrt(80 * (square - mean**2)))
        # Under half a euro for a line that costs EUR 2.50.
        assert report.chance_winnings / 80 == pytest.approx(0.46, abs=0.01)

    def test_the_yardstick_does_not_depend_on_the_strategies_played(
        self, paid_history: list[Draw]
    ) -> None:
        one = backtest(paid_history[:100], ["hot"], seed=1)
        other = backtest(paid_history[:100], ["cold", RANDOM], seed=2, refresh=False)
        assert one.chance_winnings == other.chance_winnings
        assert one.chance_winnings_sd == other.chance_winnings_sd


class TestSplit:
    def test_the_oldest_draws_train_and_the_rest_is_evaluated(
        self, paid_history: list[Draw]
    ) -> None:
        report = backtest(paid_history[:10], [RANDOM], train_fraction=0.8, lines=3, seed=1)
        assert (report.train_draws, report.test_draws) == (8, 2)
        assert (report.train_start, report.train_end) == (
            paid_history[0].date,
            paid_history[7].date,
        )
        assert (report.test_start, report.test_end) == (paid_history[8].date, paid_history[9].date)
        assert report.lines_per_draw == 3
        assert report.refresh is True
        assert report.results[0].lines == 6

    def test_the_split_is_immune_to_floating_point_noise(self, paid_history: list[Draw]) -> None:
        # 0.58 * 100 is 57.99999999999999 in floating point.
        report = backtest(paid_history[:100], [RANDOM], train_fraction=0.58, seed=1)
        assert (report.train_draws, report.test_draws) == (58, 42)

    def test_the_order_of_the_history_does_not_matter(self, paid_history: list[Draw]) -> None:
        forwards = backtest(paid_history[:60], ["hot", RANDOM], seed=4)
        assert backtest(paid_history[59::-1], ["hot", RANDOM], seed=4) == forwards

    def test_refreshed_suggestions_never_see_the_draw_they_are_scored_on(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = SuggestSpy([LINE_A, LINE_B])
        monkeypatch.setattr(suggester, "suggest", spy)
        backtest(scenario, ["hot"], train_fraction=0.6, lines=2, refresh=True)
        # One call per evaluated draw, each seeing exactly the draws that precede it.
        assert spy.calls == [scenario[:4], scenario[:5], scenario[:6]]

    def test_static_suggestions_only_see_the_training_set(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = SuggestSpy([LINE_A, LINE_B])
        monkeypatch.setattr(suggester, "suggest", spy)
        report = backtest(scenario, ["hot"], train_fraction=0.6, lines=2, refresh=False)
        assert spy.calls == [scenario[:4]]
        assert report.refresh is False

    def test_window_and_line_count_are_passed_to_the_strategy(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        received = []

        def fake(
            draws: Iterable[Draw], strategy: str, *, lines: int, window: int, seed: int | None
        ) -> list[Suggestion]:
            received.append((strategy, lines, window, seed))
            return [LINE_A, LINE_B][:lines]

        monkeypatch.setattr(suggester, "suggest", fake)
        backtest(scenario, ["cold"], train_fraction=0.6, lines=2, window=7, seed=1)
        assert [call[:3] for call in received] == [("cold", 2, 7)] * 3
        assert len({call[3] for call in received}) == 3  # a fresh seed for every draw

    @pytest.mark.parametrize("strategy", DEFAULT_STRATEGIES)
    def test_the_lines_played_do_not_depend_on_the_draw_they_are_scored_on(
        self, strategy: str, paid_history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        played: list[list[Suggestion]] = []
        real_suggest = suggester.suggest

        def recording(*args: Any, **options: Any) -> list[Suggestion]:
            played.append(real_suggest(*args, **options))
            return played[-1]

        monkeypatch.setattr(suggester, "suggest", recording)
        history = paid_history[:120]
        last = history[-1]
        altered = [*history[:-1], Draw(last.date, (1, 2, 3, 4, 5), (1, 2), TYPICAL_PRIZES)]

        backtest(history, [strategy], seed=3)
        original, played[:] = list(played), []
        backtest(altered, [strategy], seed=3)
        assert len(original) == 24
        assert played == original


class TestRuns:
    def test_default_run_compares_every_strategy_with_the_random_baseline(
        self, paid_history: list[Draw]
    ) -> None:
        report = backtest(paid_history, train_fraction=0.75, seed=11)
        assert DEFAULT_STRATEGIES == ("hot", "cold", "balanced", "weighted", "anti", "random")
        assert [result.strategy for result in report.results] == list(DEFAULT_STRATEGIES)
        assert (report.train_draws, report.test_draws) == (300, 100)
        for result in report.results:
            assert result.lines == 500
            assert result.cost == pytest.approx(1250.0)
            assert result.winnings >= 0
            assert result.net == pytest.approx(result.winnings - result.cost)
            assert sorted(result.wins_by_rank) == list(range(1, 14))
            assert result.winning_lines == sum(result.wins_by_rank.values())
            assert 0 <= result.win_rate <= 1
            assert 0 <= result.main_matches <= 5 * result.lines
            assert 0 <= result.star_matches <= 2 * result.lines
            # The history is random: no strategy can do better or worse than chance.
            assert abs(result.main_z) < 4
            assert abs(result.star_z) < 4
            assert result.estimated_wins == 0
            assert (result.top_winnings, result.top_wins) == (0.0, 0)

    def test_a_seed_makes_the_backtest_reproducible(self, paid_history: list[Draw]) -> None:
        history = paid_history[:80]
        first = backtest(history, seed=5)
        assert backtest(history, seed=5) == first
        assert backtest(history, seed=6) != first

    def test_each_strategy_has_its_own_random_stream(self, paid_history: list[Draw]) -> None:
        history = paid_history[:80]
        together = {result.strategy: result for result in backtest(history, seed=5).results}
        (alone,) = backtest(history, ["cold"], seed=5).results
        assert alone == together["cold"]

    def test_works_without_a_seed(self, paid_history: list[Draw]) -> None:
        assert len(backtest(paid_history[:60], ["hot", RANDOM]).results) == 2

    def test_static_and_refreshed_runs_differ(self, paid_history: list[Draw]) -> None:
        history = paid_history[:120]
        static = backtest(history, ["hot"], seed=2, refresh=False)
        refreshed = backtest(history, ["hot"], seed=2, refresh=True)
        assert static.results != refreshed.results

    def test_the_baseline_plays_every_number_and_star(self, paid_history: list[Draw]) -> None:
        # A baseline that never played star 12, say, would bias every comparison.
        lines = suggester.suggest(paid_history, RANDOM, lines=50, seed=1)
        assert {line.strategy for line in lines} == {RANDOM}
        played = [
            line
            for seed in range(10)
            for line in suggester.suggest(paid_history, RANDOM, lines=50, seed=seed)
        ]
        assert {n for line in played for n in line.numbers} == set(range(1, 51))
        assert {s for line in played for s in line.stars} == set(range(1, 13))

    def test_static_run_replays_the_same_lines(self, paid_history: list[Draw]) -> None:
        # Replaying one fixed set of lines gives the same chance variance on every draw.
        (result,) = backtest(paid_history[:120], [RANDOM], seed=2, lines=1, refresh=False).results
        per_draw = 5 * (5 / 50) * (45 / 50) * (45 / 49)
        expected_z = (result.main_matches - 24 * 0.5) / math.sqrt(24 * per_draw)
        assert result.main_z == pytest.approx(expected_z)

    def test_random_baseline_behaves_like_chance(self, paid_history: list[Draw]) -> None:
        (result,) = backtest(paid_history, [RANDOM], train_fraction=0.5, lines=10, seed=8).results
        assert result.lines == 2000
        assert result.main_matches_per_line == pytest.approx(0.5, abs=0.06)
        assert result.star_matches_per_line == pytest.approx(1 / 3, abs=0.05)
        assert result.win_rate == pytest.approx(WIN_PROBABILITY, abs=0.025)
        assert abs(result.main_z) < 3
        assert abs(result.star_z) < 3


class TestValidation:
    def test_a_strategy_named_twice_is_evaluated_once(self, paid_history: list[Draw]) -> None:
        report = backtest(paid_history, ["hot", "random", "hot"], seed=3)
        assert [result.strategy for result in report.results] == ["hot", "random"]
        assert report == backtest(paid_history, ["hot", "random"], seed=3)
        # Also without a seed, where a second run would otherwise differ from the first.
        assert len(backtest(paid_history, ["anti", "anti"]).results) == 1

    def test_unknown_strategy(self, paid_history: list[Draw]) -> None:
        with pytest.raises(ValueError, match=r"lucky.*hot.*random"):
            backtest(paid_history, ["hot", "lucky"])

    def test_no_strategy(self, paid_history: list[Draw]) -> None:
        with pytest.raises(ValueError, match="no strategy"):
            backtest(paid_history, [])

    @pytest.mark.parametrize("fraction", [0, 1, -0.2, 1.5])
    def test_train_fraction_must_be_strictly_between_zero_and_one(
        self, fraction: float, paid_history: list[Draw]
    ) -> None:
        with pytest.raises(ValueError, match="train_fraction"):
            backtest(paid_history, train_fraction=fraction)

    @pytest.mark.parametrize(
        ("option", "value"),
        [
            ("lines", 0),
            ("window", 0),
            ("line_price", -1.0),
            ("line_price", 0),  # a return on investment needs a cost
            ("line_price", float("nan")),
        ],
    )
    def test_numeric_options(self, option: str, value: float, paid_history: list[Draw]) -> None:
        with pytest.raises(ValueError, match=option):
            backtest(paid_history, **{option: value})  # type: ignore[arg-type]

    @pytest.mark.parametrize("count", [0, 1])
    def test_needs_a_draw_on_each_side_of_the_split(self, count: int) -> None:
        with pytest.raises(ValueError, match="at least one draw"):
            backtest(random_history(count, prizes=TYPICAL_PRIZES), [RANDOM])

    def test_tiny_fraction_leaves_no_training_draw(self, paid_history: list[Draw]) -> None:
        with pytest.raises(ValueError, match="at least one draw"):
            backtest(paid_history[:5], [RANDOM], train_fraction=0.1)

    def test_only_draws_under_the_current_rules_can_be_evaluated(self) -> None:
        # 99 draws from March 2016: the current rules start with the 61st of them.
        history = random_history(99, start=dt.date(2016, 3, 1), prizes=TYPICAL_PRIZES)
        assert history[60].date == dt.date(2016, 9, 27)
        with pytest.raises(ValueError, match=r"2016-09-23.*2016-09-27.*at least 0\.607"):
            backtest(history, [RANDOM], train_fraction=0.6)
        # The suggested fraction is rounded up, so that it is always enough.
        assert backtest(history, [RANDOM], train_fraction=0.607, seed=1).test_draws == 39

    def test_the_suggested_fraction_stays_below_one(self) -> None:
        # 1,999 draws before the current rules and a single one under them.
        first = dt.date(2010, 1, 1)
        history = [
            Draw(first + dt.timedelta(days=day), (1, 2, 3, 4, 5), (1, 2), TYPICAL_PRIZES)
            for day in range(1999)
        ]
        history.append(Draw(dt.date(2016, 9, 27), (1, 2, 3, 4, 5), (1, 2), TYPICAL_PRIZES))
        with pytest.raises(ValueError, match=r"at least 0\.9995$"):
            backtest(history, [RANDOM], train_fraction=0.9)
        assert backtest(history, [RANDOM], train_fraction=0.9995, seed=1).test_draws == 1

    @pytest.mark.parametrize(
        ("fraction", "expected"),
        [
            (0.6, "0.600"),
            (60 / 99, "0.607"),
            (940 / 1998, "0.471"),
            (0.9991, "0.9991"),
            (1999 / 2000, "0.9995"),
            (0.99999, "0.99999"),
        ],
    )
    def test_fractions_are_rounded_up_to_the_fewest_decimals_that_stay_below_one(
        self, fraction: float, expected: str
    ) -> None:
        assert backtest_module._round_up(fraction) == expected

    def test_a_history_entirely_before_the_current_rules_cannot_be_evaluated(self) -> None:
        history = random_history(50, start=dt.date(2012, 1, 3), prizes=TYPICAL_PRIZES)
        with pytest.raises(ValueError, match="nothing to evaluate"):
            backtest(history, [RANDOM])

    def test_evaluated_draws_need_prize_data(self, history: list[Draw]) -> None:
        with pytest.raises(ValueError, match="prize data"):
            backtest(history, [RANDOM])

    def test_what_the_history_does_not_allow_is_told_apart_from_a_wrong_argument(
        self, history: list[Draw], paid_history: list[Draw]
    ) -> None:
        # The first is for the user to read, the second a mistake of the caller.
        with pytest.raises(HistoryError):
            backtest(history, [RANDOM])
        with pytest.raises(HistoryError):
            backtest(paid_history[:1], [RANDOM])
        with pytest.raises(ValueError) as error:
            backtest(paid_history, ["lucky"])
        assert not isinstance(error.value, HistoryError)

    def test_draws_without_usable_prizes_are_skipped_but_still_seen(
        self, paid_history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        history = paid_history[:10]
        unknown = Draw(history[8].date, history[8].numbers, history[8].stars)
        # A rank won at every draw cannot pay nothing: that is a gap in the data.
        gap = (*TYPICAL_PRIZES[:12], PrizeTier(0, 0.0))
        gapped = Draw(history[7].date, history[7].numbers, history[7].stars, gap)
        history = [*history[:7], gapped, unknown, history[9]]
        spy = SuggestSpy([LINE_A])
        monkeypatch.setattr(suggester, "suggest", spy)
        report = backtest(history, ["hot"], train_fraction=0.6, lines=1)
        assert (report.train_draws, report.test_draws, report.skipped_draws) == (6, 2, 2)
        assert (report.test_start, report.test_end) == (history[6].date, history[9].date)
        assert report.results[0].lines == 2
        # The skipped draws are part of what the strategy knows when the last one is played.
        assert spy.calls == [history[:6], history[:9]]

    def test_the_period_starts_with_the_first_draw_actually_played(
        self, paid_history: list[Draw]
    ) -> None:
        history = paid_history[:10]
        history[8] = Draw(history[8].date, history[8].numbers, history[8].stars)  # no prizes
        report = backtest(history, [RANDOM], train_fraction=0.8, seed=1)
        assert (report.train_draws, report.test_draws, report.skipped_draws) == (8, 1, 1)
        assert report.test_start == report.test_end == history[9].date

    def test_an_unclaimed_top_rank_is_not_a_gap(self, paid_history: list[Draw]) -> None:
        # TYPICAL_PRIZES has no jackpot winner: every draw is evaluated all the same.
        assert backtest(paid_history[:50], [RANDOM], seed=1).skipped_draws == 0

    def test_training_draws_do_not_need_prize_data(
        self, history: list[Draw], paid_history: list[Draw]
    ) -> None:
        mixed = [*history[:240], *paid_history[240:300]]
        assert backtest(mixed, [RANDOM], train_fraction=0.8, seed=1).test_draws == 60


class TestProgress:
    def test_progress_reports_every_draw_played(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A, LINE_B]))
        calls: list[tuple[int, int]] = []
        backtest(
            scenario,
            ["hot", "cold"],
            train_fraction=0.6,
            lines=1,
            progress=lambda done, total: calls.append((done, total)),
        )
        assert calls == [(1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6)]

    def test_progress_counts_a_repeated_strategy_once(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A]))
        calls: list[tuple[int, int]] = []
        backtest(
            scenario,
            ["hot", "hot"],
            train_fraction=0.6,
            lines=1,
            progress=lambda done, total: calls.append((done, total)),
        )
        assert calls[-1] == (3, 3)

    def test_an_exception_from_progress_stops_the_backtest(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A]))
        calls: list[int] = []

        def stop_on_second(done: int, _total: int) -> None:
            calls.append(done)
            if done == 2:
                raise RuntimeError("stop")

        with pytest.raises(RuntimeError, match="stop"):
            backtest(scenario, ["hot"], train_fraction=0.6, lines=1, progress=stop_on_second)
        assert calls == [1, 2]


class TestReadingFigures:
    def test_reading_figures(self) -> None:
        assert backtest_module.Z_BEYOND_TWO == pytest.approx(0.0455, abs=1e-4)
        assert backtest_module.chance_of_extreme_z(12) == pytest.approx(0.43, abs=0.01)
        assert backtest_module.edge_threshold(12) == pytest.approx(2.87, abs=0.01)
        assert backtest_module.TOP_RANK_ODDS == pytest.approx(2.1e6, rel=0.05)
        assert backtest_module.expected_wins(2000)[13] == pytest.approx(
            2000 * RANK_PROBABILITIES[13]
        )
        assert sorted(backtest_module.expected_wins(1)) == list(range(1, 14))
        assert backtest_module.win_rate_sd(2000) == pytest.approx(
            math.sqrt(WIN_PROBABILITY * (1 - WIN_PROBABILITY) / 2000)
        )
        assert backtest_module.FEW_LINES == 1000


class TestAverageReturn:
    def test_prizes_paid_over_lines_sold(self) -> None:
        [draw] = random_history(1, prizes=TYPICAL_PRIZES)
        sold = TYPICAL_PRIZES[-1].winners / backtest_module.RANK_PROBABILITIES[13]
        paid = sum(tier.winners * tier.prize for tier in TYPICAL_PRIZES)
        average = backtest_module.average_return([draw])
        assert average is not None
        assert average.draws == 1
        assert average.amount == pytest.approx(paid / sold)

    def test_a_won_jackpot_counts_where_it_was_won(self) -> None:
        quiet, won = random_history(2, prizes=TYPICAL_PRIZES)
        jackpot = (PrizeTier(1, 100_000_000.0), *TYPICAL_PRIZES[1:])
        won = Draw(won.date, won.numbers, won.stars, jackpot)
        sold = TYPICAL_PRIZES[-1].winners / backtest_module.RANK_PROBABILITIES[13]
        paid = sum(tier.winners * tier.prize for tier in TYPICAL_PRIZES)
        average = backtest_module.average_return([quiet, won])
        assert average is not None
        assert average.amount == pytest.approx((2 * paid + 100_000_000) / (2 * sold))

    def test_only_draws_with_every_prize_under_the_current_rules(self) -> None:
        older = random_history(3, start=dt.date(2015, 1, 6), prizes=TYPICAL_PRIZES)
        unknown = random_history(3, start=dt.date(2017, 1, 3))
        no_winners = [
            Draw(d.date, d.numbers, d.stars, tuple(PrizeTier(0, t.prize) for t in TYPICAL_PRIZES))
            for d in random_history(3, start=dt.date(2018, 1, 2))
        ]
        assert backtest_module.average_return([*older, *unknown, *no_winners]) is None
