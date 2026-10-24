"""The notify submodule of system: what deserves your attention, popped up once, in the web app or natively (ADR 0043).

Modules report notices through the injected `Notifications` service (a draft is ready, a contract run failed); once an
hour the daemon's tick adds what doctor finds and a newer release. One policy decides what pops up and when: the kinds
you want, quiet mode, your profile's working hours unless a notice is urgent, no repeats, a rate limit. Notices are
kept on this machine only (the state directory, bounded, pruned); nothing is sent anywhere.
"""

import secrets
from typing import Annotated

from pydantic import Field

from heinzel import Check, HeinzelError, Module, NoticeKind, Notifications
from heinzel.modules.system.notify._models import Delivered, Installed, NoticeList, NotifySettings
from heinzel.modules.system.notify._render import render_installed, render_list
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
) -> NoticeList:
    """Show the latest notices, whether each popped up and who showed it, and how they reach you."""
    return service.listing(limit)


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


@module.operation(read_only=False, render=render_list, heads=HEADS)
def test(service: NotifyService) -> NoticeList:
    """Record an urgent test notice, which the open web app or the next daemon tick pops up.

    `hz system notify install` pops one up at once, through the native notifier only.

    Raises:
        HeinzelError: If notifications are off, or quiet mode keeps it from popping up.
    """
    notice = service.emit(
        NoticeKind.TEST,
        "Notifications work",
        "This is how hz tells you that a draft is ready or a run failed.",
        route="/notifications",
        command="hz system notify list",
        key=f"test:{secrets.token_hex(4)}",  # never a repeat
        urgent=True,
    )
    if notice is None:
        msg = "Notifications are off: hz system config set system.notify.enabled true"
        raise HeinzelError(msg)
    if not notice.popup:
        msg = f"Recorded, but it won't pop up: {notice.held}"
        raise HeinzelError(msg)
    return service.listing(5)


# Installing an app into ~/Applications and popping up a notification is for the person at the terminal.
@module.operation(read_only=False, render=render_installed, heads={"cli"})
def install(service: NotifyService) -> Installed:
    """Install the native notifier (Heinzel Notifier on macOS) and pop up a test notification now."""
    return service.install()


@module.health
def health(service: NotifyService) -> list[Check]:
    """How notices reach you: the web app, the daemon's native notifier, or neither."""
    return service.health()


__all__ = ["Delivered", "Installed", "NoticeList", "NotifyService", "NotifySettings", "module"]
