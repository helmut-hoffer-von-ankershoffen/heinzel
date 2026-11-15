"""A failing streamed operation logs one structured event and streams a plain error (`heads._stream`)."""

import asyncio
from types import SimpleNamespace

import pytest

from heinzel.heads import _stream

pytestmark = pytest.mark.unit


def test_a_bug_in_a_streamed_operation_logs_an_event_and_streams_a_plain_error(monkeypatch, caplog):
    monkeypatch.setattr(_stream, "mark_authenticated_presence", lambda kernel, module: None)
    monkeypatch.setattr(_stream, "request_person", lambda kernel: None)  # the fake kernel has no CurrentUser

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


@pytest.mark.parametrize(("private", "code"), [(True, "subscription_usage_limit"), (False, None)])
def test_a_streamed_error_carries_the_code_the_plain_call_answers_with(monkeypatch, private, code):
    from heinzel.kernel._errors import SubscriptionUsageLimitError

    monkeypatch.setattr(_stream, "mark_authenticated_presence", lambda kernel, module: None)
    monkeypatch.setattr(_stream, "request_person", lambda kernel: None)  # the fake kernel has no CurrentUser

    def limited(operation, **arguments):
        raise SubscriptionUsageLimitError

    kernel = SimpleNamespace(invoke=limited)
    operation = SimpleNamespace(name="demo", private=private)

    async def drain() -> str:
        response = _stream.streamed(kernel, operation, {})  # type: ignore[arg-type]
        return "".join([chunk async for chunk in response.body_iterator])  # type: ignore[misc]

    body = asyncio.run(drain())
    assert "event: error" in body
    assert ('"code": "subscription_usage_limit"' in body) is (code is not None)


def test_a_streamed_not_supported_error_names_the_capability_and_method(monkeypatch):
    from heinzel.kernel._errors import NotSupportedError

    monkeypatch.setattr(_stream, "mark_authenticated_presence", lambda kernel, module: None)
    monkeypatch.setattr(_stream, "request_person", lambda kernel: None)  # the fake kernel has no CurrentUser

    def unsupported(operation, **arguments):
        capability, method = "documents", "replace_section"
        raise NotSupportedError(capability, method)

    body = asyncio.run(_drain(SimpleNamespace(invoke=unsupported), SimpleNamespace(name="demo", private=False)))
    assert '"code": "not_supported"' in body
    assert '"capability": "documents"' in body
    assert '"method": "replace_section"' in body


async def _drain(kernel, operation) -> str:
    response = _stream.streamed(kernel, operation, {})  # type: ignore[arg-type]
    return "".join([chunk async for chunk in response.body_iterator])  # type: ignore[misc]
