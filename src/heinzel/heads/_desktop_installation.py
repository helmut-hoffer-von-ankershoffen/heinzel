"""Manage the per-user macOS desktop bundle and its Finder CLI binding."""

import ctypes
import fcntl
import json
import os
import platform
import plistlib
import shutil
import stat
import struct
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp
from typing import Literal, cast
from xml.parsers.expat import ExpatError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heinzel import HeinzelError
from heinzel.heads._desktop_archive import BUNDLE_NAME, extract_bundle
from heinzel.heads._desktop_release import VERSION, fetch_archive
from heinzel.heads._desktop_seal import (
    SealReport,
    clear_our_quarantine,
    ensure_sealed,
    inspect_bundle,
    mark_quarantined,
)

_RECEIPT = "desktop-installation.json"
_BINDING = "desktop-owned-cli.json"
_JOURNAL = "desktop-installation-journal.json"
_USE_LOCK = "desktop-use.lock"
_STAGE_PREFIX = ".heinzel-desktop-"
_CONFLICT = "The desktop bundle or its metadata is present without a matching ownership receipt."
_RENAME_SWAP = 0x00000002
_RENAME_EXCL = 0x00000004
_MAX_METADATA_BYTES = 4096
_MAX_PLIST_BYTES = 64 * 1024
_MACH_HEADER = struct.Struct("<8I")
_MACH_64 = 0xFEEDFACF
_CPU_ARM64 = 0x0100000C
_MACH_EXECUTE = 2
_BUNDLE_ID = "org.heinzelmaennchen.desktop"
_BUNDLE_EXECUTABLE = "Heinzelmaennchen"
type DesktopInstallStatus = Literal["installed", "updated", "current", "deferred", "absent"]


class DesktopInstallationError(HeinzelError):
    """The desktop bundle could not be managed safely."""


class _Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    release: str
    device: int
    inode: int
    birth_time: int | None
    executable: str

    def binding(self) -> bytes:
        return json.dumps({"version": 1, "executable": self.executable}, ensure_ascii=False).encode()


type _Identity = tuple[int, int, int | None]
type _JournalState = Literal["pending", "committed", "rolled_back"]


class _MoveIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    public: _Identity | None
    private: _Identity | None


class _Journal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    state: _JournalState = "pending"
    operation: Literal["install", "uninstall"]
    staging: str = Field(pattern=r"^\.heinzel-desktop-[a-z0-9_]{8}$")
    metadata: str = Field(pattern=r"^\.heinzel-desktop-[a-z0-9_]{8}$")
    staging_identity: _Identity
    metadata_identity: _Identity
    bundle: _MoveIdentity
    binding: _MoveIdentity
    receipt: _MoveIdentity


@dataclass(frozen=True)
class _Move:
    public: Path
    private: Path
    identity: _MoveIdentity

    def status(self) -> bool:
        actual = (_identity(self.public), _identity(self.private))
        before = (self.identity.public, self.identity.private)
        if actual == before:
            return False
        if actual == before[::-1]:
            return True
        raise DesktopInstallationError(_CONFLICT)

    def apply(self, *, reverse: bool = False) -> None:
        source, target = (self.public, self.private) if self.identity.private is None else (self.private, self.public)
        if reverse:
            source, target = target, source
        _rename(source, target, swap=self.identity.public is not None and self.identity.private is not None)
        _sync_directory(source.parent)
        _sync_directory(target.parent)


def _check_platform() -> None:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        msg = "Managed desktop installation requires an Apple silicon Mac."
        raise DesktopInstallationError(msg)


def _owned_directory(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        msg = f"The desktop installer cannot safely use {path}."
        raise DesktopInstallationError(msg)


@contextmanager
def _open_lock(path: Path) -> Generator[int]:
    fd = os.open(path, os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_RDWR, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            msg = "The desktop installer could not establish a safe installation lock."
            raise DesktopInstallationError(msg)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _locked_config(home: Path) -> Generator[Path]:
    config = home / ".config" / "heinzel"
    _owned_directory(config.parent)
    _owned_directory(config)
    with _open_lock(config / "desktop-installation.lock") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield config


@contextmanager
def _desktop_use(config: Path) -> Generator[bool]:
    # The Finder launcher takes the same BSD lock for its entire managed session.
    with _open_lock(config / _USE_LOCK) as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


def _read_metadata(path: Path) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise DesktopInstallationError(_CONFLICT)
        contents = source.read(_MAX_METADATA_BYTES + 1)
        if len(contents) > _MAX_METADATA_BYTES:
            raise DesktopInstallationError(_CONFLICT)
        return contents


def _owned_installation(bundle: Path, config: Path) -> _Receipt | None:
    receipt = _read_metadata(config / _RECEIPT)
    binding = _read_metadata(config / _BINDING)
    try:
        info = bundle.lstat()
    except FileNotFoundError as exc:
        if receipt is None and binding is None:
            return None
        raise DesktopInstallationError(_CONFLICT) from exc
    if receipt is None or binding is None:
        raise DesktopInstallationError(_CONFLICT)
    try:
        record = _Receipt.model_validate_json(receipt)
    except ValidationError as exc:
        raise DesktopInstallationError(_CONFLICT) from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or (info.st_dev, info.st_ino) != (record.device, record.inode)
        or getattr(info, "st_birthtime_ns", None) != record.birth_time
        or not VERSION.fullmatch(record.release)
        or not Path(record.executable).is_absolute()
        or "\0" in record.executable
        or binding != record.binding()
    ):
        raise DesktopInstallationError(_CONFLICT)
    return record


def _in_use(bundle: Path) -> bool:
    result = subprocess.run(["/bin/ps", "-ww", "-axo", "comm="], check=True, stdout=subprocess.PIPE, timeout=10)
    prefix = os.fsencode(bundle) + b"/"
    translocated = b"/AppTranslocation/"
    name = b"/" + os.fsencode(bundle.name) + b"/"
    return any(
        line.startswith(prefix) or (translocated in line and name in line) for line in result.stdout.splitlines()
    )


def _verify_bundle(bundle: Path, version: str) -> None:
    contents = bundle / "Contents"
    metadata = contents / "Info.plist"
    executable = contents / "MacOS" / _BUNDLE_EXECUTABLE
    problem = "The verified archive does not contain the expected Apple silicon desktop bundle."
    try:
        info = metadata.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_PLIST_BYTES:
            raise DesktopInstallationError(problem)
        identity = plistlib.loads(metadata.read_bytes())
        if not isinstance(identity, dict):
            raise DesktopInstallationError(problem)
        values = cast("dict[str, object]", identity)
        if (
            values.get("CFBundleIdentifier"),
            values.get("CFBundleExecutable"),
            values.get("CFBundleShortVersionString"),
        ) != (_BUNDLE_ID, _BUNDLE_EXECUTABLE, version):
            raise DesktopInstallationError(problem)
        mode = executable.lstat().st_mode
        if not stat.S_ISREG(mode) or not mode & stat.S_IXUSR:
            raise DesktopInstallationError(problem)
        with executable.open("rb") as file:
            magic, cpu, _, kind, *_ = _MACH_HEADER.unpack(file.read(_MACH_HEADER.size))
        if (magic, cpu, kind) != (_MACH_64, _CPU_ARM64, _MACH_EXECUTE):
            raise DesktopInstallationError(problem)
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError, struct.error) as exc:
        raise DesktopInstallationError(problem) from exc


def _prepare_for_gatekeeper(bundle: Path, *, keep_quarantine: bool) -> None:
    """Seal the checksum- and provenance-verified staging bundle; quarantine it only when asked.

    The release was downloaded and verified by Heinzel itself, so macOS has not quarantined it. Adding our
    own mark to a bundle with a broken seal is what made macOS call it damaged (owner report, 0.9.26).
    """
    ensure_sealed(bundle)
    if keep_quarantine:
        mark_quarantined(bundle)


def _rename(source: Path, target: Path, *, swap: bool = False) -> None:
    """Rename exclusively, or atomically exchange an owned target with the staging item.

    Raises:
        OSError: If the filesystem rejects the requested operation.
    """
    rename = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).renamex_np
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(target), _RENAME_SWAP if swap else _RENAME_EXCL):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def _identity(path: Path) -> _Identity | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
    ):
        raise DesktopInstallationError(_CONFLICT)
    return (info.st_dev, info.st_ino, getattr(info, "st_birthtime_ns", None))


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _raise_walk_error(error: OSError) -> None:
    raise error


def _sync_tree(root: Path) -> None:
    for directory, _, files in os.walk(root, topdown=False, onerror=_raise_walk_error):
        for name in files:
            path = Path(directory, name)
            if not path.is_symlink():
                with path.open("rb") as file:
                    os.fsync(file.fileno())
        _sync_directory(Path(directory))


def _write_journal(config: Path, journal: _Journal) -> None:
    with NamedTemporaryFile(dir=config, prefix=".desktop-journal-", delete=False) as file:
        temp = Path(file.name)
        try:
            file.write(journal.model_dump_json().encode())
            file.flush()
            os.fsync(file.fileno())
            Path(temp).replace(config / _JOURNAL)
            _sync_directory(config)
        finally:
            temp.unlink(missing_ok=True)


def _moves(bundle: Path, config: Path, journal: _Journal) -> tuple[_Move, ...]:
    staging = bundle.parent / journal.staging
    metadata = config / journal.metadata
    app_move = _Move(bundle, staging / BUNDLE_NAME, journal.bundle)
    binding_move = _Move(config / _BINDING, metadata / _BINDING, journal.binding)
    receipt_move = _Move(config / _RECEIPT, metadata / _RECEIPT, journal.receipt)
    if journal.operation == "uninstall":
        return (app_move, binding_move, receipt_move)
    # Finder needs the binding by the time the app appears at its public path.
    return (binding_move, app_move, receipt_move)


def _recover_transaction(bundle: Path, config: Path) -> None:
    data = _read_metadata(config / _JOURNAL)
    if data is None:
        return
    try:
        journal = _Journal.model_validate_json(data)
    except ValidationError as exc:
        raise DesktopInstallationError(_CONFLICT) from exc

    directories = (
        (bundle.parent / journal.staging, journal.staging_identity),
        (config / journal.metadata, journal.metadata_identity),
    )
    for directory, expected in directories:
        actual = _identity(directory)
        if actual != expected and (journal.state == "pending" or actual is not None):
            raise DesktopInstallationError(_CONFLICT)

    moves = _moves(bundle, config, journal)
    if journal.state == "pending":
        _rollback_pending(config, journal, moves)

    for move in moves:
        expected_public = move.identity.private if journal.state == "committed" else move.identity.public
        if _identity(move.public) != expected_public:
            raise DesktopInstallationError(_CONFLICT)

    _owned_installation(bundle, config)
    for directory, _ in directories:
        with suppress(FileNotFoundError):
            shutil.rmtree(directory)
        _sync_directory(directory.parent)
    (config / _JOURNAL).unlink()
    _sync_directory(config)


def _rollback_pending(config: Path, journal: _Journal, moves: tuple[_Move, ...]) -> None:
    # Check all entries before touching any. A crash during recovery leaves another valid prefix.
    statuses = tuple(move.status() for move in moves)
    if statuses != tuple(sorted(statuses, reverse=True)):
        raise DesktopInstallationError(_CONFLICT)
    for move, applied in reversed(tuple(zip(moves, statuses, strict=True))):
        if applied:
            move.apply(reverse=True)
    journal.state = "rolled_back"
    _write_journal(config, journal)


def _move_identity(public: Path, private: Path) -> _MoveIdentity:
    return _MoveIdentity(public=_identity(public), private=_identity(private))


def _commit(bundle: Path, config: Path, staging: Path, files: Path, operation: Literal["install", "uninstall"]) -> None:
    staging_identity = _identity(staging)
    metadata_identity = _identity(files)
    if staging_identity is None or metadata_identity is None:
        raise DesktopInstallationError(_CONFLICT)
    journal = _Journal(
        operation=operation,
        staging=staging.name,
        metadata=files.name,
        staging_identity=staging_identity,
        metadata_identity=metadata_identity,
        bundle=_move_identity(bundle, staging / BUNDLE_NAME),
        binding=_move_identity(config / _BINDING, files / _BINDING),
        receipt=_move_identity(config / _RECEIPT, files / _RECEIPT),
    )
    _sync_directory(staging)
    _sync_directory(files)
    _sync_directory(staging.parent)
    _sync_directory(files.parent)
    try:
        _write_journal(config, journal)
        for move in _moves(bundle, config, journal):
            move.apply()
        journal.state = "committed"
        _write_journal(config, journal)
    finally:
        _recover_transaction(bundle, config)


@contextmanager
def _staging(applications: Path, config: Path) -> Generator[tuple[Path, Path]]:
    files = Path(mkdtemp(prefix=_STAGE_PREFIX, dir=config))
    try:
        staging = Path(mkdtemp(prefix=_STAGE_PREFIX, dir=applications))
        try:
            yield staging, files
        finally:
            if not os.path.lexists(config / _JOURNAL):
                with suppress(FileNotFoundError):
                    shutil.rmtree(staging)
    finally:
        if not os.path.lexists(config / _JOURNAL):
            with suppress(FileNotFoundError):
                shutil.rmtree(files)


def _recover_before_use(bundle: Path, config: Path) -> bool:
    if not os.path.lexists(config / _JOURNAL):
        return True
    with _desktop_use(config) as locked:
        if not locked or _in_use(bundle):
            return False
        _recover_transaction(bundle, config)
    return True


def install_desktop(
    version: str,
    executable: Path,
    *,
    update_only: bool = False,
    home: Path | None = None,
    keep_quarantine: bool = False,
) -> DesktopInstallStatus:
    """Install or refresh a verified desktop release, or defer if the bundle is in use."""
    _check_platform()
    home = (home or Path.home()).resolve(strict=True)
    applications = home / "Applications"
    bundle = applications / BUNDLE_NAME
    with _locked_config(home) as config:
        if os.path.lexists(applications):
            _owned_directory(applications)
        if not _recover_before_use(bundle, config):
            return "deferred"
        existing = _owned_installation(bundle, config)
        if update_only:
            if existing is None:
                return "absent"
            if VERSION.fullmatch(version) and _version_parts(existing.release) > _version_parts(version):
                return "current"
        _owned_directory(applications)
        return _install_release(version, executable, bundle, config, existing, keep_quarantine=keep_quarantine)


def _version_parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(data)


def _install_release(  # noqa: PLR0913 - the transaction needs each of these
    version: str, executable: Path, bundle: Path, config: Path, existing: _Receipt | None, *, keep_quarantine: bool
) -> DesktopInstallStatus:
    if (
        not VERSION.fullmatch(version)
        or not executable.is_absolute()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        msg = "Desktop installation requires a stable release and an absolute, executable hz path."
        raise DesktopInstallationError(msg)
    if existing and existing.release == version and existing.executable == str(executable):
        return "current"
    if existing and _in_use(bundle):
        return "deferred"
    with _staging(bundle.parent, config) as (staging, files):
        archive = fetch_archive(version, staging)
        expanded = staging / "expanded"
        expanded.mkdir(mode=0o700)
        candidate = extract_bundle(archive, expanded)
        _verify_bundle(candidate, version)
        _prepare_for_gatekeeper(candidate, keep_quarantine=keep_quarantine)
        candidate.rename(staging / BUNDLE_NAME)
        info = (staging / BUNDLE_NAME).lstat()
        record = _Receipt(
            release=version,
            device=info.st_dev,
            inode=info.st_ino,
            birth_time=getattr(info, "st_birthtime_ns", None),
            executable=str(executable),
        )
        metadata = record.model_dump_json().encode()
        binding = record.binding()
        if max(len(metadata), len(binding)) > _MAX_METADATA_BYTES:
            msg = "The desktop installation metadata exceeds the allowed size."
            raise DesktopInstallationError(msg)
        _write_private(files / _RECEIPT, metadata)
        _write_private(files / _BINDING, binding)
        _sync_tree(staging / BUNDLE_NAME)
        _sync_tree(files)
        with _desktop_use(config) as locked:
            if not locked or (existing and _in_use(bundle)):
                return "deferred"
            if _owned_installation(bundle, config) != existing:
                raise DesktopInstallationError(_CONFLICT)
            _commit(bundle, config, staging, files, "install")
    return "updated" if existing else "installed"


def uninstall_desktop(*, home: Path | None = None) -> bool:
    """Remove an owned, stopped desktop bundle and its binding.

    Raises:
        DesktopInstallationError: If the running bundle must be quit before removal.
    """
    _check_platform()
    home = (home or Path.home()).resolve(strict=True)
    applications = home / "Applications"
    bundle = applications / BUNDLE_NAME
    with _locked_config(home) as config:
        if os.path.lexists(applications):
            _owned_directory(applications)
        if not _recover_before_use(bundle, config):
            msg = "Quit Heinzelmännchen before uninstalling the desktop app."
            raise DesktopInstallationError(msg)
        existing = _owned_installation(bundle, config)
        if existing is None:
            return False
        with _desktop_use(config) as locked:
            if not locked or _in_use(bundle):
                msg = "Quit Heinzelmännchen before uninstalling the desktop app."
                raise DesktopInstallationError(msg)
            if _owned_installation(bundle, config) != existing:
                raise DesktopInstallationError(_CONFLICT)
            with _staging(applications, config) as (staging, files):
                _commit(bundle, config, staging, files, "uninstall")
    return True


def doctor_desktop(*, fix: bool = False, home: Path | None = None) -> tuple[SealReport | None, list[str]]:
    """Inspect the receipt-owned bundle; with `fix`, reseal it ad hoc and remove our own quarantine mark.

    Only a bundle this installer owns (matching receipt) is repaired, and only while it is not running.
    The ad-hoc seal records the installed bytes and adds no trust; the download was checked against its
    published sha256 and build provenance when it was installed. Nothing is launched.

    Returns:
        The state after any repair (None when no owned app is installed) and the repairs made.

    Raises:
        DesktopInstallationError: If the app is running or its ownership metadata does not match.
    """
    _check_platform()
    home = (home or Path.home()).resolve(strict=True)
    bundle = home / "Applications" / BUNDLE_NAME
    with _locked_config(home) as config:
        if not _recover_before_use(bundle, config):
            msg = "Quit Heinzelmännchen before checking the desktop app."
            raise DesktopInstallationError(msg)
        if _owned_installation(bundle, config) is None:
            return None, []
        repairs: list[str] = []
        if fix:
            with _desktop_use(config) as locked:
                if not locked or _in_use(bundle):
                    msg = "Quit Heinzelmännchen before repairing the desktop app."
                    raise DesktopInstallationError(msg)
                if ensure_sealed(bundle):
                    repairs.append("Sealed the installed app with a local ad-hoc signature.")
                if clear_our_quarantine(bundle):
                    repairs.append("Removed the quarantine mark Heinzel had added.")
                if repairs:
                    _sync_tree(bundle)
        return inspect_bundle(bundle), repairs
