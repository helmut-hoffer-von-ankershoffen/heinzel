"""The daemon module: run modules' jobs in the background, supervised by launchd or systemd (ADR 0020)."""

from typing import Annotated

from pydantic import Field

from heinzel import Check, Module
from heinzel.kernel import parse_duration
from heinzel.modules.system.daemon._models import (
    DaemonSettings,
    DaemonStatus,
    Disposition,
    Installed,
    JobInfo,
    LogLine,
    Receipt,
    Restarted,
    TickReport,
    Toggled,
    Uninstalled,
)
from heinzel.modules.system.daemon._render import render_installed, render_jobs, render_logs, render_tick
from heinzel.modules.system.daemon._service import DaemonService
from heinzel.modules.system.daemon._status_item import status_item

module = Module("daemon", "Run modules' jobs in the background, supervised by launchd or systemd.")
"""Jobs run unattended, with your rights, on their schedule once installed (SECURITY.md). Stop them with
`hz system daemon pause`, `hz system daemon uninstall`, or by disabling the module that brings them."""
module.settings(DaemonSettings)
module.service(DaemonService, DaemonService.for_user)

INTERVAL_PATTERN = r"^[1-9][0-9]*m$|^1h$"


def _tick_exit_code(report: TickReport) -> int:
    return 1 if any(run.disposition is Disposition.FAILED for run in report.runs) else 0


# Installing persistence that runs code at every login is a deliberate human act, not something an agent does over
# MCP or a client over REST.
@module.operation(read_only=False, render=render_installed, heads={"cli"})
def install(
    service: DaemonService,
    interval: Annotated[
        str, Field(description="Minutes between ticks, dividing an hour: 1m … 30m, or 1h.", pattern=INTERVAL_PATTERN)
    ] = "5m",
    dry_run: Annotated[bool, Field(description="Only show the unit that would be written.")] = False,
) -> Installed:
    """Install and load the launchd agent or systemd timer that ticks every few minutes."""
    return service.install(parse_duration(interval), dry_run=dry_run)


@module.operation(read_only=False, heads={"cli"})
def uninstall(service: DaemonService) -> Uninstalled:
    """Unload and remove the daemon's unit; receipts and logs stay."""
    return service.uninstall()


@module.operation(read_only=False)
def restart(service: DaemonService) -> Restarted:
    """Restart the daemon so it runs with the installed hz."""
    return service.restart()


@module.operation
def status(service: DaemonService) -> DaemonStatus:
    """Show whether the daemon is installed, loaded and paused, its last tick and failing jobs."""
    return service.status()


# In the web app's status bar (ADR 0056): installing stays a command the person runs, pausing a confirmed call.
module.status(source="status", name="daemon", order=700)(status_item)


@module.operation(render=render_logs)
def logs(
    service: DaemonService,
    lines: Annotated[int, Field(description="How many of the latest lines to show.", ge=1, le=10_000)] = 50,
) -> list[LogLine]:
    """Show the latest lines of the daemon's log."""
    return service.logs(lines)


# Ticks spend money and act with your rights; REST is reachable from a browser, so the CLI (which launchd and systemd
# run) and MCP (an agent you started) get it, REST does not.
@module.operation(read_only=False, render=render_tick, exit_code=_tick_exit_code, heads={"cli", "mcp"})
def tick(
    service: DaemonService,
    dry_run: Annotated[bool, Field(description="Only show which jobs are due; run nothing.")] = False,
    job: Annotated[
        str | None, Field(description="Run this job now, as `<module>:<job>` from `hz system daemon jobs`.")
    ] = None,
) -> TickReport:
    """Run the jobs that are due and write a receipt for each; exits with 1 if any job failed."""
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
