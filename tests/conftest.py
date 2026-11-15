"""Shared test helpers."""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable, Iterable

import pytest

from euromillions.models import (
    MAIN_COUNT,
    MAIN_MAX,
    STAR_COUNT,
    Draw,
    PrizeTier,
    Suggestion,
    star_pool_size,
)
from euromillions.schedule import is_draw_day

HistoryFactory = Callable[..., list[Draw]]

#: Two lines for the tests that need to know exactly what is played.
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


#: The thirteen prize tiers of a real draw (2026-11-13), jackpot first: it was not won.
TYPICAL_PRIZES: tuple[PrizeTier, ...] = (
    PrizeTier(0, 0.0),
    PrizeTier(4, 175_411.70),
    PrizeTier(8, 20_498.30),
    PrizeTier(30, 1_702.50),
    PrizeTier(715, 131.50),
    PrizeTier(1_263, 78.70),
    PrizeTier(1_690, 41.30),
    PrizeTier(18_949, 18.40),
    PrizeTier(28_558, 13.60),
    PrizeTier(71_701, 10.10),
    PrizeTier(102_329, 8.50),
    PrizeTier(422_568, 6.50),
    PrizeTier(1_035_060, 4.30),
)


def draw_dates(count: int, start: dt.date) -> list[dt.date]:
    """Return ``count`` consecutive draw days (Tuesdays and Fridays) from ``start`` on."""
    dates: list[dt.date] = []
    day = start
    while len(dates) < count:
        if is_draw_day(day):
            dates.append(day)
        day += dt.timedelta(days=1)
    return dates


def random_history(
    count: int = 300,
    *,
    seed: int = 1,
    start: dt.date = dt.date(2017, 1, 3),
    prizes: tuple[PrizeTier, ...] = (),
) -> list[Draw]:
    """Return ``count`` uniformly random, valid draws in chronological order."""
    rng = random.Random(seed)
    return [
        Draw(
            day,
            tuple(rng.sample(range(1, MAIN_MAX + 1), MAIN_COUNT)),
            tuple(rng.sample(range(1, star_pool_size(day) + 1), STAR_COUNT)),
            prizes=prizes,
        )
        for day in draw_dates(count, start)
    ]


@pytest.fixture(autouse=True)
def no_data_dir_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the tests away from the cache of whoever runs them."""
    monkeypatch.delenv("EUROMILLIONS_DATA_DIR", raising=False)


@pytest.fixture
def make_history() -> HistoryFactory:
    return random_history


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


@pytest.fixture
def history() -> list[Draw]:
    """300 random draws under the current rules (January 2017 onwards), without prize data."""
    return random_history()


@pytest.fixture
def paid_history() -> list[Draw]:
    """400 random draws under the current rules, each paying ``TYPICAL_PRIZES``."""
    return random_history(400, prizes=TYPICAL_PRIZES)
