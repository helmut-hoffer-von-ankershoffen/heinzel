"""Admission for a separately provisioned, OS-protected native MCP observer package.

The trusted SDK supplies these pins from approved installer inventory. Provision the observer, native client,
policy and every non-system loader input under root-owned local ancestry. Writable Darwin volumes must return
explicit non-granting ACLs for the files and every traversed directory. No ACL record or unsupported retrieval
refuses admission. Read-only mounts still require root ownership and no writable mode bits.

The native observer must enforce the declared closure at execution, including loader inputs and descendants.
Installers must hold the exact protected lock inode exclusively through replacement and inventory publication.
The SDK holds that same inode shared through the probe and rechecks the package before accepting evidence.
Root remains a trusted administrator. Nothing here provisions, modifies or runs a program as root.
"""

import ctypes
import errno
import fcntl
import hashlib
import hmac
import os
import platform
import re
import stat
import struct
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from heinzel.heads.mcp._client_config import McpClient
from heinzel.kernel import HeinzelError
from heinzel.kernel._approval import arguments_hash

_BAD_INSTALL = "The protected native MCP observer installation cannot be verified."
_HASH = re.compile(r"[0-9a-f]{64}")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_MAX_FILES = 64
_MAX_FILE_BYTES = 256 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_MACH_HEADERS = frozenset(bytes.fromhex(value) for value in ("cafebabe", "cafebabf", "cffaedfe", "feedfacf"))
_DARWIN_LIBSYSTEM = "/usr/lib/libSystem.B.dylib"

# Darwin's descriptor-based extended security attribute. An omitted attribute is not ACL evidence.
# https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/attr.h
# https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/kauth.h
_ATTR_EXT_SECURITY = 0x00400000
_ATTR_RETURNED = 0x80000000
_ATTR_REQUEST = _ATTR_RETURNED | _ATTR_EXT_SECURITY
_ATTR_HEADER = struct.Struct("=6I")
_ATTR_REF = struct.Struct("=iI")
_ACL_MAGIC = 0x012CC16D
_ACL_HEADER = struct.Struct("=I16s16sII")
_ACL_ENTRY = struct.Struct("=16sII")
_ACL_DENY = 2
_ACL_KIND = 0xF
_ACL_MAX = 128


@dataclass(frozen=True, slots=True)
class ProtectedObserverFile:
    """An exact immutable file from approved package inventory."""

    path: Path = field(repr=False)
    sha256: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProtectedObserverInstall:
    """Protected root-owned helper, vendor image, confinement policy and non-system dependency closure."""

    client: McpClient
    observer: ProtectedObserverFile = field(repr=False)
    vendor: ProtectedObserverFile = field(repr=False)
    policy: ProtectedObserverFile = field(repr=False)
    dependencies: tuple[ProtectedObserverFile, ...] = field(repr=False)
    update_lock: Path = field(repr=False)
    update_lock_identity: tuple[int, int] = field(repr=False)

    @property
    def digest(self) -> str:
        """Bind every protocol exchange to the exact provisioned package and client."""
        return arguments_hash(
            {
                "client": self.client.value,
                "observer": [str(self.observer.path), self.observer.sha256],
                "vendor": [str(self.vendor.path), self.vendor.sha256],
                "policy": [str(self.policy.path), self.policy.sha256],
                "dependencies": [[str(item.path), item.sha256] for item in self.dependencies],
                "update_lock": str(self.update_lock),
                "update_lock_identity": list(self.update_lock_identity),
            }
        )

    @contextmanager
    def admitted(self) -> Generator[None]:
        """Hold the root-owned update lock while every named image and dependency is protected.

        Raises:
            HeinzelError: If protected ancestry, exact pins, unprivileged execution or update ownership fail.
        """
        if os.getuid() == 0 or os.getuid() != os.geteuid() or os.getgid() != os.getegid():
            raise HeinzelError(_BAD_INSTALL)
        files = (self.observer, self.vendor, self.policy, *self.dependencies)
        if len(files) > _MAX_FILES or len({self.update_lock, *(item.path for item in files)}) != len(files) + 1:
            raise HeinzelError(_BAD_INSTALL)
        try:
            with ExitStack() as stack:
                lock = _open_protected(self.update_lock, stack)
                lock_identity = _identity(lock)
                if lock_identity[:2] != self.update_lock_identity:
                    raise HeinzelError(_BAD_INSTALL)
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                captured = self._capture(files, stack)
                yield
                with ExitStack() as after:
                    _require_same_path(self.update_lock, lock_identity, after)
                    for path, identity in captured:
                        _require_same_path(path, identity, after)
        except (OSError, ValueError, AttributeError):
            raise HeinzelError(_BAD_INSTALL) from None

    def _capture(
        self, files: tuple[ProtectedObserverFile, ...], stack: ExitStack
    ) -> list[tuple[Path, tuple[int, ...]]]:
        total = 0
        captured = []
        for item in files:
            descriptor = _open_protected(item.path, stack)
            identity = _identity(descriptor)
            size = os.fstat(descriptor).st_size
            total += size
            if size > _MAX_FILE_BYTES or total > _MAX_TOTAL_BYTES:
                raise HeinzelError(_BAD_INSTALL)
            if item in (self.observer, self.vendor):
                _require_native_image(descriptor)
            if not _HASH.fullmatch(item.sha256) or not hmac.compare_digest(_sha256(descriptor, size), item.sha256):
                raise HeinzelError(_BAD_INSTALL)
            if _identity(descriptor) != identity:
                raise HeinzelError(_BAD_INSTALL)
            captured.append((item.path, identity))
        return captured


def _open_protected(path: Path, stack: ExitStack) -> int:
    if path.anchor != "/" or not path.name or ".." in path.parts:
        raise HeinzelError(_BAD_INSTALL)
    with ExitStack() as ancestry:
        directory = os.open("/", _DIRECTORY)
        ancestry.callback(os.close, directory)
        _require_protected(directory, directory=True)
        for part in path.parts[1:-1]:
            directory = os.open(part, _DIRECTORY, dir_fd=directory)
            ancestry.callback(os.close, directory)
            _require_protected(directory, directory=True)
        descriptor = os.open(path.name, _FILE, dir_fd=directory)
        stack.callback(os.close, descriptor)
        _require_protected(descriptor, directory=False)
        return descriptor


def _require_protected(descriptor: int, *, directory: bool) -> None:
    info = os.fstat(descriptor)
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    special = 0 if directory else stat.S_ISUID | stat.S_ISGID
    if not valid_type or info.st_uid != 0 or info.st_mode & (0o022 | special):
        raise HeinzelError(_BAD_INSTALL)
    if not _protected_acl(descriptor, directory=directory):
        raise HeinzelError(_BAD_INSTALL)


def _protected_acl(descriptor: int, *, directory: bool) -> bool:
    system = platform.system()
    if system == "Darwin":
        # ACL permissions cannot make a read-only mounted filesystem writable.
        return bool(os.fstatvfs(descriptor).f_flag & os.ST_RDONLY) or _darwin_acl(descriptor)
    if system == "Linux":
        attributes = ["system.posix_acl_access"]
        attributes += ["system.posix_acl_default"] if directory else ["security.capability"]
        return all(_linux_attribute_absent(descriptor, attribute) for attribute in attributes)
    return False


def _linux_attribute_absent(descriptor: int, name: str) -> bool:
    reader = getattr(os, "getxattr", None)
    if reader is None:
        return False
    try:
        reader(descriptor, name)
    except OSError as exc:
        return exc.errno == errno.ENODATA
    return False


def _darwin_acl(descriptor: int) -> bool:
    libc = ctypes.CDLL(_DARWIN_LIBSYSTEM, use_errno=True)
    getattrlist = libc.fgetattrlist
    getattrlist.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong]
    request = struct.pack("=HH5I", 5, 0, _ATTR_REQUEST, 0, 0, 0, 0)
    result = ctypes.create_string_buffer(4096)
    if getattrlist(descriptor, request, result, len(result), 0) != 0:
        return False
    return _deny_only_acl(result.raw)


def _deny_only_acl(data: bytes) -> bool:
    reference = _ATTR_HEADER.size
    if len(data) < reference + _ATTR_REF.size:
        return False
    length, common, volume, directory, file, fork = _ATTR_HEADER.unpack_from(data)
    if common != _ATTR_REQUEST or any((volume, directory, file, fork)):
        return False
    offset, size = _ATTR_REF.unpack_from(data, reference)
    start = reference + offset
    if not reference + _ATTR_REF.size <= start <= length <= len(data) or start % 4 or start + size > length:
        return False
    if size < _ACL_HEADER.size:
        return False
    magic, _owner, _group, count, _flags = _ACL_HEADER.unpack_from(data, start)
    if magic != _ACL_MAGIC or count > _ACL_MAX or size != _ACL_HEADER.size + count * _ACL_ENTRY.size:
        return False
    entries = data[start + _ACL_HEADER.size : start + size]
    return all(flags & _ACL_KIND == _ACL_DENY for _, flags, _ in _ACL_ENTRY.iter_unpack(entries))


def _require_native_image(descriptor: int) -> None:
    info = os.fstat(descriptor)
    header = os.pread(descriptor, 4, 0)
    headers = _MACH_HEADERS if platform.system() == "Darwin" else {b"\x7fELF"}
    if not info.st_mode & stat.S_IXOTH or header not in headers:
        raise HeinzelError(_BAD_INSTALL)


def _sha256(descriptor: int, length: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < length:
        chunk = os.pread(descriptor, min(65536, length - offset), offset)
        if not chunk:
            raise HeinzelError(_BAD_INSTALL)
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


def _identity(descriptor: int) -> tuple[int, ...]:
    info = os.fstat(descriptor)
    return info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_size, info.st_ctime_ns, info.st_mtime_ns


def _require_same_path(path: Path, identity: tuple[int, ...], stack: ExitStack) -> None:
    if _identity(_open_protected(path, stack)) != identity:
        raise HeinzelError(_BAD_INSTALL)
