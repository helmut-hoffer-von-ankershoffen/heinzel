"""The web app's notice stream: server-sent events under `/api`, behind the same token as every call (ADR 0041).

`EventSource` can't send an `Authorization` header, and a token in the URL would land in the access log and the
browser's history, so the app reads this stream with `fetch` and the Bearer header like any other call; no ticket,
no cookie, nothing new to steal. CSP `connect-src 'self'` covers it.

Every stream sends an event `changed` when the list of notices changed, for the app's list. A stream opened with
`?popups=true`, by a tab that may show desktop notifications, also tells the `Notifications` service every few
seconds that it shows them, so the daemon holds back, and sends each due notice once as an event `notice`, which the
app pops up and acknowledges (`POST /api/system/notify/ack`). A tab that can't pop up claims nothing, so the daemon
still does. A stream ends after a few minutes and the app opens a new one, so a closed tab never holds a connection
for long. When `hz ui` stops or restarts, every stream ends at once (`Stopping`), so the server doesn't wait for it.
"""

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncGenerator, Callable

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from heinzel.heads.web._shutdown import Stopping
from heinzel.kernel import Kernel, Notifications

PATH = "/system/notify/stream"
"""Under the notify module's own path, so no operation's endpoint can take it."""
POLL_SECONDS = 2.0
PING_SECONDS = 15.0
LIFETIME_SECONDS = 300.0


def add_stream(
    app: FastAPI, kernel: Kernel, stopping: Stopping | None = None, clock: Callable[[], float] = time.monotonic
) -> None:
    """Add the stream to the web head's API app if a module provides `Notifications`; the app's middleware guards it.

    Args:
        app: The web head's API app.
        kernel: The kernel that may provide `Notifications`.
        stopping: Ends every open stream when the server stops; None where nothing stops it (a test's client).
        clock: Seconds, for the stream's lifetime and pings.
    """
    stopping = stopping or Stopping()
    if not kernel.provides(Notifications):
        return

    @app.get(PATH, include_in_schema=False)
    async def stream(request: Request, popups: bool = False, once: bool = False) -> StreamingResponse:
        notifications = kernel.resolve(Notifications)
        return StreamingResponse(
            _events(request, notifications, popups=popups, once=once, clock=clock, stopping=stopping),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )


async def _events(  # noqa: PLR0913 - all but the request and the service by keyword
    request: Request,
    notifications: Notifications,
    *,
    popups: bool,
    once: bool,
    clock: Callable[[], float],
    stopping: Stopping,
) -> AsyncGenerator[str]:
    """The events of one stream, until it is old, its tab goes away or the server stops.

    Yields:
        `changed` when the latest notice changed; with `popups`, each due notice once as `notice`; a ping now and then.
    """
    sent: set[str] = set()
    latest: str | None = None
    started = last_ping = clock()
    yield "retry: 3000\n\n"
    with stopping.watch() as stopped:
        while not stopped.is_set():
            if popups:
                await run_in_threadpool(notifications.watched)
            due = await run_in_threadpool(notifications.due) if popups else []
            recent = await run_in_threadpool(notifications.recent, 1)
            for notice in due:
                if notice.id not in sent:
                    sent.add(notice.id)
                    yield _event("notice", notice.model_dump_json())
            newest = recent[0].id if recent else None
            if newest != latest:
                latest = newest
                yield _event("changed", json.dumps({"latest": newest}))
            if once or clock() - started > LIFETIME_SECONDS or await request.is_disconnected():
                return
            if clock() - last_ping > PING_SECONDS:
                last_ping = clock()
                yield ": ping\n\n"
            with contextlib.suppress(TimeoutError):  # the poll's pause, cut short when the server stops
                await asyncio.wait_for(stopped.wait(), POLL_SECONDS)


def _event(name: str, data: str) -> str:
    """One server-sent event; `data` is JSON on one line, so it needs no splitting."""
    return f"event: {name}\ndata: {data}\n\n"
