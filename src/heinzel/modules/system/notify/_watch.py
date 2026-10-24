"""What the daemon looks for on its own: doctor's findings, a newer release, meetings soon and important mail.

Once an hour doctor's findings and a newer release (ADR 0043); at every tick the meetings that start soon and new
important mail (ADR 0059). It runs at the end of a tick, before notices pop up, rather than as a job of its own: it
only records notices, and a receipt for it would say nothing a notice doesn't.
"""

import logging

from heinzel import HeinzelError, Kernel, NoticeKind, Notifications, OperationLet, Status, __version__
from heinzel.kernel import ReleaseCache
from heinzel.modules.system.notify._channels import DaemonState

logger = logging.getLogger(__name__)

SHOWN_PROBLEMS = 3


def watch(kernel: Kernel, notifications: Notifications, wants: "set[NoticeKind]") -> int:
    """Record notices for what doctor finds and a newer release; how many were reported."""
    reported = 0
    if NoticeKind.DOCTOR in wants:
        reported += _doctor(kernel, notifications)
    latest = ReleaseCache(kernel.settings.cache_dir).newer_than(__version__)
    if NoticeKind.RELEASE in wants and latest is not None:
        notice = notifications.emit(
            NoticeKind.RELEASE,
            f"hz {latest} is available",
            f"You have {__version__}; `hz system changelog --newer` says what it brings.",
            route="/doctor",
            command="hz system update",
            key=f"release:{latest}",
        )
        reported += notice is not None
    return reported


TICKS = ((NoticeKind.MEETING, "calendar", "remind"), (NoticeKind.MAIL, "mail", "notice"))
"""What runs at every tick, through the kernel (modules don't import each other): the kind of notice it records, and
its operation, `hz calendar remind` and `hz mail notice`."""


def remind(kernel: Kernel, wants: "set[NoticeKind]") -> int:
    """Run what looks for meetings that start soon and new important mail; how many notices they recorded.

    Every tick, unlike `watch`, since a meeting's reminder is due within minutes; each only while its kind of notice
    is on and a module brings its operation. One that fails is logged and the others still run.
    """
    noticed = 0
    for kind, qualname, name in TICKS:
        if kind not in wants:
            continue
        found = next(
            (let for owner, let in kernel.lets(OperationLet) if (owner.qualname, let.name) == (qualname, name)), None
        )
        if found is None or not kernel.available(found.func):
            continue
        try:
            result = kernel.invoke(found)
        except Exception as exc:  # noqa: BLE001 - isolation boundary: a broken calendar or mailbox can't stop the other
            # Its type only: an adapter's message may echo a subject or a query, and nothing logs mail.
            logger.warning("hz %s %s failed: %s", qualname, name, type(exc).__name__)
            continue
        noticed += sum(int(getattr(result, field, 0) or 0) for field in ("reminded", "noticed"))
    return noticed


def daemon_state(kernel: Kernel) -> DaemonState | None:
    """`hz system daemon status`, run through the kernel (modules don't import each other); None if it can't answer."""
    found = next(
        (let for owner, let in kernel.lets(OperationLet) if (owner.qualname, let.name) == ("system.daemon", "status")),
        None,
    )
    if found is None or not kernel.available(found.func):
        return None
    try:
        status = kernel.invoke(found)
    except HeinzelError:
        return None
    return DaemonState(
        installed=bool(getattr(status, "installed", False)),
        loaded=bool(getattr(status, "loaded", False)),
        paused=bool(getattr(status, "paused", False)),
    )


def _doctor(kernel: Kernel, notifications: Notifications) -> int:
    """Run doctor through the kernel (modules don't import each other) and report what isn't up."""
    found = next(
        (let for owner, let in kernel.lets(OperationLet) if (owner.qualname, let.name) == ("system.doctor", "doctor")),
        None,
    )
    if found is None or not kernel.available(found.func):
        return 0
    report = kernel.invoke(found)
    problems = sorted(
        f"{qualname}: {check.name}"
        for qualname, checks in report.checks.items()
        for check in checks
        if check.status is not Status.UP and qualname != "system.notify"
    )
    if report.status is Status.UP or not problems:
        return 0
    down = report.status is Status.DOWN
    more = "…" if len(problems) > SHOWN_PROBLEMS else ""
    notice = notifications.emit(
        NoticeKind.DOCTOR,
        f"Heinzelmännchen is {'down' if down else 'degraded'}",
        f"{len(problems)} check(s) need you: {', '.join(problems[:SHOWN_PROBLEMS])}{more}",
        route="/doctor",
        command="hz system doctor",
        key=f"doctor:{report.status.value}:{'|'.join(problems)}",
        urgent=down,
    )
    return int(notice is not None)
