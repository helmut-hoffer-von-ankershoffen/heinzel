"""Ctrl-C stops `hz ui` at once and quietly, while the app holds the notice stream and a terminal open.

`hz ui` runs as a process of its own, as a person starts it, serving a made-up build of the app; the test opens what a
tab keeps open and sends SIGINT, as Ctrl-C does.
"""

import os
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from typer.testing import CliRunner

from heinzel.heads import web
from heinzel.heads.cli import build
from heinzel.heads.cli._serve import UI_STOPPED
from heinzel.heads.web import _terminal
from heinzel.heads.web._shutdown import GRACE_SECONDS, Stopping

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.name != "posix", reason="SIGINT as Ctrl-C sends it"),
]

TOKEN = "t" * 43
STREAM = "/api/system/notify/stream"
STOPS_WITHIN = 5.0
UI_SCRIPT = """
import functools, sys
from pathlib import Path
from heinzel.heads import web
from heinzel.heads.cli import main
web.serve = functools.partial(web.serve, static=Path(sys.argv[1]))
sys.argv = ["hz", "ui", "--no-browser", "--port", "0"]
main()
"""
"""`hz ui` itself, with the built app swapped for a made-up one, so the test needs no `make web-build`."""


@pytest.fixture
def hz_ui(tmp_path, monkeypatch):
    """`hz ui` started in a process of its own, its base URL and its token."""
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    monkeypatch.setenv("HZ_UPDATE_CHECK", "false")  # no background check holding the exit up
    process = subprocess.Popen(
        [sys.executable, "-c", UI_SCRIPT, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout
        url = urlsplit(process.stdout.readline().strip())
        assert url.port, process.stderr.read() if process.stderr else ""
        yield process, f"http://127.0.0.1:{url.port}", url.fragment.removeprefix("token=")
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def open_stream(base: str, token: str) -> list[str | float]:
    """Read the notice stream in the background, as the app does; then what ended it, and when."""
    opened = threading.Event()
    ended: list[str | float] = []

    def read() -> None:
        try:
            with httpx.stream(
                "GET", base + STREAM, headers={"Authorization": f"Bearer {token}"}, timeout=STOPS_WITHIN * 2
            ) as response:
                for _ in response.iter_lines():
                    opened.set()
            ended.extend(["ended", time.monotonic()])
        except httpx.HTTPError as exc:
            ended.append(f"{type(exc).__name__}: {exc}")

    threading.Thread(target=read, daemon=True).start()
    assert opened.wait(10)
    return ended


def drain(socket) -> None:
    """Read the shell's output until the socket closes."""
    while True:
        socket.receive_bytes()


@pytest.mark.timeout(60)  # starting a process of hz ui takes seconds on a loaded machine
@pytest.mark.parametrize("presses", [1, 2], ids=["Ctrl-C", "Ctrl-C twice"])
def test_ctrl_c_stops_hz_ui_at_once_with_one_friendly_line(hz_ui, presses):
    process, base, token = hz_ui
    ended = open_stream(base, token)
    started = time.monotonic()
    for _ in range(presses):
        process.send_signal(signal.SIGINT)
        time.sleep(0.05)
    _, err = process.communicate(timeout=STOPS_WITHIN * 2)
    assert time.monotonic() - started < STOPS_WITHIN
    assert process.returncode == 0
    assert err.strip() == UI_STOPPED
    assert "Traceback" not in err
    assert "ERROR" not in err
    # The stream ended its response well before uvicorn's grace ran out; it wasn't cut off.
    assert ended[0] == "ended", ended
    assert float(ended[1]) - started < GRACE_SECONDS / 2


def test_ctrl_c_before_the_server_is_up_still_stops_with_the_friendly_line(make_kernel, monkeypatch):
    def interrupted(kernel, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(web, "serve", interrupted)
    result = CliRunner().invoke(build(make_kernel(builtins=True)), ["ui", "--no-browser"])
    assert result.exit_code == 0
    assert result.stderr.strip() == UI_STOPPED


@pytest.mark.parametrize(
    ("interrupted", "reason"), [(True, _terminal.STOPPED), (False, _terminal.RESTARTED)], ids=["Ctrl-C", "restart"]
)
def test_a_terminal_closes_going_away_with_a_reason_when_the_server_stops(
    make_kernel, monkeypatch, tmp_path, interrupted, reason
):
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(web.api, "LOOPBACK", web.api.LOOPBACK | {"testserver"})
    stopping = Stopping()
    client = TestClient(web.build(make_kernel(builtins=True), port=None, token=TOKEN, static=None, stopping=stopping))
    with client.websocket_connect("/api/terminal", headers={"origin": "http://testserver"}) as socket:
        socket.send_text(f'{{"token": "{TOKEN}"}}')
        socket.send_bytes(b"echo ready-$((1+1))\n")
        seen = b""
        while b"ready-2" not in seen:
            seen += socket.receive_bytes()
        stopping.interrupted = interrupted
        stopping.stop()
        with pytest.raises(WebSocketDisconnect) as closed:
            drain(socket)
    assert closed.value.code == _terminal.GOING_AWAY
    assert closed.value.reason == reason
    assert not _terminal._open


def test_a_stream_opened_after_the_server_stopped_ends_at_once(make_kernel):
    stopping = Stopping()
    stopping.stop()
    app = web.build(make_kernel(builtins=True), port=8000, token=TOKEN, static=None, stopping=stopping)
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    response = client.get(STREAM, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.text == "retry: 3000\n\n"
