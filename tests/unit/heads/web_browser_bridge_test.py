"""The web head's end of the browser bridge: who may connect as the desktop, and what crosses it (ADR 0162)."""

import json
import threading

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from heinzel.heads import api
from heinzel.heads.web import _browser_bridge
from heinzel.kernel._browser import NEEDS_DESKTOP, BrowserBridge, BrowserCommand, BrowserError

pytestmark = pytest.mark.unit

TOKEN = "t" * 43
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


def test_refuses_every_desktop_while_the_browser_tools_are_off(monkeypatch):
    monkeypatch.setattr(api, "LOOPBACK", api.LOOPBACK | {"testserver"})
    app = FastAPI()
    _browser_bridge.add_route(app, token=TOKEN, port=None, bridge=lambda: None)
    with pytest.raises(WebSocketDisconnect) as refused, connect(TestClient(app)):
        pass
    assert refused.value.code == 1008


def test_a_command_goes_to_the_desktop_and_its_answer_comes_back(client, bridge):
    assert not bridge.available
    socket = desktop(client)
    assert bridge.available
    thread, outcome = ask(bridge)
    frame = json.loads(socket.receive_text())
    assert frame == {
        "id": frame["id"],
        "command": "read",
        "agent": {"id": "chat-1", "name": "chat-1", "kind": "heinzel"},
        "args": {"filter": "all", "tab": "t-00000001"},
    }
    # Events and answers nobody waits for pass without harm.
    socket.send_text(json.dumps({"event": "take-over", "agent": "chat-1", "tab": "t-00000001"}))
    socket.send_text(json.dumps({"id": "unknown", "ok": True, "result": {}}))
    socket.send_text(
        json.dumps({"id": frame["id"], "ok": True, "result": {"url": "https://a.example.com/", "content": "x"}})
    )
    thread.join(5)
    assert outcome == {"result": {"url": "https://a.example.com/", "content": "x"}}

    thread, outcome = ask(bridge, BrowserCommand(actor="chat-1", kind="heinzel", action="tabs"))
    frame = json.loads(socket.receive_text())
    assert frame["args"] == {}
    socket.send_text(
        json.dumps(
            {"id": frame["id"], "ok": False, "error": {"code": "paused", "message": "The person took over this tab."}}
        )
    )
    thread.join(5)
    error = outcome["error"]
    assert isinstance(error, BrowserError)
    assert (error.code, str(error)) == ("paused", "The person took over this tab.")

    thread, outcome = ask(bridge)
    frame = json.loads(socket.receive_text())
    socket.send_text(json.dumps({"id": frame["id"], "ok": False, "error": {"code": "made-up", "message": "a\x00b"}}))
    thread.join(5)
    assert (outcome["error"].code, str(outcome["error"])) == ("failed", "a b")

    # The desktop leaves: a waiting command fails, and so does the next one.
    thread, outcome = ask(bridge)
    socket.receive_text()
    socket.close()
    thread.join(5)
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
    thread.join(5)
    assert outcome == {"result": {}}
    assert bridge.available
    second.close()


def test_an_actor_name_outside_the_desktop_alphabet_gets_a_stable_id():
    plain = _browser_bridge.wire_agent("ci-bot", "external")
    assert plain == {"id": "ci-bot", "name": "ci-bot", "kind": "external"}
    spaced = _browser_bridge.wire_agent("Release bot\n(2)", "external")
    assert spaced["name"] == "Release bot (2)"
    assert spaced["id"].startswith("x-")
    assert len(spaced["id"]) == 34
    assert spaced == _browser_bridge.wire_agent("Release bot\n(2)", "external")
    assert spaced["id"] != _browser_bridge.wire_agent("Release bot (2)", "external")["id"]
    assert len(_browser_bridge.wire_agent("n" * 200, "heinzel")["name"]) == 60


def test_a_commit_card_may_wait_for_the_person_and_a_silent_desktop_times_out(client, bridge, monkeypatch):
    monkeypatch.setattr(_browser_bridge, "ANSWER_SECONDS", 0.05)
    monkeypatch.setattr(_browser_bridge, "CONFIRM_SLACK_SECONDS", 0.0)
    socket = desktop(client)
    thread, outcome = ask(bridge)
    socket.receive_text()
    thread.join(5)
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
    thread.join(0.3)
    assert thread.is_alive()
    socket.send_text(json.dumps({"id": frame["id"], "ok": True, "result": {"approved": True}}))
    thread.join(5)
    assert outcome == {"result": {"approved": True}}
    socket.close()
