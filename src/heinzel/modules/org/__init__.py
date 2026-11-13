"""The org module: who you are to hz and, later, whom you work with: your team, org and company (ADR 0024).

Your profile (handle, role, time zone, working hours, GitHub login, coding agent) is asked once by `hz setup` and kept
in your user configuration, `[org]`; nothing about you is written to a repository by this module. Other modules ask
for `ProfileService` by type to learn who is acting.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field
from rich.console import RenderableType

from heinzel import (
    Calendar,
    Check,
    ConfigEditor,
    HeinzelError,
    Kernel,
    Module,
    NoticeKind,
    Notifications,
    Prompter,
    Status,
)
from heinzel.kernel import CapabilityView, DaemonAttestation
from heinzel.modules.org._agenda import Agenda, CalendarWeek, local_now, read_agenda, read_week, status_item
from heinzel.modules.org._models import ROLES, OrgSettings, Profile
from heinzel.modules.org._profile import ProfileService, ask_profile
from heinzel.modules.org._reminder_lane import ReminderState, fresh_lane, reminder_state
from heinzel.modules.org._reminder_lane import main as run_lane_process
from heinzel.modules.org._reminder_maintenance import CalendarMaintenance
from heinzel.modules.org._reminder_service import Profile as ReminderProfile
from heinzel.modules.org._reminder_service import leftover, lifecycle, retire_for_lane, verify_binding
from heinzel.modules.org._reminder_service import uninstall as uninstall_reminders
from heinzel.modules.org._reminder_worker import WorkerReads
from heinzel.modules.org._reminder_worker import run as run_reminders
from heinzel.modules.org._sync import SyncItem, SyncReport, SyncService, render_sync


def render_agenda(agenda: Agenda) -> RenderableType:
    """The meeting now and the next one, one line each, or why they can't be read and what to run."""
    from heinzel.modules.org._render import render_agenda  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_agenda(agenda)


def render_profile(profile: Profile) -> RenderableType:
    """The profile as a table of settings, and what to run when something is missing."""
    from heinzel.modules.org._render import render_profile  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_profile(profile)


module = Module("org", "Your profile, and the team, org and company you work with.")
module.settings(OrgSettings)
module.service(ProfileService)
module.service(CalendarMaintenance)
module.service(SyncService)


# Your name and email are in it; a web page can reach a local REST server, so the CLI, MCP and the web head (which
# needs its session token) get it, like drafts.
@module.operation(render=render_profile, heads={"cli", "mcp", "web"}, audience="everyday")
def show(service: ProfileService) -> Profile:
    """Show your profile: who you are, your role, time zone and working hours, and what is still missing."""
    return service.profile()


# Your meetings, with their attendees, are as private as your profile: the CLI, MCP and the web head (its footer, behind
# the session token) get it, never the unauthenticated REST head. Optional: without a calendar it says how to connect
# one instead of failing, and doctor stays green for everyone who has none.
@module.operation(render=render_agenda, heads={"cli", "mcp", "web"}, requires=["calendar:read"], optional=True)
def agenda(calendar: Calendar, view: CapabilityView, settings: OrgSettings) -> Agenda:
    """Show the meeting you are in now and your next one, from your calendar, or how to connect one."""
    return read_agenda(calendar, view, local_now(settings.timezone))


@module.operation(name="calendar-reminders-uninstall", read_only=False, heads={"cli", "web"})
def calendar_reminders_uninstall(
    confirm: Annotated[bool, Field(description="Confirm removing this profile's reminder login worker.")] = False,
) -> str:
    """Remove this profile's leftover calendar reminder worker; the daemon runs reminders now.

    Raises:
        HeinzelError: If removal has not been explicitly confirmed.
    """
    if not confirm:
        msg = "Confirm removing the reminder worker for this profile."
        raise HeinzelError(msg)
    return uninstall_reminders()


class CalendarReminderStatus(BaseModel):
    """Whether calendar reminders are on, and what keeps them from running on this computer."""

    enabled: bool = Field(description="Whether this profile permits background calendar reads and reminders.")
    state: ReminderState = Field(
        description="off: the setting is off; on: the daemon's reminder lane runs; starting: it starts at the next "
        "daemon tick; install: the daemon is not installed; elsewhere: the daemon runs for another profile; resume: "
        "the daemon is paused; provider: the calendar can't be read; overdue: the lane stopped checking in."
    )


@module.operation(name="calendar-reminders-status", heads={"cli", "web"})
def calendar_reminders_status(notifications: Notifications, kernel: Kernel) -> CalendarReminderStatus:
    """Show whether calendar reminders are on and whether the daemon's reminder lane runs them (ADR 0147)."""
    enabled = notifications.enabled(NoticeKind.CALENDAR)
    daemon = kernel.resolve(DaemonAttestation) if kernel.provides(DaemonAttestation) else None
    return CalendarReminderStatus(enabled=enabled, state=reminder_state(enabled, daemon, datetime.now(UTC)))


@module.operation(name="calendar-reminder-worker", read_only=False, heads={"cli"})
def calendar_reminder_worker(kernel: Kernel) -> str:
    """Run a leftover reminder worker, or remove it once the daemon's lane owns its profile."""
    selected = ReminderProfile.selected()
    with lifecycle(selected):
        verify_binding()
        lanes = Path(selected.environment["HZ_STATE_DIR"]) / "daemon"
        if fresh_lane(lanes, selected.identity, datetime.now(UTC)):
            return retire_for_lane(selected)
        notifications = kernel.resolve(Notifications)
    run_reminders(WorkerReads(lambda: kernel.call(agenda), lambda: lifecycle(selected)), notifications)
    return "The reminder worker restarts."


@module.operation(name="calendar-reminder-lane", read_only=False, heads={"cli"})
def calendar_reminder_lane(kernel: Kernel) -> str:
    """Run the daemon's calendar reminder lane until its slot window; only a daemon tick starts it."""
    return run_lane_process(kernel, lambda: kernel.call(agenda))


@module.operation(heads={"cli", "mcp", "web"}, requires=["calendar:read"], optional=True)
def calendar(
    calendar: Calendar,
    view: CapabilityView,
    settings: OrgSettings,
    offset: Annotated[int, Field(ge=-52, le=52, description="Weeks before or after this week.")] = 0,
) -> CalendarWeek:
    """Show a week from your connected calendar."""
    return read_week(calendar, view, local_now(settings.timezone), offset)


# The web app's status bar shows it (ADR 0056): a view over `agenda`, so it is gated and private like the operation.
module.status(source="agenda", name="meeting", order=800)(status_item)


@module.health
def profile(service: ProfileService, recommendations: SyncService) -> list[Check]:
    """Your profile names your handle, role and time zone; drift from what your team recommends; leftover workers."""
    found = service.profile()
    drift = [*recommendations.health(), *_reminder_worker()]
    if not found.complete:
        return [
            Check(
                name="profile",
                status=Status.DEGRADED,
                detail=f"missing {', '.join(found.missing)}",
                fix=f"Run `{found.fix}`",
            ),
            *drift,
        ]
    detail = f"{found.handle}, {found.role}, {found.timezone} {found.working_hours}"
    return [Check(name="profile", status=Status.UP, detail=detail), *drift]


def _reminder_worker() -> list[Check]:
    """A leftover 0126 reminder worker of this profile; the daemon's reminder lane replaces it (ADR 0147)."""
    path = leftover()
    if path is None:
        return []
    return [
        Check(
            name="reminder worker",
            status=Status.DEGRADED,
            detail=f"a separate reminder worker is still installed at {path}; the daemon runs reminders now",
            fix="Run `hz org calendar-reminders-uninstall --confirm`",
        )
    ]


# It writes your configuration on your say-so; a web page can reach a local REST server, so the CLI and MCP get it.
@module.operation(read_only=False, render=render_sync, heads={"cli", "mcp"}, audience="everyday")
def sync(
    service: SyncService,
    accept: Annotated[
        str | None,
        Field(description="Recommendations to apply, by id and comma-separated (binding:documents), or all."),
    ] = None,
) -> SyncReport:
    """Show what your team recommends beside what you have; apply only what you accept, to your user file."""
    return service.sync(accept)


@module.setup(required=True, phase="identity")
def setup(settings: OrgSettings, prompter: Prompter, editor: ConfigEditor) -> None:
    """Tell hz who you are; what your gh and gws logins and this machine say is found, the rest is asked once."""
    prompter.say("Your profile is kept on this machine only; nothing goes into a repository.")
    path = ask_profile(settings, prompter, editor)
    prompter.say(
        f"Saved to {path}. `hz org show` shows it; correct one part with e.g. `hz system config set org.role engineer`."
    )


__all__ = [
    "ROLES",
    "Agenda",
    "OrgSettings",
    "Profile",
    "ProfileService",
    "SyncItem",
    "SyncReport",
    "SyncService",
    "module",
]
