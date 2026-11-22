"""The route of the workspace handshake: the facts come from the client context, the head only serves them."""

from datetime import UTC, datetime

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
