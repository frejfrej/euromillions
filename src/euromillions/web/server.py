"""The local HTTP server of the web UI: the pages, and a small JSON API over the analyzer.

It only listens on 127.0.0.1, and refuses requests whose ``Host`` header names anything
but this machine, so that a web page cannot reach it through DNS rebinding.
"""

from __future__ import annotations

import json
import math
import re
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from euromillions import analyzer, backtest, fetcher, suggester
from euromillions.models import LINE_PRICE, Draw
from euromillions.web import api

if sys.version_info >= (3, 11):
    from importlib.resources.abc import Traversable
else:  # pragma: no cover - Python 3.10
    from importlib.abc import Traversable

#: The pages, stylesheets and scripts served as they are.
STATIC: Traversable = resources.files("euromillions.web") / "static"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
_MAX_BODY = 64 * 1024
_INTEGER = re.compile(r"-?[0-9]+")
#: Lines a backtest may play per draw, as on the command line.
_MAX_BACKTEST_LINES = 50

NO_HISTORY = "no draw history yet: use Update data to download it"


class HttpError(Exception):
    """An error reported to the page with its status code."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


class JobRunning(Exception):
    """A backtest is already running."""


class _Cancelled(Exception):
    """Raised from the progress callback to stop a cancelled backtest."""


# --- The backtest job ---------------------------------------------------------------------------


class BacktestJob:
    """One backtest at a time, run in a background thread; the last result is kept."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self._fresh("idle", {})
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _fresh(state: str, settings: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "state": state,
            "done": 0,
            "total": 0,
            "settings": dict(settings),
            "result": None,
            "error": None,
        }

    def snapshot(self) -> dict[str, Any]:
        """Return the state of the job: idle, running, done, failed or cancelled."""
        with self._lock:
            return dict(self._state)

    def start(self, draws: list[Draw], settings: Mapping[str, Any]) -> None:
        """Start a backtest of ``draws`` with ``settings`` (the keyword arguments of
        :func:`~euromillions.backtest.backtest`, ``strategies`` included).

        Returns once the run has played its first draw, so that invalid settings are
        raised here (:class:`ValueError`) rather than reported later. Raises
        :class:`JobRunning` if a backtest is already running.
        """
        with self._lock:
            if self._state["state"] == "running":
                raise JobRunning("a backtest is already running")
            previous = self._state
            self._state = self._fresh("running", settings)
            self._cancel.clear()
        started = threading.Event()
        failure: list[BaseException] = []

        def progress(done: int, total: int) -> None:
            started.set()
            if self._cancel.is_set():
                raise _Cancelled
            with self._lock:
                self._state.update(done=done, total=total)

        def run() -> None:
            outcome: dict[str, Any]
            try:
                kwargs = dict(settings)
                strategies = kwargs.pop("strategies")
                report = backtest.backtest(draws, strategies, progress=progress, **kwargs)
                outcome = {"state": "done", "result": api.backtest_result(report)}
            except _Cancelled:
                outcome = {"state": "cancelled"}
            except Exception as exc:  # reported to the page, like any other error
                if not started.is_set():
                    failure.append(exc)
                outcome = {"state": "failed", "error": str(exc)}
            with self._lock:
                self._state.update(outcome)
                if failure:  # rejected before it started: as if it never ran
                    self._state = previous
            started.set()

        self._thread = threading.Thread(target=run, name="backtest", daemon=True)
        self._thread.start()
        started.wait()
        if failure:
            self._thread.join()
            raise failure[0]

    def cancel(self) -> None:
        """Stop the running backtest, if any, and wait until it has stopped."""
        self._cancel.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=10)


# --- The application ----------------------------------------------------------------------------


class App:
    """What the request handlers share: the cache, the history read from it, the job."""

    def __init__(self, cache_path: Path) -> None:
        self.cache_path = cache_path
        self.job = BacktestJob()
        self._lock = threading.Lock()
        self._fetching = threading.Lock()
        self._draws: list[Draw] = []
        self._stamp: tuple[int, int] | None = None

    def draws(self) -> list[Draw]:
        """Return the cached history, read again whenever the file changes."""
        with self._lock:
            try:
                stat = self.cache_path.stat()
                stamp: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                stamp = None
            if stamp != self._stamp:
                self._draws = fetcher.load_draws(self.cache_path) if stamp else []
                self._stamp = stamp
            return self._draws

    def history(self) -> list[Draw]:
        """Return the cached history, or report that there is none yet."""
        draws = self.draws()
        if not draws:
            raise HttpError(HTTPStatus.CONFLICT, NO_HISTORY)
        return draws

    def fetch(self, force: bool) -> dict[str, Any]:
        """Bring the cache up to date, one download at a time."""
        with self._fetching:
            result = fetcher.update_cache(self.cache_path, force=force)
        return api.fetch_result(result)


# --- Parameters ---------------------------------------------------------------------------------


def _query_int(query: Mapping[str, list[str]], name: str, default: int | None) -> int | None:
    values = query.get(name)
    if not values or values[-1] == "":
        return default
    if not _INTEGER.fullmatch(values[-1]):
        raise ValueError(f"{name} must be a whole number, got {values[-1]!r}")
    return int(values[-1])


def _required_int(query: Mapping[str, list[str]], name: str, default: int) -> int:
    value = _query_int(query, name, default)
    assert value is not None
    return value


def _body_value(body: Mapping[str, Any], name: str, kind: type, default: Any) -> Any:
    """Return ``body[name]``, or ``default`` when absent or null, checked to be a ``kind``.

    ``kind`` is ``int``, ``float`` (an integer is accepted, and the result is finite) or
    ``bool``; a boolean is never taken for a number.
    """
    value: object = body.get(name)
    if value is None:
        return default
    if isinstance(value, bool) != (kind is bool):
        raise ValueError(f"{name} must be a {kind.__name__}, got {value!r}")
    if kind is float and isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError:
            number = math.inf
        if not math.isfinite(number):
            raise ValueError(f"{name} must be a finite number")
        return number
    if not isinstance(value, kind):
        raise ValueError(f"{name} must be a {kind.__name__}, got {value!r}")
    return value


def _backtest_settings(body: Mapping[str, Any]) -> dict[str, Any]:
    strategies = body.get("strategies", list(backtest.DEFAULT_STRATEGIES))
    if not isinstance(strategies, list) or not all(isinstance(s, str) for s in strategies):
        raise ValueError("strategies must be a list of strategy names")
    lines = _body_value(body, "lines", int, 5)
    if not 1 <= lines <= _MAX_BACKTEST_LINES:
        raise ValueError(f"lines must be between 1 and {_MAX_BACKTEST_LINES}, got {lines}")
    return {
        "strategies": strategies,
        "train_fraction": _body_value(body, "trainFraction", float, 0.8),
        "lines": lines,
        "window": _body_value(body, "window", int, analyzer.DEFAULT_WINDOW),
        "seed": _body_value(body, "seed", int, None),
        "refresh": _body_value(body, "refresh", bool, True),
        "line_price": _body_value(body, "price", float, LINE_PRICE),
    }


def _settings_for_the_page(settings: Mapping[str, Any]) -> dict[str, Any]:
    names = {"train_fraction": "trainFraction", "line_price": "price"}
    return {names.get(key, key): value for key, value in settings.items()}


# --- HTTP ---------------------------------------------------------------------------------------


_Route = Callable[[Mapping[str, list[str]]], tuple[HTTPStatus, Any]]


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: App) -> None:
        super().__init__(address, _Handler)
        self.app = app


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "euromillions"

    def log_message(self, format: str, *args: Any) -> None:
        """Stay quiet: the terminal running ``serve`` is not a log."""

    # Every method goes through the same dispatcher.
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def _dispatch(self, method: str) -> None:
        try:
            self._check_host()
            url = urlsplit(self.path)
            routes = self._routes()
            if url.path in routes:
                handlers = routes[url.path]
                if method not in handlers:
                    raise HttpError(HTTPStatus.METHOD_NOT_ALLOWED, f"{method} is not allowed here")
                status, payload = handlers[method](parse_qs(url.query))
                self._send_json(status, payload)
            elif method == "GET":
                self._send_static(url.path)
            else:
                raise HttpError(HTTPStatus.NOT_FOUND, f"nothing at {url.path}")
        except HttpError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except fetcher.CorruptCacheError as exc:
            message = f"{exc}\nDelete that file and update the data again."
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": message})
        except fetcher.CacheError as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        except fetcher.FetchError as exc:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except JobRunning as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except ValueError as exc:  # HistoryError included: the settings do not fit the history
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # a bug: say so, rather than drop the connection
            traceback.print_exc()
            message = f"unexpected error: {exc!r}"
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": message})

    def _check_host(self) -> None:
        """Refuse requests that name another host (DNS rebinding) or come from another site."""
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            raise HttpError(HTTPStatus.FORBIDDEN, "this server only answers to 127.0.0.1")
        origin = self.headers.get("Origin")
        cross_site = self.headers.get("Sec-Fetch-Site") == "cross-site"
        if cross_site or (origin is not None and origin not in {f"http://{h}" for h in hosts}):
            raise HttpError(HTTPStatus.FORBIDDEN, "requests from other sites are refused")

    def _routes(self) -> dict[str, dict[str, _Route]]:
        return {
            "/api/status": {"GET": self._status},
            "/api/fetch": {"POST": self._fetch},
            "/api/analysis": {"GET": self._analysis},
            "/api/suggest": {"GET": self._suggest},
            "/api/backtest": {
                "GET": self._backtest_state,
                "POST": self._backtest_start,
                "DELETE": self._backtest_cancel,
            },
        }

    # --- Routes ---

    def _status(self, _query: Mapping[str, list[str]]) -> tuple[HTTPStatus, Any]:
        return HTTPStatus.OK, api.status(self.server.app.draws())

    def _fetch(self, _query: Mapping[str, list[str]]) -> tuple[HTTPStatus, Any]:
        force = _body_value(self._body(), "force", bool, False)
        return HTTPStatus.OK, self.server.app.fetch(force)

    def _analysis(self, query: Mapping[str, list[str]]) -> tuple[HTTPStatus, Any]:
        draws = self.server.app.history()
        window = _required_int(query, "window", analyzer.DEFAULT_WINDOW)
        top = _required_int(query, "top", 10)
        return HTTPStatus.OK, api.analysis(draws, window=window, top=top)

    def _suggest(self, query: Mapping[str, list[str]]) -> tuple[HTTPStatus, Any]:
        draws = self.server.app.history()
        strategy = query.get("strategy", ["balanced"])[-1]
        if strategy not in suggester.STRATEGIES:
            raise ValueError(f"unknown strategy {strategy!r}")
        return HTTPStatus.OK, api.suggestion(
            draws,
            strategy=strategy,
            lines=_required_int(query, "lines", 5),
            window=_required_int(query, "window", analyzer.DEFAULT_WINDOW),
            seed=_query_int(query, "seed", None),
        )

    def _backtest_state(self, _query: Mapping[str, list[str]]) -> tuple[HTTPStatus, Any]:
        return HTTPStatus.OK, self._job_for_the_page()

    def _backtest_start(self, _query: Mapping[str, list[str]]) -> tuple[HTTPStatus, Any]:
        settings = _backtest_settings(self._body())
        draws = list(self.server.app.history())
        self.server.app.job.start(draws, settings)
        return HTTPStatus.ACCEPTED, self._job_for_the_page()

    def _backtest_cancel(self, _query: Mapping[str, list[str]]) -> tuple[HTTPStatus, Any]:
        self.server.app.job.cancel()
        return HTTPStatus.OK, self._job_for_the_page()

    def _job_for_the_page(self) -> dict[str, Any]:
        job = self.server.app.job.snapshot()
        job["settings"] = _settings_for_the_page(job["settings"])
        return job

    # --- Reading and writing ---

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
        if not length:
            return {}
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpError(HTTPStatus.BAD_REQUEST, "the request body is not JSON") from exc
        if not isinstance(body, dict):
            raise HttpError(HTTPStatus.BAD_REQUEST, "the request body must be a JSON object")
        return body

    def _send_static(self, path: str) -> None:
        name = "index.html" if path == "/" else path.lstrip("/")
        suffix = Path(name).suffix
        files = {entry.name: entry for entry in STATIC.iterdir() if entry.is_file()}
        if "/" in name or name not in files or suffix not in _CONTENT_TYPES:
            raise HttpError(HTTPStatus.NOT_FOUND, f"nothing at {path}")
        self._send(HTTPStatus.OK, _CONTENT_TYPES[suffix], files[name].read_bytes())

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self._send(status, "application/json", body)

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)


def make_server(cache_path: Path, port: int) -> ThreadingHTTPServer:
    """Return a server for the cache at ``cache_path`` on 127.0.0.1:``port`` (0: any free port).

    Raises :class:`OSError` if the port cannot be used. Call ``serve_forever()`` to run it.
    """
    return _Server(("127.0.0.1", port), App(cache_path))
