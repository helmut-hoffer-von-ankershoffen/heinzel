"""Verified recovery of an installation mutation whose owning process provably ended.

`mutation-pending.json` fences every supervised start while an update changes the installed tool. When the process
that wrote it dies (a reboot, a crash, a kill), nothing completes the transaction, and the fence used to stay forever:
the web launcher exited 75 at every start and update, install and restart all refused.

A marker is recovered only while the caller holds the daemon lifecycle lock (so no live mutation scope exists) and
only with all of this evidence:

- the marker is an exact, owner-private record this code or a released version wrote: the legacy `{"token"}` form or
  the current `{"token", "pid", "boot"}` form;
- its owner provably ended: it was written in an earlier boot (a different recorded boot identity, or, for the legacy
  form, a modification time before the current boot), or in this boot by a recorded process id that no longer exists;
- no other installation gate, rollback copy or daemon handover from that attempt remains;
- the installed tool matches its durable startup checkpoint, which updates write only for a verified generation.

The marker is not deleted: it is moved, with its exact bytes, to an archive beside it, after a receipt names the
evidence. No profile, ownership, authentication, notification or session data is read or changed.
"""

import ctypes
import hashlib
import json
import logging
import os
import re
import secrets
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from heinzel import HeinzelError
from heinzel.kernel import Installation, installation_mutation_paths
from heinzel.modules.system.daemon._recovery_files import RecoveryFile, path_absent, recovery_file, rename_exclusive

if TYPE_CHECKING:
    from pydantic import JsonValue

logger = logging.getLogger(__name__)

MUTATION = "mutation-pending.json"
_TOKEN = re.compile(r"[a-f0-9]{64}")
_BOOT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MAX_PID = 2**31 - 1
_PROC_STAT = Path("/proc/stat")
_LINUX_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
_TIMEVAL = struct.Struct("=qi")  # struct timeval on 64-bit Darwin: time_t seconds, suseconds_t microseconds
_LIMIT = 4096


@dataclass(frozen=True, slots=True)
class MutationOwner:
    """Who wrote a pending mutation marker, as far as its exact recorded form says."""

    token: str
    pid: int | None
    boot: str | None


def mutation_record(token: str, pid: int, boot: str | None) -> dict[str, str | int]:
    """The marker a mutation scope writes: its token, and the process and boot that own it."""
    record: dict[str, str | int] = {"token": token, "pid": pid}
    if boot is not None and _BOOT.fullmatch(boot):
        record["boot"] = boot
    return record


def parse_marker(content: bytes) -> MutationOwner | None:
    """Accept only the exact indented forms written by `DaemonService._write_state`, else None."""
    try:
        parsed: object = json.loads(content)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(parsed, dict) or content != (json.dumps(parsed, indent=2) + "\n").encode():
        return None
    value = cast("dict[str, object]", parsed)
    token, pid, boot = value.get("token"), value.get("pid"), value.get("boot")
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        return None
    if set(value) == {"token"}:
        return MutationOwner(token, None, None)
    if (
        set(value) - {"token", "pid", "boot"}
        or not isinstance(pid, int)
        or type(pid) is not int
        or not 0 < pid <= _MAX_PID
        or (boot is not None and not (isinstance(boot, str) and _BOOT.fullmatch(boot) is not None))
    ):
        return None
    return MutationOwner(token, pid, boot if isinstance(boot, str) else None)


def marker_token_matches(content: bytes, token: str) -> bool:
    """Whether `content` is an exact marker form holding `token`."""
    owner = parse_marker(content)
    return owner is not None and secrets.compare_digest(owner.token, token)


def _darwin_sysctl(name: bytes) -> bytes | None:
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    size = ctypes.c_size_t(0)
    if library.sysctlbyname(name, None, ctypes.byref(size), None, ctypes.c_size_t(0)) or not 0 < size.value <= _LIMIT:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if library.sysctlbyname(name, buffer, ctypes.byref(size), None, ctypes.c_size_t(0)):
        return None
    return buffer.raw[: size.value]


def _read(path: Path) -> str:
    with path.open("rb") as source:
        return source.read(_LIMIT * 16).decode("ascii")


def host_boot(platform: str = sys.platform) -> tuple[str | None, int | None]:
    """The current boot's identity and wall-clock start in nanoseconds, read natively; None for what is unknown.

    Darwin: `kern.bootsessionuuid` and `kern.boottime` through sysctlbyname(3). Linux: the kernel's random boot_id and
    the `btime` line of /proc/stat. No process is started and no service manager is asked.
    """
    identity: str | None = None
    started: int | None = None
    try:
        if platform == "darwin":
            raw = _darwin_sysctl(b"kern.bootsessionuuid")
            identity = raw.rstrip(b"\0").decode("ascii").lower() if raw else None
            timeval = _darwin_sysctl(b"kern.boottime")
            if timeval is not None and len(timeval) >= _TIMEVAL.size:
                seconds, micros = _TIMEVAL.unpack_from(timeval)
                started = seconds * 1_000_000_000 + micros * 1_000 if seconds > 0 else None
        elif platform == "linux":
            identity = _read(_LINUX_BOOT_ID).strip().lower()
            found = re.search(r"(?m)^btime (\d+)$", _read(_PROC_STAT))
            started = int(found[1]) * 1_000_000_000 if found else None
    except (OSError, ValueError, UnicodeError, struct.error):
        return None, None
    return (identity if identity and _BOOT.fullmatch(identity) else None), started


def process_exists(pid: int) -> bool:
    """Whether any process has `pid`; a process of another user counts, so only a missing one is proof."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


@dataclass(frozen=True, slots=True)
class Evidence:
    """What the current host says about a marker's owner."""

    current_boot: str | None
    boot_started_ns: int | None
    exists: Callable[[int], bool] = process_exists


def _owner_ended(owner: MutationOwner, marker: RecoveryFile, evidence: Evidence) -> str | None:
    """Why the owner can no longer run, or None when that isn't proven."""
    by_identity = (
        None
        if owner.boot is None or evidence.current_boot is None
        else owner.boot.lower() != evidence.current_boot.lower()
    )
    by_time = None if evidence.boot_started_ns is None else marker.modified_ns < evidence.boot_started_ns
    if by_identity is not None and by_time is not None and by_identity != by_time:
        return None  # contradictory clocks or identities prove nothing
    earlier = by_identity if by_identity is not None else by_time
    if earlier:
        return "previous-boot"
    if earlier is False and owner.pid is not None and owner.pid != os.getpid() and not evidence.exists(owner.pid):
        return "owner-exited"
    return None


def _other_gates_absent(state: Path, installation: Installation, marker: RecoveryFile) -> bool:
    daemon = state / "daemon"
    paths = (
        *installation_mutation_paths(installation.prefix, state),
        daemon / "handover-pending.json",
        state / "auto-update/before-update.partial",
        state / "auto-update/after-update",
    )
    if not all(path_absent(path) for path in paths if path != daemon / MUTATION):
        return False
    request = daemon / "handover-request.json"
    # A request written after the marker belongs to this attempt; only the explicit package recovery handles it.
    return path_absent(request) or recovery_file(request).modified_ns < marker.modified_ns


def _verified_generation(installation: Installation) -> str | None:
    before = installation.startup_fingerprint()
    if not installation.startup_state_matches() or installation.startup_fingerprint() != before:
        return None
    return before


@dataclass(frozen=True, slots=True)
class _Assessment:
    marker: RecoveryFile
    owner: MutationOwner
    evidence: Evidence
    reason: str
    generation: str


def _assess(state: Path, installation: Installation, observe: Callable[[], Evidence]) -> _Assessment | None:
    path = state / "daemon" / MUTATION
    try:
        marker = recovery_file(path)
        owner = parse_marker(marker.content)
        if owner is None:
            return None
        evidence = observe()
        reason = _owner_ended(owner, marker, evidence)
        if reason is None or not _other_gates_absent(state, installation, marker):
            return None
        generation = _verified_generation(installation)
        if generation is None or recovery_file(path) != marker:
            return None
    except (OSError, HeinzelError):
        return None
    return _Assessment(marker, owner, evidence, reason, generation)


def recover_abandoned_mutation(
    state: Path, installation: Installation | None, observe: Callable[[], Evidence], clock: Callable[[], datetime]
) -> Path | None:
    """Archive a pending mutation marker whose owner provably ended; None when it stays (or there is none).

    The caller must hold the daemon lifecycle lock and the tick lock. Host evidence is observed only for a well-formed
    marker. Returns the receipt.

    Raises:
        OSError: If the receipt or the exclusive archive move can't be written durably.
    """
    daemon = state / "daemon"
    path = daemon / MUTATION
    if installation is None or path_absent(path):
        return None
    found = _assess(state, installation, observe)
    if found is None:
        return None
    marker, owner, evidence = found.marker, found.owner, found.evidence
    digest = hashlib.sha256(marker.content + json.dumps(list(marker.identity)).encode()).hexdigest()
    archive = daemon / f"abandoned-mutation-{digest}.json"
    receipt = daemon / f"abandoned-mutation-{digest}.receipt.json"
    if not path_absent(archive):
        return None
    facts: dict[str, JsonValue] = {
        "action": "archive-abandoned-mutation",
        "recovered_at": clock().astimezone(UTC).isoformat(),
        "evidence": found.reason,
        "marker": marker.facts(),
        "marker_form": "legacy-token" if owner.pid is None else "owner",
        "recorded_pid": owner.pid,
        "recorded_boot": owner.boot,
        "current_boot": evidence.current_boot,
        "boot_started_ns": evidence.boot_started_ns,
        "recovering_pid": os.getpid(),
        "prefix": str(installation.prefix),
        "verified_generation": found.generation,
        "archive": str(archive),
    }
    from heinzel.modules.system.daemon._runtime import write_private  # noqa: PLC0415 - shared private writer

    write_private(receipt, json.dumps(facts, indent=2, sort_keys=True) + "\n")
    rename_exclusive(path, archive, expected_source=marker)
    logger.warning("Archived an abandoned installation mutation (%s); receipt: %s", found.reason, receipt.name)
    return receipt
