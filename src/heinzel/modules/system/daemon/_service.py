"""Service of the daemon module: install the OS unit, and tick: run the jobs that are due and write receipts.

The OS supervises and the daemon ticks (ADR 0020): launchd or systemd starts `hz system daemon tick` every few
minutes, a short-lived process that survives `hz system update`, crashes and sleep by construction.

The service reads only user-scope configuration (site and user files and the environment, not a project's
`hz.toml`), so the unit, the receipts and the logs are the same wherever `hz` runs, and a repository can't move them.
"""

import hashlib
import json
import logging
import os
import re
import secrets
import selectors
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Never, cast
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from pydantic_core import to_jsonable_python

from heinzel import (
    CatchUp,
    Check,
    Config,
    CurrentUser,
    HeinzelError,
    JobInactive,
    JobLet,
    Kernel,
    Notifications,
    Settings,
    Status,
    __version__,
)
from heinzel.kernel import (
    KERNEL_SECTION,
    DaemonHandoverAssessment,
    DaemonHandoverObservation,
    DaemonHandoverPlan,
    DaemonHandoverRequest,
    DaemonMutationScope,
    Installation,
    ProcessOutcome,
    subprocess_environment,
    tick_interval,
)
from heinzel.modules.system.daemon import _process_birth
from heinzel.modules.system.daemon import _schedule as schedule
from heinzel.modules.system.daemon._ledger import Ledger
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
from heinzel.modules.system.daemon._runtime import (
    Outcome,
    Runner,
    TickLog,
    TimeLimit,
    describe,
    lock,
    lock_until_finished,
    private_dir,
    run,
    tail,
    within,
    write_private,
)
from heinzel.modules.system.daemon._units import (
    LABEL,
    LOGIN_ITEM_HINT,
    NOTIFIER_APP,
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
STALE_TICKS = 3
"""A daemon that missed this many intervals in a row is reported as degraded."""
PLATFORMS = ("darwin", "linux")
"""launchd and systemd read unit files; they hold no secrets."""
MODULE_SECTION = "system.daemon"
MAX_SERVICE_MANAGER_OUTPUT = 1_000_000
MAX_PID_DIGITS = 10
MAX_HANDOVER_FILE_BYTES = 256 * 1024
MAX_HANDOVER_CONTRIBUTIONS = 512
MAX_HANDOVER_TEXT = 512
HANDOVER_ROW_FIELDS = 3
MAX_BOOT_IDENTITY = 128
UUID_TEXT_LENGTH = 36
MAX_OBSERVATION_BYTES = 128 * 1024
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


def _launchd_pid(output: str, expected_label: str) -> int | None:
    """Parse one bounded label-specific launchctl property record, or return unknown.

    The service-specific `list` response is not a promised stable data interface, so accept only the exact current
    Label/PID keys and fail closed when the shape changes.
    """
    if len(output) > MAX_SERVICE_MANAGER_OUTPUT:
        return None
    labels = re.findall(r'(?m)^\s*"Label"\s*=\s*"([^"\r\n]+)"\s*;\s*$', output)
    pids = re.findall(r'(?m)^\s*"PID"\s*=\s*([0-9]+)\s*;\s*$', output)
    if labels != [expected_label] or len(pids) != 1:
        return None
    return _positive_pid(pids[0])


def _reported_pid(output: str) -> int | None:
    """Parse a decimal process id from a manager's machine-readable output, or return unknown."""
    if len(output) > MAX_SERVICE_MANAGER_OUTPUT:
        return None
    return _positive_pid(output.strip())


def _positive_pid(value: str) -> int | None:
    """Parse a bounded decimal PID greater than zero."""
    if not re.fullmatch(rf"[0-9]{{1,{MAX_PID_DIGITS}}}", value):
        return None
    pid = int(value)
    return pid if pid > 0 else None


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


def _handover_absolute_path(path: Path) -> Path:
    absolute = path if path.is_absolute() else Path.cwd() / path
    if ".." in absolute.parts:
        _invalid_handover()
    # These root-owned aliases are part of macOS's filesystem layout. Other symlinks
    # are refused, including aliases in user-controlled directories.
    if sys.platform == "darwin" and len(absolute.parts) > 1 and absolute.parts[1] in {"tmp", "var", "etc"}:
        alias = Path("/") / absolute.parts[1]
        info = os.lstat(alias)
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or alias.readlink() != Path(f"private/{alias.name}"):
            _invalid_handover()
        absolute = Path("/private") / absolute.relative_to("/")
    return absolute


def _check_handover_directory(descriptor: int) -> None:
    info = os.fstat(descriptor)
    sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not sticky_root):
        _invalid_handover()


@contextmanager
def _open_verified_handover_file(path: Path) -> Generator[BinaryIO, None, None]:
    """Open through trusted directories.

    Yields:
        The opened file while directory and entry identities remain checked.
    """
    parts = _handover_absolute_path(path).parts[1:]
    with ExitStack() as stack:
        root = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, root)
        parents: list[tuple[int, int | None, str | None]] = [(root, None, None)]
        current = root
        for name in parts[:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            stack.callback(os.close, child)
            parents.append((child, current, name))
            current = child
        for directory, _, _ in parents:
            _check_handover_directory(directory)
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        with os.fdopen(descriptor, "rb") as handle:
            yield handle
            for directory, _, _ in parents:
                _check_handover_directory(directory)
            for child, parent, child_name in parents[1:]:
                if parent is None or child_name is None:
                    _invalid_handover()
                current_info = os.stat(child_name, dir_fd=parent, follow_symlinks=False)
                initial = os.fstat(child)
                if (current_info.st_dev, current_info.st_ino) != (initial.st_dev, initial.st_ino):
                    _invalid_handover()
            entry = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            file_info = os.fstat(handle.fileno())
            if (entry.st_dev, entry.st_ino) != (file_info.st_dev, file_info.st_ino):
                _invalid_handover()


def _heartbeat_covers(heartbeat: dict[str, str | int], tick_at: str, version: str) -> bool:
    last_tick = heartbeat.get("last_tick")
    if not isinstance(last_tick, str) or heartbeat.get("version") != version:
        return False
    try:
        return datetime.fromisoformat(last_tick) >= datetime.fromisoformat(tick_at)
    except (ValueError, TypeError):
        return False


def _safely_absent(path: Path) -> bool:
    """Confirm absence through physical, trusted parents without following directory links."""
    try:
        parts = _handover_absolute_path(path).parts[1:]
        with ExitStack() as stack:
            current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, current)
            _check_handover_directory(current)
            for part in parts[:-1]:
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                except FileNotFoundError:
                    return True
                stack.callback(os.close, child)
                _check_handover_directory(child)
                current = child
            try:
                os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            except FileNotFoundError:
                return True
            return False
    except (OSError, HeinzelError):
        return False


def _launchctl_quiescent_output(output: str) -> bool:
    """Parse the documented legacy list format; any unexpected record fails closed."""
    if len(output.encode("utf-8")) > MAX_SERVICE_MANAGER_OUTPUT:
        return False
    lines = output.splitlines()
    if not lines or lines[0].split() != ["PID", "Status", "Label"]:
        return False
    labels: set[str] = set()
    expected_columns = 3
    for line in lines[1:]:
        parts = line.split()
        if (
            len(parts) != expected_columns
            or (parts[0] != "-" and not parts[0].isdigit())
            or not re.fullmatch(r"-?[0-9]+", parts[1])
            or parts[2] in labels
        ):
            return False
        labels.add(parts[2])
    return LABEL not in labels


def _manager_environment(command: list[str]) -> dict[str, str]:
    if command[0] != "/usr/bin/systemctl":
        return subprocess_environment()
    runtime = f"/run/user/{os.getuid()}"
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "XDG_RUNTIME_DIR": runtime,
    }


def _quiescence_command(command: list[str]) -> ProcessOutcome:
    """Bound a read-only manager child; never signal a reaped or unknown process."""
    process = subprocess.Popen(  # noqa: S603 - fixed absolute launchctl and fixed subcommands
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_manager_environment(command),
    )
    deadline = time.monotonic() + 5
    captured = bytearray()
    truncated = False
    try:
        if process.stdout is None:
            return ProcessOutcome(None, "", "")
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                events = selector.select(max(0, deadline - time.monotonic()))
                if not events:
                    break
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                captured.extend(chunk)
                if len(captured) > MAX_SERVICE_MANAGER_OUTPUT:
                    truncated = True
                    break
        if truncated or time.monotonic() >= deadline:
            return ProcessOutcome(None, "", "", truncated=truncated)
        try:
            status = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return ProcessOutcome(None, "", "")
        return ProcessOutcome(status, captured.decode("utf-8"), "")
    finally:
        if process.poll() is None:
            with suppress(ProcessLookupError, PermissionError):
                process.kill()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1)
        if process.stdout is not None:
            process.stdout.close()


def _linux_user_manager_identity(run_root: Path = Path("/run")) -> tuple[int, ...] | None:
    """Observe the private manager socket and its physical, safely owned parent directories."""
    try:
        uid = os.getuid()
        parents = [run_root, run_root / "user"]
        runtime = run_root / "user" / str(uid)
        for path in parents:
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                return None
        directory = runtime.lstat()
        systemd = (runtime / "systemd").lstat()
        socket = (runtime / "systemd/private").lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != uid
            or directory.st_mode & 0o022
            or not stat.S_ISDIR(systemd.st_mode)
            or systemd.st_uid != uid
            or systemd.st_mode & 0o022
            or not stat.S_ISSOCK(socket.st_mode)
            or socket.st_uid != uid
        ):
            return None
    except OSError:
        return None
    else:
        return directory.st_dev, directory.st_ino, systemd.st_dev, systemd.st_ino, socket.st_dev, socket.st_ino


def _systemd_quiescent_unit(output: str, name: str) -> bool:
    """Require the documented unit properties, including an empty queued-job property."""
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            return False
        values[key] = value
    required = {"Id", "LoadState", "ActiveState", "SubState", "Job"}
    if not required.issubset(values) or set(values) - required - {"MainPID", "ControlPID"}:
        return False
    if (
        values["Id"] != name
        or values["LoadState"] not in {"loaded", "not-found"}
        or values["ActiveState"] != "inactive"
        or values["SubState"] != "dead"
        or values["Job"] != ""
    ):
        return False
    if name.endswith(".service") and values["LoadState"] == "loaded":
        return values.get("MainPID") == values.get("ControlPID") == "0"
    return all(values.get(key, "0") == "0" for key in ("MainPID", "ControlPID"))


def _invalid_handover() -> Never:
    msg = "The existing daemon unit could not be verified; no handover was requested."
    raise HeinzelError(msg)


@dataclass(frozen=True, slots=True)
class _MutationAuthority:
    pending_path: Path
    token_matches: Callable[[str], bool]
    capture: Callable[[], DaemonHandoverPlan]
    request: Callable[[DaemonHandoverPlan], DaemonHandoverRequest]
    fingerprint: Callable[[], str | None] | None
    checkpoint: Callable[[], bool]
    had_checkpoint: bool
    original_fingerprint: str
    original_identity: str
    rollback_identity: Callable[[], str]


class _MutationScope:
    """One lock-held mutation; only trusted host callers can supply checkpoint evidence."""

    def __init__(self, token: str, authority: _MutationAuthority) -> None:
        self.token = token
        self.authority = authority
        self.active = True
        self.plan: DaemonHandoverPlan | None = None
        self.expected: str | None = None
        self.requested = False
        self.aborted = False

    @property
    def checkpoint_matched(self) -> bool:
        self._require_active()
        return self.authority.had_checkpoint

    def _require_active(self) -> None:
        if not self.active or not self.authority.token_matches(self.token):
            msg = "The daemon mutation scope is unavailable or its pending token changed."
            raise HeinzelError(msg)

    def capture(self) -> DaemonHandoverPlan:
        self._require_active()
        if self.plan is not None:
            msg = "The daemon mutation state was already captured."
            raise HeinzelError(msg)
        self.plan = self.authority.capture()
        return self.plan

    def verified_complete(self, expected: str) -> None:
        self._require_active()
        if self.plan is None or self.expected is not None or not re.fullmatch(r"[a-f0-9]{64}", expected):
            msg = "The verified daemon mutation generation is invalid or already recorded."
            raise HeinzelError(msg)
        self._verify(expected)
        self.expected = expected

    def _verify(self, expected: str) -> None:
        fingerprint = self.authority.fingerprint
        if (
            fingerprint is None
            or fingerprint() != expected
            or self.authority.checkpoint() is not self.authority.had_checkpoint
            or fingerprint() != expected
        ):
            msg = "The installation generation or its durable checkpoint could not be verified."
            raise HeinzelError(msg)
        self._require_active()

    def abort_unchanged(self) -> None:
        self._require_active()
        if self.plan is None or self.expected is not None or self.requested:
            msg = "The mutation cannot be aborted after verification or handover."
            raise HeinzelError(msg)
        self._verify(self.authority.original_fingerprint)
        if self.authority.rollback_identity() != self.authority.original_identity:
            msg = "The installation identity changed; explicit recovery is required."
            raise HeinzelError(msg)
        self._require_active()
        self.authority.pending_path.unlink()
        self.aborted = True
        self.active = False

    def request(self, plan: DaemonHandoverPlan) -> DaemonHandoverRequest:
        self._require_active()
        if self.expected is None or self.plan is not plan or self.requested:
            msg = "The daemon handover does not match this verified mutation."
            raise HeinzelError(msg)
        self._verify(self.expected)
        result = self.authority.request(plan)
        if plan.active and not result.requested:
            msg = "The captured active daemon could not be handed over; mutation recovery is required."
            raise HeinzelError(msg)
        self.requested = True
        return result

    def commit(self) -> None:
        if self.aborted:
            return
        self._require_active()
        if self.expected is None or not self.requested:
            msg = "The mutation was not verified and handed over; explicit recovery is required."
            raise HeinzelError(msg)
        self._verify(self.expected)
        self.authority.pending_path.unlink()


class DaemonService:
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
        birth_fence: Callable[[], dict[str, int] | None] | None = None,
        process_birth: Callable[[], dict[str, int] | None] | None = None,
        quiescence_probe: Callable[[list[str]], ProcessOutcome] | None = None,
        quiescence_installation: Installation | None = None,
        linux_manager_identity: Callable[[], tuple[int, ...] | None] | None = None,
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
            birth_fence: Same-boot process-birth ordering fence, or None when unavailable.
            process_birth: Current process's operating-system birth evidence, or None when unavailable.
            quiescence_probe: Bounded read-only service-manager query, replaceable in tests.
            quiescence_installation: Selected tool identity for verifying an existing stopped unit.
            linux_manager_identity: Physical private manager socket identity, replaceable in tests.
        """
        self._notifier_installed = notifier_installed
        self._quiescence_probe = quiescence_probe or _quiescence_command
        self._quiescence_installation = quiescence_installation
        self._linux_manager_identity = linux_manager_identity or _linux_user_manager_identity
        self._continuity_fingerprint = continuity_fingerprint
        self._boot_identity = boot_identity or (lambda: _platform_boot_identity(self._platform, self._runner))
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
        self.compile_error: str | None = None
        """Why compiling jobs failed the last time they were listed; None when it worked."""
        self.unit_dir = daemon.unit_dir
        self.receipts_dir = settings.data_dir / "receipts"
        self.state_dir = settings.state_dir / "daemon"
        self.log_path = settings.log_dir / "daemon.jsonl"

    @classmethod
    def for_user(cls, kernel: Kernel) -> "DaemonService":
        """The service with user-scope settings: a project's `hz.toml` can't move the unit, receipts or logs."""
        config = Config.load(Path.home())  # as if run from the home directory, like the uninstall skill does
        installation = kernel.resolve(Installation)
        return cls(
            kernel,
            config.settings(Settings, KERNEL_SECTION),
            config.settings(DaemonSettings, MODULE_SECTION),
            continuity_fingerprint=installation.startup_fingerprint,
            quiescence_installation=installation,
        )

    # Installing ------------------------------------------------------------------------------------------------------

    def install(self, interval: timedelta, *, dry_run: bool = False) -> Installed:
        """Install under the lifecycle writer lock, or report a concurrent change.

        Raises:
            HeinzelError: If another lifecycle writer is active or installation fails.
        """
        if dry_run:
            return self._install_locked(interval, dry_run=True)
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            if not acquired or self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery; inspect it before installing."
                raise HeinzelError(msg)
            return self._install_locked(interval)

    def _install_locked(self, interval: timedelta, *, dry_run: bool = False) -> Installed:
        """Write the unit for this platform and load it; `dry_run` only says what it would write.

        Raises:
            HeinzelError: On an unsupported platform or interval, without a uv tool installation, or if loading fails.
        """
        minutes = self._minutes(interval)
        executable = self._shim(dry_run=dry_run)
        files = self._unit_files(executable, minutes)
        commands = self._load_commands()
        if dry_run:
            return Installed(
                platform=self._platform,
                executable=executable,
                interval_minutes=minutes,
                files=files,
                commands=[],
                dry_run=True,
            )
        private_dir(self.log_path.parent)
        for path, content in files.items():
            write_private(path, content, shared_dir=True)  # 0600: launchd and systemd read user units as the user
        if self._platform == "darwin":  # bootstrap fails on a loaded agent; unload the old one, if any, first
            self._runner(self._command("launchctl", "bootout", f"gui/{self._uid()}/{LABEL}"))
        for command in commands:
            result = self._runner(command)
            if result.returncode != 0:
                msg = f"`{' '.join(command)}` failed: {(result.stderr or result.stdout).strip()}"
                raise HeinzelError(msg)
        # This marker is used to refuse new schedule approvals when an older daemon may still be running. Write it
        # only after the service manager accepted the load/reload commands, so a failed replacement stays fail-closed.
        self._write_state(
            "install.json",
            {
                "interval_minutes": minutes,
                "executable": str(executable),
                "platform": self._platform,
                "installed_at": self._clock().isoformat(),
                "version": __version__,
            },
        )
        (self.state_dir / "handover-pending.json").unlink(missing_ok=True)
        (self.state_dir / "handover-request.json").unlink(missing_ok=True)
        hint = LOGIN_ITEM_HINT if self._platform == "darwin" and not self._notifier_installed() else None
        return Installed(
            platform=self._platform,
            executable=executable,
            interval_minutes=minutes,
            files=files,
            commands=commands,
            dry_run=False,
            hint=hint,
        )

    def restart(self) -> Restarted:
        """Ask the service manager to reload the installed daemon with the interval it has.

        The result says which version the unit was asked to use. `status` remains unknown until a tick records the
        version that actually ran.

        Raises:
            HeinzelError: If loading fails.
        """
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            if not acquired or self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery; inspect it before restarting."
                raise HeinzelError(msg)
            recorded = self._read_state("install.json")
            installed = self._platform in PLATFORMS and all(path.exists() for path in self._unit_paths())
            if not installed or "interval_minutes" not in recorded:
                return Restarted(restarted=False, version=__version__, interval_minutes=None)
            minutes = int(recorded["interval_minutes"])
            self._install_locked(timedelta(minutes=minutes))
            return Restarted(restarted=True, version=__version__, interval_minutes=minutes)

    def capture_handover(self) -> DaemonHandoverPlan:
        """Observe pre-change eligibility without installing, loading or starting a daemon.

        Raises:
            HeinzelError: If an existing service's files cannot be safely inspected.
        """
        if self._platform not in PLATFORMS or not all(path.exists() for path in self._unit_paths()) or self.paused:
            return DaemonHandoverPlan(active=False, platform=self._platform, unit_signature=None)
        signature = self._handover_signature()
        return DaemonHandoverPlan(self._loaded(), self._platform, signature)

    @contextmanager
    def admission_guard(self) -> Generator[None, None, None]:
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
    ) -> Generator[DaemonMutationScope, None, None]:
        """Hold lifecycle authority over one verified installation mutation.

        Yields:
            A single-use scope for capture, checkpoint verification and handover.

        Raises:
            HeinzelError: If another mutation or an unresolved transaction exists.
        """
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            if not acquired or self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery."
                raise HeinzelError(msg)
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
            self._write_state("mutation-pending.json", {"token": token})
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

    def _mutation_token_matches(self, token: str, name: str = "mutation-pending.json") -> bool:
        try:
            with _open_verified_handover_file(self.state_dir / name) as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                    return False
                raw = handle.read(256)
                return raw == (json.dumps({"token": token}, indent=2) + "\n").encode()
        except (OSError, HeinzelError):
            return False

    def _quiescence_target(self, binary: Path) -> bool:
        installation = self._quiescence_installation
        if installation is None or installation.prefix.name != "heinzel" or binary.is_relative_to(installation.prefix):
            return False
        prefix = installation.prefix
        store = prefix.parent
        try:
            for path in (store, binary):
                info = path.lstat()
                if (
                    not path.is_absolute()
                    or path.resolve(strict=True) != path
                    or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o022
                ):
                    return False
            if _safely_absent(prefix):
                return all(_safely_absent(binary / name) for name in ("hz", "heinzel", "hz-install"))
            info = prefix.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
                or prefix.resolve(strict=True) != prefix
            ):
                return False
            installation.verify_tool_destination(store, binary)
        except (OSError, HeinzelError):
            return False
        else:
            return True

    def _quiescence_unit_state(self, binary: Path) -> bool:
        paths = self._unit_paths()
        marker = self.state_dir / "install.json"
        absent = [_safely_absent(path) for path in paths]
        marker_absent = _safely_absent(marker)
        if all(absent) and marker_absent:
            return True
        if any(absent) or marker_absent or self._quiescence_installation is None:
            return False
        try:
            self._handover_signature()
            recorded = self._read_state("install.json")
            executable = binary / "hz"
            minutes = recorded.get("interval_minutes")
            if (
                recorded.get("executable") != str(executable)
                or recorded.get("platform") != self._platform
                or type(minutes) is not int
                or minutes <= 0
            ):
                return False
            self._quiescence_installation.verify_tool_destination(self._quiescence_installation.prefix.parent, binary)
            return all(path.read_text() == content for path, content in self._unit_files(executable, minutes).items())
        except (OSError, UnicodeError, HeinzelError):
            return False

    def _quiescent_snapshot(self, binary: Path, token: str | None = None) -> bool:
        if self._platform not in {"darwin", "linux"} or not self._quiescence_target(binary):
            return False
        paths = [
            self.state_dir / name
            for name in ("handover-pending.json", "handover-request.json", "mutation-pending.json")
        ]

        def pending_valid() -> bool:
            if token is None:
                return _safely_absent(self.state_dir / "standard-install-pending.json")
            return self._mutation_token_matches(token, "standard-install-pending.json")

        if not (
            pending_valid() and all(_safely_absent(path) for path in paths) and self._quiescence_unit_state(binary)
        ):
            return False
        if self._platform == "linux":
            return self._linux_quiescent_snapshot(binary, pending_valid, paths)
        commands = [
            ["/bin/launchctl", "managername"],
            ["/bin/launchctl", "manageruid"],
            ["/bin/launchctl", "list"],
            ["/bin/launchctl", "managername"],
            ["/bin/launchctl", "manageruid"],
        ]
        try:
            results = [self._quiescence_probe(command) for command in commands]
            if any(result.returncode != 0 or result.truncated for result in results):
                return False
            name1, uid1, listing, name2, uid2 = [result.stdout for result in results]
            return (
                name1.strip() == name2.strip() == "Aqua"
                and uid1.strip() == uid2.strip() == str(self._uid())
                and _launchctl_quiescent_output(listing)
                and pending_valid()
                and all(_safely_absent(path) for path in paths)
                and self._quiescence_unit_state(binary)
                and self._quiescence_target(binary)
            )
        except (OSError, ValueError, HeinzelError):
            return False

    def _linux_quiescent_snapshot(self, binary: Path, pending_valid: Callable[[], bool], paths: list[Path]) -> bool:
        identity = self._linux_manager_identity()
        if identity is None:
            return False
        manager = ["/usr/bin/systemctl", "--user", "--no-pager", "show", "--all", "--property=Version"]
        units = [f"{SYSTEMD_NAME}.service", f"{SYSTEMD_NAME}.timer"]
        commands = [manager]
        commands.extend(
            [
                "/usr/bin/systemctl",
                "--user",
                "--no-pager",
                "show",
                "--all",
                "--property=Id,LoadState,ActiveState,SubState,Job,MainPID,ControlPID",
                unit,
            ]
            for unit in (*units, *units)
        )
        commands.append(manager)
        try:
            results = [self._quiescence_probe(command) for command in commands]
            if any(result.returncode != 0 or result.truncated for result in results):
                return False
            first, *observations, last = [result.stdout for result in results]
            return (
                bool(re.fullmatch(r"Version=[^\r\n=]{1,128}\n?", first))
                and first == last
                and all(
                    _systemd_quiescent_unit(output, unit)
                    for output, unit in zip(observations, (*units, *units), strict=True)
                )
                and self._linux_manager_identity() == identity
                and pending_valid()
                and all(_safely_absent(path) for path in paths)
                and self._quiescence_unit_state(binary)
                and self._quiescence_target(binary)
            )
        except (OSError, ValueError, HeinzelError):
            return False

    @contextmanager
    def quiescence_guard(self, binary: Path) -> Generator[None, None, None]:
        """Hold lifecycle and tick locks through a trusted, quiescent standard installation.

        Non-GUI, unsupported or malformed manager evidence denies. This does not fence noncooperating OS actors or
        interpreter startup. The selected Installation must be bound before resolving this service.

        Yields:
            Control while cooperating daemon dispatch and lifecycle changes are fenced.

        Raises:
            HeinzelError: If a lock is busy or daemon quiescence cannot be established.
        """
        with lock(self.state_dir / "lifecycle.lock") as lifecycle:
            if not lifecycle or self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery."
                raise HeinzelError(msg)
            with lock(self.state_dir / "tick.lock") as tick:
                if tick and self._platform == "linux" and self._linux_manager_identity() is None:
                    msg = (
                        "The daemon is not proven quiescent because your Linux user manager is unavailable or "
                        "cannot be verified. Open a user session with its service manager and retry; "
                        "inspect `hz system daemon status` if needed."
                    )
                    raise HeinzelError(msg)
                if not tick or not self._quiescent_snapshot(binary):
                    if self._platform == "linux":
                        msg = (
                            "The daemon is not proven quiescent. In your Linux user session, check that the user "
                            "service manager is available and inspect `hz system daemon status`; ensure both the "
                            "service and timer are stopped before retrying."
                        )
                    elif self._platform == "darwin":
                        msg = (
                            "The daemon is not proven quiescent. Open your macOS Aqua desktop session and inspect "
                            "`hz system daemon status`; ensure the daemon is unloaded before retrying."
                        )
                    else:
                        msg = "The daemon is not proven quiescent on this platform; inspect it before installing."
                    raise HeinzelError(msg)
                token = secrets.token_hex(32)
                self._write_state("standard-install-pending.json", {"token": token})
                yield
                self._finish_quiescent_install(binary, token)

    def _finish_quiescent_install(self, binary: Path, token: str) -> None:
        installation = self._quiescence_installation
        name = "standard-install-pending.json"
        if installation is None or not self._mutation_token_matches(token, name):
            msg = "The installer transaction changed; explicit recovery is required."
            raise HeinzelError(msg)
        fingerprint = installation.startup_fingerprint()
        if (
            not re.fullmatch(r"[a-f0-9]{64}", fingerprint)
            or not installation.startup_state_matches()
            or installation.startup_fingerprint() != fingerprint
            or not self._quiescent_snapshot(binary, token)
            or installation.startup_fingerprint() != fingerprint
            or not installation.startup_state_matches()
            or not self._mutation_token_matches(token, name)
        ):
            msg = "The installation or daemon could not be verified; explicit recovery is required."
            raise HeinzelError(msg)
        (self.state_dir / name).unlink()

    def recover_quiescent_install(self, binary: Path) -> None:
        """Explicitly clear a standard-install interruption only after independently restored evidence matches.

        Raises:
            HeinzelError: If the record, installation or manager state cannot be verified.
        """
        with lock(self.state_dir / "lifecycle.lock") as lifecycle, lock(self.state_dir / "tick.lock") as tick:
            if not lifecycle or not tick:
                msg = "The daemon lifecycle is changing; retry recovery."
                raise HeinzelError(msg)
            try:
                with _open_verified_handover_file(self.state_dir / "standard-install-pending.json") as handle:
                    data = json.loads(handle.read(256))
                token = data["token"]
            except (OSError, ValueError, KeyError, TypeError, HeinzelError) as exc:
                msg = "The installer recovery record cannot be verified."
                raise HeinzelError(msg) from exc
            if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
                msg = "The installer recovery record cannot be verified."
                raise HeinzelError(msg)
            self._finish_quiescent_install(binary, token)

    def request_handover(self, previous: DaemonHandoverPlan) -> DaemonHandoverRequest:
        """Request a handover under the lifecycle writer lock.

        Raises:
            HeinzelError: If the service cannot be safely inspected.
        """
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            if not acquired or self._handover_pending():
                return DaemonHandoverRequest(
                    requested=False, reason="The daemon lifecycle is changing or requires recovery."
                )
            return self._request_handover_locked(previous)

    def _request_handover_locked(self, previous: DaemonHandoverPlan) -> DaemonHandoverRequest:
        """Ask only the unchanged active service to hand over; manager acceptance is not execution evidence.

        Raises:
            HeinzelError: If the manager rejects the request or service files cannot be inspected.
        """
        if type(previous) is not DaemonHandoverPlan:
            msg = "The pre-change daemon observation is invalid."
            raise HeinzelError(msg)
        if not previous.active or previous.platform != self._platform or previous.unit_signature is None:
            return DaemonHandoverRequest(requested=False, reason="The service was not active before the change.")
        current = self.capture_handover()
        if not current.active or current.unit_signature != previous.unit_signature:
            return DaemonHandoverRequest(
                requested=False, reason="The service state changed; no handover was requested."
            )
        if self._platform == "darwin":
            command = self._command("launchctl", "kickstart", f"gui/{self._uid()}/{LABEL}")
        else:
            command = self._command("systemctl", "--user", "try-restart", f"{SYSTEMD_NAME}.timer")
        request_id = secrets.token_hex(32)
        boot = self._current_boot_identity()
        fence = self._current_birth_fence()
        data: dict[str, str | int] = {
            "request_id": request_id,
            "boot": boot or "",
            "unit_signature": previous.unit_signature,
        }
        if fence is not None:
            data.update({f"birth_{key}": value for key, value in fence.items()})
        self._write_state("handover-request.json", data)
        result = self._runner(command)
        if result.returncode != 0:
            msg = f"The daemon handover request failed: {(result.stderr or result.stdout).strip()}"
            raise HeinzelError(msg)
        return DaemonHandoverRequest(
            requested=True,
            reason="The manager accepted the request; a later service tick must establish what ran.",
            request_id=request_id,
        )

    def handover_observation(self, request_id: str) -> DaemonHandoverObservation | None:
        """Read the current request's manager-bound tick observation, or remain pending."""
        request = self._read_state("handover-request.json")
        boot = request.get("boot")
        if (
            not re.fullmatch(r"[a-f0-9]{64}", request_id)
            or request.get("request_id") != request_id
            or not isinstance(boot, str)
            or not boot
            or self._current_boot_identity() != boot
            or self._continuity_fingerprint is None
            or self._handover_pending()
        ):
            return None
        path = self.state_dir / "handover-observation.json"
        try:
            with path.open("rb") as source:
                raw = source.read(MAX_OBSERVATION_BYTES + 1)
            if len(raw) > MAX_OBSERVATION_BYTES:
                return None
            result = TypeAdapter(DaemonHandoverObservation).validate_json(raw, strict=True)
            heartbeat = self._read_state("heartbeat.json")
            tick_at = datetime.fromisoformat(result.tick_at)
            age = self._clock() - tick_at
            if (
                result.request_id != request_id
                or result.old_unit_signature != request.get("unit_signature")
                or result.new_unit_signature != self._handover_signature()
                or not re.fullmatch(r"[a-f0-9]{64}", result.fingerprint)
                or len(result.contributions) > MAX_HANDOVER_CONTRIBUTIONS
                or not _heartbeat_covers(heartbeat, result.tick_at, result.version)
                or age < timedelta(0)
                or age > timedelta(minutes=STALE_TICKS * self._interval())
            ):
                return None
        except (OSError, ValueError, TypeError, HeinzelError):
            return None
        try:
            return result if self._continuity_fingerprint() == result.fingerprint else None
        except Exception:  # noqa: BLE001 - a changed or unavailable local observation remains pending
            return None

    def assess_handover(
        self,
        request_id: str,
        expected_version: str,
        expected_fingerprint: str,
        expected_contributions: tuple[tuple[str, str, str], ...],
    ) -> DaemonHandoverAssessment:
        """Compare a fresh active service observation with an explicit caller-owned target.

        This does not compare the caller's imported version or rewrite the historical install marker.

        Raises:
            HeinzelError: If the expected target is malformed or unbounded.
        """
        if (
            not expected_version
            or len(expected_version) > MAX_BOOT_IDENTITY
            or not re.fullmatch(r"[a-f0-9]{64}", expected_fingerprint)
            or len(expected_contributions) > MAX_HANDOVER_CONTRIBUTIONS
            or any(
                len(row) != HANDOVER_ROW_FIELDS or any(not item or len(item) > MAX_HANDOVER_TEXT for item in row)
                for row in expected_contributions
            )
            or len(set(expected_contributions)) != len(expected_contributions)
        ):
            msg = "The expected daemon handover target is invalid."
            raise HeinzelError(msg)
        try:
            if not self.capture_handover().active:
                return DaemonHandoverAssessment(status="pending", observed=None)
            observed = self.handover_observation(request_id)
        except (OSError, HeinzelError):
            observed = None
        if observed is None:
            return DaemonHandoverAssessment(status="pending", observed=None)
        matched = (
            observed.version == expected_version
            and observed.fingerprint == expected_fingerprint
            and sorted(observed.contributions) == sorted(expected_contributions)
        )
        return DaemonHandoverAssessment(status="matched" if matched else "mismatched", observed=observed)

    def _request_unit_matches(self, request: dict[str, str | int]) -> bool:
        signature = request.get("unit_signature")
        try:
            return isinstance(signature, str) and self._handover_signature() == signature
        except (OSError, HeinzelError):
            return False

    def _handover_signature(self, marker_override: bytes | None = None) -> str:
        """Bind an observation to the existing private unit and install marker, without following links.

        Raises:
            HeinzelError: If the unit cannot be safely observed.
        """
        digest = hashlib.sha256()
        try:
            for path in [*self._unit_paths(), self.state_dir / "install.json"]:
                with _open_verified_handover_file(path) as handle:
                    info = os.fstat(handle.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.getuid()
                        or info.st_mode & 0o022
                        or info.st_size > MAX_HANDOVER_FILE_BYTES
                    ):
                        _invalid_handover()
                    payload = handle.read(MAX_HANDOVER_FILE_BYTES + 1)
                    after = os.fstat(handle.fileno())
                    if len(payload) != info.st_size or (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    ) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                        _invalid_handover()
                if path == self.state_dir / "install.json" and marker_override is not None:
                    payload = marker_override
                digest.update(str(path).encode() + b"\0" + payload + b"\0")
        except OSError as exc:
            msg = "The existing daemon unit could not be verified; no handover was requested."
            raise HeinzelError(msg) from exc
        return digest.hexdigest()

    def uninstall(self) -> Uninstalled:
        """Uninstall under the lifecycle writer lock.

        Raises:
            HeinzelError: If another lifecycle writer is active or uninstall fails.
        """
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            if not acquired or self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery; inspect it before uninstalling."
                raise HeinzelError(msg)
            return self._uninstall_locked()

    def _uninstall_locked(self) -> Uninstalled:
        """Unload and remove the unit; receipts, logs and the pause flag stay. Nothing to do is not an error.

        Raises:
            HeinzelError: On an unsupported platform.
        """
        paths = [path for path in self._unit_paths() if path.exists()]
        if not paths:
            return Uninstalled(removed=[], commands=[])
        if self._platform == "darwin":
            commands = [self._command("launchctl", "bootout", f"gui/{self._uid()}/{LABEL}")]
        else:
            commands = [self._command("systemctl", "--user", "disable", "--now", f"{SYSTEMD_NAME}.timer")]
        for command in commands:  # failing because it wasn't loaded is fine: the goal is that it isn't
            self._runner(command)
        for path in paths:
            path.unlink()
        if self._platform == "linux":
            reload = self._command("systemctl", "--user", "daemon-reload")
            self._runner(reload)
            commands.append(reload)
        (self.state_dir / "install.json").unlink(missing_ok=True)
        (self.state_dir / "handover-pending.json").unlink(missing_ok=True)
        return Uninstalled(removed=paths, commands=commands)

    # Inspecting ------------------------------------------------------------------------------------------------------

    def status(self) -> DaemonStatus:
        """Installed, loaded, paused, last tick and failing jobs; runs launchctl or systemctl only when installed."""
        installed = self._platform in PLATFORMS and all(path.exists() for path in self._unit_paths())
        recorded = self._read_state("install.json")
        jobs = self.jobs()
        # install.json records the intended unit version, not the code that actually ran. The tick heartbeat is
        # written by the executing process and intentionally stays unknown for older versions without this field.
        heartbeat = self._read_state("heartbeat.json")
        version = str(heartbeat["version"]) if installed and isinstance(heartbeat.get("version"), str) else None
        installed_version = str(recorded["version"]) if installed and isinstance(recorded.get("version"), str) else None
        version_mismatch = installed and (
            self._handover_pending()
            or not self._handover_marker_valid(recorded)
            or (version is not None and version != __version__)
            or (installed_version is not None and installed_version != __version__)
            or (version is not None and installed_version is not None and version != installed_version)
        )
        return DaemonStatus(
            platform=self._platform,
            installed=installed,
            loaded=installed and self._loaded(),
            paused=self.paused,
            interval_minutes=int(recorded["interval_minutes"]) if "interval_minutes" in recorded else None,
            executable=Path(str(recorded["executable"])) if "executable" in recorded else None,
            last_tick=self._last_tick(),
            jobs=len(jobs),
            failing=[job.job for job in jobs if job.problem or job.last_disposition is Disposition.FAILED],
            version=version,
            installed_version=installed_version,
            version_mismatch=version_mismatch,
        )

    def _handover_pending(self) -> bool:
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
        elif not status.loaded:
            checks = [
                Check(
                    name="daemon",
                    status=Status.DEGRADED,
                    detail="installed but not loaded, so no ticks run",
                    fix="Run `hz system daemon install` again",
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
        if status.failing:
            checks.append(
                Check(
                    name="jobs",
                    status=Status.DEGRADED,
                    detail=f"failing: {', '.join(status.failing)}",
                    fix="See `hz system daemon jobs` and `hz system daemon logs`",
                )
            )
        return checks

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
        """Run the jobs that are due, or `job` now; launchd and systemd reach this through the CLI head.

        A tick from the local CLI is the owner's daemon, the identity approved schedules run under. MCP stays itself.
        """
        user = self._kernel.resolve(CurrentUser) if self._kernel.provides(CurrentUser) else None
        try:
            local_cli = user is not None and user.context().authentication == "cli"
        except HeinzelError:
            local_cli = False
        if user is None or not local_cli:
            return self._tick(dry_run=dry_run, job=job)
        with user.local("daemon"):
            return self._tick(dry_run=dry_run, job=job)

    def _tick(self, *, dry_run: bool = False, job: str | None = None) -> TickReport:
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
            if not lifecycle or self._handover_pending():
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
                    heartbeat: dict[str, str | int] = {"last_tick": now.isoformat()}
                    if self._service_manager_pid() == os.getpid():
                        heartbeat["version"] = __version__
                        self._finish_handover_locked(handover, now)
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
        logger.warning("Job %s is now %s; the old id works for one more minor release.", job, current)
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
            if self._platform == "darwin":
                command = self._command("launchctl", "list", LABEL)
                parser = "launchd"
            elif self._platform == "linux":
                command = self._command(
                    "systemctl",
                    "--user",
                    "show",
                    f"{SYSTEMD_NAME}.service",
                    "--property=MainPID",
                    "--value",
                )
                parser = "systemd"
            else:
                return None
            result = self._runner(command)
        except (OSError, HeinzelError):
            return None
        if result.returncode != 0:
            return None
        return _launchd_pid(result.stdout, LABEL) if parser == "launchd" else _reported_pid(result.stdout)

    def _timestamp(self, name: str, key: str) -> datetime | None:
        value = self._read_state(name).get(key)
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
        result = self._runner(self._command("uv", "tool", "dir", "--bin"))
        if result.returncode != 0:
            msg = f"`uv tool dir --bin` failed: {result.stderr.strip()}"
            raise HeinzelError(msg)
        shim = Path(result.stdout.strip()) / "hz"
        if not shim.exists() and not dry_run:
            msg = (
                f"{shim} doesn't exist. The daemon runs the uv tool installation of hz: "
                "`uv tool install git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel`."
            )
            raise HeinzelError(msg)
        return shim

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
            return {paths[0]: launchd_plist(executable, minutes, self.log_path.parent)}
        return {paths[0]: systemd_service(executable), paths[1]: systemd_timer(minutes)}

    def _load_commands(self) -> list[list[str]]:
        if self._platform == "darwin":
            return [self._command("launchctl", "bootstrap", f"gui/{self._uid()}", str(plist_path(self.unit_dir)))]
        return [
            self._command("systemctl", "--user", "daemon-reload"),
            self._command("systemctl", "--user", "enable", "--now", f"{SYSTEMD_NAME}.timer"),
        ]

    def _loaded(self) -> bool:
        if self._platform == "darwin":
            command = self._command("launchctl", "print", f"gui/{self._uid()}/{LABEL}")
        else:
            command = self._command("systemctl", "--user", "is-active", "--quiet", f"{SYSTEMD_NAME}.timer")
        return self._runner(command).returncode == 0

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
