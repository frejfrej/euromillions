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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import requests
from requests.adapters import BaseAdapter, HTTPAdapter

from euromillions import __version__, fetcher
from euromillions.fetcher import (
    HISTORY_PAGE_URL,
    KNOWN_ARCHIVE_URLS,
    CacheError,
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
from euromillions.models import Draw, PrizeTier
from euromillions.schedule import DRAW_TIMEZONE

from .conftest import TYPICAL_PRIZES

FIXTURES = Path(__file__).parent / "fixtures"

#: What a fake website answers: the body of a 200 response, an HTTP error status, or an
#: exception to raise.
Outcome = bytes | int | Exception


def read_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def tiers(*pairs: tuple[int, float]) -> tuple[PrizeTier, ...]:
    return tuple(PrizeTier(winners, prize) for winners, prize in pairs)


def paris(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=DRAW_TIMEZONE)


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
#: The same with two prize ranks, under their names before September 2016.
PRIZE_HEADER = (
    HEADER + ";nombre_de_gagnant_au_rang1_en_europe;rapport_du_rang1"
    ";nombre_de_gagnant_au_rang2_en_europe;rapport_du_rang2"
)


def csv_file(*lines: str) -> bytes:
    return "\n".join(lines).encode() + b"\n"


def zip_of(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def fdj_draws() -> list[Draw]:
    """Every draw of the fixtures, oldest first (the parser itself is checked separately)."""
    draws = merge_draws(*(parse_csv(read_fixture(name)) for name in EXPECTED))
    assert [draw.date for draw in draws] == ALL_DATES
    return draws


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
        response.status_code = outcome if isinstance(outcome, int) else 200
        response.raw = io.BytesIO(b"" if isinstance(outcome, int) else outcome)
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
    draws = parse_csv(csv_file(PRIZE_HEADER, ROW + ";1;17000000,5;3;250000"))
    assert draws == [
        Draw(NEWEST.date, NEWEST.numbers, NEWEST.stars, tiers((1, 17000000.5), (3, 250000)))
    ]


@pytest.mark.parametrize("cells", ["1;17000000,5;3;", "1;17000000,5;;250000", ";;;", "1"])
def test_parse_csv_drops_the_prizes_of_a_row_with_a_blank_prize_cell(cells: str) -> None:
    # The last case is a row that stops short of its prize cells.
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
    with pytest.raises(FetchError, match=r"line 3: invalid (number|amount)"):
        parse_csv(csv_file(PRIZE_HEADER, ROW + ";1;17000000,5;3;250000", f"{ROW};{cells}"))


def test_parse_csv_takes_only_the_european_euromillions_ranks() -> None:
    # None of these look-alikes is a prize rank of the main game.
    header = (
        HEADER + ";nombre_de_gagnant_au_rang1_en_france;nombre_de_gagnant_au_rang10_en_europe"
        ";rapport_du_rang10;nombre_de_gagnant_au_rang1_Etoile+;rapport_du_rang1_Etoile+"
        ";nombre_de_gagnant_au_rang1_Euro_Millions_en_france"
    )
    assert parse_csv(csv_file(header, ROW + ";7;8;9,5;3;4,5;6")) == [NEWEST]


def test_parse_csv_stops_at_the_first_rank_lacking_a_column() -> None:
    header = (
        HEADER + ";nombre_de_gagnant_au_rang1_Euro_Millions_en_europe"
        ";rapport_du_rang1_Euro_Millions;nombre_de_gagnant_au_rang2_Euro_Millions_en_europe"
        ";nombre_de_gagnant_au_rang3_Euro_Millions_en_europe;rapport_du_rang3_Euro_Millions"
    )
    (draw,) = parse_csv(csv_file(header, ROW + ";2;8500000;4;5;6,5"))
    assert draw.prizes == tiers((2, 8500000))


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


# --- parse_archive --------------------------------------------------------------------


def test_parse_archive_reads_the_csv_file_of_an_archive() -> None:
    content = read_fixture("euromillions_3.csv")
    assert parse_archive(zip_of({"euromillions_3.csv": content})) == parse_csv(content)


def test_parse_archive_reads_every_csv_file_and_nothing_else() -> None:
    first, second = read_fixture("euromillions_3.csv"), read_fixture("euromillions_4.csv")
    archive = zip_of({"LISEZMOI.txt": b"hello", "a/one.csv": first, "TWO.CSV": second})
    assert parse_archive(archive) == parse_csv(first) + parse_csv(second)


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
    with pytest.raises(FetchError, match=r"euromillions\.csv: larger than \d+ bytes"):
        parse_archive(archive)


def test_parse_archive_names_the_file_and_line_of_an_invalid_row() -> None:
    content = read_fixture("euromillions_3.csv").replace(b"23/09/16", b"23-09-16")
    with pytest.raises(FetchError, match=r"^euromillions_3\.csv: line 2: invalid draw date"):
        parse_archive(zip_of({"euromillions_3.csv": content}))


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


def test_oversized_responses_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher, "_MAX_DOWNLOAD_BYTES", 1000)
    session, _ = fake_session(website())
    with pytest.raises(FetchError, match="response larger than 1000 bytes"):
        discover_archive_urls(session)


# --- fetch_draws ----------------------------------------------------------------------


def test_fetch_draws_downloads_every_archive_listed_on_the_history_page(
    fdj_draws: list[Draw],
) -> None:
    session, transport = fake_session(website())
    assert fetch_draws(session, timeout=7) == fdj_draws
    assert transport.urls == [HISTORY_PAGE_URL, *reversed(KNOWN_ARCHIVE_URLS)]
    assert transport.timeouts == [7] * 7


def test_fetch_draws_prefers_the_archives_of_the_page_to_the_known_ones() -> None:
    new_url = KNOWN_ARCHIVE_URLS[-1].replace("afe6", "aff6")
    page = f'<a title="Historique EuroMillions - My Million" href="{new_url}">zip</a>'
    content = read_fixture("euromillions_202002.csv")
    session, transport = fake_session(
        {HISTORY_PAGE_URL: page.encode(), new_url: zip_of({"new.csv": content})}
    )
    assert fetch_draws(session) == sorted(parse_csv(content), key=lambda draw: draw.date)
    assert transport.urls == [HISTORY_PAGE_URL, new_url]


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
    ],
)
def test_load_draws_rejects_a_corrupt_cache(content: bytes, tmp_path: Path) -> None:
    path = tmp_path / "draws.json"
    path.write_bytes(content)
    with pytest.raises(CacheError) as error:
        load_draws(path)
    assert str(path) in str(error.value)


def test_load_draws_rejects_an_unreadable_cache(tmp_path: Path) -> None:
    with pytest.raises(CacheError, match="cannot read"):
        load_draws(tmp_path)  # a directory


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
        with pytest.raises(OSError, match="disk full"):
            save_draws(fdj_draws, path)
    assert path.read_bytes() == before
    assert [entry.name for entry in tmp_path.iterdir()] == ["draws.json"]  # no temporary file

    save_draws(fdj_draws, path)
    assert load_draws(path) == fdj_draws
    assert [entry.name for entry in tmp_path.iterdir()] == ["draws.json"]


# --- update_cache ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "now",
    [
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
    assert update_cache(path, session=session, now=now) == UpdateResult(fdj_draws, 0, False)
    assert transport.requests == []


def test_update_cache_fills_an_empty_cache(fdj_draws: list[Draw], tmp_path: Path) -> None:
    path = tmp_path / "data" / "draws.json"
    session, transport = fake_session(website())
    result = update_cache(path, session=session, now=paris(2026, 11, 14))
    assert result == UpdateResult(fdj_draws, 21, True)
    assert len(transport.requests) == 7
    assert load_draws(path) == fdj_draws


def test_update_cache_adds_the_missing_draws_to_a_stale_cache(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    save_draws(fdj_draws[:-2], path)  # ends in April 2026
    session, _ = fake_session(website())
    result = update_cache(path, session=session, now=paris(2026, 11, 14))
    assert result == UpdateResult(fdj_draws, 2, True)
    assert load_draws(path) == fdj_draws


def test_update_cache_downloads_again_once_a_new_draw_has_taken_place(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    save_draws(fdj_draws, path)
    session, transport = fake_session(website())
    # Tuesday the 17th at draw time: FDJ has not published that draw yet.
    result = update_cache(path, session=session, now=paris(2026, 11, 17, 21, 5))
    assert result == UpdateResult(fdj_draws, 0, True)
    assert len(transport.requests) == 7


def test_update_cache_downloads_again_while_the_latest_draw_lacks_its_prizes(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    save_draws([*fdj_draws[:-1], NEWEST], path)  # up to date, but for the prizes of the 13th
    session, transport = fake_session(website())
    result = update_cache(path, session=session, now=paris(2026, 11, 14))
    assert result == UpdateResult(fdj_draws, 0, True)
    assert len(transport.requests) == 7
    assert load_draws(path)[-1].prizes == TYPICAL_PRIZES


def test_update_cache_merges_the_download_into_the_cache(
    fdj_draws: list[Draw], tmp_path: Path
) -> None:
    path = tmp_path / "draws.json"
    only_cached = Draw(dt.date(2020, 2, 7), (1, 2, 3, 4, 5), (1, 2))
    save_draws([only_cached, NEWEST], path)  # the newest draw was cached without its prizes
    session, _ = fake_session(website())
    result = update_cache(path, session=session, now=paris(2026, 11, 17, 22))
    assert result.downloaded
    assert result.added == 20
    assert result.draws == merge_draws(fdj_draws, [only_cached])
    assert result.draws[-1].prizes == TYPICAL_PRIZES
    assert load_draws(path) == result.draws


def test_update_cache_can_be_forced(fdj_draws: list[Draw], tmp_path: Path) -> None:
    path = tmp_path / "draws.json"
    save_draws(fdj_draws, path)
    session, transport = fake_session(website())
    result = update_cache(path, force=True, session=session, now=paris(2026, 11, 14))
    assert result == UpdateResult(fdj_draws, 0, True)
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
        update_cache(path, session=session, now=paris(2026, 11, 14))
    assert path.read_bytes() == before
    assert [entry.name for entry in tmp_path.iterdir()] == ["draws.json"]


def test_update_cache_reports_a_corrupt_cache_before_downloading(tmp_path: Path) -> None:
    path = tmp_path / "draws.json"
    path.write_text("{")
    session, transport = fake_session(website())
    with pytest.raises(CacheError):
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
