"""What the daemon looks for on its own, once an hour: doctor's findings and a newer release (ADR 0043).

It runs at the end of a tick, before notices pop up, rather than as a job of its own: it only records notices, and an
hourly receipt for it would say nothing a notice doesn't.
"""

import logging

from heinzel import HeinzelError, Kernel, NoticeKind, Notifications, OperationLet, ResolveWhen, Status
from heinzel.kernel import ReleaseCache
from heinzel.kernel._logging import get_logger, log_event
from heinzel.modules.system.notify._channels import DaemonState
from heinzel.modules.system.notify._resolve import DOCTOR_KEY, RELEASE_KEY, installed_version

logger = get_logger("system.notify")
SHOWN_PROBLEMS = 3


def watch(kernel: Kernel, notifications: Notifications, wants: "set[NoticeKind]") -> int:
    """Record notices for what doctor finds and a newer release; how many were reported."""
    reported = 0
    if NoticeKind.DOCTOR in wants:
        reported += _doctor(kernel, notifications)
    # The version on disk, not this process's: a process started before an update must not announce what is installed.
    installed = installed_version()
    latest = ReleaseCache(kernel.settings.cache_dir).newer_than(installed, kernel.settings.update_channel)
    if NoticeKind.RELEASE in wants and latest is not None:
        notice = notifications.emit(
            NoticeKind.RELEASE,
            f"hz {latest} is available",
            f"You have {installed}; `hz system changelog --newer` says what it brings.",
            route="/doctor",
            command="hz system update",
            key=f"{RELEASE_KEY}{latest}",
            resolve_when=ResolveWhen(installed_version_at_least=latest),
        )
        reported += notice is not None
    return reported


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


def _report(kernel: Kernel) -> tuple[Status, list[str], set[str]] | None:
    """Doctor's status and the checks that aren't up (`<module>: <check>`); None if it can't run.

    It runs through the kernel: modules don't import each other.
    """
    found = next(
        (let for owner, let in kernel.lets(OperationLet) if (owner.qualname, let.name) == ("system.doctor", "doctor")),
        None,
    )
    if found is None or not kernel.available(found.func):
        return None
    report = kernel.invoke(found)
    problems = sorted(
        f"{qualname}: {check.name}"
        for qualname, checks in report.checks.items()
        for check in checks
        if check.status is not Status.UP and qualname != "system.notify"
    )
    up = {
        f"{qualname}: {check.name}"
        for qualname, checks in report.checks.items()
        for check in checks
        if check.status is Status.UP and qualname != "system.notify"
    }
    return report.status, problems, up - set(problems)


def doctor_problems(kernel: Kernel) -> set[str] | None:
    """The checks explicitly up now, for a doctor notice's `checks_up`; None if doctor can't tell."""
    try:
        found = _report(kernel)
    except Exception as exc:  # noqa: BLE001 - any doctor failure resolves nothing; logged with its traceback
        log_event(
            logger,
            "notify.resolve.doctor",
            level=logging.WARNING,
            exc_info=True,
            operation="system.notify:resolve",
            step="doctor",
            outcome="error",
            error_type=type(exc).__name__,
        )
        return None
    return None if found is None else found[2]


def _doctor(kernel: Kernel, notifications: Notifications) -> int:
    """Run doctor and report what isn't up, resolving by itself once those checks are up again."""
    found = _report(kernel)
    if found is None:
        return 0
    status, problems, _ = found
    if status is Status.UP or not problems:
        return 0
    down = status is Status.DOWN
    try:
        when: ResolveWhen | None = ResolveWhen(checks_up=problems)
    except ValueError:  # a check name a condition can't carry: the notice stays until it ages out
        when = None
    more = "…" if len(problems) > SHOWN_PROBLEMS else ""
    notice = notifications.emit(
        NoticeKind.DOCTOR,
        f"Heinzelmännchen is {'down' if down else 'degraded'}",
        f"{len(problems)} check(s) need you: {', '.join(problems[:SHOWN_PROBLEMS])}{more}",
        route="/doctor",
        command="hz system doctor",
        key=f"{DOCTOR_KEY}{status.value}:{'|'.join(problems)}",
        urgent=down,
        resolve_when=when,
    )
    return int(notice is not None)
