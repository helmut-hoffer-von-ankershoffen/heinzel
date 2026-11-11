"""Private child bootstrap: fixed-enum timestamps, then the unchanged probe main."""

import contextlib
import os
import sys
import time
from collections.abc import Callable
from typing import Any

MARKER_FD = -1


def emit(event: str) -> None:
    """Write no server data, credentials, or exception contents."""
    with contextlib.suppress(OSError):
        os.write(MARKER_FD, f"{event} {time.monotonic_ns()}\n".encode("ascii"))


def instrument(target: Any, name: str, label: str) -> None:  # noqa: ANN401
    """Wrap one async operation and preserve its arguments and return value."""
    original = getattr(target, name)

    async def observed(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        emit(f"{label}.start")
        try:
            result = await original(*args, **kwargs)
        except BaseException:
            emit(f"{label}.error")
            raise
        emit(f"{label}.ok")
        return result

    setattr(target, name, observed)


class ContextProxy:
    """Forward an async context manager's exact entry and exit behavior."""

    def __init__(self, inner: Any) -> None:  # noqa: ANN401
        """Hold the original context manager."""
        self.inner = inner

    async def __aenter__(self) -> Any:  # noqa: ANN401
        """Preserve the original enter result."""
        emit("transport.enter.start")
        try:
            result = await self.inner.__aenter__()
        except BaseException:
            emit("transport.enter.error")
            raise
        emit("transport.enter.ok")
        return result

    async def __aexit__(self, *args: object) -> Any:  # noqa: ANN401
        """Preserve exceptions, cancellation and suppression results."""
        emit("transport.exit.start")
        try:
            result = await self.inner.__aexit__(*args)
        except BaseException:
            emit("transport.exit.error")
            raise
        emit("transport.exit.ok")
        return result


def wrap_observe(original: Callable[..., Any]) -> Callable[..., Any]:
    """Record termination without interpreting the result or exception."""

    async def observed(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        try:
            return await original(*args, **kwargs)
        finally:
            emit("observe.exit")

    return observed


def main() -> None:
    """Instrument the real probe only inside its diagnostic child."""
    global MARKER_FD  # noqa: PLW0603 - isolated diagnostic child
    MARKER_FD = int(sys.argv[1])
    emit("child.entry")
    from heinzel.heads.mcp import _http_probe as probe  # noqa: PLC0415

    emit("child.import_done")
    for target, method, label in (
        (probe.httpx2.AsyncClient, "__aenter__", "client.enter"),
        (probe.httpx2.AsyncClient, "__aexit__", "client.exit"),
        (probe.ClientSession, "__aenter__", "session.enter"),
        (probe.ClientSession, "__aexit__", "session.exit"),
        (probe.ClientSession, "initialize", "init"),
        (probe.ClientSession, "list_tools", "list"),
    ):
        instrument(target, method, label)
    original_send = probe.httpx2.AsyncClient.send
    seen_401 = False

    async def send(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        nonlocal seen_401
        response = await original_send(*args, **kwargs)
        if response.status_code == 401 and not seen_401:  # noqa: PLR2004
            seen_401 = True
            emit("client.401")
        return response

    probe.httpx2.AsyncClient.send = send
    transport = probe.streamable_http_client
    probe.streamable_http_client = lambda *args, **kwargs: ContextProxy(transport(*args, **kwargs))
    probe._observe = wrap_observe(probe._observe)  # noqa: SLF001 - diagnostic seam
    endpoint = sys.argv[2]
    sys.argv = [sys.argv[0], endpoint]
    probe.main()


if __name__ == "__main__":
    main()
