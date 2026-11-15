"""Backtesting: what would the suggestion strategies have won on draws they had not seen?

The history is split in two. The oldest part only serves to build statistics.
Each draw of the remaining part is then played with lines suggested from the
draws that came before it, and scored with the prizes actually paid that day.

Every strategy rests on a hypothesis (hot numbers stay hot, overdue numbers
are due, ...). If one of them had any predictive power, its lines would match
more numbers on unseen draws than chance allows, which is what the z-scores of
a :class:`StrategyResult` measure. Money is a much noisier signal: half of the
prize money sits in top ranks that a backtest virtually never hits.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from statistics import NormalDist, fmean

from euromillions import suggester
from euromillions.analyzer import DEFAULT_WINDOW
from euromillions.errors import HistoryError
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
    chronological,
    picks_per_draw,
    pool_size,
)

#: Name of the baseline that plays uniformly random lines.
RANDOM = suggester.BASELINE

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

#: The three top ranks are often won by nobody, and then have no published prize. Every
#: other rank is won at each draw: these "common" ranks are all a backtest realistically hits.
TOP_RANKS = 3
COMMON_RANKS: tuple[int, ...] = tuple(sorted(PRIZE_RANKS.values()))[TOP_RANKS:]

#: Called with ``(done, total)`` as a backtest plays its draws; see :func:`backtest`.
ProgressCallback = Callable[[int, int], None]

# --- How to read a backtest ---------------------------------------------------------------------
#
# Shared by every report of a backtest (the command line, the web pages), so that they all
# read the same result the same way.

#: Below this many lines per strategy, luck dominates the figures of a backtest.
FEW_LINES = 1000

_NORMAL = NormalDist()

#: Probability that a z-score lands beyond +/-2 by chance alone (about 1 in 22).
Z_BEYOND_TWO = 2 * (1 - _NORMAL.cdf(2))

#: One line in this many wins one of the :data:`TOP_RANKS` top ranks (about 2.1 million).
TOP_RANK_ODDS = 1 / sum(RANK_PROBABILITIES[rank] for rank in range(1, TOP_RANKS + 1))


def chance_of_extreme_z(count: int) -> float:
    """Return the probability that at least one of ``count`` z-scores lands beyond +/-2."""
    return 1 - (1 - Z_BEYOND_TWO) ** count


def edge_threshold(count: int) -> float:
    """Return how far one of ``count`` z-scores must go to point to a real edge.

    It is the level that any of them passes by chance in 5% of the backtests only
    (a Bonferroni correction): about 2.9 for 12 z-scores.
    """
    return _NORMAL.inv_cdf(1 - 0.05 / (2 * count))


def win_rate_sd(lines: int) -> float:
    """Return the standard deviation of the share of winning lines among ``lines`` random ones."""
    return math.sqrt(WIN_PROBABILITY * (1 - WIN_PROBABILITY) / lines)


def expected_wins(lines: int) -> dict[int, float]:
    """Return how many of ``lines`` random lines are expected to win each prize rank."""
    return {rank: lines * probability for rank, probability in RANK_PROBABILITIES.items()}


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
    fair draw. Each one lies beyond 2 in absolute value about once in 22 by
    chance alone, so several of them must be read together, not one by one.
    ``estimated_wins`` counts the wins whose amount had to be estimated: a top
    rank that nobody actually won that day (valued at the average pool of that
    rank in earlier draws), or a rank whose number of winners is unknown.
    ``top_winnings`` is the part of ``winnings`` won in the top ranks, which the
    chance yardstick of a :class:`Backtest` leaves out.
    """

    strategy: str
    lines: int
    cost: float
    winnings: float
    top_winnings: float
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
        return self.net / self.cost

    @property
    def winning_lines(self) -> int:
        return sum(self.wins_by_rank.values())

    @property
    def top_wins(self) -> int:
        """Lines that won one of the top ranks."""
        return sum(self.wins_by_rank[rank] for rank in range(1, TOP_RANKS + 1))

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

    ``chance_winnings`` is what as many uniformly random lines are expected to
    win on the same draws from the common ranks (4 to 13), and
    ``chance_winnings_sd`` the standard deviation of that amount: the yardstick
    for the winnings of every strategy, whose differences are otherwise noise.
    It is to be compared with what a strategy won in those ranks, that is
    without its ``top_winnings``.
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
    chance_winnings: float
    chance_winnings_sd: float
    results: list[StrategyResult]


def backtest(
    draws: Iterable[Draw],
    strategies: Sequence[str] = DEFAULT_STRATEGIES,
    *,
    train_fraction: float = 0.8,
    lines: int = 5,
    window: int = DEFAULT_WINDOW,
    seed: int | None = None,
    refresh: bool = True,
    line_price: float = LINE_PRICE,
    progress: ProgressCallback | None = None,
) -> Backtest:
    """Play ``strategies`` on the most recent part of the history and report the outcome.

    The oldest ``train_fraction`` of the draws is the training set. Each later
    draw is played with ``lines`` lines per strategy, each strategy being
    evaluated once however many times it is named. With ``refresh`` (the
    default) the lines are suggested afresh before every draw from all the
    draws that precede it, the way the tool would be used week after week;
    without it they are suggested once from the training set and replayed
    unchanged. Either way no line ever depends on the draw it is scored on.

    Only draws under the current rules can be evaluated, because earlier ones
    had other pools and prize ranks, and only those that carry their prize
    data. ``line_price`` defaults to the price of a line in France, which
    includes a raffle entry that is not simulated here.

    ``progress``, if given, is called with ``(done, total)`` after each draw played by
    each strategy, ``total`` being the number of strategies times the number of draws
    played. An exception it raises stops the backtest and propagates: that is how a
    caller cancels one.

    Raises :class:`ValueError` for an invalid argument, and its subclass
    :class:`~euromillions.errors.HistoryError` when the history cannot be split
    or evaluated as asked.
    """
    history = chronological(draws)
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
    if not (line_price > 0 and math.isfinite(line_price)):
        raise ValueError(f"line_price must be a positive amount, got {line_price}")
    # Named twice, a strategy would fill a second row with the same run or, unseeded, another.
    strategies = list(dict.fromkeys(strategies))

    # The tiny offset keeps e.g. 0.58 * 100 (57.99999999999999 in floating point) at 58 draws.
    split = math.floor(len(history) * train_fraction + 1e-9)
    if split < 1 or split >= len(history):
        raise HistoryError(
            f"cannot split {len(history)} draws with a train fraction of {train_fraction}: "
            "both the training and the evaluation set need at least one draw"
        )
    if history[split].date < CURRENT_RULES_SINCE:
        older = sum(draw.date < CURRENT_RULES_SINCE for draw in history)
        if older == len(history):
            raise HistoryError(
                f"no draw is under the current rules ({CURRENT_RULES_SINCE}): nothing to evaluate"
            )
        raise HistoryError(
            f"the evaluation would start on {history[split].date}, before the current rules "
            f"({CURRENT_RULES_SINCE}); use a train fraction of at least "
            f"{_round_up(older / len(history))}"
        )
    scored = [index for index in range(split, len(history)) if _is_scorable(history[index])]
    if not scored:
        raise HistoryError(
            "none of the draws to evaluate has prize data; run `euromillions fetch --force` "
            "to download it"
        )

    total = len(strategies) * len(scored)
    done = 0

    def played_one() -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(done, total)

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
            # One independent, reproducible stream per strategy, whichever others are evaluated.
            rng=random.Random(None if seed is None else f"{seed}:{name}"),
            on_draw=played_one,
        )
        for name in strategies
    ]
    chance_winnings, chance_winnings_sd = _chance_winnings(
        [history[index] for index in scored], lines
    )
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
        chance_winnings=chance_winnings,
        chance_winnings_sd=chance_winnings_sd,
        results=results,
    )


def _round_up(fraction: float) -> str:
    """Format a fraction below 1 rounded up, with as few decimals (from three) as keep it so."""
    for decimals in range(3, 10):
        scale = 10**decimals
        # Rounding first absorbs float noise such as 0.6 * 1000 == 600.0000000000001.
        rounded = math.ceil(round(fraction * scale, 6)) / scale
        if rounded < 1:
            return f"{rounded:.{decimals}f}"
    return repr(fraction)


def _is_scorable(draw: Draw) -> bool:
    """Tell whether ``draw`` carries the prizes needed to score any line played on it.

    The common ranks are won at every draw: a missing amount there is a gap in the data,
    not a rank that nobody won.
    """
    return len(draw.prizes) == len(PRIZE_RANKS) and all(
        tier.prize for tier in draw.prizes[TOP_RANKS:]
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
    rng: random.Random,
    on_draw: Callable[[], None],
) -> StrategyResult:
    """Play one strategy on the draws at the ``scored`` indexes of ``history``.

    The lines come from the training set ``history[:split]`` or, with ``refresh``,
    from all the draws before the one being played: never from that draw itself.
    """
    wins_by_rank = dict.fromkeys(sorted(PRIZE_RANKS.values()), 0)
    winnings = top_winnings = 0.0
    estimated_wins = 0
    best_win: Win | None = None
    matches: dict[BallKind, int] = {"main": 0, "star": 0}
    variance: dict[BallKind, float] = {"main": 0.0, "star": 0.0}

    def suggest(known: list[Draw]) -> list[Suggestion]:
        """Return the lines the strategy plays when only the ``known`` draws have happened."""
        return suggester.suggest(
            known, strategy, lines=lines, window=window, seed=rng.getrandbits(64)
        )

    played = [] if refresh else suggest(history[:split])
    for index in scored:
        draw = history[index]
        if refresh:
            played = suggest(history[:index])

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
            amount, estimated = _prize(history, index, rank, count)
            wins_by_rank[rank] += count
            winnings += amount * count
            top_winnings += amount * count if rank <= TOP_RANKS else 0.0
            estimated_wins += count if estimated else 0
            if best_win is None or amount > best_win.amount:
                best_win = Win(draw.date, rank, amount)
        on_draw()

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
        top_winnings=top_winnings,
        wins_by_rank=wins_by_rank,
        estimated_wins=estimated_wins,
        best_win=best_win,
        main_matches=matches["main"],
        star_matches=matches["star"],
        main_z=z_scores["main"],
        star_z=z_scores["star"],
    )


def _chance_variance(played: Sequence[Suggestion], kind: BallKind) -> float:
    """Variance, under a fair draw, of the total number of ``kind`` matches of ``played``.

    The total is the sum over the pool of (lines containing the ball) x (ball drawn).
    Balls are drawn without replacement, so with ``p = picks / pool`` each indicator
    has variance ``p(1 - p)`` and two of them have covariance ``-p(1 - p) / (pool - 1)``.
    """
    pool = pool_size(kind)
    p = picks_per_draw(kind) / pool
    counts = Counter(
        ball for line in played for ball in (line.numbers if kind == "main" else line.stars)
    )
    squares = sum(count * count for count in counts.values())
    total = sum(counts.values())
    return p * (1 - p) * (squares - (total * total - squares) / (pool - 1))


def _chance_winnings(draws: Iterable[Draw], lines: int) -> tuple[float, float]:
    """Return the mean and standard deviation of what random lines win from the common ranks.

    ``lines`` independent, uniformly random lines are played on each of the ``draws``.
    The top ranks are left out: they would dominate both figures while almost never
    showing up in an actual backtest.
    """
    mean = variance = 0.0
    for draw in draws:
        first_moment = second_moment = 0.0
        for rank in COMMON_RANKS:
            amount = _shared(draw, rank, 1)
            first_moment += RANK_PROBABILITIES[rank] * amount
            second_moment += RANK_PROBABILITIES[rank] * amount * amount
        mean += lines * first_moment
        variance += lines * (second_moment - first_moment * first_moment)
    return mean, math.sqrt(variance)


def _shared(draw: Draw, rank: int, sharing: int) -> float:
    """Return what each of ``sharing`` extra winners of a rank that was won would have received.

    Prizes are pari-mutuel: the pool of a rank is split between its winners, so the lines
    played here would have shared it with the real ones. Without the number of real
    winners (missing from the source for a few draws), the published prize is kept as is.
    """
    tier = draw.prizes[rank - 1]
    return tier.prize * tier.winners / (tier.winners + sharing) if tier.winners else tier.prize


def _prize(history: Sequence[Draw], index: int, rank: int, sharing: int) -> tuple[float, bool]:
    """Return what each of the ``sharing`` lines winning ``rank`` on ``history[index]`` gets.

    The second value tells whether the amount is an estimate: the number of real winners
    is unknown or, for a top rank that nobody won that day, the pool is. An unknown pool is
    replaced by the average pool of the rank over the earlier draws in which it was won
    (never later ones, which were still in the future).
    """
    draw = history[index]
    tier = draw.prizes[rank - 1]
    if tier.prize:
        return _shared(draw, rank, sharing), not tier.winners
    pools = [
        earlier.prizes[rank - 1].winners * earlier.prizes[rank - 1].prize
        for earlier in history[:index]
        if earlier.date >= CURRENT_RULES_SINCE and len(earlier.prizes) == len(PRIZE_RANKS)
    ]
    won = [pool for pool in pools if pool]
    return (fmean(won) / sharing if won else 0.0), True
