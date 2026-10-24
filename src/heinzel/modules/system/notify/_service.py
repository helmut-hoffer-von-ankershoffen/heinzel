"""Service of the notify module: the `Notifications` the kernel injects, with the policy and the local store (ADR 0043).

Modules call `emit`; the policy decides whether and when the notice pops up; the store keeps it for the web app's
list. Whoever shows it first marks it as shown: the web app while it is open (it says so every few seconds through
`watched`), else the daemon at the end of every tick (`post_native`). So a notice pops up once, wherever you are.

The service reads only user-scope configuration, as the daemon does: a project's `hz.toml` can't decide what
interrupts you or where notices are kept.
"""

import logging
import os
import secrets
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from heinzel import Check, Config, Delivery, HeinzelError, Kernel, Notice, NoticeKind, Notifications, Settings, Status
from heinzel.kernel import KERNEL_SECTION
from heinzel.modules.system.notify import _policy as policy
from heinzel.modules.system.notify._macos import QUARANTINE, NotifierApp
from heinzel.modules.system.notify._models import Delivered, Installed, NoticeList, NotifySettings
from heinzel.modules.system.notify._native import ALLOW, DENIED_DETAIL, DENIED_FIX, HEINZEL, Native, native
from heinzel.modules.system.notify._store import Store
from heinzel.modules.system.notify._watch import watch

logger = logging.getLogger(__name__)
MODULE_SECTION = "system.notify"
WATCHED_FOR = timedelta(seconds=30)
"""After a head last said it shows notices, the daemon holds back this long: the web app says so every few seconds."""
LOOK_EVERY = timedelta(hours=1)
"""How often the daemon runs doctor to look for problems no one asked about."""
STALE = timedelta(hours=12)
"""A notice that waited longer than this to be shown doesn't pop up any more; it stays in the list."""


class NotifyService(Notifications):
    """Records notices under the policy, and shows the due ones natively when no web app shows them."""

    def __init__(  # noqa: PLR0913 - the seams tests replace, all but the first three defaulted
        self,
        settings: Settings,
        notify: NotifySettings,
        org: Mapping[str, object],
        *,
        clock: Callable[[], datetime] | None = None,
        notifier: Native | None = None,
        ui_url: Callable[[str], str | None] | None = None,
    ) -> None:
        """Create the service; `for_user` builds it for the kernel, tests pass the rest.

        Args:
            settings: Kernel settings: where notices are kept (`state_dir`).
            notify: The policy's settings.
            org: The profile's `[org]` table, for your working hours and time zone.
            clock: The current time, timezone-aware.
            notifier: The native notifier; this machine's by default.
            ui_url: The web app's address of a route, for a click to open; None while it has no stable address.
        """
        self.settings = notify
        self.store = Store(settings.state_dir / "notify")
        self._clock = clock or (lambda: datetime.now(UTC))
        self.hours = policy.working_hours(org, policy.local_zone())
        self._notifier = notifier
        self._ui_url: Callable[[str], str | None] = ui_url or _no_url
        self.watcher: Callable[[], int] | None = None
        """What `look` runs once an hour; set by `for_user`."""

    @classmethod
    def for_user(cls, kernel: Kernel) -> "NotifyService":
        """The service with user-scope settings, looking for doctor's findings through `kernel`."""
        config = Config.load(Path.home())  # as the daemon does: user scope, never the project's
        service = cls(
            config.settings(Settings, KERNEL_SECTION),
            config.settings(NotifySettings, MODULE_SECTION),
            config.section("org"),
        )
        service.watcher = lambda: watch(kernel, service, service.wanted)
        return service

    @property
    def wanted(self) -> set[NoticeKind]:
        """The kinds of notices that are recorded."""
        return {kind for kind in NoticeKind if self.settings.wants(kind)}

    def look(self) -> int:
        """Run the watcher once an hour (doctor's findings, a newer release); how many notices it reported.

        Its failure is logged, never raised: it must not stop due notices from popping up.
        """
        if self.watcher is None:
            return 0
        now = self._clock()
        last = self.store.looked_at()
        if last is not None and now - LOOK_EVERY < last <= now:
            return 0
        self.store.looked(now)
        try:
            return self.watcher()
        except Exception:
            logger.exception("looking for doctor's findings failed")
            return 0

    @property
    def notifier(self) -> Native:
        """This machine's native notifier, looked up once."""
        if self._notifier is None:
            self._notifier = native(sys.platform, shutil.which, os.environ)
        return self._notifier

    # What modules call -----------------------------------------------------------------------------------------------

    def emit(  # noqa: PLR0913 - what a notice is, all by keyword
        self,
        kind: NoticeKind,
        title: str,
        body: str = "",
        *,
        route: str,
        command: str,
        key: str,
        urgent: bool = False,
    ) -> Notice | None:
        """Record a notice under the policy; the earlier one when it repeats; None if its kind is off or it failed."""
        if not self.settings.wants(kind):
            return None
        now = self._clock()
        window = timedelta(hours=self.settings.repeat_hours)

        def record(notices: list[Notice]) -> Notice:
            if (earlier := policy.repeated(notices, key, now, window)) is not None:
                return earlier
            decision = policy.decide(self.settings, self.hours, now, urgent=urgent, recent=notices)
            notice = Notice(
                id=f"{now.astimezone(UTC):%Y%m%dT%H%M%S%fZ}-{secrets.token_hex(3)}",
                kind=kind,
                title=title,
                body=body,
                route=route if route.startswith("/") and not route.startswith("//") else "/",
                command=command,
                key=key,
                urgent=urgent,
                at=now,
                due=decision.due,
                popup=decision.popup,
                held=decision.held,
            )
            notices.append(notice)
            return notice

        try:
            return self.store.change(now, record)
        except OSError:
            logger.warning("can't record a notice in %s", self.store.directory, exc_info=True)
            return None

    # What heads call -------------------------------------------------------------------------------------------------

    def recent(self, limit: int = 50) -> list[Notice]:
        """The latest notices, newest first."""
        return list(reversed(self.store.read()))[:limit]

    def due(self, now: datetime | None = None) -> list[Notice]:
        """Notices to pop up now, oldest first: not shown, their time come, not stale, within the hour's rate limit.

        The limit counts what was shown in the last hour too, so notices held overnight don't all pop up at nine.
        """
        now = now or self._clock()
        notices = self.store.read()
        shown = sum(
            1 for n in notices if n.delivered_at is not None and now - policy.RATE_WINDOW < n.delivered_at <= now
        )
        waiting = [n for n in notices if n.popup and n.delivered_by is None and n.due <= now and now - n.due <= STALE]
        return waiting[: max(0, self.settings.per_hour - shown)]

    def deliver(self, ids: Sequence[str], by: Delivery) -> list[Notice]:
        """Mark notices as shown by `by`; the ones that weren't shown before."""
        wanted, now = set(ids), self._clock()

        def mark(notices: list[Notice]) -> list[Notice]:
            marked: list[Notice] = []
            for index, notice in enumerate(notices):
                if notice.id in wanted and notice.delivered_by is None:
                    notices[index] = notice.model_copy(update={"delivered_by": by, "delivered_at": now})
                    marked.append(notices[index])
            return marked

        return self.store.change(now, mark)

    def watched(self) -> None:
        """A head shows notices right now; the daemon holds back for `WATCHED_FOR`."""
        self.store.watched(self._clock())

    def is_watched(self) -> bool:
        """Whether a head said it shows notices within the last `WATCHED_FOR`."""
        at = self.store.watched_at()
        return at is not None and self._clock() - WATCHED_FOR < at <= self._clock()

    def post_native(self) -> int:
        """Pop up the due notices natively unless a web app shows them or the setting is off; how many were posted.

        A notice the notifier refused stays due and is tried again at the next tick, until it is stale. First, once
        an hour, it looks for doctor's findings and a newer release (`look`), so they pop up in the same tick.
        """
        self.look()
        if not self.settings.native or self.is_watched() or self.notifier.name is None:
            return 0
        # Claimed before they pop up, under the store's lock, so a web app can't show the same ones meanwhile.
        claimed = self.deliver([notice.id for notice in self.due()], Delivery.DAEMON)
        posted = 0
        problem: str | None = None
        for index, notice in enumerate(claimed):
            url = self._ui_url(notice.route) if self.notifier.clicks else None
            body = notice.body if url is not None else _with_command(notice.body, notice.command)
            if (problem := self.notifier.post(notice.title, body, url=url)) is not None:
                logger.warning("couldn't show notice %s: %s", notice.id, problem)
                self._release(claimed[index:])  # the next would fail the same way; they wait for the next tick
                break
            posted += 1
        if claimed:
            self.store.native_result(self._clock(), problem)
        return posted

    def _release(self, notices: Sequence[Notice]) -> None:
        """Hand claimed notices back, so they are due again."""
        ids = {notice.id for notice in notices}

        def release(stored: list[Notice]) -> None:
            for index, notice in enumerate(stored):
                if notice.id in ids and notice.delivered_by is Delivery.DAEMON:
                    stored[index] = notice.model_copy(update={"delivered_by": None, "delivered_at": None})

        self.store.change(self._clock(), release)

    # What operations call --------------------------------------------------------------------------------------------

    def install(self) -> Installed:
        """Install the native notifier where that is needed and pop up a test now, bypassing the policy.

        Raises:
            HeinzelError: If Heinzel Notifier can't be copied into ~/Applications.
        """
        notifier = self.notifier
        if notifier.name is None:
            fixes = [notifier.fix] if notifier.fix else []
            return Installed(notifier=None, shown=False, problem=notifier.detail, next=fixes)
        path = state = None
        steps: list[str] = []
        if notifier.name == HEINZEL and notifier.app is not None:
            try:
                installed = notifier.app.ensure()
            except OSError as exc:
                msg = f"Heinzel Notifier can't be installed in {notifier.app.path}: {exc.strerror or exc}"
                raise HeinzelError(msg) from exc
            path, state = str(installed.path), installed.state
            steps.append(ALLOW)
        problem = notifier.post("Notifications work", "This is how hz tells you that a draft is ready.", asking=True)
        self.store.native_result(self._clock(), problem)
        if problem is not None:
            steps.append("Fix what it says, then run `hz system notify install` again.")
        elif notifier.fix:
            steps.append(notifier.fix)
        return Installed(
            notifier=notifier.name, path=path, state=state, shown=problem is None, problem=problem, next=steps
        )

    def listing(self, limit: int) -> NoticeList:
        """The latest notices and how they reach you."""
        notices = self.recent(limit)
        waiting = sum(1 for n in notices if n.popup and n.delivered_by is None)
        return NoticeList(notices=notices, waiting=waiting, native=self.notifier.detail)

    def ack(self, ids: Sequence[str]) -> Delivered:
        """Mark notices as shown by the web app."""
        return Delivered(delivered=[notice.id for notice in self.deliver(ids, Delivery.WEB)])

    def health(self) -> list[Check]:
        """How notices reach you, for `hz system doctor`; degraded only while the native notifier refuses pop-ups."""
        settings = self.settings
        if not settings.enabled:
            return [Check(name="notifications", status=Status.UP, detail="off ([system.notify] enabled = false)")]
        notifier = self.notifier
        if not settings.native:
            detail = "the web app only; the daemon pops up nothing ([system.notify] native = false)"
        else:
            detail = f"the web app while it is open; otherwise {notifier.detail}"
        if settings.quiet:
            detail += "; quiet mode is on"
        if settings.working_hours:
            hours = self.hours
            detail += f"; outside {hours.start:02d}-{hours.end:02d} Monday to Friday ({hours.zone}) only urgent ones"
        checks = [Check(name="notifications", status=Status.UP, detail=detail)]
        if settings.native and (problem := self.store.native_problem()) is not None:
            checks.append(
                Check(
                    name="native notifications",
                    status=Status.DEGRADED,
                    detail=f"the last pop-up failed: {problem}",
                    fix="Run `hz system notify install` in your own terminal; the next tick tries again",
                )
            )
        if settings.native and notifier.name == HEINZEL and notifier.app is not None:
            checks.extend(_app_checks(notifier.app))
        return checks


def _app_checks(app: NotifierApp) -> list[Check]:
    """Heinzel Notifier: installed and current, not quarantined, allowed by macOS."""
    installed = app.inspect()  # doctor reads; installing is what a notification or setup does
    if installed.state == "missing":
        return [
            Check(
                name="heinzel notifier",
                status=Status.UP,
                detail=installed.detail,
                fix="hz system notify install installs it now and sends a test",
            )
        ]
    if installed.state == "outdated":
        checks = [
            Check(
                name="heinzel notifier",
                status=Status.DEGRADED,
                detail=f"{app.path}: {installed.detail}; the next notification replaces it",
                fix="hz system notify install",
            )
        ]
    else:
        checks = [Check(name="heinzel notifier", status=Status.UP, detail=f"{app.path} ({installed.state})")]
    if app.quarantined():
        checks.append(
            Check(
                name="heinzel notifier quarantine",
                status=Status.DEGRADED,
                detail="the app carries macOS's quarantine flag, so Gatekeeper may stop it; hz installs it without one",
                fix=f'xattr -d {QUARANTINE} "{app.path}"',
            )
        )
    status = app.status()
    if status == "denied":
        checks.append(
            Check(name="heinzel notifier permission", status=Status.DEGRADED, detail=DENIED_DETAIL, fix=DENIED_FIX)
        )
    elif status == "not-determined":
        checks.append(
            Check(
                name="heinzel notifier permission",
                status=Status.UP,
                detail="macOS hasn't asked yet; it asks with the first notification",
                fix="hz system notify install",
            )
        )
    else:
        checks.append(Check(name="heinzel notifier permission", status=Status.UP, detail=status))
    return checks


def _no_url(route: str) -> str | None:  # noqa: ARG001 - the web app has no stable address yet
    return None


def _with_command(body: str, command: str) -> str:
    """The body, ending with the command that shows the notice, for notifiers whose click opens nothing useful."""
    return f"{body} Run: {command}".strip() if body else f"Run: {command}"
