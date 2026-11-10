"""Durable profile mutation gates and their SDK-wide host-owned inventory (ADR 0117)."""

import hashlib
import json
import os
import stat
import sys
from contextlib import ExitStack, suppress
from enum import StrEnum
from itertools import islice
from pathlib import Path
from typing import Protocol, cast

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._host_maintenance import MaintenanceLocks, host_maintenance_gate, host_maintenance_lock
from heinzel.kernel._identity import CurrentUser
from heinzel.kernel._mcp_storage import (
    PHYSICAL_DIRECTORY_FLAGS,
    descriptor_identity,
    encode_record,
    physical_directory,
    read_record,
    write_record,
)
from heinzel.kernel._private_control import private_directory

_PROFILE_PREFIX = "mutation-profile-"
_MAX_INVENTORY = 10_000
_REFUSED = "The SDK mutation profile inventory could not be verified"
_PROFILE_GATES = {
    "daemon": ("mutation-pending.json", "standard-install-pending.json"),
    "auto-update": ("before-update", "before-update.partial", "after-update"),
}


class InstallationInventoryStatus(StrEnum):
    """A protected SDK mutation inventory's assessment for lifecycle admission."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    RECOVERY_REQUIRED = "recovery-required"


class InstallationInventoryAuthority(Protocol):
    """Trusted host authority for completeness of one exact SDK mutation inventory."""

    def status(self, prefix: Path) -> InstallationInventoryStatus:
        """Assess the exact SDK under protected provisioning and broker-mediated mutation authority."""
        ...


def installation_inventory_status(
    prefix: Path, state: Path, *, authority: InstallationInventoryAuthority | None = None
) -> InstallationInventoryStatus:
    """Combine an explicitly supplied host authority with the SDK's existing durable recovery fences.

    The ordinary installer and profile inventory do not provide a completeness authority. Without a trusted
    host binding, even a clean observed inventory remains incomplete.
    """
    try:
        status = authority.status(prefix) if authority is not None else InstallationInventoryStatus.INCOMPLETE
        if installation_mutation_pending(prefix, state):
            return InstallationInventoryStatus.RECOVERY_REQUIRED
    except (OSError, ValueError, HeinzelError):
        return InstallationInventoryStatus.RECOVERY_REQUIRED
    return status


def installation_mutation_paths(prefix: Path, state: Path) -> tuple[Path, ...]:
    """Return unresolved package-mutation gates, excluding ordinary verified daemon handover requests."""
    return (
        host_maintenance_gate(prefix),
        state / "auto-update/before-update",
        state / "daemon/mutation-pending.json",
        state / "daemon/standard-install-pending.json",
        prefix / ".heinzel-startup-pending",
    )


def installation_mutation_pending(prefix: Path, state: Path) -> bool:
    """Refuse an observed pending entry, dangling link or unreadable gate; this sample is not an atomic fence."""
    for path in installation_mutation_paths(prefix, state):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        return True
    return tracked_installation_mutation_pending(prefix)


def _name(state: Path) -> str:
    return _PROFILE_PREFIX + hashlib.sha256(os.fsencode(state)).hexdigest() + ".json"


def _profile_path(state: Path) -> Path:
    absolute = state.absolute()
    if sys.platform == "darwin" and len(absolute.parts) > 1 and absolute.parts[1] in {"tmp", "var", "etc"}:
        alias = Path("/") / absolute.parts[1]
        info = alias.lstat()
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or alias.readlink() != Path(f"private/{alias.name}"):
            raise HeinzelError(_REFUSED)
        absolute = Path("/private") / absolute.relative_to("/")
    return absolute


def _lock_identity(authority: int, lock: Path) -> tuple[int, int]:
    descriptor = CurrentUser.open_private(authority, lock.name, os.O_RDONLY)
    try:
        return descriptor_identity(descriptor)
    finally:
        os.close(descriptor)


def _profile(stack: ExitStack, state: Path, *, create: bool = False) -> dict[str, int]:
    # The names are fixed even when a profile has never run the corresponding updater.
    if create and not os.path.lexists(state):
        private_directory(state)
    directory = stack.enter_context(physical_directory(state))
    if os.fstat(directory).st_uid != os.getuid():
        raise HeinzelError(_REFUSED)
    descriptors = {"": directory}
    for name in _PROFILE_GATES:
        if create:
            with suppress(FileExistsError):
                os.mkdir(name, 0o700, dir_fd=directory)
        child = os.open(name, PHYSICAL_DIRECTORY_FLAGS, dir_fd=directory)
        stack.callback(os.close, child)
        info = os.fstat(child)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise HeinzelError(_REFUSED)
        descriptors[name] = child
    if create:
        os.fsync(directory)
    return descriptors


def _receipt(prefix: Path, state: Path, identity: tuple[int, int], profile: dict[str, int]) -> bytes:
    return encode_record(
        {
            "schema": 1,
            "sdk": os.fspath(prefix),
            "state": os.fspath(state),
            "lock": list(identity),
            "directories": {name: list(descriptor_identity(descriptor)) for name, descriptor in profile.items()},
        }
    )


def record_installation_profile(prefix: Path, state: Path, locks: MaintenanceLocks) -> Path:
    """Durably enroll the exact ordinary profile before a cooperating SDK mutation can write its gates.

    Raises:
        HeinzelError: If the SDK lock, profile directories or existing inventory have changed.
        OSError: If the record cannot be published durably.
    """
    state = _profile_path(state)
    lock = host_maintenance_lock(prefix)
    with ExitStack() as stack:
        identity = locks.identity(lock)
        authority = stack.enter_context(physical_directory(lock.parent, private=True))
        if _lock_identity(authority, lock) != identity:
            raise HeinzelError(_REFUSED)
        name = _name(state)
        existing = read_record(authority, name)
        profile = _profile(stack, state, create=existing is None)
        expected = _receipt(prefix, state, identity, profile)
        if existing is None:
            write_record(authority, name, expected)
        elif existing != expected:
            raise HeinzelError(_REFUSED)
        if locks.identity(lock) != identity or _lock_identity(authority, lock) != identity:
            raise HeinzelError(_REFUSED)
    return state


def _present(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _tracked_pending(prefix: Path, authority: int, lock: Path, excluding: Path | None) -> bool:
    with os.scandir(authority) as entries:
        names = [entry.name for entry in islice(entries, _MAX_INVENTORY + 1)]
    if len(names) > _MAX_INVENTORY:
        return True
    for name in names:
        if not name.startswith(_PROFILE_PREFIX):
            continue
        data = read_record(authority, name)
        payload: object = json.loads(data) if data is not None else None
        state_path = cast("dict[str, object]", payload).get("state") if isinstance(payload, dict) else None
        if not isinstance(state_path, str):
            return True
        state = Path(state_path)
        if name != _name(state):
            return True
        with ExitStack() as stack:
            profile = _profile(stack, state)
            if data != _receipt(prefix, state, _lock_identity(authority, lock), profile):
                return True
            if state != excluding and any(
                _present(profile[folder], marker) for folder, markers in _PROFILE_GATES.items() for marker in markers
            ):
                return True
    return False


def tracked_installation_mutation_pending(prefix: Path, *, excluding: Path | None = None) -> bool:
    """Check each enrolled profile, refusing unreadable or replaced records and directory anchors.

    A mutation or rollback writer may exclude its own profile's markers while retaining the exact SDK lock.
    The selected profile's recorded directory identities are still checked.
    """
    lock = host_maintenance_lock(prefix)
    try:
        selected = _profile_path(excluding) if excluding is not None else None
        with physical_directory(lock.parent, private=True) as authority:
            return _tracked_pending(prefix, authority, lock, selected)
    except FileNotFoundError:
        return os.path.lexists(lock.parent)
    except (OSError, ValueError, HeinzelError):
        return True
