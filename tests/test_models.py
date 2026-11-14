"""Tests for the game rules and data models."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from euromillions.models import (
    CURRENT_RULES_SINCE,
    FIRST_DRAW_DATE,
    LINE_PRICE,
    PRIZE_RANKS,
    Draw,
    PrizeTier,
    Suggestion,
    format_line,
    picks_per_draw,
    pool_size,
    star_pool_size,
)

CURRENT = dt.date(2024, 7, 23)


class TestPools:
    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (dt.date(2004, 2, 13), 9),
            (dt.date(2011, 5, 6), 9),
            (dt.date(2011, 5, 10), 11),
            (dt.date(2016, 9, 23), 11),
            (dt.date(2016, 9, 27), 12),
            (dt.date(2026, 11, 13), 12),
        ],
    )
    def test_star_pool_grew_over_time(self, day: dt.date, expected: int) -> None:
        assert star_pool_size(day) == expected

    def test_no_pool_before_the_first_draw(self) -> None:
        with pytest.raises(ValueError, match="2004-02-13"):
            star_pool_size(FIRST_DRAW_DATE - dt.timedelta(days=1))

    def test_pool_size_by_kind(self) -> None:
        assert pool_size("main", dt.date(2005, 1, 7)) == 50
        assert pool_size("main", CURRENT) == 50
        assert pool_size("star", dt.date(2005, 1, 7)) == 9
        assert pool_size("star", CURRENT) == 12

    def test_picks_per_draw(self) -> None:
        assert picks_per_draw("main") == 5
        assert picks_per_draw("star") == 2


class TestCurrentRules:
    def test_current_rules_started_with_the_twelfth_star(self) -> None:
        assert CURRENT_RULES_SINCE == dt.date(2016, 9, 27)
        assert star_pool_size(CURRENT_RULES_SINCE) == 12
        assert star_pool_size(CURRENT_RULES_SINCE - dt.timedelta(days=1)) == 11

    def test_line_price(self) -> None:
        assert LINE_PRICE == 2.5

    def test_thirteen_prize_ranks_from_the_jackpot_down(self) -> None:
        by_rank = {rank: matches for matches, rank in PRIZE_RANKS.items()}
        assert sorted(by_rank) == list(range(1, 14))
        assert by_rank[1] == (5, 2)
        assert by_rank[2] == (5, 1)
        assert by_rank[3] == (5, 0)
        # Three numbers and both stars pay more than four numbers alone.
        assert by_rank[6] == (3, 2)
        assert by_rank[7] == (4, 0)
        assert by_rank[13] == (2, 0)

    @pytest.mark.parametrize("matches", [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)])
    def test_too_few_matches_win_nothing(self, matches: tuple[int, int]) -> None:
        assert matches not in PRIZE_RANKS


class TestDraw:
    def test_numbers_and_stars_are_sorted(self) -> None:
        draw = Draw(CURRENT, (34, 8, 16, 10, 4), (8, 4))
        assert draw.numbers == (4, 8, 10, 16, 34)
        assert draw.stars == (4, 8)

    def test_accepts_any_iterable(self) -> None:
        draw = Draw(CURRENT, [5, 4, 3, 2, 1], iter([2, 1]))  # type: ignore[arg-type]
        assert draw.numbers == (1, 2, 3, 4, 5)
        assert draw.stars == (1, 2)

    def test_is_immutable_and_hashable(self) -> None:
        draw = Draw(CURRENT, (1, 2, 3, 4, 5), (1, 2))
        with pytest.raises(AttributeError):
            draw.numbers = (6, 7, 8, 9, 10)  # type: ignore[misc]
        assert len({draw, Draw(CURRENT, (5, 4, 3, 2, 1), (2, 1))}) == 1

    @pytest.mark.parametrize(
        "numbers",
        [
            (1, 2, 3, 4),  # too few
            (1, 2, 3, 4, 5, 6),  # too many
            (1, 2, 3, 4, 4),  # duplicate
            (0, 2, 3, 4, 5),  # below range
            (1, 2, 3, 4, 51),  # above range
            (1, 2, 3, 4, 5.0),  # not an integer
            (1, 2, 3, 4, True),  # bool is not a ball number
            ("1", 2, 3, 4, 5),
        ],
    )
    def test_rejects_invalid_main_numbers(self, numbers: tuple[int, ...]) -> None:
        with pytest.raises(ValueError, match="main numbers"):
            Draw(CURRENT, numbers, (1, 2))

    @pytest.mark.parametrize("stars", [(1,), (1, 2, 3), (3, 3), (0, 1), (1, 13)])
    def test_rejects_invalid_stars(self, stars: tuple[int, ...]) -> None:
        with pytest.raises(ValueError, match="Lucky Stars"):
            Draw(CURRENT, (1, 2, 3, 4, 5), stars)

    def test_stars_are_checked_against_the_pool_of_the_draw_date(self) -> None:
        numbers = (1, 2, 3, 4, 5)
        assert Draw(dt.date(2010, 1, 1), numbers, (8, 9)).stars == (8, 9)
        with pytest.raises(ValueError, match="between 1 and 9"):
            Draw(dt.date(2010, 1, 1), numbers, (9, 10))
        assert Draw(dt.date(2012, 1, 3), numbers, (10, 11)).stars == (10, 11)
        with pytest.raises(ValueError, match="between 1 and 11"):
            Draw(dt.date(2012, 1, 3), numbers, (11, 12))
        assert Draw(dt.date(2016, 9, 27), numbers, (11, 12)).stars == (11, 12)

    def test_rejects_dates_before_the_first_draw(self) -> None:
        with pytest.raises(ValueError, match="before"):
            Draw(dt.date(2004, 2, 12), (1, 2, 3, 4, 5), (1, 2))

    @pytest.mark.parametrize(
        "date", [dt.datetime(2024, 7, 23, 21, 5), "2024-07-23", None, 20240723]
    )
    def test_rejects_anything_but_a_plain_date(self, date: object) -> None:
        with pytest.raises(ValueError, match=r"datetime\.date"):
            Draw(date, (1, 2, 3, 4, 5), (1, 2))  # type: ignore[arg-type]

    def test_prizes_default_to_unknown(self) -> None:
        draw = Draw(CURRENT, (1, 2, 3, 4, 5), (1, 2))
        assert draw.prizes == ()
        assert draw.jackpot is None

    def test_prizes_are_kept_in_rank_order(self) -> None:
        tiers = [PrizeTier(1, 17_000_000.0), PrizeTier(4, 250_000.5), PrizeTier(9, 30_000.0)]
        draw = Draw(CURRENT, (1, 2, 3, 4, 5), (1, 2), tiers)  # type: ignore[arg-type]
        assert draw.prizes == tuple(tiers)
        assert draw.jackpot == PrizeTier(1, 17_000_000.0)

    @pytest.mark.parametrize("prizes", [[(1, 2.0)], [None], ["17000000"], [PrizeTier(1, 2.0), 3]])
    def test_rejects_prizes_that_are_not_prize_tiers(self, prizes: list[object]) -> None:
        with pytest.raises(ValueError, match="PrizeTier"):
            Draw(CURRENT, (1, 2, 3, 4, 5), (1, 2), prizes)  # type: ignore[arg-type]

    def test_str(self) -> None:
        assert str(Draw(CURRENT, (34, 8, 16, 10, 4), (8, 4))) == (
            "2024-07-23: 04 08 10 16 34 | stars 04 08"
        )


class TestPrizeTier:
    def test_prize_is_stored_as_float(self) -> None:
        tier = PrizeTier(3, 17_000_000)
        assert tier == PrizeTier(3, 17_000_000.0)
        assert isinstance(tier.prize, float)

    def test_nobody_won(self) -> None:
        assert PrizeTier(0, 0.0).winners == 0

    @pytest.mark.parametrize("winners", [-1, 1.5, True, "2", None, float("nan")])
    def test_rejects_invalid_winners(self, winners: object) -> None:
        with pytest.raises(ValueError, match="winners"):
            PrizeTier(winners, 10.0)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "prize", [-0.01, float("nan"), float("inf"), True, "17000000", None, [1.0]]
    )
    def test_rejects_invalid_prize(self, prize: object) -> None:
        with pytest.raises(ValueError, match="prize"):
            PrizeTier(1, prize)  # type: ignore[arg-type]

    def test_is_immutable_and_hashable(self) -> None:
        tier = PrizeTier(1, 2.0)
        with pytest.raises(AttributeError):
            tier.winners = 2  # type: ignore[misc]
        assert len({tier, PrizeTier(1, 2)}) == 1


class TestDrawSerialisation:
    def test_round_trip_with_prizes(self) -> None:
        prizes = (PrizeTier(0, 0.0), PrizeTier(4, 175_411.7), PrizeTier(1_035_060, 4.3))
        draw = Draw(CURRENT, (4, 8, 10, 16, 34), (4, 8), prizes)
        data = draw.to_dict()
        assert data == {
            "date": "2024-07-23",
            "numbers": [4, 8, 10, 16, 34],
            "stars": [4, 8],
            "prizes": [[0, 0.0], [4, 175_411.7], [1_035_060, 4.3]],
        }
        assert Draw.from_dict(json.loads(json.dumps(data))) == draw

    def test_round_trip_without_prizes_omits_the_key(self) -> None:
        draw = Draw(CURRENT, (4, 8, 10, 16, 34), (4, 8))
        data = draw.to_dict()
        assert set(data) == {"date", "numbers", "stars"}
        assert Draw.from_dict(data) == draw

    @pytest.mark.parametrize(
        "data",
        [
            {},
            {"date": "2024-07-23", "numbers": [1, 2, 3, 4, 5]},  # missing stars
            {"date": "23/07/2024", "numbers": [1, 2, 3, 4, 5], "stars": [1, 2]},
            {"date": 20240723, "numbers": [1, 2, 3, 4, 5], "stars": [1, 2]},
            {"date": "2024-07-23", "numbers": None, "stars": [1, 2]},
            {"date": "2024-07-23", "numbers": 12345, "stars": [1, 2]},
            {"date": "2024-07-23", "numbers": [1, 2, 3, 4, 99], "stars": [1, 2]},
            {"date": "2024-07-23", "numbers": [1, 2, 3, 4, 5], "stars": [[1], [2]]},
            {"date": "2024-07-23", "numbers": [1, 2, 3, 4, 5], "stars": [1, 2], "prizes": 3},
            {"date": "2024-07-23", "numbers": [1, 2, 3, 4, 5], "stars": [1, 2], "prizes": [[1]]},
            {"date": "2024-07-23", "numbers": [1, 2, 3, 4, 5], "stars": [1, 2], "prizes": [7]},
            {
                "date": "2024-07-23",
                "numbers": [1, 2, 3, 4, 5],
                "stars": [1, 2],
                "prizes": [[1, 2.0, 3]],
            },
            {
                "date": "2024-07-23",
                "numbers": [1, 2, 3, 4, 5],
                "stars": [1, 2],
                "prizes": [[-1, 2.0]],
            },
            {
                "date": "2024-07-23",
                "numbers": [1, 2, 3, 4, 5],
                "stars": [1, 2],
                "prizes": [["1", "2.0"]],
            },
            ["2024-07-23", [1, 2, 3, 4, 5], [1, 2]],
            None,
        ],
    )
    def test_from_dict_raises_value_error_on_malformed_input(self, data: object) -> None:
        with pytest.raises(ValueError):
            Draw.from_dict(data)  # type: ignore[arg-type]


class TestSuggestion:
    def test_is_normalised_and_formatted(self) -> None:
        suggestion = Suggestion((47, 3, 35, 15, 29), (4, 3), strategy="hot")
        assert suggestion.numbers == (3, 15, 29, 35, 47)
        assert suggestion.stars == (3, 4)
        assert suggestion.strategy == "hot"
        assert str(suggestion) == "03 15 29 35 47 | stars 03 04"

    def test_uses_the_current_rules(self) -> None:
        assert Suggestion((1, 2, 3, 4, 50), (11, 12), strategy="hot").stars == (11, 12)

    @pytest.mark.parametrize(
        ("numbers", "stars"),
        [
            ((1, 2, 3, 4, 51), (1, 2)),
            ((1, 2, 3, 4, 4), (1, 2)),
            ((1, 2, 3, 4), (1, 2)),
            ((1, 2, 3, 4, 5), (1, 13)),
            ((1, 2, 3, 4, 5), (2, 2)),
            ((1, 2, 3, 4, 5), (1, 2, 3)),
        ],
    )
    def test_rejects_invalid_lines(self, numbers: tuple[int, ...], stars: tuple[int, ...]) -> None:
        with pytest.raises(ValueError):
            Suggestion(numbers, stars, strategy="hot")


def test_format_line_zero_pads() -> None:
    assert format_line((1, 2, 3, 4, 50), (1, 12)) == "01 02 03 04 50 | stars 01 12"
