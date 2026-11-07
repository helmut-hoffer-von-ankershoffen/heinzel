"""Manage the macOS desktop bundle in /Applications (or ~/Applications) and its Finder CLI binding."""

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
import time
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
# Every message the ownership checks and their recovery print, in one place (the CLI copy is being redesigned).
_REPLACE_HINT = (
    "Run hz app install --replace: it moves that app to the Trash (nothing is deleted) and installs a verified copy."
)
_MSG_NOT_PRIVATE = "{path} is not a private file owned by this user."
_MSG_TOO_LARGE = "{path} is larger than an install record can be."
_MSG_ORPHANED = "Found {found} but no app at {bundle}. Run hz app uninstall to clear the stale record."
_MSG_NO_RECORD = "{bundle} has no install record ({missing} is missing): it was not installed by hz app install. "
_MSG_MISMATCH = "{bundle} is not the copy recorded in {receipt}: it was replaced, restored or copied by hand. "
_MSG_FOREIGN = (
    "{target} is already there and was not installed by hz app install. Run hz app install --replace to move it to "
    "the Trash (nothing is deleted) and install a verified copy."
)
_MSG_SET_ASIDE = "The desktop app was removed without hz app uninstall; kept its old record as {backup}."
_MSG_BACKUP_EXISTS = "{backup} is already there; move it away, then run the command again."
_MSG_REPLACE_RUNNING = "Quit Heinzelmännchen ({bundle}) before replacing it."
_MSG_TRASH_FAILED = "Move {bundle} to the Trash in Finder, then run hz app install again ({reason})."
_MSG_TRASHED = "Moved {bundle}, which hz app install did not install, to the Trash as {name}."
_MSG_JOURNAL_KEPT = "{error} An interrupted install left {journal}; it was kept as it is."
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
SYSTEM_APPLICATIONS = Path("/Applications")
"""Where Finder, Launchpad and Spotlight expect apps; used whenever this user may write there without a password."""
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
    applications: str | None = None
    """The folder the bundle moves in; absent in journals written before /Applications was supported."""


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


def system_applications_usable(path: Path | None = None) -> bool:
    """Whether this user can install into the system Applications folder without sudo or a password prompt.

    True for the usual root:admin 0775 folder when the user is in its group (admin users), or a folder the user
    owns. A world-writable or foreign-owned folder is never used.
    """
    path = path or SYSTEM_APPLICATIONS
    try:
        info = path.lstat()
    except OSError:
        return False
    uid = os.getuid()
    return (
        stat.S_ISDIR(info.st_mode)
        and not info.st_mode & 0o002
        and (info.st_uid == uid or (info.st_uid == 0 and info.st_gid in os.getgroups()))
        and os.access(path, os.W_OK | os.X_OK)
    )


def _applications_directory(applications: Path, home: Path) -> None:
    if applications == home / "Applications":
        _owned_directory(applications)
    elif applications != SYSTEM_APPLICATIONS or not system_applications_usable(applications):
        msg = f"The desktop installer cannot safely use {applications}."
        raise DesktopInstallationError(msg)


def _locate(home: Path, config: Path) -> tuple[Path, _Receipt | None]:
    """Find the owned bundle in /Applications or ~/Applications, else where a new one would go.

    Raises:
        DesktopInstallationError: If the receipt matches neither location or the target is not ours.
    """
    candidates = _bundles(home)
    for bundle in candidates:
        if os.path.lexists(bundle):
            try:
                record = _owned_installation(bundle, config)
            except DesktopInstallationError:
                continue
            if record is not None:
                return bundle, record
    target = candidates[0] if system_applications_usable() else candidates[1]
    if target == candidates[0] and os.path.lexists(target) and _read_metadata(config / _RECEIPT) is None:
        msg = _MSG_FOREIGN.format(target=target)
        raise DesktopInstallationError(msg)
    return target, _owned_installation(target, config)


def _any_in_use(home: Path) -> bool:
    return any(_in_use(folder / BUNDLE_NAME) for folder in (SYSTEM_APPLICATIONS, home / "Applications"))


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
            msg = _MSG_NOT_PRIVATE.format(path=path)
            raise _conflict(msg)
        contents = source.read(_MAX_METADATA_BYTES + 1)
        if len(contents) > _MAX_METADATA_BYTES:
            msg = _MSG_TOO_LARGE.format(path=path)
            raise _conflict(msg)
        return contents


def _conflict(detail: str) -> DesktopInstallationError:
    """The ownership error, naming what was found where and what to do about it."""
    return DesktopInstallationError(f"{_CONFLICT} {detail}")


def _owned_installation(bundle: Path, config: Path) -> _Receipt | None:
    receipt = _read_metadata(config / _RECEIPT)
    binding = _read_metadata(config / _BINDING)
    try:
        info = bundle.lstat()
    except FileNotFoundError as exc:
        if receipt is None and binding is None:
            return None
        found = " and ".join(str(config / name) for name, data in ((_RECEIPT, receipt), (_BINDING, binding)) if data)
        msg = _MSG_ORPHANED.format(found=found, bundle=bundle)
        raise _conflict(msg) from exc
    if receipt is None or binding is None:
        missing = config / (_RECEIPT if receipt is None else _BINDING)
        msg = _MSG_NO_RECORD.format(bundle=bundle, missing=missing)
        raise _conflict(msg + _REPLACE_HINT)
    mismatch = _conflict(_MSG_MISMATCH.format(bundle=bundle, receipt=config / _RECEIPT) + _REPLACE_HINT)
    try:
        record = _Receipt.model_validate_json(receipt)
    except ValidationError as exc:
        raise mismatch from exc
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
        raise mismatch
    return record


def _bundles(home: Path) -> tuple[Path, Path]:
    return (SYSTEM_APPLICATIONS / BUNDLE_NAME, home / "Applications" / BUNDLE_NAME)


def _set_aside(path: Path, stamp: str) -> Path:
    backup = path.with_name(f"{path.name}.orphaned-{stamp}")
    if os.path.lexists(backup):
        msg = _MSG_BACKUP_EXISTS.format(backup=backup)
        raise DesktopInstallationError(msg)
    path.rename(backup)
    return backup


def _clear_orphaned_record(home: Path, config: Path) -> list[str]:
    """Set our own install record aside when the app it describes is gone from both folders.

    This happens when the app was moved to the Trash by hand (macOS offers only that for an app it calls damaged).
    Only the receipt and the binding, both written by this installer, are renamed and kept as backups; nothing is
    deleted and no other file is touched. A pending transaction journal is left to recovery.

    Returns:
        What was set aside, in plain words.
    """
    if os.path.lexists(config / _JOURNAL) or any(os.path.lexists(bundle) for bundle in _bundles(home)):
        return []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    notes: list[str] = []
    for name in (_RECEIPT, _BINDING):
        if _read_metadata(config / name) is not None:
            backup = _set_aside(config / name, stamp)
            notes.append(_MSG_SET_ASIDE.format(backup=backup))
    if notes:
        _sync_directory(config)
    return notes


def _trash_unowned(home: Path, config: Path) -> list[str]:
    """Move every Heinzelmaennchen.app this installer does not own to the Trash; never delete it.

    Returns:
        What was moved, in plain words.

    Raises:
        DesktopInstallationError: If such an app is running or cannot be moved to the Trash.
    """
    notes: list[str] = []
    for bundle in _bundles(home):
        if not os.path.lexists(bundle):
            continue
        with suppress(DesktopInstallationError):
            if _owned_installation(bundle, config) is not None:
                continue
        if _in_use(bundle):
            msg = _MSG_REPLACE_RUNNING.format(bundle=bundle)
            raise DesktopInstallationError(msg)
        trash = home / ".Trash" / f"Heinzelmaennchen {time.strftime('%Y-%m-%d %H.%M.%S')}.app"
        try:
            _rename(bundle, trash)
        except OSError as exc:
            msg = _MSG_TRASH_FAILED.format(bundle=bundle, reason=exc.strerror)
            raise DesktopInstallationError(msg) from exc
        _sync_directory(bundle.parent)
        notes.append(_MSG_TRASHED.format(bundle=bundle, name=trash.name))
    return notes


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


def _journal_bundle(bundle: Path, journal: _Journal) -> Path:
    """The bundle path a journal's transaction moves: the folder it recorded, ~/Applications for older ones.

    Raises:
        DesktopInstallationError: If the journal names a folder the installer never uses.
    """
    if journal.applications is None:
        return bundle
    applications = Path(journal.applications)
    if applications not in {bundle.parent, SYSTEM_APPLICATIONS}:
        raise DesktopInstallationError(_CONFLICT)
    return applications / BUNDLE_NAME


def _recover_transaction(bundle: Path, config: Path) -> None:
    data = _read_metadata(config / _JOURNAL)
    if data is None:
        return
    try:
        journal = _Journal.model_validate_json(data)
    except ValidationError as exc:
        raise DesktopInstallationError(_CONFLICT) from exc
    bundle = _journal_bundle(bundle, journal)

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
        applications=str(bundle.parent),
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


def _recover_before_use(home: Path, config: Path) -> bool:
    if not os.path.lexists(config / _JOURNAL):
        return True
    with _desktop_use(config) as locked:
        if not locked or _any_in_use(home):
            return False
        try:
            _recover_transaction(home / "Applications" / BUNDLE_NAME, config)
        except DesktopInstallationError as exc:
            msg = _MSG_JOURNAL_KEPT.format(error=exc, journal=config / _JOURNAL)
            raise DesktopInstallationError(msg) from exc
    return True


def _migrate(bundle: Path, config: Path) -> Path:
    """Move an owned ~/Applications bundle into /Applications in one rename; keep it where it is otherwise.

    The rename keeps the bundle's identity, so the receipt stays valid and there is never a second copy.
    A different volume (EXDEV) or a refusal leaves the app where it was.
    """
    target = SYSTEM_APPLICATIONS / BUNDLE_NAME
    if bundle == target or not system_applications_usable():
        return bundle
    with _desktop_use(config) as locked:
        if not locked or _in_use(bundle):
            return bundle
        try:
            _rename(bundle, target)
        except OSError:
            return bundle
        _sync_directory(bundle.parent)
        _sync_directory(target.parent)
    return target


def desktop_location(home: Path | None = None) -> Path | None:
    """Where the owned desktop app is installed now, or None when it is not installed."""
    home = (home or Path.home()).resolve(strict=True)
    config = home / ".config" / "heinzel"
    try:
        bundle, record = _locate(home, config)
    except DesktopInstallationError:
        return None
    return bundle if record is not None and bundle.is_dir() else None


def install_desktop(  # noqa: PLR0913 - keyword-only options of one CLI command
    version: str,
    executable: Path,
    *,
    update_only: bool = False,
    home: Path | None = None,
    keep_quarantine: bool = False,
    replace: bool = False,
    notes: list[str] | None = None,
) -> DesktopInstallStatus:
    """Install or refresh a verified desktop release, or defer if the bundle is in use.

    A record whose app is gone is set aside first. With `replace`, an app this installer did not install is moved to
    the Trash before the install. What was set aside or moved is appended to `notes`.
    """
    _check_platform()
    home = (home or Path.home()).resolve(strict=True)
    notes = [] if notes is None else notes
    with _locked_config(home) as config:
        if os.path.lexists(home / "Applications"):
            _owned_directory(home / "Applications")
        if not _recover_before_use(home, config):
            return "deferred"
        if replace and not update_only:
            notes += _trash_unowned(home, config)
        notes += _clear_orphaned_record(home, config)
        bundle, existing = _locate(home, config)
        if update_only:
            if existing is None:
                return "absent"
            if VERSION.fullmatch(version) and _version_parts(existing.release) > _version_parts(version):
                return "current"
        elif existing is not None:
            bundle = _migrate(bundle, config)
        _applications_directory(bundle.parent, home)
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


def uninstall_desktop(*, home: Path | None = None, notes: list[str] | None = None) -> Path | None:
    """Remove an owned, stopped desktop bundle and its binding from whichever Applications folder holds it.

    A record whose app is already gone is set aside instead (noted in `notes`), so uninstall always finishes.

    Returns:
        Where the removed app was, or None when no owned app was installed.

    Raises:
        DesktopInstallationError: If the running bundle must be quit before removal.
    """
    _check_platform()
    home = (home or Path.home()).resolve(strict=True)
    notes = [] if notes is None else notes
    with _locked_config(home) as config:
        if os.path.lexists(home / "Applications"):
            _owned_directory(home / "Applications")
        if not _recover_before_use(home, config):
            msg = "Quit Heinzelmännchen before uninstalling the desktop app."
            raise DesktopInstallationError(msg)
        notes += _clear_orphaned_record(home, config)
        bundle, existing = _locate(home, config)
        if existing is None:
            return None
        applications = bundle.parent
        _applications_directory(applications, home)
        with _desktop_use(config) as locked:
            if not locked or _in_use(bundle):
                msg = "Quit Heinzelmännchen before uninstalling the desktop app."
                raise DesktopInstallationError(msg)
            if _owned_installation(bundle, config) != existing:
                raise DesktopInstallationError(_CONFLICT)
            with _staging(applications, config) as (staging, files):
                _commit(bundle, config, staging, files, "uninstall")
    return bundle


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
    with _locked_config(home) as config:
        if not _recover_before_use(home, config):
            msg = "Quit Heinzelmännchen before checking the desktop app."
            raise DesktopInstallationError(msg)
        bundle, record = _locate(home, config)
        if record is None:
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
