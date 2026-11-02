"""Run the real UI with request receipts for the isolated WebKit diagnostic experiment."""

import json
import os
import runpy
import time
from pathlib import Path

import uvicorn
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class Receipts:
    """Record HTTP progress without headers, queries, bodies or credentials."""

    def __init__(self, app: ASGIApp, path: Path) -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        def note(event: str, status: int | None = None) -> None:
            record = {
                "at": time.time_ns(),
                "event": event,
                "method": scope["method"],
                "path": scope["path"],
                "client_port": scope.get("client", (None, None))[1],
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
    """Wrap only this child process's server, then invoke the unchanged hz command."""
    receipt_path = Path(os.environ["HZ_REOPEN_RECEIPTS"])
    original = uvicorn.Config

    class DiagnosticConfig(original):
        def __init__(self, app, *args, **kwargs):
            super().__init__(Receipts(app, receipt_path), *args, **kwargs)

    uvicorn.Config = DiagnosticConfig
    runpy.run_module("heinzel", run_name="__main__")


if __name__ == "__main__":
    main()
