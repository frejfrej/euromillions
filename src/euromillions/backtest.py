"""Backtesting: what would the suggestion strategies have won on draws they had not seen?

The history is split in two. The oldest part only serves to build statistics.
Each draw of the remaining part is then played with lines suggested from the
draws that came before it, and scored with the prizes actually paid that day.

Every strategy rests on a hypothesis (hot numbers stay hot, overdue numbers
are due, ...). If one of them had any predictive power, its lines would match
more numbers on unseen draws than chance allows, which is exactly what the
z-scores of a :class:`StrategyResult` measure. Money is a much noisier signal:
half of the prize money sits in a jackpot that a backtest virtually never hits.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

from euromillions import suggester
from euromillions.models import (
    CURRENT_RULES_SINCE,
    LINE_PRICE,
    MAIN_COUNT,
    MAIN_MAX,
    PRIZE_RANKS,
    STAR_COUNT,
    STAR_MAX,
    BallKind,
    Draw,
    Suggestion,
    picks_per_draw,
)

#: Name of the baseline that plays uniformly random lines.
RANDOM = "random"

#: What a backtest compares by default: every suggestion strategy, then the baseline.
DEFAULT_STRATEGIES: tuple[str, ...] = (*suggester.STRATEGIES, RANDOM)

_LINES = math.comb(MAIN_MAX, MAIN_COUNT) * math.comb(STAR_MAX, STAR_COUNT)

#: Probability that a random line wins each prize rank under the current rules.
RANK_PROBABILITIES: dict[int, float] = {
    rank: math.comb(MAIN_COUNT, main)
    * math.comb(MAIN_MAX - MAIN_COUNT, MAIN_COUNT - main)
    * math.comb(STAR_COUNT, stars)
    * math.comb(STAR_MAX - STAR_COUNT, STAR_COUNT - stars)
    / _LINES
    for (main, stars), rank in PRIZE_RANKS.items()
}

#: Probability that a random line wins any prize (about 1 in 13).
WIN_PROBABILITY = sum(RANK_PROBABILITIES.values())

#: Average matches of a random line: main numbers (0.5) and Lucky Stars (0.33).
EXPECTED_MATCHES: dict[BallKind, float] = {
    "main": MAIN_COUNT * MAIN_COUNT / MAIN_MAX,
    "star": STAR_COUNT * STAR_COUNT / STAR_MAX,
}


@dataclass(frozen=True, slots=True)
class Win:
    """A prize won by one line."""

    date: dt.date
    rank: int
    amount: float


@dataclass(frozen=True, slots=True)
class StrategyResult:
    """Outcome of playing one strategy on every evaluated draw.

    ``main_z`` and ``star_z`` compare the numbers and stars matched with what
    pure chance would give: they are z-scores under the exact distribution of a
    fair draw, so values beyond about 2 in absolute value would be unusual.
    ``estimated_wins`` counts wins of a rank nobody actually won that day, whose
    amount is therefore an estimate (the average prize pool of that rank).
    """

    strategy: str
    lines: int
    cost: float
    winnings: float
    wins_by_rank: dict[int, int]
    estimated_wins: int
    best_win: Win | None
    main_matches: int
    star_matches: int
    main_z: float
    star_z: float

    @property
    def net(self) -> float:
        """Winnings minus cost."""
        return self.winnings - self.cost

    @property
    def roi(self) -> float:
        """Net result as a fraction of the cost (-1.0 means everything was lost)."""
        return self.net / self.cost if self.cost else 0.0

    @property
    def winning_lines(self) -> int:
        return sum(self.wins_by_rank.values())

    @property
    def win_rate(self) -> float:
        """Share of the lines that won a prize."""
        return self.winning_lines / self.lines

    @property
    def main_matches_per_line(self) -> float:
        return self.main_matches / self.lines

    @property
    def star_matches_per_line(self) -> float:
        return self.star_matches / self.lines


@dataclass(frozen=True, slots=True)
class Backtest:
    """A backtest: how the history was split, and one result per strategy.

    ``test_draws`` counts the draws actually played. ``skipped_draws`` counts
    those of the evaluation period that could not be scored for lack of prize
    data; strategies still see them as history when suggesting later lines.
    """

    train_draws: int
    train_start: dt.date
    train_end: dt.date
    test_draws: int
    test_start: dt.date
    test_end: dt.date
    skipped_draws: int
    lines_per_draw: int
    line_price: float
    refresh: bool
    results: list[StrategyResult]


def backtest(
    draws: Iterable[Draw],
    strategies: Sequence[str] = DEFAULT_STRATEGIES,
    *,
    train_fraction: float = 0.8,
    lines: int = 5,
    window: int = 50,
    seed: int | None = None,
    refresh: bool = True,
    line_price: float = LINE_PRICE,
) -> Backtest:
    """Play ``strategies`` on the most recent part of the history and report the outcome.

    The oldest ``train_fraction`` of the draws is the training set. Each later
    draw is played with ``lines`` lines per strategy. With ``refresh`` (the
    default) the lines are suggested afresh before every draw from all the
    draws that precede it, the way the tool would be used week after week;
    without it they are suggested once from the training set and replayed
    unchanged. Either way no line ever depends on the draw it is scored on.

    Only draws under the current rules can be evaluated, because earlier ones
    had other pools and prize ranks, and only those that carry their prize
    data. ``line_price`` defaults to the price of a line in France, which
    includes a raffle entry that is not simulated here.
    """
    history = sorted(draws, key=lambda draw: draw.date)
    if not strategies:
        raise ValueError("no strategy to evaluate")
    unknown = [name for name in strategies if name not in DEFAULT_STRATEGIES]
    if unknown:
        raise ValueError(
            f"unknown strategies: {', '.join(unknown)}; choose from {', '.join(DEFAULT_STRATEGIES)}"
        )
    if not 0 < train_fraction < 1:
        raise ValueError(
            f"train_fraction must be between 0 and 1 (exclusive), got {train_fraction}"
        )
    if lines < 1:
        raise ValueError(f"lines must be at least 1, got {lines}")
    if window < 1:
        raise ValueError(f"window must be at least 1, got {window}")
    if line_price < 0:
        raise ValueError(f"line_price cannot be negative, got {line_price}")

    # The tiny offset keeps e.g. 0.58 * 100 (57.99999999999999 in floating point) at 58 draws.
    split = math.floor(len(history) * train_fraction + 1e-9)
    if split < 1 or split >= len(history):
        raise ValueError(
            f"cannot split {len(history)} draws with a train fraction of {train_fraction}: "
            "both the training and the evaluation set need at least one draw"
        )
    if history[split].date < CURRENT_RULES_SINCE:
        older = sum(draw.date < CURRENT_RULES_SINCE for draw in history)
        # Rounded up, so that the fraction suggested is always large enough.
        minimum = math.ceil(round(older / len(history) * 1000, 6)) / 1000
        raise ValueError(
            f"the evaluation would start on {history[split].date}, before the current rules "
            f"({CURRENT_RULES_SINCE}); use a train fraction of at least {minimum:.3f}"
        )
    scored = [
        index
        for index in range(split, len(history))
        if len(history[index].prizes) == len(PRIZE_RANKS)
    ]
    if not scored:
        raise ValueError(
            "none of the draws to evaluate has prize data; run `euromillions fetch --force` "
            "to download it"
        )

    pools = _average_pools(history)
    results = [
        _play(
            name,
            history,
            split,
            scored,
            lines=lines,
            window=window,
            refresh=refresh,
            line_price=line_price,
            pools=pools,
            # One independent, reproducible stream per strategy, whichever others are evaluated.
            rng=random.Random(None if seed is None else f"{seed}:{name}"),
        )
        for name in strategies
    ]
    return Backtest(
        train_draws=split,
        train_start=history[0].date,
        train_end=history[split - 1].date,
        test_draws=len(scored),
        test_start=history[scored[0]].date,
        test_end=history[scored[-1]].date,
        skipped_draws=len(history) - split - len(scored),
        lines_per_draw=lines,
        line_price=line_price,
        refresh=refresh,
        results=results,
    )


def _play(
    strategy: str,
    history: list[Draw],
    split: int,
    scored: Sequence[int],
    *,
    lines: int,
    window: int,
    refresh: bool,
    line_price: float,
    pools: Mapping[int, float],
    rng: random.Random,
) -> StrategyResult:
    """Play one strategy on the draws at the ``scored`` indexes of ``history``.

    The lines come from the training set ``history[:split]`` or, with ``refresh``,
    from all the draws before the one being played: never from that draw itself.
    """
    wins_by_rank = dict.fromkeys(sorted(PRIZE_RANKS.values()), 0)
    winnings = 0.0
    estimated_wins = 0
    best_win: Win | None = None
    matches: dict[BallKind, int] = {"main": 0, "star": 0}
    variance: dict[BallKind, float] = {"main": 0.0, "star": 0.0}

    played = [] if refresh else _suggest(strategy, history[:split], lines, window, rng)
    for index in scored:
        draw = history[index]
        if refresh:
            played = _suggest(strategy, history[:index], lines, window, rng)

        ranks = []
        for line in played:
            main = len(set(line.numbers) & set(draw.numbers))
            stars = len(set(line.stars) & set(draw.stars))
            matches["main"] += main
            matches["star"] += stars
            ranks.append(PRIZE_RANKS.get((main, stars)))
        for kind in matches:
            variance[kind] += _chance_variance(played, kind)

        for rank, count in Counter(rank for rank in ranks if rank is not None).items():
            amount, estimated = _prize(draw, rank, count, pools)
            wins_by_rank[rank] += count
            winnings += amount * count
            estimated_wins += count if estimated else 0
            if best_win is None or amount > best_win.amount:
                best_win = Win(draw.date, rank, amount)

    total_lines = lines * len(scored)
    z_scores = {
        kind: (
            (matches[kind] - total_lines * EXPECTED_MATCHES[kind]) / math.sqrt(variance[kind])
            if variance[kind]
            else 0.0
        )
        for kind in matches
    }
    return StrategyResult(
        strategy=strategy,
        lines=total_lines,
        cost=total_lines * line_price,
        winnings=winnings,
        wins_by_rank=wins_by_rank,
        estimated_wins=estimated_wins,
        best_win=best_win,
        main_matches=matches["main"],
        star_matches=matches["star"],
        main_z=z_scores["main"],
        star_z=z_scores["star"],
    )


def _suggest(
    strategy: str, known: list[Draw], lines: int, window: int, rng: random.Random
) -> list[Suggestion]:
    """Return the lines ``strategy`` plays when only the ``known`` draws have happened."""
    if strategy != RANDOM:
        return suggester.suggest(
            known, strategy, lines=lines, window=window, seed=rng.getrandbits(64)
        )
    picked: dict[Suggestion, None] = {}
    while len(picked) < lines:
        numbers = rng.sample(range(1, MAIN_MAX + 1), MAIN_COUNT)
        stars = rng.sample(range(1, STAR_MAX + 1), STAR_COUNT)
        picked.setdefault(Suggestion(tuple(numbers), tuple(stars), RANDOM))
    return list(picked)


def _chance_variance(played: Sequence[Suggestion], kind: BallKind) -> float:
    """Variance, under a fair draw, of the total number of ``kind`` matches of ``played``.

    The total is the sum over the pool of (lines containing the ball) x (ball drawn).
    Balls are drawn without replacement, so with ``p = picks / pool`` each indicator
    has variance ``p(1 - p)`` and two of them have covariance ``-p(1 - p) / (pool - 1)``.
    """
    pool = MAIN_MAX if kind == "main" else STAR_MAX
    p = picks_per_draw(kind) / pool
    counts = Counter(
        ball for line in played for ball in (line.numbers if kind == "main" else line.stars)
    )
    squares = sum(count * count for count in counts.values())
    total = sum(counts.values())
    return p * (1 - p) * (squares - (total * total - squares) / (pool - 1))


def _average_pools(history: Iterable[Draw]) -> dict[int, float]:
    """Return the average prize pool of each rank over the draws where it was won."""
    pools: dict[int, list[float]] = {}
    for draw in history:
        if draw.date < CURRENT_RULES_SINCE or len(draw.prizes) != len(PRIZE_RANKS):
            continue
        for rank, tier in enumerate(draw.prizes, start=1):
            if tier.winners and tier.prize:
                pools.setdefault(rank, []).append(tier.winners * tier.prize)
    return {rank: fmean(amounts) for rank, amounts in pools.items()}


def _prize(draw: Draw, rank: int, sharing: int, pools: Mapping[int, float]) -> tuple[float, bool]:
    """Return what each of ``sharing`` extra winning lines would have received.

    Prizes are pari-mutuel: the lines played here would have shared the pool of
    their rank with the real winners. When nobody won that rank, the pool is
    not published and the average pool of the rank stands in for it; the second
    value tells whether the amount is such an estimate.
    """
    tier = draw.prizes[rank - 1]
    if not tier.prize:
        return pools.get(rank, 0.0) / sharing, True
    if not tier.winners:  # The number of winners is missing from the source for a few draws.
        return tier.prize, False
    return tier.prize * tier.winners / (tier.winners + sharing), False
