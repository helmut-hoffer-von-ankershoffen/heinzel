"""Service of the daemon module: install the OS unit, and tick: run the jobs that are due and write receipts.

The OS supervises and the daemon ticks (ADR 0020): launchd or systemd starts `hz system daemon tick` every few
minutes, a short-lived process that survives `hz system update`, crashes and sleep by construction.

The service reads only user-scope configuration (site and user files and the environment, not a project's
`hz.toml`), so the unit, the receipts and the logs are the same wherever `hz` runs, and a repository can't move them.
"""

import json
import logging
import os
import re
import secrets
import shutil
import stat
import sys
import time
import tomllib
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from pydantic_core import to_jsonable_python

from heinzel import (
    CatchUp,
    Check,
    Config,
    HeinzelError,
    JobInactive,
    JobLet,
    Kernel,
    Notifications,
    Progress,
    Settings,
    Status,
    __version__,
)
from heinzel._phases import Phases
from heinzel.kernel import (
    KERNEL_SECTION,
    DaemonHandoverObservation,
    DaemonHandoverPlan,
    DaemonLifecycleWait,
    DaemonMutationScope,
    Installation,
    ProcessOutcome,
    host_maintenance_gate,
    private_directory,
    private_lock,
    private_read,
    private_write,
    tick_interval,
)
from heinzel.kernel._logging import log_event
from heinzel.modules.system.daemon import _process_birth
from heinzel.modules.system.daemon import _schedule as schedule
from heinzel.modules.system.daemon._abandoned_mutation import host_boot, mutation_record
from heinzel.modules.system.daemon._handover_files import (
    handover_absolute_path as _handover_absolute_path,
)
from heinzel.modules.system.daemon._handover_files import (
    invalid_handover as _invalid_handover,
)
from heinzel.modules.system.daemon._handover_files import (
    safely_absent as _safely_absent,
)
from heinzel.modules.system.daemon._handover_files import (
    verified_handover_file as _open_verified_handover_file,
)
from heinzel.modules.system.daemon._handover_request import (
    MAX_BOOT_IDENTITY,
    MAX_HANDOVER_CONTRIBUTIONS,
    MAX_HANDOVER_FILE_BYTES,
    MAX_OBSERVATION_BYTES,
    STALE_TICKS,
)
from heinzel.modules.system.daemon._handover_request import (
    HandoverRequests as _HandoverRequest,
)
from heinzel.modules.system.daemon._handover_request import (
    heartbeat_covers as _heartbeat_covers,
)
from heinzel.modules.system.daemon._ledger import Ledger
from heinzel.modules.system.daemon._manager_commands import (
    linux_user_manager_identity as _linux_user_manager_identity,
)
from heinzel.modules.system.daemon._manager_commands import (
    quiescence_command as _quiescence_command,
)
from heinzel.modules.system.daemon._models import (
    DaemonSettings,
    DaemonStatus,
    Disposition,
    Installed,
    JobInfo,
    JobRun,
    LogLine,
    Restarted,
    TickReport,
    Toggled,
    Uninstalled,
    Wake,
)
from heinzel.modules.system.daemon._mutation_scope import (
    MutationAuthority as _MutationAuthority,
)
from heinzel.modules.system.daemon._mutation_scope import (
    MutationScope as _MutationScope,
)
from heinzel.modules.system.daemon._ownership import (
    ManagerState,
    profile_digest,
    profile_environment,
)
from heinzel.modules.system.daemon._quiescence import QuiescenceChecks as _Quiescence
from heinzel.modules.system.daemon._removal import OfflineRemoval as _Removal
from heinzel.modules.system.daemon._runtime import (
    Outcome,
    Runner,
    TickLog,
    TimeLimit,
    describe,
    fsync_dir,
    held,
    lock,
    lock_until_finished,
    run,
    tail,
    within,
    write_private,
)
from heinzel.modules.system.daemon._units import (
    LABEL,
    LOGIN_ITEM_HINT,
    NOTIFIER_APP,
    PLATFORMS,
    SYSTEMD_NAME,
    check_interval,
    launchd_plist,
    plist_path,
    systemd_paths,
    systemd_service,
    systemd_timer,
)

if TYPE_CHECKING:
    import threading

logger = logging.getLogger(__name__)
DEFAULT_INTERVAL = 5
"""Minutes between ticks unless `install --interval` says otherwise."""
SESSION_STEP = "Checking this is your Mac's desktop session"
NOTIFIER_STEP = "Setting up desktop notifications"
LOAD_FIX = "run `hz system daemon status` in a terminal for what the service manager says."
MODULE_SECTION = "system.daemon"
LIFECYCLE_POLL_SECONDS = 2.0
UUID_TEXT_LENGTH = 36
LINUX_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
MACOS_BOOT_ID_COMMAND = "/usr/sbin/sysctl"


@dataclass(frozen=True, slots=True)
class Job:
    """A registered job under its id, `<module>:<job>`."""

    id: str
    let: JobLet


@dataclass(frozen=True, slots=True)
class Due:
    """A slot to handle in this tick: run it, or record why it is skipped."""

    slot: datetime
    wake: Wake
    skip: str | None = None


def notifier_in_applications() -> bool:
    """Whether Heinzel Notifier.app is in ~/Applications."""
    return (Path.home() / "Applications" / NOTIFIER_APP).exists()


def _platform_boot_identity(platform: str, runner: Runner, linux_path: Path = LINUX_BOOT_ID) -> str | None:
    """Read an OS-provided same-boot UUID; absent, malformed and unsupported cases remain unknown.

    Darwin: XNU's readonly kern.bootsessionuuid; Linux: kernel random boot_id sysctl. This is only a same-boot marker.
    """
    try:
        if platform == "darwin":
            result = runner([MACOS_BOOT_ID_COMMAND, "-n", "kern.bootsessionuuid"])
            if result.returncode != 0:
                return None
            raw = result.stdout
        elif platform == "linux":
            with linux_path.open("rb") as source:
                raw = source.read(MAX_BOOT_IDENTITY + 1).decode("ascii")
        else:
            return None
        text = raw.strip()
        if len(raw) > MAX_BOOT_IDENTITY or len(text) != UUID_TEXT_LENGTH:
            return None
        parsed = UUID(text)
        return str(parsed) if text.lower() == str(parsed) else None
    except (OSError, ValueError, UnicodeError, HeinzelError):
        return None


def _failed_once_after_success(ledger: Ledger, job: str) -> bool:
    """Whether the job's newest run failed and the run before it worked; skipped slots don't count as runs."""
    try:
        ran = [receipt for receipt in ledger.chain(job) if receipt.disposition is not Disposition.SKIPPED]
    except HeinzelError:
        return False
    return len(ran) > 1 and ran[-1].disposition is Disposition.FAILED and ran[-2].disposition is Disposition.RENDERED


_HOLDERS = {"tick": "the background service's current run of its jobs", "other": "an update or another Heinzel change"}
"""Plain words for who holds the lifecycle lock, by the stable token the log records."""


_SECONDS_SHOWN_BELOW = 90


def _budget(seconds: float) -> str:
    """A wait budget in plain words: seconds below 90, else whole minutes."""
    return f"{seconds:g} s" if seconds < _SECONDS_SHOWN_BELOW else f"{round(seconds / 60)} min"


class DaemonService(_Quiescence, _HandoverRequest, _Removal):
    """Installs, inspects and pauses the daemon, and runs ticks."""

    def __init__(  # noqa: PLR0913, PLR0917 - the seams tests replace, all defaulted
        self,
        kernel: Kernel,
        settings: Settings,
        daemon: DaemonSettings,
        runner: Runner = run,
        platform: str = sys.platform,
        uid: Callable[[], int] = os.getuid,
        clock: Callable[[], datetime] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        time_limit: TimeLimit = within,
        zone: tzinfo | None = None,
        notifier_installed: Callable[[], bool] = notifier_in_applications,
        continuity_fingerprint: Callable[[], str | None] | None = None,
        boot_identity: Callable[[], str | None] | None = None,
        host_boot_evidence: Callable[[], tuple[str | None, int | None]] | None = None,
        birth_fence: Callable[[], dict[str, int] | None] | None = None,
        process_birth: Callable[[], dict[str, int] | None] | None = None,
        quiescence_probe: Callable[[list[str]], ProcessOutcome] | None = None,
        quiescence_installation: Installation | None = None,
        linux_manager_identity: Callable[[], tuple[int, ...] | None] | None = None,
        desktop_session: Callable[[], str | None] | None = None,
        install_notifier: Callable[[], str | None] | None = None,
        profile_config: Config | None = None,
    ) -> None:
        """Create the service; `for_user` builds it for the kernel, tests pass the rest.

        Args:
            kernel: Finds the jobs and injects their parameters.
            settings: Kernel settings: where receipts, state and logs live.
            daemon: Where the units go.
            runner: Runs launchctl, systemctl and uv.
            platform: `sys.platform`, to pick launchd or systemd.
            uid: The user id launchd's GUI domain is named after.
            clock: The current time, timezone-aware; now in `zone` by default.
            which: Finds executables.
            time_limit: Runs a job with a timeout.
            zone: The time zone cron expressions mean; the user's by default.
            notifier_installed: Whether Heinzel Notifier.app is in ~/Applications, for the Login Items hint.
            continuity_fingerprint: Local installed-file observation, or None when unavailable.
            boot_identity: Same-boot identity, or None when unavailable.
            host_boot_evidence: The current boot's identity and wall-clock start in nanoseconds, each None when
                unknown, read without a subprocess; it binds and proves the end of a mutation marker's owner.
            birth_fence: Same-boot process-birth ordering fence, or None when unavailable.
            process_birth: Current process's operating-system birth evidence, or None when unavailable.
            quiescence_probe: Bounded read-only service-manager query, replaceable in tests.
            quiescence_installation: Selected tool identity for verifying an existing stopped unit.
            linux_manager_identity: Physical private manager socket identity, replaceable in tests.
            desktop_session: The macOS launchd session this process runs in (`Aqua` on the desktop), or None when
                unknown; replaceable in tests so none touches the real launchd.
            profile_config: Selected user/explicit configuration routing, without project settings or credentials.
            install_notifier: Installs the native notifier as a step of `install` on macOS and returns what went wrong
                in plain words, or None when it worked; None (the default) leaves that step out.
        """
        self._desktop_session = desktop_session or self._launchd_session
        self._install_notifier = install_notifier
        self._notifier_installed = notifier_installed
        self._quiescence_probe = quiescence_probe or _quiescence_command
        self._quiescence_installation = quiescence_installation
        self._quiescence_problem = "The installation or service-manager evidence could not be verified."
        self._quiescence_advice: str | None = None
        self._linux_manager_identity = linux_manager_identity or _linux_user_manager_identity
        self._continuity_fingerprint = continuity_fingerprint
        self._boot_identity = boot_identity or (lambda: _platform_boot_identity(self._platform, self._runner))
        self._host_boot = host_boot_evidence or (lambda: host_boot(self._platform))
        self._birth_fence = birth_fence or (lambda: _process_birth.request_fence(self._platform))
        self._process_birth = process_birth or (lambda: _process_birth.current_birth(self._platform))
        self._kernel = kernel
        self._runner = runner
        self._platform = platform
        self._uid = uid
        self._zone = zone or schedule.local_zone()
        self._clock = clock or (lambda: datetime.now(self._zone))
        self._which = which
        self._time_limit = time_limit
        self._abandoned: dict[str, threading.Thread] = {}
        self.lifecycle_wait_seconds: float | None = 0.0
        """How long a lifecycle writer waits for another holder of the lifecycle lock; None reads the head's bound
        `DaemonLifecycleWait` at each wait (`for_user`), so no cached service keeps another head's budget."""
        self.lifecycle_poll_seconds = LIFECYCLE_POLL_SECONDS
        self.compile_error: str | None = None
        """Why compiling jobs failed the last time they were listed; None when it worked."""
        self._profile = profile_environment(settings, daemon, profile_config or Config.empty())
        self.unit_dir = daemon.unit_dir
        self.receipts_dir = settings.data_dir / "receipts"
        self.state_dir = settings.state_dir / "daemon"
        self.log_path = settings.log_dir / "daemon.jsonl"

    @classmethod
    def for_user(cls, kernel: Kernel) -> "DaemonService":
        """The service with user-scope settings: a project's `hz.toml` can't move the unit, receipts or logs."""
        config = Config.load(Path.home())  # as if run from the home directory, like the uninstall skill does
        installation = kernel.resolve(Installation)
        service = cls(
            kernel,
            config.settings(Settings, KERNEL_SECTION),
            config.settings(DaemonSettings, MODULE_SECTION),
            continuity_fingerprint=installation.startup_fingerprint,
            quiescence_installation=installation,
            profile_config=config,
        )
        service.lifecycle_wait_seconds = None  # the head's budget, read at each wait: a later binding counts
        return service

    # Installing ------------------------------------------------------------------------------------------------------

    @contextmanager
    def _lifecycle_writer(self) -> Generator[None]:
        with ExitStack() as stack:
            try:
                stack.enter_context(private_lock(_handover_absolute_path(self.state_dir / "lifecycle.lock")))
            except HeinzelError as exc:
                msg = "The daemon lifecycle is changing or its writer cannot be verified; inspect it before retrying."
                raise HeinzelError(msg) from exc
            yield

    def _lifecycle_holder(self) -> str:
        """`tick` while a tick runs its jobs, else `other` (an update or another change); from read-only probes."""
        return "tick" if held(self.state_dir / "tick.lock") else "other"

    def _wait_budget(self) -> float:
        """Seconds a lifecycle writer waits now: the fixed budget, or the head's bound one, or none."""
        if self.lifecycle_wait_seconds is not None:
            return self.lifecycle_wait_seconds
        if self._kernel.provides(DaemonLifecycleWait):
            return self._kernel.resolve(DaemonLifecycleWait).seconds
        return 0.0

    def await_lifecycle(self) -> bool:
        """Wait, up to `lifecycle_wait_seconds`, until no other process holds the lifecycle lock; never takes it.

        A tick holds the lock while its jobs run (after a laptop wakes these can take minutes), and an update holds it
        while it installs. Only a held lock is waited for: a pending marker with a free lock is left for recovery.
        Returns whether the lock is free now; the caller still acquires it without waiting, as before.
        """
        path = self.state_dir / "lifecycle.lock"
        if not held(path):
            return True
        budget = self._wait_budget()
        if budget <= 0:
            return False
        holder = self._lifecycle_holder()
        log_event(logger, "daemon.lifecycle.wait", phase="start", holder=holder, budget_seconds=budget)
        started = time.monotonic()
        deadline = started + budget
        with self._kernel.resolve(Progress).task(f"Waiting for {_HOLDERS[holder]} to finish (up to {_budget(budget)})"):
            while held(path) and time.monotonic() < deadline:
                time.sleep(min(self.lifecycle_poll_seconds, max(0.0, deadline - time.monotonic())))
        free = not held(path)
        log_event(
            logger,
            "daemon.lifecycle.wait",
            phase="outcome",
            holder=holder,
            outcome="free" if free else "busy",
            waited_seconds=round(time.monotonic() - started, 1),
        )
        return free

    def _lifecycle_refusal(self, *, busy: bool) -> str:
        """Why a lifecycle writer can't start, in plain words, with what to do; `busy` when the lock was held."""
        if busy:
            budget = self._wait_budget()
            waited = f" after waiting {_budget(budget)}" if budget > 0 else ""
            return (
                f"The daemon lifecycle is changing: {_HOLDERS[self._lifecycle_holder()]} is still running{waited}. "
                "Nothing was changed; try again in a minute or two."
            )
        return (
            "The daemon lifecycle requires recovery: an earlier update or service change did not finish. "
            "Nothing was changed; `hz system daemon status` and `hz system doctor` show the service."
        )

    def install(self, interval: timedelta, *, dry_run: bool = False, notifier: bool = True) -> Installed:
        """Install under the lifecycle writer lock, or report a concurrent change.

        Raises:
            HeinzelError: If another lifecycle writer is active or installation fails.
        """
        self._minutes(interval)
        self._unit_paths()
        if dry_run:
            return self._install_locked(interval, dry_run=True, notifier=notifier)
        if not self.await_lifecycle():
            raise HeinzelError(self._lifecycle_refusal(busy=True))
        self._recover_abandoned_mutation()
        if self._handover_pending():
            msg = "The daemon lifecycle requires recovery; inspect it before installing."
            raise HeinzelError(msg)
        executable = self._shim(dry_run=False)
        self._capture_install_files()
        self.owned_manager_state(executable.parent)
        with self._lifecycle_writer(), private_lock(_handover_absolute_path(self.state_dir / "tick.lock")):
            if self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery; inspect it before installing."
                raise HeinzelError(msg)
            return self._install_locked(interval, notifier=notifier)

    def _capture_install_files(self) -> dict[Path, bytes | None]:
        result: dict[Path, bytes | None] = {}
        for path in (*self._unit_paths(), self.state_dir / "install.json"):
            if _safely_absent(path):
                result[path] = None
                continue
            with _open_verified_handover_file(path) as handle:
                info = os.fstat(handle.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o022
                    or info.st_size > MAX_HANDOVER_FILE_BYTES
                    or info.st_nlink != 1
                ):
                    _invalid_handover()
                result[path] = handle.read(MAX_HANDOVER_FILE_BYTES + 1)
        return result

    def _install_locked(
        self, interval: timedelta, *, dry_run: bool = False, notifier: bool = True, operation: str = "system.daemon"
    ) -> Installed:
        """Replace only an exact owned profile, retaining existing activation and truthful recovery.

        Raises:
            HeinzelError: If identity is foreign, changes, or loading/recovery cannot be verified.
            OSError: If the private files cannot be observed or written.
        """
        minutes = self._minutes(interval)
        executable = self._shim(dry_run=dry_run)
        files = self._unit_files(executable, minutes)
        if dry_run:
            return Installed(
                platform=self._platform,
                executable=executable,
                interval_minutes=minutes,
                files=files,
                commands=[],
                dry_run=True,
            )
        original = self._capture_install_files()
        log_directory = _handover_absolute_path(self.log_path.parent)
        with suppress(FileNotFoundError):
            private_directory(log_directory, create=False)
        original_request = self._validated_install_history()
        previous = self.owned_manager_state(executable.parent)
        if previous.pid is not None:
            msg = "An owned daemon tick is still running; nothing was replaced. Inspect status after it finishes."
            raise HeinzelError(msg)
        existing = any(content is not None for content in original.values())
        wanted = previous if existing else ManagerState(registered=True, active=True, enabled=True)
        commands = self._install_commands(previous, wanted)
        darwin = self._platform == "darwin"
        phases = Phases(
            self._kernel.resolve(Progress),
            [
                *([("session", SESSION_STEP)] if darwin else []),
                ("unit", "Writing the service definition"),
                ("load", f"Loading it into {'launchd' if darwin else 'systemd'}"),
                ("verify", "Checking that it is loaded"),
                *([("notifier", NOTIFIER_STEP)] if darwin and notifier and self._install_notifier else []),
            ],
            operation=operation,
        )
        self._require_desktop_session(phases)
        if self._capture_install_files() != original or self.owned_manager_state(executable.parent) != previous:
            msg = "The daemon changed before installation; nothing was replaced."
            raise HeinzelError(msg)
        token = secrets.token_hex(32)
        pending = _handover_absolute_path(self.state_dir / "handover-pending.json")
        private_write(pending, json.dumps({"token": token}, indent=2) + "\n")
        candidate = {
            **files,
            self.state_dir / "install.json": json.dumps(
                {
                    "interval_minutes": minutes,
                    "executable": str(executable),
                    "platform": self._platform,
                    "installed_at": self._clock().isoformat(),
                    "version": __version__,
                    "profile": profile_digest(self._profile),
                },
                indent=2,
            )
            + "\n",
        }
        try:
            with phases.step("unit", "Writing the service definition"):
                private_directory(log_directory)
                self._replace_install_files(original, candidate)
            self._load_and_verify(phases, commands, executable, wanted)
        except (HeinzelError, OSError) as exc:
            restored = self._recover_install(original, executable, previous, original_request)
            if restored:
                pending.unlink()
            detail = (
                "The previous files and activation were verified restored."
                if restored
                else "Recovery is uncertain; its pending gate was retained. Inspect the user service before retrying."
            )
            msg = f"The daemon installation did not complete. {detail}"
            raise HeinzelError(msg) from exc
        if not self._mutation_token_matches(token, "handover-pending.json"):
            msg = "The daemon installation writer changed; explicit recovery is required."
            raise HeinzelError(msg)
        self._finish_install_history(original_request)
        pending.unlink()
        fsync_dir(pending.parent)
        if notifier:
            self._set_up_notifier(phases)
        hint = LOGIN_ITEM_HINT if darwin and not self._notifier_installed() else None
        return Installed(
            platform=self._platform,
            executable=executable,
            interval_minutes=minutes,
            files=files,
            commands=commands,
            dry_run=False,
            hint=hint,
        )

    def _validated_install_history(self) -> str | None:
        original = self._install_handover_history()
        if original is not None and not self._handover_marker_valid(self._read_state("install.json")):
            msg = "The previous handover cannot be verified; explicit recovery is required before reinstalling."
            raise HeinzelError(msg)
        return original

    def _finish_install_history(self, original: str | None) -> None:
        if self._install_handover_history() != original:
            msg = "The previous handover changed during reinstall; its pending gate was retained."
            raise HeinzelError(msg)
        if original is not None:
            (self.state_dir / "handover-request.json").unlink()
            fsync_dir(self.state_dir)

    def _install_handover_history(self) -> str | None:
        try:
            return private_read(_handover_absolute_path(self.state_dir / "handover-request.json"))
        except FileNotFoundError:
            return None

    def _replace_install_files(self, original: dict[Path, bytes | None], selected: dict[Path, str]) -> None:
        expected = dict(original)
        for path, content in selected.items():
            if self._capture_install_files() != expected:
                msg = "A daemon definition changed before replacement; explicit recovery is required."
                raise HeinzelError(msg)
            if path.name == "install.json":
                private_write(_handover_absolute_path(path), content)
            else:
                write_private(path, content, shared_dir=True)
            expected[path] = content.encode()

    def _install_commands(self, previous: ManagerState, wanted: ManagerState) -> list[list[str]]:
        if self._platform == "darwin":
            return [
                *([["/bin/launchctl", "bootout", f"gui/{self._uid()}/{LABEL}"]] if previous.registered else []),
                *(
                    [["/bin/launchctl", "bootstrap", f"gui/{self._uid()}", str(plist_path(self.unit_dir))]]
                    if wanted.registered
                    else []
                ),
            ]
        timer = f"{SYSTEMD_NAME}.timer"
        return [
            ["/usr/bin/systemctl", "--user", "daemon-reload"],
            ["/usr/bin/systemctl", "--user", "enable" if wanted.enabled else "disable", timer],
            ["/usr/bin/systemctl", "--user", "restart" if wanted.active else "stop", timer],
        ]

    def _load_and_verify(
        self, phases: Phases, commands: list[list[str]], executable: Path, wanted: ManagerState
    ) -> None:
        """Require the actual manager's selected argv/profile and activation after each owned replacement.

        Raises:
            HeinzelError: If the manager refuses, changes identity, or does not expose the expected registration.
        """
        with phases.step("load", "Loading the owned service definition"):
            for command in commands:
                self.owned_manager_state(executable.parent)
                result = self.owned_manager_command(command)
                if result.returncode != 0:
                    msg = "The verified user manager refused the daemon change."
                    raise HeinzelError(msg)
        with phases.step("verify", "Checking the owned loaded identity"):
            observed = self.owned_manager_state(executable.parent)
            if (observed.registered, observed.active, observed.enabled) != (
                wanted.registered,
                wanted.active,
                wanted.enabled,
            ):
                msg = "The manager did not confirm the selected daemon registration and profile."
                raise HeinzelError(msg)

    def _recover_install(
        self,
        original: dict[Path, bytes | None],
        executable: Path,
        previous: ManagerState,
        original_request: str | None,
    ) -> bool:
        try:
            current = self.owned_manager_state(executable.parent)
            if current.pid is not None:
                return False
            if self._platform == "darwin" and current.registered:
                stopped = self.owned_manager_command(["/bin/launchctl", "bootout", f"gui/{self._uid()}/{LABEL}"])
                if stopped.returncode != 0 or self.owned_manager_state(executable.parent).registered:
                    return False
            restoring = self._capture_install_files()
            self._restore_units(original, expected=restoring)
            for command in self._install_commands(
                ManagerState(registered=False, active=False, enabled=False), previous
            ):
                result = self.owned_manager_command(command)
                if result.returncode != 0:
                    return False
            return (
                self._capture_install_files() == original
                and self.owned_manager_state(executable.parent) == previous
                and self._install_handover_history() == original_request
            )
        except (OSError, ValueError, HeinzelError):
            return False

    def _launchd_session(self) -> str | None:
        """The launchd session this process runs in (`Aqua` on the desktop), or None when launchctl doesn't say."""
        try:
            result = self._quiescence_probe(["/bin/launchctl", "managername"])
        except (HeinzelError, OSError):
            return None
        return result.stdout.strip() or None if result.returncode == 0 and not result.truncated else None

    def _require_desktop_session(self, phases: Phases) -> None:
        """A macOS login service can only be registered from the person's desktop session.

        Raises:
            HeinzelError: If launchd puts this process in another session (SSH, a system service), saying so.
        """
        if self._platform != "darwin":
            return
        with phases.step("session", SESSION_STEP):
            name = self._desktop_session()
            if name != "Aqua":
                where = f"launchd calls it {name!r}" if name else "launchd doesn't say which it is"
                msg = (
                    "macOS only lets a login service be registered from your Mac's desktop session, and this hz "
                    f"does not run in it ({where}), for example because it was started over SSH or as a system "
                    "service. Nothing was changed. Install it where you are logged in at the Mac itself."
                )
                raise HeinzelError(msg)

    def _restore_units(self, before: dict[Path, bytes | None], *, expected: dict[Path, bytes | None]) -> None:
        """Restore captured bytes only while every currently observed entry remains unchanged.

        Raises:
            HeinzelError: If another writer changed an entry before recovery.
        """
        for path, content in before.items():
            if self._capture_install_files() != expected:
                msg = "The daemon files changed during recovery; no further files were replaced."
                raise HeinzelError(msg)
            if content is None:
                if expected[path] is not None:
                    path.unlink(missing_ok=True)
                    fsync_dir(path.parent)
            elif path.name == "install.json":
                private_write(_handover_absolute_path(path), content.decode())
            else:
                write_private(path, content.decode(), shared_dir=True)
            expected[path] = content

    def _set_up_notifier(self, phases: Phases) -> None:
        """Install the native notifier as the last step on macOS; a problem there leaves the daemon installed."""
        if self._platform != "darwin" or self._install_notifier is None:
            return
        label = NOTIFIER_STEP
        try:
            with phases.step("notifier", label):
                problem = self._install_notifier()
        except HeinzelError as exc:
            problem = str(exc)
        if problem:
            phases.note("notifier", label, "warn", problem)

    def restart(self) -> Restarted:
        """Ask the service manager to reload the installed daemon with the interval it has.

        The result says which version the unit was asked to use. `status` remains unknown until a tick records the
        version that actually ran.

        Raises:
            HeinzelError: If loading fails.
        """
        if not self.await_lifecycle():
            raise HeinzelError(self._lifecycle_refusal(busy=True))
        self._recover_abandoned_mutation()
        if self._handover_pending():
            msg = "The daemon lifecycle requires recovery; inspect it before restarting."
            raise HeinzelError(msg)
        with self._lifecycle_writer(), private_lock(_handover_absolute_path(self.state_dir / "tick.lock")):
            if self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery; inspect it before restarting."
                raise HeinzelError(msg)
            recorded = self._read_state("install.json")
            installed = self._platform in PLATFORMS and all(path.exists() for path in self._unit_paths())
            if not installed or "interval_minutes" not in recorded:
                return Restarted(restarted=False, version=__version__, interval_minutes=None)
            minutes = int(recorded["interval_minutes"])
            self._install_locked(timedelta(minutes=minutes), operation="system.daemon.restart")
            return Restarted(restarted=True, version=__version__, interval_minutes=minutes)

    def capture_handover(self) -> DaemonHandoverPlan:
        """Observe pre-change eligibility without installing, loading or starting a daemon.

        Raises:
            HeinzelError: If an existing service's files cannot be safely inspected.
        """
        if self._platform not in PLATFORMS or not all(path.exists() for path in self._unit_paths()) or self.paused:
            return DaemonHandoverPlan(active=False, platform=self._platform, unit_signature=None)
        signature = self._handover_signature()
        binary = self._shim(dry_run=False).parent
        state = self.owned_manager_state(binary)
        return DaemonHandoverPlan(state.active, self._platform, signature)

    @contextmanager
    def admission_guard(self) -> Generator[None]:
        """Hold lifecycle authority through a caller's durable schedule admission.

        Acquire this before any session writer; lifecycle writers never acquire the session writer.

        Yields:
            Control while no cooperating lifecycle change can begin.

        Raises:
            HeinzelError: If lifecycle state is changing or requires recovery.
        """
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            if not acquired or self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery."
                raise HeinzelError(msg)
            yield

    @contextmanager
    def mutation_guard(
        self, checkpoint: Callable[[], bool], rollback_identity: Callable[[], str]
    ) -> Generator[DaemonMutationScope]:
        """Hold lifecycle authority over one verified installation mutation.

        Yields:
            A single-use scope for capture, checkpoint verification and handover.

        Raises:
            HeinzelError: If another mutation or an unresolved transaction exists.
        """
        self.await_lifecycle()
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            if not acquired or not self._clear_after_recovery_locked():
                raise HeinzelError(self._lifecycle_refusal(busy=not acquired))
            fingerprint = self._continuity_fingerprint
            before = fingerprint() if fingerprint is not None else None
            had_checkpoint = checkpoint()
            original_identity = rollback_identity()
            if (
                not isinstance(before, str)
                or not re.fullmatch(r"[a-f0-9]{64}", before)
                or not re.fullmatch(r"[a-f0-9]{64}", original_identity)
                or fingerprint is None
                or fingerprint() != before
            ):
                msg = "The existing installation checkpoint cannot be verified; explicit recovery is required."
                raise HeinzelError(msg)
            token = secrets.token_hex(32)
            self._write_state(
                "mutation-pending.json", mutation_record(token, os.getpid(), self._host_boot_evidence()[0])
            )
            authority = _MutationAuthority(
                self.state_dir / "mutation-pending.json",
                self._mutation_token_matches,
                self.capture_handover,
                self._request_handover_locked,
                self._continuity_fingerprint,
                checkpoint,
                had_checkpoint,
                before,
                original_identity,
                rollback_identity,
            )
            scope = _MutationScope(token, authority)
            try:
                yield scope
                scope.commit()
            finally:
                scope.active = False

    def uninstall(self) -> Uninstalled:
        """Uninstall under the lifecycle writer lock.

        Raises:
            HeinzelError: If another lifecycle writer is active or uninstall fails.
        """
        if self._handover_pending():
            msg = "The daemon lifecycle requires recovery; inspect it before uninstalling."
            raise HeinzelError(msg)
        paths = (*self._unit_paths(), self.state_dir / "install.json")
        if all(_safely_absent(path) for path in paths):
            return Uninstalled(removed=[], commands=[])
        executable = self._shim(dry_run=False)
        self.owned_manager_state(executable.parent)
        with (
            self._lifecycle_writer(),
            private_lock(_handover_absolute_path(self.state_dir / "tick.lock")),
        ):
            if self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery; inspect it before uninstalling."
                raise HeinzelError(msg)
            return self._uninstall_locked()

    def _uninstall_locked(self) -> Uninstalled:
        """Stop only an exact owned profile and remove its unchanged definitions; pause/data remain.

        Raises:
            HeinzelError: If files, manager state or final cleanup cannot be verified.
        """
        executable = self._shim(dry_run=False)
        before = self._capture_install_files()
        previous = self.owned_manager_state(executable.parent)
        if previous.pid is not None:
            msg = "A daemon tick is still running; no service or files were removed."
            raise HeinzelError(msg)
        phases = Phases(
            self._kernel.resolve(Progress),
            [("unload", "Stopping the owned service"), ("remove", "Removing the owned service definition")],
        )
        commands: list[list[str]] = []
        token = secrets.token_hex(32)
        pending = _handover_absolute_path(self.state_dir / "handover-pending.json")
        private_write(pending, json.dumps({"token": token}, indent=2) + "\n")
        with phases.step("unload", "Stopping the owned service"):
            if previous.registered:
                command = (
                    ["/bin/launchctl", "bootout", f"gui/{self._uid()}/{LABEL}"]
                    if self._platform == "darwin"
                    else ["/usr/bin/systemctl", "--user", "disable", "--now", f"{SYSTEMD_NAME}.timer"]
                )
                if self._capture_install_files() != before or self.owned_manager_state(executable.parent) != previous:
                    msg = "The daemon changed before stopping; its files were retained."
                    raise HeinzelError(msg)
                commands.append(command)
                self.owned_manager_command(command)
            stopped = self.owned_manager_state(executable.parent)
            if stopped.active or stopped.pid is not None or (self._platform == "darwin" and stopped.registered):
                msg = "The daemon is not confirmed stopped; its definitions and recovery gate were retained."
                raise HeinzelError(msg)
        removed: list[Path] = []
        with phases.step("remove", "Removing the owned service definition"):
            removed = self._remove_install_files(before)
            if self._platform == "linux":
                command = ["/usr/bin/systemctl", "--user", "daemon-reload"]
                commands.append(command)
                if self.owned_manager_command(command).returncode != 0:
                    msg = "The definitions were removed but manager reload is uncertain; inspect its local state."
                    raise HeinzelError(msg)
            if self.owned_manager_state(executable.parent).registered:
                msg = "The daemon manager registration is not confirmed removed; explicit recovery is required."
                raise HeinzelError(msg)
        if not self._mutation_token_matches(token, "handover-pending.json"):
            msg = "The daemon removal writer changed; explicit recovery is required."
            raise HeinzelError(msg)
        pending.unlink()
        fsync_dir(pending.parent)
        return Uninstalled(removed=removed, commands=commands)

    def _remove_install_files(self, before: dict[Path, bytes | None]) -> list[Path]:
        removed: list[Path] = []
        expected = dict(before)
        for path, payload in before.items():
            if self._capture_install_files() != expected:
                msg = "The owned daemon files changed during removal; explicit recovery is required."
                raise HeinzelError(msg)
            if payload is not None:
                path.unlink()
                fsync_dir(path.parent)
                if path.name != "install.json":
                    removed.append(path)
            expected[path] = None
        return removed

    # Inspecting ------------------------------------------------------------------------------------------------------

    def job_running(self) -> bool:
        """Whether a tick holds the tick lock now, i.e. is running jobs (ADR 0116)."""
        return held(self.state_dir / "tick.lock")

    def status(self) -> DaemonStatus:
        """Installed, loaded, paused, last tick and failing jobs; runs launchctl or systemctl only when installed."""
        installed = self._platform in PLATFORMS and all(path.exists() for path in self._unit_paths())
        recorded = self._read_state("install.json")
        jobs = self.jobs()
        # install.json records the intended unit version, not the code that actually ran. The tick heartbeat is
        # written by the executing process and intentionally stays unknown for older versions without this field.
        heartbeat = self._read_state("heartbeat.json")
        version = str(heartbeat["version"]) if installed and isinstance(heartbeat.get("version"), str) else None
        tick_interval = heartbeat.get("interval_minutes")
        tick_interval = tick_interval if type(tick_interval) is int and tick_interval > 0 and version else None
        installed_version = str(recorded["version"]) if installed and isinstance(recorded.get("version"), str) else None
        version_mismatch = installed and (
            self._handover_pending()
            or not self._handover_marker_valid(recorded)
            or (version is not None and version != __version__)
            or (installed_version is not None and installed_version != __version__)
            or (version is not None and installed_version is not None and version != installed_version)
        )
        owned = False
        loaded = False
        try:
            executable = self._shim(dry_run=not installed) if installed else None
            owned = executable is not None and not self._handover_pending() and self.owned_unit_state(executable.parent)
            if owned and executable is not None:
                loaded = self.owned_manager_state(executable.parent).active
        except (OSError, ValueError, HeinzelError):
            owned = False
        return DaemonStatus(
            owned=owned,
            platform=self._platform,
            installed=installed,
            loaded=loaded,
            paused=self.paused,
            interval_minutes=int(recorded["interval_minutes"]) if "interval_minutes" in recorded else None,
            executable=Path(str(recorded["executable"])) if "executable" in recorded else None,
            last_tick=self._state_timestamp(heartbeat, "last_tick"),
            last_tick_interval_minutes=tick_interval,
            jobs=len(jobs),
            failing=[job.job for job in jobs if job.problem or job.last_disposition is Disposition.FAILED],
            version=version,
            installed_version=installed_version,
            installed_at=self._state_timestamp(recorded, "installed_at") if installed else None,
            version_mismatch=version_mismatch,
        )

    def _clear_after_recovery_locked(self) -> bool:
        """Whether no lifecycle gate remains once an abandoned mutation, if any, was recovered under the held lock."""
        self._recover_abandoned_mutation_locked()
        return not self._handover_pending()

    def _recover_abandoned_mutation(self) -> Path | None:
        """Take the lifecycle lock without waiting, then recover an abandoned mutation as the locked variant does."""
        if _safely_absent(self.state_dir / "mutation-pending.json"):
            return None
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            return self._recover_abandoned_mutation_locked() if acquired else None

    def _recover_abandoned_mutation_locked(self) -> Path | None:
        """Archive a mutation marker whose owner provably ended, with a receipt; the lifecycle lock must be held.

        Holding the lock proves no live mutation scope owns the marker; the recorded boot or process proves its owner
        ended, and the startup checkpoint proves the installed tool is a verified generation (ADR 0117).
        """
        if self._quiescence_installation is None or _safely_absent(self.state_dir / "mutation-pending.json"):
            return None
        from heinzel.modules.system.daemon._abandoned_mutation import (  # noqa: PLC0415 - recovery only
            Evidence,
            recover_abandoned_mutation,
        )

        def evidence() -> Evidence:
            return Evidence(*self._host_boot_evidence())

        with lock(self.state_dir / "tick.lock") as acquired:
            if not acquired:
                return None
            try:
                return recover_abandoned_mutation(
                    self.state_dir.parent, self._quiescence_installation, evidence, self._clock
                )
            except (OSError, HeinzelError):
                logger.warning("An abandoned installation mutation could not be archived; it stays pending.")
                return None

    def _handover_pending(self) -> bool:
        if self._quiescence_installation is not None and not _safely_absent(
            host_maintenance_gate(self._quiescence_installation.prefix)
        ):
            return True
        for name in ("handover-pending.json", "mutation-pending.json", "standard-install-pending.json"):
            try:
                (self.state_dir / name).lstat()
            except FileNotFoundError:
                continue
            except OSError:
                return True
            return True
        return False

    def _handover_marker_valid(self, recorded: dict[str, str | int]) -> bool:
        handover_id = recorded.get("handover_id")
        if handover_id is None:
            try:
                (self.state_dir / "handover-request.json").lstat()
            except FileNotFoundError:
                return True
            except OSError:
                return False
            return False
        try:
            path = self.state_dir / "handover-observation.json"
            with path.open("rb") as source:
                raw = source.read(MAX_OBSERVATION_BYTES + 1)
            if len(raw) > MAX_OBSERVATION_BYTES:
                return False
            observation = TypeAdapter(DaemonHandoverObservation).validate_json(raw, strict=True)
            request = self._read_state("handover-request.json")
            heartbeat = self._read_state("heartbeat.json")
            return (
                observation.request_id == handover_id
                and request.get("request_id") == handover_id
                and request.get("unit_signature") == observation.old_unit_signature
                and _heartbeat_covers(heartbeat, observation.tick_at, observation.version)
                and self._continuity_fingerprint is not None
                and self._continuity_fingerprint() == observation.fingerprint
                and observation.version == recorded.get("version")
                and observation.new_unit_signature == self._handover_signature()
            )
        except Exception:  # noqa: BLE001 - unavailable continuity evidence must deny admission
            return False

    def health(self) -> list[Check]:
        """The daemon's checks for `hz system doctor`; not installing it is a choice, not a problem."""
        status = self.status()
        if not status.installed:
            checks = [
                Check(name="daemon", status=Status.UP, detail="not installed; `hz system daemon install` runs jobs")
            ]
        elif not status.owned:
            checks = [
                Check(
                    name="daemon",
                    status=Status.DEGRADED,
                    detail="the service files or manager identity are not verified for this selected profile",
                    fix="Inspect the user service manager and selected profile before changing the existing files",
                )
            ]
        elif not status.loaded:
            checks = [
                Check(
                    name="daemon",
                    status=Status.DEGRADED,
                    detail="installed but not loaded, so no ticks run",
                    fix="Run `hz system daemon uninstall`, then `hz system daemon install` to activate this owned "
                    "profile; pause state is retained",
                )
            ]
        else:
            checks = [Check(name="daemon", status=Status.UP, detail=f"loaded, every {status.interval_minutes} minutes")]
            checks.append(self._freshness(status))
        if status.paused:
            checks.append(
                Check(name="paused", status=Status.DEGRADED, detail="ticks are paused", fix="hz system daemon resume")
            )
        self._jobs()
        if self.compile_error is not None:
            checks.append(
                Check(
                    name="compiled jobs",
                    status=Status.DEGRADED,
                    detail=f"compiling jobs failed, so only declared jobs run: {self.compile_error}",
                    fix="See `hz system daemon logs`; `hz heinzel check` lists problems in contracts",
                )
            )
        failing = self._failing_for_doctor()
        if failing:
            checks.append(
                Check(
                    name="jobs",
                    status=Status.DEGRADED,
                    detail=f"failing: {', '.join(failing)}",
                    fix="See `hz system daemon jobs` and `hz system daemon logs`",
                )
            )
        return checks

    def _failing_for_doctor(self) -> list[str]:
        """Jobs the doctor reports: one failed run right after a good one is a hiccup, not yet a problem.

        `status().failing` still names every job whose last run failed. The doctor, and the notice it raises, wait
        for the second failed run in a row, so one unreachable feed doesn't degrade the whole installation.
        """
        ledger = Ledger(self.receipts_dir)
        failing: list[str] = []
        for job in self.jobs():
            if job.problem is None and job.last_disposition is not Disposition.FAILED:
                continue
            if job.problem is None and _failed_once_after_success(ledger, job.job):
                continue
            failing.append(job.job)
        return failing

    def jobs(self) -> list[JobInfo]:
        """Every job the kernel knows, with its schedule, next slot and last run; a damaged ledger is a problem."""
        ledger = Ledger(self.receipts_dir)
        now = self._clock()
        infos: list[JobInfo] = []
        for job in self._jobs():
            problem = schedule.problem(job.let.schedule)
            try:
                last = ledger.last(job.id)
            except HeinzelError as exc:
                last, problem = None, str(exc)
            infos.append(
                JobInfo(
                    job=job.id,
                    summary=job.let.summary,
                    schedule=job.let.schedule,
                    scope=job.let.scope,
                    timeout_seconds=job.let.timeout.total_seconds(),
                    retries=job.let.retries,
                    catch_up=job.let.catch_up,
                    next_run=None
                    if schedule.problem(job.let.schedule)
                    else schedule.next_slot(job.let.schedule, now, self._zone),
                    last_disposition=last.disposition if last else None,
                    last_as_of=last.as_of if last else None,
                    problem=problem,
                )
            )
        return infos

    def logs(self, lines: int) -> list[LogLine]:
        """The last lines of the daemon's log, oldest first."""
        return tail(self.log_path, lines)

    # Pausing ---------------------------------------------------------------------------------------------------------

    @property
    def paused(self) -> bool:
        """Whether the local kill switch is on."""
        return (self.state_dir / "paused").exists()

    def pause(self) -> Toggled:
        """Turn the local kill switch on: ticks run no job until `resume`."""
        write_private(self.state_dir / "paused", "")
        return Toggled(paused=True)

    def resume(self) -> Toggled:
        """Turn the local kill switch off under the lifecycle lock.

        Raises:
            HeinzelError: If the lifecycle is busy or requires recovery.
        """
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            if not acquired or self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery."
                raise HeinzelError(msg)
            (self.state_dir / "paused").unlink(missing_ok=True)
            return Toggled(paused=False)

    # Ticking ---------------------------------------------------------------------------------------------------------

    def tick(self, *, dry_run: bool = False, job: str | None = None) -> TickReport:
        """Run the jobs that are due, or `job` now, and write a receipt for each; `dry_run` only says what is due.

        One job's failure, a damaged receipt chain included, fails that job only; the others still run.

        Raises:
            HeinzelError: If `job` names no registered job.
        """
        now = self._clock()
        jobs = self._jobs()
        if job is not None:
            job = self._current_id(job, jobs)
            jobs = [j for j in jobs if j.id == job]
            if not jobs:
                msg = f"No job {job!r}; `hz system daemon jobs` lists them."
                raise HeinzelError(msg)
        if dry_run:
            if self.paused:
                return TickReport(at=now, dry_run=True, paused=True, locked=False, runs=[])
            return TickReport(at=now, dry_run=True, paused=False, locked=False, runs=self._plan(jobs, job, now))
        # Lifecycle precedes tick and any session writer; never wait for another lifecycle writer.
        with lock_until_finished(
            self.state_dir / "lifecycle.lock", lambda: list(self._abandoned.values())
        ) as lifecycle:
            if not lifecycle or not self._clear_after_recovery_locked():
                return TickReport(at=now, dry_run=False, paused=self.paused, locked=True, runs=[])
            with lock(self.state_dir / "tick.lock") as acquired:
                if not acquired:
                    return TickReport(at=now, dry_run=False, paused=False, locked=True, runs=[])
                if self.paused:  # checked under the lock, so a `pause` that waited for a tick is honoured by the next
                    return TickReport(at=now, dry_run=False, paused=True, locked=False, runs=[])
                handover = self._begin_handover_observation()
                log = TickLog(self.log_path, f"{now:%Y%m%dT%H%M%S}", self._clock)
                try:
                    runs = self._run(jobs, job, now, log)
                    manager_pid = self._service_manager_pid()
                    manager_bound = manager_pid == os.getpid()
                    heartbeat: dict[str, str | int] = {
                        "last_tick": now.isoformat(),
                        "completed_at": self._clock().isoformat(),
                        "source": (
                            "scheduled" if manager_bound and job is None else "manual" if manager_pid else "unknown"
                        ),
                        "outcome": (
                            "failed" if any(run.disposition is Disposition.FAILED for run in runs) else "completed"
                        ),
                    }
                    recorded = self._read_state("install.json")
                    interval = recorded.get("interval_minutes")
                    if manager_bound and isinstance(interval, int) and not isinstance(interval, bool) and interval > 0:
                        heartbeat["interval_minutes"] = interval
                    if manager_bound:
                        heartbeat["version"] = __version__
                        self._settle_handover_locked(handover, now)
                    self._write_state("heartbeat.json", heartbeat)
                    self._post_notices(log)
                    log.write(logging.INFO, f"tick done: {len(runs)} run(s)")
                finally:
                    log.close()
            return TickReport(at=now, dry_run=False, paused=False, locked=False, runs=runs)

    def _begin_handover_observation(self) -> tuple[str, str, dict[str, int]] | None:
        """Bind a request and local fingerprint at tick start only under the running service manager."""
        request = self._read_state("handover-request.json")
        request_id = request.get("request_id")
        boot = request.get("boot")
        if (
            not isinstance(request_id, str)
            or not re.fullmatch(r"[a-f0-9]{64}", request_id)
            or not isinstance(boot, str)
            or not boot
            or self._current_boot_identity() != boot
            or self._continuity_fingerprint is None
            or self._service_manager_pid() != os.getpid()
            or not self._request_unit_matches(request)
        ):
            return None
        try:
            fence = self._request_birth_fence(request)
            birth = self._process_birth()
            if fence is None or birth is None or not _process_birth.after_request(fence, birth):
                return None
            fingerprint = self._continuity_fingerprint()
        except Exception:  # noqa: BLE001 - an unavailable local observation must not fail the daemon's jobs
            return None
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            return None
        return request_id, fingerprint, birth

    def _settle_handover_locked(self, observed: tuple[str, str, dict[str, int]] | None, now: datetime) -> None:
        """In a manager-bound tick: complete the bound handover, or re-arm one no later tick could complete."""
        if observed is None:
            self._rearm_handover_locked()
        else:
            self._finish_handover_locked(observed, now)

    def _rearm_handover_locked(self) -> None:
        """Self-heal a registration record that no later tick could correct, once per version and boot.

        Runs in a manager-bound tick under the lifecycle lock, after it could not complete a handover. When the install
        marker doesn't record the version this tick runs (or its handover can't be verified), and no later tick could
        complete the current request (none, another boot's, a changed unit or no ordering fence), it writes a fresh
        request. Nothing is restarted: the next scheduled tick is a new process born after it and commits the marker
        through the ordinary observation, with every check that path has. `handover-rearm.json` keeps it to one try.
        """
        try:
            boot = self._current_boot_identity()
            marker = self._read_state("install.json")
            if (
                not boot
                or not marker
                or self.paused
                or self._continuity_fingerprint is None
                or self._handover_pending()
                or (marker.get("version") == __version__ and self._handover_marker_valid(marker))
                or self._request_completable(self._read_state("handover-request.json"), boot)
                or self._read_state("handover-rearm.json") == {"version": __version__, "boot": boot}
            ):
                return
            fence = self._current_birth_fence()
            signature = self._handover_signature()
            if fence is None:
                return
            self._write_state("handover-rearm.json", {"version": __version__, "boot": boot})
            data: dict[str, str | int] = {
                "request_id": secrets.token_hex(32),
                "boot": boot,
                "unit_signature": signature,
            }
            data.update({f"birth_{key}": value for key, value in fence.items()})
            self._write_state("handover-request.json", data)
            log_event(logger, "daemon.registration.rearmed", version=__version__)
        except Exception:  # noqa: BLE001 - self-healing never fails a tick; the mismatch stays visible in status
            logger.warning("The daemon's registration record could not be re-armed; status shows the mismatch.")

    def _request_completable(self, request: dict[str, str | int], boot: str) -> bool:
        """Whether a later manager-bound tick of this boot could still complete `request`."""
        request_id = request.get("request_id")
        return (
            isinstance(request_id, str)
            and re.fullmatch(r"[a-f0-9]{64}", request_id) is not None
            and request.get("boot") == boot
            and self._request_birth_fence(request) is not None
            and self._request_unit_matches(request)
        )

    def _finish_handover_observation(self, observed: tuple[str, str, dict[str, int]] | None, now: datetime) -> None:
        """Commit a fresh observation under the lifecycle writer lock."""
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            if acquired and not self._handover_pending():
                self._finish_handover_locked(observed, now)

    def _finish_handover_locked(self, observed: tuple[str, str, dict[str, int]] | None, now: datetime) -> None:
        """Persist actual discovered contribution metadata only if the request and local files still match."""
        if observed is None or self._continuity_fingerprint is None:
            return
        request_id, fingerprint, birth = observed
        request = self._read_state("handover-request.json")
        if (
            request.get("request_id") != request_id
            or request.get("boot") != self._current_boot_identity()
            or not self._request_unit_matches(request)
        ):
            return
        try:
            if self._continuity_fingerprint() != fingerprint or self._process_birth() != birth:
                return
            records = self._kernel.modules
            if len(records) > MAX_HANDOVER_CONTRIBUTIONS:
                return
            contributions = [(item.qualname, item.origin.distribution, item.origin.version) for item in records]
            self._commit_handover_marker(request, request_id, fingerprint, contributions, now)

        except Exception:  # noqa: BLE001 - observation failures cannot make a successful tick fail
            return

    def _commit_handover_marker(
        self,
        request: dict[str, str | int],
        request_id: str,
        fingerprint: str,
        contributions: list[tuple[str, str, str]],
        now: datetime,
    ) -> None:
        """Commit the marker and receipt while a durable pending token gates status."""
        old_signature = request.get("unit_signature")
        if not isinstance(old_signature, str):
            return
        marker = self._read_state("install.json")
        if (
            not marker
            or self.paused
            or not self._request_unit_matches(request)
            or self._service_manager_pid() != os.getpid()
        ):
            return
        marker["version"] = __version__
        marker["handover_id"] = request_id
        marker_text = json.dumps(marker, indent=2) + "\n"
        marker_bytes = marker_text.encode()
        new_signature = self._handover_signature(marker_override=marker_bytes)
        body = {
            "request_id": request_id,
            "version": __version__,
            "fingerprint": fingerprint,
            "contributions": contributions,
            "tick_at": now.isoformat(),
            "old_unit_signature": old_signature,
            "new_unit_signature": new_signature,
        }
        text = json.dumps(body, ensure_ascii=True)
        if len(text.encode()) > MAX_OBSERVATION_BYTES or len(marker_bytes) > MAX_HANDOVER_FILE_BYTES:
            return
        pending: dict[str, str | int] = {
            "request_id": request_id,
            "old_unit_signature": old_signature,
            "new_unit_signature": new_signature,
            "version": __version__,
        }
        self._write_state("handover-pending.json", pending)
        if (
            self._read_state("handover-request.json") != request
            or not self._request_unit_matches(request)
            or self._service_manager_pid() != os.getpid()
            or self.paused
        ):
            return
        write_private(self.state_dir / "install.json", marker_text)
        if (
            self._handover_signature() != new_signature
            or self._read_state("handover-request.json") != request
            or (self.state_dir / "paused").exists()
            or self._service_manager_pid() != os.getpid()
            or self._continuity_fingerprint is None
            or self._continuity_fingerprint() != fingerprint
        ):
            return
        write_private(self.state_dir / "handover-observation.json", text + "\n")
        # Removing the durable pending token commits the transaction. If a crash resurrects it, reads fail closed.
        (self.state_dir / "handover-pending.json").unlink()

    def _current_boot_identity(self) -> str | None:
        """Return a bounded trusted same-boot marker; missing or unavailable leaves observations pending."""
        try:
            value = self._boot_identity()
        except Exception:  # noqa: BLE001 - unavailable evidence must not interrupt service operation
            return None
        return value if isinstance(value, str) and 0 < len(value) <= MAX_BOOT_IDENTITY else None

    def _host_boot_evidence(self) -> tuple[str | None, int | None]:
        """The current boot's identity and start; unavailable evidence proves nothing."""
        try:
            identity, started = self._host_boot()
        except Exception:  # noqa: BLE001 - unavailable evidence must not interrupt service operation
            return None, None
        return (
            identity if isinstance(identity, str) and 0 < len(identity) <= MAX_BOOT_IDENTITY else None,
            started if type(started) is int and started > 0 else None,
        )

    def _current_birth_fence(self) -> dict[str, int] | None:
        try:
            value = self._birth_fence()
            if value is None or set(value) != {"ticks", "hz", "ns_dev", "ns_ino"}:
                return None
            if not all(type(item) is int and item >= 0 for item in value.values()):
                return None
        except Exception:  # noqa: BLE001 - unavailable ordering evidence must not interrupt the handover request
            return None
        else:
            return value

    @staticmethod
    def _request_birth_fence(request: dict[str, str | int]) -> dict[str, int] | None:
        fields = {key: request.get(f"birth_{key}") for key in ("ticks", "hz", "ns_dev", "ns_ino")}
        if any(type(value) is not int for value in fields.values()):
            return None
        return cast("dict[str, int]", fields)

    def _current_id(self, job: str, jobs: list[Job]) -> str:
        """`job`, or its id under its module's new name when it names a renamed module's job by the old (ADR 0051)."""
        module, sep, name = job.partition(":")
        if not sep or any(j.id == job for j in jobs) or (renamed := self._kernel.renamed(module)) is None:
            return job
        current = f"{renamed}:{name}"
        log_event(logger, "daemon.job.renamed", level=logging.WARNING, old=job, current=current)
        return current

    def _post_notices(self, log: TickLog) -> None:
        """Pop up due notices natively when no web app shows them (ADR 0043); a failure only costs the pop-up."""
        if not self._kernel.provides(Notifications):
            return
        try:
            posted = self._kernel.resolve(Notifications).post_native()
        except Exception as exc:  # noqa: BLE001 - isolation boundary: notices never fail a tick
            log.write(logging.WARNING, f"posting notices failed: {describe(exc)}")
            return
        if posted:
            log.write(logging.INFO, f"posted {posted} notice(s)")

    def _plan(self, jobs: list[Job], manual: str | None, now: datetime) -> list[JobRun]:
        ledger = Ledger(self.receipts_dir)
        planned: list[JobRun] = []
        for job in jobs:
            try:
                due = self._due(ledger, job, manual, now, None)
            except HeinzelError as exc:
                planned.append(_failed(job, now, str(exc)))
                continue
            planned.extend(
                JobRun(
                    job=job.id,
                    as_of=d.slot,
                    wake=d.wake,
                    disposition=Disposition.SKIPPED if d.skip else None,
                    error=d.skip,
                )
                for d in due
            )
        return planned

    def _run(self, jobs: list[Job], manual: str | None, now: datetime, log: TickLog) -> list[JobRun]:
        ledger = Ledger(self.receipts_dir)
        ledger.broken()  # reads the receipts, so unreadable files are known before any job runs
        for name in ledger.unreadable:
            log.write(logging.WARNING, f"receipt file {name} is unreadable and was ignored")
        runs: list[JobRun] = []
        for job in jobs:
            problem = schedule.problem(job.let.schedule)
            if problem and manual is None:
                log.write(logging.ERROR, problem, job=job.id)
                runs.append(_failed(job, now, problem))
                continue
            try:
                runs.extend(self._handle(ledger, job, due, log) for due in self._due(ledger, job, manual, now, log))
            except Exception as exc:  # noqa: BLE001 - one job's damage (its ledger, its idempotency function) is its own
                error = describe(exc)
                log.write(logging.ERROR, f"{job.id} failed: {error}", job=job.id, error=error)
                runs.append(_failed(job, now, error))
        return runs

    def _handle(self, ledger: Ledger, job: Job, due: Due, log: TickLog) -> JobRun:
        """Run or skip one slot of a job and write its receipt."""
        if due.wake is Wake.MANUAL:  # its own key space, so a manual run never cancels a scheduled one
            key = f"manual:{job.id}@{due.slot.isoformat()}"
        else:
            key = job.let.idempotency(due.slot) if job.let.idempotency else f"{job.id}@{due.slot.isoformat()}"
        skip = due.skip
        if skip is None and due.wake is Wake.SCHEDULE and ledger.rendered(job.id, key):
            skip = f"already rendered as {key}"
        previous = self._abandoned.get(job.id)
        if skip is None and previous is not None and previous.is_alive():
            skip = "its previous run timed out and is still running"
        outcome = Outcome(error=skip) if skip is not None else self._execute(job, due.slot)
        if isinstance(outcome.value, JobInactive) and outcome.error is None:
            return JobRun(
                job=job.id, as_of=due.slot, wake=due.wake, disposition=Disposition.SKIPPED, error=outcome.value.reason
            )
        if skip is not None:
            disposition = Disposition.SKIPPED
        else:
            disposition = Disposition.FAILED if outcome.error else Disposition.RENDERED
        receipt = ledger.append(
            job=job.id,
            wake=due.wake,
            disposition=disposition,
            as_of=due.slot,
            idempotency_key=key,
            error=outcome.error,
            written_at=self._clock(),
            result=_json(outcome.value) if disposition is Disposition.RENDERED else None,
        )
        level = logging.ERROR if disposition is Disposition.FAILED else logging.INFO
        log.write(level, f"{job.id} {disposition.value}", job=job.id, receipt=receipt.id, error=outcome.error)
        return JobRun(
            job=job.id,
            as_of=due.slot,
            wake=due.wake,
            disposition=disposition,
            error=outcome.error,
            receipt=receipt.id,
        )

    def _due(self, ledger: Ledger, job: Job, manual: str | None, now: datetime, log: TickLog | None) -> list[Due]:
        """The slots to handle now.

        Missed slots count from the job's last scheduled receipt. A job without one starts at the later of the
        previous tick and one interval ago, so a new job, or one whose receipts were moved aside, never replays its
        history, even after a long time without ticks. A last receipt more than an interval in the future means the
        clock went back; the job then starts again from one interval ago.

        Raises:
            HeinzelError: If the job's receipts are damaged.
        """
        if manual is not None:
            skip = "this job only runs on its scheduled slots" if job.let.scheduled_only else None
            return [Due(now, Wake.MANUAL, skip)]
        if schedule.problem(job.let.schedule):
            return []
        interval = timedelta(minutes=self._interval())
        cadence = tick_interval() or interval
        if job.let.max_tick_interval is not None and cadence > job.let.max_tick_interval:
            msg = (
                f"{job.id} needs daemon ticks at least every {job.let.max_tick_interval}; current cadence is {cadence}"
            )
            raise HeinzelError(msg)
        last = ledger.last(job.id, Wake.SCHEDULE)
        start = max(filter(None, (self._last_tick(), now - interval)))
        if last is not None and last.as_of > now + interval:
            if log is not None:
                log.write(
                    logging.WARNING,
                    f"{job.id}: last run {last.as_of.isoformat()} is in the future; the clock went back",
                    job=job.id,
                )
            after = now - interval
        else:
            after = last.as_of if last is not None else start
        rule = job.let.schedule
        if job.let.catch_up is CatchUp.ALL:
            return [Due(slot, Wake.SCHEDULE) for slot in schedule.slots(rule, after, now, self._zone)]
        slot = schedule.latest(rule, after, now, self._zone)
        if slot is None:
            return []
        if job.let.catch_up is CatchUp.SKIP and now - slot > interval:
            return [Due(slot, Wake.SCHEDULE, "missed slot; catch_up is skip")]
        return [Due(slot, Wake.SCHEDULE)]

    def _execute(self, job: Job, slot: datetime) -> Outcome:
        """Run a job through kernel injection with its timeout, retrying failures but never a timeout.

        A job that timed out may still be running, so running it again could do its work twice; its thread is
        remembered, and the job is skipped while it is alive.
        """
        outcome = Outcome()
        for _ in range(job.let.retries + 1):
            outcome = self._time_limit(
                lambda: job.let.run_for_slot(slot) if job.let.run_for_slot else self._kernel.call(job.let.func),
                job.let.timeout.total_seconds(),
            )
            if outcome.abandoned is not None:
                self._abandoned[job.id] = outcome.abandoned
                return outcome
            if outcome.error is None:
                return outcome
        return outcome

    # Helpers ---------------------------------------------------------------------------------------------------------

    def _jobs(self) -> list[Job]:
        declared = [(record.qualname, let) for record, let in self._kernel.lets(JobLet)]
        # Jobs compiled from data, such as workflow contracts (ADR 0021), compiled afresh on every call.
        self.compile_error = None
        try:
            compiled = self._kernel.compiled_jobs()
        except Exception as exc:
            self.compile_error = describe(exc)
            logger.exception("compiling jobs failed; running the declared jobs only")
            compiled = []
        return [
            Job(id=f"{module}:{let.name}", let=let)
            for module, let in (*declared, *compiled)
            if self._kernel.available(let.func)
        ]

    def _last_tick(self) -> datetime | None:
        return self._timestamp("heartbeat.json", "last_tick")

    def _service_manager_pid(self) -> int | None:
        """The process manager's PID for this tick, or unknown; never infer service origin from a CLI flag/env var.

        The label-specific launchd `list` response and systemd's `MainPID` property expose the currently running
        process. The daemon's jobs are oneshot, so the manager reports a PID only while its scheduled tick is active.
        Any missing, malformed, unsupported or failed query remains unknown and cannot attest this invocation.
        """
        try:
            binary = self._shim(dry_run=False).parent
            state = self.owned_manager_state(binary)
        except (OSError, HeinzelError):
            return None
        else:
            return state.pid if state.registered else None

    def _timestamp(self, name: str, key: str) -> datetime | None:
        return self._state_timestamp(self._read_state(name), key)

    @staticmethod
    def _state_timestamp(state: dict[str, str | int], key: str) -> datetime | None:
        value = state.get(key)
        try:
            return datetime.fromisoformat(str(value)) if value else None
        except ValueError:
            return None

    def _interval(self) -> int:
        return int(self._read_state("install.json").get("interval_minutes", DEFAULT_INTERVAL))

    def _freshness(self, status: DaemonStatus) -> Check:
        interval = timedelta(minutes=status.interval_minutes or DEFAULT_INTERVAL)
        since = status.last_tick or self._timestamp("install.json", "installed_at")
        if status.paused or since is None or self._clock() - since <= STALE_TICKS * interval:
            return Check(name="last tick", status=Status.UP, detail=str(status.last_tick or "none yet"))
        return Check(
            name="last tick",
            status=Status.DEGRADED,
            detail=f"no tick since {status.last_tick or 'installation'}",
            fix="See `hz system daemon logs`; on macOS check Login Items in System Settings",
        )

    def _minutes(self, interval: timedelta) -> int:
        minutes, remainder = divmod(int(interval.total_seconds()), 60)
        if remainder:
            msg = f"The interval must be whole minutes, not {interval}."
            raise HeinzelError(msg)
        try:
            check_interval(minutes)
        except ValueError as exc:
            raise HeinzelError(str(exc)) from exc
        return minutes

    def _shim(self, *, dry_run: bool) -> Path:
        """The stable `hz` shim in uv's tool bin directory, never the environment's own interpreter (ADR 0020).

        Raises:
            HeinzelError: If uv fails, or (unless `dry_run`) hz isn't installed as a uv tool.
        """
        installation = self._quiescence_installation
        if installation is not None:
            try:
                data = tomllib.loads(installation.receipt().decode())
                entries = [entry for entry in data["tool"]["entrypoints"] if entry.get("name") == "hz"]
                (entry,) = entries
                executable = Path(entry["install-path"])
                installation.verify_tool_destination(installation.prefix.parent, executable.parent)
            except (OSError, ValueError, KeyError, TypeError, HeinzelError) as exc:
                msg = "The daemon's selected uv tool cannot be verified; nothing was installed."
                raise HeinzelError(msg) from exc
            return executable
        if not dry_run:
            msg = "The daemon needs a verified uv tool installation; use `uv tool install heinzel` first."
            raise HeinzelError(msg)
        result = self._runner(self._command("uv", "tool", "dir", "--bin"))
        if result.returncode != 0:
            msg = f"`uv tool dir --bin` failed: {result.stderr.strip()}"
            raise HeinzelError(msg)
        return Path(result.stdout.strip()) / "hz"

    def _unit_paths(self) -> list[Path]:
        if self._platform == "darwin":
            return [plist_path(self.unit_dir)]
        if self._platform == "linux":
            return list(systemd_paths(self.unit_dir))
        msg = f"The daemon supports macOS (launchd) and Linux (systemd), not {self._platform}."
        raise HeinzelError(msg)

    def _unit_files(self, executable: Path, minutes: int) -> dict[Path, str]:
        paths = self._unit_paths()
        if self._platform == "darwin":
            return {paths[0]: launchd_plist(executable, minutes, self.log_path.parent, self._profile)}
        return {paths[0]: systemd_service(executable, self._profile), paths[1]: systemd_timer(minutes)}

    def _command(self, executable: str, *args: str) -> list[str]:
        """A command with its executable resolved on PATH.

        Raises:
            HeinzelError: If the executable isn't installed.
        """
        found = self._which(executable)
        if found is None:
            msg = f"`{executable}` is not on PATH; the daemon needs it on this platform."
            raise HeinzelError(msg)
        return [found, *args]

    def _read_state(self, name: str) -> dict[str, str | int]:
        path = self.state_dir / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return cast("dict[str, str | int]", data) if isinstance(data, dict) else {}

    def _write_state(self, name: str, data: dict[str, str | int]) -> None:
        write_private(self.state_dir / name, json.dumps(data, indent=2) + "\n")


def _failed(job: Job, now: datetime, error: str) -> JobRun:
    return JobRun(job=job.id, as_of=now, wake=Wake.SCHEDULE, disposition=Disposition.FAILED, error=error)


def _json(value: object) -> JsonValue:
    """A job's return value as JSON for its receipt; what JSON can't express is kept as its text."""
    return cast("JsonValue", to_jsonable_python(value, fallback=str))
