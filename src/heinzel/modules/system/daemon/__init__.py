"""The daemon module: run modules' jobs in the background, supervised by launchd or systemd (ADR 0020)."""

from typing import Annotated

from pydantic import Field

from heinzel import Check, Module
from heinzel.kernel import parse_duration
from heinzel.modules.system.daemon._models import (
    DaemonSettings,
    DaemonStatus,
    Installed,
    JobInfo,
    LogLine,
    Receipt,
    TickReport,
    Toggled,
    Uninstalled,
)
from heinzel.modules.system.daemon._render import render_installed, render_jobs, render_logs, render_tick
from heinzel.modules.system.daemon._service import DaemonService

module = Module("daemon", "Run modules' jobs in the background, supervised by launchd or systemd.")
module.settings(DaemonSettings)
module.service(DaemonService)

INTERVAL_PATTERN = r"^[1-9][0-9]*m$|^1h$"


@module.operation(read_only=False, render=render_installed)
def install(
    service: DaemonService,
    interval: Annotated[
        str, Field(description="Minutes between ticks, dividing an hour: 1m … 30m, or 1h.", pattern=INTERVAL_PATTERN)
    ] = "5m",
    dry_run: Annotated[bool, Field(description="Only show the unit that would be written.")] = False,
) -> Installed:
    """Install and load the launchd agent or systemd timer that ticks every few minutes."""
    return service.install(parse_duration(interval), dry_run=dry_run)


@module.operation(read_only=False)
def uninstall(service: DaemonService) -> Uninstalled:
    """Unload and remove the daemon's unit; receipts and logs stay."""
    return service.uninstall()


@module.operation
def status(service: DaemonService) -> DaemonStatus:
    """Show whether the daemon is installed, loaded and paused, its last tick and failing jobs."""
    return service.status()


@module.operation(render=render_logs)
def logs(
    service: DaemonService,
    lines: Annotated[int, Field(description="How many of the latest lines to show.", ge=1, le=10_000)] = 50,
) -> list[LogLine]:
    """Show the latest lines of the daemon's log."""
    return service.logs(lines)


@module.operation(read_only=False, render=render_tick)
def tick(
    service: DaemonService,
    dry_run: Annotated[bool, Field(description="Only show which jobs are due; run nothing.")] = False,
    job: Annotated[
        str | None, Field(description="Run this job now, as `<module>:<job>` from `hz system daemon jobs`.")
    ] = None,
) -> TickReport:
    """Run the jobs that are due and write a receipt for each; launchd or systemd call this."""
    return service.tick(dry_run=dry_run, job=job)


@module.operation(read_only=False)
def pause(service: DaemonService) -> Toggled:
    """Pause the daemon: ticks run no job until resumed."""
    return service.pause()


@module.operation(read_only=False)
def resume(service: DaemonService) -> Toggled:
    """Resume a paused daemon."""
    return service.resume()


@module.operation(render=render_jobs)
def jobs(service: DaemonService) -> list[JobInfo]:
    """List every job with its schedule, next slot and last run."""
    return service.jobs()


@module.health
def health(service: DaemonService) -> list[Check]:
    """Whether the daemon is loaded, ticking and not paused, and which jobs fail."""
    return service.health()


__all__ = [
    "DaemonService",
    "DaemonSettings",
    "DaemonStatus",
    "Installed",
    "JobInfo",
    "LogLine",
    "Receipt",
    "TickReport",
    "Toggled",
    "Uninstalled",
    "module",
]
