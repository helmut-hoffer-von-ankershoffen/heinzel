"""How `hz ui` stops: the signal its long-lived responses watch, and the log records a stop keeps out."""

import asyncio
import logging
import threading

import pytest

from heinzel.heads.web._shutdown import QuietCancellation, Stopping

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("stopped", [False, True], ids=["serving", "stopping"])
def test_only_the_cancellations_of_a_stop_are_kept_out_of_the_log(stopped):
    stopping = Stopping()
    if stopped:
        stopping.stop()
    quiet = QuietCancellation(stopping)

    def record(message: str, exc: BaseException | None = None) -> logging.LogRecord:
        exc_info = (type(exc), exc, None) if exc else None
        return logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, message, (), exc_info)

    cancelled = record("Exception in ASGI application\n", asyncio.CancelledError())
    timed_out = record("Cancel 1 running task(s), timeout graceful shutdown exceeded")
    grouped = record("Exception in ASGI application\n", BaseExceptionGroup("tasks", [asyncio.CancelledError()]))
    assert [quiet.filter(r) for r in (cancelled, timed_out, grouped)] == [not stopped] * 3
    for real in (
        record("Exception in ASGI application\n", RuntimeError("a bug")),
        record("Exception in ASGI application\n", BaseExceptionGroup("tasks", [asyncio.CancelledError(), OSError()])),
    ):
        assert quiet.filter(real)  # a real error logs, stopping or not


def test_stopping_reaches_watchers_in_any_event_loop():
    stopping = Stopping()
    woke: list[bool] = []

    async def watch() -> None:
        with stopping.watch() as stopped:
            threading.Timer(0.05, stopping.stop).start()
            await asyncio.wait_for(stopped.wait(), 1)
            woke.append(stopped.is_set())

    asyncio.run(watch())
    asyncio.run(watch())  # a second loop, as a test's client uses per request
    assert woke == [True, True]
