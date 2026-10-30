"""Durable, single-use receipts for confirmed plugin adds."""

import fcntl
import os
import re
import secrets
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from heinzel import HeinzelError
from heinzel.modules.system.plugin._models import PluginCandidate, PluginChange, PluginOperation

PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
RECEIPT_ID = re.compile(r"^[A-Za-z0-9_-]{40,50}$")


class _Record(BaseModel):
    operation_id: str
    status: Literal["previewed", "running", "completed", "unknown"]
    candidate: PluginCandidate
    result: PluginChange | None = None
    owner_pid: int | None = None

    def public(self) -> PluginOperation:
        return PluginOperation(
            operation_id=self.operation_id,
            status=self.status,
            candidate=self.candidate,
            result=self.result,
        )


class PluginReceipts:
    """Receipts shared by every hz process using the same state folder."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory / "plugin-operations"

    def create(self, candidate: PluginCandidate) -> PluginOperation:
        operation_id = secrets.token_urlsafe(32)
        record = _Record(operation_id=operation_id, status="previewed", candidate=candidate)
        with self._locked():
            self._write(record)
        return record.public()

    def get(self, operation_id: str) -> PluginOperation:
        path = self._path(operation_id)
        with self._locked():
            record = self._read(path)
            if record.status == "running" and not _alive(record.owner_pid):
                # Status reads remain read-only: report a dead owner's outcome as unknown. A later explicit
                # confirmation persists that terminal state under the same receipt lock before refusing replay.
                record = record.model_copy(update={"status": "unknown", "owner_pid": None})
            return record.public()

    def claim(self, operation_id: str) -> PluginOperation:
        with self._locked():
            record = self._read(self._path(operation_id))
            if record.status == "running" and not _alive(record.owner_pid):
                record.status = "unknown"
                record.owner_pid = None
                self._write(record)
            if record.status == "running":
                msg = "This plugin operation is already running; inspect its status before retrying."
                raise HeinzelError(msg)
            if record.status != "previewed":
                return record.public()
            record.status = "running"
            record.owner_pid = os.getpid()
            try:
                self._write(record)
            except HeinzelError:
                # The atomic replace is visible even when the following fsync fails. This install has not
                # started, so replace that ambiguous running marker with a fail-closed, user-resolvable result.
                record.status = "unknown"
                record.owner_pid = None
                with suppress(HeinzelError):
                    # The directory already exists here. Skip its parent sync on recovery so a persistent
                    # parent-sync error cannot prevent replacing the unstarted running marker with unknown.
                    self._write(record, sync_parent=False)
                    # _write replaces before syncing. Even if sync keeps failing, a live status read sees unknown;
                    # after a crash, the receipt is either unknown or unavailable, both of which block replay.
                raise
            return record.public()

    def complete(self, operation_id: str, result: PluginChange) -> PluginOperation:
        with self._locked():
            record = self._read(self._path(operation_id))
            if record.status != "running" or record.owner_pid != os.getpid():
                msg = "The plugin operation receipt is no longer owned by this process."
                raise HeinzelError(msg)
            record.status = "completed"
            record.owner_pid = None
            record.result = result.model_copy(update={"operation_id": operation_id})
            self._write(record)
            return record.public()

    def unknown(self, operation_id: str) -> None:
        with self._locked():
            record = self._read(self._path(operation_id))
            if record.status == "running" and record.owner_pid == os.getpid():
                record.status = "unknown"
                record.owner_pid = None
                self._write(record)

    @contextmanager
    def installing(self) -> Generator[None]:
        """Serialize environment replacement across processes; fail fast instead of starting a second installer.

        Raises:
            HeinzelError: If another process already holds the installer lock.
        """
        _ensure_directory(self.directory)
        path = self.directory / "install.lock"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE)
        with os.fdopen(descriptor, "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                msg = "Another plugin installation is running; check its operation receipt."
                raise HeinzelError(msg) from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _path(self, operation_id: str) -> Path:
        if not RECEIPT_ID.fullmatch(operation_id):
            msg = "The plugin operation receipt is invalid or unavailable."
            raise HeinzelError(msg)
        return self.directory / f"{operation_id}.json"

    def _read(self, path: Path) -> _Record:
        try:
            record = _Record.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            msg = "The plugin operation receipt is invalid or unavailable."
            raise HeinzelError(msg) from exc
        if path != self._path(record.operation_id):
            msg = "The plugin operation receipt is invalid or unavailable."
            raise HeinzelError(msg)
        return record

    def _write(self, record: _Record, *, sync_parent: bool = True) -> None:
        _ensure_directory(self.directory, sync_parent=sync_parent)
        target = self._path(record.operation_id)
        partial = self.directory / f".{record.operation_id}.{secrets.token_hex(4)}.tmp"
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(record.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        try:
            _fsync_directory(self.directory)
        except OSError as exc:
            msg = "The plugin operation receipt could not be made durable; inspect its status before retrying."
            raise HeinzelError(msg) from exc

    @contextmanager
    def _locked(self) -> Generator[None]:
        _ensure_directory(self.directory)
        descriptor = os.open(self.directory / "receipts.lock", os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE)
        with os.fdopen(descriptor, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _fsync_directory(directory: Path) -> None:
    """Persist a replaced receipt name, not just the bytes in its file."""
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_directory(directory: Path, *, sync_parent: bool = True) -> None:
    """Create the receipt directory durably before relying on files inside it.

    Raises:
        HeinzelError: If the directory entry cannot be made durable.
    """
    directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
    directory.chmod(PRIVATE_DIR)
    if not sync_parent:
        return
    try:
        # Sync on every entry, not only the mkdir call: a prior attempt may have created the directory
        # but failed before making its parent entry durable.
        _fsync_directory(directory.parent)
    except OSError as exc:
        msg = "The plugin operation directory could not be made durable."
        raise HeinzelError(msg) from exc
