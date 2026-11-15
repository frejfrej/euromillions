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
#: Said with every result, by every front end.
DISCLAIMER = (
    "Lottery draws are random: past results cannot predict future ones.\n"
    "For educational and entertainment purposes only."
)

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

#: First draw under the current rules: 12 Lucky Stars, the prize ranks below, EUR 2.50 a line.
CURRENT_RULES_SINCE = STAR_POOL_HISTORY[-1][0]

#: Prize rank won under the current rules, by (main numbers matched, Lucky Stars matched).
#: Rank 1 is the jackpot; any other combination wins nothing.
PRIZE_RANKS: dict[tuple[int, int], int] = {
    (5, 2): 1,
    (5, 1): 2,
    (5, 0): 3,
    (4, 2): 4,
    (4, 1): 5,
    (3, 2): 6,
    (4, 0): 7,
    (2, 2): 8,
    (3, 1): 9,
    (3, 0): 10,
    (1, 2): 11,
    (2, 1): 12,
    (2, 0): 13,
}

#: Price of one line in France, in euros. EUR 2.20 of it funds the draw itself and
#: EUR 0.30 the "My Million" raffle that comes with every line.
LINE_PRICE = 2.5

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


def pool_size(kind: BallKind, on: dt.date | None = None) -> int:
    """Return the size of the ``kind`` pool for a draw held on ``on`` (default: today's)."""
    if kind == "main":
        return MAIN_MAX
    return STAR_MAX if on is None else star_pool_size(on)


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


#: No count of winners or amount in euros comes anywhere near this.
_MAX_VALUE = 1e12


def _is_number(value: object) -> bool:
    """Tell whether ``value`` is a real number (not a bool) between 0 and ``_MAX_VALUE``.

    The upper bound also rules out infinity, NaN and integers too large for a float.
    """
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= _MAX_VALUE
    )


@dataclass(frozen=True, slots=True)
class PrizeTier:
    """What one prize rank paid in a draw.

    ``winners`` counts the winning lines across Europe and ``prize`` is the
    amount each of them received, in euros (0 when nobody won that rank).
    """

    winners: int
    prize: float

    def __post_init__(self) -> None:
        if not _is_number(self.winners) or not isinstance(self.winners, int):
            raise ValueError(f"winners must be a non-negative integer, got {self.winners!r}")
        if not _is_number(self.prize):
            raise ValueError(f"prize must be a non-negative number, got {self.prize!r}")
        object.__setattr__(self, "prize", float(self.prize))


@dataclass(frozen=True, slots=True)
class Draw:
    """One historical EuroMillions draw.

    ``numbers`` and ``stars`` are validated against the rules in force on
    ``date`` and stored in ascending order. ``prizes`` lists every prize rank
    of the draw, best first (index 0 is rank 1, the jackpot), as the ranks
    were numbered at the time; it is empty when the prizes are unknown.
    """

    date: dt.date
    numbers: tuple[int, ...]
    stars: tuple[int, ...]
    prizes: tuple[PrizeTier, ...] = ()

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

        prizes = tuple(self.prizes)
        if not all(isinstance(tier, PrizeTier) for tier in prizes):
            raise ValueError(f"prizes must be PrizeTier objects, got {prizes!r}")
        object.__setattr__(self, "prizes", prizes)

    def __str__(self) -> str:
        return f"{self.date.isoformat()}: {format_line(self.numbers, self.stars)}"

    @property
    def jackpot(self) -> PrizeTier | None:
        """The top prize rank of the draw, or ``None`` when its prizes are unknown."""
        return self.prizes[0] if self.prizes else None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation (inverse of :meth:`from_dict`)."""
        data: dict[str, Any] = {
            "date": self.date.isoformat(),
            "numbers": list(self.numbers),
            "stars": list(self.stars),
        }
        if self.prizes:
            data["prizes"] = [[tier.winners, tier.prize] for tier in self.prizes]
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
                prizes=tuple(
                    PrizeTier(winners, prize) for winners, prize in data.get("prizes", ())
                ),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed draw record: {data!r}") from exc


def chronological(draws: Iterable[Draw]) -> list[Draw]:
    """Return the draws oldest first, in an order that does not depend on the input's.

    Numbers and stars only break ties between entries sharing a date.
    """
    return sorted(draws, key=lambda draw: (draw.date, draw.numbers, draw.stars))


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
