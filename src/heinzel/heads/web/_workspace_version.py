"""Authenticated, explicitly observed workspace versions; never infer a running daemon from its registration."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Response
from pydantic import BaseModel, Field

from heinzel import __version__
from heinzel.kernel import HeinzelError, Installation, Kernel, OperationLet

MAX_INTERVAL_MINUTES = 1440


class WorkspaceWebVersion(BaseModel):
    """The answering server's own loaded code and when this server instance was created."""

    version: str = Field(description="Core version loaded by the answering web server.")
    observed_since: datetime = Field(description="When this server instance was initialized; not process start time.")
    running_since: datetime | None = Field(default=None, description="Unknown without a verified process start time.")


class WorkspaceInstalledVersion(BaseModel):
    """The separately inspected installation, not a guess from the web server's imported package."""

    version: str | None = Field(default=None, description="Verified installed Core version, or null if unavailable.")
    updated_at: datetime | None = Field(default=None, description="Unknown unless an installation event is verified.")


class WorkspaceDaemonVersion(BaseModel):
    """The daemon's last verified tick, distinct from a currently running process."""

    running_version: str | None = Field(default=None, description="Unknown without a current process attestation.")
    running_since: datetime | None = Field(default=None, description="Unknown without a verified process start time.")
    last_observed_version: str | None = Field(
        default=None, description="Version reported by the last manager-bound tick."
    )
    last_observed_at: datetime | None = Field(default=None, description="When that manager-bound tick started.")
    observation_stale: bool | None = Field(
        default=None, description="Whether the last tick exceeds three recorded intervals; null when not calculable."
    )
    registered_at: datetime | None = Field(
        default=None,
        description="Time in the unit's install marker, not proof of current registration or a Core update time.",
    )
    loaded: bool | None = Field(
        default=None, description="True when the manager verified the unit loaded; otherwise unknown."
    )
    owned: bool | None = Field(
        default=None, description="True when the selected unit's manager identity was verified; otherwise unknown."
    )
    status_observed_at: datetime | None = Field(default=None, description="When this daemon status was obtained.")


class WorkspaceVersion(BaseModel):
    """Version facts read on this request, with missing evidence represented as null."""

    observed_at: datetime = Field(description="When this response's observations started.")
    web: WorkspaceWebVersion
    installed: WorkspaceInstalledVersion
    daemon: WorkspaceDaemonVersion


def _installed(kernel: Kernel) -> WorkspaceInstalledVersion:
    try:
        installation = kernel.resolve(Installation)
        if installation.is_tool:
            return WorkspaceInstalledVersion(version=installation.installed_core_version())
    except (HeinzelError, OSError, ValueError):
        pass
    return WorkspaceInstalledVersion()


def _past(value: object, now: datetime) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value if value <= now else None


def _daemon(kernel: Kernel, now: datetime) -> WorkspaceDaemonVersion:
    try:
        operation = next(
            (
                op
                for record, op in kernel.lets(OperationLet)
                if (record.qualname, op.name) == ("system.daemon", "status")
            ),
            None,
        )
        if (
            operation is None
            or not operation.read_only
            or operation.private
            or (operation.heads is not None and "web" not in operation.heads)
            or not kernel.available(operation.func)
        ):
            return WorkspaceDaemonVersion()
        status: Any = kernel.invoke(operation)
        if status is None:
            return WorkspaceDaemonVersion()
        observed = _past(getattr(status, "last_tick", None), now)
        version = getattr(status, "version", None)
        version = version if observed is not None and isinstance(version, str) and version else None
        interval = getattr(status, "last_tick_interval_minutes", None)
        stale = (
            (now - observed > timedelta(minutes=3 * interval))
            if (
                observed is not None
                and version is not None
                and type(interval) is int
                and 0 < interval <= MAX_INTERVAL_MINUTES
            )
            else None
        )
        return WorkspaceDaemonVersion(
            last_observed_version=version,
            last_observed_at=observed if version is not None else None,
            observation_stale=stale,
            registered_at=_past(getattr(status, "installed_at", None), now),
            loaded=True if getattr(status, "loaded", None) is True else None,
            owned=True if getattr(status, "owned", None) is True else None,
            status_observed_at=datetime.now(UTC),
        )
    except Exception:  # noqa: BLE001 - an unavailable status is not evidence about the daemon
        return WorkspaceDaemonVersion()


def add_workspace_version(app: FastAPI, kernel: Kernel) -> None:
    """Expose transport status only behind the web API's existing authentication middleware."""
    initialized = datetime.now(UTC)

    @app.get("/workspace-version", tags=["web"])
    def workspace_version(response: Response) -> WorkspaceVersion:
        response.headers["Cache-Control"] = "no-store"
        now = datetime.now(UTC)
        return WorkspaceVersion(
            observed_at=now,
            web=WorkspaceWebVersion(version=__version__, observed_since=initialized),
            installed=_installed(kernel),
            daemon=_daemon(kernel, now),
        )
