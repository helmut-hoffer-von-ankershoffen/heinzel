"""Durable uninstall coordinator and standalone recovery material, prepared before any service stop (ADR 0131)."""

import hashlib
import json
import os
import secrets
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heinzel.kernel._host_maintenance import HostIntegration

from heinzel.kernel import _uninstall_recovery as recovery
from heinzel.kernel._installation_continuity import tool_python
from heinzel.kernel._offline_uv import VerifiedUV, fsync_directory
from heinzel.kernel._private_control import private_directory, private_read
from heinzel.kernel._tool_uninstall import OwnedFile, owned_file


@dataclass(frozen=True)
class RemovalCapture:
    files: tuple[OwnedFile, ...]
    entrypoints: tuple[Path, ...]
    manager_units: dict[str, dict[str, object]]
    gate: Path | None = None
    locks: tuple[Path, ...] = ()
    labels: tuple[str, ...] = ()
    integrations: tuple["HostIntegration", ...] = ()


class RemovalJournal:
    """A private exact-target journal that remains usable after its command's SDK is gone."""

    def __init__(
        self,
        executable: VerifiedUV,
        *,
        prefix: Path,
        binary: Path,
        state: Path,
        captured: tuple[tuple[OwnedFile, ...], tuple[Path, ...], dict[str, dict[str, object]]] | RemovalCapture,
    ) -> None:
        captured = captured if isinstance(captured, RemovalCapture) else RemovalCapture(*captured)
        files, entrypoints, manager_units = captured.files, captured.entrypoints, captured.manager_units
        self.directory = executable.path.parent
        self.path = self.directory / "journal.json"
        self.gate = captured.gate or state / "daemon/standard-install-pending.json"
        self.gate_content = json.dumps({"token": secrets.token_hex(32)}, indent=2) + "\n"
        self.token = json.loads(self.gate_content)["token"]
        python, python_digest, _identity = tool_python(prefix)
        self.command = shlex.join([python, "-I", str(self.directory / "recover.py")])
        info = prefix.stat()
        lock_names = sorted(
            {
                str(state / "ui/lifecycle.lock"),
                str(state / "daemon/lifecycle.lock"),
                str(state / "daemon/tick.lock"),
                *(str(path) for path in captured.locks),
            }
        )
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
            "manager_units": manager_units,
            "manager_labels": list(captured.labels),
            "integrations": [
                {
                    "name": item.name,
                    "prefix": str(item.prefix),
                    "platform": item.platform,
                    "unit": str(item.unit),
                    "program": str(item.program),
                    "arguments": list(item.arguments),
                    "environment": item.environment,
                    "directory": str(item.directory),
                    "locks": [str(path) for path in item.locks],
                    "files": [
                        {
                            "path": str(file.path),
                            "identity": list(file.identity),
                            "content": file.content.decode("utf-8"),
                        }
                        for file in item.files
                    ],
                }
                for item in captured.integrations
            ],
            "uv": str(executable.path),
            "uv_sha256": executable.digest,
            "uv_identity": list(executable.identity),
            "environment": {
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
                "UV_NO_CACHE": "1",
                "UV_TOOL_DIR": str(prefix.parent),
                "UV_TOOL_BIN_DIR": str(binary),
            },
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
            "locks": lock_names,
            "gate": str(self.gate),
            "gate_content": self.gate_content,
        }
        self.value["lock_identities"] = {
            name: [Path(name).lstat().st_dev, Path(name).lstat().st_ino] for name in lock_names
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
        """Exclusively publish the cooperative gate durably before stopping any owned job.

        Raises:
            FileExistsError: If another operation already owns the gate; its bytes remain unchanged.
        """
        private_directory(self.gate.parent, create=False)
        descriptor = os.open(self.gate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(self.gate_content)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(self.gate.parent)

    def phase(self, phase: str) -> None:
        """Durably record an observed phase; a later failure never fabricates rollback or completion."""
        self.value["phase"] = phase
        recovery.write(self.path, self.value)

    def require_gate(self) -> None:
        """Recheck exact durable authority before each effect; a foreign operation is never adopted.

        Raises:
            ValueError: If the current gate differs from this operation.
        """
        if private_read(self.gate) != self.gate_content:
            msg = "The uninstall recovery gate changed"
            raise ValueError(msg)

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
