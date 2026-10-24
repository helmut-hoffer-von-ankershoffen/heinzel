"""The local streams: append-only JSON Lines files in your state directory, one per stream and month (ADR 0062).

`<state dir>/state/<stream>/<YYYY-MM>.jsonl` is laid out exactly as the writer branch is, so a push copies a file
whose copy on the branch is a prefix of it, and what hasn't been pushed yet is the bytes past that prefix: nothing
else to keep or lose. Lines are appended under an exclusive lock, so two hz processes noting the same day write it
once, and never through a symbolic link.
"""

import fcntl
import os
import re
import secrets
import string
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from heinzel import HeinzelError
from heinzel.modules.system.state._models import MACHINE, STREAMS, AdoptionLine, Os, PresenceLine

DIRECTORY = "state"
MACHINE_FILE = "machine"
SUFFIX = ".jsonl"
SEGMENT = re.compile(r"^[0-9]{4}-(?:0[1-9]|1[0-2])\.jsonl$")
"""A segment's name: the month its lines are dated in."""
MAX_SEGMENT = 8 * 1024 * 1024
"""Bytes a segment may hold: a presence segment holds at most 31 lines, so anything near this is not ours."""
MAX_LINE = 1024
_ALPHABET = string.ascii_lowercase + string.digits


def _flags(base: int) -> int:
    return base | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


@dataclass(frozen=True, slots=True)
class Segment:
    """One local stream file: its stream, its path relative to the streams' root (and the branch), and its bytes."""

    stream: str
    name: str
    data: bytes

    @property
    def path(self) -> str:
        """Where it lives below the root, `presence/2026-10.jsonl`."""
        return f"{self.stream}/{self.name}"


class Streams:
    """This machine's streams below `<state dir>/state`: its id, and appending and reading lines."""

    def __init__(self, state_dir: Path) -> None:
        """Keep everything below `state_dir/state`."""
        self.root = state_dir / DIRECTORY

    def _directory(self, path: Path) -> Path:
        if path.is_symlink():
            msg = f"{path} is a symbolic link; hz keeps its state only in a real directory"
            raise HeinzelError(msg)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def machine(self) -> str:
        """This machine's id, made once from random characters; never the host name, which can name a person.

        Raises:
            HeinzelError: If the id file is damaged or can't be written.
        """
        path = self._directory(self.root) / MACHINE_FILE
        try:
            descriptor = os.open(path, _flags(os.O_RDWR | os.O_CREAT), 0o600)
        except OSError as exc:
            msg = f"Can't read or write {path}: {exc.strerror or exc}"
            raise HeinzelError(msg) from exc
        with os.fdopen(descriptor, "r+", encoding="ascii") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            found = handle.read(64).strip()
            if not found:
                found = "".join(secrets.choice(_ALPHABET) for _ in range(8))
                handle.write(found + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        if not MACHINE.fullmatch(found):
            msg = f"{path} doesn't hold a machine id; move it aside and hz makes a new one"
            raise HeinzelError(msg)
        return found

    def note_presence(self, today: date, version: str) -> bool:
        """Append today's presence line unless this month's segment has one; whether it appended.

        Raises:
            HeinzelError: If the segment can't be written.
        """
        line = PresenceLine(date=today, hz=version)

        def absent(existing: bytes) -> bool:
            return not any(isinstance(p, PresenceLine) and p.date == today for p in _lines("presence", existing))

        return self._append("presence", today, line.model_dump_json().encode() + b"\n", absent)

    def note_version(self, today: date, version: str, os_family: Os) -> AdoptionLine | None:
        """Append `installed` the first time, `upgraded` when the version changed since the last line; that line.

        Raises:
            HeinzelError: If a segment can't be read or written.
        """
        latest = self._latest_adoption()
        if latest is not None and latest.hz == version:
            return None
        line = AdoptionLine(kind="installed" if latest is None else "upgraded", date=today, hz=version, os=os_family)

        def unchanged(existing: bytes) -> bool:  # another process may have written it since `_latest_adoption`
            lines = [p for p in _lines("adoption", existing) if isinstance(p, AdoptionLine)]
            return not lines or lines[-1].hz != version

        return line if self._append("adoption", today, line.model_dump_json().encode() + b"\n", unchanged) else None

    def _latest_adoption(self) -> AdoptionLine | None:
        for segment in reversed(self.segments("adoption")):
            lines = [p for p in _lines("adoption", segment.data) if isinstance(p, AdoptionLine)]
            if lines:
                return lines[-1]
        return None

    def _append(self, stream: str, day: date, line: bytes, wanted: Callable[[bytes], bool]) -> bool:
        directory = self._directory(self._directory(self.root) / stream)
        path = directory / f"{day:%Y-%m}{SUFFIX}"
        try:
            descriptor = os.open(path, _flags(os.O_RDWR | os.O_CREAT | os.O_APPEND), 0o600)
        except OSError as exc:
            msg = f"Can't write {path}: {exc.strerror or exc}"
            raise HeinzelError(msg) from exc
        with os.fdopen(descriptor, "r+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            existing = handle.read(MAX_SEGMENT + 1)
            if len(existing) + len(line) > MAX_SEGMENT:
                raise HeinzelError(_too_large(path))
            if not wanted(existing):
                return False
            if existing and not existing.endswith(b"\n"):
                handle.write(b"\n")  # a line cut off by a crash stays a line of its own, and is reported as invalid
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def segments(self, stream: str) -> list[Segment]:
        """The stream's local segments, oldest first, read under a shared lock; links and stray files are skipped.

        Raises:
            HeinzelError: If a segment is too large or can't be read.
        """
        directory = self.root / stream
        if not directory.is_dir() or directory.is_symlink():
            return []
        found: list[Segment] = []
        for path in sorted(directory.iterdir()):
            if not SEGMENT.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                continue
            found.append(Segment(stream, path.name, _locked_read(path)))
        return found

    def everything(self) -> list[Segment]:
        """Every stream's segments, stream by stream."""
        return [segment for stream in STREAMS for segment in self.segments(stream)]


def _locked_read(path: Path) -> bytes:
    try:
        descriptor = os.open(path, _flags(os.O_RDONLY))
    except OSError as exc:
        msg = f"Can't read {path}: {exc.strerror or exc}"
        raise HeinzelError(msg) from exc
    with os.fdopen(descriptor, "rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        data = handle.read(MAX_SEGMENT + 1)
    if len(data) > MAX_SEGMENT:
        raise HeinzelError(_too_large(path))
    return data


def _too_large(path: Path) -> str:
    return f"{path} is over {MAX_SEGMENT // 1024 // 1024} MiB; a stream holds a line a day, so something is wrong"


def _lines(stream: str, data: bytes) -> Iterator[PresenceLine | AdoptionLine]:
    model = STREAMS[stream]
    for raw in data.splitlines():
        if not raw.strip() or len(raw) > MAX_LINE:
            continue
        try:
            yield model.model_validate_json(raw)  # type: ignore[misc]  # one of the two line models
        except ValidationError:
            continue


def problems(segment: Segment) -> list[str]:
    """Why a segment may not be pushed: each line that isn't one of its stream's, or is dated in another month."""
    found: list[str] = []
    model = STREAMS[segment.stream]
    month = segment.name.removesuffix(SUFFIX)
    for number, raw in enumerate(segment.data.splitlines(), start=1):
        if len(raw) > MAX_LINE:
            found.append(f"{segment.path}:{number}: longer than {MAX_LINE} bytes")
            continue
        try:
            line = model.model_validate_json(raw)
        except ValidationError as exc:
            found.append(f"{segment.path}:{number}: {exc.errors()[0]['msg']}")
            continue
        if f"{line.date:%Y-%m}" != month:
            found.append(f"{segment.path}:{number}: dated {line.date}, not in {month}")
    if segment.data and not segment.data.endswith(b"\n"):
        found.append(f"{segment.path}: its last line is cut off")
    return found
