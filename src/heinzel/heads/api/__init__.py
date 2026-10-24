"""The REST head: every operation becomes an HTTP endpoint with an OpenAPI schema (served at `/docs`).

Read-only operations are `GET`, the others `POST`; parameters are query parameters described by their `Field`. A
`HeinzelError` becomes `400 {"error": ...}`; an operation whose exit code would be non-zero answers `503`, so
`GET /system/doctor` doubles as a health probe.

The server has no authentication, so it answers only requests that name a loopback host (or one listed in
`[hz] api_allowed_hosts`) on its own port, and refuses requests from another origin with `403`: a web page in your
browser can reach `127.0.0.1` too, directly or through DNS rebinding, and must not be able to call operations.
Operations that spend money or act unattended (`hz workflow run`, `hz system daemon tick`) aren't exposed here.

The web head (`hz ui`) mounts the same endpoints with `head="web"` and a per-session `token`: every request must then
carry the token as `Authorization: Bearer <token>` as well, and the operations that list the `web` head are exposed too.
It also gets `POST /stream/<path>` for each of them: the same operation, answering with server-sent events as it runs
(`heinzel.heads._stream`, ADR 0045).
"""

import hmac
import inspect
from collections.abc import Awaitable, Callable, Iterable
from typing import Annotated, Any, get_args, get_origin

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic.fields import FieldInfo

from heinzel import __version__
from heinzel.heads._exposure import Exposed, exposed
from heinzel.heads._presence import mark_presence
from heinzel.heads._stream import streamed
from heinzel.kernel import HeinzelError, Kernel, boot

HEAD = "api"
UNAVAILABLE = 503
FORBIDDEN = 403
UNAUTHORIZED = 401
DEFAULT_PORTS = frozenset({80, 443})
LOOPBACK = frozenset({"localhost", "127.0.0.1", "[::1]"})
"""Hosts the REST head always answers; `[hz] api_allowed_hosts` adds others."""


def build(  # noqa: PLR0913 - the head, where it listens and its credential, all by keyword
    kernel: Kernel,
    *,
    port: int | None = None,
    allowed_hosts: Iterable[str] = (),
    head: str = HEAD,
    token: str | None = None,
    docs: bool = True,
    accept: Callable[[str], str | None] | None = None,
) -> FastAPI:
    """A FastAPI app with one endpoint per exposed operation, answering only local, same-origin requests.

    Args:
        kernel: The kernel whose operations become endpoints.
        port: The port it listens on; a request naming another port is refused. Any port when None.
        allowed_hosts: Host names to answer besides localhost, 127.0.0.1 and [::1].
        head: Whose operations to expose: `api`, or `web` for the web head, which sets a `token` too.
        token: When set, every request must carry it as `Authorization: Bearer <token>`.
        docs: Serve the interactive docs at `/docs`; they load scripts from a CDN, which the web head's CSP forbids.
        accept: With a `token`, which other bearer token is accepted too (the web head's device tokens): it returns
            the device's id, or None. A request records what it carried in `request.state.auth` (`session` or
            `device`) and, for a device, its id in `request.state.device`.
    """
    app = FastAPI(
        title="🧝 Heinzelmännchen",
        version=__version__,
        description="Open-source helpers that keep product teams running. Every endpoint is also a `hz` command.",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
    )
    hosts = LOOPBACK | {host.strip().lower() for host in allowed_hosts}
    bearer = f"Bearer {token}".encode() if token is not None else b""

    @app.middleware("http")
    async def local_only(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        headers = request.headers
        if problem := refusal(
            headers.get("host", ""), headers.get("origin"), hosts, port, headers.get("sec-fetch-site")
        ):
            return JSONResponse(status_code=FORBIDDEN, content={"error": problem})
        if token is not None and not _authorized(request, headers.get("authorization", ""), bearer, accept):
            # A header, not a cookie: another site can make the browser send cookies along, never this header.
            problem = "Refused: send the session token `hz ui` opened the app with, as `Authorization: Bearer <token>`."
            return JSONResponse(
                status_code=UNAUTHORIZED, content={"error": problem}, headers={"WWW-Authenticate": "Bearer"}
            )
        response = await call_next(request)
        if token is not None:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HeinzelError)
    async def heinzel_error(request: object, exc: HeinzelError) -> JSONResponse:  # noqa: ARG001 - FastAPI signature
        return JSONResponse(status_code=400, content={"error": str(exc)})

    for item in exposed(kernel, head):
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
        if token is not None:  # streams only behind a token: the web head's
            app.add_api_route(
                "/stream/" + "/".join(item.path),
                stream_endpoint(kernel, item),
                methods=["POST"],
                include_in_schema=False,
            )
    return app


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


def refusal(
    host: str, origin: str | None, hosts: frozenset[str], port: int | None, fetch_site: str | None = None
) -> str | None:
    """Why a request is refused, or None: its Host must be an allowed name on our port, its Origin our own.

    Without an Origin, a browser's `Sec-Fetch-Site: cross-site` or `same-site` still marks a request another page
    made. On port 80 or 443 a Host without a port is the same port.
    """
    name, given = _split(host)
    default = port in DEFAULT_PORTS and given == ""
    if name not in hosts or (port is not None and given != str(port) and not default):
        where = f" on port {port}" if port is not None else ""
        return (
            f"Refused: this server answers requests to {', '.join(sorted(hosts))}{where}, not {host!r}. If you serve "
            "on another interface, add its name to [hz] api_allowed_hosts in your user configuration."
        )
    if origin is not None and origin.strip().lower() not in {f"http://{host.lower()}", f"https://{host.lower()}"}:
        return f"Refused: a request from another origin ({origin}) can't call operations; use the CLI or MCP."
    if origin is None and (fetch_site or "").strip().lower() in {"cross-site", "same-site"}:
        return f"Refused: a request another site made (Sec-Fetch-Site: {fetch_site}) can't call operations."
    return None


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
    """A function with the operation's public parameters as query parameters, run through the kernel."""

    def run(response: Response, **kwargs: Any) -> Any:  # noqa: ANN401 - whatever the operation returns
        mark_presence(kernel, item.record.qualname)
        result = kernel.invoke(item.operation, **kwargs)
        if item.operation.exit_code is not None and item.operation.exit_code(result):
            response.status_code = UNAVAILABLE
        return result

    response_parameter = inspect.Parameter("response", inspect.Parameter.KEYWORD_ONLY, annotation=Response)
    run.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [_query(p) for p in item.parameters] + [response_parameter], return_annotation=item.returns
    )
    run.__name__ = item.name
    return run


def stream_endpoint(kernel: Kernel, item: Exposed) -> Callable[..., Any]:
    """Like `endpoint`, but the answer is the operation's events as a server-sent event stream."""

    async def run(**kwargs: Any) -> Any:  # noqa: ANN401 - a streaming response
        return streamed(kernel, item.operation, kwargs)

    run.__signature__ = inspect.Signature([_query(p) for p in item.parameters])  # type: ignore[attr-defined]
    run.__name__ = f"{item.name}_stream"
    return run


def _query(parameter: inspect.Parameter) -> inspect.Parameter:
    annotation, description = parameter.annotation, None
    if get_origin(annotation) is Annotated:
        annotation, *metadata = get_args(annotation)
        description = next((m.description for m in metadata if isinstance(m, FieldInfo)), None)
    return parameter.replace(
        annotation=Annotated[annotation, Query(description=description)], kind=inspect.Parameter.KEYWORD_ONLY
    )


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Boot the kernel and serve its operations over HTTP until interrupted."""
    import uvicorn  # noqa: PLC0415 - only needed when serving

    kernel = boot()
    uvicorn.run(build(kernel, port=port, allowed_hosts=kernel.settings.api_allowed_hosts), host=host, port=port)
