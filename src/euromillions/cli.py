"""Command-line interface: ``euromillions fetch | analyze | suggest | backtest | next-draw``."""

from __future__ import annotations

import datetime as dt
import math
import statistics
import textwrap
from collections.abc import Iterable, Mapping
from pathlib import Path

import click

from euromillions import __version__, analyzer, backtest, fetcher, schedule, suggester
from euromillions.analyzer import AnalysisReport, Distribution, NumberStats, Ranking
from euromillions.backtest import Backtest
from euromillions.models import (
    LINE_PRICE,
    MAIN_COUNT,
    MAIN_MAX,
    PRIZE_RANKS,
    STAR_COUNT,
    STAR_MAX,
    BallKind,
    Draw,
)

DISCLAIMER = (
    "Lottery draws are random: past results cannot predict future ones.\n"
    "For educational and entertainment purposes only."
)

_WIDTH = 80
_LABEL_WIDTH = 18
_UTC = dt.timezone.utc
#: Below this many lines per strategy, a backtest says very little.
_FEW_LINES = 1000
#: (main numbers, Lucky Stars) to match for each prize rank.
_RANK_MATCHES = {rank: matches for matches, rank in PRIZE_RANKS.items()}


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="euromillions")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=fetcher.DEFAULT_DATA_DIR,
    envvar="EUROMILLIONS_DATA_DIR",
    show_default=True,
    help="Directory holding the cached draw history.",
)
@click.pass_context
def cli(ctx: click.Context, data_dir: Path) -> None:
    """Analyse EuroMillions draw history and suggest numbers.

    Run `fetch` first to download the draw history. The options below go before
    the command: `euromillions --data-dir DIR analyze`.
    """
    ctx.obj = data_dir / fetcher.CACHE_FILENAME


@cli.command()
@click.option("--force", is_flag=True, help="Download even if the cache looks up to date.")
@click.pass_obj
def fetch(cache_path: Path, force: bool) -> None:
    """Download new draw results into the local cache."""
    try:
        result = fetcher.update_cache(cache_path, force=force)
    except fetcher.CacheError as exc:
        raise _cache_problem(exc) from exc
    except fetcher.FetchError as exc:
        raise click.ClickException(str(exc)) from exc

    draws = result.draws
    if not result.downloaded:
        click.echo("Already up to date.")
    else:
        changes = [
            f"added {_count(result.added, 'new draw')}" if result.added else "",
            f"updated {_count(result.updated, 'draw')}" if result.updated else "",
        ]
        summary = ", ".join(filter(None, changes)) or "nothing new at the source"
        click.echo(f"{summary[0].upper()}{summary[1:]}.")
        latest = schedule.previous_draw().date()
        if draws[-1].date < latest:
            click.echo(f"The draw of {latest} has not been published yet.")
    click.echo(
        f"{_count(len(draws), 'draw')} in {cache_path}, from {draws[0].date} to {draws[-1].date}."
    )
    click.echo(f"Latest draw: {_describe_draw(draws[-1])}")


@cli.command()
@click.option("--last", type=click.IntRange(min=1), help="Only analyse the N most recent draws.")
@click.option(
    "--window",
    type=click.IntRange(min=1),
    default=50,
    show_default=True,
    help="Number of recent draws used to rank hot and cold numbers.",
)
@click.option(
    "--top", type=click.IntRange(min=1), default=10, show_default=True, help="Entries per ranking."
)
@click.option("--full", is_flag=True, help="Also print the statistics of every number and star.")
@click.pass_obj
def analyze(cache_path: Path, last: int | None, window: int, top: int, full: bool) -> None:
    """Show statistics about past draws."""
    draws = analyzer.recent(_load_draws(cache_path), last)
    report = analyzer.analyze(draws, window=window, top=top)
    click.echo("\n".join(_render_report(report, top)))
    if full:
        click.echo()
        click.echo("\n".join(_render_number_table("MAIN NUMBERS", report.number_stats.values())))
        click.echo()
        click.echo("\n".join(_render_number_table("LUCKY STARS", report.star_stats.values())))
    click.echo()
    click.echo(DISCLAIMER)


_SUGGEST_HELP = "Suggest lines for the next draw.\n\n\b\nStrategies:\n" + "\n".join(
    f"  {name:<9} {description}" for name, description in suggester.STRATEGIES.items()
)


@cli.command(help=_SUGGEST_HELP)
@click.option(
    "--strategy",
    type=click.Choice(list(suggester.STRATEGIES)),
    default="balanced",
    show_default=True,
    help="How numbers are chosen.",
)
@click.option(
    "--lines", type=click.IntRange(1, 50), default=5, show_default=True, help="Lines to suggest."
)
@click.option(
    "--window",
    type=click.IntRange(min=1),
    default=50,
    show_default=True,
    help="Number of recent draws that define hot numbers.",
)
@click.option("--seed", type=int, help="Seed for reproducible suggestions.")
@click.pass_obj
def suggest(cache_path: Path, strategy: str, lines: int, window: int, seed: int | None) -> None:
    draws = _load_draws(cache_path)
    try:
        suggestions = suggester.suggest(draws, strategy, lines=lines, window=window, seed=seed)
    except (ValueError, RuntimeError) as exc:
        # E.g. a history too short for the strategy.
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Strategy: {strategy} - {suggester.STRATEGIES[strategy]}")
    click.echo(f"Based on {len(draws)} draws up to {draws[-1].date}.")
    click.echo()
    for index, suggestion in enumerate(suggestions, start=1):
        click.echo(f"  {index:>2}.  {suggestion}")
    click.echo()
    click.echo(DISCLAIMER)


@cli.command("backtest")
@click.option(
    "--strategy",
    "strategies",
    multiple=True,
    type=click.Choice(list(backtest.DEFAULT_STRATEGIES)),
    help="Strategy to evaluate; repeat the option to compare several.  "
    "[default: all of them and the random baseline]",
)
@click.option(
    "--train-fraction",
    type=click.FloatRange(0, 1, min_open=True, max_open=True),
    default=0.8,
    show_default=True,
    help="Share of the history, oldest draws first, that only serves to build statistics.",
)
@click.option(
    "--lines",
    type=click.IntRange(1, 50),
    default=5,
    show_default=True,
    help="Lines played on each draw.",
)
@click.option(
    "--window",
    type=click.IntRange(min=1),
    default=50,
    show_default=True,
    help="Number of recent draws that define hot numbers.",
)
@click.option("--seed", type=int, help="Seed for a reproducible backtest.")
@click.option(
    "--static",
    is_flag=True,
    help="Suggest the lines once, from the training draws, and replay them on every draw.",
)
@click.option(
    "--price",
    type=click.FloatRange(min=0, min_open=True),
    default=LINE_PRICE,
    show_default=True,
    help="Price of one line, in euros.",
)
@click.pass_obj
def backtest_command(
    cache_path: Path,
    strategies: tuple[str, ...],
    train_fraction: float,
    lines: int,
    window: int,
    seed: int | None,
    static: bool,
    price: float,
) -> None:
    """Measure what the strategies would have won on draws they had not seen.

    The oldest draws only serve to build statistics. Each remaining draw is then
    played with lines suggested from the draws that precede it, and scored with
    the prizes really paid that day.
    """
    draws = _load_draws(cache_path)
    try:
        report = backtest.backtest(
            draws,
            strategies or backtest.DEFAULT_STRATEGIES,
            train_fraction=train_fraction,
            lines=lines,
            window=window,
            seed=seed,
            refresh=not static,
            line_price=price,
        )
    except (ValueError, RuntimeError) as exc:
        # E.g. an evaluation period that starts before the current rules.
        raise click.ClickException(str(exc)) from exc
    click.echo("\n".join(_render_backtest(report)))
    click.echo()
    click.echo(DISCLAIMER)


@cli.command("next-draw")
def next_draw() -> None:
    """Show when the next draw takes place."""
    now = dt.datetime.now(tz=schedule.DRAW_TIMEZONE)
    upcoming = schedule.next_draw(now)
    click.echo(
        f"Next draw: {upcoming:%A} {upcoming.day} {upcoming:%B %Y} at {upcoming:%H:%M} "
        f"Paris time ({_format_countdown(upcoming, now)})."
    )


def _cache_problem(error: fetcher.CacheError) -> click.ClickException:
    """Turn a cache error into a user-facing one, with the way out when there is one."""
    if isinstance(error, fetcher.CorruptCacheError):
        return click.ClickException(
            f"{error}\nDelete that file and run `euromillions fetch` again."
        )
    return click.ClickException(str(error))


def _load_draws(cache_path: Path) -> list[Draw]:
    """Read the cached history, turning problems into user-facing errors."""
    try:
        draws = fetcher.load_draws(cache_path)
    except fetcher.CacheError as exc:
        raise _cache_problem(exc) from exc
    if not draws:
        raise click.ClickException(
            f"no draw history in {cache_path.resolve()}: run `euromillions fetch` first, "
            "or give the directory that holds it with --data-dir."
        )
    if draws[-1].date < schedule.previous_draw().date():
        click.echo(
            f"Note: the history ends on {draws[-1].date}; run `euromillions fetch` to update it.",
            err=True,
        )
    return draws


def _count(number: int, noun: str) -> str:
    """Return e.g. ``"1 draw"`` or ``"3 draws"``."""
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _describe_draw(draw: Draw) -> str:
    """Describe a draw and, when its prizes are known, what became of the jackpot."""
    jackpot = draw.jackpot
    if jackpot is None:
        return str(draw)
    if not jackpot.prize:
        return f"{draw} (jackpot not won)"
    winners = _count(jackpot.winners, "winner")
    return f"{draw} (jackpot won: {winners}, EUR {jackpot.prize:,.0f} each)"


def _format_countdown(moment: dt.datetime, now: dt.datetime) -> str:
    # Subtract in UTC: two datetimes sharing a tzinfo are subtracted as wall-clock
    # times, which is off by an hour across a daylight-saving change.
    remaining = moment.astimezone(_UTC) - now.astimezone(_UTC)
    total_minutes = max(int(remaining.total_seconds() // 60), 0)
    days, rest = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    parts = [(days, "day"), (hours, "hour"), (minutes, "minute")]
    shown = [_count(value, unit) for value, unit in parts if value]
    return "in " + " ".join(shown[:2]) if shown else "now"


def _ranking(label: str, entries: Iterable[str]) -> str:
    """Lay out a labelled list of entries, wrapping between entries only."""
    # NUL stands in for the spaces inside an entry so that textwrap cannot break there.
    body = "  ".join(entry.replace(" ", "\0") for entry in entries) or "-"
    wrapped = textwrap.fill(
        body,
        width=_WIDTH,
        initial_indent=f"  {label:<{_LABEL_WIDTH}}",
        subsequent_indent=" " * (_LABEL_WIDTH + 2),
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped.replace("\0", " ")


def _percent(share: float) -> str:
    return f"{share * 100:.1f}%"


def _note(text: str, indent: str = "") -> str:
    """Wrap a remark to the output width."""
    return textwrap.fill(text, width=_WIDTH, initial_indent=indent, subsequent_indent=indent)


def _render_number_rankings(
    title: str,
    kind: BallKind,
    stats: Mapping[int, NumberStats],
    hot: Iterable[NumberStats],
    cold: Iterable[NumberStats],
    overdue: Iterable[NumberStats],
    *,
    window: int,
    top: int,
) -> list[str]:
    # Ranked by z-score, i.e. against what each number could be expected to do: raw counts
    # would be unfair to the stars that joined the pool later. Half of the pool at most, so
    # that no number is listed as both most and least drawn.
    drawn = [s for s in stats.values() if s.eligible_draws]
    most_drawn = sorted(drawn, key=lambda s: (-s.z_score, s.number))
    least_drawn = most_drawn[::-1]
    shown = min(top, len(drawn) // 2)

    def entries(ranking: Iterable[NumberStats]) -> Iterable[str]:
        return (f"{s.number} ({s.count}x, {s.z_score:+.1f})" for s in ranking)

    lines = [
        title,
        _ranking("Most drawn", entries(most_drawn[:shown])),
        _ranking("Least drawn", entries(least_drawn[:shown])),
        _ranking(f"Hot, last {window}", entries(hot)),
        _ranking(f"Cold, last {window}", entries(cold)),
        _ranking(
            "Most overdue", (f"{s.number} ({_count(s.current_gap, 'draw')})" for s in overdue)
        ),
        _note(
            "In brackets: times drawn, then z-score (distance to the expected count, in "
            f"standard deviations). Fair draws typically put the most extreme of {len(stats)} "
            f"z-scores near +/-{analyzer.typical_extreme_z(len(stats)):.1f} and the longest "
            f"current absence near {analyzer.typical_longest_gap(kind)} draws.",
            indent="  ",
        ),
    ]
    if len({s.eligible_draws for s in drawn}) > 1:
        lines.append(
            _note(
                "Not all of them were in the pool for the whole period: each one is compared "
                "with the number of times it could be expected to come up.",
                indent="  ",
            )
        )
    return lines


def _render_combinations(label: str, ranking: Ranking) -> str:
    entries = [f"{'-'.join(map(str, combo))} ({count}x)" for combo, count in ranking.entries]
    if ranking.more:
        entries.append(f"+{ranking.more} more at {ranking.entries[-1][1]}x")
    return _ranking(label, entries)


def _render_distribution(label: str, distribution: Distribution) -> str:
    low, high = distribution.quantile(0.1), distribution.quantile(0.9)
    return _ranking(
        label,
        [
            f"mean {distribution.mean:.1f}",
            f"median {distribution.median:g}",
            f"min {distribution.minimum}",
            f"max {distribution.maximum}",
            f"{distribution.share_between(low, high):.0%} of draws between {low} and {high}",
        ],
    )


def _render_split(label: str, counts: Mapping[int, int], total: int) -> str:
    """Render e.g. the odd/even split as ``3/2 (32.1%)`` entries, most common first."""
    ordered = sorted(counts.items(), key=lambda item: (-item[1], -item[0]))
    return _ranking(
        label,
        (
            f"{value}/{MAIN_COUNT - value} ({_percent(draws / total)})"
            for value, draws in ordered
            if draws
        ),
    )


def _render_report(report: AnalysisReport, top: int) -> list[str]:
    lines = [
        f"EuroMillions analysis: {_count(report.total_draws, 'draw')} "
        f"from {report.first_date} to {report.last_date}",
        "",
    ]
    lines += _render_number_rankings(
        f"MAIN NUMBERS ({MAIN_COUNT} drawn from 1-{MAIN_MAX})",
        "main",
        report.number_stats,
        report.hot_numbers,
        report.cold_numbers,
        report.overdue_numbers,
        window=report.window,
        top=top,
    )
    lines.append("")
    lines += _render_number_rankings(
        f"LUCKY STARS ({STAR_COUNT} drawn from 1-{STAR_MAX})",
        "star",
        report.star_stats,
        report.hot_stars,
        report.cold_stars,
        report.overdue_stars,
        window=report.window,
        top=top,
    )
    pairs, triplets, star_pairs = report.top_pairs, report.top_triplets, report.top_star_pairs
    lines += [
        "",
        "COMBINATIONS DRAWN TOGETHER",
        _render_combinations("Top pairs", pairs),
        _render_combinations("Top triplets", triplets),
        _render_combinations("Top star pairs", star_pairs),
        _note(
            f"By chance a given pair is expected {pairs.expected:.1f} times and the luckiest "
            f"one about {pairs.typical_top}; a triplet {triplets.expected:.1f} and "
            f"{triplets.typical_top}; a star pair {star_pairs.expected:.1f} and "
            f"{star_pairs.typical_top} (star pairs: draws with {STAR_MAX} stars only).",
            indent="  ",
        ),
        "",
        "PATTERNS OF THE MAIN NUMBERS",
        _render_distribution("Sum", report.sums),
        _render_distribution("Range (max-min)", report.ranges),
        _ranking(
            "Consecutive",
            [f"{_percent(report.consecutive.share)} of draws contain consecutive numbers"],
        ),
        _render_split("Odd/even", report.odd_counts, report.total_draws),
        _render_split(f"Low/high (1-{analyzer.LOW_MAX})", report.low_counts, report.total_draws),
    ]
    return lines


def _render_number_table(title: str, stats: Iterable[NumberStats]) -> list[str]:
    """Render one row per number; gaps are counted in draws."""
    lines = [
        title,
        "   No  Drawn  Freq %  Expected       z  Avg gap  Gap now  Max gap  Last seen",
    ]
    for s in stats:
        average_gap = "-" if s.average_gap is None else f"{s.average_gap:.1f}"
        last_seen = "-" if s.last_seen is None else s.last_seen.isoformat()
        lines.append(
            f"  {s.number:>3}  {s.count:>5}  {s.frequency * 100:>6.2f}  {s.expected:>8.1f}  "
            f"{s.z_score:>+6.2f}  {average_gap:>7}  {s.current_gap:>7}  {s.longest_gap:>7}  "
            f"{last_seen}"
        )
    return lines


def _render_backtest(report: Backtest) -> list[str]:
    source = (
        f"the {_count(report.train_draws, 'draw')} from {report.train_start} to {report.train_end}"
    )
    method = (
        f"suggested from all the draws before it: {source} at first, then every draw since"
        if report.refresh
        else f"suggested once from {source} and replayed unchanged"
    )
    played, cost = report.results[0].lines, report.results[0].cost
    lines = [
        _note(
            f"Backtest on {_count(report.test_draws, 'draw')} from {report.test_start} to "
            f"{report.test_end}. Each strategy plays {_count(report.lines_per_draw, 'line')} "
            f"per draw, {method}. That is {_count(played, 'line')} for "
            f"EUR {cost:,.2f} per strategy."
        ),
        "",
        _backtest_row("Strategy", "Winnings", "Net", "ROI", "Wins", "Numbers", "Stars"),
    ]
    for result in report.results:
        lines.append(
            _backtest_row(
                result.strategy,
                f"{result.winnings:,.2f}",
                f"{result.net:,.2f}",
                f"{result.roi:.1%}",
                f"{result.win_rate:.1%}",
                f"{result.main_matches_per_line:.3f} ({result.main_z:+.1f})",
                f"{result.star_matches_per_line:.3f} ({result.star_z:+.1f})",
            )
        )
    win = backtest.WIN_PROBABILITY
    lines += [
        _backtest_row(
            "by chance",
            f"{report.chance_winnings:,.2f}",
            f"{report.chance_winnings - cost:,.2f}",
            f"{report.chance_winnings / cost - 1:.1%}",
            f"{win:.1%}",
            f"{backtest.EXPECTED_MATCHES['main']:.3f}",
            f"{backtest.EXPECTED_MATCHES['star']:.3f}",
        ),
        _backtest_row(
            "+/- 1 SD",
            f"{report.chance_winnings_sd:,.2f}",
            f"{report.chance_winnings_sd:,.2f}",
            f"{report.chance_winnings_sd / cost:.1%}",
            f"{math.sqrt(win * (1 - win) / played):.1%}",
            "",
            "",
        ).rstrip(),
        "",
    ]

    z_scores = 2 * len(report.results)
    normal = statistics.NormalDist()
    beyond_two = 2 * (1 - normal.cdf(2))
    notes = [
        "Amounts are in euros. ROI is the net result over the cost; Wins is the share of "
        "lines that won a prize.",
        "By chance: what uniformly random lines are expected to win on these draws from "
        f"prize ranks {backtest.COMMON_RANKS[0]} to {backtest.COMMON_RANKS[-1]}, and by how "
        "much that varies (one standard deviation) for this many lines. A strategy within "
        "about two standard deviations of it, the random one included, has done nothing "
        "that chance does not do.",
        "Numbers and Stars: average matches per line, with the z-score against pure chance "
        f"in brackets. Chance alone puts a z-score beyond +/-2 about once in "
        f"{1 / beyond_two:.0f}: with {z_scores} of them here, at least one in about "
        f"{1 - (1 - beyond_two) ** z_scores:.0%} of backtests. Only a value beyond about "
        f"+/-{normal.inv_cdf(1 - 0.05 / (2 * z_scores)):.1f} that holds with other seeds "
        "and periods would point to a real edge, and a small one could go unnoticed.",
        f"The {backtest.TOP_RANKS} top ranks, jackpot included, hold about half of the "
        "prize money but are far too rare to show up in a backtest."
        + (
            " The cost includes EUR 0.30 per line for the My Million raffle, which is not "
            "simulated; by design, the draw returns half of the other EUR 2.20 in prizes "
            "in the long run, top ranks included."
            if report.line_price == LINE_PRICE
            else ""
        ),
    ]
    if played < _FEW_LINES:
        notes.append(
            f"With fewer than {_FEW_LINES} lines per strategy, luck dominates these figures."
        )
    estimated = sum(result.estimated_wins for result in report.results)
    if estimated:
        notes.append(
            f"{_count(estimated, 'win')} valued by estimate: the rank had no winner that day, "
            "or their number is unknown."
        )
    if report.skipped_draws:
        notes.append(
            f"{_count(report.skipped_draws, 'draw')} without prize data could not be played."
        )
    wins = [(result.best_win, result.strategy) for result in report.results if result.best_win]
    if wins:
        best, strategy = max(wins, key=lambda win: win[0].amount)
        main, stars = _RANK_MATCHES[best.rank]
        notes.append(
            f"Biggest win: EUR {best.amount:,.2f} by {strategy} on {best.date} "
            f"({_count(main, 'number')} + {_count(stars, 'star')})."
        )
    lines += [
        textwrap.fill(note, _WIDTH, initial_indent="- ", subsequent_indent="  ") for note in notes
    ]
    return lines


def _backtest_row(
    name: str, winnings: str, net: str, roi: str, wins: str, numbers: str, stars: str
) -> str:
    """Lay out one row of the backtest table; a cell wider than its column pushes the rest."""
    return f"{name:<9} {winnings:>11} {net:>12} {roi:>8} {wins:>6}  {numbers:<13} {stars}"
