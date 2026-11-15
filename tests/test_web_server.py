"""Tests for the local HTTP server of the web UI."""

from __future__ import annotations

import datetime as dt
import http.client
import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from euromillions import backtest as backtest_module
from euromillions import fetcher
from euromillions.backtest import Backtest
from euromillions.fetcher import FetchError, UpdateResult
from euromillions.models import Draw
from euromillions.web import api as api_module
from euromillions.web.server import STATIC, make_server

from .conftest import TYPICAL_PRIZES, random_history

REAL_BACKTEST = backtest_module.backtest


@dataclass
class Client:
    port: int
    cache: Path

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        host: str | None = None,
        headers: dict[str, str] | None = None,
        body_text: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        data = None if body is None else json.dumps(body).encode()
        if body_text is not None:
            data = body_text.encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, data, headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def json(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        status, _, data = self.request(method, path, body)
        return status, json.loads(data)

    def wait_for(self, state: str, timeout: float = 10) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, job = self.json("GET", "/api/backtest")
            if job["state"] == state:
                return dict(job)
            time.sleep(0.02)
        raise AssertionError(f"the backtest never reached {state!r}: {job}")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Client]:
    cache = tmp_path / "draws.json"
    server = make_server(cache, 0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield Client(server.server_address[1], cache)
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def history(client: Client) -> list[Draw]:
    draws = random_history(300, prizes=TYPICAL_PRIZES)
    fetcher.save_draws(draws, client.cache)
    return draws


class Gate:
    """A stand-in for ``backtest()`` that reports progress until it is released or cancelled."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def __call__(self, draws: list[Draw], strategies: Any, **kwargs: Any) -> Backtest:
        progress: Callable[[int, int], None] = kwargs.pop("progress")
        done = 0
        while not self.release.is_set():
            done += 1
            progress(done, 10**6)
            time.sleep(0.005)
        return REAL_BACKTEST(draws, strategies, **kwargs)


class TestPages:
    def test_serves_the_home_page(self, client: Client) -> None:
        status, headers, body = client.request("GET", "/")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert headers["Cache-Control"] == "no-store"
        assert body == (STATIC / "index.html").read_bytes()

    @pytest.mark.parametrize("path", ["/nope.html", "/../pyproject.toml", "/api/nope"])
    def test_unknown_paths(self, client: Client, path: str) -> None:
        assert client.request("GET", path)[0] == 404

    def test_wrong_method(self, client: Client) -> None:
        assert client.request("PUT", "/api/status")[0] == 405

    @pytest.mark.parametrize("path", ["/", "/api/status"])
    def test_foreign_host_is_refused(self, client: Client, path: str) -> None:
        assert client.request("GET", path, host=f"evil.example:{client.port}")[0] == 403
        assert client.request("GET", path, host=f"localhost:{client.port}")[0] == 200

    def test_pages_reference_existing_assets(self) -> None:
        names = {path.name for path in STATIC.iterdir()}
        for page in (path for path in STATIC.iterdir() if path.name.endswith(".html")):
            for target in re.findall(r'(?:src|href)="([^"#:]+)"', page.read_text()):
                assert target.lstrip("/") in names or target == "/", (page.name, target)


class TestData:
    def test_status_without_a_cache(self, client: Client) -> None:
        status, body = client.json("GET", "/api/status")
        assert (status, body["cache"]) == (200, False)
        status, body = client.json("GET", "/api/analysis")
        assert status == 409
        assert body["error"].startswith("no draw history yet")

    def test_a_corrupt_cache(self, client: Client) -> None:
        client.cache.write_text("not json")
        status, body = client.json("GET", "/api/status")
        assert status == 500
        assert "update the data again" in body["error"]

    def test_analysis(self, client: Client, history: list[Draw]) -> None:
        status, body = client.json("GET", "/api/analysis?window=20&top=5")
        assert status == 200
        assert (body["totalDraws"], body["window"], len(body["hot"])) == (300, 20, 5)

    def test_suggest(self, client: Client, history: list[Draw]) -> None:
        status, body = client.json("GET", "/api/suggest?strategy=hot&lines=3&seed=7")
        assert status == 200
        assert (len(body["lines"]), body["seed"]) == (3, 7)
        assert client.json("GET", "/api/suggest?strategy=hot&lines=3&seed=7")[1] == body
        assert isinstance(client.json("GET", "/api/suggest")[1]["seed"], int)

    @pytest.mark.parametrize(
        "query",
        [
            "lines=abc",
            "lines=139838161",
            "window=0",
            "seed=1.5",
            "strategy=nope",
            "window=1e3",
        ],
    )
    def test_suggest_rejects_bad_parameters(
        self, client: Client, history: list[Draw], query: str
    ) -> None:
        status, body = client.json("GET", f"/api/suggest?{query}")
        assert status == 400
        assert body["error"]

    def test_the_cache_is_read_again_when_it_changes(
        self, client: Client, history: list[Draw]
    ) -> None:
        assert client.json("GET", "/api/status")[1]["draws"] == 300
        fetcher.save_draws(random_history(301, prizes=TYPICAL_PRIZES), client.cache)
        assert client.json("GET", "/api/status")[1]["draws"] == 301


class TestFetch:
    def test_fetch(
        self, client: Client, history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[bool] = []

        def update_cache(path: Path, *, force: bool = False, **_: Any) -> UpdateResult:
            calls.append(force)
            return UpdateResult(history, 3, 1, True)

        monkeypatch.setattr(fetcher, "update_cache", update_cache)
        status, body = client.json("POST", "/api/fetch", {"force": True})
        assert status == 200
        assert (body["added"], body["updated"], body["status"]["draws"]) == (3, 1, 300)
        assert client.json("POST", "/api/fetch")[0] == 200
        assert calls == [True, False]

    def test_a_failed_download(self, client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
        def update_cache(path: Path, **_: Any) -> UpdateResult:
            raise FetchError("the source is down")

        monkeypatch.setattr(fetcher, "update_cache", update_cache)
        assert client.json("POST", "/api/fetch") == (502, {"error": "the source is down"})

    def test_a_bad_body(self, client: Client) -> None:
        status, _, _ = client.request("POST", "/api/fetch", ["force"])
        assert status == 400


class TestBacktest:
    def test_backtest_lifecycle(
        self, client: Client, history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gate = Gate()
        monkeypatch.setattr(backtest_module, "backtest", gate)
        assert client.json("GET", "/api/backtest")[1]["state"] == "idle"
        settings = {"trainFraction": 0.9, "lines": 1, "seed": 1, "strategies": ["hot"]}
        assert client.json("POST", "/api/backtest", settings)[0] == 202
        running = client.wait_for("running")
        assert running["done"] >= 1
        assert running["settings"]["trainFraction"] == 0.9
        assert client.json("POST", "/api/backtest", settings)[0] == 409
        gate.release.set()
        done = client.wait_for("done")
        assert [r["strategy"] for r in done["result"]["results"]] == ["hot"]

    def test_backtest_cancel(
        self, client: Client, history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert client.json("DELETE", "/api/backtest") == (
            200,
            client.json("GET", "/api/backtest")[1],
        )
        monkeypatch.setattr(backtest_module, "backtest", Gate())
        assert client.json("POST", "/api/backtest", {"strategies": ["hot"]})[0] == 202
        status, job = client.json("DELETE", "/api/backtest")
        assert (status, job["state"]) == (200, "cancelled")
        # A new run can start after a cancellation.
        assert client.json("POST", "/api/backtest", {"strategies": ["hot"]})[0] == 202
        client.json("DELETE", "/api/backtest")

    def test_backtest_bad_split(self, client: Client) -> None:
        draws = random_history(300, start=dt.date(2016, 1, 1), prizes=TYPICAL_PRIZES)
        fetcher.save_draws(draws, client.cache)
        status, body = client.json("POST", "/api/backtest", {"trainFraction": 0.01})
        assert status == 400
        assert "current rules" in body["error"]
        assert client.json("GET", "/api/backtest")[1]["state"] == "idle"

    @pytest.mark.parametrize(
        "settings",
        [
            {"strategies": ["nope"]},
            {"strategies": "hot"},
            {"lines": 0},
            {"lines": 139_838_161},
            {"lines": True},
            {"trainFraction": "half"},
            {"refresh": "yes"},
            {"price": -1},
        ],
    )
    def test_backtest_rejects_bad_settings(
        self, client: Client, history: list[Draw], settings: dict[str, Any]
    ) -> None:
        status, body = client.json("POST", "/api/backtest", settings)
        assert status == 400
        assert body["error"]

    def test_fetch_during_a_backtest(
        self, client: Client, history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gate = Gate()
        monkeypatch.setattr(backtest_module, "backtest", gate)
        newer = random_history(301, prizes=TYPICAL_PRIZES)

        def update_cache(path: Path, **_: Any) -> UpdateResult:
            fetcher.save_draws(newer, path)
            return UpdateResult(newer, 1, 0, True)

        monkeypatch.setattr(fetcher, "update_cache", update_cache)
        assert client.json("POST", "/api/backtest", {"strategies": ["hot"], "lines": 1})[0] == 202
        assert client.json("POST", "/api/fetch")[0] == 200
        assert client.json("GET", "/api/status")[1]["draws"] == 301
        assert client.json("GET", "/api/backtest")[1]["state"] == "running"
        gate.release.set()
        assert client.wait_for("done")["result"]["trainDraws"] == 240  # the 300 draws it started on


class TestReviewFixes:
    def test_a_rejected_backtest_keeps_the_last_result(
        self, client: Client, history: list[Draw]
    ) -> None:
        settings = {"trainFraction": 0.9, "lines": 1, "seed": 1, "strategies": ["hot"]}
        assert client.json("POST", "/api/backtest", settings)[0] == 202
        done = client.wait_for("done")
        assert client.json("POST", "/api/backtest", {"price": 0})[0] == 400
        assert client.json("GET", "/api/backtest")[1] == done

    def test_an_unexpected_error_is_a_json_500(
        self, client: Client, history: list[Draw], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("a bug")

        monkeypatch.setattr(api_module, "analysis", broken)
        status, body = client.json("GET", "/api/analysis")
        assert status == 500
        assert "a bug" in body["error"]

    @pytest.mark.parametrize("seed", [12345678901234567891, "12345678901234567891"])
    def test_a_backtest_seed_keeps_every_digit(
        self, client: Client, history: list[Draw], seed: int | str
    ) -> None:
        settings = {"trainFraction": 0.9, "lines": 1, "seed": seed, "strategies": ["hot"]}
        assert client.json("POST", "/api/backtest", settings)[0] == 202
        assert client.wait_for("done")["settings"]["seed"] == "12345678901234567891"

    @pytest.mark.parametrize("seed", ["1.5", "seven", "", 1.5, True])
    def test_a_backtest_seed_must_be_a_whole_number(
        self, client: Client, history: list[Draw], seed: object
    ) -> None:
        status, body = client.json("POST", "/api/backtest", {"strategies": ["hot"], "seed": seed})
        assert (status, body["error"].startswith("seed must be a whole number")) == (400, True)

    def test_a_huge_number_is_a_bad_request(self, client: Client, history: list[Draw]) -> None:
        status, _, data = client.request(
            "POST",
            "/api/backtest",
            None,
            body_text='{"trainFraction": 1' + "0" * 400 + "}",
        )
        assert status == 400
        assert json.loads(data)["error"]

    @pytest.mark.parametrize(
        "headers",
        [
            {"Origin": "https://evil.example"},
            {"Sec-Fetch-Site": "cross-site"},
        ],
    )
    def test_cross_site_requests_are_refused(
        self, client: Client, history: list[Draw], headers: dict[str, str]
    ) -> None:
        status, _, _ = client.request("POST", "/api/backtest", {}, headers=headers)
        assert status == 403
        assert client.json("GET", "/api/backtest")[1]["state"] == "idle"

    def test_same_site_requests_are_accepted(self, client: Client, history: list[Draw]) -> None:
        headers = {"Origin": f"http://localhost:{client.port}", "Sec-Fetch-Site": "same-origin"}
        status, _, _ = client.request("GET", "/api/status", headers=headers)
        assert status == 200

    @pytest.mark.parametrize("path", ["/", "/suggest.html", "/api/status"])
    def test_cross_site_navigation_still_opens_the_pages(
        self, client: Client, history: list[Draw], path: str
    ) -> None:
        # A link from another site, or a page opened by a browser extension.
        assert client.request("GET", path, headers={"Sec-Fetch-Site": "cross-site"})[0] == 200
