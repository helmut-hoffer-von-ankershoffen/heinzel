"""Explicit forward recovery of an interrupted, verified 0.9.22 automatic update."""

import fcntl
import hashlib
import json
import os
import re
import stat
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Never

from pydantic import JsonValue, TypeAdapter

from heinzel import HeinzelError
from heinzel.kernel import (
    DaemonHandoverObservation,
    DaemonRecoverySelection,
    Installation,
    installation_mutation_paths,
    private_lock,
)
from heinzel.modules.system.daemon._recovery_files import RecoveryFile, path_absent, recovery_file, rename_exclusive
from heinzel.modules.system.daemon._service import handover_absolute_path, verified_handover_file

if TYPE_CHECKING:
    from heinzel.modules.system.daemon._service import DaemonService

_OLD_VERSION = "0.9.21"
_NEW_VERSION = "0.9.22"
_DIGEST = re.compile(r"[a-f0-9]{64}")
_MUTATION = "mutation-pending.json"
_REQUEST = "handover-request.json"
_REQUEST_FIELDS = {"request_id", "boot", "unit_signature", "birth_ticks", "birth_hz", "birth_ns_dev", "birth_ns_ino"}


def _refuse() -> Never:
    msg = "The interrupted package handover cannot be verified; its state was not changed."
    raise HeinzelError(msg)


def _object(record: RecoveryFile) -> dict[str, str | int]:
    # Only the exact indented form written by DaemonService._write_state is in scope.
    parsed = TypeAdapter(dict[str, str | int]).validate_json(record.content, strict=True)
    if record.content != (json.dumps(parsed, indent=2) + "\n").encode():
        _refuse()
    return parsed


def _is_old_heartbeat(data: dict[str, str | int], interval: str | int | None) -> bool:
    tick, done = data.get("last_tick"), data.get("completed_at")
    if (
        data.get("version") != _OLD_VERSION
        or data.get("source") != "scheduled"
        or data.get("outcome") not in {"completed", "failed"}
        or data.get("interval_minutes") != interval
        or not isinstance(tick, str)
        or not isinstance(done, str)
    ):
        return False
    tick_at, completed_at = datetime.fromisoformat(tick), datetime.fromisoformat(done)
    return tick_at.tzinfo is not None and completed_at.tzinfo is not None and tick_at <= completed_at


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


@contextmanager
def _existing_apply_lock(path: Path) -> Generator[None]:
    # 0.9.22 creates this flock with the user's ordinary umask, so 0644 is allowed.
    with verified_handover_file(path) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_nlink != 1:
            _refuse()
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _refuse()
        yield


class PackageHandoverRecovery:
    """Archive only a verified interruption after package trial, leaving the genuine scheduled tick to attest."""

    def __init__(self, service: "DaemonService", selection: DaemonRecoverySelection) -> None:
        self.service = service
        self.selection = selection

    def _installation(self) -> Installation:
        installation = self.service.recovery_installation
        if (
            installation is None
            or self.service.recovery_platform != "darwin"
            or self.service.recovery_uid() != os.getuid()
        ):
            _refuse()
        return installation

    def _gates(self, installation: Installation) -> None:
        daemon = self.service.state_dir
        state = daemon.parent
        snapshots = (state / "auto-update").lstat()
        if not stat.S_ISDIR(snapshots.st_mode) or snapshots.st_uid != os.getuid() or snapshots.st_mode & 0o022:
            _refuse()
        paths = (
            *installation_mutation_paths(installation.prefix, state),
            daemon / "handover-pending.json",
            daemon / "paused",
            state / "auto-update/after-update",
            state / "auto-update/before-update.partial",
        )
        if not all(path_absent(path) for path in paths if path != daemon / _MUTATION):
            _refuse()

    def _request(self, record: RecoveryFile) -> dict[str, str | int]:
        request = _object(record)
        if (
            set(request) != _REQUEST_FIELDS
            or not _is_digest(request["request_id"])
            or not _is_digest(request["unit_signature"])
            or not isinstance(request["birth_ticks"], int)
            or request["birth_ticks"] <= 0
            or request["birth_hz"] != 0
            or request["birth_ns_dev"] != 0
            or request["birth_ns_ino"] != 0
        ):
            _refuse()
        return request

    def _failed_update(self, request: RecoveryFile) -> RecoveryFile:
        record = recovery_file(self.service.state_dir.parent / "web/auto-update.json", private=False)
        plan = TypeAdapter(dict[str, JsonValue]).validate_json(record.content, strict=True)
        retry = plan.get("retry_at")
        if (
            plan.get("core") != _NEW_VERSION
            or plan.get("apply_at") is not None
            or plan.get("restarting_to") is not None
            or not isinstance(retry, str)
            or retry != plan.get("window_at")
            or plan.get("message")
            != (
                "The automatic update didn't work, so hz 0.9.21 keeps running as before. It will try again in 6 hours; "
                "to update now, use Update in the footer."
            )
        ):
            _refuse()
        failed_at = datetime.fromisoformat(retry) - timedelta(hours=6)
        request_at = datetime.fromtimestamp(request.modified_ns / 1_000_000_000, UTC)
        written_at = datetime.fromtimestamp(record.modified_ns / 1_000_000_000, UTC)
        if (
            failed_at.tzinfo is None
            or not request_at < failed_at <= written_at <= failed_at + timedelta(minutes=1)
            or failed_at > request_at + timedelta(minutes=1)
        ):
            _refuse()
        return record

    def _records(
        self, installation: Installation, generation: str, incident: tuple[str, str, str]
    ) -> dict[str, JsonValue]:
        daemon = self.service.state_dir
        mutation = recovery_file(daemon / _MUTATION)
        request_file = recovery_file(daemon / _REQUEST)
        checkpoint = recovery_file(installation.prefix / ".heinzel-startup-state")
        marker = recovery_file(daemon / "install.json")
        heartbeat = recovery_file(daemon / "heartbeat.json")
        request = self._request(request_file)
        mutation_data = _object(mutation)
        marker_data = _object(marker)
        heartbeat_data = _object(heartbeat)
        if (
            set(mutation_data) != {"token"}
            or not _is_digest(mutation_data["token"])
            or mutation_data["token"] == request["request_id"]
            or checkpoint.content != (generation + "\n").encode()
            or not max(marker.modified_ns, heartbeat.modified_ns)
            < mutation.modified_ns
            < checkpoint.modified_ns
            < request_file.modified_ns
            or marker_data.get("version") != _OLD_VERSION
            or marker_data.get("handover_id") == request["request_id"]
            or not _is_old_heartbeat(heartbeat_data, marker_data.get("interval_minutes"))
        ):
            _refuse()
        _, binary = installation.tool_destination()
        if not self.service.idle_package_handover_matches(request, binary):
            _refuse()
        observation_path = daemon / "handover-observation.json"
        observation = None if path_absent(observation_path) else recovery_file(observation_path)
        if observation is not None and (
            observation.modified_ns >= mutation.modified_ns
            or TypeAdapter(DaemonHandoverObservation).validate_json(observation.content, strict=True).request_id
            == request["request_id"]
        ):
            _refuse()
        failure = self._failed_update(request_file)
        # Released records did not causally link the automatic trial, mutation and request.
        # The caller must supply their independently captured, reviewed incident evidence.
        if (
            tuple(hashlib.sha256(record.content).hexdigest() for record in (mutation, request_file, failure))
            != incident
        ):
            _refuse()
        return {
            "mutation": mutation.facts(),
            "request": request_file.facts(),
            "failed_update": failure.facts(),
            "request_id": request["request_id"],
            "checkpoint": checkpoint.facts(),
            "install_marker": marker.facts(),
            "heartbeat": heartbeat.facts(),
            "previous_observation": observation.facts() if observation is not None else None,
            "executable": str(binary / "hz"),
        }

    def _snapshot(self, installation: Installation, incident: tuple[str, str, str]) -> dict[str, JsonValue]:
        self._gates(installation)
        standard = recovery_file(Path(self.service.recovery_profile["HZ_DATA_DIR"]) / "standard-app.json")
        if standard.path != self.selection.standard_receipt or standard.content != self.selection.content:
            _refuse()
        generation = installation.startup_fingerprint()
        if not installation.startup_state_matches() or installation.installed_core_version() != _NEW_VERSION:
            _refuse()
        evidence = self._records(installation, generation, incident)
        if not installation.startup_state_matches() or installation.startup_fingerprint() != generation:
            _refuse()
        self._gates(installation)
        return {
            "action": "archive-reviewed-interrupted-mutation",
            "transaction_binding": "externally-reviewed-incident-evidence",
            "old_daemon_version": _OLD_VERSION,
            "installed_package_version": _NEW_VERSION,
            "uid": os.getuid(),
            "profile": dict(self.service.recovery_profile),
            "prefix": str(installation.prefix),
            "generation": generation,
            "standard_receipt": standard.facts(),
            **evidence,
        }

    def _plan(self, installation: Installation, incident: tuple[str, str, str]) -> dict[str, JsonValue]:
        if not all(_is_digest(item) for item in incident):
            _refuse()
        facts = self._snapshot(installation, incident)
        digest = hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        archive = handover_absolute_path(self.service.state_dir / f"recovered-mutation-{digest}.json")
        if not path_absent(archive):
            _refuse()
        return {"digest": digest, "facts": facts, "archive": str(archive), "status": "confirmation-required"}

    @contextmanager
    def _web_writers(self) -> Generator[None]:
        web = self.service.state_dir.parent / "web"
        with _existing_apply_lock(web / "auto-update-apply.lock"), _existing_apply_lock(web / "auto-update.lock"):
            yield

    def plan(self, incident: tuple[str, str, str]) -> dict[str, JsonValue]:
        """Inspect the exact supported interruption without modifying any file or registration."""
        installation = self._installation()
        with self._web_writers(), installation.tool_transaction():
            return self._plan(installation, incident)

    def resume(self, incident: tuple[str, str, str], confirmation: str) -> dict[str, JsonValue]:
        """Archive the exact pending token atomically, with ordinary update and tick writers fenced.

        Raises:
            HeinzelError: If evidence changed, ownership is uncertain, or the confirmation does not match.
            OSError: If the archive move or its durability cannot be confirmed.
        """
        if not _is_digest(confirmation):
            _refuse()
        installation = self._installation()
        daemon = handover_absolute_path(self.service.state_dir)
        with (
            self._web_writers(),
            self.service.recovery_writer(),
            private_lock(daemon / "tick.lock"),
            installation.tool_transaction(),
        ):
            current = self._plan(installation, incident)
            if current["digest"] != confirmation:
                _refuse()
            archive = daemon / f"recovered-mutation-{confirmation}.json"
            source = recovery_file(daemon / _MUTATION)
            facts = current["facts"]
            if not isinstance(facts, dict) or facts["mutation"] != source.facts():
                _refuse()
            rename_exclusive(source.path, archive, expected_source=source)
        return {
            "digest": confirmation,
            "archive": str(archive),
            "status": "awaiting-scheduled-tick",
            "message": "The package mutation was archived. Wait for the owned daemon's next scheduled tick.",
        }
