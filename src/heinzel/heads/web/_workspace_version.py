"""The route of the workspace handshake: the facts come from the client context, the head only serves them."""

from datetime import UTC, datetime
from ipaddress import ip_address

from fastapi import FastAPI, Request, Response

from heinzel.kernel import Kernel
from heinzel.modules.system.client import WorkspaceVersion, observe_workspace_version

_MAX_PORT = 65_535


def _backend_ip(server: object) -> str | None:
    """Accept only a concrete local socket IP; never resolve names or trust forwarded headers."""
    match server:
        case [str() as host, int() as port] if "%" not in host and type(port) is int and 0 < port <= _MAX_PORT:
            pass
        case _:
            return None
    try:
        address = ip_address(host)
    except ValueError:
        return None
    return None if address.is_unspecified else str(address)


def add_workspace_version(app: FastAPI, kernel: Kernel) -> None:
    """Expose transport status only behind the web API's existing authentication middleware."""
    initialized = datetime.now(UTC)

    @app.get("/workspace-version", tags=["web"])
    def workspace_version(request: Request, response: Response) -> WorkspaceVersion:
        response.headers["Cache-Control"] = "no-store"
        result = observe_workspace_version(kernel, initialized)
        result.web.backend_ip = _backend_ip(request.scope.get("server"))
        return result
