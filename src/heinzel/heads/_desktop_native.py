"""Small fail-closed Darwin identity adapter; never signal by a reusable PID alone.

Flavor 18 is an Apple private-header ABI (136-byte BSD info + 56-byte unique info).
Unsupported symbols, sizes or identities refuse the operation. No fallback to kill().
"""

import ctypes as c
import errno
import os
import platform
import struct
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

_MIN_MACOS = 15
_MAX_IDENTITY_BYTES = 65536
_ZOMBIE = 5


class NativeUnavailableError(RuntimeError):
    """The native identity or lifecycle operation could not be verified."""


class ImageGoneError(NativeUnavailableError):
    """A live process whose executable file no longer has a path: it was deleted or its folder replaced.

    Such a process cannot be running from any bundle that is on disk now, so it never makes an app look open. Any
    other unreadable process still refuses the operation.
    """


@dataclass(frozen=True)
class ProcessIdentity:
    """Kernel generation and credentials from one combined snapshot."""

    pid: int
    uid: int
    real_uid: int
    saved_uid: int
    unique_id: int
    version: int
    birth: tuple[int, int]
    token: bytes


@dataclass(frozen=True)
class ExecutableIdentity:
    """Kernel vnode path bound to one process generation, independent of argv[0]."""

    pid: int
    path: Path
    unique_id: int
    version: int


@dataclass(frozen=True)
class CodeIdentity:
    """Exact code identity, including signing policy; never a name-only match."""

    identifier: str
    team: str
    adhoc: bool
    digest: bytes
    requirement: bytes


def _function(library: c.CDLL, name: str, result: object, *args: object) -> Callable[..., Any]:
    function = getattr(library, name)
    function.restype, function.argtypes = result, args
    return cast("Callable[..., Any]", function)


def _constant(library: c.CDLL, name: str) -> int:
    value = c.c_void_p.in_dll(library, name).value
    if value is None:
        raise NativeUnavailableError
    return value


class Native:
    """Resolve supported native calls lazily, only on a native desktop lifecycle path."""

    def __init__(self) -> None:
        if platform.system() != "Darwin" or int(platform.mac_ver()[0].split(".")[0]) < _MIN_MACOS:
            raise NativeUnavailableError
        try:
            self.proc = c.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            self.cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            self.security = c.CDLL("/System/Library/Frameworks/Security.framework/Security")
            self.core = c.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
            self._pidinfo = _function(
                self.proc, "proc_pidinfo", c.c_int, c.c_int, c.c_int, c.c_uint64, c.c_void_p, c.c_int
            )
            self._pidpath = _function(self.proc, "proc_pidpath_audittoken", c.c_int, c.c_void_p, c.c_void_p, c.c_uint)
            self._signal = _function(self.proc, "proc_signal_with_audittoken", c.c_int, c.c_void_p, c.c_int)
            self._release = _function(self.cf, "CFRelease", None, c.c_void_p)
            self._url = _function(
                self.cf,
                "CFURLCreateFromFileSystemRepresentation",
                c.c_void_p,
                c.c_void_p,
                c.c_char_p,
                c.c_long,
                c.c_bool,
            )
            self._data = _function(self.cf, "CFDataCreate", c.c_void_p, c.c_void_p, c.c_void_p, c.c_long)
            self._length = _function(self.cf, "CFDataGetLength", c.c_long, c.c_void_p)
            self._bytes = _function(self.cf, "CFDataGetBytePtr", c.c_void_p, c.c_void_p)
            self._string = _function(
                self.cf, "CFStringGetCString", c.c_bool, c.c_void_p, c.c_void_p, c.c_long, c.c_uint
            )
            self._number = _function(self.cf, "CFNumberGetValue", c.c_bool, c.c_void_p, c.c_int, c.c_void_p)
            self._dictionary = _function(
                self.cf,
                "CFDictionaryCreate",
                c.c_void_p,
                c.c_void_p,
                c.c_void_p,
                c.c_void_p,
                c.c_long,
                c.c_void_p,
                c.c_void_p,
            )
            self._value = _function(self.cf, "CFDictionaryGetValue", c.c_void_p, c.c_void_p, c.c_void_p)
            self._type = _function(self.cf, "CFGetTypeID", c.c_ulong, c.c_void_p)
        except (OSError, AttributeError) as exc:
            raise NativeUnavailableError from exc

    @contextmanager
    def owned(self, pointer: int) -> Generator[int]:
        if not pointer:
            raise NativeUnavailableError
        try:
            yield pointer
        finally:
            self._release(pointer)

    def key(self, name: str) -> int:
        return _constant(self.security, name)

    def check_type(self, value: int | None, kind: str) -> int:
        expected = _function(self.cf, f"CF{kind}GetTypeID", c.c_ulong)()
        if not value or self._type(value) != expected:
            raise NativeUnavailableError
        return value

    def blob(self, value: int | None) -> bytes:
        value = self.check_type(value, "Data")
        size = self._length(value)
        if not 0 < size <= _MAX_IDENTITY_BYTES:
            raise NativeUnavailableError
        return c.string_at(self._bytes(value), size)

    def text(self, value: int | None) -> str:
        value = self.check_type(value, "String")
        buffer = c.create_string_buffer(4096)
        if not self._string(value, buffer, len(buffer), 0x08000100):
            raise NativeUnavailableError
        return buffer.value.decode("utf-8", errors="strict")

    def process(self, pid: int) -> ProcessIdentity:
        """Read UID, birth and PID version atomically; exact-size refusal protects the ABI.

        Raises:
            NativeUnavailableError: If the identity cannot be verified.
        """
        buffer = c.create_string_buffer(192)
        if self._pidinfo(pid, 18, 0, buffer, len(buffer)) != len(buffer):
            raise NativeUnavailableError
        fields = struct.unpack_from("=12I", buffer)
        unique_id = struct.unpack_from("=Q", buffer, 152)[0]
        version = struct.unpack_from("=i", buffer, 168)[0]
        birth = struct.unpack_from("=QQ", buffer, 120)
        if fields[3] != pid or pid <= 0 or version <= 0 or unique_id <= 0 or birth[0] <= 0:
            raise NativeUnavailableError
        token = struct.pack("=8I", 0, fields[5], fields[6], fields[7], fields[8], pid, 0, version)
        return ProcessIdentity(pid, fields[5], fields[7], fields[9], unique_id, version, birth, token)

    def generation(self, pid: int) -> tuple[int, int]:
        """Flavor17 is available across UIDs; flavor18 credentials remain candidate-only.

        Raises:
            NativeUnavailableError: If the generation cannot be read exactly.
        """
        buffer = c.create_string_buffer(56)
        if pid <= 0 or self._pidinfo(pid, 17, 0, buffer, len(buffer)) != len(buffer):
            raise NativeUnavailableError
        unique_id = struct.unpack_from("=Q", buffer, 16)[0]
        version = struct.unpack_from("=i", buffer, 32)[0]
        if unique_id <= 0 or version <= 0:
            raise NativeUnavailableError
        return unique_id, version

    def executable(self, pid: int) -> ExecutableIdentity:
        """Read the kernel's executable vnode path with audit-generation binding.

        Raises:
            ImageGoneError: If the same live process's executable no longer has a path.
            NativeUnavailableError: If the image is inaccessible, replaced or incomplete.
        """
        unique_id, version = self.generation(pid)
        token = struct.pack("=8I", 0, 0, 0, 0, 0, pid, 0, version)
        buffer = c.create_string_buffer(4096)
        c.set_errno(0)
        size = self._pidpath(token, buffer, len(buffer))
        if size <= 0 and c.get_errno() == errno.ENOENT and self.generation(pid) == (unique_id, version):
            raise ImageGoneError  # the same live process, its executable unlinked (an app updated while it runs)
        if not 0 < size < len(buffer) or len(buffer.value) != size:
            raise NativeUnavailableError
        path = Path(os.fsdecode(buffer.value))
        if not path.is_absolute() or self.generation(pid) != (unique_id, version):
            raise NativeUnavailableError
        return ExecutableIdentity(pid, path, unique_id, version)

    def zombie(self, pid: int) -> bool:
        """Only a complete kernel short-BSD snapshot can establish a zombie; errors are unknown."""
        buffer = c.create_string_buffer(64)
        if self._pidinfo(pid, 13, 1, buffer, len(buffer)) != len(buffer):
            return False
        found_pid, _, _, status = struct.unpack_from("=4I", buffer)
        return bool(found_pid == pid and status == _ZOMBIE)  # SZOMB: no executable can continue using resources.

    def _copy(self, name: str, *arguments: int | None) -> int:
        result = c.c_void_p()
        function = _function(
            self.security, name, c.c_int, *(c.c_void_p for _ in arguments[:-1]), c.c_uint, c.POINTER(c.c_void_p)
        )
        if function(*arguments, c.byref(result)) or not result.value:
            if result.value:
                self._release(result.value)
            raise NativeUnavailableError
        return result.value

    def _identity(self, code: int) -> CodeIdentity:
        with self.owned(self._copy("SecCodeCopySigningInformation", code, 2)) as info:
            self.check_type(info, "Dictionary")

            def value(key: str) -> int | None:
                return cast("int | None", self._value(info, self.key(key)))

            flags = c.c_int64()
            flag_value = self.check_type(value("kSecCodeInfoFlags"), "Number")
            if not self._number(flag_value, 4, c.byref(flags)):
                raise NativeUnavailableError
            team_value = value("kSecCodeInfoTeamIdentifier")
            identifier = self.text(value("kSecCodeInfoIdentifier"))
            team = self.text(team_value) if team_value else ""
            digest = self.blob(value("kSecCodeInfoUnique"))
        with (
            self.owned(self._copy("SecCodeCopyDesignatedRequirement", code, 0)) as requirement,
            self.owned(self._copy("SecRequirementCopyData", requirement, 0)) as data,
        ):
            return CodeIdentity(identifier, team, bool(flags.value & 2), digest, self.blob(data))

    def static(self, path: Path) -> CodeIdentity:
        """Validate the strict static seal before returning its identity.

        Raises:
            NativeUnavailableError: If the identity cannot be verified.
        """
        encoded = os.fsencode(path)
        with (
            self.owned(self._url(None, encoded, len(encoded), path.is_dir())) as url,
            self.owned(self._copy("SecStaticCodeCreateWithPath", url, 0)) as code,
        ):
            check = _function(self.security, "SecStaticCodeCheckValidity", c.c_int, c.c_void_p, c.c_uint, c.c_void_p)
            if check(code, (1 << 4) | (1 << 3), None):
                raise NativeUnavailableError
            return self._identity(code)

    def running(self, process: ProcessIdentity) -> CodeIdentity:
        """Ask Security about the exact running generation, never the new code at its old path.

        Raises:
            NativeUnavailableError: If the identity cannot be verified.
        """
        with self.owned(self._data(None, process.token, len(process.token))) as token:
            keys = (c.c_void_p * 2)(self.key("kSecGuestAttributeAudit"), self.key("kSecGuestAttributeDynamicCode"))
            values = (c.c_void_p * 2)(token, _constant(self.cf, "kCFBooleanTrue"))
            with (
                self.owned(self._dictionary(None, keys, values, 2, None, None)) as attributes,
                self.owned(self._copy("SecCodeCopyGuestWithAttributes", None, attributes, 0)) as code,
            ):
                check = _function(self.security, "SecCodeCheckValidity", c.c_int, c.c_void_p, c.c_uint, c.c_void_p)
                if check(code, 0, None):
                    raise NativeUnavailableError
                return self._identity(code)

    def request_exit(self, process: ProcessIdentity) -> bool:
        """Send only SIGTERM to the bound generation; never escalate or use a PID fallback."""
        return bool(self._signal(process.token, 15) == 0)

    def register(self, bundle: Path) -> bool:
        """Refresh registration of this exact installed path, without launching anything."""
        encoded = os.fsencode(bundle)
        with self.owned(self._url(None, encoded, len(encoded), 1)) as url:
            register = _function(self.core, "LSRegisterURL", c.c_int, c.c_void_p, c.c_bool)
            return bool(register(url, 1) == 0)
