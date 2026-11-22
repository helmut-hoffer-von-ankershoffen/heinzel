"""The route of the workspace handshake: the facts come from the client context, the head only serves them."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Response

from heinzel.kernel import Kernel
from heinzel.modules.system.client import WorkspaceVersion, observe_workspace_version


def add_workspace_version(app: FastAPI, kernel: Kernel) -> None:
    """Expose transport status only behind the web API's existing authentication middleware."""
    initialized = datetime.now(UTC)

    @app.get("/workspace-version", tags=["web"])
    def workspace_version(response: Response) -> WorkspaceVersion:
        response.headers["Cache-Control"] = "no-store"
        return observe_workspace_version(kernel, initialized)


def add_connected_clients(app: FastAPI, directory: Path) -> None:
    """List which desktop apps connected, newest first, behind the API's authentication."""
    # Imported here: a published process finishing its update has the package's old names (cross-version test).
    from heinzel.modules.system.client import ConnectedClient, ConnectedClients  # noqa: PLC0415 - late: old names in a swapped process

    clients = ConnectedClients(directory)

    @app.get("/connected-clients", tags=["web"])
    def connected_clients(response: Response) -> list[ConnectedClient]:
        response.headers["Cache-Control"] = "no-store"
        return clients.recorded()
