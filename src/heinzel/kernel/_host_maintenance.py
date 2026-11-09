"""Typed host-owned integration inventory and cooperative SDK-wide maintenance fencing (ADR 0133)."""

import hashlib
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Never, Self

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._private_control import private_directory, private_lock
from heinzel.kernel._services import ServiceContract
from heinzel.kernel._tool_uninstall import OwnedFile, owned_file, remove_owned_file
from heinzel.kernel._uninstall_recovery import manager_identity_matches, manager_properties

STOP_SECONDS = 5.0
STOP_POLL_SECONDS = 0.1


def host_maintenance_directory(prefix: Path) -> Path:
    """Select a stable owner-only authority outside the SDK that removal itself cannot erase."""
    digest = hashlib.sha256(str(prefix).encode()).hexdigest()[:24]
    return prefix.parent / ".heinzel-maintenance" / digest


def host_maintenance_gate(prefix: Path) -> Path:
    """The single durable removal gate shared by every profile using this exact SDK."""
    return host_maintenance_directory(prefix) / "pending.json"


def host_maintenance_lock(prefix: Path) -> Path:
    """Serialize inventory, setup and worker admission across profiles of this exact SDK."""
    return host_maintenance_directory(prefix) / "lifecycle.lock"


class HostServiceManager(ServiceContract):
    """Fixed executable, UID/session/bus verified manager authority; never an ambient PATH command."""

    def command(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        """Execute a bounded request through the registered trusted system manager implementation."""
        raise NotImplementedError


class MaintenanceLocks:
    """A live, deterministic union of physical private locks; a caller cannot request an unchecked bypass."""

    def __init__(self, paths: Sequence[Path]) -> None:
        self.paths = tuple(sorted(set(paths), key=str))
        self._stack: ExitStack | None = None
        self._identities: dict[Path, tuple[int, int]] = {}

    def __enter__(self) -> Self:
        stack = ExitStack()
        try:
            for path in self.paths:
                missing: list[Path] = []
                parent = path.parent
                while not os.path.lexists(parent):
                    missing.append(parent)
                    parent = parent.parent
                lock = stack.enter_context(private_lock(path))
                for directory in {path.parent, *(item.parent for item in missing), *missing}:
                    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                info = os.fstat(lock)
                self._identities[path] = info.st_dev, info.st_ino
            self._stack = stack
            for path in self.paths:
                with self.guard(path):
                    pass
        except BaseException:
            stack.close()
            self._stack = None
            self._identities.clear()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        if self._stack is not None:
            self._stack.close()
        self._stack = None
        self._identities.clear()

    @contextmanager
    def guard(self, path: Path) -> Generator[None]:
        """Require this exact still-held lock, including its current physical inode.

        Raises:
            HeinzelError: If this operation does not retain the exact physical lock.
        """
        info = path.lstat()
        if (
            self._stack is None
            or self._identities.get(path) != (info.st_dev, info.st_ino)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            msg = "The integration maintenance lock is not held by this operation."
            raise HeinzelError(msg)
        private_directory(path.parent, create=False)
        yield

    def identity(self, path: Path) -> tuple[int, int]:
        """Return the physical identity of the exact verified held lock."""
        with self.guard(path):
            return self._identities[path]


@dataclass(frozen=True, slots=True)
class HostIntegrationScope:
    """Complete owner namespace to rescan with stdlib-only recovery, including an empty inventory."""

    directory: Path
    file_prefix: str
    manager_prefix: str


@dataclass(frozen=True, slots=True)
class HostIntegration:
    """Bounded plain-data ownership of one exact manager registration; no persisted executable callbacks."""

    name: str
    prefix: Path
    platform: str
    files: tuple[OwnedFile, ...]
    locks: tuple[Path, ...]
    unit: Path
    program: Path
    arguments: tuple[str, ...]
    environment: dict[str, str]
    directory: Path

    def manager_facts(self) -> dict[str, object]:
        """Plain immutable facts hash-bound into standalone recovery before any stop."""
        return {
            "fragment": str(self.unit),
            "program": str(self.program),
            "arguments": " ".join(self.arguments),
            "environment": self.environment,
            "directory": str(self.directory),
            "disabled": True,
        }


class HostMaintenanceIntegration(ServiceContract):
    """A built-in owner supplies every profile of its persistent integrations without resolving providers."""

    def discovery_scopes(self) -> tuple[HostIntegrationScope, ...]:
        """Describe bounded definition/manager namespaces even when no integration is currently installed."""
        raise NotImplementedError

    def inventory(self, prefix: Path, binary: Path, manager: HostServiceManager) -> tuple[HostIntegration, ...]:
        """Capture all matching owned definitions, refusing ambiguous or foreign recorded identities."""
        raise NotImplementedError

    def guard(
        self, integration: HostIntegration, manager: HostServiceManager, locks: MaintenanceLocks
    ) -> "HostIntegrationRemoval":
        """Verify the captured inventory under the complete held lock union before any effect."""
        return HostIntegrationRemoval(integration, manager, locks)


MANAGER_COLUMNS = 3
MAX_MANAGER_BYTES = 1_000_000


class HostIntegrationRemoval:
    """Runtime-only verified stop/final-delete plan; recovery retains only the descriptor's plain data."""

    def __init__(
        self,
        integration: HostIntegration,
        manager: HostServiceManager,
        locks: MaintenanceLocks,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.integration, self.manager, self.locks = integration, manager, locks
        self._clock, self._sleep = clock, sleep
        self._verify_files()
        self._state = self._observe()

    def _verify_files(self) -> None:
        for path in self.integration.locks:
            with self.locks.guard(path):
                pass
        if any(owned_file(file.path) != file for file in self.integration.files):
            msg = "An owned integration changed during inventory; nothing was removed."
            raise HeinzelError(msg)

    def _observe(self) -> tuple[bool, int | None, bool]:
        return self._mac_observe() if self.integration.platform == "darwin" else self._linux_observe()

    def _mac_observe(self) -> tuple[bool, int | None, bool]:
        item = self.integration
        result = self.manager.command(["/bin/launchctl", "print", f"gui/{os.getuid()}/{item.name}"])
        if result.returncode:
            listing = self.manager.command(["/bin/launchctl", "list"])
            rows = listing.stdout.splitlines()
            if (
                listing.returncode
                or not rows
                or rows[0].split() != ["PID", "Status", "Label"]
                or any(
                    len(row.split()) != MANAGER_COLUMNS
                    or not re.fullmatch(r"-|[1-9][0-9]{0,9}", row.split()[0])
                    or not re.fullmatch(r"-?[0-9]{1,10}", row.split()[1])
                    or row.split()[-1] == item.name
                    for row in rows[1:]
                )
                or len({row.split()[-1] for row in rows[1:]}) != len(rows) - 1
            ):
                self._refuse()
            return False, None, False
        text = result.stdout
        argv = re.findall(r"(?ms)^\s*arguments = \{\n(.*?)^\s*\}", text)
        env = re.findall(r"(?ms)^\s*environment = \{\n(.*?)^\s*\}", text)
        observed: dict[str, str] = {}
        if len(env) == 1:
            for row in env[0].splitlines():
                key, separator, value = row.lstrip().partition(" =>")
                if not separator or key in observed:
                    self._refuse()
                observed[key] = value.removeprefix(" ")
        if (
            len(text.encode()) > MAX_MANAGER_BYTES
            or re.findall(r"(?m)^\s*path = (.+)$", text) != [str(item.unit)]
            or re.findall(r"(?m)^\s*program = (.+)$", text) != [str(item.program)]
            or len(argv) != 1
            or [row.strip() for row in argv[0].splitlines()] != list(item.arguments)
            or re.findall(r"(?m)^\s*working directory = (.+)$", text) != [str(item.directory)]
            or any(observed.get(key) != value for key, value in item.environment.items())
            or any(key.startswith("HZ_") and key not in item.environment for key in observed)
        ):
            self._refuse()
        pids = re.findall(r"(?m)^\s*pid = (.*)$", text)
        if len(pids) > 1 or (pids and not re.fullmatch(r"[1-9][0-9]{0,9}", pids[0])):
            self._refuse()
        return True, int(pids[0]) if pids else None, True

    def _linux_observe(self) -> tuple[bool, int | None, bool]:
        item = self.integration
        result = self.manager.command(
            [
                "/usr/bin/systemctl",
                "--user",
                "show",
                item.name,
                "--property=Id,LoadState,FragmentPath,DropInPaths,ExecStart,Environment,WorkingDirectory,ActiveState,UnitFileState,MainPID,ControlPID,Job",
            ]
        )
        if result.returncode:
            self._refuse()
        values = manager_properties(result.stdout)
        if values.get("LoadState") == "not-found":
            if (
                values.get("Id") != item.name
                or values.get("MainPID", "0") != "0"
                or values.get("ControlPID", "0") != "0"
                or values.get("Job", "")
            ):
                self._refuse()
            return False, None, False
        expected = item.manager_facts() | {"disabled": False}
        if (
            not manager_identity_matches(values, expected, item.name)
            or values.get("ControlPID") != "0"
            or values.get("Job") != ""
        ):
            self._refuse()
        raw = values.get("MainPID", "")
        if not re.fullmatch(r"0|[1-9][0-9]{0,9}", raw) or values.get("ActiveState") not in {"active", "inactive"}:
            self._refuse()
        return True, int(raw) or None, values.get("ActiveState") == "active"

    @staticmethod
    def _refuse() -> Never:
        msg = "The integration manager identity is foreign, ambiguous or not proven quiescent."
        raise HeinzelError(msg)

    def stop(self) -> None:
        """Stop only the captured owned unit; preserve definitions and observe quiescence before returning."""
        self._verify_files()
        if self._observe() != self._state:
            self._refuse()
        if self._state[0]:
            command = (
                ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{self.integration.name}"]
                if self.integration.platform == "darwin"
                else ["/usr/bin/systemctl", "--user", "disable", "--now", self.integration.name]
            )
            if self.manager.command(command).returncode:
                self._refuse()
        loaded = self._settled()
        if self.integration.platform == "linux" and loaded:
            result = self.manager.command(["/usr/bin/systemctl", "--user", "is-enabled", self.integration.name])
            if result.stdout.strip() != "disabled":
                self._refuse()

    def _settled(self) -> bool:
        """Bound native stop settlement; every observation retains exact file and manager ownership."""
        deadline = self._clock() + STOP_SECONDS
        while True:
            self._verify_files()
            loaded, pid, active = self._observe()
            if self._clock() > deadline:
                self._refuse()
            if not active and pid is None and not (self.integration.platform == "darwin" and loaded):
                return loaded
            remaining = deadline - self._clock()
            if remaining <= 0:
                self._refuse()
            self._sleep(min(STOP_POLL_SECONDS, remaining))

    def quiescent(self) -> None:
        """Recheck exact files and no worker execution immediately before removing its SDK."""
        self._verify_files()
        loaded, pid, active = self._observe()
        if active or pid is not None or (self.integration.platform == "darwin" and loaded):
            self._refuse()

    def finish(self) -> None:
        """Delete only unchanged captured definitions after the coordinator observed SDK removal."""
        if os.path.lexists(self.integration.prefix):
            self._refuse()
        self._verify_files()
        loaded, pid, active = self._observe()
        if active or pid is not None or (self.integration.platform == "darwin" and loaded):
            self._refuse()
        for file in self.integration.files:
            remove_owned_file(file)
        if (
            self.integration.platform == "linux"
            and self.manager.command(["/usr/bin/systemctl", "--user", "daemon-reload"]).returncode
        ):
            self._refuse()
