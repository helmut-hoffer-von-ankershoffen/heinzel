"""After an update, each installed long-running head reports whether it now runs the installed version (ADR 0116).

An update replaces the tool folder, but a process that started before it keeps running the code it imported. The
updater asks every head it knows through this port, after the installation change is complete and no mutation is
pending: the head restarts through its own owned path and reports the version its live process loaded. The updater
never claims a head runs the new version without that observation.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

HeadName = Literal["daemon", "web", "notifier", "desktop"]
HeadState = Literal["verified", "pending", "failed", "not-installed", "self", "on-disk"]


class HeadRefresh(BaseModel):
    """What happened to one head; only `verified` is evidence of its running version."""

    head: HeadName = Field(description="The installed head: daemon, web, notifier or desktop.")
    status: HeadState = Field(
        description="verified: its live process reported the target version; pending: deferred or unobserved; "
        "failed: the head could not be refreshed or verified; not-installed: nothing to refresh; self: the "
        "process that ran the update hosts this head and restarts itself afterwards; on-disk: the desktop bundle "
        "was checked on disk."
    )
    version: str | None = Field(default=None, description="The version its live process reported, when observed.")
    detail: str = Field(default="", description="What happened, and the command that checks or finishes it.")


@dataclass(frozen=True, slots=True)
class WebHeadRefresh:
    """Restart the owned persistent web service into the installed version and observe its replacement.

    `refresh(target)` never raises: every outcome is a `HeadRefresh`. Only the module that owns the web service
    provides it; the updater resolves it by type, so neither imports the other.
    """

    refresh: Callable[[str], HeadRefresh]
    inspect: Callable[[str], HeadRefresh] | None = None


@dataclass(frozen=True, slots=True)
class DesktopHeadRefresh:
    """The manual CLI's managed-desktop boundary, using the verified tool generation after the update."""

    present: Callable[[], bool]
    eligible: Callable[[str], bool]
    refresh: Callable[[str, str], HeadRefresh]

    @contextmanager
    def invocation(self) -> Generator[None]:
        """Expose the desktop operation only while the manual CLI invokes `system update`."""
        token = _manual_desktop.set(self)
        try:
            yield
        finally:
            _manual_desktop.reset(token)

    @staticmethod
    def current() -> "DesktopHeadRefresh | None":
        """The desktop operation for this invocation, if supplied by the CLI."""
        return _manual_desktop.get()


_manual_desktop: ContextVar[DesktopHeadRefresh | None] = ContextVar("manual_desktop_update", default=None)
