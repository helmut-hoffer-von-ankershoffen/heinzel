"""The notify submodule of system: what deserves your attention, popped up once, in the web app or natively (ADR 0043).

Modules report notices through the injected `Notifications` service (a draft is ready, a contract run failed); once an
hour the daemon's tick adds what doctor finds and a newer release. One policy decides what pops up and when: the kinds
you want, quiet mode, your profile's working hours unless a notice is urgent, no repeats, a rate limit. Notices are
kept on this machine only (the state directory, bounded, pruned); nothing is sent anywhere.
"""

from typing import Annotated

from pydantic import Field

from heinzel import Check, Module, Notifications
from heinzel.modules.system.notify._models import (
    Channel,
    Cleaned,
    Delivered,
    Installed,
    NoticeList,
    NotifySettings,
    Opened,
    Tested,
)
from heinzel.modules.system.notify._render import render_installed, render_list, render_tested
from heinzel.modules.system.notify._service import NotifyService

module = Module("notify", "Pop up what deserves your attention: in the web app while it is open, else natively.")
module.settings(NotifySettings)
module.service(NotifyService, NotifyService.for_user)


def _notifications(service: NotifyService) -> Notifications:
    return service


module.service(Notifications, _notifications)

# Notices name your contracts and what failed on your machine: like `drafts`, they aren't for the unauthenticated REST
# head, which any web page can reach; the web head's token closes that hole (ADR 0039).
HEADS = frozenset({"cli", "mcp", "web"})


@module.operation(name="list", render=render_list, heads=HEADS)
def list_(
    service: NotifyService,
    limit: Annotated[int, Field(description="How many of the latest notices to show.", ge=1, le=200)] = 50,
    before: Annotated[
        str | None,
        Field(description="Only notices older than this id, for the next page.", pattern=r"^[A-Za-z0-9-]+$"),
    ] = None,
) -> NoticeList:
    """Show the latest notices, whether each popped up and who showed it, and how they reach you."""
    return service.listing(limit, before=before)


@module.operation(read_only=False, heads=HEADS)
def ack(
    service: NotifyService,
    ids: Annotated[
        str,
        Field(
            description="Ids of the notices that were shown, from `list`, separated by commas.",
            max_length=8000,
            pattern=r"^[A-Za-z0-9-]+(,[A-Za-z0-9-]+)*$",
        ),
    ],
) -> Delivered:
    """Mark notices as shown, so the daemon doesn't pop them up again.

    The web app calls it for every notice it popped up.
    """
    return service.ack(ids.split(","))


# A test may pop up a native notification and let macOS ask whether to allow it: that's for a person at the screen
# (the CLI, the web app's button), not for an agent over MCP.
def _tested_exit_code(result: Tested) -> int:
    return 0 if result.pops_up else 1


@module.operation(name="test", read_only=False, render=render_tested, exit_code=_tested_exit_code, heads={"cli", "web"})
def send_test(
    service: NotifyService,
    native: Annotated[
        bool, Field(description="Pop it up through the native notifier now too, as the web app's test button does.")
    ] = False,
) -> Tested:
    """Record a test notice, report each way it can reach you, and exit 1 if nothing pops it up.

    The open web app or the next daemon tick pops it up; `hz system notify install` pops one up at once, through the
    native notifier only.

    Raises:
        HeinzelError: If notifications are off, or quiet mode keeps it from popping up.
    """
    return service.test(native=native)


# Installing an app into ~/Applications and popping up a notification is for the person at the terminal.
@module.operation(read_only=False, render=render_installed, heads={"cli"})
def install(service: NotifyService) -> Installed:
    """Install the native notifier (Heinzel Notifier on macOS) and pop up a test notification now."""
    return service.install()


# Ends processes on this machine: for the person at the terminal, never an agent.
@module.operation(read_only=False, heads={"cli"})
def clean(service: NotifyService) -> Cleaned:
    """End leftover Heinzel Notifier processes (it should end by itself within a minute of each notification)."""
    return service.clean()


# Opens a window on the machine hz runs on, for the person who clicked: the CLI and the web app, not agents.
@module.operation(name="settings", read_only=False, heads={"cli", "web"})
def open_settings(service: NotifyService) -> Opened:
    """Open the system's notification settings to allow hz's pop-ups (macOS: System Settings)."""
    return service.open_settings()


@module.health
def health(service: NotifyService) -> list[Check]:
    """How notices reach you: the web app, the daemon's native notifier, or neither."""
    return service.health()


__all__ = [
    "Channel",
    "Cleaned",
    "Delivered",
    "Installed",
    "NoticeList",
    "NotifyService",
    "NotifySettings",
    "Opened",
    "Tested",
    "module",
]
