"""Restore Electron's Linux setuid sandbox helper from its verified release archive."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import BinaryIO

# Update this map together with desktop/package.json and pnpm-lock.yaml when Electron changes.
# The value is the SHA-256 of the official electron-v45.0.0-linux-x64.zip release asset.
ARCHIVE_SHA256 = {
    "45.0.0": "22f6a10bd3f7fef987ddbb379d3375ce664ad0c98a9a50f80c89f1718a0c3e3a",
}
HELPER_SHA256 = {
    "45.0.0": "f16e2562837856845cfc8ebf4aa7f78b57fdadc6b69130509b1db87ce5d2f869",
}
MAX_ARCHIVE_BYTES = 160 * 1024 * 1024
MAX_HELPER_BYTES = 1024 * 1024
ARGUMENT_COUNT = 2
CONFIGURE_ARGUMENT_COUNT = 3
USAGE_ERROR = 2


class SandboxArchiveError(ValueError):
    """An Electron release archive is not the pinned, expected artifact."""


def verified_helper(archive: bytes, version: str) -> bytes:
    """Return chrome-sandbox bytes only after verifying the pinned release archive.

    Raises:
        SandboxArchiveError: The version is unpinned or the archive is invalid.
    """
    if len(archive) > MAX_ARCHIVE_BYTES:
        message = "Electron release archive exceeds the configured size limit"
        raise SandboxArchiveError(message)
    expected = ARCHIVE_SHA256.get(version)
    if expected is None:
        message = f"Electron {version} has no pinned Linux release archive digest"
        raise SandboxArchiveError(message)
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        message = f"Electron {version} archive SHA-256 mismatch: {actual}"
        raise SandboxArchiveError(message)

    with zipfile.ZipFile(io.BytesIO(archive)) as release:
        entry = release.getinfo("chrome-sandbox")
        mode = entry.external_attr >> 16
        if not stat.S_ISREG(mode):
            message = "Electron release chrome-sandbox entry is not a regular file"
            raise SandboxArchiveError(message)
        helper = release.read(entry)
    expected_helper = HELPER_SHA256.get(version)
    if expected_helper is None or hashlib.sha256(helper).hexdigest() != expected_helper:
        message = "Electron sandbox helper does not match its pinned digest"
        raise SandboxArchiveError(message)
    return helper


def install_helper(
    electron_root: Path,
    *,
    open_url: Callable[..., BinaryIO] | None = None,
) -> Path:
    """Download the pinned Electron archive and restore only its Linux sandbox helper.

    Raises:
        SandboxArchiveError: The archive or destination path is invalid.
    """
    package = json.loads((electron_root / "package.json").read_text(encoding="utf-8"))
    version = package["version"]
    if version not in ARCHIVE_SHA256:
        message = f"Electron {version} has no pinned Linux release archive digest"
        raise SandboxArchiveError(message)
    url = f"https://github.com/electron/electron/releases/download/v{version}/electron-v{version}-linux-x64.zip"
    request = urllib.request.Request(url)
    opener = open_url or urllib.request.urlopen  # noqa: S310 - URL is built only from the pinned HTTPS release host.
    with opener(request, timeout=60) as response:
        archive = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(archive) > MAX_ARCHIVE_BYTES:
            message = "Electron release archive exceeds the configured size limit"
            raise SandboxArchiveError(message)
        helper = verified_helper(archive, version)

    if electron_root.is_symlink() or not electron_root.is_dir():
        message = "Electron package root must be a real directory"
        raise SandboxArchiveError(message)
    dist = electron_root / "dist"
    if dist.is_symlink() or not dist.is_dir():
        message = "Electron dist path must be an existing real directory"
        raise SandboxArchiveError(message)
    destination = dist / "chrome-sandbox"
    if destination.is_symlink():
        message = "Refusing to replace a symlinked Electron sandbox helper"
        raise SandboxArchiveError(message)
    if destination.exists() and not stat.S_ISREG(destination.lstat().st_mode):
        message = "Electron sandbox helper destination must be a regular file"
        raise SandboxArchiveError(message)

    descriptor, temporary_name = tempfile.mkstemp(prefix=".chrome-sandbox-", suffix=".tmp", dir=dist)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(helper)
        if destination.is_symlink():
            message = "Refusing to replace a symlinked Electron sandbox helper"
            raise SandboxArchiveError(message)
        Path(temporary).replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _open_nofollow(path: Path) -> int:
    """Open a file without following symlinks in any path component.

    Raises:
        OSError: The path cannot be safely opened.
        SandboxArchiveError: The path is not absolute and normalized.
    """
    if not path.is_absolute() or ".." in path.parts:
        message = "Electron sandbox helper path must be absolute and normalized"
        raise SandboxArchiveError(message)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory_fd = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)


def configure_helper_permissions(electron_root: Path, version: str) -> None:
    """Set root ownership and the setuid mode on the digest-pinned helper descriptor.

    Raises:
        OSError: The path cannot be safely opened or configured.
        SandboxArchiveError: The version or opened helper is not valid.
    """
    expected = HELPER_SHA256.get(version)
    if expected is None:
        message = f"Electron {version} has no pinned sandbox helper digest"
        raise SandboxArchiveError(message)
    helper_path = electron_root / "dist" / "chrome-sandbox"
    descriptor = _open_nofollow(helper_path)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_HELPER_BYTES:
            message = "Electron sandbox helper must be a regular file"
            raise SandboxArchiveError(message)
        chunks = []
        size = 0
        while size <= MAX_HELPER_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_HELPER_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        contents = b"".join(chunks)
        if size > MAX_HELPER_BYTES or hashlib.sha256(contents).hexdigest() != expected:
            message = "Electron sandbox helper content does not match the pinned release"
            raise SandboxArchiveError(message)
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o4755)
        result = os.fstat(descriptor)
        if (result.st_uid, result.st_gid, stat.S_IMODE(result.st_mode)) != (0, 0, 0o4755):
            message = "Electron sandbox helper must be root-owned with mode 4755"
            raise SandboxArchiveError(message)
    finally:
        os.close(descriptor)


def main() -> int:
    """Install the Linux helper under the installed Electron package."""
    if len(sys.argv) == CONFIGURE_ARGUMENT_COUNT and sys.argv[1] == "--configure":
        try:
            electron_root = Path(sys.argv[2])
            package = json.loads((electron_root / "package.json").read_text(encoding="utf-8"))
            configure_helper_permissions(electron_root, package["version"])
        except (OSError, SandboxArchiveError, KeyError) as error:
            print(f"::error::Could not configure Electron sandbox helper: {error}", file=sys.stderr)  # noqa: T201
            return 1
        print(f"Configured Electron sandbox helper under: {electron_root}")  # noqa: T201
        return 0
    if len(sys.argv) != ARGUMENT_COUNT:
        print("usage: python3 desktop/ci/restore_linux_sandbox.py ELECTRON_PACKAGE_ROOT", file=sys.stderr)  # noqa: T201
        return USAGE_ERROR
    try:
        helper = install_helper(Path(sys.argv[1]))
    except (OSError, SandboxArchiveError, KeyError, zipfile.BadZipFile) as error:
        print(f"::error::Could not restore verified Electron sandbox helper: {error}", file=sys.stderr)  # noqa: T201
        return 1
    print(f"Restored verified Electron sandbox helper: {helper}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
