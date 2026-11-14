"""MCP for outside agents at `/mcp` on the web server, switched by `[hz] mcp_http` (ADR 0162, amending ADR 0142).

The streamable-HTTP MCP head of ADR 0142 is a path of the one web server (port `[hz] ui_port`, which Tailscale Serve
covers too), not a process of its own. It is shut by default: while `mcp_http` is off every request to `/mcp` is a
plain 404, outside the page fallback, and nothing of the MCP head is built. The setting is read again with each
request (at most once a second), so `hz system serve mcp-start`, `mcp-stop` and Settings → MCP act at once, and
each shows what the other did. Switching it off ends the open MCP sessions.

Who gets in is decided in two places, neither of them the page's session: the web head's own guard (Host and Origin,
as for `/api`) runs before this path, and the MCP SDK's bearer check then accepts only an unexpired `hzmcp_` token
from `McpTokens`; a read token calls read-only tools only (`heads/mcp`). The UI's session or device token never
authenticates here, and an MCP token never authenticates `/api`.

Tokens are made, listed and revoked by the person under `/api/_web/mcp` (`add_routes`), never as operations, so no MCP
head offers them.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from heinzel.heads._mcp_tokens import McpToken, McpTokens
from heinzel.heads.web._shutdown import Stopping
from heinzel.kernel import KERNEL_SECTION, ConfigEditor, HeinzelError, Kernel, Settings

PATH = "/mcp"
SETTING = "hz.mcp_http"
REREAD_SECONDS = 1.0
"""How stale the switch may be: a request reads the configuration again when its last read is older than this."""
NOT_FOUND = 404
FORBIDDEN = 403
log = logging.getLogger(__name__)
Reread = Callable[[], bool]


def enabled_in(kernel: Kernel) -> bool:
    """Whether `[hz] mcp_http` is on in the configuration as it is now; an unreadable file keeps this server's own."""
    try:
        return kernel.config.reread().settings(Settings, KERNEL_SECTION).mcp_http
    except Exception:  # noqa: BLE001 - a damaged file must not open the door: fall back to what this server loaded
        return kernel.settings.mcp_http


class McpEndpoint:
    """The ASGI app at `/mcp`: 404 while the setting is off, else the authenticated MCP head, built on first use."""

    def __init__(
        self,
        kernel: Kernel,
        tokens: McpTokens,
        stopping: Stopping,
        *,
        enabled: Reread | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Serve `kernel`'s tools to holders of `tokens`; `enabled` replaces the configuration read in a test."""
        self._kernel = kernel
        self._tokens = tokens
        self._stopping = stopping
        self._enabled = enabled or (lambda: enabled_in(kernel))
        self._clock = clock
        self._read_at = float("-inf")
        self._on = False
        self._lock = asyncio.Lock()
        self._app: ASGIApp | None = None
        self._off: asyncio.Event | None = None
        self._holder: asyncio.Task[None] | None = None

    def is_on(self) -> bool:
        """The switch, read again at most every `REREAD_SECONDS`."""
        now = self._clock()
        if now - self._read_at >= REREAD_SECONDS:
            self._on = self._enabled()
            self._read_at = now
        return self._on

    def forget(self) -> None:
        """Read the switch afresh at the next request, after this server itself changed it."""
        self._read_at = float("-inf")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer one request: 404 while off, else the MCP app's answer."""
        if scope["type"] != "http":
            return
        if not self.is_on() or self._stopping.stopped:
            await self._close()  # only the switch ends the open sessions, never a request's path
            await JSONResponse({"error": "Not found."}, status_code=NOT_FOUND)(scope, receive, send)
            return
        if scope["path"] != PATH:
            await JSONResponse({"error": "Not found."}, status_code=NOT_FOUND)(scope, receive, send)
            return
        app = await self._running()
        await app(scope, receive, send)

    async def _close(self) -> None:
        """End the open sessions once the switch went off, so the next switch-on builds a fresh head."""
        async with self._lock:
            if self._holder is None:
                return
            if self._off is not None:
                self._off.set()
            holder, self._holder, self._app, self._off = self._holder, None, None, None
            with contextlib.suppress(Exception):
                await holder

    async def _running(self) -> ASGIApp:
        async with self._lock:
            if self._app is not None:
                return self._app
            from heinzel.heads import mcp  # noqa: PLC0415 - heavy import, only once the switch is on

            server = mcp.build(self._kernel, "streamable-http", tokens=self._tokens)
            # The web head's guard has already applied its one Host and Origin policy (as for /api, with the
            # allowed HTTPS origins): a second, narrower list in the SDK would only drift from it.
            app = server.streamable_http_app(
                transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
            )
            manager = server.session_manager
            ready, off = asyncio.Event(), asyncio.Event()

            async def hold() -> None:
                async with manager.run():
                    ready.set()
                    with self._stopping.watch() as stopped:
                        waits = {asyncio.create_task(off.wait()), asyncio.create_task(stopped.wait())}
                        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                        for wait in waits:
                            wait.cancel()

            self._holder = asyncio.create_task(hold())
            await ready.wait()
            self._off, self._app = off, app
            return app


def add_endpoint(app: FastAPI, endpoint: McpEndpoint) -> None:
    """Route `/mcp` (and anything below it, a 404 as well) to `endpoint`, before the page fallback takes every path."""
    app.router.routes.insert(0, Route(PATH, endpoint, methods=["GET", "POST", "DELETE"]))
    app.router.routes.insert(1, Route(PATH + "/{rest:path}", endpoint))


class TokenRequest(BaseModel):
    """A new token, as the Settings page asks for it."""

    actor: str = Field(max_length=40, description="Which agent it is for, e.g. `claude-code`.")
    scope: Literal["read", "write"] = Field(default="read", description="`read` or `write`.")
    days: int = Field(default=90, ge=1, le=365, description="How long it works.")


class SwitchRequest(BaseModel):
    """The switch."""

    enabled: bool = Field(description="Whether `/mcp` serves.")


def _public(record: McpToken) -> dict[str, object]:
    return record.model_dump(mode="json", exclude={"sha256"})


def add_routes(app: FastAPI, kernel: Kernel, tokens: McpTokens, endpoint: McpEndpoint) -> None:
    """Add `/_web/mcp` to the web head's API app: the switch, and the tokens made, listed and revoked.

    Only the person's own web session or paired device reaches these (the API's token check, which an MCP token never
    passes), and none of them is an operation, so no MCP head or REST endpoint offers them.
    """

    def state() -> dict[str, object]:
        return {"enabled": enabled_in(kernel), "tokens": [_public(record) for record in tokens.tokens()]}

    @app.get("/_web/mcp", include_in_schema=False)
    async def status() -> JSONResponse:
        return JSONResponse(state())

    @app.put("/_web/mcp", include_in_schema=False)
    async def switch(body: SwitchRequest) -> JSONResponse:
        try:
            kernel.resolve(ConfigEditor).write(SETTING, body.enabled)
        except HeinzelError as exc:
            return JSONResponse({"error": str(exc)}, status_code=FORBIDDEN)
        endpoint.forget()
        return JSONResponse(state())

    @app.post("/_web/mcp/tokens", include_in_schema=False)
    async def create(request: Request, body: TokenRequest) -> JSONResponse:
        if getattr(request.state, "auth", None) not in {"session", "device"}:
            return JSONResponse({"error": "Refused: only your own signed-in app makes tokens."}, status_code=FORBIDDEN)
        try:
            record, token = tokens.create(body.actor, body.scope, timedelta(days=body.days))
        except HeinzelError as exc:
            return JSONResponse({"error": str(exc)}, status_code=FORBIDDEN)
        return JSONResponse({**_public(record), "token": token})

    @app.delete("/_web/mcp/tokens/{which}", include_in_schema=False)
    async def revoke(which: str) -> JSONResponse:
        gone = tokens.revoke(which)
        status_code = 200 if gone else NOT_FOUND
        return JSONResponse({"revoked": [_public(record) for record in gone]}, status_code=status_code)
