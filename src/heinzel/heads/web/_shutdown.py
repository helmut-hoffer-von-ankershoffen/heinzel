"""How `hz ui` stops: the responses that stay open end first, so a Ctrl-C stops it at once and quietly.

The notice stream and the terminal keep their requests open for minutes. uvicorn's graceful shutdown waits for them,
then cancels them, and each cancellation would land in the log as an "Exception in ASGI application" with its
traceback. So the server tells the app it is stopping before it waits (`Stopping`): the stream ends its response and
each terminal hangs up its shell and closes its socket with "going away" and a reason the app shows. What is still
running when the short backstop runs out (an operation streaming its progress) is cancelled as before; the log
records of that cancellation are dropped while stopping, and only then, so a real error still logs while serving.

Ctrl-C is how a session of `hz ui` ends, so the server doesn't raise it again once it stopped, as uvicorn does: `serve`
returns and the CLI says so in one line. A second Ctrl-C while it stops ends the wait at once, as in uvicorn, and one
after that, while the process exits, ends it at once without a traceback.
"""

import asyncio
import contextlib
import logging
import os
import signal
import threading
from collections.abc import Generator
from types import FrameType
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import socket

    import uvicorn

GRACE_SECONDS = 3
"""How long open responses get to end before uvicorn cancels them; ours end within a fraction of it."""
KEEP_ALIVE_SECONDS = 75
"""Outlast the browser's idle connections (WebKit drops them at about 30 s): a server closing first races the next
request, and WebKit fails a POST caught by that close with "Load failed" instead of retrying it."""
SETTLE_SECONDS = 0.1
"""How long the app gets to close its terminals itself before uvicorn closes what is left with its own code."""
UVICORN_LOGGER = "uvicorn.error"


class Stopping:
    """Whether the server is stopping, for the responses that would otherwise stay open: the stream, the terminals.

    `watch` hands each one an event of its own event loop, so a test's client, which runs each request in a loop of
    its own, works like the server.
    """

    def __init__(self) -> None:
        """Not stopping yet."""
        self._stopped = False
        self.interrupted = False
        """Whether Ctrl-C (SIGINT) stopped the server, rather than a restart or a test."""
        self._lock = threading.Lock()
        self._events: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()

    @property
    def stopped(self) -> bool:
        """Whether `stop` was called."""
        return self._stopped

    def stop(self) -> None:
        """Set every watcher's event; one that watches later finds it set at once."""
        with self._lock:
            self._stopped = True
            events = list(self._events)
        for loop, event in events:
            with contextlib.suppress(RuntimeError):  # its loop is closed: nothing waits there any more
                loop.call_soon_threadsafe(event.set)

    @contextlib.contextmanager
    def watch(self) -> Generator[asyncio.Event]:
        """An event of the running loop, set when the server stops.

        Yields:
            The event; already set when the server stopped before.
        """
        entry = (asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            if self._stopped:
                entry[1].set()
            self._events.add(entry)
        try:
            yield entry[1]
        finally:
            with self._lock:
                self._events.discard(entry)


class QuietCancellation(logging.Filter):
    """Drops uvicorn's records of responses it cancels while the server stops; every other record passes."""

    def __init__(self, stopping: Stopping) -> None:
        """Quiet only once `stopping` is stopped."""
        super().__init__()
        self.stopping = stopping

    def filter(self, record: logging.LogRecord) -> bool:
        """Whether `record` is logged."""
        if not self.stopping.stopped:
            return True
        if record.getMessage().startswith("Cancel ") and "graceful shutdown exceeded" in record.getMessage():
            return False
        return not (record.exc_info and _cancelled(record.exc_info[1]))


def _cancelled(exc: BaseException | None) -> bool:
    """Whether `exc` is a cancellation, alone or as the only kind in a group."""
    if isinstance(exc, BaseExceptionGroup):
        group = cast("BaseExceptionGroup[BaseException]", exc)  # isinstance leaves the members' type unknown
        return all(_cancelled(inner) for inner in group.exceptions)
    return isinstance(exc, asyncio.CancelledError)


def _exit_quietly(sig: int, frame: FrameType | None) -> None:  # noqa: ARG001 - a signal handler's signature
    """End the process at once, as a third Ctrl-C asks, without a traceback from wherever it was."""
    os._exit(0)


def server(config: "uvicorn.Config", stopping: Stopping) -> "uvicorn.Server":
    """A uvicorn server that tells the app through `stopping` that it stops, before it waits for open responses.

    The class is made here, not at import: uvicorn is imported only when serving, and a test can replace
    `uvicorn.Server` to record the server.
    """
    import uvicorn  # noqa: PLC0415 - only needed when serving

    quiet = QuietCancellation(stopping)

    class _Server(uvicorn.Server):
        def run(self, sockets: "list[socket.socket] | None" = None) -> None:
            # Around the whole event loop: after a second Ctrl-C, the loop cancels what is left only as it closes.
            logger = logging.getLogger(UVICORN_LOGGER)
            logger.addFilter(quiet)
            try:
                super().run(sockets)
            finally:
                logger.removeFilter(quiet)
            if stopping.interrupted and threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGINT, _exit_quietly)  # the process ends now; another Ctrl-C only hurries it

        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            # Unlike uvicorn's, this doesn't record SIGINT to raise it again once stopped; the exit code test in
            # tests/integration/ui_stop_test.py notices if uvicorn starts raising it some other way.
            if sig != signal.SIGINT:
                super().handle_exit(sig, frame)  # SIGTERM: uvicorn raises it again once stopped, so the process ends
                return
            stopping.interrupted = True
            self.force_exit = self.should_exit  # the second Ctrl-C stops waiting
            self.should_exit = True

        async def shutdown(self, sockets: "list[socket.socket] | None" = None) -> None:
            stopping.stop()
            await asyncio.sleep(SETTLE_SECONDS)
            await super().shutdown(sockets)

    return _Server(config)
