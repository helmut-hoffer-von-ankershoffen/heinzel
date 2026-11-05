"""Durable explicit web-launcher migration/retirement, independent of SDK-removal recovery (ADR 0133)."""

import hashlib
import json
import os
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Never

from pydantic import BaseModel, ConfigDict, JsonValue

from heinzel import HeinzelError
from heinzel.kernel import (
    MaintenanceLocks,
    host_maintenance_gate,
    host_maintenance_lock,
    private_directory,
    private_read,
)
from heinzel.kernel._offline_uv import fsync_directory
from heinzel.kernel._tool_uninstall import OwnedFile, owned_file, remove_owned_file

MAX_TRANSITION_BYTES = 256 * 1024
IDENTITY_FIELDS = 7
REPLACEMENTS = 2

Action = Literal["migrate", "retire"]
Phase = Literal["prepared", "stopped", "canonical", "loaded", "retired", "cleaned", "complete"]


class FileSnapshot(BaseModel):
    """Exact original bytes/inode or a privately staged replacement; no executable journal callbacks."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path
    identity: tuple[int, ...]
    content: str

    @classmethod
    def capture(cls, path: Path) -> "FileSnapshot":
        """Capture the existing physical file with the shared bounded ownership policy."""
        value = owned_file(path)
        return cls(path=path, identity=value.identity, content=value.content.decode())

    def matches(self, path: Path | None = None, *, renamed: bool = False) -> bool:
        """A rename may change ctime; its opened inode, mode, owner, size, mtime and bytes must remain exact."""
        value = owned_file(path or self.path)
        expected = self.identity[:-1] if renamed else self.identity
        actual = value.identity[:-1] if renamed else value.identity
        return (
            actual == expected and value.content == self.content.encode() and (path or self.path).lstat().st_nlink == 1
        )


class TransitionRecord(BaseModel):
    """A separate operation kind with complete old/new authority and physical fence parents."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal[1] = 1
    kind: Literal["web-launcher-migrate", "web-launcher-retire"]
    uid: int
    prefix: Path
    executable: Path
    token: str
    phase: Phase
    old: dict[str, JsonValue]
    new: dict[str, JsonValue] | None
    files: tuple[FileSnapshot, ...]
    staged: tuple[FileSnapshot, ...]
    gates: dict[str, tuple[int, ...]]
    locks: dict[str, tuple[int, int]]
    retiring_instance: str | None = None


class LauncherTransition:
    """Retain old authority until exact canonical replacement or authorized retirement is durably complete."""

    def __init__(self, directory: Path, prefix: Path, executable: Path, locks: MaintenanceLocks | None = None) -> None:
        self.directory, self.prefix, self.executable = directory, prefix, executable
        self.path = directory / "launcher-transition.json"
        self.locks = locks
        self.value: TransitionRecord | None = None

    @property
    def lock_paths(self) -> tuple[Path, ...]:
        """The exact deterministic SDK/web union, never an arbitrary journal lock set."""
        return tuple(sorted((host_maintenance_lock(self.prefix), self.directory / "lifecycle.lock"), key=str))

    def _require_locks(self) -> None:
        if self.locks is None:
            self._refuse()
        for path in self.lock_paths:
            with self.locks.guard(path):
                if self.value is not None and self.record.locks[str(path)] != (
                    path.lstat().st_dev,
                    path.lstat().st_ino,
                ):
                    self._refuse()

    @property
    def record(self) -> TransitionRecord:
        """The validated operation; no missing or ambient authority is inferred.

        Raises:
            HeinzelError: If exact private operation authority cannot be established.
        """
        if self.value is None:
            msg = "No web launcher transition was prepared."
            raise HeinzelError(msg)
        return self.value

    @property
    def gate_content(self) -> str:
        """Bind both fences to the complete immutable old/new operation, not merely a shared token."""
        value = self.record.model_dump(mode="json", exclude={"phase"})
        digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return json.dumps({"kind": self.record.kind, "token": self.record.token, "authority": digest}, indent=2) + "\n"

    def load(self) -> TransitionRecord:
        """Read only a physical private operation; the caller must validate every old/new tuple before effects.

        Raises:
            HeinzelError: If exact private operation authority cannot be established.
        """
        content = _read_journal(self.path)
        value = TransitionRecord.model_validate_json(content)
        if (
            content != value.model_dump_json()
            or value.uid != os.getuid()
            or value.prefix != self.prefix
            or value.executable != self.executable
            or re.fullmatch(r"[a-f0-9]{32}", value.token) is None
            or (value.retiring_instance is not None and re.fullmatch(r"[a-f0-9]{32}", value.retiring_instance) is None)
            or set(value.locks) != {str(path) for path in self.lock_paths}
            or any(len(row.identity) != IDENTITY_FIELDS for row in (*value.files, *value.staged))
        ):
            msg = "The web launcher transition identity changed; its files and fences were kept."
            raise HeinzelError(msg)
        self.value = value
        return value

    def prepare(
        self,
        action: Action,
        records: tuple[dict[str, JsonValue], dict[str, JsonValue] | None],
        files: tuple[Path, ...],
        *,
        retiring_instance: str | None = None,
    ) -> None:
        """Durably capture exact old/new authority before publishing either fence or stopping a job.

        Raises:
            HeinzelError: If exact private operation authority cannot be established.
        """
        self._require_locks()
        old, new = records
        token = secrets.token_hex(16)
        staged: list[FileSnapshot] = []
        if new is not None:
            for name, content in (("launcher", str(new["launcher"])), ("marker", json.dumps(new))):
                path = self.directory / f".web-{token}-{name}.new"
                _exclusive_write(path, content)
                staged.append(FileSnapshot.capture(path))
        gates = (host_maintenance_gate(self.prefix), self.directory.parent / "daemon/standard-install-pending.json")
        parents: dict[str, tuple[int, ...]] = {}
        for gate in gates:
            private_directory(gate.parent)
            if os.path.lexists(gate):
                msg = "An existing installation fence requires recovery before web migration or retirement."
                raise HeinzelError(msg)
            info = gate.parent.lstat()
            parents[str(gate)] = (info.st_dev, info.st_ino, info.st_mode, info.st_uid)
        self.value = TransitionRecord(
            kind="web-launcher-migrate" if action == "migrate" else "web-launcher-retire",
            uid=os.getuid(),
            prefix=self.prefix,
            executable=self.executable,
            token=token,
            phase="prepared",
            old=old,
            new=new,
            files=tuple(FileSnapshot.capture(path) for path in files),
            staged=tuple(staged),
            gates=parents,
            locks={str(path): (path.lstat().st_dev, path.lstat().st_ino) for path in self.lock_paths},
            retiring_instance=retiring_instance,
        )
        _exclusive_write(self.path, self.record.model_dump_json())

    def validate(
        self,
        action: Action,
        paths: tuple[Path, ...],
        valid: Callable[[dict[str, JsonValue]], bool],
        historical: Callable[[dict[str, JsonValue] | None], bool],
    ) -> None:
        """Require complete canonical tuples and exact fixed paths; no journal-supplied program is accepted.

        Raises:
            HeinzelError: If exact private operation authority cannot be established.
        """
        value = self.record
        expected_kind = "web-launcher-migrate" if action == "migrate" else "web-launcher-retire"
        if value.kind != expected_kind:
            msg = "Complete the existing web launcher operation using its original install or uninstall command."
            raise HeinzelError(msg)
        expected_gates = {
            str(host_maintenance_gate(self.prefix)),
            str(self.directory.parent / "daemon/standard-install-pending.json"),
        }
        if (
            set(value.gates) != expected_gates
            or tuple(row.path for row in value.files) != paths
            or not valid(value.old)
            or not historical(value.old)
        ):
            self._refuse()
        if value.new is None:
            if action != "retire" or value.staged:
                self._refuse()
        elif (
            action != "migrate"
            or not valid(value.new)
            or historical(value.new)
            or any(value.old[key] != value.new[key] for key in value.old if key not in {"generation", "uv", "launcher"})
            or len(value.staged) != REPLACEMENTS
            or tuple(row.path for row in value.staged)
            != tuple(self.directory / f".web-{value.token}-{name}.new" for name in ("launcher", "marker"))
            or value.staged[0].content != value.new["launcher"]
            or json.loads(value.staged[1].content) != value.new
        ):
            self._refuse()
        unit, launcher, marker = value.files
        if (
            unit.content != value.old["content"]
            or launcher.content != value.old["launcher"]
            or json.loads(marker.content) != value.old
        ):
            self._refuse()
        self.check_files()

    def check_files(self) -> None:
        """Admit only captured originals or the exact staged replacement inode, never edited or foreign files."""
        value = self.record
        staged = {value.files[index + 1].path: row for index, row in enumerate(value.staged)}
        for row in value.files:
            if not os.path.lexists(row.path):
                if value.kind == "web-launcher-retire" and value.phase != "prepared":
                    continue
                self._refuse()
            if not row.matches() and not (row.path in staged and staged[row.path].matches(row.path, renamed=True)):
                self._refuse()
        for row in value.staged:
            if os.path.lexists(row.path) and not row.matches():
                self._refuse()

    def require_gates(self, *, terminal: bool = False) -> None:
        """Every effect requires both exact physical fences; only completed cleanup permits missing fences."""
        self._require_locks()
        if _read_journal(self.path) != self.record.model_dump_json():
            self._refuse()
        for name, identity in self.record.gates.items():
            path = Path(name)
            private_directory(path.parent, create=False)
            info = path.parent.lstat()
            if (info.st_dev, info.st_ino, info.st_mode, info.st_uid) != identity:
                self._refuse()
            if terminal and not os.path.lexists(path):
                continue
            if private_read(path) != self.gate_content:
                self._refuse()

    def publish(self) -> None:
        """Prepared publication can fill only its own absent exact fences after caller revalidation."""
        self._require_locks()
        if self.record.phase != "prepared":
            self.require_gates()
            return
        # Validate every present gate before creating a missing one; foreign bytes never permit progress.
        for name in self.record.gates:
            path = Path(name)
            if os.path.lexists(path) and private_read(path) != self.gate_content:
                self._refuse()
        for name in self.record.gates:
            path = Path(name)
            private_directory(path.parent, create=False)
            info = path.parent.lstat()
            if (info.st_dev, info.st_ino, info.st_mode, info.st_uid) != self.record.gates[name]:
                self._refuse()
            if not os.path.lexists(path):
                _exclusive_write(path, self.gate_content)
        self.require_gates()

    def phase(self, phase: Phase) -> None:
        """Persist observed progress while retaining the immutable authority hash."""
        self._require_locks()
        if _read_journal(self.path) != self.record.model_dump_json():
            self._refuse()
        self.value = self.record.model_copy(update={"phase": phase})
        _write_journal(self.path, self.record.model_dump_json())

    def replace(self) -> None:
        """Conditionally replace each exact original with its privately staged canonical inode."""
        for target, staged in zip(self.record.files[1:], self.record.staged, strict=True):
            self.require_gates()
            self.check_files()
            if staged.matches(target.path, renamed=True):
                continue
            if not target.matches() or not staged.matches():
                self._refuse()
            staged.path.replace(target.path)
            fsync_directory(target.path.parent)
        self.check_files()
        self.phase("canonical")

    def retire(self) -> None:
        """Remove only captured definitions after the caller proved native quiescence, retaining the SDK and data."""
        for row in self.record.files:
            self.require_gates()
            self.check_files()
            if os.path.lexists(row.path):
                remove_owned_file(OwnedFile(row.path, row.content.encode(), row.identity))
        self.phase("retired")

    def finish(self) -> None:
        """Clear only own fences after verified final state, preserving an immutable local recovery history."""
        terminal = self.record.phase in {"cleaned", "complete"}
        self.require_gates(terminal=terminal)
        self.check_files()
        if self.record.kind == "web-launcher-migrate":
            if any(
                not staged.matches(target.path, renamed=True)
                for target, staged in zip(self.record.files[1:], self.record.staged, strict=True)
            ):
                self._refuse()
        elif any(os.path.lexists(row.path) for row in self.record.files):
            self._refuse()
        self.phase("cleaned")
        for name in reversed(self.record.gates):
            path = Path(name)
            if not os.path.lexists(path):
                continue
            if private_read(path) != self.gate_content:
                self._refuse()
            path.unlink()
            fsync_directory(path.parent)
        self.phase("complete")
        history = self.directory / "launcher-history"
        private_directory(history)
        target = history / f"{self.record.token}.json"
        content = self.record.model_dump_json()
        if os.path.lexists(target):
            if _read_journal(target) != content:
                self._refuse()
        else:
            _exclusive_write(target, content)
        pending = owned_file(self.path)
        if pending.content.decode() != content:
            self._refuse()
        remove_owned_file(pending)

    @staticmethod
    def _refuse() -> Never:
        msg = "The web launcher transition authority changed; its files and exact recovery fences were kept."
        raise HeinzelError(msg)


def _exclusive_write(path: Path, content: str) -> None:
    if len(content.encode()) > MAX_TRANSITION_BYTES:
        msg = "The web transition material exceeds its fixed budget."
        raise HeinzelError(msg)
    private_directory(path.parent, create=False)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)


def _read_journal(path: Path) -> str:
    private_directory(path.parent, create=False)
    value = owned_file(path, limit=MAX_TRANSITION_BYTES)
    if value.identity[2] & 0o077 or path.lstat().st_nlink != 1:
        msg = "The web transition record is not private."
        raise HeinzelError(msg)
    return value.content.decode()


def _write_journal(path: Path, content: str) -> None:
    if len(content.encode()) > MAX_TRANSITION_BYTES:
        msg = "The web transition record exceeds its fixed budget."
        raise HeinzelError(msg)
    original = owned_file(path, limit=MAX_TRANSITION_BYTES)
    temporary = path.with_name(f".web-transition-{secrets.token_hex(16)}.tmp")
    _exclusive_write(temporary, content)
    try:
        if owned_file(path, limit=MAX_TRANSITION_BYTES) != original:
            msg = "The web transition record changed before persistence."
            raise HeinzelError(msg)
        temporary.replace(path)
        fsync_directory(path.parent)
    finally:
        if os.path.lexists(temporary):
            remove_owned_file(owned_file(temporary))
