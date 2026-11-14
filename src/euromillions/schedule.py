"""EuroMillions draw schedule: every Tuesday and Friday evening in Paris."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

#: Draw days as ``date.weekday()`` values (Monday is 0): Tuesday and Friday.
DRAW_WEEKDAYS = frozenset({1, 4})

#: Approximate time of the draw, in Paris local time.
DRAW_TIME = dt.time(21, 5)
DRAW_TIMEZONE = ZoneInfo("Europe/Paris")


def is_draw_day(day: dt.date) -> bool:
    """Return whether a draw is scheduled on ``day``."""
    return day.weekday() in DRAW_WEEKDAYS


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
