"""Tests for the command-line interface."""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from euromillions import __version__, fetcher, schedule
from euromillions.cli import DISCLAIMER, _describe_draw, _format_countdown, _ranking, cli
from euromillions.models import Draw, PrizeTier

from .conftest import TYPICAL_PRIZES, HistoryFactory

LINE_PATTERN = re.compile(r"^ +\d+\.  (\d\d ){5}\| stars \d\d \d\d$", re.MULTILINE)
PARIS = schedule.DRAW_TIMEZONE


@pytest.fixture
def data_dir(tmp_path: Path, history: list[Draw]) -> Path:
    """A data directory whose cache holds 300 random draws."""
    fetcher.save_draws(history, tmp_path / fetcher.CACHE_FILENAME)
    return tmp_path


def run(*args: str, data_dir: Path | None = None) -> Result:
    prefix = [] if data_dir is None else ["--data-dir", str(data_dir)]
    return CliRunner().invoke(cli, [*prefix, *args])


class TestGeneral:
    def test_help_lists_the_commands(self) -> None:
        result = run("--help")
        assert result.exit_code == 0
        for command in ("fetch", "analyze", "suggest", "backtest", "next-draw"):
            assert command in result.output

    def test_version(self) -> None:
        result = run("--version")
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_python_dash_m_entry_point(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "euromillions", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0
        assert "Usage: euromillions" in completed.stdout

    def test_data_dir_can_come_from_the_environment(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EUROMILLIONS_DATA_DIR", str(data_dir))
        assert run("suggest", "--lines", "1").exit_code == 0

    @pytest.mark.parametrize("command", ["analyze", "suggest", "backtest"])
    def test_missing_cache_tells_the_user_to_fetch(self, command: str, tmp_path: Path) -> None:
        result = run(command, data_dir=tmp_path)
        assert result.exit_code == 1
        assert "euromillions fetch" in result.output

    @pytest.mark.parametrize("command", ["analyze", "suggest", "backtest"])
    def test_corrupt_cache_is_reported_cleanly(self, command: str, tmp_path: Path) -> None:
        (tmp_path / fetcher.CACHE_FILENAME).write_text("{not json", encoding="utf-8")
        result = run(command, data_dir=tmp_path)
        assert result.exit_code == 1
        assert result.output.startswith("Error:")
        assert "Traceback" not in result.output

    def test_stale_cache_triggers_a_note(self, data_dir: Path, history: list[Draw]) -> None:
        result = run("suggest", "--lines", "1", data_dir=data_dir)
        assert result.exit_code == 0
        assert f"Note: the history ends on {history[-1].date}" in result.output

    def test_fresh_cache_does_not_trigger_a_note(
        self, data_dir: Path, history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        latest = schedule.draw_datetime(history[-1].date)
        monkeypatch.setattr(schedule, "previous_draw", lambda now=None: latest)
        result = run("suggest", "--lines", "1", data_dir=data_dir)
        assert result.exit_code == 0
        assert "Note:" not in result.output


class TestFetch:
    @pytest.fixture
    def update(self, monkeypatch: pytest.MonkeyPatch, history: list[Draw]) -> dict[str, object]:
        """Replace the network update by a stub; returns the dict that configures it."""
        config: dict[str, object] = {
            "added": 0,
            "updated": 0,
            "downloaded": True,
            "error": None,
            "calls": [],
        }

        def fake_update_cache(path: Path, *, force: bool = False) -> fetcher.UpdateResult:
            assert isinstance(config["calls"], list)
            config["calls"].append((path, force))
            if isinstance(config["error"], Exception):
                raise config["error"]
            assert isinstance(config["added"], int)
            assert isinstance(config["updated"], int)
            return fetcher.UpdateResult(
                history, config["added"], config["updated"], bool(config["downloaded"])
            )

        monkeypatch.setattr(fetcher, "update_cache", fake_update_cache)
        return config

    def test_reports_new_draws(
        self, update: dict[str, object], tmp_path: Path, history: list[Draw]
    ) -> None:
        update["added"] = 3
        result = run("fetch", data_dir=tmp_path)
        assert result.exit_code == 0
        assert "Added 3 new draws." in result.output
        assert f"300 draws in {tmp_path / fetcher.CACHE_FILENAME}" in result.output
        assert f"from {history[0].date} to {history[-1].date}" in result.output
        assert f"Latest draw: {history[-1]}" in result.output
        assert update["calls"] == [(tmp_path / fetcher.CACHE_FILENAME, False)]

    def test_singular(self, update: dict[str, object], tmp_path: Path) -> None:
        update["added"] = 1
        assert "Added 1 new draw." in run("fetch", data_dir=tmp_path).output

    def test_updated_draws(self, update: dict[str, object], tmp_path: Path) -> None:
        # E.g. the prizes of the latest draw, published after its numbers.
        update["updated"] = 1
        assert "Updated 1 draw." in run("fetch", data_dir=tmp_path).output
        update["added"] = 2
        assert "Added 2 new draws, updated 1 draw." in run("fetch", data_dir=tmp_path).output

    def test_nothing_new(self, update: dict[str, object], tmp_path: Path) -> None:
        output = run("fetch", data_dir=tmp_path).output
        assert "Nothing new at the source." in output
        # The history of the stub ends in 2019: the latest draw is missing from it.
        assert re.search(
            r"^The draw of \d{4}-\d\d-\d\d has not been published yet\.$", output, re.M
        )

    def test_latest_draw_already_there(
        self,
        update: dict[str, object],
        tmp_path: Path,
        history: list[Draw],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        latest = schedule.draw_datetime(history[-1].date)
        monkeypatch.setattr(schedule, "previous_draw", lambda now=None: latest)
        output = run("fetch", "--force", data_dir=tmp_path).output
        assert "Nothing new at the source." in output
        assert "has not been published yet" not in output

    def test_already_up_to_date(self, update: dict[str, object], tmp_path: Path) -> None:
        update["downloaded"] = False
        output = run("fetch", data_dir=tmp_path).output
        assert "Already up to date." in output
        assert "has not been published yet" not in output

    def test_force_is_passed_on(self, update: dict[str, object], tmp_path: Path) -> None:
        assert run("fetch", "--force", data_dir=tmp_path).exit_code == 0
        assert update["calls"] == [(tmp_path / fetcher.CACHE_FILENAME, True)]

    @pytest.mark.parametrize(
        ("error", "hint"),
        [
            (fetcher.FetchError("download failed"), False),
            (fetcher.CacheError("cannot write the cache"), False),
            (fetcher.CorruptCacheError("the cache is not valid JSON"), True),
        ],
    )
    def test_errors_are_reported_cleanly(
        self, error: Exception, hint: bool, update: dict[str, object], tmp_path: Path
    ) -> None:
        update["error"] = error
        result = run("fetch", data_dir=tmp_path)
        assert result.exit_code == 1
        assert f"Error: {error}" in result.output
        assert "Traceback" not in result.output
        # Deleting the file only helps when its content is the problem.
        assert ("Delete that file" in result.output) is hint

    def test_a_cache_that_cannot_be_written_is_reported_cleanly(
        self, tmp_path: Path, history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def disk_full(source: object, target: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(fetcher, "fetch_draws", lambda session=None, now=None: history)
        monkeypatch.setattr(fetcher.os, "replace", disk_full)
        result = run("fetch", data_dir=tmp_path)
        assert result.exit_code == 1
        assert "Error: cannot write the draw cache" in result.output
        assert "disk full" in result.output
        assert "Delete that file" not in result.output
        assert "Traceback" not in result.output
        assert list(tmp_path.iterdir()) == []


class TestAnalyze:
    def test_prints_every_section(self, data_dir: Path, history: list[Draw]) -> None:
        result = run("analyze", data_dir=data_dir)
        assert result.exit_code == 0
        assert (
            f"EuroMillions analysis: 300 draws from {history[0].date} to {history[-1].date}"
            in result.output
        )
        for expected in (
            "MAIN NUMBERS (5 drawn from 1-50)",
            "LUCKY STARS (2 drawn from 1-12)",
            "Most drawn",
            "Least drawn",
            "Hot, last 50",
            "Cold, last 50",
            "Most overdue",
            "Top pairs",
            "Top triplets",
            "Top star pairs",
            "Sum",
            "Range (max-min)",
            "Consecutive",
            "Odd/even",
            "Low/high (1-25)",
            DISCLAIMER,
        ):
            assert expected in result.output
        assert "Gap now" not in result.output  # the per-number table needs --full
        assert "Not all of them were in the pool" not in result.output

    def test_no_number_is_both_most_and_least_drawn(self, data_dir: Path) -> None:
        result = run("analyze", "--top", "40", data_dir=data_dir)
        stars = result.output.split("LUCKY STARS")[1].split("COMBINATIONS")[0]
        most = re.findall(r"(\d+) \(\d+x", stars.split("Most drawn")[1].split("Least drawn")[0])
        least = re.findall(r"(\d+) \(\d+x", stars.split("Least drawn")[1].split("Hot, last")[0])
        assert len(most) == len(least) == 6
        assert not set(most) & set(least)

    def test_extremes_come_with_what_chance_alone_produces(self, data_dir: Path) -> None:
        output = " ".join(run("analyze", data_dir=data_dir).output.split())
        # Every number is listed with its count and z-score...
        assert re.search(r"Most drawn \d+ \(\d+x, \+\d\.\d\)", output)
        assert re.search(r"Least drawn \d+ \(\d+x, -\d\.\d\)", output)
        # ... and each block says how far fair draws go.
        assert "the most extreme of 50 z-scores near +/-2.5" in output
        assert "the longest current absence near 40 draws" in output
        assert "the most extreme of 12 z-scores near +/-1.9" in output
        assert "the longest current absence near 15 draws" in output
        assert re.search(
            r"By chance a given pair is expected 2\.4 times and the luckiest one about \d+; "
            r"a triplet 0\.2 and \d; a star pair 4\.5 and \d+ ",
            output,
        )

    def test_rankings_cut_inside_a_tie_say_so(self, data_dir: Path) -> None:
        # More triplets came up as often as the last one listed than the ranking can show.
        output = run("analyze", data_dir=data_dir).output
        triplets = output.split("Top triplets")[1].split("Top star pairs")[0]
        assert re.search(r"\((\d+)x\)  \+\d+ more at \1x\n", triplets)

    def test_spread_is_given_with_its_real_share_of_the_draws(self, data_dir: Path) -> None:
        output = run("analyze", data_dir=data_dir).output
        shares = [int(share) for share in re.findall(r"(\d+)% of draws between", output)]
        assert len(shares) == 2
        assert all(80 <= share <= 90 for share in shares)

    def test_a_single_draw(self, data_dir: Path) -> None:
        result = run("analyze", "--last", "1", data_dir=data_dir)
        assert result.exit_code == 0
        assert "EuroMillions analysis: 1 draw from" in result.output

    def test_stars_that_joined_later_are_explained(
        self, tmp_path: Path, make_history: HistoryFactory
    ) -> None:
        # 100 draws from March 2016 straddle the arrival of the twelfth star.
        history = make_history(100, start=dt.date(2016, 3, 1))
        fetcher.save_draws(history, tmp_path / fetcher.CACHE_FILENAME)
        result = run("analyze", data_dir=tmp_path)
        assert result.exit_code == 0
        assert result.output.count("Not all of them were in the pool") == 1

    @pytest.mark.parametrize("arguments", [("--top", "15"), ("--full",), ("--last", "3")])
    def test_output_fits_in_80_columns(self, arguments: tuple[str, ...], data_dir: Path) -> None:
        result = run("analyze", *arguments, data_dir=data_dir)
        assert result.exit_code == 0
        assert max(len(line) for line in result.output.splitlines()) <= 80

    def test_last_restricts_the_history(self, data_dir: Path, history: list[Draw]) -> None:
        result = run("analyze", "--last", "20", "--window", "50", data_dir=data_dir)
        assert result.exit_code == 0
        assert f"20 draws from {history[-20].date} to {history[-1].date}" in result.output
        assert "Hot, last 20" in result.output

    def test_window_and_top(self, data_dir: Path) -> None:
        result = run("analyze", "--window", "30", "--top", "3", data_dir=data_dir)
        assert result.exit_code == 0
        assert "Hot, last 30" in result.output
        (overdue,) = (
            line for line in result.output.split("LUCKY STARS")[0].splitlines() if "overdue" in line
        )
        assert len(re.findall(r"\d+ draws?\)", overdue)) == 3

    def test_counts_are_singular_when_they_should(
        self, tmp_path: Path, history: list[Draw]
    ) -> None:
        fetcher.save_draws(history[:2], tmp_path / fetcher.CACHE_FILENAME)
        result = run("analyze", data_dir=tmp_path)
        assert result.exit_code == 0
        assert "(1 draw)" in result.output
        assert "(1 draws)" not in result.output

    def test_full_prints_one_row_per_number_and_star(self, data_dir: Path) -> None:
        result = run("analyze", "--full", data_dir=data_dir)
        assert result.exit_code == 0
        rows = re.findall(r"^ +\d+ +\d+ +\d+\.\d\d .*$", result.output, re.MULTILINE)
        assert len(rows) == 50 + 12
        assert result.output.count("Gap now") == 2

    @pytest.mark.parametrize("option", ["--last", "--window", "--top"])
    def test_rejects_non_positive_values(self, option: str, data_dir: Path) -> None:
        assert run("analyze", option, "0", data_dir=data_dir).exit_code == 2


class TestSuggest:
    @pytest.mark.parametrize("strategy", ["hot", "cold", "balanced", "weighted", "anti"])
    def test_prints_the_requested_lines(self, strategy: str, data_dir: Path) -> None:
        result = run("suggest", "--strategy", strategy, "--lines", "4", data_dir=data_dir)
        assert result.exit_code == 0
        assert f"Strategy: {strategy} - " in result.output
        assert len(LINE_PATTERN.findall(result.output)) == 4
        assert DISCLAIMER in result.output
        assert max(len(line) for line in result.output.splitlines()) <= 80

    def test_defaults_to_five_balanced_lines(self, data_dir: Path) -> None:
        result = run("suggest", data_dir=data_dir)
        assert result.exit_code == 0
        assert "Strategy: balanced" in result.output
        assert len(LINE_PATTERN.findall(result.output)) == 5

    def test_seed_makes_the_output_reproducible(self, data_dir: Path) -> None:
        first = run("suggest", "--seed", "1", data_dir=data_dir).output
        assert run("suggest", "--seed", "1", data_dir=data_dir).output == first
        assert run("suggest", "--seed", "2", data_dir=data_dir).output != first

    def test_help_describes_every_strategy(self) -> None:
        result = run("suggest", "--help")
        assert result.exit_code == 0
        for name in ("hot", "cold", "balanced", "weighted", "anti"):
            assert re.search(rf"^ +{name} +\w", result.output, re.MULTILINE)

    @pytest.mark.parametrize(
        "arguments", [("--strategy", "lucky"), ("--lines", "0"), ("--window", "0")]
    )
    def test_rejects_invalid_options(self, arguments: tuple[str, ...], data_dir: Path) -> None:
        assert run("suggest", *arguments, data_dir=data_dir).exit_code == 2

    def test_strategy_errors_are_reported_cleanly(
        self, tmp_path: Path, history: list[Draw]
    ) -> None:
        fetcher.save_draws(history[:5], tmp_path / fetcher.CACHE_FILENAME)
        result = run("suggest", "--strategy", "anti", data_dir=tmp_path)
        assert result.exit_code == 1
        assert "Error: the anti strategy needs at least 20 draws" in result.output


class TestBacktest:
    @pytest.fixture
    def paid_dir(self, tmp_path: Path, paid_history: list[Draw]) -> Path:
        """A data directory whose cache holds 400 random draws with their prizes."""
        fetcher.save_draws(paid_history, tmp_path / fetcher.CACHE_FILENAME)
        return tmp_path

    def test_compares_every_strategy_with_chance(
        self, paid_dir: Path, paid_history: list[Draw]
    ) -> None:
        result = run("backtest", "--seed", "1", data_dir=paid_dir)
        assert result.exit_code == 0
        assert (
            f"Backtest on 80 draws from {paid_history[320].date} to {paid_history[-1].date}."
            in result.output
        )
        assert f"the 320 draws from {paid_history[0].date} to {paid_history[319].date}" in (
            " ".join(result.output.split())
        )
        assert "400 lines for EUR 1,000.00 per strategy" in " ".join(result.output.split())
        rows = {
            line.split()[0]: line.split()
            for line in result.output.splitlines()
            if re.match(r"^(hot|cold|balanced|weighted|anti|random) ", line)
        }
        assert list(rows) == ["hot", "cold", "balanced", "weighted", "anti", "random"]
        for row in rows.values():
            winnings, net, roi = (float(cell.replace(",", "").rstrip("%")) for cell in row[1:4])
            assert net == pytest.approx(winnings - 1000.0, abs=0.011)
            assert roi == pytest.approx(net / 10.0, abs=0.051)
        # The yardstick: what random lines are expected to win, and how much that varies.
        chance = re.search(
            r"^by chance +([\d.]+) +(-[\d.]+) +(-[\d.]+)% +7\.7%  0\.500 +0\.333$",
            result.output,
            re.MULTILINE,
        )
        assert chance
        winnings, net, roi = map(float, chance.groups())
        assert winnings == pytest.approx(183.63, abs=0.006)  # 400 lines at EUR 0.459 each
        assert net == pytest.approx(winnings - 1000.0, abs=0.011)
        assert roi == pytest.approx(net / 10.0, abs=0.051)
        spread = re.search(
            r"^\+/- 1 SD +([\d.]+) +([\d.]+) +(\d+\.\d)% +1\.3%$", result.output, re.MULTILINE
        )
        assert spread
        assert (
            float(spread[1]) == float(spread[2]) == pytest.approx(float(spread[3]) * 10, abs=0.51)
        )
        assert DISCLAIMER in result.output
        assert max(len(line) for line in result.output.splitlines()) <= 80

    def test_explains_how_to_read_the_figures(self, paid_dir: Path) -> None:
        output = " ".join(run("backtest", "--seed", "1", data_dir=paid_dir).output.split())
        # Twelve z-scores on screen: one beyond 2 is nothing unusual.
        assert "beyond +/-2 about once in 22: with 12 of them here" in output
        assert "at least one in about 43% of backtests" in output
        assert "Only a value beyond about +/-2.9" in output
        assert "from prize ranks 4 to 13" in output
        assert "The 3 top ranks, jackpot included, hold about half of the prize money" in output
        assert "EUR 0.30 per line for the My Million raffle, which is not simulated" in output
        assert "With fewer than 1000 lines per strategy, luck dominates these figures." in output
        two = " ".join(
            run("backtest", "--strategy", "hot", "--seed", "1", data_dir=paid_dir).output.split()
        )
        assert "with 2 of them here, at least one in about 9% of backtests" in two
        assert "Only a value beyond about +/-2.2" in two

    def test_enough_lines_and_another_price_drop_the_notes_that_do_not_apply(
        self, paid_dir: Path
    ) -> None:
        arguments = ("--strategy", "random", "--lines", "13", "--price", "2.2", "--seed", "1")
        output = " ".join(run("backtest", *arguments, data_dir=paid_dir).output.split())
        assert "1040 lines for EUR 2,288.00 per strategy" in output
        assert "luck dominates" not in output
        assert "My Million" not in output

    def test_a_cell_too_wide_for_its_column_does_not_touch_its_neighbour(
        self, paid_dir: Path
    ) -> None:
        arguments = ("--strategy", "hot", "--lines", "50", "--price", "99999.99", "--seed", "1")
        output = run("backtest", *arguments, data_dir=paid_dir).output
        (row,) = (line for line in output.splitlines() if line.startswith("hot "))
        assert re.fullmatch(r"hot +[\d,.]+ -[\d,.]+ +-100\.0% +\d+\.\d% .*", row)

    def test_seed_makes_the_output_reproducible(self, paid_dir: Path) -> None:
        first = run("backtest", "--seed", "4", "--strategy", "hot", data_dir=paid_dir).output
        assert (
            run("backtest", "--seed", "4", "--strategy", "hot", data_dir=paid_dir).output == first
        )
        assert (
            run("backtest", "--seed", "5", "--strategy", "hot", data_dir=paid_dir).output != first
        )

    def test_chosen_strategies_lines_fraction_and_price(self, paid_dir: Path) -> None:
        result = run(
            "backtest",
            "--strategy",
            "cold",
            "--strategy",
            "random",
            "--train-fraction",
            "0.9",
            "--lines",
            "2",
            "--price",
            "2.2",
            "--seed",
            "1",
            data_dir=paid_dir,
        )
        assert result.exit_code == 0
        assert "Backtest on 40 draws" in result.output
        assert "80 lines for EUR 176.00 per strategy" in " ".join(result.output.split())
        names = re.findall(r"^(hot|cold|balanced|weighted|anti|random) ", result.output, re.M)
        assert names == ["cold", "random"]

    def test_static_replays_the_lines_of_the_training_set(self, paid_dir: Path) -> None:
        result = run("backtest", "--static", "--seed", "1", data_dir=paid_dir)
        assert result.exit_code == 0
        assert "suggested once from the 320 draws" in " ".join(result.output.split())
        assert "replayed unchanged" in " ".join(result.output.split())
        refreshed = run("backtest", "--seed", "1", data_dir=paid_dir)
        assert "then every draw since" in " ".join(refreshed.output.split())

    def test_reports_the_biggest_win(self, paid_dir: Path) -> None:
        result = run("backtest", "--seed", "1", data_dir=paid_dir)
        assert re.search(
            r"^- Biggest win: EUR [\d,]+\.\d\d by \w+ on \d{4}-\d\d-\d\d "
            r"\(\d numbers? \+ \d stars?\)\.$",
            result.output,
            re.MULTILINE,
        )

    def test_draws_without_prize_data_are_reported(
        self, tmp_path: Path, paid_history: list[Draw]
    ) -> None:
        last = paid_history[-1]
        history = [*paid_history[:-1], Draw(last.date, last.numbers, last.stars)]
        fetcher.save_draws(history, tmp_path / fetcher.CACHE_FILENAME)
        result = run("backtest", "--strategy", "random", "--seed", "1", data_dir=tmp_path)
        assert result.exit_code == 0
        assert "Backtest on 79 draws" in result.output
        assert "1 draw without prize data could not be played." in result.output

    def test_errors_are_reported_cleanly(self, data_dir: Path) -> None:
        # The cache of this fixture has no prize data at all.
        result = run("backtest", data_dir=data_dir)
        assert result.exit_code == 1
        assert "Error: none of the draws to evaluate has prize data" in result.output
        assert "Traceback" not in result.output

    def test_evaluation_before_the_current_rules_is_refused(
        self, tmp_path: Path, make_history: HistoryFactory
    ) -> None:
        history = make_history(99, start=dt.date(2016, 3, 1), prizes=TYPICAL_PRIZES)
        fetcher.save_draws(history, tmp_path / fetcher.CACHE_FILENAME)
        result = run("backtest", "--train-fraction", "0.5", data_dir=tmp_path)
        assert result.exit_code == 1
        assert "use a train fraction of at least 0.607" in result.output

    @pytest.mark.parametrize(
        "arguments",
        [
            ("--strategy", "lucky"),
            ("--train-fraction", "0"),
            ("--train-fraction", "1"),
            ("--lines", "0"),
            ("--window", "0"),
            ("--price", "-1"),
            ("--price", "0"),
        ],
    )
    def test_rejects_invalid_options(self, arguments: tuple[str, ...], data_dir: Path) -> None:
        assert run("backtest", *arguments, data_dir=data_dir).exit_code == 2

    @pytest.mark.parametrize("price", ["nan", "inf"])
    def test_rejects_a_price_that_is_not_an_amount(self, price: str, paid_dir: Path) -> None:
        result = run("backtest", "--price", price, data_dir=paid_dir)
        assert result.exit_code != 0
        assert "Traceback" not in result.output
        assert "nan%" not in result.output


class TestNextDraw:
    def test_prints_the_next_draw(self) -> None:
        result = run("next-draw")
        assert result.exit_code == 0
        assert re.fullmatch(
            r"Next draw: (Tuesday|Friday) \d{1,2} [A-Z][a-z]+ \d{4} at 21:05 Paris time "
            r"\((in \d+ \w+( \d+ \w+)?|now)\)\.\n",
            result.output,
        )

    def test_does_not_need_a_cache(self, tmp_path: Path) -> None:
        assert run("next-draw", data_dir=tmp_path / "missing").exit_code == 0


class TestFormatting:
    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            (dt.datetime(2026, 11, 14, 15, 52, tzinfo=PARIS), "in 3 days 5 hours"),
            (dt.datetime(2026, 11, 16, 21, 5, tzinfo=PARIS), "in 1 day"),
            (dt.datetime(2026, 11, 17, 20, 4, tzinfo=PARIS), "in 1 hour 1 minute"),
            (dt.datetime(2026, 11, 17, 21, 4, 30, tzinfo=PARIS), "now"),
            (dt.datetime(2026, 11, 17, 18, 35, tzinfo=dt.timezone.utc), "in 1 hour 30 minutes"),
        ],
    )
    def test_countdown(self, now: dt.datetime, expected: str) -> None:
        upcoming = dt.datetime(2026, 11, 17, 21, 5, tzinfo=PARIS)
        assert _format_countdown(upcoming, now) == expected

    def test_countdown_counts_real_time_across_a_clock_change(self) -> None:
        # Clocks go back one hour on Sunday 2026-10-25.
        now = dt.datetime(2026, 10, 23, 21, 5, tzinfo=PARIS)
        upcoming = dt.datetime(2026, 10, 27, 21, 5, tzinfo=PARIS)
        assert _format_countdown(upcoming, now) == "in 4 days 1 hour"

    @pytest.mark.parametrize(
        ("jackpot", "expected"),
        [
            (None, ""),
            (PrizeTier(0, 0.0), " (jackpot not won)"),
            (PrizeTier(1, 17_000_000.0), " (jackpot won: 1 winner, EUR 17,000,000 each)"),
            (PrizeTier(2, 8_500_000.4), " (jackpot won: 2 winners, EUR 8,500,000 each)"),
        ],
    )
    def test_describe_draw(self, jackpot: PrizeTier | None, expected: str) -> None:
        prizes = () if jackpot is None else (jackpot, *TYPICAL_PRIZES[1:])
        draw = Draw(dt.date(2026, 11, 13), (16, 27, 32, 41, 45), (2, 12), prizes)
        assert _describe_draw(draw) == f"2026-11-13: 16 27 32 41 45 | stars 02 12{expected}"

    def test_ranking_wraps_between_entries_only(self) -> None:
        text = _ranking("Top pairs", [f"{n}-{n + 1} (12x)" for n in range(1, 21)])
        lines = text.splitlines()
        assert len(lines) > 1
        assert all(len(line) <= 80 for line in lines)
        assert lines[0].startswith("  Top pairs         1-2 (12x)  2-3 (12x)")
        assert all(line.startswith(" " * 20) and line.endswith("(12x)") for line in lines[1:])

    def test_ranking_of_nothing(self) -> None:
        assert _ranking("Hot, last 50", []) == "  Hot, last 50      -"
