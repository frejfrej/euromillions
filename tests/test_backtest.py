"""Tests for the backtest."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable

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
from euromillions.models import Draw, PrizeTier, Suggestion

from .conftest import TYPICAL_PRIZES, draw_dates, random_history

LINE_A = Suggestion((1, 2, 3, 4, 5), (1, 2), "hot")
LINE_B = Suggestion((1, 2, 3, 4, 6), (1, 3), "hot")


class SuggestSpy:
    """Stands in for ``suggester.suggest``: returns fixed lines and records what it was shown."""

    def __init__(self, lines: list[Suggestion]) -> None:
        self.lines = lines
        self.calls: list[list[Draw]] = []

    def __call__(
        self, draws: Iterable[Draw], strategy: str, *, lines: int, window: int, seed: int | None
    ) -> list[Suggestion]:
        self.calls.append(list(draws))
        return self.lines[:lines]


@pytest.fixture
def scenario() -> list[Draw]:
    """Four training draws, then three evaluated draws with known outcomes for A and B."""
    dates = draw_dates(7, dt.date(2020, 1, 3))
    jackpot_won = (PrizeTier(2, 30_000_000.0), *TYPICAL_PRIZES[1:])
    training = [
        Draw(dates[0], (10, 20, 30, 40, 50), (5, 6), jackpot_won),
        *(Draw(day, (11, 21, 31, 41, 49), (7, 8), TYPICAL_PRIZES) for day in dates[1:4]),
    ]
    evaluated = [
        # A: 2 numbers, B: 2 numbers -> both rank 13.
        Draw(dates[4], (1, 2, 10, 20, 30), (5, 6), TYPICAL_PRIZES),
        # A: 3 numbers + 2 stars -> rank 6; B: 3 numbers + 1 star -> rank 9.
        Draw(dates[5], (1, 2, 3, 40, 41), (1, 2), TYPICAL_PRIZES),
        # A: 5 numbers + 2 stars -> jackpot, which nobody won; B: 4 numbers + 1 star -> rank 5.
        Draw(dates[6], (1, 2, 3, 4, 5), (1, 2), TYPICAL_PRIZES),
    ]
    return training + evaluated


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
        assert result.best_win == Win(scenario[-1].date, 1, pytest.approx(jackpot))
        assert result.main_matches == (2 + 2) + (3 + 3) + (5 + 4)
        assert result.star_matches == (0 + 0) + (2 + 1) + (2 + 1)
        assert result.main_matches_per_line == pytest.approx(19 / 6)
        assert result.star_matches_per_line == pytest.approx(1.0)

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
        assert result.estimated_wins == 1
        assert result.best_win is not None
        assert result.best_win.rank == 6  # the jackpot hit is worth nothing here

    def test_a_published_prize_without_winner_count_is_paid_as_is(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The source has a few draws where the European winner counts are all zero.
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_B]))
        no_counts = tuple(PrizeTier(0, tier.prize) for tier in TYPICAL_PRIZES)
        patched = [*scenario[:-1], Draw(scenario[-1].date, (1, 2, 3, 4, 5), (1, 2), no_counts)]
        (result,) = backtest(patched, ["hot"], train_fraction=0.6, lines=1, refresh=False).results
        assert result.best_win == Win(scenario[-1].date, 5, pytest.approx(131.50))
        assert result.estimated_wins == 0

    def test_line_price(self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A, LINE_B]))
        report = backtest(scenario, ["hot"], train_fraction=0.6, lines=2, line_price=2.2)
        assert report.line_price == 2.2
        assert report.results[0].cost == pytest.approx(6 * 2.2)

    def test_a_free_game_has_no_return_on_investment(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A]))
        report = backtest(scenario, ["hot"], train_fraction=0.6, lines=1, line_price=0)
        assert report.results[0].roi == 0.0


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
        real_draw = backtest_module._suggest

        def recording(*args: object) -> list[Suggestion]:
            played.append(real_draw(*args))  # type: ignore[arg-type]
            return played[-1]

        monkeypatch.setattr(backtest_module, "_suggest", recording)
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
        assert backtest(history, ["hot"], seed=2, refresh=False) != backtest(
            history, ["hot"], seed=2, refresh=True
        )

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
        ("option", "value"), [("lines", 0), ("window", 0), ("line_price", -1.0)]
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

    def test_evaluated_draws_need_prize_data(self, history: list[Draw]) -> None:
        with pytest.raises(ValueError, match="prize data"):
            backtest(history, [RANDOM])

    def test_training_draws_do_not_need_prize_data(
        self, history: list[Draw], paid_history: list[Draw]
    ) -> None:
        mixed = [*history[:240], *paid_history[240:300]]
        assert backtest(mixed, [RANDOM], train_fraction=0.8, seed=1).test_draws == 60
