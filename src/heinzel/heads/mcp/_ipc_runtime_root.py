"""Host-selected MCP locator storage and observational maintenance receipts."""

import hashlib
import json
import os
import secrets
import stat
import sys
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import JsonValue

from heinzel.kernel import CurrentUser, HeinzelError
from heinzel.kernel._host_maintenance import MaintenanceLocks, host_maintenance_lock
from heinzel.kernel._installation_mutations import installation_mutation_pending

_DARWIN_USER_TEMP_DIR = 65537  # Darwin's _CS_DARWIN_USER_TEMP_DIR, absent from Python's string table.
_ROOT_BYTES = 86  # Reserve 17 bytes for /g-<12 hex characters>/s.
_RECORD_LIMIT = 4096
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_REFUSED = "The installed MCP runtime root is unavailable or changed"


@dataclass(frozen=True, slots=True)
class InstalledMcpRuntime:
    """SDK prefix and private profile state selected by trusted native activation, never by a vendor or CLI flag."""

    prefix: Path
    state: Path


def _os_runtime_base() -> Path:
    if sys.platform == "linux":
        return Path("/run/user") / str(os.getuid())
    if sys.platform == "darwin":
        value = os.confstr(_DARWIN_USER_TEMP_DIR)
        if value is not None:
            path = Path(value)
            # Canonicalize the system /var spelling without following user-controlled links.
            if path.is_relative_to("/var/folders"):
                path = Path("/private") / path.relative_to("/")
            if path.is_relative_to("/private/var/folders") and ".." not in path.parts:
                return path
    raise HeinzelError(_REFUSED)


def _identity(descriptor: int) -> tuple[int, int]:
    info = os.fstat(descriptor)
    return info.st_dev, info.st_ino


def _locator_name() -> str:
    return "hz-m-" + secrets.token_hex(8)


@contextmanager
def _physical_directory(path: Path, *, private: bool = False) -> Generator[int]:
    if not path.is_absolute() or ".." in path.parts or path.anchor != "/":
        raise HeinzelError(_REFUSED)
    descriptor = os.open("/", _DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in {0, os.getuid()} or (info.st_mode & 0o022 and not sticky_root):
                raise HeinzelError(_REFUSED)
        if private:
            CurrentUser.verify_private(os.fstat(descriptor), directory=True)
        yield descriptor
    finally:
        os.close(descriptor)


def installation_directories(installation: InstalledMcpRuntime) -> tuple[tuple[int, int], tuple[int, int]]:
    """Observe the trusted SDK and private profile directory identities for lifecycle review.

    Raises:
        HeinzelError: If the SDK or profile directories are unavailable or unsafe.
    """
    try:
        with (
            _physical_directory(installation.prefix) as prefix,
            _physical_directory(installation.state, private=True) as state,
        ):
            return _identity(prefix), _identity(state)
    except OSError:
        raise HeinzelError(_REFUSED) from None


def _read_record(directory: int, name: str) -> bytes | None:
    try:
        descriptor = CurrentUser.open_private(directory, name, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as source:
        data = source.read(_RECORD_LIMIT + 1)
    if len(data) > _RECORD_LIMIT:
        raise HeinzelError(_REFUSED)
    return data


def _encode(data: dict[str, JsonValue]) -> bytes:
    encoded = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    if len(encoded) > _RECORD_LIMIT:
        raise HeinzelError(_REFUSED)
    return encoded


def _record(directory: int, name: str, data: bytes) -> None:
    descriptor = CurrentUser.open_private(directory, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(descriptor, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())
    os.fsync(directory)


@dataclass(slots=True)
class McpRuntimeLocator:
    """Retain locator storage and fence activation through the SDK maintenance lock."""

    path: Path
    descriptor: int = field(repr=False)
    _installation: InstalledMcpRuntime = field(repr=False)
    _inventory: int = field(repr=False)
    _record_name: str = field(repr=False)
    _locks: MaintenanceLocks = field(repr=False)
    _anchors: tuple[tuple[Path, int, bool], ...] = field(repr=False)
    _storage: dict[str, JsonValue] = field(repr=False)
    _observation: bytes | None = field(default=None, init=False, repr=False)

    def require_current(self) -> None:
        """Refuse changed anchors or a maintenance fence before using locator storage.

        Raises:
            HeinzelError: If an anchor or observation changed or the host is under maintenance.
        """
        lock = host_maintenance_lock(self._installation.prefix)
        try:
            with self._locks.guard(lock):
                for path, retained, private in self._anchors:
                    with _physical_directory(path, private=private) as current:
                        if _identity(current) != _identity(retained):
                            raise HeinzelError(_REFUSED)
                if (
                    self._observation is not None
                    and _read_record(self._inventory, self._record_name) != self._observation
                ):
                    raise HeinzelError(_REFUSED)
                if installation_mutation_pending(self._installation.prefix, self._installation.state):
                    raise HeinzelError(_REFUSED)
        except OSError:
            raise HeinzelError(_REFUSED) from None

    def observe_generation(
        self,
        locator: Path,
        directory: tuple[int, int],
        socket_node: tuple[int, int],
        registration: str,
        generation: int,
    ) -> None:
        """Commit one observation of the retained listener before publishing the supervisor.

        The receipt grants no admission, adoption or deletion rights. An incomplete write is left in
        inventory and the caller retires its unpublished listener. Activation never restores a receipt.

        Raises:
            HeinzelError: If the generation or maintenance inventory cannot be verified or recorded.
        """
        self.require_current()
        leaf = locator.parent.name
        if (
            self._observation is not None
            or locator.parent.parent != self.path
            or locator.name != "s"
            or not leaf.startswith("g-")
        ):
            raise HeinzelError(_REFUSED)
        data: dict[str, JsonValue] = {
            "schema": 1,
            "kind": "mcp-locator-observation",
            "storage": self._storage,
            "registration": registration,
            "generation": generation,
            "locator": os.fspath(locator),
            "directory": list(directory),
            "socket": list(socket_node),
        }
        try:
            observation = _encode(data)
            os.fsync(self.descriptor)
            _record(self._inventory, self._record_name, observation)
            self._observation = observation
            self.require_current()
        except OSError:
            raise HeinzelError(_REFUSED) from None


def _provision(
    installation: InstalledMcpRuntime, stack: ExitStack, locks: MaintenanceLocks, prefix: int, state: int
) -> McpRuntimeLocator:
    lock = host_maintenance_lock(installation.prefix)
    if installation_mutation_pending(installation.prefix, installation.state):
        raise HeinzelError(_REFUSED)
    inventory = stack.enter_context(_physical_directory(lock.parent, private=True))
    base = _os_runtime_base()
    base_fd = stack.enter_context(_physical_directory(base, private=True))
    sdk, profile = os.fspath(installation.prefix), os.fspath(installation.state)
    key = hashlib.sha256(os.fsencode(sdk) + b"\0" + os.fsencode(profile)).hexdigest()
    name = _locator_name()
    path = base / name
    if len(os.fsencode(path)) > _ROOT_BYTES:
        msg = "The installed MCP runtime root exceeds the Unix socket path budget"
        raise HeinzelError(msg)
    record_name = f"mcp-activation-{key}-{name}.json"
    os.mkdir(name, mode=0o700, dir_fd=base_fd)  # A pre-existing entry refuses this activation.
    descriptor = os.open(name, _DIR_FLAGS, dir_fd=base_fd)
    stack.callback(os.close, descriptor)
    CurrentUser.verify_private(os.fstat(descriptor), directory=True)
    # mkdir/open cannot prove creation provenance against another process under the same UID.
    # This descriptor bounds subsequent creation-only operations. It grants no authority to the path.
    os.fsync(base_fd)
    data: dict[str, JsonValue] = {
        "platform": sys.platform,
        "uid": os.getuid(),
        "sdk": sdk,
        "sdk_identity": list(_identity(prefix)),
        "profile": profile,
        "profile_identity": list(_identity(state)),
        "os_root": os.fspath(base),
        "os_root_identity": list(_identity(base_fd)),
        "locator_root": os.fspath(path),
        "observed_directory": list(_identity(descriptor)),
    }
    anchors = (
        (installation.prefix, prefix, False),
        (installation.state, state, True),
        (lock.parent, inventory, True),
        (base, base_fd, True),
        (path, descriptor, True),
    )
    return McpRuntimeLocator(path, descriptor, installation, inventory, record_name, locks, anchors, data)


@contextmanager
def mcp_runtime_locator(installation: InstalledMcpRuntime) -> Generator[McpRuntimeLocator]:
    """Hold the SDK maintenance fence and retain fresh per-activation locator storage.

    The already-authenticated isolated runtime supplies the installation binding. This scope stays locked
    until its supervisor exits, pending a native manager registration that maintenance can stop itself.

    Raises:
        HeinzelError: If isolation, maintenance authority, inventory or the OS runtime root is unavailable.

    Yields:
        A bounded locator directory descriptor selected by the host.
    """
    if not sys.flags.isolated or os.getuid() != os.geteuid() or installation.state.is_relative_to(installation.prefix):
        raise HeinzelError(_REFUSED)
    with ExitStack() as stack:
        try:
            lock = host_maintenance_lock(installation.prefix)
            # Verify native paths before the existing maintenance lock creates its own state outside the SDK.
            prefix = stack.enter_context(_physical_directory(installation.prefix))
            state = stack.enter_context(_physical_directory(installation.state, private=True))
            locks = stack.enter_context(MaintenanceLocks((lock,)))
            with locks.guard(lock):
                runtime = _provision(installation, stack, locks, prefix, state)
            runtime.require_current()
        except (OSError, ValueError):
            raise HeinzelError(_REFUSED) from None
        yield runtime
