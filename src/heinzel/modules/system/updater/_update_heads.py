"""The update service's heads: the daemon, the web server and the desktop app after an install."""

import json
import logging
import platform
import subprocess
from contextlib import suppress

from heinzel import HeinzelError
from heinzel.kernel import (
    DaemonInstallationLifecycle,
    HeadRefresh,
)
from heinzel.kernel._logging import log_event, step
from heinzel.modules.system._models import UpdateResult
from heinzel.modules.system.updater._update_core import _UpdateCore
from heinzel.modules.system.updater._update_rules import (
    DAEMON_POLL_SECONDS,
    DAEMON_SECONDS,
    DEFERRED,
    DESKTOP_RECEIPT,
    QUIT_SECONDS,
    REASON_LENGTH,
    log,
)


class _UpdateHeads(_UpdateCore):
    """Refreshes the daemon, the web server and the desktop app after an install."""

    def _desktop_state(self) -> tuple[str | None, bool] | None:
        """The installed desktop app's release and whether it is open; none if there is no managed app here."""
        if not self._desktop_installed():
            return None
        release = None
        with suppress(OSError, ValueError, AttributeError, RuntimeError):
            text = self._home_file(DESKTOP_RECEIPT)
            found = json.loads(text) if text is not None else {}
            release = found.get("release")
        return release if isinstance(release, str) else None, self._running()

    def _app_head(
        self, result: UpdateResult, app: tuple[str | None, bool], asked: tuple[bool, bool, bool]
    ) -> HeadRefresh:
        """The desktop app's outcome: left alone, already newest, deferred until it is quit, or updated."""
        desktop, restart, from_app = asked
        release, running = app
        target = result.after
        known = (release, running)
        if not desktop:
            return HeadRefresh(
                head="desktop",
                status="pending",
                detail="left alone; update it later with `hz app install`.",
                before=known[0],
                was_running=known[1],
            )
        if release == target or (release is None and not result.updated):  # nothing known to replace
            return HeadRefresh(
                head="desktop",
                status="verified",
                version=target,
                detail="Already the newest.",
                before=release,
                was_running=running,
            )
        if running and (from_app or not restart):
            why = "installs when you quit the app" if from_app else "installs when you quit it"
            return HeadRefresh(head="desktop", status="pending", detail=f"{why}.", before=release, was_running=running)
        return self._desktop_install(target, restart=running and restart, known=known)

    def _desktop_install(self, target: str, *, restart: bool, known: tuple[str | None, bool]) -> HeadRefresh:
        """Update the installed desktop app with the new hz's own `hz app install`; never raises."""
        with step(log, "update.desktop", operation="system.update", version=target) as outcome:
            try:
                code, output = self._desktop(restart=True) if restart else self._desktop()
            except (OSError, subprocess.SubprocessError) as exc:
                code, output = 1, str(exc)
            outcome.note(exit_code=code, restart=restart)
            if code not in {0, DEFERRED}:
                outcome.outcome = "error"
        if code == 0:
            return HeadRefresh(
                head="desktop",
                status="verified",
                version=target,
                detail="Updated by `hz app install`.",
                reopened=restart,
                before=known[0],
                was_running=known[1],
            )
        if code == DEFERRED:
            return HeadRefresh(
                head="desktop",
                status="pending",
                detail=f"still open after {QUIT_SECONDS:g} s, so it installs when you quit it: `hz app install`.",
                reopened=False,
                before=known[0],
                was_running=known[1],
            )
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        said = " ".join(lines[-2:])[:REASON_LENGTH] if lines else "It gave no reason."
        return HeadRefresh(
            head="desktop",
            status="failed",
            detail=f"`hz app install` didn't update it: {said} hz itself is updated. Run `hz app install` again.",
            before=known[0],
            was_running=known[1],
        )

    def _refresh_heads(self, target: str) -> list[HeadRefresh]:
        """The heads after an automatic update: the daemon's handover and the web server, the app left to its update."""
        heads: list[HeadRefresh] = []
        if self._lifecycle is not None:
            heads.append(self._handed_over(target))
        if self._web is not None:
            try:
                heads.append(self._web.refresh(target))
            except Exception as exc:  # noqa: BLE001 - a head's own failure is reported, never hidden or raised
                heads.append(
                    HeadRefresh(
                        head="web",
                        status="failed",
                        detail=f"The web server couldn't be refreshed ({exc}). Run `hz ui restart`, then "
                        "`hz ui status`.",
                    )
                )
        if self._desktop_installed():
            heads.append(
                HeadRefresh(
                    head="desktop",
                    status="pending",
                    detail="Not updated by this command: the desktop app updates itself at the web server's next "
                    "automatic update check, if automatic updates are on. Update it now with `hz app install`.",
                )
            )
        return heads

    def _daemon_version(self) -> str | None:
        """The version the installed daemon's process runs, when there is one and it can be told."""
        if self._daemon is None or self._lifecycle is None:
            return None
        try:
            status = self._daemon.status()
        except Exception:  # noqa: BLE001 - an unreadable status means nothing is claimed about the daemon
            return None
        return status.version if status.installed else None

    def _service_heads(self, result: UpdateResult, ran: str | None, *, daemon: bool) -> list[HeadRefresh]:
        """Move each installed long-running head onto the installed code and observe what its live process runs.

        Runs only after the installation mutation completed, so no pending marker blocks an owned restart. Nothing
        here raises: a head that can't be verified is reported as `pending` or `failed`, with the command that
        finishes it, and the update never claims it. Without a change a head is left alone, unless the daemon runs
        older code than is installed. With `daemon` false nothing restarts.
        """
        target = result.after
        heads: list[HeadRefresh] = []
        if self._lifecycle is not None:
            heads.append(self._daemon_head(result, ran, daemon=daemon))
        if self._web is not None and result.updated and daemon:
            try:
                heads.append(self._web.refresh(target))
            except Exception as exc:  # noqa: BLE001 - a head's own failure is reported, never hidden or raised
                heads.append(
                    HeadRefresh(
                        head="web",
                        status="failed",
                        detail=f"The web server couldn't be refreshed ({exc}). Run `hz ui restart`, then "
                        "`hz ui status`.",
                    )
                )
        return heads

    def _daemon_head(self, result: UpdateResult, ran: str | None, *, daemon: bool) -> HeadRefresh:
        """The daemon after the update: restarted into it, already running it, or still on older code."""
        target = result.after
        if ran is None and not result.updated:
            return HeadRefresh(head="daemon", status="not-installed", detail="No daemon is running.")
        if not result.updated and ran == target:
            return HeadRefresh(
                head="daemon", status="verified", version=ran, detail="Already runs it.", restarted=False
            )
        if not daemon:
            return self._still_runs(ran)
        if not result.updated and (failed := self._restart_stale(target)) is not None:
            return failed
        head = self._handed_over(target)
        return head.model_copy(update={"before": ran, "restarted": head.status == "verified"})

    @staticmethod
    def _still_runs(ran: str | None) -> HeadRefresh:
        what = f"hz {ran}" if ran else "the old version"
        return HeadRefresh(
            head="daemon",
            status="pending",
            before=ran,
            restarted=False,
            detail=f"still runs {what}: `hz system daemon restart`.",
        )

    def _restart_stale(self, target: str) -> HeadRefresh | None:
        """Hand the daemon over to the installed code when nothing was installed; the head that says why it failed."""
        try:
            with self._mutation() as scope:
                plan = scope.capture() if scope is not None else None
                candidate = self._installation.startup_fingerprint() if scope is not None else None
                if scope is None or plan is None or candidate is None:
                    return None
                if scope.checkpoint_matched:
                    self._installation.record_startup_state(expected=candidate)
                scope.verified_complete(candidate)
                self._handover = (plan, scope.request(plan))
        except (HeinzelError, OSError) as exc:
            log_event(log, "update.daemon_restart", level=logging.WARNING, version=target, error=str(exc))
            return HeadRefresh(
                head="daemon",
                status="failed",
                detail=f"couldn't restart it ({str(exc).splitlines()[0]}): `hz system daemon restart`.",
            )
        return None

    def _desktop_installed(self) -> bool:
        try:
            return platform.system() == "Darwin" and self._home_file(DESKTOP_RECEIPT) is not None
        except (OSError, RuntimeError):  # no home to look in: nothing is claimed about a desktop app
            return False

    def _handed_over(self, target: str) -> HeadRefresh:
        """Wait, within `DAEMON_SECONDS`, for the handed-over daemon's own tick to report the version it loaded."""
        if self._handover is None:
            return HeadRefresh(head="daemon", status="not-installed", detail="No daemon handover was requested.")
        plan, handover = self._handover
        if not handover.requested and not plan.active:  # no daemon, a paused one or one not running: nothing old runs
            return HeadRefresh(head="daemon", status="not-installed", detail=handover.reason)
        if not handover.requested:
            return HeadRefresh(
                head="daemon",
                status="pending",
                detail=f"The daemon wasn't asked to restart ({handover.reason}) and may still run the old code. Run "
                "`hz system daemon restart`, then `hz system daemon status`.",
            )
        if handover.request_id is None or self._lifecycle is None:
            return HeadRefresh(
                head="daemon",
                status="pending",
                detail="The daemon was asked to restart; check `hz system daemon status` for the version it runs.",
            )
        return self._await_daemon(self._lifecycle, handover.request_id, target)

    def _await_daemon(self, lifecycle: DaemonInstallationLifecycle, request_id: str, target: str) -> HeadRefresh:
        """The handed-over tick's own report of the version it loaded, or `pending` after `DAEMON_SECONDS`."""
        deadline = self._clock() + DAEMON_SECONDS
        while True:
            try:
                observed = lifecycle.observation(request_id)
            except Exception:  # noqa: BLE001 - an unreadable observation stays pending, never verified
                observed = None
            if observed is not None:
                if observed.version == target:
                    return HeadRefresh(
                        head="daemon", status="verified", version=target, detail="The daemon restarted into it."
                    )
                return HeadRefresh(
                    head="daemon",
                    status="failed",
                    version=observed.version,
                    detail=f"The daemon restarted but runs hz {observed.version}, not {target}. Run "
                    "`hz system daemon restart`.",
                )
            remaining = deadline - self._clock()
            if remaining <= 0:
                return HeadRefresh(
                    head="daemon",
                    status="pending",
                    detail=f"The daemon was asked to restart; its first tick hadn't finished within "
                    f"{DAEMON_SECONDS:g} seconds. Check `hz system daemon status` for the version it runs.",
                )
            self._sleep(min(DAEMON_POLL_SECONDS, remaining))
