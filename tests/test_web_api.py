"""Tests for the JSON payloads of the web UI."""

from __future__ import annotations

import datetime as dt
import json
import random

import pytest

from euromillions import schedule, suggester
from euromillions.analyzer import number_stats, recent
from euromillions.backtest import RANK_PROBABILITIES, backtest
from euromillions.fetcher import UpdateResult
from euromillions.models import DISCLAIMER, Draw, format_line
from euromillions.web import api

from .conftest import LINE_A, LINE_B, TYPICAL_PRIZES, SuggestSpy, random_history
from .test_analyzer import HISTORY as SIX_DRAWS

PARIS = schedule.DRAW_TIMEZONE


def after_draw(draw: Draw, days: int = 0) -> dt.datetime:
    """A moment one hour after the draw of ``draw``, ``days`` later."""
    return schedule.draw_datetime(draw.date) + dt.timedelta(days=days, hours=1)


class TestStatus:
    def test_status_of_an_empty_history(self) -> None:
        status = api.status([])
        assert status["cache"] is False
        assert status["draws"] == 0
        assert status["latest"] is None
        assert dt.datetime.fromisoformat(status["nextDraw"]).tzinfo is not None
        assert status["strategies"][0] == {
            "name": "hot",
            "description": suggester.STRATEGIES["hot"],
            "usesWindow": True,
        }
        assert [s["name"] for s in status["strategies"]] == list(suggester.STRATEGIES)
        assert status["disclaimer"] == DISCLAIMER
        assert status["minTrainFraction"] is None

    def test_status_of_a_history(self) -> None:
        history = random_history(300, prizes=TYPICAL_PRIZES)
        status = api.status(history, now=after_draw(history[-1]))
        assert status["cache"] is True
        assert status["draws"] == 300
        assert (status["first"], status["last"]) == (
            history[0].date.isoformat(),
            history[-1].date.isoformat(),
        )
        assert status["latest"]["numbers"] == list(history[-1].numbers)
        assert status["latest"]["jackpotWon"] is False
        assert status["behind"] is False
        assert api.status(history, now=after_draw(history[-1], days=14))["behind"] is True
        # The history starts under the current rules: the smallest split is one draw.
        assert status["minTrainFraction"] == pytest.approx(0.004)

    def test_the_smallest_train_fraction_starts_under_the_current_rules(self) -> None:
        history = random_history(100, start=dt.date(2016, 1, 1), prizes=TYPICAL_PRIZES)
        older = sum(draw.date < dt.date(2016, 9, 27) for draw in history)
        fraction = api.status(history)["minTrainFraction"]
        assert older / 100 <= fraction < (older + 1) / 100
        backtest(history, ["random"], train_fraction=fraction, seed=1)  # accepted


class TestAnalysis:
    def test_every_payload_is_json(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        history = random_history(200, prizes=TYPICAL_PRIZES)
        json.dumps(api.status(history))
        json.dumps(api.analysis(history))
        json.dumps(api.suggestion(history, strategy="hot", lines=2, window=50, seed=1))
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A, LINE_B]))
        json.dumps(api.backtest_result(backtest(scenario, ["hot"], train_fraction=0.6, lines=2)))

    def test_analysis_matches_the_cli_figures(self) -> None:
        analysis = api.analysis(SIX_DRAWS, window=4)
        assert analysis["totalDraws"] == 6
        assert analysis["window"] == 4
        assert [s["number"] for s in analysis["mostDrawn"][:3]] == [7, 21, 35]
        assert [s["number"] for s in analysis["hot"][:3]] == [7, 21, 35]
        assert analysis["pairs"]["entries"][0] == {"combo": [7, 21], "count": 3}
        assert analysis["chance"]["extremeZ"]["main"] == pytest.approx(2.5, abs=0.05)
        assert analysis["chance"]["longestGap"]["main"] == 40
        assert len(analysis["numbers"]) == 50
        assert len(analysis["stars"]) == 12

    def test_the_recent_window_has_its_own_stats(self) -> None:
        analysis = api.analysis(SIX_DRAWS, window=4)
        expected = number_stats(recent(SIX_DRAWS, 4), "main")
        assert [s["count"] for s in analysis["recentNumbers"]] == [
            expected[n].count for n in range(1, 51)
        ]

    def test_sum_bands_cover_every_draw(self) -> None:
        history = random_history(200)
        bands = api.analysis(history)["sumBands"]
        assert sum(band["draws"] for band in bands) == 200
        assert all(band["from"] % 10 == 0 for band in bands)
        assert [band["from"] for band in bands] == list(
            range(bands[0]["from"], bands[-1]["from"] + 10, 10)
        )

    def test_fair_figures(self) -> None:
        analysis = api.analysis(SIX_DRAWS)
        assert sum(analysis["fairSplit"].values()) == pytest.approx(1)
        assert analysis["consecutive"]["fairShare"] == pytest.approx(0.353, abs=0.001)


class TestSuggestion:
    def test_suggestion_returns_the_seed_it_used(self) -> None:
        history = random_history(200)
        first = api.suggestion(
            history, strategy="cold", lines=3, window=50, seed=None, rng=random.Random(3)
        )
        again = api.suggestion(history, strategy="cold", lines=3, window=50, seed=first["seed"])
        assert again["lines"] == first["lines"]
        line = first["lines"][0]
        assert line["sum"] == sum(line["numbers"])
        assert line["odd"] == sum(n % 2 for n in line["numbers"])
        assert line["low"] == sum(n <= 25 for n in line["numbers"])
        assert line["text"] == format_line(line["numbers"], line["stars"])
        assert first["basedOn"] == {"draws": 200, "last": history[-1].date.isoformat()}

    @pytest.mark.parametrize("lines", [0, 11])
    def test_suggestion_rejects_bad_lines(self, lines: int) -> None:
        with pytest.raises(ValueError, match="between 1 and 10"):
            api.suggestion(random_history(50), strategy="hot", lines=lines, window=50, seed=1)


class TestBacktestResult:
    def test_backtest_result_flags(
        self, scenario: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suggester, "suggest", SuggestSpy([LINE_A, LINE_B]))
        report = backtest(scenario, ["hot"], train_fraction=0.6, lines=2)
        result = api.backtest_result(report)
        (hot,) = result["results"]
        assert hot["topWins"] == 1
        assert hot["commonWinnings"] == pytest.approx(hot["winnings"] - hot["topWinnings"])
        assert result["reading"]["fewLines"] is True
        assert result["reading"]["zScores"] == 2
        assert result["bestWin"]["rank"] == 1
        assert (result["bestWin"]["numbers"], result["bestWin"]["stars"]) == (5, 2)
        assert result["rankMatches"][13] == [2, 0]
        assert result["chance"]["wins"][13] == pytest.approx(6 * RANK_PROBABILITIES[13])

    def test_within_chance_and_edge(self) -> None:
        history = random_history(300, prizes=TYPICAL_PRIZES)
        result = api.backtest_result(backtest(history, ["random"], train_fraction=0.5, seed=1))
        (random_lines,) = result["results"]
        assert isinstance(random_lines["withinChance"], bool)
        assert random_lines["edge"] is False


class TestFetchResult:
    def test_fetch_result_reports_an_unpublished_draw(self) -> None:
        history = random_history(50, prizes=TYPICAL_PRIZES)
        now = after_draw(history[-1], days=7)
        latest = schedule.previous_draw(now).date()
        result = api.fetch_result(UpdateResult(history, 2, 0, True), now=now)
        assert result["unpublished"] == latest.isoformat()
        assert (result["downloaded"], result["added"], result["updated"]) == (True, 2, 0)
        assert result["status"]["draws"] == 50

    def test_nothing_is_unpublished_when_the_history_is_current(self) -> None:
        history = random_history(50, prizes=TYPICAL_PRIZES)
        result = api.fetch_result(UpdateResult(history, 0, 0, False), now=after_draw(history[-1]))
        assert result["unpublished"] is None


def test_analysis_says_when_the_late_stars_joined_the_pool() -> None:
    assert api.analysis(SIX_DRAWS)["starPoolSince"] == {
        10: "2011-05-10",
        11: "2011-05-10",
        12: "2016-09-27",
    }


def test_analysis_gives_the_fair_mean_of_the_sum() -> None:
    assert api.analysis(SIX_DRAWS)["sums"]["fairMean"] == 127.5


def test_status_gives_the_line_price() -> None:
    assert api.status([])["linePrice"] == 2.5
