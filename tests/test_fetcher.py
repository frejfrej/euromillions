"""Tests for downloading, parsing and caching the draw history.

Everything runs offline, against excerpts of the real FDJ files kept in ``fixtures/``
(byte-exact: a header and a few rows of each archive, plus a trimmed history page),
except one opt-in test that downloads the real history.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import os
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import requests
from requests.adapters import BaseAdapter, HTTPAdapter

from euromillions import __version__, fetcher
from euromillions.fetcher import (
    HISTORY_PAGE_URL,
    KNOWN_ARCHIVE_URLS,
    CacheError,
    CorruptCacheError,
    FetchError,
    UpdateResult,
    discover_archive_urls,
    fetch_draws,
    load_draws,
    merge_draws,
    parse_archive,
    parse_csv,
    save_draws,
    update_cache,
)
from euromillions.models import FIRST_DRAW_DATE, Draw, PrizeTier
from euromillions.schedule import DRAW_TIMEZONE

from .conftest import TYPICAL_PRIZES, random_history

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class Redirect:
    """An HTTP redirection to ``location``."""

    status: int
    location: str


#: What a fake website answers: the body of a 200 response (as bytes, or as a stream to
#: read it from), an HTTP error status, a redirection, or an exception to raise.
Outcome = bytes | io.BytesIO | int | Redirect | Exception

#: The tolerance of the real completeness check, before any test relaxes it.
REAL_MAX_GAP = fetcher._MAX_GAP


def read_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def tiers(*pairs: tuple[int, float]) -> tuple[PrizeTier, ...]:
    return tuple(PrizeTier(winners, prize) for winners, prize in pairs)


def paris(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=DRAW_TIMEZONE)


#: When most tests take place: the day after the newest draw of the fixtures.
NOW = paris(2026, 11, 14)


#: The rows of each fixture, in file order, as (date, main numbers, Lucky Stars, jackpot tier).
#: The files are listed oldest first, like ``KNOWN_ARCHIVE_URLS``.
EXPECTED: dict[str, list[tuple[dt.date, tuple[int, ...], tuple[int, ...], PrizeTier]]] = {
    "euromillions.csv": [  # dates as 20110506; 12 prize ranks; Lucky Stars 1-9
        (dt.date(2011, 5, 6), (11, 16, 20, 22, 28), (4, 9), PrizeTier(1, 28772008.0)),
        (dt.date(2011, 4, 29), (15, 25, 38, 40, 41), (1, 2), PrizeTier(0, 0.0)),
        (dt.date(2011, 3, 25), (6, 12, 21, 27, 33), (3, 7), PrizeTier(2, 69105267.0)),
        (dt.date(2004, 2, 13), (16, 29, 32, 36, 41), (7, 9), PrizeTier(1, 15000000.0)),
    ],
    "euromillions_2.csv": [  # dates as 31/01/2014; Lucky Stars 1-11
        (dt.date(2014, 1, 31), (8, 10, 15, 16, 31), (8, 9), PrizeTier(1, 72149579.0)),
        (dt.date(2013, 1, 1), (2, 7, 8, 36, 49), (1, 11), PrizeTier(3, 12677536.0)),
        (dt.date(2011, 5, 10), (3, 10, 15, 28, 45), (5, 7), PrizeTier(0, 0.0)),
    ],
    "euromillions_3.csv": [  # its first row is dated 23/09/16
        (dt.date(2016, 9, 23), (14, 16, 39, 42, 49), (3, 9), PrizeTier(1, 23850387.0)),
        (dt.date(2016, 9, 2), (8, 16, 32, 40, 50), (2, 11), PrizeTier(5, 7840860.0)),
        (dt.date(2014, 2, 4), (1, 21, 33, 37, 38), (4, 8), PrizeTier(0, 0.0)),
    ],
    "euromillions_4.csv": [  # Windows-1252, renamed prize columns, one surplus field per row
        (dt.date(2016, 9, 27), (6, 9, 13, 39, 41), (2, 12), PrizeTier(0, 0.0)),
        (dt.date(2016, 11, 25), (5, 11, 17, 23, 28), (10, 11), PrizeTier(2, 30570889.0)),
        (dt.date(2019, 2, 26), (3, 15, 29, 35, 47), (3, 4), PrizeTier(0, 0.0)),
    ],
    "euromillions_201902.csv": [  # Windows-1252
        (dt.date(2020, 1, 31), (13, 18, 20, 23, 30), (2, 4), PrizeTier(0, 0.0)),
        (dt.date(2019, 5, 28), (7, 19, 20, 28, 29), (1, 5), PrizeTier(2, 32676834.0)),
        (dt.date(2019, 3, 1), (6, 9, 19, 26, 31), (11, 12), PrizeTier(0, 0.0)),
    ],
    "euromillions_202002.csv": [  # UTF-8 with an accent in the header
        (dt.date(2026, 11, 13), (16, 27, 32, 41, 45), (2, 12), PrizeTier(0, 0.0)),
        (dt.date(2026, 11, 10), (15, 18, 31, 37, 41), (1, 5), PrizeTier(1, 141899445.0)),
        (dt.date(2026, 4, 21), (13, 16, 29, 40, 47), (3, 4), PrizeTier(3, 48175066.0)),
        (dt.date(2022, 8, 19), (2, 9, 13, 19, 38), (2, 6), PrizeTier(0, 0.0)),
        (dt.date(2020, 2, 4), (21, 23, 33, 35, 47), (6, 7), PrizeTier(0, 0.0)),
    ],
}
ALL_DATES = sorted(date for rows in EXPECTED.values() for date, *_ in rows)

# fmt: off
#: Every prize tier, best rank first, of one or two rows of each generation of archive.
EXPECTED_PRIZES: dict[tuple[str, dt.date], tuple[PrizeTier, ...]] = {
    # Columns named ..._rang1_en_europe / rapport_du_rang1; only twelve ranks before 2011.
    ("euromillions.csv", dt.date(2011, 5, 6)): tiers(
        (1, 28772008), (14, 227484), (19, 47567.7), (149, 4332.6), (2244, 191.7), (3810, 79),
        (6446, 66.7), (93092, 23.5), (84572, 22.3), (152321, 13.2), (410991, 10.5),
        (1191381, 8.6),
    ),
    ("euromillions_3.csv", dt.date(2016, 9, 23)): tiers(
        (1, 23850387), (6, 221259.6), (11, 40229), (42, 5268), (831, 232.9), (1573, 123),
        (2433, 56.8), (37391, 17), (37525, 16.2), (70992, 14.4), (206354, 8.7), (570692, 8.5),
        (1084325, 4.5),
    ),
    # Columns named ..._rang1_Euro_Millions_en_europe / rapport_du_rang1_Euro_Millions.
    ("euromillions_4.csv", dt.date(2016, 9, 27)): tiers(
        (0, 0), (3, 293260.3), (7, 29272.9), (21, 4772.7), (536, 199.4), (877, 170.1),
        (1554, 54.4), (13715, 28.4), (25400, 16.2), (72147, 10.8), (70688, 15.5), (368993, 8.9),
        (1018716, 3.9),
    ),
    # Published with no European winner next to non-zero prizes: kept as it is.
    ("euromillions_201902.csv", dt.date(2020, 1, 31)): tiers(
        (0, 0), (0, 90934.6), (0, 14119.8), (0, 2645), (0, 109.3), (0, 71.9), (0, 42),
        (0, 15.4), (0, 10.9), (0, 9.8), (0, 9.2), (0, 6.7), (0, 3.9),
    ),
    # Amounts with two decimals: 175411,70.
    ("euromillions_202002.csv", dt.date(2026, 11, 13)): tiers(
        (0, 0), (4, 175411.70), (8, 20498.30), (30, 1702.50), (715, 131.50), (1263, 78.70),
        (1690, 41.30), (18949, 18.40), (28558, 13.60), (71701, 10.10), (102329, 8.50),
        (422568, 6.50), (1035060, 4.30),
    ),
    # The only row in which FDJ wrote amounts with a decimal point: 93.4, 9.8 and 5.4.
    ("euromillions_202002.csv", dt.date(2022, 8, 19)): tiers(
        (0, 0), (8, 95084), (11, 16161.9), (58, 954.7), (1092, 93.4), (2913, 37), (2104, 36),
        (38526, 9.8), (48895, 8.6), (97525, 8), (176398, 5.4), (638876, 4.6), (1328725, 3.6),
    ),
}
# fmt: on

#: A minimal file: the required columns and the newest real draw.
HEADER = "date_de_tirage;boule_1;boule_2;boule_3;boule_4;boule_5;etoile_1;etoile_2"
ROW = "13/11/2026;41;32;45;27;16;12;2"
NEWEST = Draw(dt.date(2026, 11, 13), (16, 27, 32, 41, 45), (2, 12))
#: The same with the thirteen prize ranks, under their names before September 2016.
PRIZE_HEADER = HEADER + "".join(
    f";nombre_de_gagnant_au_rang{rank}_en_europe;rapport_du_rang{rank}" for rank in range(1, 14)
)
#: The cells of ranks 3 to 13 in the rows of such a file: the tests only vary the first two.
LOWER_RANKS = "".join(f";{1000 * rank};{20 - rank},5" for rank in range(3, 14))
LOWER_TIERS = tuple(PrizeTier(1000 * rank, 20.5 - rank) for rank in range(3, 14))


def csv_file(*lines: str) -> bytes:
    return "\n".join(lines).encode() + b"\n"


def zip_of(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def csv_of(draws: Iterable[Draw]) -> bytes:
    """Write draws the way FDJ does, without their prizes."""
    return csv_file(
        HEADER,
        *(f"{d.date:%d/%m/%Y};" + ";".join(map(str, (*d.numbers, *d.stars))) for d in draws),
    )


@pytest.fixture(scope="module")
def fdj_draws() -> list[Draw]:
    """Every draw of the fixtures, oldest first (the parser itself is checked separately)."""
    draws = merge_draws(*(parse_csv(read_fixture(name)) for name in EXPECTED))
    assert [draw.date for draw in draws] == ALL_DATES
    return draws


@pytest.fixture(scope="module")
def full_history() -> list[Draw]:
    """A draw on every draw day from the first to 2026-11-13: 1998 of them, as in reality."""
    draws = random_history(1998, start=FIRST_DRAW_DATE)
    assert draws[-1].date == NEWEST.date
    return draws


@pytest.fixture(autouse=True)
def excerpts_pass_for_a_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the 21 draws of the fixtures pass for a complete history.

    A real history has no hole longer than two weeks. The tests of that rule ask for the
    ``strict`` fixture, which puts the real tolerance back.
    """
    monkeypatch.setattr(fetcher, "_MAX_GAP", dt.timedelta(days=10_000))


@pytest.fixture
def strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher, "_MAX_GAP", REAL_MAX_GAP)


# --- Fake website ---------------------------------------------------------------------


class FakeTransport(BaseAdapter):
    """Answers the requests of a real ``requests.Session`` from a table, and records them.

    Unknown URLs get a 404.
    """

    def __init__(self, routes: Mapping[str, Outcome]) -> None:
        super().__init__()
        self.routes = dict(routes)
        self.requests: list[requests.PreparedRequest] = []
        self.timeouts: list[Any] = []

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: Any = None,
        verify: Any = True,
        cert: Any = None,
        proxies: Any = None,
    ) -> requests.Response:
        self.requests.append(request)
        self.timeouts.append(timeout)
        assert request.url is not None
        outcome = self.routes.get(request.url, 404)
        if isinstance(outcome, Exception):
            raise outcome
        response = requests.Response()
        response.request = request
        response.url = request.url
        if isinstance(outcome, Redirect):
            response.status_code = outcome.status
            response.headers["Location"] = outcome.location
        else:
            response.status_code = outcome if isinstance(outcome, int) else 200
        if isinstance(outcome, io.BytesIO):
            response.raw = outcome
        else:
            response.raw = io.BytesIO(outcome if isinstance(outcome, bytes) else b"")
        return response

    def close(self) -> None:
        pass

    @property
    def urls(self) -> list[str | None]:
        return [request.url for request in self.requests]


def fake_session(routes: Mapping[str, Outcome]) -> tuple[requests.Session, FakeTransport]:
    """Return a session that can only reach the fake website, and its transport."""
    transport = FakeTransport(routes)
    session = requests.Session()
    session.trust_env = False
    session.mount("https://", transport)
    session.mount("http://", transport)
    return session, transport


#: The fixture standing in for each real archive.
ARCHIVE_FILES = dict(zip(KNOWN_ARCHIVE_URLS, EXPECTED, strict=True))


def website() -> dict[str, Outcome]:
    """The history page and the six archives it links to."""
    routes: dict[str, Outcome] = {HISTORY_PAGE_URL: read_fixture("history_page.html")}
    for url, name in ARCHIVE_FILES.items():
        routes[url] = zip_of({name: read_fixture(name)})
    return routes


def site_serving(*files: Iterable[Draw]) -> dict[str, Outcome]:
    """A website whose page is down and whose known archives hold the given files of draws.

    There are at most six files; the remaining archives repeat the first draw of all.
    """
    contents = [csv_of(draws) for draws in files]
    filler = csv_file(*contents[0].decode().splitlines()[:2])
    routes: dict[str, Outcome] = {HISTORY_PAGE_URL: 503}
    for index, url in enumerate(KNOWN_ARCHIVE_URLS):
        content = contents[index] if index < len(contents) else filler
        routes[url] = zip_of({"draws.csv": content})
    return routes


# --- parse_csv ------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(EXPECTED))
def test_parse_csv_reads_every_generation_of_archive(name: str) -> None:
    draws = parse_csv(read_fixture(name))
    assert [(d.date, d.numbers, d.stars, d.jackpot) for d in draws] == EXPECTED[name]
    assert {len(d.prizes) for d in draws} == {12 if name == "euromillions.csv" else 13}


@pytest.mark.parametrize(("name", "date"), list(EXPECTED_PRIZES))
def test_parse_csv_reads_the_prizes_of_every_rank(name: str, date: dt.date) -> None:
    (draw,) = (d for d in parse_csv(read_fixture(name)) if d.date == date)
    assert draw.prizes == EXPECTED_PRIZES[name, date]


def test_fixture_of_the_newest_draw_agrees_with_the_shared_prizes() -> None:
    assert parse_csv(read_fixture("euromillions_202002.csv"))[0].prizes == TYPICAL_PRIZES


@pytest.mark.parametrize("name", ["euromillions_4.csv", "euromillions_201902.csv"])
def test_parse_csv_falls_back_to_windows_1252(name: str) -> None:
    content = read_fixture(name)
    assert b"num\xe9ro_de_tirage_dans_le_cycle" in content
    with pytest.raises(UnicodeDecodeError):
        content.decode("utf-8")
    assert len(parse_csv(content)) == 3


def test_parse_csv_tolerates_a_byte_order_mark_blank_lines_and_crlf() -> None:
    content = read_fixture("euromillions_202002.csv")
    expected = parse_csv(content)
    assert parse_csv(b"\xef\xbb\xbf" + content) == expected
    assert parse_csv(content.replace(b"\n", b"\r\n")) == expected
    assert parse_csv(b"\n" + content.replace(b"\n", b"\n\n   \n") + b"\n") == expected
    # The mark must not stick to the name of the first column when that one is read.
    assert parse_csv(b"\xef\xbb\xbf" + csv_file(HEADER, ROW)) == [NEWEST]


def test_parse_csv_needs_only_the_draw_columns() -> None:
    assert parse_csv(csv_file(HEADER, ROW)) == [NEWEST]


def test_parse_csv_ignores_spaces_around_names_and_cells() -> None:
    header = " ; ".join(HEADER.split(";"))
    row = " ; ".join(ROW.split(";"))
    assert parse_csv(csv_file(f" {header} ", f" {row} ")) == [NEWEST]


def test_parse_csv_ignores_surplus_trailing_fields() -> None:
    assert parse_csv(csv_file(HEADER, ROW + ";;x;")) == [NEWEST]


def test_parse_csv_keeps_one_row_per_line_whatever_the_quotes() -> None:
    # Honouring the quote characters would merge the three rows into one.
    content = csv_file(
        HEADER + ";code", ROW + ';"AB', "10/11/2026;15;18;37;41;31;1;5;CD", ROW + ';EF"'
    )
    assert [draw.date.day for draw in parse_csv(content)] == [13, 10, 13]


def test_parse_csv_reads_the_ranks_in_order_and_decimal_commas() -> None:
    draws = parse_csv(csv_file(PRIZE_HEADER, ROW + ";1;17000000,5;3;250000" + LOWER_RANKS))
    top_tiers = tiers((1, 17000000.5), (3, 250000))
    assert draws == [Draw(NEWEST.date, NEWEST.numbers, NEWEST.stars, top_tiers + LOWER_TIERS)]
    assert len(draws[0].prizes) == 13


@pytest.mark.parametrize(
    "cells",
    [
        "1;17000000,5;3;" + LOWER_RANKS,
        "1;17000000,5;;250000" + LOWER_RANKS,
        ";;;" + LOWER_RANKS,
        "1;17000000,5;3;250000" + LOWER_RANKS.rpartition(";")[0] + ";",  # the very last cell
        "1;17000000,5;3;250000",  # a row that stops short of its last prize cells
        "1",
    ],
)
def test_parse_csv_drops_the_prizes_of_a_row_with_a_blank_prize_cell(cells: str) -> None:
    assert parse_csv(csv_file(PRIZE_HEADER, f"{ROW};{cells}")) == [NEWEST]


def test_a_blank_prize_cell_only_affects_its_own_row() -> None:
    content = read_fixture("euromillions_202002.csv")
    assert content.count(b";175411,70;") == 1
    draws = parse_csv(content.replace(b";175411,70;", b";;"))
    assert [len(draw.prizes) for draw in draws] == [0, 13, 13, 13, 13]
    assert draws[0] == NEWEST  # still a valid draw, without prizes


@pytest.mark.parametrize(
    "cells",
    [
        "abc;17000000,5;3;250000",  # winners
        "-5;17000000,5;3;250000",
        "1.5;17000000,5;3;250000",
        "1;abc;3;250000",  # prize
        "1;-5;3;250000",
        "1;17 000 000;3;250000",
        "1;17.000.000;3;250000",
        "1;17.000;3;250000",  # a thousands separator, not a decimal mark
        "1;17000000,5;3;x",
        ";;3;x",  # malformed next to blank cells
    ],
)
def test_parse_csv_rejects_a_malformed_prize_cell(cells: str) -> None:
    good = ROW + ";1;17000000,5;3;250000" + LOWER_RANKS
    with pytest.raises(FetchError, match=r"line 3: invalid (number|amount)"):
        parse_csv(csv_file(PRIZE_HEADER, good, f"{ROW};{cells}{LOWER_RANKS}"))


def test_parse_csv_rejects_absurd_figures() -> None:
    # Far beyond any count of winners or amount, and beyond what a float can hold.
    for cells in ("9" * 400 + ";17000000,5;3;250000", "1;" + "9" * 400 + ";3;250000"):
        with pytest.raises(FetchError, match="line 2: "):
            parse_csv(csv_file(PRIZE_HEADER, f"{ROW};{cells}{LOWER_RANKS}"))


def test_parse_csv_takes_only_the_european_euromillions_ranks() -> None:
    # The look-alikes of the real files, placed before the columns of the ranks themselves.
    header = (
        HEADER + ";nombre_de_gagnant_au_rang1_en_france;nombre_de_gagnant_au_rang1_Etoile+"
        ";rapport_du_rang1_Etoile+;nombre_de_gagnant_au_rang1_Euro_Millions_en_france"
        + PRIZE_HEADER.removeprefix(HEADER)
    )
    (draw,) = parse_csv(csv_file(header, ROW + ";7;8;9,5;6;1;17000000,5;3;250000" + LOWER_RANKS))
    assert draw.prizes == tiers((1, 17000000.5), (3, 250000)) + LOWER_TIERS


@pytest.mark.parametrize(
    ("header", "ranks"),
    [
        # Renamed columns: none of the ranks is recognised any more.
        (PRIZE_HEADER.replace("_en_europe", "_in_europe"), 0),
        (HEADER + ";nombre_de_gagnant_au_rang1_Etoile+;rapport_du_rang1_Etoile+", 0),
        # Rank 2 lacks its prize column: the ranks would stop there.
        (PRIZE_HEADER.replace(";rapport_du_rang2", ";rapport_rang2"), 1),
        # Fewer ranks than the game ever had, then more.
        (PRIZE_HEADER.rpartition(";nombre_de_gagnant_au_rang12")[0], 11),
        (PRIZE_HEADER + ";nombre_de_gagnant_au_rang14_en_europe;rapport_du_rang14", 14),
    ],
)
def test_parse_csv_rejects_prize_columns_it_does_not_fully_understand(
    header: str, ranks: int
) -> None:
    # Reading them as "no prizes" would silently drop what the file says.
    message = rf"line 1: unexpected prize columns \({ranks} complete ranks found\)"
    with pytest.raises(FetchError, match=message):
        parse_csv(csv_file(header, ROW))


def test_parse_csv_accepts_the_twelve_ranks_of_the_first_years() -> None:
    header = PRIZE_HEADER.rpartition(";nombre_de_gagnant_au_rang13")[0]
    row = "06/05/2011;11;16;20;22;28;4;9;1;17000000,5;3;250000" + LOWER_RANKS
    (draw,) = parse_csv(csv_file(header, row))
    assert draw.prizes == tiers((1, 17000000.5), (3, 250000)) + LOWER_TIERS[:10]


@pytest.mark.parametrize("column", ["date_de_tirage", "boule_1", "etoile_2", "rapport_du_rang7"])
def test_parse_csv_rejects_a_column_given_twice(column: str) -> None:
    # Which of the two would be the right one?
    with pytest.raises(FetchError, match=rf"line 1: column\(s\) {column} appear more than once"):
        parse_csv(csv_file(f"{PRIZE_HEADER};{column}", ROW))


def test_parse_csv_ignores_repeated_columns_it_does_not_read() -> None:
    # The real files end with several unnamed columns.
    assert parse_csv(csv_file(HEADER + ";code;code;;", ROW + ";A;B;;")) == [NEWEST]


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("2026-11-13;41;32;45;27;16;12;2", "invalid draw date '2026-11-13'"),
        ("32/01/2026;41;32;45;27;16;12;2", "invalid draw date"),
        ("20261340;41;32;45;27;16;12;2", "invalid draw date"),
        ("1/2/2026;41;32;45;27;16;12;2", "invalid draw date"),
        (";41;32;45;27;16;12;2", "invalid draw date ''"),
        ("06/02/2004;41;32;45;27;16;9;2", "2004-02-13"),  # before the first draw
        ("13/11/2026;41;x;45;27;16;12;2", "invalid number 'x'"),
        ("13/11/2026;41;3.5;45;27;16;12;2", "invalid number"),
        ("13/11/2026;41;-3;45;27;16;12;2", "invalid number"),
        ("13/11/2026;41;1_0;45;27;16;12;2", "invalid number"),
        ("13/11/2026;41;;45;27;16;12;2", "invalid number ''"),
        ("13/11/2026;41;32;45;27", "invalid number ''"),  # row too short
        ("13/11/2026;41;41;45;27;16;12;2", "distinct"),
        ("13/11/2026;41;32;45;27;51;12;2", "between 1 and 50"),
        ("03/02/2015;41;32;45;27;16;12;2", "between 1 and 11"),  # no star 12 before 2016
    ],
)
def test_parse_csv_rejects_an_invalid_row_and_names_its_line(row: str, message: str) -> None:
    with pytest.raises(FetchError, match=message) as error:
        parse_csv(csv_file(HEADER, ROW, "", row, ROW))
    assert str(error.value).startswith("line 4: ")


def test_parse_csv_rejects_a_file_lacking_a_required_column() -> None:
    header = HEADER.replace(";boule_3", "").replace(";etoile_2", "")
    with pytest.raises(FetchError, match=r"line 1: missing column\(s\) boule_3, etoile_2"):
        parse_csv(csv_file(header, ROW))


@pytest.mark.parametrize("content", [b"", b"\n\n  \n", b"date_de_tirage;\x81"])
def test_parse_csv_rejects_a_file_that_is_empty_or_not_text(content: bytes) -> None:
    with pytest.raises(FetchError):
        parse_csv(content)


def test_parse_csv_rejects_a_file_without_any_draw() -> None:
    # An archive cut down to its header would otherwise leave a hole in the history.
    with pytest.raises(FetchError, match="the file has no draw"):
        parse_csv(csv_file(HEADER))


# --- parse_archive --------------------------------------------------------------------


def test_parse_archive_reads_the_csv_file_of_an_archive() -> None:
    content = read_fixture("euromillions_3.csv")
    assert parse_archive(zip_of({"euromillions_3.csv": content})) == parse_csv(content)


def test_parse_archive_reads_every_csv_file_and_nothing_else() -> None:
    first, second = read_fixture("euromillions_3.csv"), read_fixture("euromillions_4.csv")
    archive = zip_of(
        {
            "LISEZMOI.txt": b"hello",
            "a/one.csv": first,
            "TWO.CSV": second,
            # What macOS and others add next to the real files.
            "__MACOSX/a/._one.csv": b"\x00\x05\x16\x07",
            ".hidden.csv": b"junk",
            "a/.cache/three.csv": b"junk",
        }
    )
    assert parse_archive(archive) == parse_csv(first) + parse_csv(second)


def test_parse_archive_refuses_too_many_csv_files() -> None:
    content = read_fixture("euromillions_3.csv")
    assert len(parse_archive(zip_of({f"{n}.csv": content for n in range(4)}))) == 12
    with pytest.raises(FetchError, match="more than 4 CSV files"):
        parse_archive(zip_of({f"{n}.csv": content for n in range(5)}))


def damaged_archive(damage: str) -> bytes:
    name = "euromillions.csv"
    good = zip_of({name: read_fixture(name)})
    directory = good.rindex(b"PK\x01\x02")  # central directory entry of the only file
    data = bytearray(good)
    if damage == "not a zip":
        return b"<html><body>Service unavailable</body></html>"
    if damage == "empty":
        return b""
    if damage == "truncated":
        return good[: len(good) // 2]
    if damage == "corrupt data":
        data[30 + len(name)] = 0x07  # first byte of the deflate stream: a reserved block type
    elif damage == "unsupported compression":
        data[8:10] = data[directory + 10 : directory + 12] = (99).to_bytes(2, "little")
    elif damage == "understated size":
        data[directory + 24 : directory + 28] = (10).to_bytes(4, "little")
    return bytes(data)


@pytest.mark.parametrize(
    "damage",
    [
        "not a zip",
        "empty",
        "truncated",
        "corrupt data",
        "unsupported compression",
        "understated size",
    ],
)
def test_parse_archive_rejects_what_is_not_a_readable_zip_archive(damage: str) -> None:
    with pytest.raises(FetchError, match="not a readable zip archive"):
        parse_archive(damaged_archive(damage))


def test_parse_archive_rejects_an_archive_without_csv_file() -> None:
    with pytest.raises(FetchError, match="no CSV file"):
        parse_archive(zip_of({"LISEZMOI.txt": b"hello", "data/": b""}))


def test_parse_archive_caps_the_decompressed_size(monkeypatch: pytest.MonkeyPatch) -> None:
    content = read_fixture("euromillions.csv")
    archive = zip_of({"euromillions.csv": content})
    monkeypatch.setattr(fetcher, "_MAX_CSV_BYTES", len(content))
    assert len(parse_archive(archive)) == 4
    monkeypatch.setattr(fetcher, "_MAX_CSV_BYTES", len(content) - 1)
    with pytest.raises(FetchError, match=r"'euromillions\.csv': larger than \d+ bytes"):
        parse_archive(archive)


def test_an_oversized_file_is_never_inflated_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    # A megabyte of zeros takes a kilobyte in the archive: its declared size cannot be trusted
    # and its content must not be read beyond what tells that it is too large.
    monkeypatch.setattr(fetcher, "_MAX_CSV_BYTES", 1000)
    bomb = zip_of({"bomb.csv": bytes(1_000_000), "small.csv": b"x" * 10})
    assert len(bomb) < 2000
    assert [(name, len(data)) for name, data in fetcher._csv_files(bomb)] == [
        ("bomb.csv", 1001),
        ("small.csv", 10),
    ]


def test_parse_archive_names_the_file_and_line_of_an_invalid_row() -> None:
    content = read_fixture("euromillions_3.csv").replace(b"23/09/16", b"23-09-16")
    with pytest.raises(FetchError, match=r"^'euromillions_3\.csv': line 2: invalid draw date"):
        parse_archive(zip_of({"euromillions_3.csv": content}))


def test_parse_archive_quotes_the_name_of_the_file_in_its_messages() -> None:
    # The name comes from the archive: it must not reach a terminal as it is.
    with pytest.raises(FetchError) as error:
        parse_archive(zip_of({"\x1b]0;owned\x07.csv": b"not a csv"}))
    assert "\x1b" not in str(error.value)
    assert "\\x1b]0;owned\\x07.csv" in str(error.value)


# --- discover_archive_urls ------------------------------------------------------------


def test_discover_archive_urls_keeps_the_euromillions_archives_in_page_order() -> None:
    session, transport = fake_session(website())
    # The page lists the newest archive first. Its look-alike links are all rejected and
    # the second link to the newest archive is not repeated.
    assert discover_archive_urls(session, timeout=5) == list(reversed(KNOWN_ARCHIVE_URLS))
    assert transport.urls == [HISTORY_PAGE_URL]
    assert transport.timeouts == [5]


@pytest.mark.parametrize(
    ("anchor", "kept"),
    [
        ('<a title="Historique EuroMillions - My Million" href="{url}">zip</a>', True),
        ('<a aria-label="Historique EUROMILLIONS (nouvel onglet)" href="{url}">zip</a>', True),
        ('<a href=" {url} "><span>Historique</span> <b>euromillions</b></a>', True),
        ('<a href="{url}">Historique</a>', False),
        ('<a title="Historique Loto" href="{url}">zip</a>', False),
        ('<a title="Historique EuroMillions" href="{url}/../x">zip</a>', False),
        ('<a title="Historique EuroMillions" href="{url}?file=1">zip</a>', False),
        ('<a title="Historique EuroMillions" data-href="{url}">zip</a>', False),
    ],
)
def test_discover_archive_urls_needs_an_archive_url_and_a_euromillions_label(
    anchor: str, kept: bool
) -> None:
    url = KNOWN_ARCHIVE_URLS[0].replace("afa8", "ffff")
    page = f"<html><body><p>EuroMillions</p>{anchor.format(url=url)}</body></html>"
    session, _ = fake_session({HISTORY_PAGE_URL: page.encode()})
    assert discover_archive_urls(session) == ([url] if kept else [])


@pytest.mark.parametrize("outcome", [503, 404, requests.ConnectionError("no route to host")])
def test_discover_archive_urls_reports_an_unavailable_page(outcome: Outcome) -> None:
    session, _ = fake_session({HISTORY_PAGE_URL: outcome})
    with pytest.raises(FetchError, match=r"cannot download https://www\.fdj\.fr/"):
        discover_archive_urls(session)


def test_discover_archive_urls_refuses_a_page_with_too_many_archives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = fake_session(website())
    monkeypatch.setattr(fetcher, "_MAX_ARCHIVES", 6)
    assert len(discover_archive_urls(session)) == 6
    monkeypatch.setattr(fetcher, "_MAX_ARCHIVES", 5)
    with pytest.raises(FetchError, match="lists 6 archives"):
        discover_archive_urls(session)


def test_oversized_responses_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher, "_MAX_DOWNLOAD_BYTES", 1000)
    session, _ = fake_session(website())
    with pytest.raises(FetchError, match="response larger than 1000 bytes"):
        discover_archive_urls(session)


def test_endless_responses_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # Whatever the time each wait for data takes, the whole response has a time limit.
    monkeypatch.setattr(fetcher, "_MAX_DOWNLOAD_SECONDS", -1.0)
    session, _ = fake_session(website())
    with pytest.raises(FetchError, match="no complete response within the time allowed"):
        discover_archive_urls(session)


class Trickle(io.BytesIO):
    """A response body of which every block read takes one second of a fake clock."""

    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.seconds = 0.0
        self.served = 0

    def read(self, size: int | None = -1) -> bytes:
        self.seconds += 1.0
        data = super().read(size)
        self.served += len(data)
        return data


def test_a_response_that_trickles_in_is_given_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # The whole response may take two and a half seconds; this one would take thirteen.
    body = Trickle(bytes(100_000))
    monkeypatch.setattr(fetcher, "time", SimpleNamespace(monotonic=lambda: body.seconds))
    monkeypatch.setattr(fetcher, "_MAX_DOWNLOAD_SECONDS", 2.5)
    session, _ = fake_session({HISTORY_PAGE_URL: body})
    with pytest.raises(FetchError, match="no complete response within the time allowed"):
        discover_archive_urls(session)
    # Noticed at the first block that completes past the limit, not at the end.
    assert body.served == 3 * fetcher._BLOCK_BYTES < 100_000


def test_a_response_within_the_time_allowed_is_read_to_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = Trickle(read_fixture("history_page.html"))
    monkeypatch.setattr(fetcher, "time", SimpleNamespace(monotonic=lambda: body.seconds))
    session, _ = fake_session({HISTORY_PAGE_URL: body})
    assert discover_archive_urls(session) == list(reversed(KNOWN_ARCHIVE_URLS))


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_are_refused_not_followed(status: int) -> None:
    elsewhere = "http://mirror.example.net/euromillions.zip"
    session, transport = fake_session(
        {
            HISTORY_PAGE_URL: Redirect(status, elsewhere),
            elsewhere: read_fixture("history_page.html"),
        }
    )
    with pytest.raises(FetchError, match=f"unexpected redirect \\(HTTP {status}\\)"):
        discover_archive_urls(session)
    assert transport.urls == [HISTORY_PAGE_URL]


# --- fetch_draws ----------------------------------------------------------------------


def test_fetch_draws_downloads_every_archive_listed_on_the_history_page(
    fdj_draws: list[Draw],
) -> None:
    session, transport = fake_session(website())
    assert fetch_draws(session, timeout=7) == fdj_draws
    assert transport.urls == [HISTORY_PAGE_URL, *reversed(KNOWN_ARCHIVE_URLS)]
    assert transport.timeouts == [7] * 7


def test_fetch_draws_prefers_the_archives_of_the_page_to_the_known_ones(
    fdj_draws: list[Draw],
) -> None:
    # FDJ reshuffles its files into two archives that the known list does not have.
    new_urls = [KNOWN_ARCHIVE_URLS[-1].replace("afe6", suffix) for suffix in ("aff6", "aff7")]
    page = "".join(
        f'<a title="Historique EuroMillions - My Million" href="{url}">zip</a>' for url in new_urls
    )
    names = list(EXPECTED)
    session, transport = fake_session(
        {
            HISTORY_PAGE_URL: page.encode(),
            new_urls[0]: zip_of({name: read_fixture(name) for name in names[:3]}),
            new_urls[1]: zip_of({name: read_fixture(name) for name in names[3:]}),
        }
    )
    assert fetch_draws(session) == fdj_draws
    assert transport.urls == [HISTORY_PAGE_URL, *new_urls]


@pytest.mark.parametrize(
    "page",
    [503, requests.ConnectionError("no route to host"), b"<html><body>Maintenance</body></html>"],
)
def test_fetch_draws_falls_back_to_the_known_archives(
    page: Outcome, fdj_draws: list[Draw], caplog: pytest.LogCaptureFixture
) -> None:
    session, transport = fake_session({**website(), HISTORY_PAGE_URL: page})
    with caplog.at_level(logging.WARNING, logger="euromillions.fetcher"):
        assert fetch_draws(session) == fdj_draws
    assert transport.urls == [HISTORY_PAGE_URL, *KNOWN_ARCHIVE_URLS]
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert "known archive list" in record.getMessage()


def test_fetch_draws_logs_nothing_when_the_page_lists_the_archives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, _ = fake_session(website())
    with caplog.at_level(logging.DEBUG, logger="euromillions.fetcher"):
        fetch_draws(session)
    assert not caplog.records


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (500, "cannot download"),
        (requests.Timeout("read timed out"), "cannot download"),
        (b"<html>Service unavailable</html>", "not a readable zip archive"),
        (zip_of({"LISEZMOI.txt": b"hello"}), "no CSV file"),
        (zip_of({"draws.csv": csv_file(HEADER, ROW, ROW.replace("41", "32"))}), "line 3"),
    ],
)
def test_fetch_draws_never_returns_a_partial_history(outcome: Outcome, message: str) -> None:
    broken_url = KNOWN_ARCHIVE_URLS[2]
    session, _ = fake_session({**website(), broken_url: outcome})
    with pytest.raises(FetchError, match=message) as error:
        fetch_draws(session)
    assert broken_url in str(error.value)


def test_fetch_draws_rejects_archives_without_any_draw() -> None:
    routes: dict[str, Outcome] = {url: zip_of({"empty.csv": csv_file(HEADER)}) for url in website()}
    routes[HISTORY_PAGE_URL] = read_fixture("history_page.html")
    session, _ = fake_session(routes)
    with pytest.raises(FetchError, match="no draw"):
        fetch_draws(session)


def test_fetch_draws_keeps_the_prizes_when_a_draw_is_listed_twice(fdj_draws: list[Draw]) -> None:
    # The newest draw is also in another archive, without its prizes, before and after.
    routes = website()
    for url in (KNOWN_ARCHIVE_URLS[0], KNOWN_ARCHIVE_URLS[-1]):
        name = ARCHIVE_FILES[url]
        routes[url] = zip_of({"extra.csv": csv_file(HEADER, ROW), name: read_fixture(name)})
    session, _ = fake_session(routes)
    assert fetch_draws(session) == fdj_draws
    assert fetch_draws(session)[-1].prizes == TYPICAL_PRIZES


def test_fetch_draws_rejects_two_different_draws_on_the_same_date() -> None:
    routes = website()
    other = csv_file(HEADER, ROW.replace(";41;", ";40;"))
    routes[KNOWN_ARCHIVE_URLS[0]] = zip_of(
        {"euromillions.csv": read_fixture("euromillions.csv"), "other.csv": other}
    )
    session, _ = fake_session(routes)
    with pytest.raises(FetchError, match="two different draws are dated 2026-11-13"):
        fetch_draws(session)


def test_fetch_draws_accepts_a_complete_history(full_history: list[Draw], strict: None) -> None:
    files = (full_history[:700], full_history[700:1400], full_history[1400:])
    session, _ = fake_session(site_serving(*files))
    assert fetch_draws(session, now=NOW) == full_history


def test_fetch_draws_tolerates_a_draw_that_was_cancelled_or_is_late(
    full_history: list[Draw], strict: None
) -> None:
    # One weekly draw of 2006 and two of 2012 are missing, and so is the latest one.
    history = [*full_history[:100], *full_history[101:500], *full_history[502:-1]]
    session, _ = fake_session(site_serving(history))
    assert fetch_draws(session, now=NOW) == history


def test_fetch_draws_rejects_a_history_that_does_not_start_at_the_first_draw(
    full_history: list[Draw], strict: None
) -> None:
    session, _ = fake_session(site_serving(full_history[1:]))
    with pytest.raises(FetchError, match="starts on 2004-02-20 instead of 2004-02-13"):
        fetch_draws(session, now=NOW)


def test_fetch_draws_rejects_a_history_with_a_hole(full_history: list[Draw], strict: None) -> None:
    # An archive is missing from the page, or reduced to a few rows.
    before, after = full_history[:800], full_history[805:]
    session, _ = fake_session(site_serving(before, after))
    message = f"no draw between {before[-1].date} and {after[0].date}"
    with pytest.raises(FetchError, match=message):
        fetch_draws(session, now=NOW)


def test_fetch_draws_rejects_a_history_that_stops_short(
    full_history: list[Draw], strict: None
) -> None:
    session, _ = fake_session(site_serving(full_history[:-6]))
    with pytest.raises(FetchError, match="ends on 2026-10-23: its latest draws are missing"):
        fetch_draws(session, now=NOW)


def test_fetch_draws_rejects_a_draw_dated_off_schedule(full_history: list[Draw]) -> None:
    thursday = Draw(dt.date(2026, 11, 12), (1, 2, 3, 4, 5), (1, 2))
    session, _ = fake_session(site_serving([*full_history, thursday]))
    with pytest.raises(FetchError, match=r"2026-11-12 \(Thursday\) is not a draw day"):
        fetch_draws(session, now=NOW)
    # Tuesdays only became draw days in May 2011.
    tuesday = Draw(dt.date(2005, 3, 1), (1, 2, 3, 4, 5), (1, 2))
    session, _ = fake_session(site_serving([*full_history, tuesday]))
    with pytest.raises(FetchError, match=r"2005-03-01 \(Tuesday\) is not a draw day"):
        fetch_draws(session, now=NOW)


def test_fetch_draws_rejects_a_draw_dated_in_the_future(full_history: list[Draw]) -> None:
    session, _ = fake_session(site_serving(full_history))
    # The latest draw is dated Friday the 13th: on the Thursday, it cannot have taken place.
    with pytest.raises(FetchError, match="2026-11-13 is in the future"):
        fetch_draws(session, now=paris(2026, 11, 12, 23, 59))
    # The day that counts is that of Paris, where it is still Thursday.
    auckland = dt.datetime(2026, 11, 13, 0, 30, tzinfo=ZoneInfo("Pacific/Auckland"))
    with pytest.raises(FetchError, match="2026-11-13 is in the future"):
        fetch_draws(session, now=auckland)
    assert fetch_draws(session, now=paris(2026, 11, 13, 21, 5)) == full_history


def test_fetch_draws_accepts_a_draw_published_before_its_scheduled_time(
    full_history: list[Draw],
) -> None:
    # 21:05 is only the usual time of the draw: a result out earlier that day is no mistake,
    # and refusing it would make the whole download fail.
    session, _ = fake_session(site_serving(full_history))
    assert fetch_draws(session, now=paris(2026, 11, 13, 20, 55)) == full_history
    assert fetch_draws(session, now=paris(2026, 11, 13, 0, 0)) == full_history


def test_fetch_draws_sends_no_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # requests would otherwise send the login found in ~/.netrc to any host.
    netrc = tmp_path / "netrc"
    netrc.write_text("default login alice password s3cret\n")
    monkeypatch.setenv("NETRC", str(netrc))
    transport = FakeTransport(website())
    monkeypatch.setattr(
        HTTPAdapter, "send", lambda adapter, request, **kwargs: transport.send(request, **kwargs)
    )
    fetch_draws()
    assert len(transport.requests) == 7
    assert not any("Authorization" in request.headers for request in transport.requests)


def test_fetch_draws_uses_a_generic_session_of_its_own(
    fdj_draws: list[Draw], monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeTransport(website())
    adapters: list[HTTPAdapter] = []
    closed: list[requests.Session] = []

    def send(adapter: HTTPAdapter, request: requests.PreparedRequest, **kwargs: Any) -> Any:
        adapters.append(adapter)
        return transport.send(request, **kwargs)

    def close(session: requests.Session) -> None:
        closed.append(session)

    monkeypatch.setattr(HTTPAdapter, "send", send)
    monkeypatch.setattr(requests.Session, "close", close)

    assert fetch_draws(timeout=9) == fdj_draws
    assert len(transport.requests) == 7
    assert transport.timeouts == [9] * 7
    # Nothing in the User-Agent identifies the user or the machine.
    agents = {request.headers["User-Agent"] for request in transport.requests}
    assert agents == {f"euromillions-analyzer/{__version__}"}
    # Transient failures are retried a bounded number of times.
    retries = adapters[0].max_retries
    assert retries.total == 3
    assert set(retries.status_forcelist or ()) == {500, 502, 503, 504}
    # A server cannot make the command wait as long as it likes between two attempts.
    assert retries.respect_retry_after_header is False
    assert len(closed) == 1


# --- merge_draws ----------------------------------------------------------------------


def test_merge_draws_sorts_by_date_and_lets_later_groups_win() -> None:
    monday = Draw(dt.date(2026, 11, 3), (1, 2, 3, 4, 5), (1, 2))
    friday = Draw(dt.date(2026, 11, 6), (6, 7, 8, 9, 10), (3, 4))
    friday_with_prizes = Draw(friday.date, friday.numbers, friday.stars, TYPICAL_PRIZES)
    tuesday = Draw(dt.date(2026, 11, 10), (11, 12, 13, 14, 15), (5, 6))

    merged = merge_draws([tuesday, friday], iter([monday]), (friday_with_prizes,))
    assert merged == [monday, friday_with_prizes, tuesday]
    assert merged[1].prizes == TYPICAL_PRIZES
    assert merge_draws([friday_with_prizes], [friday])[0].prizes == ()
    assert merge_draws([friday, friday_with_prizes]) == [friday_with_prizes]
    assert merge_draws() == []
    assert merge_draws([], ()) == []


# --- load_draws / save_draws ----------------------------------------------------------


def test_cache_round_trip_keeps_the_draws_and_their_prizes(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "data" / "nested" / "draws.json"  # the directories do not exist yet
    save_draws(reversed(fdj_draws), path)
    loaded = load_draws(path)
    assert loaded == fdj_draws
    assert [len(draw.prizes) for draw in loaded] == [12] * 4 + [13] * 17
    assert loaded[-1].prizes == TYPICAL_PRIZES
    assert [path.name] == [entry.name for entry in path.parent.iterdir()]


def test_save_draws_writes_compact_versioned_json(tmp_path: Path) -> None:
    path = tmp_path / "draws.json"
    with_prizes = Draw(dt.date(2026, 11, 10), (15, 18, 31, 37, 41), (1, 5), tiers((1, 141899445)))
    save_draws([NEWEST, with_prizes, NEWEST], path)
    assert path.read_text(encoding="utf-8") == (
        '{"version":1,"draws":['
        '{"date":"2026-11-10","numbers":[15,18,31,37,41],"stars":[1,5],'
        '"prizes":[[1,141899445.0]]},'
        '{"date":"2026-11-13","numbers":[16,27,32,41,45],"stars":[2,12]}]}'
    )
    assert load_draws(path) == [with_prizes, NEWEST]


def test_load_draws_without_cache_file_returns_no_draw(tmp_path: Path) -> None:
    assert load_draws(tmp_path / "missing" / "draws.json") == []


def test_load_draws_sorts_the_draws(tmp_path: Path) -> None:
    path = tmp_path / "draws.json"
    older = Draw(dt.date(2026, 11, 10), (15, 18, 31, 37, 41), (1, 5))
    path.write_text(json.dumps({"version": 1, "draws": [NEWEST.to_dict(), older.to_dict()]}))
    assert load_draws(path) == [older, NEWEST]
    path.write_text('{"version": 1, "draws": []}')
    assert load_draws(path) == []


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not json",
        b'{"version": 1, "draws": [',
        b"\xff\xfe\x00",
        b"[]",
        b'{"draws": []}',
        b'{"version": 2, "draws": []}',
        b'{"version": 1}',
        b'{"version": 1, "draws": {}}',
        b'{"version": 1, "draws": [42]}',
        b'{"version": 1, "draws": [{"date": "2026-11-13", "numbers": [16, 27, 32, 41, 45]}]}',
        b'{"version": 1, "draws": [{"date": "13/11/2026", "numbers": [16, 27, 32, 41, 45],'
        b' "stars": [2, 12]}]}',
        b'{"version": 1, "draws": [{"date": "2026-11-13", "numbers": [16, 16, 32, 41, 45],'
        b' "stars": [2, 12]}]}',
        b'{"version": 1, "draws": [{"date": "2026-11-13", "numbers": [16, 27, 32, 41, 45],'
        b' "stars": [2, 12], "prizes": [[1, -5.0]]}]}',
        # Figures and nesting that no real cache has.
        b'{"version": 1, "draws": [{"date": "2026-11-13", "numbers": [16, 27, 32, 41, 45],'
        b' "stars": [2, 12], "prizes": [[1' + b"0" * 400 + b", 5.0]]}]}",
        b'{"version": 1, "draws": [{"date": "2026-11-13", "numbers": [16, 27, 32, 41, 45],'
        b' "stars": [2, 12], "prizes": [[1, 1e999]]}]}',
        b"[" * 200_000,
    ],
)
def test_load_draws_rejects_a_corrupt_cache(content: bytes, tmp_path: Path) -> None:
    path = tmp_path / "draws.json"
    path.write_bytes(content)
    with pytest.raises(CorruptCacheError) as error:
        load_draws(path)
    assert str(path) in str(error.value)


def test_load_draws_rejects_an_unreadable_cache(tmp_path: Path) -> None:
    with pytest.raises(CacheError, match="cannot read") as error:
        load_draws(tmp_path)  # a directory
    # Nothing says the content is wrong: deleting the "file" would not be the way out.
    assert not isinstance(error.value, CorruptCacheError)


def test_save_draws_replaces_the_cache_only_once_fully_written(
    fdj_draws: list[Draw], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "draws.json"
    save_draws(fdj_draws[:3], path)
    before = path.read_bytes()

    def fail(source: object, target: object) -> None:
        assert Path(str(target)) == path
        assert Path(str(source)).parent == path.parent  # the same directory: an atomic rename
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        with pytest.raises(CacheError, match=r"cannot write the draw cache .*disk full"):
            save_draws(fdj_draws, path)
    assert path.read_bytes() == before
    assert [entry.name for entry in tmp_path.iterdir()] == ["draws.json"]  # no temporary file

    save_draws(fdj_draws, path)
    assert load_draws(path) == fdj_draws
    assert [entry.name for entry in tmp_path.iterdir()] == ["draws.json"]


def test_save_draws_reports_a_cache_that_cannot_be_written(tmp_path: Path) -> None:
    (tmp_path / "data").write_text("a file where the directory should be")
    with pytest.raises(CacheError, match="cannot write the draw cache") as error:
        save_draws([NEWEST], tmp_path / "data" / "draws.json")
    assert not isinstance(error.value, CorruptCacheError)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_save_draws_creates_the_cache_like_any_other_file(tmp_path: Path) -> None:
    umask = os.umask(0)
    os.umask(umask)
    path = tmp_path / "draws.json"
    save_draws([NEWEST], path)
    assert path.stat().st_mode & 0o777 == 0o666 & ~umask  # not private to the user
    # Permissions chosen by the user survive the next update.
    path.chmod(0o640)
    save_draws([NEWEST], path)
    assert path.stat().st_mode & 0o777 == 0o640


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges on Windows")
def test_save_draws_writes_through_a_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere" / "draws.json"
    target.parent.mkdir()
    target.write_text("old")
    link = tmp_path / "draws.json"
    link.symlink_to(target)
    save_draws([NEWEST], link)
    assert link.is_symlink()
    assert load_draws(target) == load_draws(link) == [NEWEST]


# --- update_cache ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "now",
    [
        paris(2026, 11, 13, 20, 55),  # the draw of that Friday was published a little early
        paris(2026, 11, 13, 21, 5),  # the draw of Friday the 13th has just taken place
        paris(2026, 11, 14),
        paris(2026, 11, 17, 21, 4),  # a minute before the next draw
    ],
)
def test_update_cache_makes_no_request_when_the_cache_is_fresh(
    now: dt.datetime, fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    save_draws(fdj_draws, path)
    session, transport = fake_session(website())
    assert update_cache(path, session=session, now=now) == UpdateResult(fdj_draws, 0, 0, False)
    assert transport.requests == []


def test_update_cache_fills_an_empty_cache(fdj_draws: list[Draw], tmp_path: Path) -> None:
    path = tmp_path / "data" / "draws.json"
    session, transport = fake_session(website())
    result = update_cache(path, session=session, now=NOW)
    assert result == UpdateResult(fdj_draws, 21, 0, True)
    assert len(transport.requests) == 7
    assert load_draws(path) == fdj_draws


def test_update_cache_adds_the_missing_draws_to_a_stale_cache(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    save_draws(fdj_draws[:-2], path)  # ends in April 2026
    session, _ = fake_session(website())
    result = update_cache(path, session=session, now=NOW)
    assert result == UpdateResult(fdj_draws, 2, 0, True)
    assert load_draws(path) == fdj_draws


def test_update_cache_downloads_again_once_a_new_draw_has_taken_place(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    save_draws(fdj_draws, path)
    session, transport = fake_session(website())
    # Tuesday the 17th at draw time: FDJ has not published that draw yet.
    result = update_cache(path, session=session, now=paris(2026, 11, 17, 21, 5))
    assert result == UpdateResult(fdj_draws, 0, 0, True)
    assert len(transport.requests) == 7


def test_update_cache_downloads_again_while_the_latest_draw_lacks_its_prizes(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    save_draws([*fdj_draws[:-1], NEWEST], path)  # up to date, but for the prizes of the 13th
    session, transport = fake_session(website())
    result = update_cache(path, session=session, now=NOW)
    assert result == UpdateResult(fdj_draws, 0, 1, True)
    assert len(transport.requests) == 7
    assert load_draws(path)[-1].prizes == TYPICAL_PRIZES


def test_update_cache_replaces_what_the_download_does_not_confirm(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    # A draw that the source has dropped since (a row once misdated, say), another that it
    # has corrected, and the newest one cached before its prizes were known.
    dropped = Draw(dt.date(2020, 2, 7), (1, 2, 3, 4, 5), (1, 2))
    corrected = Draw(dt.date(2026, 11, 10), (1, 2, 3, 4, 5), (1, 2), TYPICAL_PRIZES)
    save_draws([dropped, corrected, NEWEST], path)
    session, _ = fake_session(website())
    result = update_cache(path, session=session, now=paris(2026, 11, 17, 22))
    assert result == UpdateResult(fdj_draws, added=19, updated=2, downloaded=True)
    assert result.draws[-1].prizes == TYPICAL_PRIZES
    assert load_draws(path) == fdj_draws


def test_update_cache_keeps_the_prizes_that_the_source_no_longer_gives(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    save_draws(fdj_draws, path)
    # The newest archive comes back with a blank prize cell in each of its rows.
    newest_url, name = KNOWN_ARCHIVE_URLS[-1], "euromillions_202002.csv"
    lines = read_fixture(name).split(b"\n")
    column = lines[0].decode("utf-8").split(";").index("rapport_du_rang13_Euro_Millions")
    blanked = [lines[0]]
    for line in lines[1:]:
        cells = line.split(b";")
        if len(cells) > column:
            cells[column] = b""
        blanked.append(b";".join(cells))
    session, _ = fake_session({**website(), newest_url: zip_of({name: b"\n".join(blanked)})})
    assert [len(draw.prizes) for draw in fetch_draws(session, now=NOW)[-5:]] == [0] * 5
    result = update_cache(path, force=True, session=session, now=NOW)
    assert result == UpdateResult(fdj_draws, 0, 0, True)
    assert load_draws(path) == fdj_draws


def test_update_cache_keeps_a_recent_draw_that_the_source_does_not_have_yet(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    # Cached from a source that was ahead: Tuesday the 17th, and an impossible later date.
    tuesday = Draw(dt.date(2026, 11, 17), (1, 2, 3, 4, 5), (1, 2), TYPICAL_PRIZES)
    ghost = Draw(dt.date(2026, 11, 20), (1, 2, 3, 4, 5), (1, 2), TYPICAL_PRIZES)
    save_draws([*fdj_draws, tuesday, ghost], path)
    session, _ = fake_session(website())
    result = update_cache(path, force=True, session=session, now=paris(2026, 11, 18))
    assert result == UpdateResult([*fdj_draws, tuesday], 0, 0, True)
    assert load_draws(path) == [*fdj_draws, tuesday]


def test_update_cache_downloads_again_when_the_cache_has_a_hole(
    full_history: list[Draw], strict: None, tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    priced = [Draw(d.date, d.numbers, d.stars, TYPICAL_PRIZES) for d in full_history]
    save_draws(priced, path)
    session, transport = fake_session(site_serving(full_history))
    # Complete and up to date: nothing to download.
    assert update_cache(path, session=session, now=NOW) == UpdateResult(priced, 0, 0, False)
    assert transport.requests == []
    # Its newest draw is there, but some forty earlier ones are not.
    save_draws([*priced[:1200], *priced[1240:]], path)
    result = update_cache(path, session=session, now=NOW)
    assert (result.added, result.updated, result.downloaded) == (40, 0, True)
    assert [draw.date for draw in load_draws(path)] == [draw.date for draw in full_history]


def test_update_cache_can_be_forced(fdj_draws: list[Draw], tmp_path: Path) -> None:
    path = tmp_path / "draws.json"
    save_draws(fdj_draws, path)
    session, transport = fake_session(website())
    result = update_cache(path, force=True, session=session, now=NOW)
    assert result == UpdateResult(fdj_draws, 0, 0, True)
    assert len(transport.requests) == 7


@pytest.mark.parametrize(
    "outcome", [503, requests.ConnectionError("no route to host"), b"not a zip archive"]
)
def test_update_cache_leaves_the_cache_untouched_when_the_download_fails(
    outcome: Outcome, fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    save_draws(fdj_draws[:5], path)
    before = path.read_bytes()
    session, _ = fake_session({**website(), KNOWN_ARCHIVE_URLS[-1]: outcome})
    with pytest.raises(FetchError):
        update_cache(path, session=session, now=NOW)
    assert path.read_bytes() == before
    assert [entry.name for entry in tmp_path.iterdir()] == ["draws.json"]


def test_update_cache_leaves_the_cache_untouched_when_the_download_is_incomplete(
    full_history: list[Draw], strict: None, tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    save_draws(full_history[:-3], path)
    before = path.read_bytes()
    # The source only has the last few years left.
    session, _ = fake_session(site_serving(full_history[1500:]))
    with pytest.raises(FetchError, match="instead of 2004-02-13"):
        update_cache(path, session=session, now=NOW)
    assert path.read_bytes() == before


def test_update_cache_reports_a_corrupt_cache_before_downloading(tmp_path: Path) -> None:
    path = tmp_path / "draws.json"
    path.write_text("{")
    session, transport = fake_session(website())
    with pytest.raises(CorruptCacheError):
        update_cache(path, force=True, session=session)
    assert transport.requests == []
    assert path.read_text() == "{"


# --- The real thing -------------------------------------------------------------------


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("EUROMILLIONS_LIVE_TESTS") != "1",
    reason="downloads the real history: set EUROMILLIONS_LIVE_TESTS=1 to run it",
)
def test_live_download_of_the_real_history(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="euromillions.fetcher"):
        draws = fetch_draws()
    assert not caplog.records, "the archives were not found on the history page"

    assert len(draws) >= 1998
    dates = [draw.date for draw in draws]
    assert dates == sorted(set(dates))
    assert dates[0] == dt.date(2004, 2, 13)

    (friday,) = (draw for draw in draws if draw.date == dt.date(2026, 11, 13))
    assert friday.numbers == (16, 27, 32, 41, 45)
    assert friday.stars == (2, 12)
    assert len(friday.prizes) == 13
    assert friday.prizes[0] == PrizeTier(0, 0.0)
    assert friday.prizes[1] == PrizeTier(4, 175411.70)
    assert friday.prizes[12] == PrizeTier(1035060, 4.30)
    assert all(len(draw.prizes) == 13 for draw in draws if draw.date >= dt.date(2016, 9, 27))
