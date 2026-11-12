"""One local-owner web sign-in using OpenAI's loopback callback, with no credentials in the app UI."""

import asyncio
import logging
import secrets
import webbrowser
from collections.abc import Callable
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from heinzel import CurrentUser, Kernel
from heinzel.kernel import AiAccessWebAttempt, AiAccessWebSignIn
from heinzel.kernel._logging import log_event

START = "/ai/access/chatgpt-sign-in"
CALLBACK = "/auth/callback"
FINISHED = "/auth/chatgpt-finished"
_CALLBACK_QUERY_LIMIT = 8192
_PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port")
SIGN_IN_TIMEOUT = 300.0
_NO_STORE = {"Cache-Control": "no-store"}
logger = logging.getLogger(__name__)

Outcome = Literal["connected", "declined", "failed", "cancelled", "expired", "browser-unavailable"]


class ChatGptSignInResult(BaseModel):
    """Only the outcome of this sign-in. OAuth data stays on the host."""

    outcome: Outcome


def _local(request: Request, port: int | None) -> bool:
    return (
        port is not None
        and request.client is not None
        and request.client.host == "127.0.0.1"
        and request.url.scheme == "http"
        and request.headers.getlist("host") == [f"127.0.0.1:{port}"]
        and not any(name in request.headers for name in _PROXY_HEADERS)
    )


def local_callback_navigation(request: Request, port: int | None) -> bool:
    """Only these local GET navigations may bypass the web head's cross-site rejection for OAuth."""
    return request.method == "GET" and request.url.path in {CALLBACK, FINISHED} and _local(request, port)


async def _disconnected(request: Request) -> None:
    while (await request.receive())["type"] != "http.disconnect":
        pass


async def _keep(commit: Callable[[], bool]) -> Outcome:
    try:
        granted = await asyncio.to_thread(commit)
    except Exception:  # noqa: BLE001 - a credential backend may include secrets in error text
        return "failed"
    else:
        return "connected" if granted else "declined"


class ChatGptSignIn:
    """Own a single cancellable attempt for this web server instance."""

    def __init__(self, kernel: Kernel, port: int | None) -> None:
        """Use the already listening web server as the exact loopback OAuth callback."""
        self._kernel = kernel
        self._port = port
        self._callback: asyncio.Future[tuple[str, str | None] | None] | None = None
        self._login: AiAccessWebAttempt | None = None

    async def start(self, request: Request) -> ChatGptSignInResult | Response:
        """Start a browser sign-in from the locally authenticated owning web session."""
        identity = self._kernel.resolve(CurrentUser).context()
        origin = f"http://127.0.0.1:{self._port}"
        if (
            not _local(request, self._port)
            or identity.authentication != "web-session"
            or request.headers.getlist("origin") != [origin]
            or request.headers.getlist("sec-fetch-site") not in ([], ["same-origin"])
        ):
            return JSONResponse(
                {"error": "Open Heinzel in a local browser session on this computer to start ChatGPT sign-in."},
                status_code=403,
            )
        if (
            request.url.query
            or request.headers.get("content-length", "0") != "0"
            or "transfer-encoding" in request.headers
        ):
            return JSONResponse({"error": "ChatGPT sign-in takes no parameters."}, status_code=400)
        if self._callback is not None:
            return JSONResponse({"error": "A ChatGPT sign-in is already open."}, status_code=409)
        loop = asyncio.get_running_loop()
        self._callback = loop.create_future()
        disconnect = asyncio.create_task(_disconnected(request))
        deadline = loop.time() + SIGN_IN_TIMEOUT
        work = asyncio.create_task(self._run(f"{origin}{CALLBACK}", self._callback))
        outcome: Outcome = "cancelled"
        log_event(logger, "ai.access.chatgpt.web_sign_in", phase="start")
        try:
            done, _ = await asyncio.wait(
                (work, disconnect), timeout=SIGN_IN_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
            )
            if disconnect not in done:
                result = work.result() if work in done and loop.time() < deadline else "expired"
                # Let the approved keychain write finish on its own thread while this attempt retains the slot.
                outcome = await _keep(result) if callable(result) else result
            return ChatGptSignInResult(outcome=outcome)
        finally:
            work.cancel()
            disconnect.cancel()
            await asyncio.gather(work, disconnect, return_exceptions=True)
            self._callback = None
            self._login = None
            log_event(logger, "ai.access.chatgpt.web_sign_in", phase="finish", outcome=outcome)

    async def _run(
        self,
        redirect_uri: str,
        callback: asyncio.Future[tuple[str, str | None] | None],
    ) -> Outcome | Callable[[], bool]:
        try:
            access = self._kernel.resolve_builtin_service(AiAccessWebSignIn, "ai.access")
            self._login = login = await asyncio.to_thread(access.prepare_web_login, redirect_uri)
            # The system browser receives the OAuth URL. The initiating app only receives the result.
            if not await asyncio.to_thread(webbrowser.open, login.url, new=2):
                return "browser-unavailable"
            code = await callback
            if code is None:
                return "declined"
            commit = await login.exchange(*code)
        except Exception:  # noqa: BLE001 - OAuth or a credential backend may include secrets in error text
            return "failed"
        else:
            return commit

    async def callback(self, request: Request) -> Response:
        """Consume one matching OAuth state, never reflecting parameters in a response."""
        callback, login = self._callback, self._login
        if (
            not local_callback_navigation(request, self._port)
            or len(request.scope["query_string"]) > _CALLBACK_QUERY_LIMIT
            or callback is None
            or callback.done()
            or login is None
        ):
            return PlainTextResponse("There is no matching ChatGPT sign-in.", status_code=400, headers=_NO_STORE)
        query = request.query_params
        state = query.getlist("state")
        if len(state) != 1 or not secrets.compare_digest(state[0].encode(), login.state.encode()):
            return PlainTextResponse("There is no matching ChatGPT sign-in.", status_code=400, headers=_NO_STORE)
        if "error" in query:
            callback.set_result(None)
        else:
            code, client_id = query.getlist("code"), query.getlist("client_id")
            if len(code) != 1 or not code[0] or len(client_id) > 1:
                return PlainTextResponse("The ChatGPT callback was incomplete.", status_code=400, headers=_NO_STORE)
            callback.set_result((code[0], client_id[0] if client_id else None))
        return RedirectResponse(FINISHED, status_code=303, headers=_NO_STORE)

    async def finished(self, request: Request) -> Response:
        """Give the browser a clean address after receiving an OAuth redirect."""
        if not local_callback_navigation(request, self._port):
            return PlainTextResponse("This page is available on this computer.", status_code=403, headers=_NO_STORE)
        return PlainTextResponse("Return to Heinzel to check your ChatGPT sign-in.", headers=_NO_STORE)

    def add_start(self, app: FastAPI) -> None:
        """Put the initiating request behind the web API's bearer and same-origin checks."""
        app.add_api_route(
            START,
            self.start,
            methods=["POST"],
            response_model=ChatGptSignInResult,
            tags=["web"],
            operation_id="web_chatgpt_sign_in",
            summary="Start ChatGPT sign-in from the local owner's browser session.",
        )

    def add_callback(self, app: FastAPI) -> None:
        """Put only the OAuth return and its static completion page outside the API bearer check."""
        app.add_api_route(CALLBACK, self.callback, methods=["GET"], include_in_schema=False)
        app.add_api_route(FINISHED, self.finished, methods=["GET"], include_in_schema=False)
