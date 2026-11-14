"""Shared test helpers."""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable

import pytest

from euromillions.models import MAIN_COUNT, MAIN_MAX, STAR_COUNT, Draw, star_pool_size
from euromillions.schedule import is_draw_day

HistoryFactory = Callable[..., list[Draw]]


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
    count: int = 300, *, seed: int = 1, start: dt.date = dt.date(2017, 1, 3)
) -> list[Draw]:
    """Return ``count`` uniformly random, valid draws in chronological order."""
    rng = random.Random(seed)
    return [
        Draw(
            day,
            tuple(rng.sample(range(1, MAIN_MAX + 1), MAIN_COUNT)),
            tuple(rng.sample(range(1, star_pool_size(day) + 1), STAR_COUNT)),
        )
        for day in draw_dates(count, start)
    ]


@pytest.fixture
def make_history() -> HistoryFactory:
    return random_history


@pytest.fixture
def history() -> list[Draw]:
    """300 random draws under the current rules (January 2017 onwards)."""
    return random_history()
