"""Request progress for the isolated reopen fixture; never record request content."""

import json
import os
import runpy
import time
from pathlib import Path

import uvicorn
from starlette.types import ASGIApp, Message, Receive, Scope, Send

RECEIPT_TAIL_BYTES = 16_384


def receipt_tail(path: Path, offset: int) -> str:
    """Read at most 16 KiB of this invocation's receipts and return its last 30 complete lines."""
    with path.open("rb") as stream:
        end = stream.seek(0, os.SEEK_END)
        start = max(offset, end - RECEIPT_TAIL_BYTES)
        stream.seek(start)
        tail = stream.read(min(RECEIPT_TAIL_BYTES, max(0, end - start)))
    if start > offset:
        # A byte boundary can split a record; never emit its partial projection.
        tail = tail.partition(b"\n")[2]
    if not tail.endswith(b"\n"):
        tail = tail.rpartition(b"\n")[0]
    return b"\n".join(tail.splitlines()[-30:]).decode("utf-8", errors="replace")


class NavigationReceipts:
    """Distinguish a browser navigation reaching the server from the driver's independent health probe."""

    def __init__(self, app: ASGIApp, path: Path) -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "GET" or scope["path"] != "/":
            await self.app(scope, receive, send)
            return

        # Project only a boolean from this fixed Fetch Metadata field, never arbitrary header text.
        navigation = any(name == b"sec-fetch-dest" and value == b"document" for name, value in scope["headers"])

        def note(event: str, status: int | None = None) -> None:
            record = {
                "at": time.time_ns(),
                "event": event,
                "navigation": navigation,
                "client_port": (scope.get("client") or (None, None))[1],
                "status": status,
            }
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")

        async def traced_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                note("response", message["status"])
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                note("finished")

        note("request")
        await self.app(scope, receive, traced_send)


def main() -> None:
    """Wrap only this fixture child process, then execute the unchanged hz command."""
    receipt_path = Path(os.environ["HZ_REOPEN_RECEIPTS"])
    original = uvicorn.Config

    class DiagnosticConfig(original):
        def __init__(self, app, *args, **kwargs):
            super().__init__(NavigationReceipts(app, receipt_path), *args, **kwargs)

    uvicorn.Config = DiagnosticConfig
    runpy.run_module("heinzel", run_name="__main__")


if __name__ == "__main__":
    main()
