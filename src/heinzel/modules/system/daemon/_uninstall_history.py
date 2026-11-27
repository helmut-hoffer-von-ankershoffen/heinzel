"""Preserve a completed daemon handover before uninstall retires its live request."""

import os
from dataclasses import dataclass
from pathlib import Path

from heinzel import HeinzelError
from heinzel.kernel import private_directory
from heinzel.modules.system.daemon._recovery_files import RecoveryFile, recovery_file, rename_exclusive
from heinzel.modules.system.daemon._runtime import fsync_dir


@dataclass(frozen=True)
class UninstallHistory:
    """Files captured while the daemon lifecycle and tick locks are held."""

    request: RecoveryFile
    records: tuple[RecoveryFile, ...]

    @classmethod
    def capture(cls, daemon: Path, installed: dict[Path, bytes | None]) -> "UninstallHistory":
        """Capture original unit, marker and independent tick records for a completed handover.

        Raises:
            HeinzelError: If the installation changed.
        """
        request = recovery_file(daemon / "handover-request.json")
        units = tuple(recovery_file(path, private=False) for path in installed)
        if any(record.content != installed[record.path] for record in units):
            msg = "The daemon installation changed before its handover could be archived."
            raise HeinzelError(msg)
        evidence = tuple(recovery_file(daemon / name) for name in ("handover-observation.json", "heartbeat.json"))
        return cls(request, (*units, *evidence))

    def archive(self, directory: Path) -> None:
        """Copy the checked evidence durably, then move the unchanged request without replacing an older archive.

        Raises:
            HeinzelError: If a source file or its archived contents changed.
        """
        private_directory(directory.parent, create=False)
        directory.mkdir(mode=0o700)
        fsync_dir(directory.parent)
        for record in self.records:
            path = directory / record.path.name
            with path.open("xb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(record.content)
                output.flush()
                os.fsync(output.fileno())
        fsync_dir(directory)
        for record in self.records:
            if (
                recovery_file(record.path, private=False) != record
                or recovery_file(directory / record.path.name).content != record.content
            ):
                msg = "The daemon handover evidence changed before archiving; removal is pending."
                raise HeinzelError(msg)
        rename_exclusive(self.request.path, directory / self.request.path.name, expected_source=self.request)
