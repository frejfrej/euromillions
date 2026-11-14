"""Tests for the draw schedule."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from euromillions.schedule import (
    DRAW_TIMEZONE,
    draw_datetime,
    is_draw_day,
    next_draw,
    previous_draw,
)

UTC = dt.timezone.utc


def paris(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=DRAW_TIMEZONE)


# Week used throughout: Monday 2026-11-09 .. Sunday 2026-11-15 (draws on Tue 10th and Fri 13th).


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (dt.date(2026, 11, 9), False),  # Monday
        (dt.date(2026, 11, 10), True),  # Tuesday
        (dt.date(2026, 11, 11), False),
        (dt.date(2026, 11, 12), False),
        (dt.date(2026, 11, 13), True),  # Friday
        (dt.date(2026, 11, 14), False),
        (dt.date(2026, 11, 15), False),
    ],
)
def test_draws_are_on_tuesdays_and_fridays(day: dt.date, expected: bool) -> None:
    assert is_draw_day(day) is expected


def test_draw_datetime_is_evening_in_paris() -> None:
    moment = draw_datetime(dt.date(2026, 11, 13))
    assert moment == paris(2026, 11, 13, 21, 5)
    assert moment.utcoffset() == dt.timedelta(hours=1)
    # In summer Paris is two hours ahead of UTC; the local draw time does not move.
    assert draw_datetime(dt.date(2026, 7, 14)).utcoffset() == dt.timedelta(hours=2)
    assert draw_datetime(dt.date(2026, 7, 14)).astimezone(UTC).hour == 19


def test_draw_datetime_rejects_other_days() -> None:
    with pytest.raises(ValueError, match="Saturday"):
        draw_datetime(dt.date(2026, 11, 14))


@pytest.mark.parametrize(
    ("now", "expected_next", "expected_previous"),
    [
        # Monday: next is Tuesday, previous is last Friday.
        (paris(2026, 11, 9, 12), paris(2026, 11, 10, 21, 5), paris(2026, 11, 6, 21, 5)),
        # Tuesday before the draw.
        (paris(2026, 11, 10, 21, 4), paris(2026, 11, 10, 21, 5), paris(2026, 11, 6, 21, 5)),
        # Tuesday at draw time: that draw is now the previous one.
        (paris(2026, 11, 10, 21, 5), paris(2026, 11, 13, 21, 5), paris(2026, 11, 10, 21, 5)),
        # Wednesday.
        (paris(2026, 11, 11, 8), paris(2026, 11, 13, 21, 5), paris(2026, 11, 10, 21, 5)),
        # Friday after the draw.
        (paris(2026, 11, 13, 23), paris(2026, 11, 17, 21, 5), paris(2026, 11, 13, 21, 5)),
        # Weekend.
        (paris(2026, 11, 15, 9), paris(2026, 11, 17, 21, 5), paris(2026, 11, 13, 21, 5)),
    ],
)
def test_next_and_previous_draw(
    now: dt.datetime, expected_next: dt.datetime, expected_previous: dt.datetime
) -> None:
    assert next_draw(now) == expected_next
    assert previous_draw(now) == expected_previous


def test_other_timezones_are_converted_to_paris_time() -> None:
    # Tuesday 20:30 UTC is 21:30 in Paris: the Tuesday draw has already happened.
    now = dt.datetime(2026, 11, 10, 20, 30, tzinfo=UTC)
    assert next_draw(now) == paris(2026, 11, 13, 21, 5)
    assert previous_draw(now) == paris(2026, 11, 10, 21, 5)
    # Saturday 07:00 in Sydney is still Friday 21:00 in Paris, minutes before the draw.
    sydney = dt.datetime(2026, 11, 14, 7, 0, tzinfo=ZoneInfo("Australia/Sydney"))
    assert next_draw(sydney) == paris(2026, 11, 13, 21, 5)


def test_results_are_in_paris_time() -> None:
    now = dt.datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    assert next_draw(now).tzinfo is DRAW_TIMEZONE
    assert previous_draw(now).tzinfo is DRAW_TIMEZONE


def test_schedule_across_a_daylight_saving_change() -> None:
    # Clocks go back on Sunday 2026-10-25, between a Friday and a Tuesday draw.
    friday = paris(2026, 10, 23, 21, 5)
    tuesday = paris(2026, 10, 27, 21, 5)
    assert next_draw(friday) == tuesday
    assert previous_draw(tuesday - dt.timedelta(seconds=1)) == friday
    # The draw stays at 21:05 local time, so one extra real hour separates the two.
    assert tuesday.astimezone(UTC) - friday.astimezone(UTC) == dt.timedelta(days=4, hours=1)


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_draw(dt.datetime(2026, 11, 10, 12, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        previous_draw(dt.datetime(2026, 11, 10, 12, 0))


def test_defaults_to_the_current_time() -> None:
    before = dt.datetime.now(tz=UTC)
    upcoming = next_draw()
    latest = previous_draw()
    assert latest <= before < upcoming
    assert (upcoming.date() - latest.date()).days in {3, 4}
