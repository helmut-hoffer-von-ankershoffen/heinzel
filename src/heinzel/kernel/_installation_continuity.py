"""Local, non-executing continuity evidence for an owned uv tool (ADR 0108)."""

import contextlib
import fcntl
import hashlib
import os
import re
import secrets
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from heinzel.kernel._errors import HeinzelError


class _Digest(Protocol):
    def update(self, data: bytes, /) -> None: ...


_RECORD = ".heinzel-startup-state"
_PENDING = ".heinzel-startup-pending"
_MAX_FILES = 100_000
_MAX_BYTES = 2_000_000_000
_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_DIRECTORY = _FLAGS | os.O_DIRECTORY


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _owned(info: os.stat_result, *, external: bool = False) -> None:
    owners = {os.getuid(), 0} if external else {os.getuid()}
    if info.st_uid not in owners or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
        msg = "Installation contains a file with unsafe ownership or permissions."
        raise HeinzelError(msg)


def _regular(fd: int, digest: _Digest, budget: list[int]) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES - budget[1]:
        msg = "Unsafe or oversized installation file."
        raise HeinzelError(msg)
    budget[1] += info.st_size
    digest.update(info.st_size.to_bytes(8, "big"))
    remaining = info.st_size
    while remaining:
        chunk = os.read(fd, min(remaining, 1024 * 1024))
        if not chunk:
            msg = "Installation changed during inspection."
            raise HeinzelError(msg)
        digest.update(chunk)
        remaining -= len(chunk)
    if _identity(info) != _identity(os.fstat(fd)):
        msg = "Installation changed during inspection."
        raise HeinzelError(msg)


def _external_parents(path: Path) -> None:
    for parent in path.parents:
        info = parent.stat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if info.st_uid not in {0, os.getuid()} or (info.st_mode & 0o022 and not sticky_root):
            msg = "External interpreter directory has unsafe ownership or permissions."
            raise HeinzelError(msg)


class _Observation:
    def __init__(self, prefix: Path, *, strict_identity: bool = False, receipt: bytes | None = None) -> None:
        self.prefix = prefix
        self.strict_identity = strict_identity
        self.receipt = receipt
        self.digest = hashlib.sha256()
        self.digest.update(b"heinzel-installation-continuity-v1\0" + os.fsencode(prefix) + b"\0")
        self.budget = [0, 0]

    def visit(self, directory: int, relative: Path) -> None:
        before = _identity(os.fstat(directory))
        with os.scandir(directory) as scan:
            names = sorted(entry.name for entry in scan)
        for name in names:
            path = relative / name
            if path in {Path(_RECORD), Path(_PENDING)}:
                continue
            self.budget[0] += 1
            if self.budget[0] > _MAX_FILES:
                msg = "Installation has too many files to inspect."
                raise HeinzelError(msg)
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            _owned(info)
            if name.endswith((".pyc", ".pyo")) and stat.S_ISREG(info.st_mode):
                continue
            encoded = os.fsencode(str(path))
            if name != "__pycache__" or not stat.S_ISDIR(info.st_mode):
                self.digest.update(len(encoded).to_bytes(8, "big") + encoded + info.st_mode.to_bytes(4, "big"))
                if self.strict_identity:
                    self.digest.update(repr(_identity(info)).encode("ascii"))
            self.entry(directory, name, path, info)
        if before != _identity(os.fstat(directory)):
            msg = "Installation changed during inspection."
            raise HeinzelError(msg)

    def entry(self, directory: int, name: str, path: Path, info: os.stat_result) -> None:
        if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode):
            is_directory = stat.S_ISDIR(info.st_mode)
            fd = os.open(name, _DIRECTORY if is_directory else _FLAGS, dir_fd=directory)
            try:
                if _identity(info) != _identity(os.fstat(fd)):
                    msg = "Installation changed during inspection."
                    raise HeinzelError(msg)
                if is_directory:
                    self.visit(fd, path)
                elif path == Path("uv-receipt.toml") and self.receipt is not None:
                    _regular(fd, hashlib.sha256(), self.budget)
                    self.digest.update(len(self.receipt).to_bytes(8, "big"))
                    self.digest.update(self.receipt)
                else:
                    _regular(fd, self.digest, self.budget)
            finally:
                os.close(fd)
        elif stat.S_ISLNK(info.st_mode):
            self.link(directory, name, path, info)
        else:
            msg = "Installation contains an unsafe file."
            raise HeinzelError(msg)

    def link(self, directory: int, name: str, path: Path, info: os.stat_result) -> None:
        link = os.readlink(name, dir_fd=directory)
        self.digest.update(os.fsencode(link) + b"\0")
        target = (self.prefix / path.parent / link).resolve(strict=True)
        if target.is_relative_to(self.prefix) and (
            target in {self.prefix / _RECORD, self.prefix / _PENDING}
            or (target.suffix in {".pyc", ".pyo"} and target.is_file())
        ):
            msg = "Installation link points to excluded continuity or bytecode content."
            raise HeinzelError(msg)
        if not target.is_relative_to(self.prefix):
            if path.parent != Path("bin") or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", name):
                msg = "Installation contains an unsafe external link."
                raise HeinzelError(msg)
            _external_parents(target)
            fd = os.open(target, _FLAGS)
            try:
                info_target = os.fstat(fd)
                _owned(info_target, external=True)
                self.digest.update(os.fsencode(str(target)) + b"\0")
                self.digest.update(f"{info_target.st_uid}:{info_target.st_mode}\0".encode("ascii"))
                if self.strict_identity:
                    self.digest.update(repr(_identity(info_target)).encode("ascii"))
                _regular(fd, self.digest, self.budget)
            finally:
                os.close(fd)
        if _identity(info) != _identity(os.stat(name, dir_fd=directory, follow_symlinks=False)):
            msg = "Installation changed during inspection."
            raise HeinzelError(msg)


def _snapshot(prefix: Path, *, strict_identity: bool = False, receipt: bytes | None = None) -> str:
    observation = _Observation(prefix, strict_identity=strict_identity, receipt=receipt)
    root = os.open(prefix, _DIRECTORY)
    try:
        info = os.fstat(root)
        observation.digest.update(f"{info.st_dev}:{info.st_ino}\0".encode("ascii"))
        if strict_identity:
            observation.digest.update(repr(_identity(info)).encode("ascii"))
        observation.visit(root, Path())
    finally:
        os.close(root)
    return observation.digest.hexdigest()


def fingerprint(
    prefix: Path, authority: Callable[[], object], *, strict_identity: bool = False, receipt: bytes | None = None
) -> str:
    """Capture twice and validate the tool authority on both sides.

    Raises:
        HeinzelError: If the installation cannot be inspected safely.
    """
    try:
        target = authority()
        if prefix.resolve(strict=True) != prefix:
            msg = "The installation prefix must be a physical path."
            raise HeinzelError(msg)
        info = prefix.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            msg = "The installation prefix is not privately owned."
            raise HeinzelError(msg)
        if receipt is not None:
            first = _snapshot(prefix, strict_identity=strict_identity, receipt=receipt)
            second = _snapshot(prefix, strict_identity=strict_identity, receipt=receipt)
        else:
            first = _snapshot(prefix, strict_identity=True) if strict_identity else _snapshot(prefix)
            second = _snapshot(prefix, strict_identity=True) if strict_identity else _snapshot(prefix)
        if first != second or authority() != target:
            msg = "Installation changed during inspection."
            raise HeinzelError(msg)
    except (OSError, ValueError) as exc:
        msg = "Cannot inspect the owned installation safely."
        raise HeinzelError(msg) from exc
    else:
        return first


def _pending_exists(directory: int) -> bool:
    try:
        os.stat(_PENDING, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def replace_receipt(
    prefix: Path, authority: Callable[[], object], expected: str, transform: Callable[[], bytes]
) -> str:
    """Replace only a projected receipt under the continuity lock; uncertainty retains the pending marker.

    Raises:
        HeinzelError: If authority or generation cannot be established.
        OSError: If a private write or synchronization fails.
    """
    _validate_expected(expected)
    directory, name = os.open(prefix, _DIRECTORY), f".heinzel-receipt-{secrets.token_hex(16)}"
    marker = -1
    committed = False
    try:
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _require_generation(fingerprint(prefix, authority), expected)
        target = authority()
        replacement = transform()
        projected = fingerprint(prefix, authority, receipt=replacement)
        _require_generation(fingerprint(prefix, authority), expected)
        info = os.stat("uv-receipt.toml", dir_fd=directory, follow_symlinks=False)
        marker = os.open(_PENDING, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        os.fsync(marker)
        marker_info = os.fstat(marker)
        os.fsync(directory)
        _require_generation(fingerprint(prefix, authority), expected)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            os.fchmod(fd, stat.S_IMODE(info.st_mode))
            if os.write(fd, replacement) != len(replacement):
                msg = "Could not write the complete uv receipt."
                raise HeinzelError(msg)
            os.fsync(fd)
            if _identity(os.stat("uv-receipt.toml", dir_fd=directory, follow_symlinks=False)) != _identity(info):
                msg = "The uv receipt changed during replacement."
                raise HeinzelError(msg)
            os.replace(name, "uv-receipt.toml", src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
            if authority() != target or fingerprint(prefix, authority) != projected:
                msg = "The replacement could not be verified; inspect the installation."
                raise HeinzelError(msg)
            if _identity(os.fstat(directory)) != _identity(prefix.stat()):
                msg = "The installation directory changed during replacement."
                raise HeinzelError(msg)
            _verify_marker(directory, marker_info)
            os.unlink(_PENDING, dir_fd=directory)
            committed = True
            return projected
        finally:
            _close_fd(fd, committed=committed)
    finally:
        if not committed:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=directory)
        if marker >= 0:
            _close_fd(marker, committed=committed)
        _close_fd(directory, committed=committed)


def matches(prefix: Path, authority: Callable[[], object], *, pending: bool = False) -> bool:
    """Read existing private evidence; checking never creates or changes it."""
    try:
        directory = os.open(prefix, _DIRECTORY)
        try:
            if not pending and _pending_exists(directory):
                return False
            fd = os.open(_RECORD, _FLAGS, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                    or info.st_nlink != 1
                ):
                    return False
                stored = os.read(fd, 128)
                if _identity(info) != _identity(os.fstat(fd)):
                    return False
            finally:
                os.close(fd)
            current = fingerprint(prefix, authority)
            return (
                _identity(os.stat(_RECORD, dir_fd=directory, follow_symlinks=False)) == _identity(info)
                and _identity(os.fstat(directory)) == _identity(prefix.stat())
                and (pending or not _pending_exists(directory))
                and stored == (current + "\n").encode("ascii")
            )
        finally:
            os.close(directory)
    except (OSError, HeinzelError):
        return False


def record(prefix: Path, authority: Callable[[], object], *, expected: str) -> None:
    """Seed only after the caller has independently verified an authorized mutation.

    Raises:
        HeinzelError: If the installation changes during inspection.
        OSError: If the private write, synchronization or writer lock fails.
    """
    _validate_expected(expected)
    directory = os.open(prefix, _DIRECTORY)
    name = f".heinzel-continuity-{secrets.token_hex(16)}"
    marker = -1
    committed = False
    try:
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        value = fingerprint(prefix, authority)
        _require_generation(value, expected)
        original_authority = authority()
        marker = os.open(_PENDING, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        os.fsync(marker)
        marker_info = os.fstat(marker)
        os.fsync(directory)
        if fingerprint(prefix, authority) != value:
            msg = "Installation changed before recording continuity."
            raise HeinzelError(msg)
        if _identity(os.fstat(directory)) != _identity(prefix.stat()):
            msg = "Installation directory changed before recording continuity."
            raise HeinzelError(msg)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            data = (value + "\n").encode("ascii")
            if os.write(fd, data) != len(data):
                msg = "Could not write the complete continuity record."
                raise HeinzelError(msg)
            os.fsync(fd)
            created = os.fstat(fd)
            os.replace(name, _RECORD, src_dir_fd=directory, dst_dir_fd=directory)
            try:
                os.fsync(directory)
                _confirm_record(prefix, authority, original_authority, value)
            except (OSError, HeinzelError):
                _invalidate_record(directory, created)
                raise
            _verify_marker(directory, marker_info)
            os.unlink(_PENDING, dir_fd=directory)
            committed = True
        finally:
            _close_fd(fd, committed=committed)
    finally:
        if not committed:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=directory)
        if marker >= 0:
            _close_fd(marker, committed=committed)
        _close_fd(directory, committed=committed)


def _validate_expected(expected: object) -> None:
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        msg = "The expected installation fingerprint is invalid."
        raise HeinzelError(msg)


def _require_generation(value: str, expected: str) -> None:
    if value != expected:
        msg = "The installation no longer matches the verified generation."
        raise HeinzelError(msg)


def _close_fd(fd: int, *, committed: bool) -> None:
    if committed:
        with contextlib.suppress(OSError):
            os.close(fd)
    else:
        os.close(fd)


def _verify_marker(directory: int, expected: os.stat_result) -> None:
    current = os.stat(_PENDING, dir_fd=directory, follow_symlinks=False)
    if (
        _identity(current) != _identity(expected)
        or not stat.S_ISREG(current.st_mode)
        or current.st_uid != os.getuid()
        or current.st_mode & 0o077
        or current.st_nlink != 1
    ):
        msg = "Continuity pending marker changed; inspect the installation."
        raise HeinzelError(msg)


def _invalidate_record(directory: int, created: os.stat_result) -> None:
    """Remove this transaction's unconfirmed record if it has not been replaced."""
    try:
        current = os.stat(_RECORD, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
        os.unlink(_RECORD, dir_fd=directory)
        os.fsync(directory)


def _confirm_record(prefix: Path, authority: Callable[[], object], expected: object, value: str) -> None:
    msg = "Continuity record could not be confirmed after writing; inspect the installation."
    try:
        confirmed = (
            authority() == expected
            and fingerprint(prefix, authority) == value
            and matches(prefix, authority, pending=True)
        )
    except (OSError, HeinzelError) as exc:
        raise HeinzelError(msg) from exc
    if not confirmed:
        raise HeinzelError(msg)


def external_python(path: Path) -> tuple[str, str, tuple[int, ...]]:
    """Capture a physical, safely owned external interpreter without executing it.

    Raises:
        HeinzelError: If the interpreter cannot be verified.
    """
    msg = "The original tool interpreter cannot be verified; inspect the installation before updating."
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise HeinzelError(msg)
        _external_parents(path)
        fd = os.open(path, _FLAGS)
        try:
            info = os.fstat(fd)
            _owned(info, external=True)
            if not info.st_mode & 0o111:
                raise HeinzelError(msg)
            digest = hashlib.sha256()
            _regular(fd, digest, [0, 0])
            return str(path), digest.hexdigest(), _identity(info)
        finally:
            os.close(fd)
    except OSError as exc:
        raise HeinzelError(msg) from exc


def tool_python(prefix: Path) -> tuple[str, str, tuple[int, ...]]:
    """Capture the selected tool's external Python link and its bytes.

    Raises:
        HeinzelError: If the link or target cannot be verified.
    """
    msg = "The original tool interpreter cannot be verified; inspect the installation before updating."
    try:
        prefix = prefix.resolve(strict=True)
        link = prefix / "bin/python"
        before = link.lstat()
        if not stat.S_ISLNK(before.st_mode):
            raise HeinzelError(msg)
        target = link.resolve(strict=True)
        if target.is_relative_to(prefix):
            raise HeinzelError(msg)
        result = external_python(target)
        if _identity(link.lstat()) != _identity(before):
            raise HeinzelError(msg)
    except OSError as exc:
        raise HeinzelError(msg) from exc
    else:
        return result
