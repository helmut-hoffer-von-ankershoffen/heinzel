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
import tomllib
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
from heinzel.kernel import (
    KERNEL_SECTION,
    DaemonHandoverAssessment,
    DaemonHandoverObservation,
    DaemonHandoverPlan,
    DaemonHandoverRequest,
    DaemonMutationScope,
    Installation,
    MaintenanceLocks,
    ProcessOutcome,
    host_maintenance_gate,
    private_directory,
    private_lock,
    private_read,
    private_write,
    tick_interval,
)
from heinzel.kernel._tool_uninstall import OwnedFile, owned_file, remove_owned_file
from heinzel.modules.system._phases import Phases
from heinzel.modules.system.daemon import _process_birth
from heinzel.modules.system.daemon import _schedule as schedule
from heinzel.modules.system.daemon._abandoned_mutation import host_boot, marker_token_matches, mutation_record
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
from heinzel.modules.system.daemon._ownership import (
    ManagerState,
    launchd_owned,
    profile_digest,
    profile_environment,
    systemd_owned,
    tick_environment,
)
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
SESSION_STEP = "Checking this is your Mac's desktop session"
NOTIFIER_STEP = "Setting up desktop notifications"
LOAD_FIX = "run `hz system daemon status` in a terminal for what the service manager says."
PLATFORMS = ("darwin", "linux")
"""launchd and systemd read unit files; they hold no secrets."""
MODULE_SECTION = "system.daemon"
MAX_SERVICE_MANAGER_OUTPUT = 1_000_000
MANAGER_TIMEOUT = 5
# launchd can defer a respawn for ten seconds (launchd.plist(5)); allow time for its reply as well.
OWNED_KICKSTART_TIMEOUT = 15
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
    """Whether the documented legacy list format holds no daemon record."""
    labels = _launchctl_records(output)
    return labels is not None and LABEL not in labels


def _launchctl_records(output: str) -> dict[str, str] | None:
    """Each label's PID column from the documented legacy list format; any unexpected record fails closed."""
    if len(output.encode("utf-8")) > MAX_SERVICE_MANAGER_OUTPUT:
        return None
    lines = output.splitlines()
    if not lines or lines[0].split() != ["PID", "Status", "Label"]:
        return None
    labels: dict[str, str] = {}
    expected_columns = 3
    for line in lines[1:]:
        parts = line.split()
        if (
            len(parts) != expected_columns
            or (parts[0] != "-" and not parts[0].isdigit())
            or not re.fullmatch(r"-?[0-9]+", parts[1])
            or parts[2] in labels
        ):
            return None
        labels[parts[2]] = parts[0]
    return labels


def _manager_environment(command: list[str]) -> dict[str, str]:
    if command[0] != "/usr/bin/systemctl":
        return {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(Path.home())}
    runtime = f"/run/user/{os.getuid()}"
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "XDG_RUNTIME_DIR": runtime,
    }


def _manager_deadline(command: list[str]) -> float:
    own_kickstart = command == ["/bin/launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"]
    return time.monotonic() + (OWNED_KICKSTART_TIMEOUT if own_kickstart else MANAGER_TIMEOUT)


def bounded_manager_command(
    command: list[str], *, deadline: float | None = None, env: dict[str, str] | None = None
) -> ProcessOutcome:
    """Bound a manager child; never signal a reaped or unknown process."""
    process = subprocess.Popen(  # noqa: S603 - fixed absolute launchctl and fixed subcommands
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env if env is not None else _manager_environment(command),
    )
    deadline = deadline if deadline is not None else _manager_deadline(command)
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


# Retain the internal lifecycle probe name used by the Linux session acceptance harness.
_quiescence_command = bounded_manager_command


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


@dataclass(frozen=True)
class DaemonRemoval:
    """One held owned daemon plan; its durable coordinator controls stop and final cleanup."""

    files: tuple[OwnedFile, ...]
    stop: Callable[[str], None]
    finish: Callable[[], None]
    manager_units: dict[str, dict[str, object]]


def handover_absolute_path(path: Path) -> Path:
    """Resolve the existing physical handover path for sibling recovery code."""
    return _handover_absolute_path(path)


@contextmanager
def verified_handover_file(path: Path) -> Generator[BinaryIO, None, None]:
    """Open a file through the existing bounded handover authority boundary.

    Yields:
        The physically verified bounded handover file handle.
    """
    with _open_verified_handover_file(path) as handle:
        yield handle


def launchctl_records(output: str) -> dict[str, str] | None:
    """Parse the same complete service-manager listing used by normal lifecycle checks."""
    return _launchctl_records(output)


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
        return cls(
            kernel,
            config.settings(Settings, KERNEL_SECTION),
            config.settings(DaemonSettings, MODULE_SECTION),
            continuity_fingerprint=installation.startup_fingerprint,
            quiescence_installation=installation,
            profile_config=config,
        )

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

    def install(self, interval: timedelta, *, dry_run: bool = False, notifier: bool = True) -> Installed:
        """Install under the lifecycle writer lock, or report a concurrent change.

        Raises:
            HeinzelError: If another lifecycle writer is active or installation fails.
        """
        self._minutes(interval)
        self._unit_paths()
        if dry_run:
            return self._install_locked(interval, dry_run=True, notifier=notifier)
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
            if not acquired or not self._clear_after_recovery_locked():
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

    def _mutation_token_matches(self, token: str, name: str = "mutation-pending.json") -> bool:
        try:
            with _open_verified_handover_file(self.state_dir / name) as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                    return False
                raw = handle.read(256)
                if name == "mutation-pending.json":
                    return marker_token_matches(raw, token)
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

    @property
    def recovery_platform(self) -> str:
        """The platform selected by this daemon authority."""
        return self._platform

    @property
    def recovery_profile(self) -> dict[str, str]:
        """A copy of the selected profile routing for explicit recovery inspection."""
        return dict(self._profile)

    @property
    def recovery_installation(self) -> Installation | None:
        """The existing installation bound to this daemon's quiescence checks."""
        return self._quiescence_installation

    def recovery_uid(self) -> int:
        """Observe the same user identity used by normal daemon authority checks."""
        return self._uid()

    def recovery_now(self) -> datetime:
        """Observe this service's clock for completed-history validation."""
        return self._clock()

    def recovery_token_matches(self, token: str, name: str) -> bool:
        """Check the exact existing mutation token without modifying its gate."""
        return self._mutation_token_matches(token, name)

    @contextmanager
    def recovery_writer(self) -> Generator[None]:
        """Hold the same lifecycle writer used by ordinary daemon mutations."""
        with self._lifecycle_writer():
            yield

    def owned_manager_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        """Use only the fixed, bounded manager in this verified user session, before and after the request.

        Raises:
            HeinzelError: If manager authority changes or the request cannot be observed completely.
        """
        expected = "/bin/launchctl" if self._platform == "darwin" else "/usr/bin/systemctl"
        if self._platform not in PLATFORMS or not command or command[0] != expected:
            msg = "The daemon request is not addressed to the fixed local user manager."
            raise HeinzelError(msg)
        identity = self._linux_manager_identity() if self._platform == "linux" else None
        if (self._platform == "linux" and identity is None) or (
            self._platform == "darwin" and not self._mac_session_verified()
        ):
            msg = "The daemon's user service manager cannot be verified; nothing was changed."
            raise HeinzelError(msg)
        result = self._quiescence_probe(command)
        stable = (
            self._linux_manager_identity() == identity if self._platform == "linux" else self._mac_session_verified()
        )
        if not stable or result.truncated or result.returncode is None:
            msg = "The daemon manager request is uncertain; inspect its actual state before retrying."
            raise HeinzelError(msg)
        return subprocess.CompletedProcess(command, result.returncode, result.stdout, result.stderr)

    def owned_unit_state(self, binary: Path, *, allow_absent: bool = False) -> bool:
        """Whether exact safe files, receipt, profile and tool authority agree; absence is explicit."""
        present = not all(_safely_absent(path) for path in (*self._unit_paths(), self.state_dir / "install.json"))
        return (present or allow_absent) and self._quiescence_unit_state(binary)

    def owned_manager_state(self, binary: Path) -> ManagerState:
        """Observe an owned registration and activation, or verified absence, with its current tick PID.

        Raises:
            HeinzelError: If files, manager argv/profile or user-session authority cannot be verified.
        """
        if not self.owned_unit_state(binary, allow_absent=True):
            msg = "The daemon files belong to another profile or were changed; nothing was changed."
            raise HeinzelError(msg)
        executable = binary / "hz"
        environment = tick_environment(executable, self._profile)
        if self._platform == "darwin":
            result = self.owned_manager_command(["/bin/launchctl", "print", f"gui/{self._uid()}/{LABEL}"])
            if result.returncode != 0:
                records = self.owned_manager_command(["/bin/launchctl", "list"])
                listing = _launchctl_records(records.stdout) if records.returncode == 0 else None
                if listing is None or LABEL in listing:
                    msg = "The daemon's manager registration is unknown; nothing was changed."
                    raise HeinzelError(msg)
                return ManagerState(registered=False, active=False, enabled=False)
            state = launchd_owned(result.stdout, plist_path(self.unit_dir), executable, environment)
        else:
            outputs: list[str] = []
            for unit in (f"{SYSTEMD_NAME}.service", f"{SYSTEMD_NAME}.timer"):
                result = self.owned_manager_command(
                    [
                        "/usr/bin/systemctl",
                        "--user",
                        "show",
                        unit,
                        "--property=Id,LoadState,FragmentPath,DropInPaths,ExecStart,Triggers,Environment,WorkingDirectory,ActiveState,UnitFileState,MainPID,ControlPID,Job",
                    ]
                )
                if result.returncode != 0:
                    msg = "The daemon's user manager unit cannot be inspected; nothing was changed."
                    raise HeinzelError(msg)
                outputs.append(result.stdout)
            state = systemd_owned(outputs[0], outputs[1], systemd_paths(self.unit_dir), executable, environment)
        if state.registered and not self.owned_unit_state(binary):
            msg = "A daemon label is loaded without this profile's exact owned files; nothing was changed."
            raise HeinzelError(msg)
        return state

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
                recorded.get("profile") != profile_digest(self._profile)
                or recorded.get("executable") != str(executable)
                or recorded.get("platform") != self._platform
                or type(minutes) is not int
                or minutes <= 0
            ):
                return False
            # On macOS uv uninstall removes the tool environment and shims, retaining an owned stopped unit.
            # The enclosing snapshot still requires a trusted empty destination and actual manager absence.
            if self._platform != "darwin" or not _safely_absent(self._quiescence_installation.prefix):
                self._quiescence_installation.verify_tool_destination(
                    self._quiescence_installation.prefix.parent, binary
                )
            return all(path.read_text() == content for path, content in self._unit_files(executable, minutes).items())
        except (OSError, ValueError, HeinzelError):  # UnicodeError and a marker's impossible interval are ValueErrors
            return False

    def _recover_retained_unit(self, binary: Path) -> None:
        """Retain only a profile-bound owned stopped unit; unknown legacy definitions are never retired.

        A released byte format and a dead PID do not establish which profile or loaded job owns a fixed label.
        Recovery never bootouts, moves or deletes those definitions on the basis of that weaker evidence.
        """
        self._quiescence_advice = None
        if all(_safely_absent(path) for path in (*self._unit_paths(), self.state_dir / "install.json")):
            return
        self._quiescence_advice = (
            "The retained daemon definition or its profile cannot be verified. Inspect the user service manager, "
            "the selected configuration and existing unit before changing them; "
            "no definition was moved or unloaded. "
            "For an older daemon, follow the explicit recovery guide: https://heinzel.team/DAEMON_RECOVERY/."
        )
        if self.owned_unit_state(binary):
            self._quiescence_advice = None

    def _quiescent_snapshot(
        self,
        binary: Path,
        token: str | None = None,
        *,
        gate: Path | None = None,
        compatibility_gates: tuple[Path, ...] = (),
    ) -> bool:
        self._quiescence_problem = "The selected installation destination could not be verified."
        if self._platform not in {"darwin", "linux"} or not self._quiescence_target(binary):
            return False
        paths = [
            self.state_dir / name
            for name in ("handover-pending.json", "handover-request.json", "mutation-pending.json")
        ]

        def pending_valid() -> bool:
            if gate is not None:
                installation = self._quiescence_installation
                return (
                    installation is not None
                    and gate == host_maintenance_gate(installation.prefix)
                    and (
                        private_read(self.state_dir / "standard-install-pending.json")
                        == json.dumps({"token": token}, indent=2) + "\n"
                        if compatibility_gates == (self.state_dir / "standard-install-pending.json",)
                        else not compatibility_gates
                        and _safely_absent(self.state_dir / "standard-install-pending.json")
                    )
                    and private_read(gate) == json.dumps({"token": token}, indent=2) + "\n"
                )
            if self._quiescence_installation is not None and not _safely_absent(
                host_maintenance_gate(self._quiescence_installation.prefix)
            ):
                return False
            if token is None:
                return _safely_absent(self.state_dir / "standard-install-pending.json")
            return self._mutation_token_matches(token, "standard-install-pending.json")

        self._quiescence_problem = "A previous daemon or installation transaction requires recovery."
        if not (pending_valid() and all(_safely_absent(path) for path in paths)):
            return False
        self._quiescence_problem = "The retained daemon unit or install receipt could not be verified."
        if not self._quiescence_unit_state(binary):
            return False
        if self._platform == "linux":
            return self._linux_quiescent_snapshot(binary, pending_valid, paths)
        if not self._mac_manager_absent():
            return False
        self._quiescence_problem = "The installation or daemon evidence changed during verification."
        return (
            pending_valid()
            and all(_safely_absent(path) for path in paths)
            and self._quiescence_unit_state(binary)
            and self._quiescence_target(binary)
        )

    def _mac_session_verified(self) -> bool:
        try:
            results = [
                self._quiescence_probe(command)
                for command in (["/bin/launchctl", "managername"], ["/bin/launchctl", "manageruid"])
            ]
        except (OSError, ValueError, HeinzelError):
            return False
        name, uid = results
        return (
            name.returncode == uid.returncode == 0
            and name.stdout.strip() == "Aqua"
            and uid.stdout.strip() == str(self._uid())
        )

    def _mac_manager_absent(self) -> bool:
        commands = [
            ["/bin/launchctl", "managername"],
            ["/bin/launchctl", "manageruid"],
            ["/bin/launchctl", "list"],
            ["/bin/launchctl", "managername"],
            ["/bin/launchctl", "manageruid"],
        ]
        self._quiescence_problem = "macOS service-manager inspection failed or returned incomplete evidence."
        try:
            results = [self._quiescence_probe(command) for command in commands]
            if any(result.returncode != 0 or result.truncated for result in results):
                return False
            name1, uid1, listing, name2, uid2 = [result.stdout for result in results]
            if not (name1.strip() == name2.strip() == "Aqua" and uid1.strip() == uid2.strip() == str(self._uid())):
                self._quiescence_problem = (
                    "The current process is not in a verified macOS desktop session for this user."
                )
                return False
            self._quiescence_problem = (
                "macOS did not confirm daemon absence; the service is loaded or its list is invalid."
            )
            return _launchctl_quiescent_output(listing)
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
                if tick and self._platform == "darwin" and self._quiescence_installation is not None:
                    with suppress(OSError, ValueError, HeinzelError):  # the snapshot below still decides
                        self._recover_retained_unit(binary)
                if not tick or not self._quiescent_snapshot(binary):
                    if self._platform == "linux":
                        msg = (
                            "The daemon is not proven quiescent. In your Linux user session, check that the user "
                            "service manager is available and inspect `hz system daemon status`; ensure both the "
                            "service and timer are stopped before retrying."
                        )
                    elif self._platform == "darwin":
                        problem = self._quiescence_problem if tick else "The daemon tick lock is busy."
                        msg = (
                            f"The daemon is not proven quiescent. {problem} "
                            f"{self._quiescence_advice + ' ' if self._quiescence_advice else ''}"
                            "Inspect `uvx --from heinzel hz system daemon status` before retrying. "
                            "This inspection command also works when hz is not installed."
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
        try:
            current = self.capture_handover()
        except (OSError, HeinzelError):
            return DaemonHandoverRequest(
                requested=False, reason="The owned service identity changed; no handover was requested."
            )
        if not current.active or current.unit_signature != previous.unit_signature:
            return DaemonHandoverRequest(
                requested=False, reason="The service state changed; no handover was requested."
            )
        if self._platform == "darwin":
            command = ["/bin/launchctl", "kickstart", f"gui/{self._uid()}/{LABEL}"]
        else:
            command = ["/usr/bin/systemctl", "--user", "try-restart", f"{SYSTEMD_NAME}.timer"]
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
        result = self.owned_manager_command(command)
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

    def idle_package_handover_matches(self, request: dict[str, str | int], binary: Path) -> bool:
        """Verify the exact same-boot request and its unchanged, idle, active manager registration.

        Raises:
            HeinzelError: If the manager authority or loaded unit cannot be verified.
        """
        boot = self._current_boot_identity()
        fence = self._request_birth_fence(request)
        birth = self._process_birth()
        return (
            self._platform == "darwin"
            and self._uid() == os.getuid()
            and bool(boot)
            and request.get("boot") == boot
            and fence is not None
            and birth is not None
            and _process_birth.after_request(fence, birth)
            and self._request_unit_matches(request)
            and self.owned_unit_state(binary)
            and self.owned_manager_state(binary) == ManagerState(registered=True, active=True, enabled=True)
        )

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

    def _offline_probe(self, command: list[str]) -> str | None:
        result = self.owned_manager_command(command)
        if result.returncode != 0:
            return None
        return result.stdout

    def _offline_files(self, binary: Path) -> list[OwnedFile]:
        if not self.owned_unit_state(binary, allow_absent=True):
            msg = "The daemon's service files are foreign or changed; they were left unchanged."
            raise HeinzelError(msg)
        paths = [*self._unit_paths(), self.state_dir / "install.json"]
        return [owned_file(path) for path in paths if os.path.lexists(path)]

    def _offline_stop(
        self,
        binary: Path,
        files: list[OwnedFile],
        state: ManagerState,
        token: str,
        *,
        fence: tuple[Path | None, tuple[Path, ...]] = (None, ()),
    ) -> None:
        from heinzel.modules.system.daemon._units import LABEL, SYSTEMD_NAME  # noqa: PLC0415 - fixed manager names

        gate, compatibility_gates = fence
        if any(owned_file(file.path) != file for file in files) or self.owned_manager_state(binary) != state:
            msg = "The daemon changed before stopping; it was left unchanged."
            raise HeinzelError(msg)
        if state.registered:
            command = (
                ["/bin/launchctl", "bootout", f"gui/{self._uid()}/{LABEL}"]
                if self._platform == "darwin"
                else [
                    "/usr/bin/systemctl",
                    "--user",
                    "disable",
                    "--now",
                    f"{SYSTEMD_NAME}.timer",
                    f"{SYSTEMD_NAME}.service",
                ]
            )
            if self._offline_probe(command) is None:
                msg = "The owned daemon could not be stopped; its files and installation were retained."
                raise HeinzelError(msg)
        if not self._quiescent_snapshot(binary, token, gate=gate, compatibility_gates=compatibility_gates):
            msg = (
                "The daemon is not proven quiescent; the installation was retained. Inspect its local service manager."
            )
            raise HeinzelError(msg)

    def _offline_manager_units(self, binary: Path, files: list[OwnedFile]) -> dict[str, dict[str, object]]:
        from heinzel.modules.system.daemon._units import TICK  # noqa: PLC0415 - shared fixed argv

        if self._platform != "linux" or not files:
            return {}
        service, timer = self._unit_paths()
        executable = binary / "hz"
        environment = tick_environment(executable, self._profile)
        return {
            service.name: {
                "fragment": str(service),
                "program": str(executable),
                "arguments": f"{executable} {' '.join(TICK)}",
                "environment": environment,
                "directory": environment["HOME"],
            },
            timer.name: {"fragment": str(timer), "triggers": service.name, "disabled": True},
        }

    @contextmanager
    def offline_removal_guard(
        self,
        binary: Path,
        *,
        locks: MaintenanceLocks | None = None,
        gate: Path | None = None,
        compatibility_gates: tuple[Path, ...] = (),
    ) -> Generator[DaemonRemoval, None, None]:
        """Hold daemon lifecycle/tick authority while a durable coordinator stops and removes one tool.

        Yields:
            Captured owned definitions and separate stop/final-cleanup actions.

        Raises:
            HeinzelError: If ownership, quiescence or final cleanup cannot be established.
            OSError: If physical local authority cannot be inspected.
        """
        lifecycle, tick = self.state_dir / "lifecycle.lock", self.state_dir / "tick.lock"
        with (
            locks.guard(lifecycle) if locks is not None else private_lock(lifecycle),
            locks.guard(tick) if locks is not None else private_lock(tick),
        ):
            if self._handover_pending():
                msg = "A Heinzel lifecycle change requires recovery; nothing was uninstalled."
                raise HeinzelError(msg)
            files = self._offline_files(binary)
            state = self.owned_manager_state(binary)

            def stop(token: str) -> None:
                self._offline_stop(binary, files, state, token, fence=(gate, compatibility_gates))

            def finish() -> None:
                if self._quiescence_installation is None or not _safely_absent(self._quiescence_installation.prefix):
                    msg = "The Heinzel tool removal is incomplete; its stopped service files were retained."
                    raise HeinzelError(msg)
                for captured in files:
                    remove_owned_file(captured)
                if (
                    self._platform == "linux"
                    and self._offline_probe(["/usr/bin/systemctl", "--user", "daemon-reload"]) is None
                ):
                    msg = "Heinzel was removed, but the user manager needs a local reload; user data was kept."
                    raise HeinzelError(msg)

            yield DaemonRemoval(tuple(files), stop, finish, self._offline_manager_units(binary, files))

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
            last_tick=self._last_tick(),
            jobs=len(jobs),
            failing=[job.job for job in jobs if job.problem or job.last_disposition is Disposition.FAILED],
            version=version,
            installed_version=installed_version,
            installed_at=self._timestamp("install.json", "installed_at") if installed else None,
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
            logger.info(
                "The daemon's registration record names another version; the next tick records %s.", __version__
            )
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
            binary = self._shim(dry_run=False).parent
            state = self.owned_manager_state(binary)
        except (OSError, HeinzelError):
            return None
        else:
            return state.pid if state.registered else None

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
