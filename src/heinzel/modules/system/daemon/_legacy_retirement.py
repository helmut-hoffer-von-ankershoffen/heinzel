"""Explicit legacy retirement; never called by ordinary lifecycle operations (ADR 0132)."""


# ruff: noqa: SLF001 -- sibling implementation of the same daemon authority boundary

import ctypes
import hashlib
import json
import os
import pwd
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Never
from uuid import UUID

from pydantic import TypeAdapter

from heinzel import Config, HeinzelError, Settings
from heinzel.kernel import (
    DaemonHandoverObservation,
    DaemonRecoveryPlan,
    DaemonRecoveryResult,
    DaemonRecoverySelection,
    private_directory,
    private_lock,
    private_read,
    private_write,
)
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._ownership import profile_environment
from heinzel.modules.system.daemon._runtime import fsync_dir
from heinzel.modules.system.daemon._units import INTERVALS, LABEL, PATH, TICK, launchd_plist, plist_path

if TYPE_CHECKING:
    from heinzel.modules.system.daemon._service import DaemonService

LIMIT = 256 * 1024
MANAGER_LIMIT = 1_000_000
MAX_PLAN_BYTES = 12000
MAX_JOURNAL_BYTES = 16 * 1024
MAX_HISTORY_OBSERVATION = 128 * 1024
MAX_HISTORY_CONTRIBUTIONS = 512
LEGACY_FILE_COUNT = 2
DIGEST = re.compile(r"[a-f0-9]{64}")
PENDING = "standard-install-pending.json"


def _refuse() -> Never:
    msg = "Legacy retirement evidence is unknown or changed; no further recovery action was authorized."
    raise HeinzelError(msg)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _refuse()
        result[key] = value
    return result


@dataclass(frozen=True)
class _File:
    path: Path
    content: bytes
    identity: tuple[int, ...]

    def facts(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": hashlib.sha256(self.content).hexdigest(),
            "identity": list(self.identity),
        }


def _file(path: Path) -> _File:
    # Reuse the daemon's physical-parent, no-follow file boundary, not Path.read_bytes().
    from heinzel.modules.system.daemon._service import _open_verified_handover_file  # noqa: PLC0415 - shared sibling boundary

    with _open_verified_handover_file(path) as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > LIMIT
        ):
            _refuse()
        content = handle.read(LIMIT + 1)
        after = os.fstat(handle.fileno())

        def identity(i: os.stat_result) -> tuple[int, ...]:
            return (i.st_dev, i.st_ino, i.st_mode, i.st_uid, i.st_size, i.st_mtime_ns, i.st_ctime_ns)

        if len(content) > LIMIT or identity(info) != identity(after):
            _refuse()
        return _File(path, content, identity(info))


def _absent(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def _rename_exclusive(source: Path, destination: Path) -> None:
    """Atomically restore without replacing any destination.

    Raises:
        OSError: The exclusive native operation is unavailable or refuses the move.
    """
    # Darwin RENAME_EXCL=4 (xnu bsd/sys/stdio.h); Linux RENAME_NOREPLACE=1.
    # Linux supports protocol-double tests only; real legacy recovery remains macOS-only.
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        rename = library.renameatx_np
        flags = 4
    elif sys.platform == "linux":
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renameat2
        flags = 1
    else:
        _refuse()
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    source_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        destination_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if rename(source_fd, os.fsencode(source.name), destination_fd, os.fsencode(destination.name), flags):
                raise OSError(ctypes.get_errno(), "Exclusive legacy restoration refused")
            os.fsync(destination_fd)
            os.fsync(source_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


def _account() -> dict[str, Any]:
    account = pwd.getpwuid(os.getuid())
    if account.pw_uid != os.getuid():
        _refuse()
    return {"uid": account.pw_uid, "gid": account.pw_gid, "home": account.pw_dir}


def _safe_home(home: str) -> None:
    path = Path(home)
    if not path.is_absolute() or ".." in path.parts or path.resolve(strict=True) != path:
        _refuse()
    for parent in (path, *path.parents):
        info = parent.lstat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or (info.st_mode & 0o022 and not sticky_root)
        ):
            _refuse()
    if path.stat().st_uid != os.getuid():
        _refuse()


def _environment(output: str, home: str) -> dict[str, str]:
    """Require complete, unique bounded blocks; hidden/conflicting routing is never merged away."""
    names = ("default environment", "inherited environment", "environment")
    observed = re.findall(r"(?m)^\s*([^\n={}]*environment) = \{", output)
    if sorted(name.strip() for name in observed) != sorted(names):
        _refuse()
    environment: dict[str, str] = {}
    for name in names:
        blocks = re.findall(rf"(?ms)^\s*{name} = \{{\n(.*?)^\s*\}}", output)
        if len(blocks) != 1:
            _refuse()
        values: dict[str, str] = {}
        for line in blocks[0].splitlines():
            key, separator, value = line.strip().partition(" => ")
            if (
                not separator
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                or key in values
                or key.startswith(("HZ_", "XDG_", "PYTHON"))
                or (key == "HOME" and value != home)
            ):
                _refuse()
            values[key] = value
        environment.update(values)
    return environment


def _loaded(output: str, unit: Path, executable: Path, home: str) -> dict[str, Any]:
    if len(output.encode()) > MANAGER_LIMIT:
        _refuse()
    paths = re.findall(r"(?m)^\s*path = (.+)$", output)
    programs = re.findall(r"(?m)^\s*program = (.+)$", output)
    arguments = re.findall(r"(?ms)^\s*arguments = \{\n(.*?)^\s*\}", output)
    directories = re.findall(r"(?m)^\s*working directory = (.+)$", output)
    states = re.findall(r"(?m)^\s*state = (.+)$", output)
    pids = re.findall(r"(?m)^\s*pid = (.*)$", output)
    argv = [line.strip() for line in arguments[0].splitlines()] if len(arguments) == 1 else []
    environment = _environment(output, home)
    if (
        paths != [str(unit)]
        or programs != [str(executable)]
        or argv != [str(executable), *TICK]
        or directories not in ([], [home])
        or states != ["not running"]
        or pids not in ([], ["0"])
        or environment.get("PATH") != f"{executable.parent}:{PATH}"
    ):
        _refuse()
    if "HOME" in environment:
        return {"home": "observed", "selection": "observed-current-default", "historical_profile": "unknown"}
    account = _account()
    if account["home"] != home:
        _refuse()
    _safe_home(home)
    return {
        "home": "unobserved",
        "selection": "native-account-default",
        "native_account": account,
        "historical_profile": "unknown",
        "disclosure": "Historical runtime profile is unknown; consent retires only this exact registration.",
    }


class LegacyRetirement:
    """One explicit, private, digest-bound retirement transaction for a verified persistent SDK."""

    def __init__(self, service: "DaemonService", selection: DaemonRecoverySelection) -> None:
        self.service = service
        self.selection = selection
        self.root = service.state_dir / "legacy-retirement"

    def _scope(self) -> None:
        service = self.service
        if service._platform != "darwin" or service._uid() != os.getuid():
            _refuse()  # Other managers need their own proven effective-environment protocol.
        if any(
            key in os.environ
            for key in (
                "HZ_CONFIG",
                "HZ_CONFIG_HOME",
                "HZ_DATA_DIR",
                "HZ_CACHE_DIR",
                "HZ_STATE_DIR",
                "HZ_LOG_DIR",
                "HZ_SYSTEM_DAEMON__UNIT_DIR",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "XDG_CACHE_HOME",
                "XDG_STATE_HOME",
            )
        ):
            _refuse()
        expected = profile_environment(Settings(), DaemonSettings(), Config.empty())
        if service._profile != expected:
            _refuse()

    def _target(self) -> dict[str, Any]:
        self._scope()
        standard = _file(Path(self.service._profile["HZ_DATA_DIR"]) / "standard-app.json")
        if standard.path != self.selection.standard_receipt or standard.content != self.selection.content:
            _refuse()
        installation = self.service._quiescence_installation
        if installation is None or not installation.startup_state_matches():
            _refuse()
        import tomllib  # noqa: PLC0415 - receipt parsing only on explicit recovery

        receipt = tomllib.loads(installation.receipt().decode())
        matches = [entry for entry in receipt["tool"]["entrypoints"] if entry["name"] == "hz"]
        if len(matches) != 1:
            _refuse()
        executable = Path(matches[0]["install-path"])
        installation.verify_tool_destination(installation.prefix.parent, executable.parent)
        return {
            "standard_app": standard.facts(),
            "native_account": _account(),
            "prefix": str(installation.prefix),
            "executable": str(executable),
            "generation": installation.startup_fingerprint(),
            "version": installation.installed_core_version(),
            "uid": os.getuid(),
            "profile": dict(self.service._profile),
        }

    def _manager(self, executable: Path, *, require_absent: bool = False) -> dict[str, Any]:
        service = self.service
        result = service.owned_manager_command(["/bin/launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
        if result.returncode:
            from heinzel.modules.system.daemon._service import _launchctl_records  # noqa: PLC0415 - one shared absence parser

            listing = service.owned_manager_command(["/bin/launchctl", "list"])
            records = _launchctl_records(listing.stdout) if listing.returncode == 0 else None
            if records is None or LABEL in records:
                _refuse()
            return {"registered": False, "observation": _digest(records)}
        if require_absent:
            _refuse()
        routing = _loaded(result.stdout, plist_path(service.unit_dir), executable, service._profile["HOME"])
        return {
            "registered": True,
            "observation": hashlib.sha256(result.stdout.encode()).hexdigest(),
            "routing": routing,
        }

    def _files(self, executable: Path) -> tuple[_File, ...]:
        service = self.service
        unit = _file(plist_path(service.unit_dir))
        marker = _file(service.state_dir / "install.json")
        record = json.loads(marker.content, object_pairs_hook=_pairs)
        fields = {"interval_minutes", "executable", "platform", "installed_at", "version"}
        if not isinstance(record, dict) or set(record) not in (fields, fields | {"handover_id"}):
            _refuse()
        minutes = record["interval_minutes"]
        if (
            record["platform"] != "darwin"
            or record["executable"] != str(executable)
            or record["version"] not in {"0.9.14", "0.9.15"}
            or type(minutes) is not int
            or minutes not in INTERVALS
            or unit.content != launchd_plist(executable, minutes, self.service.log_path.parent).encode()
            or datetime.fromisoformat(record["installed_at"]).tzinfo is None
        ):
            _refuse()
        if "handover_id" in record:
            request, _ = self._completed_history(unit, marker, record)
            return unit, marker, request
        if not _absent(service.state_dir / "handover-request.json"):
            _refuse()
        return unit, marker

    def _completed_history(self, unit: _File, marker: _File, record: dict[str, Any]) -> tuple[_File, tuple[_File, ...]]:
        try:
            return self._verified_history(unit, marker, record)
        except (OSError, ValueError, TypeError, KeyError):
            _refuse()

    def _verified_history(self, unit: _File, marker: _File, record: dict[str, Any]) -> tuple[_File, tuple[_File, ...]]:
        service = self.service
        request = _file(service.state_dir / "handover-request.json")
        observation = _file(service.state_dir / "handover-observation.json")
        heartbeat = _file(service.state_dir / "heartbeat.json")
        requested = json.loads(request.content, object_pairs_hook=_pairs)
        observed_fields = json.loads(observation.content, object_pairs_hook=_pairs)
        if (
            len(observation.content) > MAX_HISTORY_OBSERVATION
            or not isinstance(observed_fields, dict)
            or set(observed_fields)
            != {
                "request_id",
                "version",
                "fingerprint",
                "contributions",
                "tick_at",
                "old_unit_signature",
                "new_unit_signature",
            }
        ):
            _refuse()
        observed = TypeAdapter(DaemonHandoverObservation).validate_json(observation.content, strict=True)
        beat = json.loads(heartbeat.content, object_pairs_hook=_pairs)
        request_fields = {
            "request_id",
            "boot",
            "unit_signature",
            "birth_ticks",
            "birth_hz",
            "birth_ns_dev",
            "birth_ns_ino",
        }
        if (
            not isinstance(requested, dict)
            or set(requested) != request_fields
            or not isinstance(beat, dict)
            or set(beat) != {"last_tick", "completed_at", "source", "outcome", "interval_minutes", "version"}
        ):
            _refuse()
        request_id = record["handover_id"]
        current = hashlib.sha256()
        for file in (unit, marker):
            current.update(str(file.path).encode() + b"\0" + file.content + b"\0")
        installation = service._quiescence_installation
        if (
            not isinstance(request_id, str)
            or not DIGEST.fullmatch(request_id)
            or requested["request_id"] != request_id
            or observed.request_id != request_id
            or not isinstance(requested["unit_signature"], str)
            or not DIGEST.fullmatch(requested["unit_signature"])
            or observed.old_unit_signature != requested["unit_signature"]
            or observed.new_unit_signature != current.hexdigest()
            or len(observed.contributions) > MAX_HISTORY_CONTRIBUTIONS
            or record["version"] != "0.9.15"
            or observed.version != record["version"]
            or installation is None
            or installation.installed_core_version() != observed.version
            or installation.startup_fingerprint() != observed.fingerprint
            or not isinstance(requested["boot"], str)
            or not requested["boot"]
            or str(UUID(requested["boot"])) != requested["boot"].lower()
            or any(
                type(requested[key]) is not int or requested[key] < 0
                for key in request_fields
                if key.startswith("birth_")
            )
            or not 0 < requested["birth_ticks"] < 2**63
            or any(requested[key] != 0 for key in ("birth_hz", "birth_ns_dev", "birth_ns_ino"))
            or beat.get("source") != "scheduled"
            or beat.get("version") != observed.version
            or beat.get("interval_minutes") != record["interval_minutes"]
            or beat.get("outcome") not in {"completed", "failed"}
        ):
            _refuse()
        tick = datetime.fromisoformat(observed.tick_at)
        last = datetime.fromisoformat(beat["last_tick"])
        completed = datetime.fromisoformat(beat["completed_at"])
        if (
            any(value.tzinfo is None for value in (tick, last, completed))
            or not tick <= last <= completed <= service._clock()
        ):
            _refuse()
        return request, (observation, heartbeat)

    def _evidence(self, files: tuple[_File, ...]) -> tuple[_File, ...]:
        if len(files) == LEGACY_FILE_COUNT:
            return ()
        record = json.loads(files[1].content, object_pairs_hook=_pairs)
        _, evidence = self._completed_history(files[0], files[1], record)
        return evidence

    def _idle(self, *, token: str | None = None, check_tick: bool = True, allow_history: bool = False) -> None:
        service = self.service
        for name in ("handover-pending.json", "mutation-pending.json", PENDING):
            if name == PENDING and token is not None:
                if not service._mutation_token_matches(token, PENDING):
                    _refuse()
            elif not _absent(service.state_dir / name):
                _refuse()
        if not allow_history and not _absent(service.state_dir / "handover-request.json"):
            _refuse()
        # The tick lock is also acquired during execution. A read-only plan never creates a lock file.
        from heinzel.modules.system.daemon._runtime import held  # noqa: PLC0415 - read-only lock inspection

        if check_tick and not _absent(service.state_dir / "tick.lock"):
            _file(service.state_dir / "tick.lock")
        if check_tick and held(service.state_dir / "tick.lock"):
            _refuse()

    def plan(self) -> DaemonRecoveryPlan:
        """Inspect only; no directories, locks, backup, journal or manager mutation is created."""
        return self._plan()

    def _plan(self, *, check_tick: bool = True) -> DaemonRecoveryPlan:
        self._idle(check_tick=check_tick, allow_history=True)
        target = self._target()
        if target["version"] not in {"0.9.14", "0.9.15"}:
            _refuse()
        files = self._files(Path(target["executable"]))
        evidence = self._evidence(files)
        manager = self._manager(Path(target["executable"]))
        if any(file.identity[0] != self.service.state_dir.stat().st_dev for file in files):
            _refuse()
        if (
            self._target() != target
            or self._files(Path(target["executable"])) != files
            or self._evidence(files) != evidence
        ):
            _refuse()
        facts: dict[str, Any] = {
            "action": "retire-legacy-daemon",
            "target": target,
            "files": [file.facts() for file in files],
            "evidence": [file.facts() for file in evidence],
            "manager": manager,
            "historical_profile": "unknown",
            "backup_root": str(self.root),
            "preserve": ["pause", "configuration", "data", "schedules", "installation-receipts"],
            "effects": ["unload verified legacy registration if present", "preserve exact unit and marker"]
            + (
                ["archive verified completed handover request; preserve observation and heartbeat copies"]
                if evidence
                else []
            ),
        }
        plan_size = len(_json(facts).encode())
        backup_budget = sum(
            len(str(self.root / ("a" * 64) / f"original-{index}").encode()) + 512 for index in range(len(files))
        )
        if plan_size > MAX_PLAN_BYTES or plan_size + backup_budget + 1024 > MAX_JOURNAL_BYTES:
            _refuse()
        return DaemonRecoveryPlan(digest=_digest(facts), facts=facts)

    def _directory(self, digest: str) -> Path:
        if not DIGEST.fullmatch(digest):
            _refuse()
        return self.root / digest

    def _read(self, digest: str) -> dict[str, Any]:
        value = json.loads(private_read(self._directory(digest) / "transaction.json"), object_pairs_hook=_pairs)
        if not isinstance(value, dict) or value.get("digest") != digest or value.get("uid") != os.getuid():
            _refuse()
        plan = value.get("plan")
        if (
            not isinstance(plan, dict)
            or not isinstance(plan.get("files"), list)
            or len(plan["files"]) not in {LEGACY_FILE_COUNT, LEGACY_FILE_COUNT + 1}
            or any(not isinstance(item, dict) for item in plan["files"])
            or not isinstance(plan.get("target"), dict)
            or plan["target"].get("profile") != dict(self.service._profile)
            or plan["target"].get("uid") != os.getuid()
            or plan.get("action") != "retire-legacy-daemon"
        ):
            _refuse()
        if (
            value.get("format") != 1
            or value.get("token") != digest
            or _digest(plan) != digest
            or [file.get("path") for file in plan.get("files", [])]
            != (
                [str(plist_path(self.service.unit_dir)), str(self.service.state_dir / "install.json")]
                + (
                    [str(self.service.state_dir / "handover-request.json")]
                    if len(plan["files"]) > LEGACY_FILE_COUNT
                    else []
                )
            )
            or value.get("state")
            not in {
                "intent",
                "unload-intent",
                "unloaded",
                "move-0-intent",
                "move-1-intent",
                "move-2-intent",
                "moved-0",
                "moved-1",
                "moved-2",
                "retired",
                "restore-intent",
                "restore-load-intent",
                "restored",
            }
        ):
            _refuse()
        backups = value.get("backups")
        if (
            not isinstance(backups, list)
            or len(backups) > len(plan["files"])
            or any(not isinstance(item, dict) for item in backups)
        ):
            _refuse()
        for index, item in enumerate(backups):
            if item.get("path") != str(self._directory(digest) / f"original-{index}") or item.get("sha256") != plan[
                "files"
            ][index].get("sha256"):
                _refuse()
        if value["state"] not in {"intent", "restore-intent", "restore-load-intent", "restored"} and len(
            backups
        ) != len(plan["files"]):
            _refuse()
        if value.get("profile") != dict(self.service._profile):
            _refuse()
        return value

    def _write(self, journal: dict[str, Any]) -> None:
        path = self._directory(journal["digest"]) / "transaction.json"
        content = _json(journal) + "\n"
        if len(content.encode()) > MAX_JOURNAL_BYTES:
            _refuse()
        private_write(path, content)
        fsync_dir(path.parent)

    def _terminal(self, digest: str, status: Literal["retired", "restored"]) -> DaemonRecoveryResult:
        return DaemonRecoveryResult(
            digest=digest,
            status=status,
            message="Historical operation completed; current service state is not asserted.",
        )

    def _release_gate(self, journal: dict[str, Any]) -> None:
        service = self.service
        if not service._mutation_token_matches(journal["token"], PENDING):
            _refuse()
        (service.state_dir / PENDING).unlink()
        fsync_dir(service.state_dir)

    def _replay_terminal(self, journal: dict[str, Any]) -> DaemonRecoveryResult:
        if self.service._mutation_token_matches(journal["token"], PENDING):
            from heinzel.modules.system.daemon._service import _handover_absolute_path  # noqa: PLC0415 - shared lock boundary

            with (
                self.service._lifecycle_writer(),
                private_lock(_handover_absolute_path(self.service.state_dir / "tick.lock")),
            ):
                if self._read(journal["digest"]) != journal:
                    _refuse()
                if self.service._mutation_token_matches(journal["token"], PENDING):
                    self._finish_terminal_gate(journal)
        return self._terminal(journal["digest"], "retired")

    def _finish_terminal_gate(self, journal: dict[str, Any]) -> None:
        """Finish only this completed transaction's gate, without touching a later installation."""
        gate = self.service.state_dir / PENDING
        if _absent(gate):
            return
        target = journal["plan"]["target"]
        if self._target() != target or any(not _absent(Path(item["path"])) for item in journal["plan"]["files"]):
            _refuse()
        self._manager(Path(target["executable"]), require_absent=True)
        self._release_gate(journal)

    def retire(self, digest: str) -> DaemonRecoveryResult:
        """Perform only a freshly confirmed plan, retaining an explicit gate on any uncertainty."""
        directory = self._directory(digest)
        if not _absent(directory):
            journal = self._read(digest)
            if journal["state"] == "retired":
                return self._replay_terminal(journal)
            _refuse()  # No mutation replay; an interrupted transaction needs its own restore confirmation.
        service = self.service
        from heinzel.modules.system.daemon._service import _handover_absolute_path  # noqa: PLC0415 - shared physical lock path

        with service._lifecycle_writer(), private_lock(_handover_absolute_path(service.state_dir / "tick.lock")):
            plan = self._plan(check_tick=False)
            facts: dict[str, Any] = plan.model_dump()["facts"]
            if plan.digest != digest:
                _refuse()
            files = self._files(Path(facts["target"]["executable"]))
            private_directory(directory)
            fsync_dir(directory.parent)
            token = digest
            journal = {
                "format": 1,
                "digest": digest,
                "uid": os.getuid(),
                "profile": dict(service._profile),
                "token": token,
                "plan": facts,
                "state": "intent",
                "moved": [],
                "backups": [],
            }
            self._write(journal)
            private_write(service.state_dir / PENDING, json.dumps({"token": token}, indent=2) + "\n")
            fsync_dir(service.state_dir)
            self._preserve_evidence(journal)
            self._preserve_originals(journal, files)
            if (
                self._target() != facts["target"]
                or self._files(Path(facts["target"]["executable"])) != files
                or self._manager(Path(facts["target"]["executable"])) != facts["manager"]
            ):
                _refuse()
            # An acknowledgement failure is uncertain; no retry or inferred host acknowledgement follows.
            if facts["manager"]["registered"]:
                journal["state"] = "unload-intent"
                self._write(journal)
                self._admit_retire(journal, files)
                if service.owned_manager_command(
                    ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]
                ).returncode:
                    _refuse()
            self._manager(Path(facts["target"]["executable"]), require_absent=True)
            journal["state"] = "unloaded"
            self._write(journal)
            self._retire_files(journal, files)
            self._manager(Path(facts["target"]["executable"]), require_absent=True)
            if self._target() != facts["target"] or any(not _absent(file.path) for file in files):
                _refuse()
            journal["state"] = "retired"
            self._write(journal)
            self._release_gate(journal)
            return self._terminal(digest, "retired")

    def _admit_retire(self, journal: dict[str, Any], files: tuple[_File, ...]) -> None:
        facts = journal["plan"]
        executable = Path(facts["target"]["executable"])
        if (
            self._target() != facts["target"]
            or self._files(executable) != files
            or [item.facts() for item in self._evidence(files)] != facts["evidence"]
            or self._manager(executable) != facts["manager"]
        ):
            _refuse()
        self._verify_remaining(journal, files, 0)
        self._idle(token=journal["token"], check_tick=False, allow_history=True)

    def _preserve_evidence(self, journal: dict[str, Any]) -> None:
        for index, expected in enumerate(journal["plan"]["evidence"]):
            source = _file(Path(expected["path"]))
            if source.facts() != expected:
                _refuse()
            backup = self._directory(journal["digest"]) / f"evidence-{index}"
            if not _absent(backup):
                _refuse()
            private_write(backup, source.content.decode())
            fsync_dir(backup.parent)
            if _file(backup).content != source.content or _file(source.path) != source:
                _refuse()

    def _preserve_originals(self, journal: dict[str, Any], files: tuple[_File, ...]) -> None:
        """Synchronize byte-for-byte private copies before any unload or source-file removal."""
        for index, source in enumerate(files):
            if _file(source.path) != source:
                _refuse()
            backup = self._directory(journal["digest"]) / f"original-{index}"
            if not _absent(backup):
                _refuse()
            private_write(backup, source.content.decode())
            fsync_dir(backup.parent)
            captured = _file(backup)
            if captured.content != source.content or _file(source.path) != source:
                _refuse()
            journal["backups"].append(captured.facts())
            self._write(journal)

    def _verify_evidence(self, journal: dict[str, Any]) -> None:
        for index, expected in enumerate(journal["plan"]["evidence"]):
            if _file(Path(expected["path"])).facts() != expected:
                _refuse()
            backup = self._directory(journal["digest"]) / f"evidence-{index}"
            if _absent(backup) and len(journal["backups"]) < len(journal["plan"]["files"]):
                continue
            if hashlib.sha256(_file(backup).content).hexdigest() != expected["sha256"]:
                _refuse()

    def _verify_remaining(self, journal: dict[str, Any], files: tuple[_File, ...], removed: int) -> None:
        self._verify_evidence(journal)
        for index, file in enumerate(files):
            backup = _file(self._directory(journal["digest"]) / f"original-{index}")
            if backup.facts() != journal["backups"][index] or backup.content != file.content:
                _refuse()
            if index >= removed:
                if _file(file.path) != file:
                    _refuse()
            elif not _absent(file.path):
                _refuse()

    def _retire_files(self, journal: dict[str, Any], files: tuple[_File, ...]) -> None:
        for index, file in enumerate(files):
            self._verify_remaining(journal, files, index)
            if self._target() != journal["plan"]["target"]:
                _refuse()
            self._idle(token=journal["token"], check_tick=False, allow_history=True)
            journal["state"] = f"move-{index}-intent"
            self._write(journal)
            self._verify_remaining(journal, files, index)
            if self._target() != journal["plan"]["target"]:
                _refuse()
            self._manager(Path(journal["plan"]["target"]["executable"]), require_absent=True)
            self._idle(token=journal["token"], check_tick=False, allow_history=True)
            self._remove_original(file)
            journal["moved"].append(index)
            journal["state"] = f"moved-{index}"
            self._write(journal)

    @staticmethod
    def _remove_original(file: _File) -> None:
        """Remove only the still-identical source through its captured physical parent directory."""
        if _file(file.path) != file:
            _refuse()
        parent = os.open(file.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.stat(file.path.name, dir_fd=parent, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != file.identity[:2]:
                _refuse()
            os.unlink(file.path.name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)
        if not _absent(file.path):
            _refuse()

    def restore_plan(self, digest: str, *, check_tick: bool = True) -> DaemonRecoveryPlan:
        """Inspect journal-bound rollback, refusing any new target or integration."""
        journal = self._read(digest)
        if journal["state"] in {"retired", "restored"} or self._target() != journal["plan"]["target"]:
            _refuse()
        gate_absent = _absent(self.service.state_dir / PENDING)
        if gate_absent and journal["state"] != "intent":
            _refuse()
        self._idle(token=None if gate_absent else journal["token"], check_tick=check_tick, allow_history=True)
        target = journal["plan"]["target"]
        self._verify_evidence(journal)
        manager = self._manager(Path(target["executable"]))
        files = []
        preserved = []
        for index, original in enumerate(journal["plan"]["files"]):
            path = Path(original["path"])
            backups = journal["backups"]
            backup = backups[index] if index < len(backups) else None
            present = not _absent(path)
            if backup is not None:
                preserved_path = Path(backup["path"])
                if not _absent(preserved_path) and _file(preserved_path).facts() != backup:
                    _refuse()
            backup_path = self._directory(digest) / f"original-{index}"
            preserved.append(None if _absent(backup_path) else _file(backup_path).facts())
            if present:
                captured = _file(path)
                known = [original["identity"][:2]] + ([backup["identity"][:2]] if backup else [])
                if (
                    list(captured.identity[:2]) not in known
                    or hashlib.sha256(captured.content).hexdigest() != original["sha256"]
                ):
                    _refuse()
            elif backup is not None:
                captured = _file(Path(backup["path"]))
                if captured.facts() != backup or hashlib.sha256(captured.content).hexdigest() != original["sha256"]:
                    _refuse()
            else:
                _refuse()
            files.append({"original": original, "at_original": present, "current": captured.facts()})
        facts = {
            "action": "restore-legacy-daemon",
            "transaction": digest,
            "journal": _digest(journal),
            "gate_absent": gate_absent,
            "target": target,
            "files": files,
            "backups": preserved,
            "manager": manager,
            "historical_profile": "unknown",
        }
        return DaemonRecoveryPlan(digest=_digest(facts), facts=facts)

    def restore(self, digest: str, confirmation: str) -> DaemonRecoveryResult:
        """Restore only an explicitly confirmed unchanged journal; never undo terminal retirement."""
        service = self.service
        from heinzel.modules.system.daemon._service import _handover_absolute_path  # noqa: PLC0415 - shared physical lock path

        with service._lifecycle_writer(), private_lock(_handover_absolute_path(service.state_dir / "tick.lock")):
            plan = self.restore_plan(digest, check_tick=False)
            facts: dict[str, Any] = plan.model_dump()["facts"]
            if plan.digest != confirmation:
                _refuse()
            journal = self._read(digest)
            if facts["gate_absent"]:
                self._admit_restore(journal, facts, gate_absent=True)
                gate_fd = os.open(
                    service.state_dir / PENDING, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(gate_fd, "w", encoding="utf-8") as gate:
                    gate.write(json.dumps({"token": journal["token"]}, indent=2) + "\n")
                    gate.flush()
                    os.fsync(gate.fileno())
                fsync_dir(service.state_dir)
            journal["state"] = "restore-intent"
            self._write(journal)
            self._restore_files(journal, facts)
            target = journal["plan"]["target"]
            manager = self._manager(Path(target["executable"]))
            if journal["plan"]["manager"]["registered"] and not manager["registered"]:
                journal["state"] = "restore-load-intent"
                self._write(journal)
                self._admit_restore(journal, facts)
                result = service.owned_manager_command(
                    ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path(service.unit_dir))]
                )
                if result.returncode:
                    _refuse()
            facts["manager"] = self._manager(Path(target["executable"]))
            if facts["manager"]["registered"] != journal["plan"]["manager"]["registered"]:
                _refuse()
            self._admit_restore(journal, facts)
            journal["state"] = "restored"
            self._write(journal)
            self._release_gate(journal)
            return self._terminal(digest, "restored")

    def _admit_restore(self, journal: dict[str, Any], facts: dict[str, Any], *, gate_absent: bool = False) -> None:
        """Revalidate the entire coherent rollback set immediately before each effect."""
        if self._target() != facts["target"] or self._manager(Path(facts["target"]["executable"])) != facts["manager"]:
            _refuse()
        self._verify_evidence(journal)
        self._idle(token=None if gate_absent else journal["token"], check_tick=False, allow_history=True)
        for index, item in enumerate(facts["files"]):
            original = Path(item["original"]["path"])
            backup = self._directory(journal["digest"]) / f"original-{index}"
            expected_backup = facts["backups"][index]
            if expected_backup is None:
                if not _absent(backup):
                    _refuse()
            elif _file(backup).facts() != expected_backup:
                _refuse()
            if item["at_original"]:
                if _file(original).facts() != item["current"]:
                    _refuse()
            elif not _absent(original) or _file(backup).facts() != item["current"]:
                _refuse()

    def _restore_files(self, journal: dict[str, Any], facts: dict[str, Any]) -> None:
        for index, item in enumerate(facts["files"]):
            self._admit_restore(journal, facts)
            if item["at_original"]:
                continue
            path = Path(item["original"]["path"])
            backup = self._directory(journal["digest"]) / f"original-{index}"
            _rename_exclusive(backup, path)
            restored = _file(path).facts()
            if (
                restored["identity"][:2] != item["current"]["identity"][:2]
                or restored["sha256"] != item["current"]["sha256"]
            ):
                _refuse()
            item.update(at_original=True, current=restored)
            facts["backups"][index] = None
