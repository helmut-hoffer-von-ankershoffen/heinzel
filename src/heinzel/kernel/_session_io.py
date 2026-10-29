"""Private descriptor-relative session files and their hash chains (ADR 0049)."""

import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from pydantic import JsonValue, TypeAdapter, ValidationError

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._session import SessionEvent, SessionRead

PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
MAX_LINE = 64 * 1024
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
JSON_VALUE = TypeAdapter(JsonValue)


def identifier(value: str) -> str:
    """Refuse a path disguised as a session or turn id.

    Raises:
        HeinzelError: If the id is not one safe path component.
    """
    if not ID.fullmatch(value):
        msg = "A session or turn id must contain only letters, numbers, underscores and dashes"
        raise HeinzelError(msg)
    return value


@contextmanager
def directory(path: Path) -> Generator[int]:
    """Open the owner-controlled root without following its final component.

    Yields:
        The directory descriptor, closed when the context exits.

    Raises:
        HeinzelError: If the root is not a private directory owned by this user.
    """
    try:
        path.mkdir(mode=PRIVATE_DIR, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        msg = "The session root cannot be opened safely; choose a private directory owned by you"
        raise HeinzelError(msg) from None
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != PRIVATE_DIR:
            msg = "The session root must be owned by you with mode 0700"
            raise HeinzelError(msg)
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def child(parent: int, name: str, *, create: bool = False) -> Generator[int]:
    """Open a private subdirectory beneath an already-open directory.

    Yields:
        The child directory descriptor, closed when the context exits.

    Raises:
        HeinzelError: If the directory is not private or is not owned by this user.
    """
    if create:
        try:
            os.mkdir(name, PRIVATE_DIR, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != PRIVATE_DIR:
            msg = "Session directories must be private and owned by you"
            raise HeinzelError(msg)
        yield descriptor
    finally:
        os.close(descriptor)


def open_file(parent: int, name: str, flags: int) -> int:
    """Open a private regular file without following links.

    Raises:
        HeinzelError: If the target is not a private regular file owned by this user.
    """
    safe_flags = flags | os.O_NOFOLLOW | os.O_NONBLOCK
    if flags & os.O_CREAT and not flags & os.O_EXCL:
        # Darwin can report ENOENT to simultaneous O_CREAT|O_NOFOLLOW callers. Exclusive creation followed by
        # opening an existing entry avoids that race while retaining descriptor-relative no-follow validation.
        try:
            descriptor = os.open(name, safe_flags | os.O_EXCL, PRIVATE_FILE, dir_fd=parent)
        except FileExistsError:
            descriptor = os.open(name, safe_flags & ~os.O_CREAT, dir_fd=parent)
    else:
        descriptor = os.open(name, safe_flags, PRIVATE_FILE, dir_fd=parent)
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != PRIVATE_FILE
        or info.st_nlink != 1
    ):
        os.close(descriptor)
        msg = "Session files must be private regular files owned by you"
        raise HeinzelError(msg)
    return descriptor


def encoded(value: JsonValue) -> bytes:
    """One canonical JSON line, including the newline covered by the next event's hash."""
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def append_line(parent: int, name: str, line: bytes) -> None:
    """Append one line and sync the file and directory before returning.

    Raises:
        HeinzelError: If the target is not a private regular file owned by this user.
    """
    descriptor = open_file(parent, name, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    with os.fdopen(descriptor, "ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    os.fsync(parent)


def read_checkpoint(parent: int, name: str) -> dict[str, JsonValue] | None:
    """Read one bounded atomic session checkpoint, or None before its first write.

    Raises:
        HeinzelError: If the checkpoint name, file or content is unsafe.
    """
    identifier(name)
    filename = f"checkpoint-{name}.json"
    try:
        descriptor = open_file(parent, filename, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        content = handle.read(MAX_LINE + 1)
    if len(content) > MAX_LINE or not content.endswith(b"\n"):
        msg = "The session checkpoint is oversized or incomplete; repair it before continuing"
        raise HeinzelError(msg)
    try:
        value = JSON_VALUE.validate_json(content)
    except ValidationError:
        msg = "The session checkpoint is invalid; repair it before continuing"
        raise HeinzelError(msg) from None
    if not isinstance(value, dict):
        msg = "The session checkpoint is invalid; repair it before continuing"
        raise HeinzelError(msg)
    return value


def replace_checkpoint(parent: int, name: str, value: dict[str, JsonValue]) -> None:
    """Atomically replace one bounded private checkpoint and fsync its directory.

    Raises:
        HeinzelError: If the checkpoint name, file or content is unsafe.
    """
    identifier(name)
    filename = f"checkpoint-{name}.json"
    payload = encoded(value)
    if len(payload) > MAX_LINE:
        msg = "The session checkpoint exceeds the storage limit"
        raise HeinzelError(msg)
    try:
        descriptor = open_file(parent, filename, os.O_RDONLY)
    except FileNotFoundError:
        pass
    else:
        os.close(descriptor)
    temporary = f".checkpoint-{name}-{secrets.token_hex(12)}"
    descriptor = open_file(parent, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, filename, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=parent)
        raise


def scan(parent: int) -> SessionRead:
    """Verify the entire chain, including unknown kinds; retain the good prefix after damage.

    Raises:
        HeinzelError: If a spine is not a private regular file owned by this user.
    """
    try:
        descriptor = open_file(parent, "session.jsonl", os.O_RDONLY)
    except FileNotFoundError:
        return SessionRead()
    result = SessionRead()
    previous: str | None = None
    with os.fdopen(descriptor, "rb") as handle:
        while line := handle.readline(MAX_LINE + 1):
            number = len(result.events) + 1
            if len(line) > MAX_LINE or not line.endswith(b"\n"):
                result.problem = f"Session line {number} is oversized or incomplete"
                break
            try:
                event = SessionEvent.model_validate_json(line)
            except ValidationError:
                result.problem = f"Session line {number} is not a supported event envelope"
                break
            if event.seq != number or event.prev != previous:
                result.problem = f"Session chain is broken at line {number}"
                break
            previous = hashlib.sha256(line).hexdigest()
            result.events.append(event)
            result.hashes.append(previous)
    return result


def temporary_name() -> str:
    """An unpredictable temporary name for a derived index rebuild."""
    return f".index-{secrets.token_hex(12)}.sqlite"
