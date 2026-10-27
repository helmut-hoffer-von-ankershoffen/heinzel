"""Restore Electron's Linux setuid sandbox helper from its verified release archive."""

# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

if TYPE_CHECKING:
    from collections.abc import Callable

# Update this map together with desktop/package.json and pnpm-lock.yaml when Electron changes.
# The value is the SHA-256 of the official electron-v45.0.0-linux-x64.zip release asset.
ARCHIVE_SHA256 = {
    "45.0.0": "22f6a10bd3f7fef987ddbb379d3375ce664ad0c98a9a50f80c89f1718a0c3e3a",
}
ARGUMENT_COUNT = 2
USAGE_ERROR = 2


class SandboxArchiveError(ValueError):
    """An Electron release archive is not the pinned, expected artifact."""


def verified_helper(archive: bytes, version: str) -> bytes:
    """Return chrome-sandbox bytes only after verifying the pinned release archive.

    Raises:
        SandboxArchiveError: The version is unpinned or the archive is invalid.
    """
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
        return release.read(entry)


def install_helper(
    electron_root: Path,
    *,
    open_url: Callable[..., BinaryIO] | None = None,
) -> Path:
    """Download the pinned Electron archive and restore only its Linux sandbox helper."""
    package = json.loads((electron_root / "package.json").read_text(encoding="utf-8"))
    version = package["version"]
    url = f"https://github.com/electron/electron/releases/download/v{version}/electron-v{version}-linux-x64.zip"
    request = urllib.request.Request(url)
    opener = open_url or urllib.request.urlopen  # noqa: S310 - URL is built only from the pinned HTTPS release host.
    with opener(request, timeout=60) as response:
        helper = verified_helper(response.read(), version)

    destination = electron_root / "dist" / "chrome-sandbox"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(helper)
    temporary.replace(destination)
    return destination


def main() -> int:
    """Install the Linux helper under the installed Electron package."""
    if len(sys.argv) != ARGUMENT_COUNT:
        print("usage: python3 scripts/electron_linux_sandbox.py ELECTRON_PACKAGE_ROOT", file=sys.stderr)
        return USAGE_ERROR
    try:
        helper = install_helper(Path(sys.argv[1]))
    except (OSError, SandboxArchiveError, KeyError, zipfile.BadZipFile) as error:
        print(f"::error::Could not restore verified Electron sandbox helper: {error}", file=sys.stderr)
        return 1
    print(f"Restored verified Electron sandbox helper: {helper}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
