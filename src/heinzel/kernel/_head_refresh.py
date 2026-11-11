"""After an update, each installed long-running head reports whether it now runs the installed version (ADR 0116).

An update replaces the tool folder, but a process that started before it keeps running the code it imported. The
updater asks every head it knows through this port, after the installation change is complete and no mutation is
pending: the head restarts through its own owned path and reports the version its live process loaded. The updater
never claims a head runs the new version without that observation.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

HeadName = Literal["daemon", "web", "notifier", "desktop"]
HeadState = Literal["verified", "pending", "failed", "not-installed", "self"]


class HeadRefresh(BaseModel):
    """What one installed head runs after an update; only `verified` is evidence of the target version."""

    head: HeadName = Field(description="The installed head: daemon, web, notifier or desktop.")
    status: HeadState = Field(
        description="verified: its live process reported the target version; pending: requested, not yet observed; "
        "failed: it reported another version or could not restart; not-installed: nothing to refresh; self: the "
        "process that ran the update hosts this head and restarts itself afterwards."
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
