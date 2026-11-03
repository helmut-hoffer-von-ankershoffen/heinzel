"""A streamed chat turn holds off an automatic update until it has ended, through a real streamed request (ADR 0116).

The web head's middleware gets a streamed response back when its headers go out; the turn runs while its body streams,
and its operation's thread can run on after the browser left.
"""

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from heinzel import Module, Progress
from heinzel.heads import web
from heinzel.heads._stream import WORK, StreamProgress, streamed
from heinzel.heads.web._auto_update import Activity, AutoUpdater
from heinzel.kernel import OperationLet

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests-that-is-long-enough"
BASE = "http://127.0.0.1:8000"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def turn_module(started: threading.Event, finish: threading.Event, kernel_of: list) -> Module:
    module = Module("talk", "A chat turn that waits to be told to finish.")

    @module.operation
    def turn() -> str:
        """Answer, slowly."""
        kernel_of[0].resolve(Progress).emitter()("text", {"text": "thinking"})
        started.set()
        assert finish.wait(10)
        return "done"

    return module


def build(make_kernel):
    started, finish, kernel_of = threading.Event(), threading.Event(), []
    kernel = make_kernel(builtins=True, talk=turn_module(started, finish, kernel_of))
    kernel_of.append(kernel)
    app = web.build(kernel, port=8000, token=TOKEN, static=None, restart=lambda: None)
    updater = app.state.auto_update
    assert isinstance(updater, AutoUpdater)
    return TestClient(app, base_url=BASE), updater, started, finish


def test_a_streamed_turn_is_work_until_its_stream_has_ended(make_kernel):
    client, updater, started, finish = build(make_kernel)
    assert not updater.busy()
    with client.stream("POST", "/api/stream/talk/turn", headers=AUTH) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines()
        assert "event: text" in next(lines)  # the headers and the first event are out; the turn still runs
        assert started.wait(5)
        assert updater.busy()
        finish.set()
        rest = "\n".join(lines)
    assert "event: result" in rest
    assert not updater.busy()


def test_a_turn_whose_browser_left_is_work_until_its_operation_ends(make_kernel):
    started, finish, kernel_of = threading.Event(), threading.Event(), []
    kernel = make_kernel(builtins=True, talk=turn_module(started, finish, kernel_of))
    kernel_of.append(kernel)
    kernel.bind(Progress, StreamProgress())  # as the web head does: events reach the stream
    activity = Activity()
    operation = next(op for record, op in kernel.lets(OperationLet) if record.qualname == "talk")

    async def leave_early() -> None:
        token = WORK.set((activity.enter, activity.leave))
        try:
            response = streamed(kernel, operation, {})
        finally:
            WORK.reset(token)
        body = response.body_iterator
        await anext(body)  # the first event reached the browser
        await body.aclose()  # and the browser left
        closed.set()

    closed = threading.Event()
    # The loop waits for the operation's thread when it ends, so it runs on a thread of its own.
    loop = threading.Thread(target=asyncio.run, args=(leave_early(),))
    loop.start()
    assert closed.wait(5)
    assert started.wait(5)
    assert activity.busy  # the stream is gone, the operation's thread still answers
    finish.set()
    for _ in range(100):
        if not activity.busy:
            break
        threading.Event().wait(0.05)
    assert not activity.busy
    loop.join(5)
