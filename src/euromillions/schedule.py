"""EuroMillions draw schedule: every Tuesday and Friday evening in Paris."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from euromillions.models import FIRST_DRAW_DATE

#: Draws have been held every Friday since the first one, and also every Tuesday since
#: this date.
TUESDAY_DRAWS_SINCE = dt.date(2011, 5, 10)

_TUESDAY, _FRIDAY = 1, 4  # as returned by date.weekday()

#: Approximate time of the draw, in Paris local time.
DRAW_TIME = dt.time(21, 5)
DRAW_TIMEZONE = ZoneInfo("Europe/Paris")


def is_draw_day(day: dt.date) -> bool:
    """Return whether a draw is, or was, scheduled on ``day``."""
    if day < FIRST_DRAW_DATE:
        return False
    weekday = day.weekday()
    return weekday == _FRIDAY or (weekday == _TUESDAY and day >= TUESDAY_DRAWS_SINCE)


def draw_datetime(day: dt.date) -> dt.datetime:
    """Return the moment of the draw held on ``day`` (timezone-aware, Paris time)."""
    if not is_draw_day(day):
        raise ValueError(f"no draw is scheduled on {day.isoformat()} ({day:%A})")
    return dt.datetime.combine(day, DRAW_TIME, tzinfo=DRAW_TIMEZONE)


def _paris_time(now: dt.datetime | None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(tz=DRAW_TIMEZONE)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("`now` must be a timezone-aware datetime")
    return now.astimezone(DRAW_TIMEZONE)


def today(now: dt.datetime | None = None) -> dt.date:
    """Return the date in Paris at ``now`` (default: the current time)."""
    return _paris_time(now).date()


def next_draw(now: dt.datetime | None = None) -> dt.datetime:
    """Return the first scheduled draw strictly after ``now`` (default: the current time)."""
    current = _paris_time(now)
    for offset in range(8):
        day = current.date() + dt.timedelta(days=offset)
        if is_draw_day(day) and draw_datetime(day) > current:
            return draw_datetime(day)
    raise AssertionError("unreachable: there is a draw at least twice a week")


def previous_draw(now: dt.datetime | None = None) -> dt.datetime:
    """Return the most recent scheduled draw at or before ``now`` (default: the current time)."""
    current = _paris_time(now)
    for offset in range(8):
        day = current.date() - dt.timedelta(days=offset)
        if is_draw_day(day) and draw_datetime(day) <= current:
            return draw_datetime(day)
    raise AssertionError("unreachable: there is a draw at least twice a week")
