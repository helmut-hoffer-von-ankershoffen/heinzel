"""Automatic updates: the `hz ui` server checks, warns every window, waits for idle and applies (ADR 0116).

The plan lives here, in the server, never in a browser: two windows show the same countdown, a reload shows what is
left of it, and with no window open the update still applies on time. It is saved under the state folder, so a
restart keeps postponements, the last failure and the version a restart is expected to bring.

Applying uses what a person's click uses: the `system.update` operation (hz and plugins together, rolled back by the
installer when it fails) and the restart in place on the same port. The head never imports a module: it finds the
operation, the release sources and the daemon status through the kernel.
"""

import json
import logging
import os
import random
import signal
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException, Response
from packaging.version import Version
from pydantic import BaseModel, Field

from heinzel import __version__
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

UPDATE_MODULE, UPDATE_OPERATION = "system", "update"

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
        }

    @classmethod
    def from_json(cls, data: object) -> "Plan":
        """A plan from the state file; anything unreadable starts afresh."""
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
            apply_at=None,  # a countdown never survives a restart: the windows must see it again
            postponements=count if isinstance(count, int) and count >= 0 else 0,
            retry_at=when("retry_at"),
            message=text("message"),
            restarting_to=text("restarting_to"),
        )


def found(plan: Plan, core: str | None, plugins: dict[str, str], now: datetime) -> None:
    """Record what a check found. The same news keeps its times; new news waits for its own window."""
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
        self._running = 0

    def enter(self) -> None:
        """One more request is running."""
        with self._lock:
            self._running += 1

    def leave(self) -> None:
        """One request finished."""
        with self._lock:
            self._running = max(0, self._running - 1)

    @property
    def busy(self) -> bool:
        """Whether any is running."""
        with self._lock:
            return self._running > 0


TRIAL_SECONDS = 90.0
"""How long the new version may take to answer before its start counts as failed."""


def trial_start(version: str, timeout: float = TRIAL_SECONDS) -> bool:
    """Whether the installed hz starts a web app server that answers with `version`, before this one restarts into it.

    It runs `hz ui` as the restart would, on a free port with its own token and automatic updates off, asks
    `/api/lifecycle` once, and stops it. Nothing of this server is handed over, so a failure changes nothing here.
    """
    env = {key: value for key, value in os.environ.items() if key != "HEINZEL_UI_RESUME"}
    env["HZ_AUTO_UPDATE"] = "false"
    command = [sys.executable, "-m", "heinzel", "ui", "--port", "0", "--no-browser"]
    try:
        child = subprocess.Popen(  # noqa: S603 - this interpreter and fixed arguments
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True
        )
    except OSError:
        return False
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.append(child.stdout.readline() if child.stdout else ""))
    reader.daemon = True
    reader.start()
    try:
        reader.join(timeout)
        return _answers(lines[0] if lines else "", version)
    finally:
        child.send_signal(signal.SIGINT)
        try:
            child.wait(15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(15)
        if child.stdout is not None:
            child.stdout.close()


def _answers(line: str, version: str) -> bool:
    """Whether the server whose session link is `line` answers `/api/lifecycle` with `version`."""
    url = urllib.parse.urlsplit(line.strip())
    token = urllib.parse.parse_qs(url.fragment).get("token", [""])[0]
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not token:
        return False
    request = urllib.request.Request(
        f"http://127.0.0.1:{url.port}/api/lifecycle", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - checked above
            answer: object = json.loads(response.read(64 * 1024))
    except (OSError, ValueError):
        return False
    return isinstance(answer, dict) and cast("dict[str, object]", answer).get("version") == version


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
        update: Callable[[], object] | None = None,
        trial: Callable[[str], bool] | None = None,
        restore: Callable[[str], object] | None = None,
    ) -> None:
        """Load the saved plan and settle a restart that an earlier process started."""
        self._kernel = kernel
        self._restart = restart
        self._activity = activity or Activity()
        self._clock = clock or _now
        self._random = rng or random.SystemRandom()
        self._version = version
        self._read_settings = settings or self._reread
        self._run_update = update or self._update
        self._trial = trial or trial_start
        self._restore = restore or self._reinstall
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._path = kernel.settings.state_dir / "web" / FILENAME
        self.plan = self._load()
        now = self._clock()
        self._next_check = now + self._between(*FIRST_CHECK)
        if self.plan.restarting_to is not None:
            if self.plan.restarting_to == version:
                done(self.plan, f"hz is up to date: {version}, updated automatically.")
            else:
                failed(self.plan, failure_message(version), now)
            self._save()

    # Settings and conditions ----------------------------------------------------------------------------------------

    def _reread(self) -> Settings:
        try:
            return self._kernel.config.reread().settings(Settings, KERNEL_SECTION)
        except Exception:  # noqa: BLE001 - an unreadable file keeps the settings this server loaded
            return self._kernel.settings

    def reason(self) -> str | None:
        """Why automatic updates can't run here now; None when they can."""
        settings = self._read_settings()
        if not settings.auto_update:
            return (
                "Automatic updates are off. Turn them on in the Updates pane, in Settings or with "
                "hz system config set hz.auto_update true."
            )
        if settings.update_channel != "stable":
            return "Automatic updates follow the stable channel only; this installation follows another."
        if self._restart is None:
            return "This server can't restart itself, so it can't update itself."
        pin = self._kernel.resolve(Installation).core_pin()
        follows = pin is not None and (
            pin.kind in {"release", "default", "index"} or (pin.kind, pin.ref) == ("ref", "main")
        )
        if not follows:
            return "This installation is pinned or isn't a uv tool, so it can't update itself."
        return None

    def busy(self) -> bool:
        """Whether a chat answer, an install or a daemon job is running now."""
        if self._activity.busy:
            return True
        if self._kernel.provides(DaemonAttestation):
            try:
                return self._kernel.resolve(DaemonAttestation).job_running()
            except Exception:  # noqa: BLE001 - an unknown daemon state must not block updates for good
                return False
        return False

    # The loop --------------------------------------------------------------------------------------------------------

    def start(self) -> None:
        """Run the plan on a background thread until `stop`."""
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
            with self._lock:
                if self.plan.apply_at is not None:
                    self.plan.apply_at = None
            return
        if now >= self._next_check:
            self._next_check = now + CHECK_EVERY + self._between(timedelta(0), JITTER)
            self.check(now)
        with self._lock:
            before = self.plan.to_json()
            due = step(self.plan, now, busy=self.busy())
            if due:
                self.plan.applying = True
            if self.plan.to_json() != before:
                self._save()
        if due:
            self.apply()

    def check(self, now: datetime) -> None:
        """Ask for a newer stable hz and newer plugins; a failed check changes nothing."""
        core: str | None = None
        plugins: dict[str, str] = {}
        try:
            latest = self._kernel.resolve(ReleaseSource).latest_version(TIMEOUT)
            ReleaseCache(self._kernel.settings.cache_dir).remember(latest, now)
            version = Version(latest)
            if not (version.is_prerelease or version.is_devrelease) and version > Version(self._version):
                core = str(version)
            if self._kernel.provides(PluginReleases):
                plugins = self._kernel.resolve(PluginReleases).newer(TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - offline or rate-limited: try at the next check
            log.info("automatic update check failed: %s", exc)
            return
        with self._lock:
            found(self.plan, core, plugins, now)
            self._save()

    def apply(self) -> None:
        """Install with `system.update`, prove the new version starts, then restart in place.

        Any failure keeps this process, the old version, running. If the new version was installed but doesn't
        start, the old one is installed again where its source can name it, before anything restarts.
        """
        previous = self._previous_source()
        try:
            result = self._run_update()
        except Exception as exc:  # noqa: BLE001 - every failure leaves the old version running and is reported once
            log.warning("automatic update failed: %s", exc)
            self._fail(failure_message(self._version))
            return
        after = getattr(result, "after", None)
        if not getattr(result, "updated", False) or not isinstance(after, str) or self._restart is None:
            # Something newer was announced but the installer changed nothing: say so once and wait, rather than
            # warning every window again at the next check.
            self._fail(failure_message(self._version))
            return
        if not self._trial(after):
            self._fail(self._roll_back(after, previous))
            return
        with self._lock:
            self.plan.restarting_to = after
            self._save()
        self._restart()

    def _fail(self, message: str) -> None:
        with self._lock:
            failed(self.plan, message, self._clock())
            self._save()

    def _previous_source(self) -> str | None:
        """Where the running version can be installed from again: its release tag; None when that can't be named."""
        try:
            pin = self._kernel.resolve(Installation).core_pin()
            return pin.source_at(pin.ref) if pin is not None and pin.kind == "release" and pin.ref else None
        except Exception:  # noqa: BLE001 - no source only means no reinstall
            return None

    def _roll_back(self, after: str, previous: str | None) -> str:
        """Install the running version again after the new one failed its start; the message that says what happened."""
        if previous is not None:
            try:
                self._restore(previous)
            except Exception as exc:  # noqa: BLE001 - reported in the message
                log.warning("restoring hz %s failed: %s", self._version, exc)
            else:
                return (
                    f"hz {after} was installed but didn't start, so hz {self._version} was put back and keeps running. "
                    "It will try again in 6 hours."
                )
        return (
            f"hz {after} was installed but didn't start. hz {self._version} keeps running until you restart it; "
            "before that, run hz system update --source with your previous version, or reinstall hz."
        )

    def _reinstall(self, source: str) -> object:
        return self._invoke_update(source=source)

    def _update(self) -> object:
        return self._invoke_update(channel="stable")

    def _invoke_update(self, **arguments: str) -> object:
        for record, operation in self._kernel.lets(OperationLet):
            if record.qualname == UPDATE_MODULE and operation.name == UPDATE_OPERATION:
                with self._kernel.resolve(CurrentUser).local("daemon"):
                    return self._kernel.invoke(operation, **arguments)
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
                message=plan.message,
            )

    def postpone(self) -> AutoUpdateStatus:
        """Put the update off by an hour, for every window."""
        with self._lock:
            postpone(self.plan, self._clock())
            self._save()
        return self.status()

    def dismiss(self) -> AutoUpdateStatus:
        """The message was read, in one window for all of them."""
        with self._lock:
            self.plan.message = None
            self._save()
        return self.status()

    # State file ------------------------------------------------------------------------------------------------------

    def _between(self, low: timedelta, high: timedelta) -> timedelta:
        return low + (high - low) * self._random.random()

    def _load(self) -> Plan:
        try:
            return Plan.from_json(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return Plan()

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


def counts_as_work(method: str, path: str) -> bool:
    """Whether a request to the web head's API changes state and so holds off an update while it runs."""
    if method in {"GET", "HEAD", "OPTIONS"}:
        return False
    return not path.startswith(f"/api{AUTO_UPDATE_PATH}")
