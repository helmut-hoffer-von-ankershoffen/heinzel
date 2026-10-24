"""Streaming an operation's events to a browser as server-sent events: the web head's one stream mechanism.

`POST /api/stream/<path>` runs the operation at `/api/<path>` with the same parameters and the same checks (token,
Host, Origin), and answers `text/event-stream`: the events the operation emits through `Progress.emitter()` as they
happen (`text`, `tool`, `task`), then `result` with the operation's JSON result, or `error` with the message a
`HeinzelError` carried. The page reads the stream with `fetch`, not `EventSource`, because only `fetch` sends the
`Authorization` header (ADR 0039); `web/src/api/stream.ts` is the reader. Other server pushes (notifications) should
use this format and that reader too (ADR 0045).
"""

import asyncio
import contextlib
import contextvars
import json
import logging
from collections.abc import AsyncGenerator, Generator, Mapping
from contextlib import contextmanager
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic_core import to_jsonable_python

from heinzel.kernel import Emit, HeinzelError, Kernel, OperationLet, Progress
from heinzel.kernel._progress import Advance

log = logging.getLogger(__name__)
_SINK: contextvars.ContextVar[Emit | None] = contextvars.ContextVar("hz_stream_sink", default=None)
END = "end"
HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


class StreamProgress(Progress):
    """The web head's `Progress`: events go to the stream of the request whose operation emits them, else nowhere."""

    @contextmanager
    def task(self, description: str, total: float | None = None) -> Generator[Advance]:
        """Announce `description` to the stream, if any.

        Yields:
            A function that drops the amount of work done; the stream shows the task, not a bar.
        """
        del total
        sink = _SINK.get()
        if sink is not None:
            sink("task", {"text": description})
        yield lambda _amount: None

    def emitter(self) -> Emit:
        """The stream of the request running on this thread, or a function that drops events."""
        sink = _SINK.get()
        return sink if sink is not None else _drop


def _drop(kind: str, data: Mapping[str, object]) -> None:
    del kind, data


def frame(kind: str, data: object) -> str:
    """One server-sent event: `event: <kind>` and its data as one line of JSON."""
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def streamed(kernel: Kernel, operation: OperationLet, arguments: dict[str, Any]) -> StreamingResponse:
    """Run `operation` in a worker thread and stream its events, then its result or error."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    def emit(kind: str, data: Mapping[str, object]) -> None:
        with contextlib.suppress(RuntimeError):  # the loop closed: the browser left, nobody reads any more
            loop.call_soon_threadsafe(queue.put_nowait, (kind, dict(data)))

    def work() -> None:
        _SINK.set(emit)
        try:
            result = kernel.invoke(operation, **arguments)
            emit("result", {"data": to_jsonable_python(result, fallback=str)})
        except HeinzelError as exc:
            emit("error", {"error": str(exc)})
        except Exception:
            log.exception("Streaming %s failed", operation.name)
            emit("error", {"error": f"{operation.name} failed with a bug; the log of `hz ui` says why."})
        finally:
            emit(END, {})

    loop.run_in_executor(None, contextvars.copy_context().run, work)

    async def events() -> AsyncGenerator[str]:
        while True:
            kind, data = await queue.get()
            if kind == END:
                return
            yield frame(kind, data)

    return StreamingResponse(events(), media_type="text/event-stream", headers=HEADERS)
