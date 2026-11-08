"""The daemon in the web app's status bar: starting, running, paused, failing, stale or not installed (ADR 0056).

A view over `hz system daemon status`, in plain words: a daemon that was just installed says it is starting and when its
first check comes, and only a real version mismatch asks for a restart. Installing and uninstalling change what runs at
every login, so they are operations the app runs after the person confirms the exact call, with the steps shown as
they happen (the CLI and the web app are the heads that install, ADR 0020); pausing, resuming and restarting are
operations the same way.
"""

from datetime import UTC, datetime, timedelta

from heinzel import DetailLine, LiveTime, RunCommand, RunOperation, StatusItem, WidgetAction, __version__
from heinzel.modules.system.daemon._models import DaemonStatus
from heinzel.modules.system.daemon._service import DEFAULT_INTERVAL, PLATFORMS

STALE_INTERVALS = 2
"""A last tick older than this many intervals means the daemon isn't ticking."""

INSTALL = WidgetAction(label="Install", icon="play", effect=RunOperation(operation="install"))
UNINSTALL = WidgetAction(label="Uninstall", icon="circle-alert", effect=RunOperation(operation="uninstall"))
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
            detail=[
                DetailLine(text="Keeps your Heinzelmännchen working while the app is closed"),
                DetailLine(text=f"Jobs that would run on their schedule: {status.jobs}"),
            ],
            actions=[INSTALL],
        )
    last = status.last_tick.astimezone(UTC) if status.last_tick else None  # a naive one is local time
    minutes = status.interval_minutes or DEFAULT_INTERVAL
    interval = timedelta(minutes=status.interval_minutes or 0)
    installed = status.installed_at.astimezone(UTC) if status.installed_at else None
    waiting = last is None and (installed is None or now - installed <= STALE_INTERVALS * timedelta(minutes=minutes))
    detail = [
        DetailLine(text=f"Last check {_ago(last, now)}" if last else "Waiting for its first check"),
        DetailLine(text=f"Jobs: {status.jobs}"),
        *(DetailLine(text=f"Failing: {job}", tone="bad") for job in status.failing[:5]),
    ]
    if status.version_mismatch:
        detail.append(
            DetailLine(
                text=(
                    f"Installed for hz {status.installed_version or 'unknown'}; the last check ran "
                    f"{status.version or 'unknown'}; this is {__version__}"
                ),
                tone="bad",
            )
        )
    elif status.version:
        detail.append(DetailLine(text=f"Running hz {status.version}"))
    restart = [RESTART] if status.version_mismatch else []
    stale = bool(last and interval and now - last > STALE_INTERVALS * interval)
    if status.paused:
        return StatusItem(
            text="Daemon paused", tone="warn", icon="bot", detail=detail, actions=[*restart, RESUME, LOGS, UNINSTALL]
        )
    if not status.loaded or status.failing or stale or (last is None and not waiting):
        text = (
            "Daemon not loaded"
            if not status.loaded
            else f"Daemon failing: {len(status.failing)} of {status.jobs} jobs"
            if status.failing
            else "Daemon not ticking"
        )
        return StatusItem(text=text, tone="bad", icon="bot", detail=detail, actions=[*restart, LOGS, PAUSE, UNINSTALL])
    if last is None:
        return StatusItem(
            text=f"Daemon starting · first check within {minutes} min",
            icon="bot",
            detail=detail,
            actions=[*restart, LOGS, PAUSE, UNINSTALL],
        )
    upcoming = last + interval if interval else None
    return StatusItem(
        text="Daemon needs a restart · version mismatch"
        if status.version_mismatch
        else "Daemon running · next check"
        if upcoming
        else "Daemon running",
        tone="warn" if status.version_mismatch else "ok",
        icon="bot",
        live=LiveTime(at=upcoming) if upcoming else None,
        detail=detail,
        actions=[*restart, LOGS, PAUSE, UNINSTALL],
    )
