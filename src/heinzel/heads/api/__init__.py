"""The REST head: every operation becomes an HTTP endpoint with an OpenAPI schema (served at `/docs`).

Read-only operations are `GET`, the others `POST`; parameters are query parameters described by their `Field`. A
`HeinzelError` becomes `400 {"error": ...}`; an operation whose exit code would be non-zero answers `503`, so
`GET /system/doctor` doubles as a health probe.

The default server has no authentication, so it answers only requests that name a loopback host (or one listed in
`[hz] api_allowed_hosts`) on its own port, and refuses requests from another origin with `403`: a web page in your
browser can reach `127.0.0.1` too, directly or through DNS rebinding, and must not be able to call operations.
Operations that spend money or act unattended (`hz heinzel wake`, `hz system daemon tick`) aren't exposed here.

The web head (`hz ui`) mounts the same endpoints with `head="web"` and a per-session `token`: every request must then
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
from typing import Annotated, Any, Literal, get_args, get_origin

from fastapi import Body, Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from pydantic import ConfigDict, create_model
from pydantic.fields import FieldInfo
from starlette.datastructures import Headers

from heinzel import __version__
from heinzel.heads._exposure import API_PRIVATE, Exposed, exposed
from heinzel.heads._presence import mark_authenticated_presence
from heinzel.heads._stream import streamed
from heinzel.heads.api._auth import ApiBearer
from heinzel.kernel import CallIdentity, CurrentUser, HeinzelError, Kernel, boot
from heinzel.kernel._identity import LOCAL_PERMISSIONS

HEAD = "api"
UNAVAILABLE = 503
FORBIDDEN = 403
UNAUTHORIZED = 401
DEFAULT_PORTS = frozenset({80, 443})
LOOPBACK = frozenset({"localhost", "127.0.0.1", "[::1]"})
"""Hosts the REST head always answers; `[hz] api_allowed_hosts` adds others."""
API_HOST_HELP = "If you serve on another interface, add its name to [hz] api_allowed_hosts in your user configuration."
WEB_HOST_HELP = (
    "For a private HTTPS proxy, add its exact HTTPS origin to [hz] ui_allowed_origins in your user configuration "
    "and restart hz ui. api_allowed_hosts configures the REST head only."
)


def build(  # noqa: PLR0913 - the head, where it listens and its credential, all by keyword
    kernel: Kernel,
    *,
    port: int | None = None,
    allowed_hosts: Iterable[str] = (),
    allowed_origins: frozenset[str] = frozenset(),
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
    app = FastAPI(
        title="Heinzelmännchen",
        version=__version__,
        description="Helpers accelerating product teams. Every endpoint is also a `hz` command.",
        docs_url="/docs" if docs and credentials is None else None,
        redoc_url="/redoc" if docs and credentials is None else None,
        # Middleware verifies the credential; this dependency describes its header in authenticated OpenAPI.
        dependencies=[Depends(HTTPBearer(auto_error=False, scheme_name="PrivateApiBearer"))]
        if credentials is not None
        else [],
    )
    hosts = LOOPBACK | {host.strip().lower() for host in allowed_hosts}
    bearer = f"Bearer {token}".encode() if token is not None else b""

    @app.middleware("http")
    async def local_only(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        headers = request.headers
        if problem := request_refusal(
            headers,
            hosts,
            port,
            allowed_origins=allowed_origins,
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

    @app.exception_handler(HeinzelError)
    async def heinzel_error(request: object, exc: HeinzelError) -> JSONResponse:  # noqa: ARG001 - FastAPI signature
        return JSONResponse(status_code=400, content={"error": str(exc)})

    for item in exposed(kernel, selected):
        returns = item.returns if item.returns is not inspect.Signature.empty else None
        app.add_api_route(
            "/" + "/".join(item.path),
            endpoint(kernel, item),
            methods=["GET" if item.operation.read_only else "POST"],
            summary=item.operation.summary,
            description=inspect.getdoc(item.operation.func) or "",
            response_model=returns,
            # The same result when its exit code is non-zero (doctor says DOWN), so clients can read it there too.
            responses={UNAVAILABLE: {"model": returns, "description": "The result, when its exit code is non-zero."}}
            if item.operation.exit_code is not None and returns is not None
            else None,
            tags=[item.record.path[0]],
            operation_id=item.name,
        )
        if token is not None or credentials is not None:  # Streams retain the authenticated call context.
            app.add_api_route(
                "/stream/" + "/".join(item.path),
                stream_endpoint(kernel, item),
                methods=["POST"],
                include_in_schema=False,
            )
    return app


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


def request_refusal(
    headers: Headers,
    hosts: frozenset[str],
    port: int | None,
    *,
    allowed_origins: frozenset[str] = frozenset(),
    host_help: str = API_HOST_HELP,
) -> str | None:
    """Apply one authority policy to HTTP and WebSocket headers, refusing ambiguous duplicate headers."""
    if len(headers.getlist("host")) != 1 or any(
        len(headers.getlist(name)) > 1 for name in ("origin", "sec-fetch-site")
    ):
        return "Refused: supply exactly one Host and at most one Origin and Sec-Fetch-Site header."
    return refusal(
        headers.get("host", ""),
        headers.get("origin"),
        hosts,
        port,
        headers.get("sec-fetch-site"),
        allowed_origins=allowed_origins,
        host_help=host_help,
    )


def refusal(  # noqa: PLR0913 - the caller headers and the explicit server authority policy
    host: str,
    origin: str | None,
    hosts: frozenset[str],
    port: int | None,
    fetch_site: str | None = None,
    *,
    allowed_origins: frozenset[str] = frozenset(),
    host_help: str = API_HOST_HELP,
) -> str | None:
    """Why a request is refused, or None: its Host must be an allowed name on our port, its Origin our own.

    Without an Origin, a browser's `Sec-Fetch-Site: cross-site` or `same-site` still marks a request another page
    made. On port 80 or 443 a Host without a port is the same port.
    """
    if any(char.isspace() or char in "\\/@?#," or char < " " or char == "\x7f" for char in host):
        return "Refused: malformed Host authority."
    if fetch_site is not None and fetch_site.strip().lower() not in {"none", "same-origin"}:
        return f"Refused: a request another site made (Sec-Fetch-Site: {fetch_site}) can't call operations."
    external = next((value for value in allowed_origins if host.lower() in _authorities(value)), None)
    if external is not None:
        expected = {f"https://{name}" for name in _authorities(external)}
    else:
        name, given = _split(host)
        default = port in DEFAULT_PORTS and given == ""
        if name not in hosts or (port is not None and given != str(port) and not default):
            where = f" on port {port}" if port is not None else ""
            return (
                f"Refused: this server answers requests to {', '.join(sorted(hosts))}{where}, not {host!r}. {host_help}"
            )
        expected = {f"http://{host.lower()}", f"https://{host.lower()}"}
    if origin is not None and origin.lower() not in expected:
        return f"Refused: a request from another origin ({origin}) can't call operations; use the CLI or MCP."
    return None


def _authorities(origin: str) -> frozenset[str]:
    """The configured HTTPS authority, including the equivalent explicit default port."""
    authority = origin.removeprefix("https://")
    _, port = _split(authority)
    return frozenset({authority}) if port else frozenset({authority, f"{authority}:443"})


def _split(host: str) -> tuple[str, str]:
    """A Host header's name and port; `[::1]:8000` keeps its brackets."""
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return host, ""
        rest = host[end + 1 :]
        return host[: end + 1], rest[1:] if rest.startswith(":") else ""
    name, _, port = host.partition(":")
    return name, port


def endpoint(kernel: Kernel, item: Exposed) -> Callable[..., Any]:
    """Bind public parameters, keeping private mutation input in a strict JSON body."""
    body = item.operation.private and not item.operation.read_only

    def run(response: Response, **kwargs: Any) -> Any:  # noqa: ANN401 - whatever the operation returns
        mark_authenticated_presence(kernel, item.record.qualname)
        result = kernel.invoke(item.operation, **_arguments(item, kwargs, body=body))
        if item.operation.exit_code is not None and item.operation.exit_code(result):
            response.status_code = UNAVAILABLE
        return result

    response_parameter = inspect.Parameter("response", inspect.Parameter.KEYWORD_ONLY, annotation=Response)
    run.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [*_parameters(item, body=body), response_parameter], return_annotation=item.returns
    )
    run.__name__ = item.name
    return run


def stream_endpoint(kernel: Kernel, item: Exposed) -> Callable[..., Any]:
    """Like `endpoint`, but the answer is the operation's events as a server-sent event stream."""

    async def run(**kwargs: Any) -> Any:  # noqa: ANN401 - a streaming response
        return streamed(
            kernel, item.operation, _arguments(item, kwargs, body=item.operation.private), module=item.record.qualname
        )

    run.__signature__ = inspect.Signature(_parameters(item, body=item.operation.private))  # type: ignore[attr-defined]
    run.__name__ = f"{item.name}_stream"
    return run


def _parameters(item: Exposed, *, body: bool) -> list[inspect.Parameter]:
    if not body:
        return [_query(parameter) for parameter in item.parameters]
    fields: dict[str, Any] = {  # runtime function annotations become Pydantic field definitions
        parameter.name: (
            parameter.annotation,
            ... if parameter.default is inspect.Parameter.empty else parameter.default,
        )
        for parameter in item.parameters
    }
    model = create_model(f"{item.name}_Input", __config__=ConfigDict(extra="forbid"), **fields)
    return [
        inspect.Parameter(
            "_input",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[model, Body()],
            default=inspect.Parameter.empty if item.parameters else model(),
        )
    ]


def _arguments(item: Exposed, values: dict[str, Any], *, body: bool) -> dict[str, Any]:
    if not body:
        return values
    # FastAPI's strict model refuses unknown fields; the kernel still owns operation validation and enforcement.
    return {parameter.name: getattr(values["_input"], parameter.name) for parameter in item.parameters}


def _query(parameter: inspect.Parameter) -> inspect.Parameter:
    annotation, description = parameter.annotation, None
    if get_origin(annotation) is Annotated:
        annotation, *metadata = get_args(annotation)
        description = next((m.description for m in metadata if isinstance(m, FieldInfo)), None)
    return parameter.replace(
        annotation=Annotated[annotation, Query(description=description)], kind=inspect.Parameter.KEYWORD_ONLY
    )


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
