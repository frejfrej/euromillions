"""Download, parse and cache the official EuroMillions draw history.

The French operator (FDJ) publishes every draw since 2004 as a handful of zipped
CSV files linked from its history page. :func:`update_cache` downloads them when
the local JSON cache may be missing a draw.

Unexpected data raises :class:`FetchError` instead of being skipped: a history
with missing draws would silently bias every statistic computed from it.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import io
import itertools
import json
import logging
import os
import re
import tempfile
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from euromillions import __version__, schedule
from euromillions.models import Draw, PrizeTier

logger = logging.getLogger(__name__)

HISTORY_PAGE_URL = "https://www.fdj.fr/jeux-de-tirage/euromillions-my-million/historique"

_ARCHIVE_URL_PREFIX = "https://www.sto.api.fdj.fr/anonymous/service-draw-info/v3/documentations/"
_ARCHIVE_URL = re.compile(re.escape(_ARCHIVE_URL_PREFIX) + r"[0-9A-Za-z-]+")

#: The archives linked from the history page in November 2026, oldest first. They are only
#: a fallback for when the page cannot be read: FDJ starts a new archive now and then.
KNOWN_ARCHIVE_URLS: tuple[str, ...] = tuple(
    _ARCHIVE_URL_PREFIX + archive_id
    for archive_id in (
        "1a2b3c4d-9876-4562-b3fc-2c963f66afa8",  # February 2004 to May 2011
        "1a2b3c4d-9876-4562-b3fc-2c963f66afa9",  # May 2011 to January 2014
        "1a2b3c4d-9876-4562-b3fc-2c963f66afb6",  # February 2014 to September 2016
        "1a2b3c4d-9876-4562-b3fc-2c963f66afc6",  # September 2016 to February 2019
        "1a2b3c4d-9876-4562-b3fc-2c963f66afd6",  # March 2019 to January 2020
        "1a2b3c4d-9876-4562-b3fc-2c963f66afe6",  # February 2020 onwards
    )
)

DEFAULT_DATA_DIR = Path("data")
CACHE_FILENAME = "draws.json"
_CACHE_VERSION = 1

#: Largest HTTP response accepted; the history page weighs 0.8 MB and the archives 0.1 MB.
_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
#: Largest CSV file accepted once decompressed; the real ones stay under 0.3 MB.
_MAX_CSV_BYTES = 20 * 1024 * 1024

_DATE_COLUMN = "date_de_tirage"
_NUMBER_COLUMNS = ("boule_1", "boule_2", "boule_3", "boule_4", "boule_5")
_STAR_COLUMNS = ("etoile_1", "etoile_2")
#: The ``(winners, prize)`` columns of prize rank ``{rank}``, as named before and since
#: September 2016. They have to be matched exactly, because of look-alikes such as
#: ``nombre_de_gagnant_au_rang1_en_france`` and ``rapport_du_rang1_Etoile+``.
_PRIZE_COLUMNS = (
    ("nombre_de_gagnant_au_rang{rank}_en_europe", "rapport_du_rang{rank}"),
    (
        "nombre_de_gagnant_au_rang{rank}_Euro_Millions_en_europe",
        "rapport_du_rang{rank}_Euro_Millions",
    ),
)

#: The layouts of ``date_de_tirage``. The patterns are needed because ``strptime`` alone
#: is lenient about the number of digits.
_DATE_LAYOUTS = (
    (re.compile(r"[0-9]{8}"), "%Y%m%d"),  # 20040213, in the oldest archive
    (re.compile(r"[0-9]{2}/[0-9]{2}/[0-9]{4}"), "%d/%m/%Y"),  # 13/05/2011
    (re.compile(r"[0-9]{2}/[0-9]{2}/[0-9]{2}"), "%d/%m/%y"),  # 23/09/16, a single draw
)
#: An amount in euros. The cents follow a comma or, in a few cells, a point; allowing at
#: most two of them keeps a thousands separator from being read as a decimal mark.
_AMOUNT = re.compile(r"[0-9]+([,.][0-9]{1,2})?")


class FetchError(Exception):
    """The draw history could not be downloaded or parsed."""


class CacheError(Exception):
    """The local cache file is unreadable or corrupt."""


def parse_csv(content: bytes) -> list[Draw]:
    """Parse one of FDJ's history files and return its draws in file order.

    The files are ``;``-separated and their columns changed over the years, so fields are
    looked up by column name. Each draw comes with the prize tiers of every rank the file
    has columns for, or with none if its row leaves any of them blank. Blank lines and
    surplus trailing fields are ignored; anything else that does not yield a valid draw
    raises :class:`FetchError` naming its line.
    """
    # QUOTE_NONE guarantees one row per line. FDJ never quotes fields, and honouring a stray
    # quote character would silently merge all the lines up to the next one into a single row.
    reader = csv.reader(
        io.StringIO(_decode(content), newline=""), delimiter=";", quoting=csv.QUOTE_NONE
    )
    header: list[str] = []
    prize_columns: list[tuple[str, str]] = []
    draws: list[Draw] = []
    try:
        for row in reader:
            cells = [cell.strip() for cell in row]
            if not any(cells):
                continue
            if not header:
                _check_header(cells)
                header, prize_columns = cells, _prize_columns(cells)
            else:
                # Not strict: a row may have surplus trailing fields, or lack its last ones.
                record = dict(zip(header, cells, strict=False))
                draws.append(_parse_row(record, prize_columns))
    except (csv.Error, ValueError) as exc:
        raise FetchError(f"line {reader.line_num}: {exc}") from exc
    if not header:
        raise FetchError("the file has no header row")
    return draws


def _decode(content: bytes) -> str:
    """Decode a history file: most are UTF-8, those of 2016-2020 are Windows-1252."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FetchError("the file is neither UTF-8 nor Windows-1252 text")


def _check_header(header: list[str]) -> None:
    missing = [
        name for name in (_DATE_COLUMN, *_NUMBER_COLUMNS, *_STAR_COLUMNS) if name not in header
    ]
    if missing:
        raise ValueError(f"missing column(s) {', '.join(missing)}")


def _prize_columns(header: list[str]) -> list[tuple[str, str]]:
    """Return the ``(winners, prize)`` column names of every prize rank, best rank first.

    The ranks are numbered from 1 and stop at the first one lacking either column.
    """
    names = set(header)
    for winners, prize in _PRIZE_COLUMNS:
        ranks = ((winners.format(rank=k), prize.format(rank=k)) for k in itertools.count(1))
        columns = list(itertools.takewhile(names.issuperset, ranks))
        if columns:
            return columns
    return []


def _parse_row(record: Mapping[str, str], prize_columns: Sequence[tuple[str, str]]) -> Draw:
    """Build a draw from a row given as ``{column name: cell}``.

    A row shorter than the header lacks its last columns, which then count as blank.
    """
    return Draw(
        date=_parse_date(record.get(_DATE_COLUMN, "")),
        numbers=tuple(_parse_int(record.get(name, "")) for name in _NUMBER_COLUMNS),
        stars=tuple(_parse_int(record.get(name, "")) for name in _STAR_COLUMNS),
        prizes=_parse_prizes(record, prize_columns),
    )


def _parse_prizes(
    record: Mapping[str, str], columns: Sequence[tuple[str, str]]
) -> tuple[PrizeTier, ...]:
    """Return the prize tiers of a row, best rank first, or ``()`` if they are incomplete.

    It is all or nothing, as a list with a gap would shift the ranks: a single blank cell
    (FDJ may publish a draw before its prizes) discards them all, the draw itself staying
    valid. Cells that are filled in are parsed regardless, so that a malformed one is
    always reported.
    """
    counts = [_parse_int(cell) for name, _ in columns if (cell := record.get(name, ""))]
    amounts = [_parse_amount(cell) for _, name in columns if (cell := record.get(name, ""))]
    if len(counts) < len(columns) or len(amounts) < len(columns):
        return ()
    return tuple(map(PrizeTier, counts, amounts))


def _parse_date(text: str) -> dt.date:
    for pattern, layout in _DATE_LAYOUTS:
        if pattern.fullmatch(text):
            with contextlib.suppress(ValueError):
                return dt.datetime.strptime(text, layout).date()
    raise ValueError(f"invalid draw date {text!r}")


def _parse_int(text: str) -> int:
    # int() alone would also accept signs, underscores and non-ASCII digits.
    if not (text.isascii() and text.isdigit()):
        raise ValueError(f"invalid number {text!r}")
    return int(text)


def _parse_amount(text: str) -> float:
    """Parse an amount in euros such as ``175411,70``."""
    if not _AMOUNT.fullmatch(text):
        raise ValueError(f"invalid amount {text!r}")
    return float(text.replace(",", "."))


def parse_archive(content: bytes) -> list[Draw]:
    """Parse every CSV file of a zip archive downloaded from FDJ.

    Raises :class:`FetchError` if ``content`` is not a zip archive, holds no CSV file, or
    holds one that is invalid or suspiciously large.
    """
    draws: list[Draw] = []
    found = False
    for name, data in _csv_files(content):
        found = True
        if len(data) > _MAX_CSV_BYTES:
            raise FetchError(f"{name}: larger than {_MAX_CSV_BYTES} bytes once decompressed")
        try:
            draws.extend(parse_csv(data))
        except FetchError as exc:
            raise FetchError(f"{name}: {exc}") from exc
    if not found:
        raise FetchError("the archive contains no CSV file")
    return draws


def _csv_files(content: bytes) -> Iterator[tuple[str, bytes]]:
    """Yield ``(name, data)`` for each CSV file of a zip archive.

    No file is inflated beyond ``_MAX_CSV_BYTES + 1`` bytes, whatever size the archive
    declares for it: the extra byte lets the caller recognise a decompression bomb.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for info in archive.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".csv"):
                    continue
                with archive.open(info) as member:
                    data = member.read(_MAX_CSV_BYTES + 1)
                yield info.filename, data
    except Exception as exc:
        # zipfile reports a damaged or unsupported archive with many unrelated exception
        # types: BadZipFile, zlib.error, NotImplementedError, ValueError, OSError...
        raise FetchError(f"not a readable zip archive ({exc})") from exc


def discover_archive_urls(session: requests.Session, *, timeout: float = 30.0) -> list[str]:
    """Return the URLs of the history archives linked from FDJ's history page, in page order.

    A link is kept if it points to a file of FDJ's draw information service, over HTTPS,
    and its title, ARIA label or text mentions EuroMillions: the same service publishes
    the archives of other games.
    """
    soup = BeautifulSoup(_download(session, HISTORY_PAGE_URL, timeout=timeout), "html.parser")
    urls = [
        url
        for anchor in soup.select("a[href]")
        if _ARCHIVE_URL.fullmatch(url := _attribute(anchor, "href").strip())
        and "euromillions" in _label(anchor).casefold()
    ]
    return list(dict.fromkeys(urls))


def _attribute(tag: Tag, name: str) -> str:
    value = tag.get(name)
    return value if isinstance(value, str) else ""


def _label(anchor: Tag) -> str:
    """Return everything a link is labelled with: its title, ARIA label and text."""
    return " ".join(
        (_attribute(anchor, "title"), _attribute(anchor, "aria-label"), anchor.get_text(" "))
    )


def fetch_draws(session: requests.Session | None = None, *, timeout: float = 30.0) -> list[Draw]:
    """Download the complete draw history, oldest draw first.

    The archives are those linked from FDJ's history page or, if that page is unavailable
    or lists none, :data:`KNOWN_ARCHIVE_URLS`. A partial history is never returned: if any
    archive cannot be downloaded or parsed, :class:`FetchError` is raised.

    ``timeout`` applies to each request. A ``session`` passed in is used as it is; without
    one, a temporary session is created that sends a generic User-Agent and retries
    transient failures.
    """
    if session is None:
        with _new_session() as own_session:
            return fetch_draws(own_session, timeout=timeout)
    groups: list[list[Draw]] = []
    for url in _archive_urls(session, timeout):
        content = _download(session, url, timeout=timeout)
        try:
            groups.append(parse_archive(content))
        except FetchError as exc:
            raise FetchError(f"{url}: {exc}") from exc
    draws = merge_draws(*groups)
    if not draws:
        raise FetchError("the history archives hold no draw")
    return draws


def _new_session() -> requests.Session:
    session = requests.Session()
    # Deliberately generic: nothing that identifies the user or the machine.
    session.headers["User-Agent"] = f"euromillions-analyzer/{__version__}"
    # Up to three more attempts, 0.5 to 2 seconds apart, after a connection error or 5xx.
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 503, 504),
        respect_retry_after_header=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _archive_urls(session: requests.Session, timeout: float) -> list[str]:
    try:
        urls = discover_archive_urls(session, timeout=timeout)
    except FetchError as exc:
        logger.warning("Falling back to the known archive list: %s", exc)
        return list(KNOWN_ARCHIVE_URLS)
    if not urls:
        logger.warning(
            "Falling back to the known archive list: no archive link found on %s",
            HISTORY_PAGE_URL,
        )
        return list(KNOWN_ARCHIVE_URLS)
    return urls


def _download(session: requests.Session, url: str, *, timeout: float) -> bytes:
    """GET ``url`` and return the response body, refusing oversized responses."""
    body = bytearray()
    try:
        with session.get(url, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                body += chunk
                if len(body) > _MAX_DOWNLOAD_BYTES:
                    raise FetchError(f"{url}: response larger than {_MAX_DOWNLOAD_BYTES} bytes")
    except requests.RequestException as exc:
        raise FetchError(f"cannot download {url}: {exc}") from exc
    return bytes(body)


def merge_draws(*groups: Iterable[Draw]) -> list[Draw]:
    """Merge groups of draws into a single history, oldest draw first.

    The result has one draw per date: a draw from a later group replaces the draw of the
    same date from an earlier group.
    """
    by_date = {draw.date: draw for group in groups for draw in group}
    return [by_date[date] for date in sorted(by_date)]


def load_draws(path: Path) -> list[Draw]:
    """Read the cached history, oldest draw first; a missing file is an empty history.

    Raises :class:`CacheError` if the file cannot be read or is not a valid cache.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise CacheError(f"cannot read the draw cache {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("draws"), list):
        raise CacheError(f"the draw cache {path} does not have the expected structure")
    if payload.get("version") != _CACHE_VERSION:
        raise CacheError(
            f"the draw cache {path} has unsupported version {payload.get('version')!r}"
        )
    try:
        return merge_draws(Draw.from_dict(record) for record in payload["draws"])
    except ValueError as exc:
        raise CacheError(f"the draw cache {path} holds an invalid draw: {exc}") from exc


def save_draws(draws: Iterable[Draw], path: Path) -> None:
    """Write the cache file, creating its directory if needed.

    Draws are stored oldest first, one per date. The file is replaced atomically, so
    that a failure never leaves a truncated cache behind.
    """
    payload = {
        "version": _CACHE_VERSION,
        "draws": [draw.to_dict() for draw in merge_draws(draws)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temp_file:
            json.dump(payload, temp_file, separators=(",", ":"))
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """Outcome of :func:`update_cache`."""

    #: The full history after the update, oldest draw first.
    draws: list[Draw]
    #: How many of these draws were not in the cache before.
    added: int
    #: False when the cache was already up to date and no request was made.
    downloaded: bool


def update_cache(
    path: Path,
    *,
    force: bool = False,
    session: requests.Session | None = None,
    now: dt.datetime | None = None,
) -> UpdateResult:
    """Bring the cache at ``path`` up to date and return the resulting history.

    Nothing is downloaded when the cache already holds the latest draw scheduled before
    ``now`` (a timezone-aware datetime, by default the current time) together with its
    prizes, unless ``force`` is set. Otherwise the whole history is downloaded and merged
    into the cache.

    Raises :class:`CacheError` for an unusable cache file, and :class:`FetchError` if the
    download fails, in which case the cache is left untouched.
    """
    cached = load_draws(path)
    if (
        cached
        and not force
        and cached[-1].date >= schedule.previous_draw(now).date()
        # A draw may be published before its prizes: look again until they are known.
        and cached[-1].prizes
    ):
        return UpdateResult(cached, 0, False)
    merged = merge_draws(cached, fetch_draws(session))
    save_draws(merged, path)
    return UpdateResult(merged, len(merged) - len(cached), True)
