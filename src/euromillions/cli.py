"""Command-line interface: ``euromillions fetch | analyze | suggest | next-draw``."""

from __future__ import annotations

import datetime as dt
import textwrap
from collections.abc import Iterable, Mapping
from pathlib import Path

import click

from euromillions import __version__, analyzer, fetcher, schedule, suggester
from euromillions.analyzer import AnalysisReport, Distribution, NumberStats
from euromillions.models import MAIN_COUNT, MAIN_MAX, STAR_COUNT, STAR_MAX, Draw

DISCLAIMER = (
    "Lottery draws are random: past results cannot predict future ones.\n"
    "For educational and entertainment purposes only."
)

_WIDTH = 80
_LABEL_WIDTH = 18
_UTC = dt.timezone.utc


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

    Run `fetch` first to download the draw history.
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
        raise _corrupt_cache(exc) from exc
    except fetcher.FetchError as exc:
        raise click.ClickException(str(exc)) from exc

    if not result.downloaded:
        click.echo("Already up to date.")
    elif result.added:
        click.echo(f"Added {result.added} new draw{'s' if result.added != 1 else ''}.")
    else:
        click.echo("No new draw has been published yet.")
    draws = result.draws
    click.echo(f"{len(draws)} draws in {cache_path}, from {draws[0].date} to {draws[-1].date}.")
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


@cli.command("next-draw")
def next_draw() -> None:
    """Show when the next draw takes place."""
    now = dt.datetime.now(tz=schedule.DRAW_TIMEZONE)
    upcoming = schedule.next_draw(now)
    click.echo(
        f"Next draw: {upcoming:%A} {upcoming.day} {upcoming:%B %Y} at {upcoming:%H:%M} "
        f"Paris time ({_format_countdown(upcoming, now)})."
    )


def _corrupt_cache(error: fetcher.CacheError) -> click.ClickException:
    return click.ClickException(f"{error}\nDelete that file and run `euromillions fetch` again.")


def _load_draws(cache_path: Path) -> list[Draw]:
    """Read the cached history, turning problems into user-facing errors."""
    try:
        draws = fetcher.load_draws(cache_path)
    except fetcher.CacheError as exc:
        raise _corrupt_cache(exc) from exc
    if not draws:
        raise click.ClickException(
            f"no draw history in {cache_path}; run `euromillions fetch` first."
        )
    if draws[-1].date < schedule.previous_draw().date():
        click.echo(
            f"Note: the history ends on {draws[-1].date}; run `euromillions fetch` to update it.",
            err=True,
        )
    return draws


def _describe_draw(draw: Draw) -> str:
    text = str(draw)
    if draw.jackpot_winners is None:
        return text
    if draw.jackpot_winners == 0:
        return f"{text} (jackpot not won)"
    winners = f"{draw.jackpot_winners} winner{'s' if draw.jackpot_winners != 1 else ''}"
    if not draw.jackpot_prize:
        return f"{text} (jackpot won, {winners})"
    return f"{text} (jackpot won, {winners}, EUR {draw.jackpot_prize:,.0f} each)"


def _format_countdown(moment: dt.datetime, now: dt.datetime) -> str:
    # Subtract in UTC: two datetimes sharing a tzinfo are subtracted as wall-clock
    # times, which is off by an hour across a daylight-saving change.
    remaining = moment.astimezone(_UTC) - now.astimezone(_UTC)
    total_minutes = max(int(remaining.total_seconds() // 60), 0)
    days, rest = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    parts = [(days, "day"), (hours, "hour"), (minutes, "minute")]
    shown = [f"{value} {unit}{'s' if value != 1 else ''}" for value, unit in parts if value]
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


def _render_number_rankings(
    title: str,
    stats: Mapping[int, NumberStats],
    hot: Iterable[NumberStats],
    cold: Iterable[NumberStats],
    overdue: Iterable[NumberStats],
    *,
    window: int,
    top: int,
) -> list[str]:
    drawn = [s for s in stats.values() if s.eligible_draws]
    by_frequency = sorted(drawn, key=lambda s: (-s.frequency, s.number))
    least_frequent = sorted(drawn, key=lambda s: (s.frequency, s.number))
    return [
        title,
        _ranking(
            "Most frequent", (f"{s.number} ({_percent(s.frequency)})" for s in by_frequency[:top])
        ),
        _ranking(
            "Least frequent",
            (f"{s.number} ({_percent(s.frequency)})" for s in least_frequent[:top]),
        ),
        _ranking(f"Hot, last {window}", (f"{s.number} ({s.count}x)" for s in hot)),
        _ranking(f"Cold, last {window}", (f"{s.number} ({s.count}x)" for s in cold)),
        _ranking("Most overdue", (f"{s.number} ({s.current_gap} draws)" for s in overdue)),
    ]


def _render_distribution(label: str, distribution: Distribution) -> str:
    return _ranking(
        label,
        [
            f"mean {distribution.mean:.1f}",
            f"median {distribution.median:g}",
            f"min {distribution.minimum}",
            f"max {distribution.maximum}",
            f"80% of draws between {distribution.quantile(0.1)} and {distribution.quantile(0.9)}",
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
        f"EuroMillions analysis: {report.total_draws} draws "
        f"from {report.first_date} to {report.last_date}",
        "",
    ]
    lines += _render_number_rankings(
        f"MAIN NUMBERS ({MAIN_COUNT} drawn from 1-{MAIN_MAX})",
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
        report.star_stats,
        report.hot_stars,
        report.cold_stars,
        report.overdue_stars,
        window=report.window,
        top=top,
    )
    lines += [
        "",
        "COMBINATIONS DRAWN TOGETHER",
        _ranking("Top pairs", (f"{a}-{b} ({n}x)" for (a, b), n in report.top_pairs)),
        _ranking("Top triplets", (f"{a}-{b}-{c} ({n}x)" for (a, b, c), n in report.top_triplets)),
        _ranking("Top star pairs", (f"{a}-{b} ({n}x)" for (a, b), n in report.top_star_pairs)),
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
