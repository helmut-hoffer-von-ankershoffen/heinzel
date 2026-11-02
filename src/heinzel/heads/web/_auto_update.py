"""Automatic updates: the `hz ui` server checks, warns every window, waits for idle and applies (ADR 0116).

The plan lives here, in the server, never in a browser: two windows show the same countdown, a reload shows what is
left of it, and with no window open the update still applies on time. It is saved under the state folder, so a
restart keeps postponements, the last failure and the version a restart is expected to bring.

Applying uses what a person's click uses: the `system.update` operation (hz and plugins together, rolled back by the
installer when it fails) and the restart in place on the same port. The head never imports a module: it finds the
operation, the release sources and the daemon status through the kernel.
"""

import contextlib
import fcntl
import importlib.metadata
import json
import logging
import os
import random
import sys
import threading
import weakref
from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any, Literal, cast

from fastapi import FastAPI, HTTPException, Response
from packaging.version import Version
from pydantic import BaseModel, Field

from heinzel import __version__
from heinzel.heads._stream import WORK
from heinzel.kernel import (
    KERNEL_SECTION,
    CurrentUser,
    DaemonAttestation,
    Installation,
    Kernel,
    OperationLet,
    PluginReleases,
    ReleaseCache,
    ReleaseSource,
    Settings,
)

log = logging.getLogger(__name__)

CHECK_EVERY = timedelta(minutes=30)
"""How often the server asks for newer releases."""
JITTER = timedelta(minutes=5)
"""At most this much is added to each check at random, so many installations don't ask at the same second."""
FIRST_CHECK = (timedelta(minutes=1), timedelta(minutes=5))
"""The first check comes this long after start, at random within the range."""
COUNTDOWN = timedelta(seconds=30)
"""The warning every window gets before an update applies."""
POSTPONE = timedelta(hours=1)
"""How far one click on Postpone moves the apply time."""
RETRY_AFTER_FAILURE = timedelta(hours=6)
"""The earliest next attempt after a failed one."""
TIMEOUT = 10.0
"""Seconds a release source may take per network operation."""
STEP_SECONDS = 1.0
"""How often the server looks at its plan."""
FILENAME = "auto-update.json"

UPDATE_MODULE, UPDATE_OPERATION, RECOVER_OPERATION = "system", "apply-announced", "recover-announced"

type Phase = Literal["off", "idle", "waiting", "countdown", "applying"]


@dataclass
class Plan:
    """What is pending and when it applies; pure data, changed only by the functions below."""

    core: str | None = None
    """The newer hz release, if any."""
    plugins: dict[str, str] = field(default_factory=dict[str, str])
    """Plugins with a newer release, by distribution, mapped to it."""
    window_at: datetime | None = None
    """The earliest time the countdown may start; None when nothing is pending."""
    apply_at: datetime | None = None
    """When the running countdown ends and the update applies; None when no countdown runs."""
    postponements: int = 0
    retry_at: datetime | None = None
    """No attempt before this, after a failure."""
    message: str | None = None
    """One plain message for the person: an update that worked or failed."""
    applying: bool = False
    """The update runs now."""
    restarting_to: str | None = None
    """The version a restart in place is expected to load, saved before restarting."""
    commit: str | None = None
    """The commit the announced hz release's tag named when it was announced; checked once it is installed."""
    refused: dict[str, Any] | None = None
    """News that was installed but didn't start, and was put back: never applied again; newer news is."""

    @property
    def pending(self) -> bool:
        """Whether anything newer is waiting to be installed."""
        return self.core is not None or bool(self.plugins)

    def to_json(self) -> dict[str, Any]:
        """The plan as JSON for the state file."""

        def stamp(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "core": self.core,
            "plugins": self.plugins,
            "window_at": stamp(self.window_at),
            "apply_at": stamp(self.apply_at),
            "postponements": self.postponements,
            "retry_at": stamp(self.retry_at),
            "message": self.message,
            "restarting_to": self.restarting_to,
            "refused": self.refused,
            "commit": self.commit,
        }

    @classmethod
    def from_json(cls, data: object, *, fresh: bool = False) -> "Plan":
        """A plan from the state file; anything unreadable starts afresh; `fresh` at a server's start."""
        if not isinstance(data, dict):
            return cls()
        values: dict[str, Any] = dict(data)  # pyright: ignore[reportUnknownArgumentType] - free-form JSON

        def when(name: str) -> datetime | None:
            value = values.get(name)
            try:
                parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
            except ValueError:
                return None
            return parsed if parsed is None or parsed.tzinfo else parsed.replace(tzinfo=UTC)

        def text(name: str) -> str | None:
            value = values.get(name)
            return value if isinstance(value, str) else None

        found_plugins: object = values.get("plugins")
        plugins = (
            {str(k): str(v) for k, v in found_plugins.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            if isinstance(found_plugins, dict)
            else {}
        )
        count = values.get("postponements")
        return cls(
            core=text("core"),
            plugins=plugins,
            window_at=when("window_at"),
            # A countdown never survives a restart: the windows must see it again. Another server's countdown does.
            apply_at=None if fresh else when("apply_at"),
            postponements=count if isinstance(count, int) and count >= 0 else 0,
            retry_at=when("retry_at"),
            message=text("message"),
            restarting_to=text("restarting_to"),
            refused=_news(values.get("refused")),
            commit=text("commit"),
        )


def _news(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    news = cast("dict[str, Any]", value)
    plugins: object = news.get("plugins")
    core: object = news.get("core")
    return {
        "core": core if isinstance(core, str) else None,
        "plugins": {str(k): str(v) for k, v in cast("dict[object, object]", plugins).items()}
        if isinstance(plugins, dict)
        else {},
    }


def found(plan: Plan, core: str | None, plugins: dict[str, str], now: datetime) -> None:
    """Record what a check found. The same news keeps its times; new news waits for its own window.

    News that was put back after it didn't start counts as no news, so the same broken release is never retried.
    """
    if plan.refused == {"core": core, "plugins": dict(plugins)}:
        core, plugins = None, {}
    if core == plan.core and plugins == plan.plugins:
        return
    first_news = not plan.pending
    plan.core, plan.plugins = core, dict(plugins)
    if core is None and not plugins:
        plan.window_at = plan.apply_at = None
        plan.postponements = 0
        return
    if first_news:
        plan.window_at = max(now, plan.retry_at) if plan.retry_at else now
        plan.apply_at = None
        plan.postponements = 0


def step(plan: Plan, now: datetime, *, busy: bool) -> bool:
    """Advance the plan to `now`; True when the update should apply now.

    The countdown starts only when nothing is busy, and work that starts during it cancels it until all is idle again,
    when a fresh countdown warns every window once more.
    """
    if not plan.pending or plan.applying or plan.window_at is None:
        return False
    if plan.retry_at is not None and now < plan.retry_at:
        plan.apply_at = None
        return False
    if plan.apply_at is None:
        if now >= plan.window_at and not busy:
            plan.apply_at = now + COUNTDOWN
        return False
    if busy:
        plan.apply_at = None
        plan.window_at = now
        return False
    return now >= plan.apply_at


def postpone(plan: Plan, now: datetime) -> None:
    """Move the apply time one hour later; the countdown comes back 30 seconds before it."""
    if not plan.pending or plan.applying:
        return
    due = plan.apply_at or max(now + COUNTDOWN, (plan.window_at or now) + COUNTDOWN)
    plan.apply_at = None
    plan.window_at = due + POSTPONE - COUNTDOWN
    plan.postponements += 1


def failed(plan: Plan, message: str, now: datetime) -> None:
    """Keep the news, wait at least six hours, and say what happened once."""
    plan.applying = False
    plan.apply_at = None
    plan.restarting_to = None
    plan.retry_at = now + RETRY_AFTER_FAILURE
    plan.window_at = plan.retry_at
    plan.message = message


def done(plan: Plan, message: str) -> None:
    """Forget what was pending after an update that worked, keeping only the message that says so."""
    plan.__dict__.update(Plan(message=message).__dict__)


def failure_message(running: str) -> str:
    """The one plain message after a failed attempt."""
    return (
        f"The automatic update didn't work, so hz {running} keeps running as before. It will try again in 6 hours; "
        "to update now, use Update in the footer."
    )


class AutoUpdateStatus(BaseModel):
    """What every window shows about automatic updates; the server's plan, never a browser's."""

    enabled: bool = Field(description="Whether automatic updates are on and can work here.")
    reason: str | None = Field(description="Why they can't work here, in plain words; none when they can.")
    phase: Phase = Field(description="off, idle (nothing newer), waiting, countdown or applying.")
    version: str = Field(description="The hz version this server loaded.")
    core: str | None = Field(description="The newer hz release that will be installed, if any.")
    plugins: dict[str, str] = Field(description="Plugins that will be updated, mapped to their new version.")
    seconds_left: int | None = Field(description="Seconds until the update applies, while the countdown runs.")
    apply_at: datetime | None = Field(description="When the update applies, while the countdown runs.")
    next_window_at: datetime | None = Field(description="When the next countdown may start, while waiting.")
    postponements: int = Field(description="How often the update has been put off.")
    message: str | None = Field(description="One plain message about the last automatic update.")
    development: str | None = Field(
        default=None,
        description="Why this hz runs for development, so it never updates itself and both switches are disabled.",
    )
    by_hand: list[str] = Field(
        default_factory=list[str],
        description="Installed plugins from git, a folder or a URL, which only a person updates.",
    )
    held: str | None = Field(
        default=None,
        description="The one line both switches show, disabled, when the setting can't turn updates on here: "
        "development, or update checks off.",
    )


def _phase(plan: Plan, reason: str | None) -> Phase:
    """The phase every window shows for `plan`."""
    if reason is not None:
        return "off"
    if plan.applying or plan.restarting_to:
        return "applying"
    if plan.apply_at is not None:
        return "countdown"
    return "waiting" if plan.pending else "idle"


class Activity:
    """Requests that change state and are still running in this server: a chat answer, an install."""

    def __init__(self) -> None:
        """Nothing running yet."""
        self._lock = threading.Lock()
        self._running: dict[str, int] = {}

    def enter(self, what: str = "a request") -> None:
        """One more request is running; `what` names it (`POST /api/...`) for the log."""
        with self._lock:
            self._running[what] = self._running.get(what, 0) + 1

    def leave(self, what: str = "a request") -> None:
        """One request finished."""
        with self._lock:
            count = self._running.get(what, 0) - 1
            if count > 0:
                self._running[what] = count
            else:
                self._running.pop(what, None)

    @property
    def busy(self) -> bool:
        """Whether any is running."""
        with self._lock:
            return bool(self._running)

    def running(self) -> list[str]:
        """What is running, by name, for the log."""
        with self._lock:
            return sorted(self._running)


SOURCE_CHECKOUT = "Updates are managed by your source checkout."
"""What both switches say when hz runs from a source checkout or an editable install."""
DEVELOPMENT = "Automatic updates are off while hz runs for development or tests."
"""What both switches say when another development fact holds: tests, CI, a dev server or make, a test state folder."""
PINNED = "This installation is pinned or isn't a uv tool, so it can't update itself."
CHECKS_OFF = "Update checks are off, so hz neither looks for updates nor installs them by itself."
"""What both switches say while `update_check` is off: no automatic update without a check."""
TEST_OVERRIDE = "HEINZEL_AUTO_UPDATE_TEST"
"""Set to 1 by a test that drives the real automatic-update flow: none of the development facts below count then."""
DEVELOPMENT_VARIABLES = (
    "HZ_DEV",
    "HZ_TEST",
    "CI",
    "PYTEST_CURRENT_TEST",
    "PYTEST_XDIST_WORKER",
    "TEST_WORKER_INDEX",  # the Playwright test runner
    "PLAYWRIGHT_TEST_BASE_URL",
    "MAKELEVEL",  # started by a make target
    "npm_lifecycle_event",  # started by an npm script, such as the Vite dev server
)
"""Environment variables that mean this process runs for development, a test or CI, whatever their value."""


def _distribution_is_editable() -> bool:
    """Whether the installed `heinzel` distribution is an editable install, as its `direct_url.json` records it."""
    try:
        text = importlib.metadata.distribution("heinzel").read_text("direct_url.json")
        data: object = json.loads(text) if text else None
    except (importlib.metadata.PackageNotFoundError, ValueError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    info: object = cast("dict[str, object]", data).get("dir_info")
    return isinstance(info, dict) and cast("dict[str, object]", info).get("editable") is True


def _default_state_dir() -> Path:
    factory = Settings.model_fields["state_dir"].default_factory
    return cast("Callable[[], Path]", factory)()


def _is_checkout(folder: Path) -> bool:
    return (folder / ".git").exists() or (folder / "pyproject.toml").is_file()


def development_reason(
    state_dir: Path,
    *,
    environ: Mapping[str, str] | None = None,
    package: Path | None = None,
    prefix: Path | None = None,
    editable: Callable[[], bool] = _distribution_is_editable,
) -> str | None:
    """Why this process runs for development, judged by facts; None for an installed hz with the user's state folder.

    Automatic updates never run then: an update would replace code someone is working on, or race a test (ADR 0116).
    The facts: the code is imported from a source tree or an editable install, or its environment sits in a checkout
    (a `.venv` next to `.git`); a development, test or CI variable is set; make or npm started it; or the state folder
    isn't the user's default. `HEINZEL_AUTO_UPDATE_TEST=1` turns them all off, for the test of the real flow.
    """
    env = os.environ if environ is None else environ
    if env.get(TEST_OVERRIDE) == "1":
        return None
    package = package or Path(__file__).resolve().parents[2]  # the `heinzel` package folder
    prefix = prefix or Path(sys.prefix)
    if "site-packages" not in package.parts or editable() or _is_checkout(prefix.parent):
        return SOURCE_CHECKOUT
    if any(name in env for name in DEVELOPMENT_VARIABLES):
        return DEVELOPMENT
    try:
        default = _default_state_dir().resolve()
        if state_dir.expanduser().resolve() != default:
            return DEVELOPMENT
    except OSError:
        return DEVELOPMENT
    return None


def update_summary(plan: Plan) -> str:
    """What a plan installs, in plain words, for the log."""
    parts = [f"hz {plan.core}"] if plan.core else []
    return ", ".join([*parts, *(f"{name} {version}" for name, version in plan.plugins.items())])


def _old_enough(published: datetime | None, hours: int, now: datetime) -> bool:
    """Whether a release published at `published` is at least `hours` old; an unknown time counts as too new."""
    if hours <= 0:
        return True
    return published is not None and now - published >= timedelta(hours=hours)


@contextmanager
def _locked(path: Path) -> Generator[None]:
    """Hold an exclusive lock on `path` for the block; without a writable state folder, go on without one."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8")
    except OSError:
        yield
        return
    with handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _try_lock(path: Path) -> IO[str] | None:
    """An exclusive lock on `path` if no other holder has it, kept until released or this process ends."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8")
    except OSError:
        return None
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _unlock(handle: IO[str] | None) -> None:
    if handle is not None and not handle.closed:
        with contextlib.suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _now() -> datetime:
    return datetime.now(UTC)


class AutoUpdater:
    """Runs the plan in the `hz ui` server: checks, countdown, postpone, apply, and the outcome after a restart."""

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
        self._waited: list[str] = []
        """What held the countdown off at the last look, so the log says it once per change."""
        self._applying: IO[str] | None = None
        """Held from the start of an apply until this process ends, so no other `hz ui` applies meanwhile."""
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
                    failed(plan, failure_message(version), now)

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

        hz and the plugins from PyPI update around them; a person updates these.
        """
        try:
            return sorted(pin.name for pin in self._kernel.resolve(Installation).plugin_pins() if pin.kind != "index")
        except Exception:  # noqa: BLE001 - an unreadable receipt holds updates as well
            return ["the plugins (the uv receipt can't be read)"]

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
        if now >= self._next_check:
            self._next_check = now + CHECK_EVERY + self._between(timedelta(0), JITTER)
            self.check(now)
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

    def check(self, now: datetime) -> None:
        """Ask for a newer stable hz and newer plugins old enough to install; what can't be asked stays as it was.

        A release counts only once its last file on PyPI is as old as `auto_update_min_age_hours` (hz) or
        `auto_update_plugin_min_age_hours` (plugins), and never as a pre-release, a draft or a yanked one. For hz from
        a release tag, the commit its tag names now is recorded, so a tag moved later is refused at apply time and
        once installed. hz and the plugins are asked apart: GitHub or PyPI failing for one keeps the other's news.
        """
        settings = self._read_settings()
        try:
            core, commit = self._check_core(settings, now)
        except Exception as exc:  # noqa: BLE001 - offline or rate-limited: keep what hz's last check found
            log.info("automatic update check of hz failed: %s", exc)
            core, commit = self.plan.core, self.plan.commit
        try:
            plugins = self._check_plugins(settings, now)
        except Exception as exc:  # noqa: BLE001 - the same for the plugins
            log.info("automatic update check of the plugins failed: %s", exc)
            plugins = dict(self.plan.plugins)
        with self._editing() as plan:
            before = (plan.core, dict(plan.plugins))
            found(plan, core, plugins, now)
            if plan.core == core:
                plan.commit = commit
            if (plan.core, plan.plugins) != before:
                log.info("automatic update found: %s", update_summary(plan) or "nothing newer")

    def _check_core(self, settings: Settings, now: datetime) -> tuple[str | None, str | None]:
        """A newer stable hz old enough to install, and the commit its tag names for an installation from a tag."""
        source = self._kernel.resolve(ReleaseSource)
        latest = source.latest_version(TIMEOUT)
        ReleaseCache(self._kernel.settings.cache_dir).remember(latest, now)
        version = Version(latest)
        if version.is_prerelease or version.is_devrelease or version <= Version(self._version):
            return None, None
        index = self._kernel.resolve(PluginReleases) if self._kernel.provides(PluginReleases) else None
        uploaded = index.release("heinzel", str(version), TIMEOUT) if index is not None else None
        if not _old_enough(uploaded.uploaded_at if uploaded else None, settings.auto_update_min_age_hours, now):
            return None, None
        if not self._from_tag():
            return str(version), None  # from PyPI: its release there is what counts, GitHub isn't asked again
        facts = source.release_facts(str(version), TIMEOUT)
        return (None, None) if facts.prerelease else (str(version), facts.commit)

    def _check_plugins(self, settings: Settings, now: datetime) -> dict[str, str]:
        if not self._kernel.provides(PluginReleases):
            return {}
        return {
            name: release.version
            for name, release in self._kernel.resolve(PluginReleases).newer_releases(TIMEOUT).items()
            if _old_enough(release.uploaded_at, settings.auto_update_plugin_min_age_hours, now)
        }

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
        if self._applying is None and (handle := _try_lock(self._apply_lock)) is not None:
            self._applying = handle
            weakref.finalize(self, _unlock, handle)  # closed with this updater at the latest
        if self._applying is None:
            # Another `hz ui` on this state folder applies now: it restarts with the news, this one waits.
            log.info("automatic update left to another server that is applying it")
            with self._editing() as plan:
                plan.applying = False
                plan.apply_at = None
                plan.window_at = self._clock() + POSTPONE
            return
        news: dict[str, Any] = {"core": self.plan.core, "plugins": dict(self.plan.plugins), "commit": self.plan.commit}
        if (moved := self._tag_moved(news)) is not None:
            self._fail(moved)
            return
        try:
            result = self._run_update(news)
        except Exception as exc:  # noqa: BLE001 - every failure leaves the old version running and is reported once
            log.warning("automatic update failed: %s", exc)
            self._fail(failure_message(self._version))
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

    def _fail(self, message: str) -> None:
        with self._editing() as plan:
            failed(plan, message, self._clock())
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

    def _recover(self) -> None:
        """Put back an update that a dead process left half done, before anything else (`system.recover-announced`).

        Only while no other `hz ui` on this state folder applies one: its copy is in use.
        """
        handle = _try_lock(self._apply_lock)
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
        for record, operation in self._kernel.lets(OperationLet):
            if record.qualname == UPDATE_MODULE and operation.name == UPDATE_OPERATION:
                with self._kernel.resolve(CurrentUser).local("daemon"):
                    return self._kernel.invoke(operation, core=news["core"], plugins=plugins, commit=news["commit"])
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
                message=None if self.development is not None else plan.message,
                development=self.development,
                held=self.development or (reason if reason == CHECKS_OFF else None),
                by_hand=[] if self.development is not None else self.by_hand(),
            )

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

    def _read(self, *, fresh: bool = False) -> Plan | None:
        try:
            return Plan.from_json(json.loads(self._path.read_text(encoding="utf-8")), fresh=fresh)
        except (OSError, ValueError):
            return None

    @contextmanager
    def _editing(self) -> Generator[Plan]:
        """The plan as the state file has it now, to change and save, under a lock every `hz ui` takes.

        Two servers on one state folder each change the latest plan, never their own older copy, so neither undoes
        the other's postpone, dismissal or news (no last writer wins). What only this process knows (that it applies
        now) stays as it is.

        Yields:
            The latest plan, saved after the block if it changed.
        """
        with self._lock, _locked(self._plan_lock):
            latest = self._read()
            if latest is not None:
                latest.applying = self.plan.applying
                self.plan = latest
            before = self.plan.to_json()
            yield self.plan
            if self.plan.to_json() != before:
                self._save()

    def _save(self) -> None:
        """Replace the state file at once; an unwritable state folder only costs what survives a restart."""
        partial = self._path.with_name(f".{FILENAME}.{os.getpid()}")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            partial.write_text(json.dumps(self.plan.to_json()), encoding="utf-8")
            partial.replace(self._path)
        except OSError:
            return


AUTO_UPDATE_PATH = "/auto-update"


def add_auto_update(app: FastAPI, updater: AutoUpdater | None) -> None:
    """`GET /auto-update`, `POST /auto-update/postpone` and `POST /auto-update/dismiss` behind the web head's token."""

    def require() -> AutoUpdater:
        if updater is None:
            raise HTTPException(status_code=409, detail="This server doesn't run automatic updates.")
        return updater

    refused: dict[int | str, dict[str, Any]] = {409: {"description": "This server doesn't run automatic updates."}}

    @app.get(AUTO_UPDATE_PATH, tags=["web"], responses=refused)
    def status(response: Response) -> AutoUpdateStatus:
        response.headers["Cache-Control"] = "no-store"
        return require().status()

    @app.post(f"{AUTO_UPDATE_PATH}/postpone", tags=["web"], responses=refused)
    def postpone_update(response: Response) -> AutoUpdateStatus:
        response.headers["Cache-Control"] = "no-store"
        return require().postpone()

    @app.post(f"{AUTO_UPDATE_PATH}/dismiss", tags=["web"], responses=refused)
    def dismiss_message(response: Response) -> AutoUpdateStatus:
        response.headers["Cache-Control"] = "no-store"
        return require().dismiss()


def held_until_sent(response: Response, leave: Callable[[], None]) -> Response:
    """`response`, calling `leave` once its body has been sent, the browser left, or the body was dropped unsent.

    A middleware's `call_next` returns when the headers go out; a streamed chat turn runs while its body streams, so
    the work it holds off an automatic update for ends only then.
    """
    body: AsyncIterable[Any] | None = getattr(response, "body_iterator", None)
    if body is None:
        leave()
        return response
    lock, left = threading.Lock(), [False]

    def done() -> None:
        with lock:
            if left[0]:
                return
            left[0] = True
        leave()

    async def held() -> AsyncGenerator[Any]:
        try:
            async for chunk in body:
                yield chunk
        finally:
            done()

    wrapped = held()
    weakref.finalize(wrapped, done)  # never iterated (the browser left before the first byte): still let go
    setattr(response, "body_iterator", wrapped)  # noqa: B010 - only streamed responses have it
    return response


async def as_work[R](activity: Activity, call_next: Callable[[R], Awaitable[Response]], request: R) -> Response:
    """Run a request that changes state as work: from its start until its response body has ended.

    A streamed chat turn runs while its body streams, and its operation's thread can outlive the stream when the
    browser leaves; `WORK` lets the stream hold the activity for that thread as well.
    """
    url = getattr(request, "url", None)
    what = f"{getattr(request, 'method', 'POST')} {getattr(url, 'path', '')}".strip()
    activity.enter(what)
    token = WORK.set((lambda: activity.enter(what), lambda: activity.leave(what)))
    try:
        response = await call_next(request)
    except BaseException:
        activity.leave(what)
        raise
    finally:
        WORK.reset(token)
    return held_until_sent(response, lambda: activity.leave(what))


def counts_as_work(method: str, path: str) -> bool:
    """Whether a request to the web head's API changes state and so holds off an update while it runs."""
    if method in {"GET", "HEAD", "OPTIONS"}:
        return False
    return not path.startswith(f"/api{AUTO_UPDATE_PATH}")
