"""A failing streamed operation logs one structured event and streams a plain error (`heads._stream`)."""

import asyncio
from types import SimpleNamespace

import pytest

from heinzel.heads import _stream

pytestmark = pytest.mark.unit


def test_a_bug_in_a_streamed_operation_logs_an_event_and_streams_a_plain_error(monkeypatch, caplog):
    monkeypatch.setattr(_stream, "mark_authenticated_presence", lambda kernel, module: None)

    def exploding(operation, **arguments):
        msg = "secret detail"
        raise RuntimeError(msg)

    kernel = SimpleNamespace(invoke=exploding)
    operation = SimpleNamespace(name="demo")

    async def drain() -> str:
        response = _stream.streamed(kernel, operation, {})  # type: ignore[arg-type]
        return "".join([chunk async for chunk in response.body_iterator])  # type: ignore[misc]

    body = asyncio.run(drain())
    assert "demo failed with a bug" in body
    assert "secret detail" not in body
    (event,) = [r for r in caplog.records if getattr(r, "event", "") == "stream.operation.failed"]
    assert event.operation == "demo"
    assert event.exc_info is not None
