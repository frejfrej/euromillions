"""Shared test helpers."""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable

import pytest

from euromillions.models import (
    MAIN_COUNT,
    MAIN_MAX,
    STAR_COUNT,
    Draw,
    PrizeTier,
    star_pool_size,
)
from euromillions.schedule import is_draw_day

HistoryFactory = Callable[..., list[Draw]]

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


@pytest.fixture
def make_history() -> HistoryFactory:
    return random_history


@pytest.fixture
def history() -> list[Draw]:
    """300 random draws under the current rules (January 2017 onwards), without prize data."""
    return random_history()


@pytest.fixture
def paid_history() -> list[Draw]:
    """400 random draws under the current rules, each paying ``TYPICAL_PRIZES``."""
    return random_history(400, prizes=TYPICAL_PRIZES)
