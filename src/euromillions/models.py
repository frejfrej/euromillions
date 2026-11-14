"""Game rules and core data models for EuroMillions.

The rules encoded here are the single source of truth for the rest of the
package: how many balls are drawn, from which pools, and how the Lucky Star
pool grew over time (which matters when analysing the full draw history).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

#: Main numbers drawn per draw, and the size of their pool (1-50, unchanged since 2004).
MAIN_COUNT = 5
MAIN_MAX = 50

#: Lucky Stars drawn per draw, and the size of their pool under the current rules (1-12).
STAR_COUNT = 2
STAR_MAX = 12

#: Date of the very first EuroMillions draw.
FIRST_DRAW_DATE = dt.date(2004, 2, 13)

#: Lucky Star pool size over time, as ``(first draw under these rules, pool size)``
#: pairs in chronological order. The main pool has always been 1-50.
STAR_POOL_HISTORY: tuple[tuple[dt.date, int], ...] = (
    (dt.date(2004, 2, 13), 9),
    (dt.date(2011, 5, 10), 11),
    (dt.date(2016, 9, 27), 12),
)

#: The two kinds of balls in a draw.
BallKind = Literal["main", "star"]


def star_pool_size(on: dt.date) -> int:
    """Return how many Lucky Stars were in the pool for a draw held on ``on``."""
    if on < FIRST_DRAW_DATE:
        raise ValueError(f"no EuroMillions draw before {FIRST_DRAW_DATE.isoformat()}: {on}")
    size = STAR_POOL_HISTORY[0][1]
    for since, pool in STAR_POOL_HISTORY:
        if on >= since:
            size = pool
    return size


def pool_size(kind: BallKind, on: dt.date) -> int:
    """Return the size of the ``kind`` pool for a draw held on ``on``."""
    return MAIN_MAX if kind == "main" else star_pool_size(on)


def picks_per_draw(kind: BallKind) -> int:
    """Return how many balls of ``kind`` are drawn in each draw."""
    return MAIN_COUNT if kind == "main" else STAR_COUNT


def format_line(numbers: Iterable[int], stars: Iterable[int]) -> str:
    """Format a line for display, e.g. ``"03 15 29 35 47 | stars 03 04"``."""
    main = " ".join(f"{n:02d}" for n in numbers)
    lucky = " ".join(f"{s:02d}" for s in stars)
    return f"{main} | stars {lucky}"


def _normalise(values: Iterable[int], *, count: int, maximum: int, label: str) -> tuple[int, ...]:
    """Validate a pick of ``count`` distinct integers in ``1..maximum``; return it sorted."""
    picked = tuple(values)
    for value in picked:
        # bool is a subclass of int, but True/False are never valid ball numbers.
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{label} must be integers, got {value!r}")
    if len(picked) != count:
        raise ValueError(f"expected {count} {label}, got {len(picked)}: {picked}")
    if len(set(picked)) != count:
        raise ValueError(f"{label} must be distinct, got {picked}")
    if any(not 1 <= value <= maximum for value in picked):
        raise ValueError(f"{label} must be between 1 and {maximum}, got {picked}")
    return tuple(sorted(picked))


@dataclass(frozen=True, slots=True)
class Draw:
    """One historical EuroMillions draw.

    ``numbers`` and ``stars`` are validated against the rules in force on
    ``date`` and stored in ascending order. The optional jackpot fields
    describe the top prize rank: how many tickets won it across Europe and
    how much each of them received, in euros.
    """

    date: dt.date
    numbers: tuple[int, ...]
    stars: tuple[int, ...]
    jackpot_winners: int | None = None
    jackpot_prize: float | None = None

    def __post_init__(self) -> None:
        # datetime is a subclass of date but does not compare with plain dates.
        if not isinstance(self.date, dt.date) or isinstance(self.date, dt.datetime):
            raise ValueError(f"draw date must be a datetime.date, got {self.date!r}")
        numbers = _normalise(self.numbers, count=MAIN_COUNT, maximum=MAIN_MAX, label="main numbers")
        stars = _normalise(
            self.stars,
            count=STAR_COUNT,
            maximum=star_pool_size(self.date),
            label=f"Lucky Stars on {self.date.isoformat()}",
        )
        object.__setattr__(self, "numbers", numbers)
        object.__setattr__(self, "stars", stars)

        winners = self.jackpot_winners
        if winners is not None and (
            not isinstance(winners, int) or isinstance(winners, bool) or winners < 0
        ):
            raise ValueError(f"jackpot_winners must be a non-negative integer, got {winners!r}")
        prize = self.jackpot_prize
        if prize is not None:
            if not isinstance(prize, (int, float)) or isinstance(prize, bool) or not prize >= 0:
                raise ValueError(f"jackpot_prize must be a non-negative number, got {prize!r}")
            object.__setattr__(self, "jackpot_prize", float(prize))

    def __str__(self) -> str:
        return f"{self.date.isoformat()}: {format_line(self.numbers, self.stars)}"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation (inverse of :meth:`from_dict`)."""
        data: dict[str, Any] = {
            "date": self.date.isoformat(),
            "numbers": list(self.numbers),
            "stars": list(self.stars),
        }
        if self.jackpot_winners is not None:
            data["jackpot_winners"] = self.jackpot_winners
        if self.jackpot_prize is not None:
            data["jackpot_prize"] = self.jackpot_prize
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Draw:
        """Build a draw from :meth:`to_dict` output.

        Raises :class:`ValueError` for any malformed input, so callers only
        have one exception type to handle.
        """
        try:
            return cls(
                date=dt.date.fromisoformat(data["date"]),
                numbers=tuple(data["numbers"]),
                stars=tuple(data["stars"]),
                jackpot_winners=data.get("jackpot_winners"),
                jackpot_prize=data.get("jackpot_prize"),
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"malformed draw record: {data!r}") from exc


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A suggested line for the next draw, valid under the current rules."""

    numbers: tuple[int, ...]
    stars: tuple[int, ...]
    strategy: str

    def __post_init__(self) -> None:
        numbers = _normalise(self.numbers, count=MAIN_COUNT, maximum=MAIN_MAX, label="main numbers")
        stars = _normalise(self.stars, count=STAR_COUNT, maximum=STAR_MAX, label="Lucky Stars")
        object.__setattr__(self, "numbers", numbers)
        object.__setattr__(self, "stars", stars)

    def __str__(self) -> str:
        return format_line(self.numbers, self.stars)
