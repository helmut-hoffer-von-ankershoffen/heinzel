"""The update service's base: its seams, the mutation scope, the generation checks and the interpreter."""

import logging
import subprocess
import sys
import threading
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from heinzel import HeinzelError, Progress
from heinzel._adapter_system import SYSTEM_CLOCK, SYSTEM_RUNNER
from heinzel.kernel import (
    CorePin,
    DaemonAttestation,
    DaemonHandoverPlan,
    DaemonHandoverRequest,
    DaemonInstallationLifecycle,
    DaemonMutationScope,
    Installation,
    WebHeadRefresh,
    subprocess_environment,
)
from heinzel.kernel._desktop_app import DesktopApp, installed_desktop_app
from heinzel.kernel._logging import log_event
from heinzel.kernel.install._tool_python import PythonNeed, newer_python
from heinzel.modules.system._models import UpdateResult
from heinzel.modules.system._service import RELEASE_TIMEOUT
from heinzel.modules.system.updater._adapter_state import FileInstallationState
from heinzel.modules.system.updater._plugin_releases import PinnedPlugins
from heinzel.modules.system.updater._update_rules import (
    DESKTOP_SECONDS,
    ORIGINALS,
    Before,
    log,
    parse_version,
    release_version,
)
from heinzel.ports import Clock, InstallationState, ProcessRunner, ReleaseSource

if TYPE_CHECKING:
    from heinzel.modules.system.updater._update_steps import Steps


UPDATE_LOCK_NOTICE = 1.0
"""Seconds an update waits for the installation's update lock before it says it waits for another update."""
UPDATE_LOCK_SECONDS = 600.0
"""Seconds an update waits for the update lock in all before it gives up with a clear error."""


class UpdateCore:
    """The update service's seams and the checks every mutation shares."""

    def __init__(  # noqa: PLR0913 - the installation, its boundaries, its heads and the seams tests replace
        self,
        installation: Installation,
        releases: ReleaseSource,
        *,
        lifecycle: DaemonInstallationLifecycle | None = None,
        require_lifecycle: bool = False,
        web: WebHeadRefresh | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        clocks: Clock = SYSTEM_CLOCK,
        runner: ProcessRunner = SYSTEM_RUNNER,
        state: InstallationState | None = None,
        home: Path | None = None,
        progress: Progress | None = None,
        pinned: Callable[[float], PinnedPlugins] | None = None,
        newer: Callable[[float], dict[str, str]] | None = None,
        desktop: Callable[..., tuple[int, str]] | None = None,
        need: PythonNeed | None = None,
        daemon: DaemonAttestation | None = None,
        running: Callable[[], bool] | None = None,
        found: Callable[[], DesktopApp | None] | None = None,
    ) -> None:
        """Create the service with an optional trusted lifecycle boundary and the heads it refreshes afterwards.

        `progress` hears the steps of `update` as they happen (`phase` events), so the app can show each one.
        `pinned` asks the releases of plugins pinned to an exact version or a GitHub commit and `newer` those of
        plugins that follow the index (both from the system module's `PluginReleases`); `desktop` runs the new hz's
        `hz app install` (`restart=True`: `--restart`) and returns its exit code and output; `daemon` reads the version
        the daemon runs, `running` whether the desktop app is open and `found` which app sits in Applications (with
        `home`, only that home's Applications folder is looked at). Tests replace all of them.
        `need` keeps what the last update found about a release that needs a newer Python, for the doctor.
        The world comes in through ports (`heinzel.ports`): `clocks` for time (or the single `clock` and `sleep`
        callables, which win), `runner` for the programs it starts, `state` for the state folder.
        """
        self._pinned_plugins = pinned
        self._need = need
        self._newer_plugins = newer
        self._desktop = desktop or self._app_install
        self._daemon = daemon
        self._running = running or self._app_open
        self._found = found or self._found_app
        self._restart_daemon = True
        """Whether the update in progress may restart the daemon and the web server (`--no-daemon` says not)."""

        self._progress = progress or Progress()
        self._steps: Steps | None = None
        self._installation = installation
        self._releases = releases
        self._lifecycle = lifecycle
        self._require_lifecycle = require_lifecycle
        self._web = web
        self._clock, self._sleep = clock or clocks.monotonic, sleep or clocks.sleep
        self._runner = runner
        self._state = state
        self._home = home
        self._handover: tuple[DaemonHandoverPlan, DaemonHandoverRequest] | None = None
        """What the daemon was before the last completed mutation and the handover it requested, for the heads."""

    @property
    def _updating(self) -> threading.Lock:
        """Held while `update` runs: one update at a time in this process (the lifecycle guards other processes)."""
        lock: threading.Lock = self.__dict__.setdefault("_updating_lock", threading.Lock())
        return lock

    @contextmanager
    def _across_processes(self) -> Generator[None]:
        """Hold the state folder's `update` lock: one update or automatic update at a time on this installation.

        After `UPDATE_LOCK_NOTICE` seconds it says that it waits for another update; after `UPDATE_LOCK_SECONDS` in
        all it gives up.

        Raises:
            HeinzelError: If another update still holds the installation after `UPDATE_LOCK_SECONDS`.
        """
        if self._state is None:
            yield
            return
        with ExitStack() as stack:
            try:
                stack.enter_context(self._state.lock("update", wait=UPDATE_LOCK_NOTICE))
            except TimeoutError:
                log_event(log, "update.lock.wait", outcome="waiting")
                try:
                    with self._progress.task("Waiting for another update…"):
                        stack.enter_context(self._state.lock("update", wait=UPDATE_LOCK_SECONDS - UPDATE_LOCK_NOTICE))
                except TimeoutError:
                    minutes = round(UPDATE_LOCK_SECONDS / 60)
                    msg = (
                        f"Another update has held this installation for {minutes} minutes, so this one didn't start."
                        " Wait for it to finish, then try again."
                    )
                    raise HeinzelError(msg) from None
            yield

    def _found_app(self) -> DesktopApp | None:
        """The app in Applications, whoever installed it; with a `home` (a test) only in that home's folder."""
        with suppress(OSError, RuntimeError):
            home = self._home or Path.home()
            return installed_desktop_app(home, None if self._home is None else home / "Applications")
        return None

    def _home_file(self, relative: Path) -> str | None:
        """A file under the home folder, read through the state port; with no port, under `home` (tests) or `~`."""
        state = self._state if self._state is not None else FileInstallationState(Path(), self._home)
        return state.home_file(relative.as_posix())

    def _start_step(self, id_: str) -> None:
        """Tell the running `update` that its step `id_` started; nothing outside one."""
        if self._steps is not None:
            self._steps.start(id_)

    def _mutation(self) -> AbstractContextManager[DaemonMutationScope | None]:
        # Taking the lifecycle (it may wait for a running tick or update) is the install's work, not the check's.
        self._start_step("install")
        if self._lifecycle is not None:
            return self._lifecycle.mutation(
                self._installation.repaired_startup_state_matches, self._installation.rollback_identity
            )
        if self._require_lifecycle:
            msg = "The daemon lifecycle boundary is unavailable; the installation was not changed."
            raise HeinzelError(msg)
        return nullcontext(None)

    def _require_running_version(self, current: str) -> None:
        try:
            installed = self._installation.installed_core_version()
            matched = Version(installed) == Version(current)
        except (HeinzelError, InvalidVersion) as exc:
            msg = "The installed core version cannot be verified; reload the application before updating."
            raise HeinzelError(msg) from exc
        if not matched:
            msg = (
                f"This process is running hz {current}, but the installation is hz {installed}; reload before updating."
            )
            raise HeinzelError(msg)

    def _automatic_generation(self, current: str) -> str | None:
        if self._lifecycle is None:
            self._require_running_version(current)
            return None
        before = self._installation.startup_fingerprint()
        self._require_running_version(current)
        if self._installation.startup_fingerprint() != before:
            msg = "The installation changed while checking its version; reload before updating."
            raise HeinzelError(msg)
        return before

    def _require_generation(self, current: str, initial: str | None) -> None:
        if initial is not None and self._installation.startup_fingerprint() != initial:
            msg = "The installation changed since this update was prepared; reload before updating."
            raise HeinzelError(msg)
        self._require_running_version(current)
        if initial is not None and self._installation.startup_fingerprint() != initial:
            msg = "The installation changed while checking its version; reload before updating."
            raise HeinzelError(msg)

    def _verify_automatic_generation(
        self, current: str, initial: str | None, scope: DaemonMutationScope | None
    ) -> None:
        try:
            self._require_generation(current, initial)
        except HeinzelError:
            if scope is not None:
                self._abort_or_leave_pending(scope)
            raise

    def _verify_selection(self, pin: CorePin, plugins: list[CorePin], scope: DaemonMutationScope | None) -> None:
        if self._installation.core_pin() != pin or self._installation.plugin_pins() != plugins:
            if scope is not None:
                self._abort_or_leave_pending(scope)
            msg = "The installation requirements changed before the update; review them and retry."
            raise HeinzelError(msg)
        try:
            names = [canonicalize_name(Requirement(plugin.requirement).name) for plugin in plugins]
        except InvalidRequirement as exc:
            if scope is not None:
                self._abort_or_leave_pending(scope)
            msg = "The installed plugin requirements are invalid; inspect them before updating."
            raise HeinzelError(msg) from exc
        if "heinzel" in names or len(set(names)) != len(names):
            if scope is not None:
                self._abort_or_leave_pending(scope)
            msg = "The installed tool requirements name the same distribution more than once; inspect them first."
            raise HeinzelError(msg)

    @staticmethod
    def _abort_or_leave_pending(scope: DaemonMutationScope) -> bool:
        try:
            scope.abort_unchanged()
        except (HeinzelError, OSError):
            return False
        return True

    def _finish_mutation(
        self,
        scope: DaemonMutationScope,
        plan: DaemonHandoverPlan,
        candidate: str,
        result: UpdateResult,
        *,
        before_handover: Callable[[], None] | None = None,
    ) -> UpdateResult:
        if scope.checkpoint_matched:
            self._installation.record_startup_state(expected=candidate)
        # Verification is in memory. Keep the rollback until this succeeds, then retire it before any durable
        # handover request or completion of the daemon mutation.
        scope.verified_complete(candidate)
        if before_handover is not None:
            before_handover()
        if not self._restart_daemon:
            scope.finish_without_handover()
            self._handover = (plan, DaemonHandoverRequest(requested=False, reason="--no-daemon left it running"))
            return result
        self._handover = (plan, scope.request(plan))
        return result

    def _state_folder(self) -> Path:
        """The state folder of the installation, for what has to be kept beside it.

        Raises:
            HeinzelError: If this service was given none.
        """
        if self._state is None:
            msg = "There is no state folder to copy the installation into; nothing was installed."
            raise HeinzelError(msg)
        return self._state.folder

    @staticmethod
    def _target_release(pin: CorePin, current: str, latest: str | None, target: str | None) -> str | None:
        """The release an install moves to, whose Python requirement decides the interpreter."""
        if target is not None:
            return release_version(target)
        if (
            pin.kind in {"default", "index"}
            and pin.git is None
            and latest is not None
            and Version(latest) > parse_version(current)
            and Requirement(pin.requirement).specifier.contains(latest)
        ):
            return latest
        return None

    def _fingerprints(self, *, include_content: bool = False) -> "Before":
        """The generation before an install and, for a portable rollback, what its content must match."""
        generation = self._installation.startup_fingerprint()
        if not include_content and not self._installation.changes_interpreter:
            return Before(generation)
        return Before(generation, self._installation.content_fingerprint(), self._installation.startup_state_matches())

    def needed_python(self, version: str | None) -> str | None:
        """The newer Python (`X.Y`) release `version` requires than this one runs on; None if this one will do.

        None too when the release source can't say: the update then stays on the tool's interpreter, as before.

        Raises:
            HeinzelError: If the release's `Requires-Python` is malformed or no newer Python satisfies it.
        """
        if version is None:
            return None
        try:
            requires = self._releases.requires_python(version, RELEASE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - isolation boundary: an unknown requirement keeps today's interpreter
            log_event(log, "update.python.unknown", level=logging.WARNING, version=version, error=str(exc))
            return None
        return newer_python(requires)

    @contextmanager
    def _interpreter_for(self, version: str | None) -> Generator[None]:
        """Install release `version` onto a uv-managed newer Python if it needs one, else onto the tool's own.

        Raises:
            HeinzelError: If the newer Python can't be obtained; nothing was changed then.
        """
        try:
            needed = self.needed_python(version)
        except HeinzelError as exc:
            if self._need is not None and version is not None:
                self._need.remember(version, "", held=str(exc))
            raise
        if needed is None:
            if self._need is not None and version is not None:
                self._need.forget()
            yield
            return
        entered = False
        token = ORIGINALS.set({})  # what this update captured; none of it outlives it
        try:
            with self._installation.newer_interpreter(needed, subprocess_environment()) as path:
                entered = True
                log_event(log, "update.python.managed", version=version, python=needed, interpreter=path)
                if self._need is not None and version is not None:
                    self._need.remember(version, needed)
                yield
        except HeinzelError as exc:
            if not entered and self._need is not None and version is not None:
                self._need.remember(version, needed, held=str(exc))
            raise
        finally:
            ORIGINALS.reset(token)

    def _app_open(self) -> bool:
        """Whether the managed desktop app is open now: one of its processes runs from inside its folder."""
        try:
            listing = self._runner.run(["/bin/ps", "-ww", "-axo", "comm="], timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return "/Heinzelmaennchen.app/" in listing

    def _app_install(self, *, restart: bool = False) -> tuple[int, str]:
        """Run `hz app install` in a fresh interpreter, which is the new hz; its exit code and what it printed."""
        flags = ["-I", "-B"] if sys.dont_write_bytecode else ["-I"]
        result = self._runner.run(
            [sys.executable, *flags, "-m", "heinzel", "app", "install", *(["--restart"] if restart else [])],
            timeout=DESKTOP_SECONDS,
            env=subprocess_environment() | {"HZ_UPDATE_CHECK": "false"},
        )
        return result.returncode, result.stderr.strip() or result.stdout.strip()

    def _original_generation(self) -> str | None:
        try:
            generation = self._installation.startup_fingerprint()
            if self._installation.changes_interpreter:  # uv will recreate the folder: remember its content too
                before = self._fingerprints()
                ORIGINALS.set({**ORIGINALS.get(), generation: before})
        except HeinzelError:
            return None
        return generation

    def _same_generation(self, expected: str | None) -> bool:
        """Whether the tool is generation `expected` again; for a move between interpreters, its files in a new folder.

        Then a continuity record that matched before is renewed for the new folder, as a put-back does.
        """
        try:
            current = self._installation.startup_fingerprint() if expected is not None else None
        except HeinzelError:
            return False
        if current is None or current == expected:
            return current is not None
        before = ORIGINALS.get().get(expected or "")
        try:
            if before is None or before.content is None or self._installation.content_fingerprint() != before.content:
                return False
            if before.matched:
                self._installation.record_startup_state(expected=current)
        except (HeinzelError, OSError):
            return False
        return True

    def _latest(self, current: str) -> str:
        """The highest stable release: never a pre-release, even when one is installed (ADR 0116 amends ADR 0006).

        Edge builds are GitHub pre-releases (ADR 0114); the stable channel must never move onto one by itself.
        """
        del current
        return self._releases.highest_version(RELEASE_TIMEOUT, prereleases=False)

    def _version(self) -> str:
        """The version a fresh `hz` reports: the installation as it is now, not as this process loaded it.

        Raises:
            HeinzelError: If `hz` doesn't start.
        """
        return self._installation.hz("--version").splitlines()[0].removeprefix("hz ").strip()
