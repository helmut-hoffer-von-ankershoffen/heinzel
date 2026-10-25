"""The daemon in the web app's status bar: running, paused, failing, stale or not installed (ADR 0056).

A view over `hz system daemon status`. Installing and uninstalling change what runs at every login, so they open the
terminal with the command and the person presses Enter (the CLI is the only head that installs, ADR 0020); pausing and
resuming are operations the app runs only after the person confirms the exact call.
"""

from datetime import UTC, datetime, timedelta

from heinzel import DetailLine, LiveTime, RunCommand, RunOperation, StatusItem, WidgetAction, __version__
from heinzel.modules.system.daemon._models import DaemonStatus
from heinzel.modules.system.daemon._service import PLATFORMS

STALE_INTERVALS = 2
"""A last tick older than this many intervals means the daemon isn't ticking."""

INSTALL = WidgetAction(label="Install", icon="play", effect=RunCommand(command="hz system daemon install"))
UNINSTALL = WidgetAction(
    label="Uninstall", icon="circle-alert", effect=RunCommand(command="hz system daemon uninstall")
)
RESTART = WidgetAction(label="Restart daemon", icon="zap", effect=RunOperation(operation="restart"))
PAUSE = WidgetAction(label="Pause", icon="timer", effect=RunOperation(operation="pause"))
RESUME = WidgetAction(label="Resume", icon="play", effect=RunOperation(operation="resume"))
LOGS = WidgetAction(label="Logs", icon="file-text", effect=RunCommand(command="hz system daemon logs"))


def _ago(moment: datetime, now: datetime) -> str:
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 120:  # noqa: PLR2004 - two hours read better in minutes
        return f"{minutes} min ago"
    return f"{minutes // 60} h ago"


def status_item(status: DaemonStatus, now: datetime | None = None) -> StatusItem:
    """What the status bar says about the daemon, with the buttons that change it."""
    now = now or datetime.now(UTC)
    if status.platform not in PLATFORMS:
        return StatusItem(
            text="Daemon unsupported here", icon="bot", detail=[DetailLine(text="Needs launchd or systemd")]
        )
    if not status.installed:
        return StatusItem(
            text="Daemon not installed",
            icon="bot",
            detail=[DetailLine(text=f"Jobs that would run on their schedule: {status.jobs}")],
            actions=[INSTALL],
        )
    last = status.last_tick.astimezone(UTC) if status.last_tick else None  # a naive one is local time
    detail = [
        DetailLine(text=f"Jobs: {status.jobs}"),
        DetailLine(text=f"Last tick {_ago(last, now)}" if last else "No tick yet"),
        *(DetailLine(text=f"Failing: {job}", tone="bad") for job in status.failing[:5]),
    ]
    if status.version:
        detail.insert(1, DetailLine(text=f"Daemon version {status.version}"))
    if status.version_mismatch:
        detail.insert(
            2,
            DetailLine(
                text=f"Daemon runs hz {status.version}, not the installed {__version__}: restart it", tone="bad"
            ),
        )
    restart = [RESTART] if status.version_mismatch else []
    interval = timedelta(minutes=status.interval_minutes or 0)
    stale = bool(last and interval and now - last > STALE_INTERVALS * interval)
    if status.paused:
        return StatusItem(
            text="Daemon paused", tone="warn", icon="bot", detail=detail, actions=[*restart, RESUME, LOGS, UNINSTALL]
        )
    if not status.loaded or status.failing or stale:
        text = (
            "Daemon not loaded"
            if not status.loaded
            else f"Daemon failing: {len(status.failing)} of {status.jobs} jobs"
            if status.failing
            else "Daemon not ticking"
        )
        return StatusItem(text=text, tone="bad", icon="bot", detail=detail, actions=[*restart, LOGS, PAUSE, UNINSTALL])
    upcoming = last + interval if last and interval else None
    return StatusItem(
        text="Daemon needs a restart"
        if status.version_mismatch
        else "Daemon running · next tick"
        if upcoming
        else "Daemon running",
        tone="warn" if status.version_mismatch else "ok",
        icon="bot",
        live=LiveTime(at=upcoming) if upcoming else None,
        detail=detail,
        actions=[*restart, LOGS, PAUSE, UNINSTALL],
    )
