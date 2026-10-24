"""What the daemon looks for on its own, once an hour: doctor's findings and a newer release (ADR 0043).

It runs at the end of a tick, before notices pop up, rather than as a job of its own: it only records notices, and an
hourly receipt for it would say nothing a notice doesn't.
"""

from heinzel import Kernel, NoticeKind, Notifications, OperationLet, Status, __version__
from heinzel.kernel import ReleaseCache

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
