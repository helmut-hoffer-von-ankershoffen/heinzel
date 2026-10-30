"""Read-only, conservative process-birth ordering for an owned daemon tick.

Linux reports process start in boot-time clock ticks. Its time-namespace identity must match the updater's.
On macOS, an owned child supplies a process unique-counter witness; a later manager-bound tick must have a larger
counter in the same boot. This is process ordering evidence, not executable or complete runtime attestation.
"""

import contextlib
import ctypes
import os
import subprocess
import time
from pathlib import Path

_MAX_STAT_BYTES = 8192
_MAX_DIGITS = 20
_MAX_TICKS_PER_SECOND = 1_000_000
_NANOSECONDS = 1_000_000_000
_START_FIELD_INDEX = 19
_MAC_UNIQUE_INFO = 17
_MAX_UNIQUE_ID = (1 << 63) - 1


class _MacUniqueInfo(ctypes.Structure):
    _fields_ = [
        ("uuid", ctypes.c_ubyte * 16),
        ("unique_id", ctypes.c_uint64),
        ("parent_unique_id", ctypes.c_uint64),
        ("id_version", ctypes.c_int32),
        ("reserved2", ctypes.c_uint32),
        ("reserved3", ctypes.c_uint64),
        ("reserved4", ctypes.c_uint64),
    ]


def _mac_unique(pid: int) -> tuple[int, int] | None:
    """Read only the caller or its held child; invalid or unavailable libproc responses remain unknown."""
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        query = library.proc_pidinfo
        query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        query.restype = ctypes.c_int
        info = _MacUniqueInfo()
        size = ctypes.sizeof(info)
        result: int = query(pid, _MAC_UNIQUE_INFO, 0, ctypes.byref(info), size)
        unique, parent = int(info.unique_id), int(info.parent_unique_id)
        if result != size or not 0 < unique <= _MAX_UNIQUE_ID or parent < 0:
            return None
    except (OSError, ValueError, AttributeError):
        return None
    else:
        return unique, parent


def _mac_witness() -> int | None:
    """Sample an explicitly owned short-lived child as an order fence; never signal an unknown PID."""
    parent = _mac_unique(os.getpid())
    if parent is None:
        return None
    try:
        child = subprocess.Popen(  # fixed OS executable; stdin closure makes the child exit
            ["/bin/cat"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )
    except OSError:
        return None
    witness: int | None = None
    try:
        if child.poll() is None:
            info = _mac_unique(child.pid)
            if info is not None and info[1] == parent[0] and info[0] > parent[0] and child.poll() is None:
                witness = info[0]
    finally:
        if child.stdin is not None:
            with contextlib.suppress(OSError):
                child.stdin.close()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if child.poll() is None:
                with contextlib.suppress(OSError):
                    child.kill()  # The live ChildProcess is the witness we created, never a discovered PID.
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                witness = None
    return witness if child.returncode == 0 else None


def _positive(value: bytes) -> int | None:
    if not value.isdigit() or len(value) > _MAX_DIGITS:
        return None
    result = int(value)
    return result if result > 0 else None


def _time_namespace(path: Path = Path("/proc/self/ns/time")) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


def _clock_ticks() -> int:
    value = os.sysconf("SC_CLK_TCK")
    if not 0 < value <= _MAX_TICKS_PER_SECOND:
        raise ValueError
    return value


def request_fence(platform: str) -> dict[str, int] | None:
    """Observe a Darwin child witness or Linux boot-clock time and namespace; uncertainty remains pending."""
    if platform == "darwin":
        witness = _mac_witness()
        return {"ticks": witness, "hz": 0, "ns_dev": 0, "ns_ino": 0} if witness is not None else None
    if platform != "linux":
        return None
    try:
        before = _time_namespace()
        frequency = _clock_ticks()
        clock: object = getattr(time, "CLOCK_BOOTTIME", None)
        if not isinstance(clock, int):
            return None
        now = time.clock_gettime_ns(clock)
        after = _time_namespace()
        if before != after or now <= 0:
            return None
        return {"ticks": now * frequency // _NANOSECONDS, "hz": frequency, "ns_dev": before[0], "ns_ino": before[1]}
    except (OSError, ValueError, AttributeError):
        return None


def current_birth(platform: str, *, stat_path: Path = Path("/proc/self/stat")) -> dict[str, int] | None:
    """Observe the current process only; a manager PID match is required separately."""
    if platform == "darwin":
        identity = _mac_unique(os.getpid())
        return {"ticks": identity[0], "hz": 0, "ns_dev": 0, "ns_ino": 0} if identity is not None else None
    if platform != "linux":
        return None
    return _linux_birth(stat_path)


def _linux_birth(stat_path: Path) -> dict[str, int] | None:
    """Inspect current-process Linux proc data, not a discovered process identifier."""
    try:
        before = _time_namespace()
        with stat_path.open("rb") as source:
            data = source.read(_MAX_STAT_BYTES + 1)
        after = _time_namespace()
        if len(data) > _MAX_STAT_BYTES or before != after:
            return None
        left = data.find(b" (")
        right = data.rfind(b") ")
        if left < 1 or right <= left:
            return None
        pid = _positive(data[:left])
        fields = data[right + 2 :].split()
        # proc stat field 22; fields begin with process state, field 3.
        if (
            pid != os.getpid()
            or len(fields) <= _START_FIELD_INDEX
            or (start := _positive(fields[_START_FIELD_INDEX])) is None
        ):
            return None
        return {"ticks": start, "hz": _clock_ticks(), "ns_dev": before[0], "ns_ino": before[1]}
    except (OSError, ValueError, AttributeError):
        return None


def after_request(request: dict[str, int], birth: dict[str, int]) -> bool:
    """Require same clock and namespace, and a strictly later process-start tick."""
    return (
        all(
            type(request.get(key)) is int and type(birth.get(key)) is int for key in ("ticks", "hz", "ns_dev", "ns_ino")
        )
        and 0 <= request["hz"] <= _MAX_TICKS_PER_SECOND
        and request["hz"] == birth["hz"]
        and request["ns_dev"] == birth["ns_dev"]
        and request["ns_ino"] == birth["ns_ino"]
        and (
            (request["hz"] == 0 and request["ns_dev"] == 0 and request["ns_ino"] == 0)
            or (request["hz"] > 0 and request["ns_ino"] > 0)
        )
        and birth["ticks"] <= _MAX_UNIQUE_ID
        and birth["ticks"] > request["ticks"] >= 0
    )
