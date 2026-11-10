"""Descriptor-anchored storage shared by MCP registrations and runtime observations."""

import json
import os
import stat
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._identity import CurrentUser

_RECORD_LIMIT = 4096
PHYSICAL_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_REFUSED = "The installed MCP runtime root is unavailable or changed"


@dataclass(frozen=True, slots=True)
class InstalledMcpRuntime:
    """SDK prefix and private profile state selected by trusted native activation, never by a vendor or CLI flag."""

    prefix: Path
    state: Path


def descriptor_identity(descriptor: int) -> tuple[int, int]:
    """Return the device and inode of the retained descriptor.

    Raises:
        OSError: If the descriptor cannot be inspected.
    """
    info = os.fstat(descriptor)
    return info.st_dev, info.st_ino


@contextmanager
def physical_directory(path: Path, *, private: bool = False) -> Generator[int]:
    """Walk a physical directory path without links and optionally require a private leaf.

    Raises:
        HeinzelError: If a path component fails the ownership or mode check.
        OSError: If opening or inspecting a directory fails.

    Yields:
        The retained descriptor for the leaf directory.
    """
    if not path.is_absolute() or ".." in path.parts or path.anchor != "/":
        raise HeinzelError(_REFUSED)
    descriptor = os.open("/", PHYSICAL_DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            child = os.open(component, PHYSICAL_DIRECTORY_FLAGS, dir_fd=descriptor)
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
            physical_directory(installation.prefix) as prefix,
            physical_directory(installation.state, private=True) as state,
        ):
            return descriptor_identity(prefix), descriptor_identity(state)
    except OSError:
        raise HeinzelError(_REFUSED) from None


def read_record(directory: int, name: str) -> bytes | None:
    """Read a bounded private record through its directory descriptor.

    Raises:
        HeinzelError: If the record is unsafe or exceeds the bound.
        OSError: If the record cannot be read.
    """
    try:
        descriptor = CurrentUser.open_private(directory, name, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as source:
        data = source.read(_RECORD_LIMIT + 1)
    if len(data) > _RECORD_LIMIT:
        raise HeinzelError(_REFUSED)
    return data


def encode_record(data: dict[str, JsonValue]) -> bytes:
    """Encode the bounded canonical JSON bytes shared by the storage records.

    Raises:
        HeinzelError: If the encoded record exceeds the bound.
    """
    encoded = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    if len(encoded) > _RECORD_LIMIT:
        raise HeinzelError(_REFUSED)
    return encoded


def write_record(directory: int, name: str, data: bytes) -> None:
    """Exclusively create and sync a private record through its directory descriptor.

    Raises:
        HeinzelError: If the record cannot be safely opened.
        OSError: If creating, writing or syncing the record fails.
    """
    descriptor = CurrentUser.open_private(directory, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(descriptor, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())
    os.fsync(directory)
