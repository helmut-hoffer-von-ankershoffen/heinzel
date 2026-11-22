"""The web head's end of the browser bridge: who may connect as the desktop, and what crosses it (ADR 0162)."""

import json
import re
import threading

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from heinzel.heads import api, web
from heinzel.heads.web import _browser_bridge
from heinzel.kernel._browser import NEEDS_DESKTOP, BrowserBridge, BrowserCommand, BrowserError
from heinzel.testing import scaled

pytestmark = pytest.mark.unit

TOKEN = "b" * 43
"""The bridge token: what only the desktop app's main process and the service it started know."""
SESSION = "s" * 43
"""The web app's session token, which every page of the app holds."""
ORIGIN = "http://testserver"
HELLO = {"hello": "hz-browser-bridge", "version": 1, "token": TOKEN}
READY = {"ready": True, "version": 1}
COMMAND = BrowserCommand(actor="chat-1", kind="heinzel", action="read", tab="t-00000001", arguments={"filter": "all"})


@pytest.fixture
def bridge() -> BrowserBridge:
    return BrowserBridge()


@pytest.fixture
def client(bridge, monkeypatch) -> TestClient:
    monkeypatch.setattr(api, "LOOPBACK", api.LOOPBACK | {"testserver"})
    app = FastAPI()
    _browser_bridge.add_route(app, token=TOKEN, port=None, bridge=lambda: bridge)
    return TestClient(app)


def connect(client, headers=None):
    return client.websocket_connect(_browser_bridge.PATH, headers={"origin": ORIGIN} if headers is None else headers)


def desktop(client):
    """A connected desktop: the socket after the hello and hz's ready answer."""
    socket = connect(client).__enter__()
    socket.send_text(json.dumps(HELLO))
    assert json.loads(socket.receive_text()) == READY
    return socket


def ask(bridge, command=COMMAND):
    """Run one command in a thread, as an operation does; returns the thread and where its outcome lands."""
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["result"] = bridge.request(command)
        except BrowserError as error:
            outcome["error"] = error

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


@pytest.mark.parametrize(
    "headers",
    [{"origin": "http://evil.example"}, {}, {"origin": ORIGIN, "sec-fetch-site": "cross-site"}],
    ids=["other origin", "no origin", "cross-site"],
)
def test_refuses_a_handshake_from_anywhere_but_this_computer(client, bridge, headers):
    with pytest.raises(WebSocketDisconnect) as refused, connect(client, headers):
        pass
    assert refused.value.code == 1008
    assert not bridge.available


def test_an_origin_shared_for_the_web_app_is_not_the_desktop(bridge, monkeypatch):
    # The route takes no allowed origins at all: a shared web app (ADR 0084) never reaches the browser.
    app = FastAPI()
    _browser_bridge.add_route(app, token=TOKEN, port=8737, bridge=lambda: bridge)
    headers = {"host": "hz.example.ts.net", "origin": "https://hz.example.ts.net"}
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        TestClient(app).websocket_connect(_browser_bridge.PATH, headers=headers),
    ):
        pass
    assert refused.value.code == 1008


@pytest.mark.parametrize(
    "hello",
    [
        {**HELLO, "token": "wrong"},
        {**HELLO, "token": SESSION},
        {**HELLO, "token": TOKEN + "x"},
        {**HELLO, "token": None},
        {"token": TOKEN},
        {**HELLO, "version": 2},
        {**HELLO, "version": True},
        {**HELLO, "hello": "terminal"},
        {**HELLO, "extra": 1},
        [TOKEN],
        "not json",
    ],
)
def test_closes_without_the_exact_hello_and_the_session_token(client, bridge, hello):
    with connect(client) as socket:
        socket.send_text(hello if isinstance(hello, str) else json.dumps(hello))
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()
    assert closed.value.code == 1008
    assert not bridge.available


@pytest.mark.parametrize(
    ("token", "bridge"),
    [(TOKEN, None), (None, BrowserBridge())],
    ids=["the browser module is not loaded", "no desktop app started this service"],
)
def test_refuses_every_socket_without_a_bridge_or_a_bridge_token(monkeypatch, token, bridge):
    monkeypatch.setattr(api, "LOOPBACK", api.LOOPBACK | {"testserver"})
    app = FastAPI()
    _browser_bridge.add_route(app, token=token, port=None, bridge=lambda: bridge)
    with pytest.raises(WebSocketDisconnect) as refused, connect(TestClient(app)):
        pass
    assert refused.value.code == 1008


def test_the_session_token_of_the_web_app_never_opens_the_bridge(make_kernel, monkeypatch):
    # The whole web head: a page holds SESSION and passes Host and Origin, and is still not the desktop app.
    monkeypatch.setattr(api, "LOOPBACK", api.LOOPBACK | {"testserver"})
    kernel = make_kernel(builtins=True)
    client = TestClient(web.build(kernel, port=None, token=SESSION, static=None, bridge_token=TOKEN))
    bridge = _browser_bridge.kernel_bridge(kernel)
    assert bridge is not None
    with connect(client) as socket:
        socket.send_text(json.dumps({**HELLO, "token": SESSION}))
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()
    assert closed.value.code == 1008
    assert not bridge.available
    with connect(client) as socket:
        socket.send_text(json.dumps(HELLO))
        assert json.loads(socket.receive_text()) == READY
        assert bridge.available
    # Without a bridge token (hz ui serve in a terminal, no desktop app) nothing opens it, not even that token.
    plain = TestClient(web.build(make_kernel(builtins=True), port=None, token=SESSION, static=None))
    with pytest.raises(WebSocketDisconnect) as refused, connect(plain):
        pass
    assert refused.value.code == 1008


def test_a_bridge_token_is_well_formed_and_never_the_session_token():
    assert _browser_bridge.bridge_token(TOKEN, SESSION) == TOKEN
    for given in (None, "", "short", "b" * 31, "b" * 257, "b" * 40 + " x", "b" * 40 + "\n", SESSION):
        assert _browser_bridge.bridge_token(given, SESSION) is None


def test_a_command_goes_to_the_desktop_and_its_answer_comes_back(client, bridge):
    assert not bridge.available
    socket = desktop(client)
    assert bridge.available
    thread, outcome = ask(bridge)
    frame = json.loads(socket.receive_text())
    assert frame == {
        "id": frame["id"],
        "command": "read",
        "agent": {"id": "heinzel:chat-1", "name": "chat-1", "kind": "heinzel"},
        "args": {"filter": "all", "tab": "t-00000001"},
    }
    # Events and answers nobody waits for pass without harm.
    socket.send_text(json.dumps({"event": "take-over", "agent": "chat-1", "tab": "t-00000001"}))
    socket.send_text(json.dumps({"id": "unknown", "ok": True, "result": {}}))
    socket.send_text(
        json.dumps({"id": frame["id"], "ok": True, "result": {"url": "https://a.example.com/", "content": "x"}})
    )
    thread.join(scaled(5))
    assert outcome == {"result": {"url": "https://a.example.com/", "content": "x"}}

    thread, outcome = ask(bridge, BrowserCommand(actor="chat-1", kind="heinzel", action="tabs"))
    frame = json.loads(socket.receive_text())
    assert frame["args"] == {}
    socket.send_text(
        json.dumps(
            {"id": frame["id"], "ok": False, "error": {"code": "paused", "message": "The person took over this tab."}}
        )
    )
    thread.join(scaled(5))
    error = outcome["error"]
    assert isinstance(error, BrowserError)
    assert (error.code, str(error)) == ("paused", "The person took over this tab.")

    thread, outcome = ask(bridge)
    frame = json.loads(socket.receive_text())
    socket.send_text(json.dumps({"id": frame["id"], "ok": False, "error": {"code": "made-up", "message": "a\x00b"}}))
    thread.join(scaled(5))
    assert (outcome["error"].code, str(outcome["error"])) == ("failed", "a b")

    # The desktop leaves: a waiting command fails, and so does the next one.
    thread, outcome = ask(bridge)
    socket.receive_text()
    socket.close()
    thread.join(scaled(5))
    assert (outcome["error"].code, str(outcome["error"])) == ("unavailable", NEEDS_DESKTOP)
    assert not bridge.available
    with pytest.raises(BrowserError, match="needs the desktop app"):
        bridge.request(COMMAND)


@pytest.mark.parametrize(
    "frame",
    [
        {"id": "1", "ok": True},
        {"id": "1", "ok": "yes", "result": {}},
        {"id": "1", "ok": True, "result": {}, "more": 1},
        {"id": "1", "ok": True, "error": {"code": "paused", "message": "x"}},
        {"id": "1", "ok": False, "error": {"code": "paused"}},
        {"id": 1, "ok": True, "result": {}},
        {"event": "made-up", "agent": "chat-1", "tab": None},
        {"event": "stop", "agent": "chat-1"},
        {"command": "tabs"},
        [],
        "not json",
    ],
)
def test_a_frame_outside_the_protocol_ends_the_connection(client, bridge, frame):
    socket = desktop(client)
    socket.send_text(frame if isinstance(frame, str) else json.dumps(frame))
    with pytest.raises(WebSocketDisconnect) as closed:
        socket.receive_text()
    assert closed.value.code == 1002
    assert not bridge.available


def test_a_newer_desktop_replaces_the_older_one(client, bridge):
    first = desktop(client)
    second = desktop(client)
    with pytest.raises(WebSocketDisconnect) as closed:
        first.receive_text()
    assert closed.value.code == 1001
    # The newer one stays the link, also after the older socket's cleanup ran.
    thread, outcome = ask(bridge)
    frame = json.loads(second.receive_text())
    second.send_text(json.dumps({"id": frame["id"], "ok": True, "result": {}}))
    thread.join(scaled(5))
    assert outcome == {"result": {}}
    assert bridge.available
    second.close()


def test_agent_ids_are_apart_by_kind_and_stable_for_any_name():
    wire = _browser_bridge.wire_agent
    assert wire("ci-bot", "external") == {"id": "external:ci-bot", "name": "ci-bot", "kind": "external"}
    # A token named like a Heinzel's session, or like stdio's `local`, never shares that one's tabs.
    assert wire("chat-1", "heinzel")["id"] != wire("chat-1", "external")["id"]
    assert wire("local", "external")["id"] != wire("token:local", "external")["id"]
    assert wire("token:local", "external")["id"] == "external:token:local"
    spaced = wire("Release bot\n(2)", "external")
    assert spaced["name"] == "Release bot (2)"
    assert re.fullmatch(r"external:x-[0-9a-f]{32}", spaced["id"])
    assert spaced == wire("Release bot\n(2)", "external")
    assert spaced["id"] != wire("Release bot (2)", "external")["id"]
    long = wire("n" * 200, "heinzel")
    assert len(long["name"]) == 60
    assert re.fullmatch(r"heinzel:x-[0-9a-f]{32}", long["id"])
    assert all(
        re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", wire(actor, "external")["id"]) for actor in ("a" * 71, "a" * 72, "ü", "")
    )


def test_a_commit_card_may_wait_for_the_person_and_a_silent_desktop_times_out(client, bridge, monkeypatch):
    monkeypatch.setattr(_browser_bridge, "ANSWER_SECONDS", 0.05)
    monkeypatch.setattr(_browser_bridge, "CONFIRM_SLACK_SECONDS", 0.0)
    socket = desktop(client)
    thread, outcome = ask(bridge)
    socket.receive_text()
    thread.join(scaled(5))
    assert outcome["error"].code == "timeout"
    confirm = BrowserCommand(
        actor="ci-bot",
        kind="external",
        action="confirm",
        tab="t-00000001",
        arguments={"summary": "Send", "fingerprint": "a" * 64, "seconds": 2},
    )
    thread, outcome = ask(bridge, confirm)
    frame = json.loads(socket.receive_text())
    thread.join(scaled(0.3))
    assert thread.is_alive()
    socket.send_text(json.dumps({"id": frame["id"], "ok": True, "result": {"approved": True}}))
    thread.join(scaled(5))
    assert outcome == {"result": {"approved": True}}
    socket.close()
