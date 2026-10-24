"""`hz ui`: the web head on a free localhost port, a fresh token per process, the browser opened at it (ADR 0039)."""

import threading
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn
from typer.testing import CliRunner

from heinzel import HeinzelError
from heinzel.heads import web
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration


@pytest.fixture
def static(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    return tmp_path


def test_serve_opens_the_browser_at_a_loopback_url_with_a_fresh_token(make_kernel, static, monkeypatch):
    servers: list[uvicorn.Server] = []
    opened: list[str] = []
    announced = threading.Event()

    class Recorded(uvicorn.Server):
        def __init__(self, config: uvicorn.Config) -> None:
            super().__init__(config)
            servers.append(self)

    monkeypatch.setattr(uvicorn, "Server", Recorded)
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    urls: list[str] = []

    def announce(url: str) -> None:
        urls.append(url)
        announced.set()

    thread = threading.Thread(
        target=web.serve, args=(make_kernel(builtins=True),), kwargs={"announce": announce, "static": static}
    )
    thread.start()
    try:
        assert announced.wait(10)
        url = urlsplit(urls[0])
        assert url.hostname == "127.0.0.1"
        assert url.port
        token = url.fragment.removeprefix("token=")
        assert len(token) >= 40  # 32 random bytes, URL-safe
        assert opened == urls
        base = f"http://127.0.0.1:{url.port}"
        assert httpx.get(f"{base}/api/system/doctor", timeout=10).status_code == 401
        doctor = httpx.get(f"{base}/api/system/doctor", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        assert doctor.json()["status"] in {"up", "degraded", "down"}
        assert "<div id=root>" in httpx.get(f"{base}/doctor", timeout=10).text
    finally:
        servers[0].should_exit = True
        thread.join(10)


def test_every_process_gets_its_own_token():
    assert web.session_url(1, "a") != web.session_url(1, "b")
    assert web.session_url(8123, "t") == "http://127.0.0.1:8123/#token=t"
    assert web.session_url(8123, "t", base="http://localhost:5173") == "http://localhost:5173/#token=t"


def test_hz_ui_without_a_browser_prints_only_the_url(make_kernel, monkeypatch):
    calls = []

    def serve(kernel, **kwargs):
        calls.append(kwargs)
        kwargs["announce"]("http://127.0.0.1:4321/#token=t")

    monkeypatch.setattr(web, "serve", serve)
    result = CliRunner().invoke(build(make_kernel(builtins=True)), ["ui", "--no-browser", "--port", "4321"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "http://127.0.0.1:4321/#token=t\n"
    assert calls[0]["browser"] is False
    assert calls[0]["port"] == 4321


def test_hz_ui_says_where_it_runs_when_it_opens_the_browser(make_kernel, monkeypatch):
    monkeypatch.setattr(web, "serve", lambda kernel, **kwargs: kwargs["announce"]("http://127.0.0.1:1/#token=t"))
    result = CliRunner().invoke(build(make_kernel(builtins=True)), ["ui"])
    assert result.exit_code == 0
    assert "Heinzelmännchen runs at http://127.0.0.1:1/#token=t" in result.stderr
    assert "Ctrl-C" in result.stderr


@pytest.mark.parametrize(
    ("problem", "says"),
    [(HeinzelError("The web app isn't built"), "isn't built"), (OSError("Address already in use"), "--port")],
)
def test_hz_ui_explains_why_it_cant_start(make_kernel, monkeypatch, problem, says):
    def serve(kernel, **kwargs):
        raise problem

    monkeypatch.setattr(web, "serve", serve)
    result = CliRunner().invoke(build(make_kernel(builtins=True)), ["ui", "--no-browser"])
    assert result.exit_code == 1
    assert says in result.stderr
