"""Durable uninstall coordinator and standalone recovery material, prepared before any service stop (ADR 0131)."""

import hashlib
import json
import os
import secrets
import shlex
import sys
from pathlib import Path

from heinzel.kernel import _uninstall_recovery as recovery
from heinzel.kernel._installation_continuity import tool_python
from heinzel.kernel._offline_uv import VerifiedUV, fsync_directory
from heinzel.kernel._private_control import private_read, private_write
from heinzel.kernel._tool_uninstall import OwnedFile, owned_file


class RemovalJournal:
    """A private exact-target journal that remains usable after its command's SDK is gone."""

    def __init__(
        self,
        executable: VerifiedUV,
        *,
        prefix: Path,
        binary: Path,
        state: Path,
        captured: tuple[tuple[OwnedFile, ...], tuple[Path, ...]],
    ) -> None:
        files, entrypoints = captured
        self.directory = executable.path.parent
        self.path = self.directory / "journal.json"
        self.gate = state / "daemon/standard-install-pending.json"
        self.gate_content = json.dumps({"token": secrets.token_hex(32)}, indent=2) + "\n"
        self.token = json.loads(self.gate_content)["token"]
        python, python_digest, _identity = tool_python(prefix)
        self.command = shlex.join([python, "-I", str(self.directory / "recover.py")])
        info = prefix.stat()
        self.value = {
            "format": 1,
            "uid": os.getuid(),
            "directory": str(self.directory),
            "phase": "prepared",
            "platform": sys.platform,
            "python": python,
            "python_continuity": python_digest,
            "prefix": str(prefix),
            "prefix_identity": [info.st_dev, info.st_ino],
            "manifest": recovery.manifest(prefix),
            "uv": str(executable.path),
            "uv_sha256": executable.digest,
            "uv_identity": list(executable.identity),
            "environment": {"PATH": "/usr/bin:/bin", "UV_TOOL_DIR": str(prefix.parent), "UV_TOOL_BIN_DIR": str(binary)},
            "entrypoints": [str(path) for path in entrypoints],
            "links": [
                {
                    "path": str(path),
                    "device": path.lstat().st_dev,
                    "inode": path.lstat().st_ino,
                    "target": str(path.readlink()),
                }
                for path in entrypoints
            ],
            "files": [
                {
                    "path": str(file.path),
                    "identity": list(file.identity),
                    "sha256": hashlib.sha256(file.content).hexdigest(),
                }
                for file in files
            ],
            "locks": [
                str(state / "ui/lifecycle.lock"),
                str(state / "daemon/lifecycle.lock"),
                str(state / "daemon/tick.lock"),
            ],
            "gate": str(self.gate),
            "gate_content": self.gate_content,
        }
        if len(json.dumps(self.value).encode()) > recovery.MAX_BYTES:
            msg = "The local uninstall recovery record exceeds its budget; no service was stopped."
            raise ValueError(msg)
        digest = recovery.authority_digest(self.value)
        source = (
            owned_file(Path(recovery.__file__))
            .content.decode()
            .replace('EXPECTED_AUTHORITY = ""', f'EXPECTED_AUTHORITY = "{digest}"', 1)
            .encode()
        )
        descriptor = os.open(self.directory / "recover.py", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o500)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
        recovery.write(self.path, self.value)
        fsync_directory(self.directory)

    def begin(self) -> None:
        """Publish the existing cooperative installation gate durably before stopping any owned job."""
        private_write(self.gate, self.gate_content)
        fsync_directory(self.gate.parent)

    def phase(self, phase: str) -> None:
        """Durably record an observed phase; a later failure never fabricates rollback or completion."""
        self.value["phase"] = phase
        recovery.write(self.path, self.value)

    def complete(self) -> None:
        """Clear only this exact recovery gate after integration cleanup and durable manager reload.

        Raises:
            ValueError: If the current gate differs from the recorded removal.
        """
        if private_read(self.gate) != self.gate_content:
            msg = "The uninstall recovery gate changed"
            raise ValueError(msg)
        self.gate.unlink()
        fsync_directory(self.gate.parent)
        self.phase("complete")
