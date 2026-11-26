"""Automatic updates: the plan a `hz ui serve` server runs; it checks, warns, waits for idle and applies (ADR 0116).

The plan lives here, in the server, never in a browser: two windows show the same countdown, a reload shows what is
left of it, and with no window open the update still applies on time. It is saved under the state folder, so a
restart keeps postponements, the last failure and the version a restart is expected to bring.

Applying uses what a person's click uses: the `system.update` operation (hz and plugins together, rolled back by the
installer when it fails) and the restart in place on the same port. It finds the operation, the release sources and
the daemon status through the kernel. The web head serves it (`heinzel.heads.web._auto_update`) and hands it the
guarded state folder of the client context (`StateFiles`), which this context does not import.
"""

import contextlib
import json
import logging
import os
import random
import secrets
import threading
import weakref
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO, Any, ClassVar

from packaging.version import Version

from heinzel import HeinzelError, __version__
from heinzel._adapter_system import SYSTEM_CLOCK
from heinzel.kernel import (
    KERNEL_SECTION,
    CurrentUser,
    DaemonAttestation,
    Installation,
    Kernel,
    OperationLet,
    PluginNews,
    PluginReleases,
    ReleaseCache,
    ReleaseSource,
    Settings,
)
from heinzel.kernel._logging import get_logger, log_event
from heinzel.kernel._logging import step as log_step
from heinzel.modules.system.updater._schedule_plan import (
    ACCEPTANCE_EVENT,
    CHECK_EVERY,
    COUNTDOWN,
    FILENAME,
    FIRST_CHECK,
    JITTER,
    POSTPONE,
    RECHECK_GAP,
    RECOVER_OPERATION,
    RELOAD_RETRY,
    REPAIR_OPERATION,
    RETRY_AFTER_FAILURE,
    STEP_SECONDS,
    TIMEOUT,
    UPDATE_MODULE,
    UPDATE_OPERATION,
    Activity,
    AutoUpdateStatus,
    Plan,
    PluginHold,
    done,
    failed,
    failure_message,
    found,
    postpone,
    settle,
    step,
)
from heinzel.modules.system.updater._schedule_plan import (
    not_newer as _not_newer,
)
from heinzel.modules.system.updater._schedule_plan import (
    plan_phase as _phase,
)
from heinzel.modules.system.updater._schedule_policy import (
    CHECKS_OFF,
    DEVELOPMENT,
    DEVELOPMENT_VARIABLES,
    PINNED,
    SOURCE_CHECKOUT,
    TEST_OVERRIDE,
    development_reason,
    error_reason,
    plugin_holds,
    source_plugins,
    update_summary,
)
from heinzel.modules.system.updater._schedule_policy import (
    old_enough as _old_enough,
)
from heinzel.modules.system.updater._schedule_state import (
    StateFiles,
)
from heinzel.modules.system.updater._schedule_state import (
    locked as _locked,
)
from heinzel.modules.system.updater._schedule_state import (
    try_lock as _try_lock,
)
from heinzel.modules.system.updater._schedule_state import (
    unlock as _unlock,
)

__all__ = [
    "ACCEPTANCE_EVENT",
    "CHECKS_OFF",
    "CHECK_EVERY",
    "COUNTDOWN",
    "DEVELOPMENT",
    "DEVELOPMENT_VARIABLES",
    "FILENAME",
    "FIRST_CHECK",
    "JITTER",
    "PINNED",
    "POSTPONE",
    "RECHECK_GAP",
    "RECOVER_OPERATION",
    "RELOAD_RETRY",
    "REPAIR_OPERATION",
    "RETRY_AFTER_FAILURE",
    "SOURCE_CHECKOUT",
    "STEP_SECONDS",
    "TEST_OVERRIDE",
    "TIMEOUT",
    "UPDATE_MODULE",
    "UPDATE_OPERATION",
    "Activity",
    "AutoUpdateStatus",
    "Plan",
    "PluginHold",
    "Schedule",
    "StateFiles",
    "development_reason",
    "done",
    "failed",
    "failure_message",
    "found",
    "postpone",
    "settle",
    "step",
    "update_summary",
]

log = logging.getLogger("heinzel.heads.web._auto_update")
"""The logger the web head's updater always had: log names stay as they were before the plan moved here."""
events = get_logger("web.auto_update")


def _now() -> datetime:
    return SYSTEM_CLOCK.now()


class Schedule:
    """Run the web server update plan, retaining its countdown, deferral and restart outcome."""

    files: ClassVar[StateFiles]
    """The guarded state folder; the web head's `AutoUpdater` sets it."""

    def __init__(  # noqa: PLR0913 - the seams tests replace and all by keyword
        self,
        kernel: Kernel,
        *,
        restart: Callable[[], None] | None,
        activity: Activity | None = None,
        clock: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
        version: str = __version__,
        settings: Callable[[], Settings] | None = None,
        update: Callable[[dict[str, Any]], object] | None = None,
        development: Callable[[Path], str | None] | str | None = development_reason,
    ) -> None:
        """Load the saved plan and settle a restart that an earlier process started.

        `development` judges once why this process runs for development (`development_reason`); tests pass None
        for an installed hz or the reason itself.
        """
        self._kernel = kernel
        self._restart = restart
        self._activity = activity or Activity()
        self._clock = clock or _now
        self._random = rng or random.SystemRandom()
        self._version = version
        self._read_settings = settings or self._reread
        self._run_update = update or self._update
        self.development = development(kernel.settings.state_dir) if callable(development) else development
        self._lock = threading.Lock()
        self._stop = threading.Event()
        folder = kernel.settings.state_dir / "web"
        self._path = folder / FILENAME
        self._plan_lock = folder / "auto-update.lock"
        self._apply_lock = folder / "auto-update-apply.lock"
        self._held = False
        """Automatic updates couldn't run at the last look; once they can, the next look checks at once."""
        self._looked: tuple[datetime, bool, bool] | None = None
        """When hz and the plugins were last asked for news, and whether each answered; this process only."""
        self._error: str | None = None
        """Why the last look for news, or the last automatic update, failed; this process only."""
        self._waited: list[str] = []
        """What held the countdown off at the last look, so the log says it once per change."""
        self._applying: IO[str] | None = None
        """Held from the start of an apply until this process ends, so no other `hz ui serve` applies meanwhile."""
        self._reloading: str | None = None
        """The installed hz this process asked to restart into; asked once, the restart replaces the process."""
        self.plan = self._read(fresh=True) or Plan()
        now = self._clock()
        self._next_check = now + self._between(*FIRST_CHECK)
        if self.development is None:
            self._recover()
        if self.plan.restarting_to is not None:
            with self._editing() as plan:
                if plan.restarting_to == version:
                    done(plan, f"hz is up to date: {version}, updated automatically.")
                elif plan.restarting_to is not None:
                    failed(plan, failure_message(version), now, version)
        with self._editing() as plan:
            self._forget_installed_plugins(plan)
            settle(plan, version, now)

    def _forget_installed_plugins(self, plan: Plan) -> None:
        """Drop plugin news the installation already has (another update installed it): no needless restart."""
        if not plan.plugins or plan.applying or plan.restarting_to is not None:
            return
        try:
            installed = self._kernel.resolve(Installation).installed_versions(list(plan.plugins))
        except Exception:  # noqa: BLE001 - unreadable metadata keeps the news; the next check asks again
            return
        for name, version in list(plan.plugins.items()):
            if name in installed and _not_newer(version, installed[name]):
                del plan.plugins[name]
        if not plan.pending:
            plan.window_at = plan.apply_at = None
            plan.postponements = 0

    # Settings and conditions ----------------------------------------------------------------------------------------

    def _reread(self) -> Settings:
        try:
            return self._kernel.config.reread().settings(Settings, KERNEL_SECTION)
        except Exception:  # noqa: BLE001 - an unreadable file keeps the settings this server loaded
            return self._kernel.settings

    def reason(self) -> str | None:
        """Why automatic updates can't run here now; None when they can."""
        if self.development is not None:
            return self.development
        settings = self._read_settings()
        if not settings.update_check:
            return CHECKS_OFF
        if not settings.auto_update:
            return (
                "Automatic updates are off. Turn them on in the Updates pane, in Settings or with "
                "hz system config set hz.auto_update true."
            )
        if settings.update_channel != "stable":
            return "Automatic updates follow the stable channel only; this installation follows another."
        return self._installation_reason()

    def _installation_reason(self) -> str | None:
        """Why this installation can't update itself; None when it can."""
        if self._restart is None:
            return "This server can't restart itself, so it can't update itself."
        return None if self._follows_releases() else PINNED

    def _follows_releases(self) -> bool:
        """Whether hz follows release tags or the package index: the installations `system.apply-announced` holds."""
        pin = self._kernel.resolve(Installation).core_pin()
        return pin is not None and (
            (pin.kind == "release" and pin.git is not None)
            or (pin.kind in {"index", "default"} and pin.git is None and not (pin.ref or "").startswith("=="))
        )

    def by_hand(self) -> list[str]:
        """Installed plugins from git, a folder or a URL: automatic updates leave them exactly as they are (ADR 0116).

        hz and the plugins from PyPI update around them; a person updates these. A plugin pinned to a GitHub commit
        whose repository tags its releases updates by itself (ADR 0137), once a check could read those tags.
        """
        followed = set(self.plan.followed)
        try:
            return source_plugins(self._kernel.resolve(Installation).plugin_pins(), followed)
        except Exception:  # noqa: BLE001 - an unreadable receipt holds updates as well
            return ["the plugins (the uv receipt can't be read)"]

    def holds(self) -> list[PluginHold]:
        """Every installed plugin automatic updates leave alone: held to one version, or installed from a source."""
        try:
            pins = self._kernel.resolve(Installation).plugin_pins()
        except Exception:  # noqa: BLE001 - an unreadable receipt holds updates as well
            return []
        return plugin_holds(pins, set(self.plan.followed))

    def busy(self) -> bool:
        """Whether a chat answer, an install or a daemon job is running now."""
        return bool(self.waiting_for())

    def waiting_for(self) -> list[str]:
        """What holds off the countdown now, by name: requests that change state, or a daemon job."""
        running = self._activity.running()
        if self._kernel.provides(DaemonAttestation):
            try:
                if self._kernel.resolve(DaemonAttestation).job_running():
                    running.append("a daemon job")
            except Exception:
                log.debug("the daemon's tick lock couldn't be read", exc_info=True)
        return running

    # The loop --------------------------------------------------------------------------------------------------------

    def start(self) -> None:
        """Run the plan on a background thread until `stop`; never while developing, so nothing is checked."""
        if self.development is not None:
            log.info("automatic updates are off: %s", self.development)
            return
        threading.Thread(target=self._run, name="hz-auto-update", daemon=True).start()

    def stop(self) -> None:
        """End the background thread."""
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(STEP_SECONDS):
            try:
                self.tick()
            except Exception:
                log.exception("automatic update step failed")

    def tick(self) -> None:
        """One look at the plan: check when due, open or cancel the countdown, apply when it ends."""
        now = self._clock()
        if self.reason() is not None:
            self._held = True
            if self.plan.apply_at is not None:
                with self._editing() as plan:
                    plan.apply_at = None
            return
        if self._held:  # just turned on (or update checks on again): look now, not at the next half hour
            self._held = False
            self._next_check = now
        checking = now >= self._next_check
        if checking:
            self._next_check = now + CHECK_EVERY + self._between(timedelta(0), JITTER)
            checking = self.check(now)  # False once this process restarts into an hz installed under it
        waiting = self.waiting_for()
        if waiting != self._waited:  # what holds the countdown off, in the log once per change
            self._waited = waiting
            log.info("automatic update %s", f"waits for: {', '.join(waiting)}" if waiting else "waits for nothing")
        with self._editing() as plan:
            counting = plan.apply_at
            due = step(plan, now, busy=bool(waiting))
            if due:
                plan.applying = True
            elif plan.apply_at != counting:
                log.info("automatic update countdown %s", "opened" if plan.apply_at else "held off")
        if due:
            log.info("automatic update applies now: %s", update_summary(plan))
            self.apply()
        elif checking and not waiting and not self.plan.core and not self.plan.plugins:
            self._refresh_desktop()
            self._repair_plugins()

    def _repair_plugins(self) -> None:
        """Install the plugins installed plugins require but lack (`system.plugin.repair`), then restart to load them.

        Each missing plugin is tried once per version of the plugin that requires it; a failure leaves a notice that
        disappears once the plugin is there. Nothing is signed in: System health names each sign-in step.
        """
        found = next(
            (op for record, op in self._kernel.lets(OperationLet) if (record.qualname, op.name) == REPAIR_OPERATION),
            None,
        )
        if found is None or not self._kernel.available(found.func):
            return
        try:
            with self._kernel.resolve(CurrentUser).local("daemon"):
                result = self._kernel.invoke(found, automatic=True)
        except HeinzelError as exc:  # another change is running: the next check asks again
            log_event(log, "update.auto.repair", outcome="skipped", reason=str(exc))
            return
        if getattr(result, "needs", None):
            log_event(log, "update.auto.repair", outcome="ok", detail=getattr(result, "message", ""))
        if getattr(result, "restart_required", False) and self._restart is not None:
            try:
                self._restart()
            except Exception as exc:  # noqa: BLE001 - the plugins are installed; the next start loads them
                log_event(log, "update.auto.repair_restart", level=logging.WARNING, outcome="error", error=str(exc))

    def _refresh_desktop(self) -> None:
        """Bring an already installed desktop shell up to this running stable Core: the web head's `AutoUpdater` does.

        The desktop installation belongs to the heads, which this context does not import.
        """

    def check(self, now: datetime) -> bool:
        """Ask for a newer stable hz and newer plugins old enough to install; what can't be asked stays as it was.

        A release counts only once its last file on PyPI is as old as `auto_update_min_age_hours` (hz) or
        `auto_update_plugin_min_age_hours` (plugins), and never as a pre-release, a draft or a yanked one. For hz from
        a release tag, the commit its tag names now is recorded, so a tag moved later is refused at apply time and
        once installed. hz and the plugins are asked apart: GitHub or PyPI failing for one keeps the other's news.

        Returns:
            False, asking nothing, when another update installed a different hz under this process: it restarts into
            that one instead (`_load_installed`), which then asks itself. True after asking.
        """
        if self._load_installed(now):
            return False
        settings = self._read_settings()
        core_ok = plugins_ok = True
        errors: list[str] = []
        try:
            core, commit, core_held = self._check_core(settings, now)
        except Exception as exc:  # noqa: BLE001 - offline or rate-limited: keep what hz's last check found
            log.info("automatic update check of hz failed: %s", exc)
            core, commit, core_held = self.plan.core, self.plan.commit, self.plan.core_held
            core_ok = False
            errors.append(f"hz: {error_reason(exc)}")
        try:
            news = self._check_plugins(settings, now)
        except Exception as exc:  # noqa: BLE001 - the same for the plugins
            log.info("automatic update check of the plugins failed: %s", exc)
            news = None
            plugins_ok = False
            errors.append(f"plugins: {error_reason(exc)}")
        self._looked = (now, core_ok, plugins_ok)
        self._error = "; ".join(errors) or None
        plugins = dict(self.plan.plugins) if news is None else {n: r.version for n, r in news.releases.items()}
        with self._editing() as plan:
            before = (plan.core, dict(plan.plugins))
            found(plan, core, plugins, now)
            if plan.core == core:
                plan.commit = commit
            plan.core_held = core_held
            if news is not None:
                commits = {n: r.commit for n, r in news.releases.items() if r.commit is not None}
                plan.plugin_commits = {n: c for n, c in commits.items() if n in plan.plugins}
                plan.followed = list(news.followed)
                if news.held != plan.held_plugins:
                    for line in news.held:
                        log.info("automatic update holds a plugin back: %s", line)
                plan.held_plugins = list(news.held)
            if (plan.core, plan.plugins) != before:
                log.info("automatic update found: %s", update_summary(plan) or "nothing newer")
        return True

    def _check_core(self, settings: Settings, now: datetime) -> tuple[str | None, str | None, str | None]:
        """A newer stable, accepted hz old enough to install, its tag's commit, and a newer one held back.

        The commit is the one its tag names, for an installation from a tag; the held one is a release that is
        not accepted (yet).

        Acceptance is the release train's verdict on the published wheel, uploaded with the release; a release
        without it never installs itself, however old. `hz system update` doesn't ask.
        """
        source = self._kernel.resolve(ReleaseSource)
        latest = source.latest_version(TIMEOUT)
        ReleaseCache(self._kernel.settings.cache_dir).remember(latest, now)
        version = Version(latest)
        if version.is_prerelease or version.is_devrelease or version <= Version(self._version):
            return None, None, None
        index = self._kernel.resolve(PluginReleases) if self._kernel.provides(PluginReleases) else None
        uploaded = index.release("heinzel", str(version), TIMEOUT) if index is not None else None
        if not _old_enough(uploaded.uploaded_at if uploaded else None, settings.auto_update_min_age_hours, now):
            return None, None, None
        with log_step(events, ACCEPTANCE_EVENT, operation="auto_update", step="acceptance", version=str(version)) as s:
            if not source.accepted(str(version), TIMEOUT):
                s.skip("not accepted")
                return None, None, str(version)
        if not self._from_tag():
            return str(version), None, None  # from PyPI: its release there is what counts, GitHub isn't asked again
        facts = source.release_facts(str(version), TIMEOUT)
        return (None, None, None) if facts.prerelease else (str(version), facts.commit, None)

    def _check_plugins(self, settings: Settings, now: datetime) -> PluginNews:
        """Newer plugin releases old enough to install that can move with what they need, dependencies first."""
        if not self._kernel.provides(PluginReleases):
            return PluginNews({})
        hours = settings.auto_update_plugin_min_age_hours
        return self._kernel.resolve(PluginReleases).announceable(
            TIMEOUT, lambda release: _old_enough(release.uploaded_at, hours, now)
        )

    def _from_tag(self) -> bool:
        """Whether hz is installed from a release tag (else from PyPI)."""
        pin = self._kernel.resolve(Installation).core_pin()
        return pin is not None and pin.kind == "release" and pin.git is not None

    def apply(self) -> None:
        """Install exactly the announced versions (`system.apply_announced`), then restart in place.

        The operation copies the tool folder, installs exactly these versions or nothing, reads their metadata, starts
        the new hz on throwaway folders, and on any failure puts everything back. Before that, the tag hz is installed
        from must still name the commit it named when it was announced. Any failure keeps this process, the old
        version, running.
        """
        if self._load_installed(self._clock()):
            with self._editing() as plan:  # nothing applies from here: the restarted process asks for news itself
                plan.applying = False
                plan.apply_at = None
                plan.window_at = self._clock() + RELOAD_RETRY
            return
        if self._applying is None and (handle := _try_lock(self._apply_lock, self.files)) is not None:
            self._applying = handle
            weakref.finalize(self, _unlock, handle)  # closed with this updater at the latest
        if self._applying is None:
            # Another `hz ui serve` on this state folder applies now: it restarts with the news, this one waits.
            log.info("automatic update left to another server that is applying it")
            with self._editing() as plan:
                plan.applying = False
                plan.apply_at = None
                plan.window_at = self._clock() + POSTPONE
            return
        news: dict[str, Any] = {"core": self.plan.core, "plugins": dict(self.plan.plugins), "commit": self.plan.commit}
        if commits := {n: c for n, c in self.plan.plugin_commits.items() if n in self.plan.plugins}:
            news["plugin_commits"] = commits
        if (moved := self._tag_moved(news) or self._plugin_tag_moved(news)) is not None:
            self._fail(moved)
            return
        try:
            result = self._run_update(news)
        except Exception as exc:  # noqa: BLE001 - every failure leaves the old version running and is reported once
            log.warning("automatic update failed: %s", exc)
            self._error = error_reason(exc)
            self._fail(
                failure_message(self._version)
            )  # the stored words stay as 0.9.21 wrote them: recovery reads them
            return
        after = getattr(result, "after", None)
        message = getattr(result, "message", None) or failure_message(self._version)
        if getattr(result, "refused", False):
            with self._editing() as plan:
                plan.refused = {"core": news["core"], "plugins": news["plugins"]}
        if not getattr(result, "updated", False) or not isinstance(after, str) or self._restart is None:
            self._fail(message)
            return
        with self._editing() as plan:
            plan.restarting_to = after
        try:
            self._restart()  # the apply lock stays held until this process is replaced
        except Exception as exc:  # noqa: BLE001 - the windows must stop showing "applying" and say what to do
            log.warning("restarting into hz %s failed: %s", after, exc)
            self._fail(
                f"hz {after} is installed, but this server couldn't restart into it, so hz {self._version} keeps "
                "running. Restart hz ui to finish the update."
            )

    def _installed_elsewhere(self) -> str | None:
        """The hz version installed now when it isn't the one this process runs; None when it is, or can't be read.

        `hz system update`, the footer of another window, another `hz ui serve` on this state folder or the desktop
        app may have installed a release since this process started. Doubt counts as none: no restart on a guess.
        """
        try:
            installed = self._kernel.resolve(Installation).installed_core_version()
            return installed if Version(installed) != Version(self._version) else None
        except Exception:  # noqa: BLE001 - unreadable metadata keeps this process; the next check asks again
            log_event(log, "update.auto.installed_version", level=logging.DEBUG, outcome="unreadable", exc_info=True)
            return None

    def _load_installed(self, now: datetime) -> bool:
        """Restart into the hz installed under this process, once idle; True while the installed one differs.

        This process can't apply news then: `system.apply-announced` refuses an outdated process ("reload before
        updating"), and that failure would hold every server on this state folder off for six hours, while the
        footer keeps saying an update is available. The process that starts runs what is installed and asks for
        news itself.
        """
        installed = self._installed_elsewhere()
        if installed is None or self._restart is None:
            return False
        if self._reloading == installed:
            return True
        if waiting := self.waiting_for():
            self._next_check = now + RELOAD_RETRY
            log_event(log, "update.auto.reload", outcome="waiting", installed=installed, waiting=", ".join(waiting))
            return True
        log_event(log, "update.auto.reload", outcome="restarting", installed=installed, running=self._version)
        self._reloading = installed
        try:
            self._restart()
        except Exception as exc:  # noqa: BLE001 - this process keeps running; the next look tries again
            self._reloading = None
            self._next_check = now + RELOAD_RETRY
            log_event(
                log, "update.auto.reload", level=logging.WARNING, outcome="error", installed=installed, error=str(exc)
            )
        return True

    def _with_reason(self, message: str | None) -> str | None:
        """The plan's failure message with why it failed, when this process saw the failure; the stored words stay."""
        if message is not None and self._error and message == failure_message(self._version):
            return f"{message} Reason: {self._error}"
        return message

    def _fail(self, message: str) -> None:
        with self._editing() as plan:
            failed(plan, message, self._clock(), self._version)
        _unlock(self._applying)
        self._applying = None

    def _tag_moved(self, news: dict[str, Any]) -> str | None:
        """Why hz's tag can't be trusted now: it names another commit than when announced, or can't be asked."""
        core, commit = news["core"], news["commit"]
        if core is None or commit is None or not self._from_tag():  # from PyPI: no tag, GitHub isn't asked
            return None
        try:
            now = self._kernel.resolve(ReleaseSource).release_facts(core, TIMEOUT).commit
        except Exception as exc:  # noqa: BLE001 - offline: not now, and say so
            log.info("automatic update: the tag of hz %s couldn't be asked: %s", core, exc)
            return failure_message(self._version)
        if now == commit:
            return None
        return (
            f"The tag of hz {core} names another commit than when it was announced, so nothing was installed and "
            f"hz {self._version} keeps running. It will look again in 6 hours."
        )

    def _plugin_tag_moved(self, news: dict[str, Any]) -> str | None:
        """Why a tagged plugin can't be trusted now: its tag names another commit than when announced, or is unasked."""
        commits: dict[str, str] = news.get("plugin_commits") or {}
        for name, commit in commits.items():
            version = news["plugins"][name]
            try:
                now = self._kernel.resolve(PluginReleases).tag_commit(name, version, TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - offline: not now, and say so
                log.info("automatic update: the tag of %s %s couldn't be asked: %s", name, version, exc)
                return failure_message(self._version)
            if now != commit:
                return (
                    f"The tag of {name} {version} names another commit than when it was announced, so nothing was "
                    f"installed and hz {self._version} keeps running. It will look again in 6 hours."
                )
        return None

    def _recover(self) -> None:
        """Put back an update that a dead process left half done, before anything else (`system.recover-announced`).

        Only while no other `hz ui serve` on this state folder applies one: its copy is in use.
        """
        handle = _try_lock(self._apply_lock, self.files)
        if handle is None:
            return
        try:
            self._recover_with_lock()
        finally:
            _unlock(handle)

    def _recover_with_lock(self) -> None:
        for record, operation in self._kernel.lets(OperationLet):
            if record.qualname == UPDATE_MODULE and operation.name == RECOVER_OPERATION:
                try:
                    with self._kernel.resolve(CurrentUser).local("daemon"):
                        self._kernel.invoke(operation)
                except Exception:
                    log.exception("finishing an interrupted automatic update failed")

    def _update(self, news: dict[str, Any]) -> object:
        plugins = [f"{name}=={version}" for name, version in news["plugins"].items()]
        tagged: dict[str, str] = news.get("plugin_commits") or {}
        commits = [f"{name}@{commit}" for name, commit in tagged.items()]
        for record, operation in self._kernel.lets(OperationLet):
            if record.qualname == UPDATE_MODULE and operation.name == UPDATE_OPERATION:
                with self._kernel.resolve(CurrentUser).local("daemon"):
                    return self._kernel.invoke(
                        operation, core=news["core"], plugins=plugins, commit=news["commit"], plugin_commits=commits
                    )
        msg = "The system module is off, so hz can't update itself."
        raise RuntimeError(msg)

    # What windows see and do -----------------------------------------------------------------------------------------

    def status(self) -> AutoUpdateStatus:
        """The plan as every window shows it."""
        now = self._clock()
        reason = self.reason()
        with self._lock:
            plan = self.plan
            phase = _phase(plan, reason)
            left = max(0, round((plan.apply_at - now).total_seconds())) if plan.apply_at is not None else None
            return AutoUpdateStatus(
                enabled=reason is None,
                reason=reason,
                phase=phase,
                version=self._version,
                core=plan.core,
                plugins=dict(plan.plugins),
                seconds_left=left if phase == "countdown" else None,
                apply_at=plan.apply_at if phase == "countdown" else None,
                next_window_at=plan.window_at if phase == "waiting" else None,
                postponements=plan.postponements,
                message=None if self.development is not None else self._with_reason(plan.message),
                development=self.development,
                held=self.development or (reason if reason == CHECKS_OFF else None),
                error=None if self.development is not None else self._error,
                by_hand=[] if self.development is not None else self.by_hand(),
                holds=[] if self.development is not None else self.holds(),
                checked_at=self._looked[0] if self._looked else None,
                core_checked=self._looked[1] if self._looked else None,
                plugins_checked=self._looked[2] if self._looked else None,
                plugins_held=[] if self.development is not None else list(plan.held_plugins),
                core_held=None if self.development is not None else plan.core_held,
            )

    def recheck(self) -> AutoUpdateStatus:
        """Look for news now because a person asked (the pane's retry button), at most once every ten seconds."""
        now = self._clock()
        if self._looked is not None and now - self._looked[0] < RECHECK_GAP:
            return self.status()
        if self.reason() is None:
            self.check(now)
        elif self.development is None and self._read_settings().update_check:
            # Automatic updates can't run here, but the pane still shows when hz itself was last looked up.
            ReleaseCache(self._kernel.settings.cache_dir).refresh(self._kernel.resolve(ReleaseSource), TIMEOUT)
            self._looked = (now, ReleaseCache(self._kernel.settings.cache_dir).error is None, False)
        return self.status()

    def postpone(self) -> AutoUpdateStatus:
        """Put the update off by an hour, for every window."""
        with self._editing() as plan:
            postpone(plan, self._clock())
        return self.status()

    def dismiss(self) -> AutoUpdateStatus:
        """The message was read, in one window for all of them."""
        with self._editing() as plan:
            plan.message = None
        return self.status()

    # State file ------------------------------------------------------------------------------------------------------

    def _between(self, low: timedelta, high: timedelta) -> timedelta:
        return low + (high - low) * self._random.random()

    def _read(self, *, fresh: bool = False, folder: int | None = None) -> Plan | None:
        try:
            with contextlib.ExitStack() as stack:
                if folder is None:
                    folder = stack.enter_context(self.files.private_directory(self._path.parent))
                descriptor = self.files.open_updater_file(folder, self._path.name, os.O_RDONLY)
                with os.fdopen(descriptor, encoding="utf-8") as handle:
                    return Plan.from_json(json.load(handle), fresh=fresh)
        except (OSError, ValueError, HeinzelError):
            return None

    @contextmanager
    def _editing(self) -> Generator[Plan]:
        """The plan as the state file has it now, to change and save, under a lock every `hz ui serve` takes.

        Two servers on one state folder each change the latest plan, never their own older copy, so neither undoes
        the other's postpone, dismissal or news (no last writer wins). What only this process knows (that it applies
        now) stays as it is.

        Yields:
            The latest plan, saved after the block if it changed.
        """
        with self._lock, _locked(self._plan_lock, self.files) as folder:
            latest = self._read(folder=folder) if folder is not None else None
            if latest is not None:
                latest.applying = self.plan.applying
                self.plan = latest
            before = self.plan.to_json()
            yield self.plan
            if folder is not None and self.plan.to_json() != before:
                self._save(folder=folder)

    def _save(self, *, folder: int | None = None) -> None:
        """Replace the state file at once; an unwritable state folder only costs what survives a restart."""
        if folder is None:
            with _locked(self._plan_lock, self.files) as locked:
                if locked is not None:
                    self._save(folder=locked)
            return
        partial = f".{FILENAME}.{secrets.token_hex(12)}"
        created = False
        try:
            with contextlib.suppress(FileNotFoundError):
                os.close(self.files.open_updater_file(folder, self._path.name, os.O_RDONLY))
            descriptor = self.files.open_updater_file(folder, partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            created = True
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.plan.to_json(), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(partial, self._path.name, src_dir_fd=folder, dst_dir_fd=folder)
            os.fsync(folder)
        except (OSError, HeinzelError):
            return
        finally:
            if created:
                with contextlib.suppress(OSError):
                    os.unlink(partial, dir_fd=folder)
