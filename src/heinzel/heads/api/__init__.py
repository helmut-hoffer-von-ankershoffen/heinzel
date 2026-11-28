"""The REST head: every operation becomes an HTTP endpoint with an OpenAPI schema (served at `/docs`).

Read-only operations default to `GET` and the others to `POST`. Private operations may opt into a bounded JSON body
and `POST` even for reads. A `HeinzelError` becomes `400 {"error": ...}`. An operation whose exit code would be
non-zero answers `503`, so `GET /system/doctor` doubles as a health probe.

The default server has no authentication, so it answers only requests that name a loopback host (or one listed in
`[hz] api_allowed_hosts`) on its own port, and refuses requests from another origin with `403`: a web page in your
browser can reach `127.0.0.1` too, directly or through DNS rebinding, and must not be able to call operations.
Operations that spend money or act unattended (`hz heinzel wake`, `hz system daemon tick`) aren't exposed here.

The web head (`hz ui serve`) mounts the same endpoints with `head="web"` and a per-session `token`:
every request must then
carry the token as `Authorization: Bearer <token>` as well, and the operations that list the `web` head are exposed too.
An explicit private API uses an expiring server-scoped bearer (`hz system serve api --private`), includes declared
private operations, and shares the kernel's trusted CurrentUser context (ADR 0068). It never treats a bearer as
human approval. Authenticated heads also get `POST /stream/<path>` for each operation, answering with server-sent
events as it runs
(`heinzel.heads._stream`, ADR 0045).
"""

import hmac
import inspect
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer

from heinzel import __version__
from heinzel.heads import _kit
from heinzel.heads._exposure import API_PRIVATE, exposed
from heinzel.heads._kit import API_HOST_HELP as API_HOST_HELP
from heinzel.heads._kit import DEFAULT_PORTS as DEFAULT_PORTS
from heinzel.heads._kit import FORBIDDEN as FORBIDDEN
from heinzel.heads._kit import LOOPBACK as LOOPBACK
from heinzel.heads._kit import WEB_HOST_HELP as WEB_HOST_HELP
from heinzel.heads._kit import refusal as refusal
from heinzel.heads._kit import request_refusal as request_refusal
from heinzel.heads._served_tools import bind_served_tools
from heinzel.heads._stream import error_body
from heinzel.heads.api import _routes
from heinzel.heads.api._auth import ApiBearer
from heinzel.heads.api._routes import UNAVAILABLE, bounded_body_route, normal_body, parameters
from heinzel.heads.api._routes import endpoint as endpoint
from heinzel.heads.api._routes import stream_endpoint as stream_endpoint
from heinzel.kernel import CallIdentity, CurrentUser, HeinzelError, Kernel, boot
from heinzel.kernel._errors import NotSupportedError as NotSupportedError
from heinzel.kernel._errors import SubscriptionUsageLimitError as SubscriptionUsageLimitError
from heinzel.kernel._identity import LOCAL_PERMISSIONS

HEAD = "api"
UNAUTHORIZED = 401
_arguments = _routes.arguments
_bounded_body_route = bounded_body_route
_normal_body = normal_body
_parameters = parameters
_query = _routes.query_parameter


def _error_response(exc: HeinzelError, *, private: bool) -> JSONResponse:
    return JSONResponse(status_code=400, content=error_body(exc, private=private))


def build(  # noqa: PLR0913 - the head, where it listens and its credential, all by keyword
    kernel: Kernel,
    *,
    port: int | None = None,
    allowed_hosts: Iterable[str] = (),
    allowed_origins: frozenset[str] | Callable[[], frozenset[str]] = frozenset(),
    head: str = HEAD,
    token: str | None = None,
    docs: bool = True,
    accept: Callable[[str], str | None] | None = None,
    credentials: ApiBearer | None = None,
    audience: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    """A FastAPI app with one endpoint per exposed operation, answering only local, same-origin requests.

    Args:
        kernel: The kernel whose operations become endpoints.
        port: The port it listens on; a request naming another port is refused. Any port when None.
        allowed_hosts: Host names to answer besides localhost, 127.0.0.1 and [::1].
        allowed_origins: Exact canonical HTTPS reverse-proxy origins, supplied explicitly by the authenticated web head.
        head: Whose operations to expose: `api`, or `web` for the web head, which sets a `token` too.
        token: When set, every request must carry it as `Authorization: Bearer <token>`.
        docs: Serve the interactive docs at `/docs`; they load scripts from a CDN, which the web head's CSP forbids.
        accept: With a `token`, which other bearer token is accepted too (the web head's device tokens): it returns
            the device's id, or None. A request records what it carried in `request.state.auth` (`session` or
            `device`) and, for a device, its id in `request.state.device`.
        credentials: A dedicated expiring private-API bearer; requires its exact server audience.
        audience: The identity of this server, independent of caller-supplied headers.
        clock: The trusted clock for credential expiry checks.

    Raises:
        HeinzelError: If a private head has no credential or a credential has no server audience.
    """
    _validate_auth(head, token, credentials, audience)
    selected = API_PRIVATE if credentials is not None and head == HEAD else head
    user = kernel.resolve(CurrentUser)
    bind_served_tools(kernel)
    app = FastAPI(
        title="Heinzelmännchen",
        version=__version__,
        description=(
            "Durable agents accelerating product teams, like the little elves from Cologne. "
            "Every endpoint is also a `hz` command."
        ),
        docs_url="/docs" if docs and credentials is None else None,
        redoc_url="/redoc" if docs and credentials is None else None,
        # Middleware verifies the credential; this dependency describes its header in authenticated OpenAPI.
        dependencies=[Depends(HTTPBearer(auto_error=False, scheme_name="PrivateApiBearer"))]
        if credentials is not None
        else [],
    )
    hosts = _kit.LOOPBACK | {host.strip().lower() for host in allowed_hosts}
    bearer = f"Bearer {token}".encode() if token is not None else b""

    @app.middleware("http")
    async def local_only(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        headers = request.headers
        active_origins = allowed_origins() if callable(allowed_origins) else allowed_origins
        if problem := request_refusal(
            headers,
            hosts,
            port,
            allowed_origins=active_origins,
            host_help=WEB_HOST_HELP if head == "web" else API_HOST_HELP,
        ):
            return JSONResponse(status_code=FORBIDDEN, content={"error": problem})
        identity: CallIdentity | None = None
        if credentials is not None:
            identity = credentials.authenticate(headers.get("authorization", ""), audience or "", now=clock())
        elif token is not None and _authorized(request, headers.get("authorization", ""), bearer, accept):
            method: Literal["web-device", "web-session"] = (
                "web-device" if request.state.auth == "device" else "web-session"
            )
            identity = CallIdentity(
                user.owner(), method, getattr(request.state, "device", "session"), "hz-web", LOCAL_PERMISSIONS
            )
        if (token is not None or credentials is not None) and identity is None:
            problem = "Refused: supply Authorization: Bearer with a valid, unexpired credential for this server"
            return JSONResponse(
                status_code=UNAUTHORIZED, content={"error": problem}, headers={"WWW-Authenticate": "Bearer"}
            )
        # Explicitly clear inherited identity for public REST, even if its server was started from a local call.
        with user.using(identity):
            response = await call_next(request)
        if token is not None or credentials is not None:
            response.headers["Cache-Control"] = "no-store"
        return response

    app.exception_handler(RequestValidationError)(_invalid_request)

    @app.exception_handler(HeinzelError)
    async def heinzel_error(request: object, exc: HeinzelError) -> JSONResponse:  # noqa: ARG001 - FastAPI signature
        return _error_response(exc, private=head == "web" or selected == API_PRIVATE)

    for item in exposed(kernel, selected):
        # The private body model and byte limit apply equally to the normal and streaming routes.
        limit = item.operation.request_body_limit
        route_class = bounded_body_route(limit) if limit is not None else None
        body_parameters = parameters(item, body=True) if normal_body(item) else None
        returns = item.returns if item.returns is not inspect.Signature.empty else None
        app.router.add_api_route(
            "/" + "/".join(item.path),
            endpoint(kernel, item, body_parameters=body_parameters),
            methods=[item.operation.http_method],
            summary=item.operation.summary,
            description=inspect.getdoc(item.operation.func) or "",
            response_model=returns,
            # The same result when its exit code is non-zero (doctor says DOWN), so clients can read it there too.
            responses={UNAVAILABLE: {"model": returns, "description": "The result, when its exit code is non-zero."}}
            if item.operation.exit_code is not None and returns is not None
            else None,
            tags=[item.record.path[0]],
            operation_id=item.name,
            route_class_override=route_class,
        )
        if token is not None or credentials is not None:  # Streams retain the authenticated call context.
            app.router.add_api_route(
                "/stream/" + "/".join(item.path),
                stream_endpoint(kernel, item, body_parameters=body_parameters),
                methods=["POST"],
                include_in_schema=False,
                route_class_override=route_class,
            )
    return app


async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:  # noqa: ARG001 - FastAPI
    """FastAPI's 422 without the refused `input` and `ctx`: a refused body may be a secret, e.g. an overlong API key."""
    detail = [
        {"type": str(error.get("type", "")), "loc": list(error.get("loc", ())), "msg": str(error.get("msg", ""))}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


def _validate_auth(head: str, token: str | None, credentials: ApiBearer | None, audience: str | None) -> None:
    if head not in {HEAD, "web", API_PRIVATE}:
        msg = "The API can expose only public, authenticated private or paired web operations"
        raise HeinzelError(msg)
    if head == API_PRIVATE and credentials is None:
        msg = "The private API head requires an expiring server-scoped credential"
        raise HeinzelError(msg)
    if head == "web" and token is None and credentials is None:
        msg = "Private API and web heads require authentication before routes are built"
        raise HeinzelError(msg)
    if token == "" or (
        credentials is not None and (token is not None or not audience or audience != credentials.audience)
    ):
        msg = "Private API authentication requires a nonempty credential and matching server audience"
        raise HeinzelError(msg)


def _authorized(request: Request, header: str, bearer: bytes, accept: Callable[[str], str | None] | None) -> bool:
    """Whether `header` carries the session token or, through `accept`, another one; notes which in the request."""
    if hmac.compare_digest(header.encode(), bearer):
        request.state.auth = "session"
        return True
    scheme, _, presented = header.partition(" ")
    if accept is not None and scheme == "Bearer" and presented and (device := accept(presented)) is not None:
        request.state.auth = "device"
        request.state.device = device
        return True
    return False


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    private: bool = False,
    credential_file: Path | None = None,
    announce: Callable[[str], None] | None = None,
) -> None:
    """Boot the kernel and serve public operations, or authenticated private operations with an owner-only credential.

    Raises:
        HeinzelError: If private identity or credential storage is unsafe.
    """
    import secrets  # noqa: PLC0415 - only needed when starting a private server

    import uvicorn  # noqa: PLC0415 - only needed when serving

    kernel = boot()
    if not private:
        uvicorn.run(build(kernel, port=port, allowed_hosts=kernel.settings.api_allowed_hosts), host=host, port=port)
        return
    audience = f"hz-private-api:{secrets.token_hex(16)}"
    credential = ApiBearer.issue(kernel.resolve(CurrentUser).owner(), audience)
    destination = (
        credential_file or kernel.resolve(CurrentUser).directory.parent / "api" / f"{credential.credential_id}.json"
    )
    credential.write(destination)
    if announce is not None:
        announce(f"Private API credential saved to {destination}; expires {credential.expires_at.isoformat()}")
    app = build(
        kernel,
        port=port,
        allowed_hosts=kernel.settings.api_allowed_hosts,
        credentials=credential,
        audience=audience,
    )
    uvicorn.run(app, host=host, port=port)
