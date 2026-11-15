"""Tests for ``euromillions serve``."""

from __future__ import annotations

import socket
import webbrowser
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from euromillions.cli import cli
from euromillions.web import server


class FakeServer:
    """Stands in for the HTTP server: serving is interrupted at once, as by Ctrl+C."""

    def __init__(self, port: int) -> None:
        self.server_address = ("127.0.0.1", port or 54321)
        self.closed = False

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *args, **kwargs: urls.append(url))
    return urls


@pytest.fixture
def servers(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, FakeServer]]:
    made: list[tuple[Path, FakeServer]] = []

    def make_server(cache_path: Path, port: int) -> Any:
        fake = FakeServer(port)
        made.append((cache_path, fake))
        return fake

    monkeypatch.setattr(server, "make_server", make_server)
    return made


def test_serve_opens_the_browser(
    tmp_path: Path, opened: list[str], servers: list[tuple[Path, FakeServer]]
) -> None:
    result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "serve", "--port", "8123"])
    assert result.exit_code == 0, result.output
    assert "Serving on http://127.0.0.1:8123/ - press Ctrl+C to stop." in result.output
    assert result.output.rstrip().endswith("Stopped.")
    assert opened == ["http://127.0.0.1:8123/"]
    ((cache_path, fake),) = servers
    assert cache_path == tmp_path / "draws.json"
    assert fake.closed


def test_serve_without_a_history_says_how_to_get_one(
    tmp_path: Path, opened: list[str], servers: list[tuple[Path, FakeServer]]
) -> None:
    result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "serve", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "No draw history yet" in result.output


def test_serve_no_browser(
    tmp_path: Path, opened: list[str], servers: list[tuple[Path, FakeServer]]
) -> None:
    result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "serve", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:8000/" in result.output
    assert opened == []


def test_serve_port_in_use(tmp_path: Path, opened: list[str]) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        result = CliRunner().invoke(
            cli, ["--data-dir", str(tmp_path), "serve", "--port", str(port), "--no-browser"]
        )
    assert result.exit_code == 1
    assert f"port {port} is already in use: choose another one with --port" in result.output
    assert opened == []


def test_static_files_are_packaged() -> None:
    static = resources.files("euromillions.web") / "static"
    assert (static / "index.html").is_file()
