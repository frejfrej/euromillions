"""The data behind the web pages, as JSON-ready dicts.

Every figure the pages show is computed here, from the same functions as the command line:
the pages only lay them out. The functions take domain objects and return plain dicts,
lists, strings, numbers, booleans and ``None``; dates are ISO strings.
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Mapping, Sequence
from typing import Any

from euromillions import analyzer, backtest, schedule, suggester
from euromillions.analyzer import Distribution, NumberStats, Ranking
from euromillions.backtest import Backtest, StrategyResult
from euromillions.fetcher import UpdateResult
from euromillions.models import (
    DISCLAIMER,
    LINE_PRICE,
    MAIN_COUNT,
    MAIN_MAX,
    PRIZE_RANKS,
    STAR_POOL_HISTORY,
    BallKind,
    Draw,
    chronological,
    format_line,
)

JSON = dict[str, Any]

#: Most lines the pages may ask for at once.
MAX_LINES = 10

#: Seeds the server picks when a page asks for lines without one.
_SEED_RANGE = (1, 1_000_000)

_RANK_MATCHES = {rank: matches for matches, rank in PRIZE_RANKS.items()}


def _date(day: dt.date | None) -> str | None:
    return None if day is None else day.isoformat()


def status(draws: Sequence[Draw], *, now: dt.datetime | None = None) -> JSON:
    """Describe the cached history, the next draw and the strategies on offer."""
    history = chronological(draws)
    latest = history[-1] if history else None
    jackpot = latest.jackpot if latest else None
    return {
        "cache": bool(history),
        "draws": len(history),
        "first": _date(history[0].date) if history else None,
        "last": _date(latest.date) if latest else None,
        "latest": None
        if latest is None
        else {
            "date": latest.date.isoformat(),
            "numbers": list(latest.numbers),
            "stars": list(latest.stars),
            "jackpotWon": None if jackpot is None else bool(jackpot.prize),
        },
        "nextDraw": schedule.next_draw(now).isoformat(),
        "behind": latest is None or latest.date < schedule.previous_draw(now).date(),
        "strategies": [
            {
                "name": name,
                "description": description,
                "usesWindow": name in suggester.WINDOW_STRATEGIES,
            }
            for name, description in suggester.STRATEGIES.items()
        ],
        "minTrainFraction": backtest.min_train_fraction(history),
        "disclaimer": DISCLAIMER,
    }


def number(stats: NumberStats) -> JSON:
    """Describe one main number or Lucky Star."""
    return {
        "number": stats.number,
        "count": stats.count,
        "eligible": stats.eligible_draws,
        "expected": stats.expected,
        "z": stats.z_score,
        "frequency": stats.frequency,
        "averageGap": stats.average_gap,
        "currentGap": stats.current_gap,
        "longestGap": stats.longest_gap,
        "lastSeen": _date(stats.last_seen),
    }


def _numbers(stats: Mapping[int, NumberStats] | Sequence[NumberStats]) -> list[JSON]:
    values = stats.values() if isinstance(stats, Mapping) else stats
    return [number(s) for s in values]


def _ranking(ranking: Ranking) -> JSON:
    return {
        "entries": [{"combo": list(combo), "count": count} for combo, count in ranking.entries],
        "more": ranking.more,
        "expected": ranking.expected,
        "typicalTop": ranking.typical_top,
    }


def _distribution(distribution: Distribution) -> JSON:
    low, high = distribution.quantile(0.1), distribution.quantile(0.9)
    return {
        "mean": distribution.mean,
        "median": distribution.median,
        "stdev": distribution.stdev,
        "min": distribution.minimum,
        "max": distribution.maximum,
        "histogram": distribution.histogram(),
        "low": low,
        "high": high,
        "centralShare": distribution.share_between(low, high),
    }


def _bands(distribution: Distribution, width: int) -> list[JSON]:
    """Group a distribution into consecutive bands of ``width`` values, empty ones included."""
    bands: dict[int, int] = {}
    for value, draws in distribution.histogram().items():
        start = value // width * width
        bands[start] = bands.get(start, 0) + draws
    return [
        {"from": start, "draws": bands.get(start, 0)}
        for start in range(min(bands), max(bands) + width, width)
    ]


def _star_pool_since() -> dict[int, str]:
    """Return the date each star beyond the first pool joined it."""
    since: dict[int, str] = {}
    previous = STAR_POOL_HISTORY[0][1]
    for day, pool in STAR_POOL_HISTORY[1:]:
        since.update(dict.fromkeys(range(previous + 1, pool + 1), day.isoformat()))
        previous = pool
    return since


def analysis(
    draws: Sequence[Draw], *, window: int = analyzer.DEFAULT_WINDOW, top: int = 10
) -> JSON:
    """Everything ``analyze --full`` computes, plus the stats of the recent window.

    Raises :class:`~euromillions.errors.HistoryError` for an empty history, and
    :class:`ValueError` for a ``window`` or ``top`` below 1.
    """
    report = analyzer.analyze(draws, window=window, top=top)
    latest = analyzer.recent(draws, window)
    most, least = analyzer.most_and_least_drawn(report.number_stats, top)
    most_stars, least_stars = analyzer.most_and_least_drawn(report.star_stats, top)
    kinds: tuple[BallKind, BallKind] = ("main", "star")
    consecutive = report.consecutive
    return {
        "totalDraws": report.total_draws,
        "first": report.first_date.isoformat(),
        "last": report.last_date.isoformat(),
        "window": report.window,
        "numbers": _numbers(report.number_stats),
        "stars": _numbers(report.star_stats),
        "recentNumbers": _numbers(analyzer.number_stats(latest, "main")),
        "recentStars": _numbers(analyzer.number_stats(latest, "star")),
        "mostDrawn": _numbers(most),
        "leastDrawn": _numbers(least),
        "mostDrawnStars": _numbers(most_stars),
        "leastDrawnStars": _numbers(least_stars),
        "hot": _numbers(report.hot_numbers),
        "cold": _numbers(report.cold_numbers),
        "overdue": _numbers(report.overdue_numbers),
        "hotStars": _numbers(report.hot_stars),
        "coldStars": _numbers(report.cold_stars),
        "overdueStars": _numbers(report.overdue_stars),
        "pairs": _ranking(report.top_pairs),
        "triplets": _ranking(report.top_triplets),
        "starPairs": _ranking(report.top_star_pairs),
        "consecutive": {
            "share": consecutive.share,
            "draws": consecutive.draws,
            "pairCounts": consecutive.pair_count_distribution,
            "longestRuns": consecutive.longest_run_distribution,
            "fairShare": analyzer.fair_consecutive_share(),
        },
        "sums": {**_distribution(report.sums), "fairMean": MAIN_COUNT * (MAIN_MAX + 1) / 2},
        "sumBands": _bands(report.sums, 10),
        "ranges": _distribution(report.ranges),
        "oddCounts": report.odd_counts,
        "lowCounts": report.low_counts,
        "lowMax": analyzer.LOW_MAX,
        "starPoolSince": _star_pool_since(),
        "fairSplit": analyzer.fair_split_shares(),
        "chance": {
            "extremeZ": {
                "main": analyzer.typical_extreme_z(len(report.number_stats)),
                "star": analyzer.typical_extreme_z(len(report.star_stats)),
            },
            "longestGap": {kind: analyzer.typical_longest_gap(kind) for kind in kinds},
        },
    }


def suggestion(
    draws: Sequence[Draw],
    *,
    strategy: str,
    lines: int,
    window: int,
    seed: int | None,
    rng: random.Random | None = None,
) -> JSON:
    """Suggest ``lines`` lines, and say which seed gives them again.

    Without a ``seed``, one is picked with ``rng`` and returned. Raises :class:`ValueError`
    for an invalid argument and :class:`~euromillions.errors.HistoryError` for an empty or
    too short history.
    """
    if not 1 <= lines <= MAX_LINES:
        raise ValueError(f"lines must be between 1 and {MAX_LINES}, got {lines}")
    if seed is None:
        seed = (rng or random.Random()).randrange(*_SEED_RANGE)
    history = chronological(draws)
    suggested = suggester.suggest(history, strategy, lines=lines, window=window, seed=seed)
    return {
        "strategy": strategy,
        "description": suggester.STRATEGIES.get(strategy, ""),
        "seed": seed,
        "window": window,
        "basedOn": {"draws": len(history), "last": history[-1].date.isoformat()},
        "lines": [
            {
                "numbers": list(line.numbers),
                "stars": list(line.stars),
                "sum": sum(line.numbers),
                "odd": analyzer.odd_count(line.numbers),
                "low": analyzer.low_count(line.numbers),
                "text": format_line(line.numbers, line.stars),
            }
            for line in suggested
        ],
    }


def _strategy(result: StrategyResult, report: Backtest, threshold: float) -> JSON:
    common = result.winnings - result.top_winnings
    return {
        "strategy": result.strategy,
        "lines": result.lines,
        "cost": result.cost,
        "winnings": result.winnings,
        "topWinnings": result.top_winnings,
        "commonWinnings": common,
        "topWins": result.top_wins,
        "net": result.net,
        "roi": result.roi,
        "winRate": result.win_rate,
        "winningLines": result.winning_lines,
        "winsByRank": result.wins_by_rank,
        "estimatedWins": result.estimated_wins,
        "mainMatches": result.main_matches,
        "starMatches": result.star_matches,
        "mainPerLine": result.main_matches_per_line,
        "starPerLine": result.star_matches_per_line,
        "mainZ": result.main_z,
        "starZ": result.star_z,
        "withinChance": abs(common - report.chance_winnings) <= 2 * report.chance_winnings_sd,
        "edge": max(abs(result.main_z), abs(result.star_z)) > threshold,
    }


def backtest_result(report: Backtest) -> JSON:
    """Every figure ``backtest`` prints, and those behind its reading notes."""
    first = report.results[0]
    z_scores = 2 * len(report.results)
    threshold = backtest.edge_threshold(z_scores)
    wins = [(result.best_win, result.strategy) for result in report.results if result.best_win]
    best = max(wins, key=lambda win: win[0].amount) if wins else None
    return {
        "trainDraws": report.train_draws,
        "trainStart": report.train_start.isoformat(),
        "trainEnd": report.train_end.isoformat(),
        "testDraws": report.test_draws,
        "testStart": report.test_start.isoformat(),
        "testEnd": report.test_end.isoformat(),
        "skippedDraws": report.skipped_draws,
        "linesPerDraw": report.lines_per_draw,
        "linePrice": report.line_price,
        "refresh": report.refresh,
        "lines": first.lines,
        "cost": first.cost,
        "results": [_strategy(result, report, threshold) for result in report.results],
        "chance": {
            "winnings": report.chance_winnings,
            "sd": report.chance_winnings_sd,
            "roi": report.chance_winnings / first.cost - 1,
            "roiSd": report.chance_winnings_sd / first.cost,
            "winRate": backtest.WIN_PROBABILITY,
            "winRateSd": backtest.win_rate_sd(first.lines),
            "mainPerLine": backtest.EXPECTED_MATCHES["main"],
            "starPerLine": backtest.EXPECTED_MATCHES["star"],
            "wins": backtest.expected_wins(first.lines),
        },
        "reading": {
            "zScores": z_scores,
            "zBeyondTwo": backtest.Z_BEYOND_TWO,
            "extremeZShare": backtest.chance_of_extreme_z(z_scores),
            "edgeThreshold": threshold,
            "topRanks": backtest.TOP_RANKS,
            "topRankOdds": backtest.TOP_RANK_ODDS,
            "commonRanks": list(backtest.COMMON_RANKS),
            "fewLines": first.lines < backtest.FEW_LINES,
            "fewLinesThreshold": backtest.FEW_LINES,
            "estimatedWins": sum(result.estimated_wins for result in report.results),
            "skippedDraws": report.skipped_draws,
            "rafflePrice": 0.3 if report.line_price == LINE_PRICE else None,
        },
        "bestWin": None
        if best is None
        else {
            "strategy": best[1],
            "date": best[0].date.isoformat(),
            "rank": best[0].rank,
            "amount": best[0].amount,
            "numbers": _RANK_MATCHES[best[0].rank][0],
            "stars": _RANK_MATCHES[best[0].rank][1],
        },
        "rankMatches": {rank: list(matches) for rank, matches in _RANK_MATCHES.items()},
        "mainCount": MAIN_COUNT,
        "mainMax": MAIN_MAX,
    }


def fetch_result(result: UpdateResult, *, now: dt.datetime | None = None) -> JSON:
    """What ``fetch`` reports, and the status of the updated history."""
    latest = schedule.previous_draw(now).date()
    last = result.draws[-1].date if result.draws else None
    return {
        "downloaded": result.downloaded,
        "added": result.added,
        "updated": result.updated,
        "unpublished": latest.isoformat() if last is None or last < latest else None,
        "status": status(result.draws, now=now),
    }
