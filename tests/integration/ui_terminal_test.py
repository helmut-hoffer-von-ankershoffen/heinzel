"""The web head's terminal: a login shell behind /api/terminal, checked before the socket is accepted (ADR 0048)."""

import asyncio
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from heinzel.heads import web
from heinzel.heads.web import _terminal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.name != "posix", reason="the terminal needs a POSIX pseudo-terminal"),
]

TOKEN = "t" * 43
ORIGIN = "http://testserver"


@pytest.fixture
def client(make_kernel, monkeypatch, tmp_path):
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(web.api, "LOOPBACK", web.api.LOOPBACK | {"testserver"})
    return TestClient(web.build(make_kernel(builtins=True), port=None, token=TOKEN, static=None))


def read_until(socket, needle: bytes) -> bytes:
    seen = b""
    while needle not in seen:
        seen += socket.receive_bytes()
    return seen


def test_runs_the_shell_and_resizes_it(client):
    with client.websocket_connect("/api/terminal", headers={"origin": ORIGIN}) as socket:
        socket.send_text(json.dumps({"token": TOKEN}))
        socket.send_text(json.dumps({"resize": [97, 31]}))
        socket.send_bytes(b"echo hz-$((40+2)); : </dev/tty && echo controlling-$((1+1)); stty size\n")
        output = read_until(socket, b"31 97")
        assert b"hz-42" in output
        assert b"controlling-2" in output


@pytest.mark.parametrize("headers", [{"origin": "http://evil.example"}, {}], ids=["other origin", "no origin"])
def test_refuses_a_handshake_from_anywhere_but_the_app(client, headers):
    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect("/api/terminal", headers=headers):
        pass
    assert refused.value.code == 1008


def test_closes_without_the_session_token(client):
    with client.websocket_connect("/api/terminal", headers={"origin": ORIGIN}) as socket:
        socket.send_text(json.dumps({"token": "wrong"}))
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_bytes()
    assert closed.value.code == 1008


def connect(client):
    return client.websocket_connect("/api/terminal", headers={"origin": ORIGIN})


def test_a_shell_that_prints_faster_than_the_browser_reads_is_paused(client, monkeypatch):
    gate = threading.Event()
    send_bytes = WebSocket.send_bytes

    async def slow_browser(self, data):
        await asyncio.to_thread(gate.wait)
        await send_bytes(self, data)

    monkeypatch.setattr(WebSocket, "send_bytes", slow_browser)
    with connect(client) as socket:
        socket.send_text(json.dumps({"token": TOKEN}))
        socket.send_bytes(b"yes\n")
        sizes = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            (terminal,) = _terminal._open
            sizes.append(terminal.output.qsize())
            time.sleep(0.05)
        assert terminal.paused
        assert max(sizes) <= _terminal.OUTPUT_CHUNKS
        gate.set()
        socket.send_bytes(b"\x03echo do$((1+1))ne\n")
        read_until(socket, b"do2ne")


def test_keystrokes_beyond_the_terminal_buffer_all_reach_the_shell(client, monkeypatch):
    monkeypatch.setattr(_terminal, "PENDING_INPUT", 4096)
    with connect(client) as socket:
        socket.send_text(json.dumps({"token": TOKEN}))
        socket.send_bytes(b"stty -echo -icanon; echo rea''dy; head -c $((100*1024)) | wc -c\n")
        read_until(socket, b"ready")
        for _ in range(4):
            socket.send_bytes(b"x" * 25600)
        read_until(socket, b"102400")


def test_the_socket_closes_when_the_shell_ends(client):
    with connect(client) as socket:
        socket.send_text(json.dumps({"token": TOKEN}))
        socket.send_bytes(b"exit\n")
        with pytest.raises(WebSocketDisconnect) as closed:
            read_until(socket, b"output after exit")
    assert closed.value.reason == "The shell ended."


def test_closing_the_socket_ends_the_shell(client):
    with connect(client) as socket:
        socket.send_text(json.dumps({"token": TOKEN}))
        socket.send_bytes(b"echo pid=$$.\n")
        seen = b""
        while not (found := re.search(rb"pid=(\d+)\.", seen)):
            seen += socket.receive_bytes()
    with pytest.raises(ProcessLookupError):
        os.kill(int(found.group(1)), 0)


def test_refuses_more_terminals_than_the_cap(client, monkeypatch):
    monkeypatch.setattr(_terminal, "SHELLS", 0)
    with connect(client) as socket:
        socket.send_text(json.dumps({"token": TOKEN}))
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_bytes()
    assert closed.value.code == 1013


def test_the_shell_gets_a_desktop_terminals_environment_without_hzs(monkeypatch):
    ours = str(Path(sys.prefix) / "bin")
    monkeypatch.setattr(sys, "base_prefix", "/somewhere/else")
    monkeypatch.setenv("PATH", os.pathsep.join([ours, "/usr/bin"]))
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    monkeypatch.setenv("UV_PROJECT", "/elsewhere")
    monkeypatch.setenv("NO_COLOR", "1")
    env = _terminal.environment()
    assert env["PATH"] == "/usr/bin"
    assert not {"VIRTUAL_ENV", "UV_PROJECT", "NO_COLOR"} & env.keys()
    assert (env["TERM"], env["COLORTERM"], env["CLICOLOR"], env["TERM_PROGRAM"]) == (
        "xterm-256color",
        "truecolor",
        "1",
        "heinzel",
    )


def test_a_later_resize_changes_the_size_the_shell_sees(client):
    with connect(client) as socket:
        socket.send_text(json.dumps({"token": TOKEN}))
        socket.send_text(json.dumps({"resize": [50, 20]}))
        socket.send_bytes(b"stty size\n")
        read_until(socket, b"20 50")
        socket.send_text(json.dumps({"resize": [133, 41]}))
        socket.send_bytes(b"stty size\n")
        read_until(socket, b"41 133")
