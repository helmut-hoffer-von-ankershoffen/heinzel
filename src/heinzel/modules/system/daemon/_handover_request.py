"""The daemon's handover request: ask the running unit to hand over, then observe and assess it."""

import hashlib
import os
import re
import secrets
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from heinzel import HeinzelError
from heinzel._adapter_service import Job as ManagerJob
from heinzel._adapter_service import LaunchdServiceManager, ManagerRefused, SystemdServiceManager
from heinzel.kernel import (
    DaemonHandoverAssessment,
    DaemonHandoverObservation,
    DaemonHandoverPlan,
    DaemonHandoverRequest,
)
from heinzel.modules.system.daemon import _process_birth
from heinzel.modules.system.daemon._handover_files import (
    invalid_handover as _invalid_handover,
)
from heinzel.modules.system.daemon._handover_files import (
    verified_handover_file as _open_verified_handover_file,
)
from heinzel.modules.system.daemon._mutation_scope import MutationHandover
from heinzel.modules.system.daemon._ownership import ManagerState
from heinzel.modules.system.daemon._runtime import lock
from heinzel.modules.system.daemon._units import LABEL, SYSTEMD_NAME
from heinzel.ports import ServiceManager

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable

STALE_TICKS = 3
"""A daemon that missed this many intervals in a row is reported as degraded."""
MAX_HANDOVER_FILE_BYTES = 256 * 1024
MAX_HANDOVER_CONTRIBUTIONS = 512
MAX_HANDOVER_TEXT = 512
HANDOVER_ROW_FIELDS = 3
MAX_BOOT_IDENTITY = 128
MAX_OBSERVATION_BYTES = 128 * 1024


def heartbeat_covers(heartbeat: dict[str, str | int], tick_at: str, version: str) -> bool:
    last_tick = heartbeat.get("last_tick")
    if not isinstance(last_tick, str) or heartbeat.get("version") != version:
        return False
    try:
        return datetime.fromisoformat(last_tick) >= datetime.fromisoformat(tick_at)
    except (ValueError, TypeError):
        return False


class HandoverRequests:
    """Handover requests and observations of DaemonService; their state lives on the service."""

    if TYPE_CHECKING:
        _clock: Callable[[], datetime]
        _continuity_fingerprint: Callable[[], str | None] | None
        _platform: str
        _process_birth: Callable[[], dict[str, int] | None]
        _uid: Callable[[], int]
        state_dir: Path

        def _current_birth_fence(self) -> dict[str, int] | None: ...
        def _current_boot_identity(self) -> str | None: ...
        def _handover_pending(self) -> bool: ...
        def _interval(self) -> int: ...
        def _read_state(self, name: str) -> dict[str, str | int]: ...
        @staticmethod
        def _request_birth_fence(request: dict[str, str | int]) -> dict[str, int] | None: ...
        def _unit_paths(self) -> list[Path]: ...
        def _write_state(self, name: str, data: dict[str, str | int]) -> None: ...
        def capture_handover(self) -> DaemonHandoverPlan: ...
        def owned_manager_command(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...
        def owned_manager_state(self, binary: Path) -> ManagerState: ...
        def owned_unit_state(self, binary: Path, *, allow_absent: bool = False) -> bool: ...

    def request_handover(self, previous: DaemonHandoverPlan) -> DaemonHandoverRequest:
        """Request a handover under the lifecycle writer lock.

        Raises:
            HeinzelError: If the service cannot be safely inspected.
        """
        with lock(self.state_dir / "lifecycle.lock") as acquired:
            if not acquired or self._handover_pending():
                return DaemonHandoverRequest(
                    requested=False, reason="The daemon lifecycle is changing or requires recovery."
                )
            return self._request_handover_locked(previous)

    def _request_handover_locked(
        self, previous: DaemonHandoverPlan, mutation: MutationHandover | None = None
    ) -> DaemonHandoverRequest:
        """Ask only the unchanged active service to hand over; manager acceptance is not execution evidence.

        Raises:
            HeinzelError: If the manager rejects the request or service files cannot be inspected.
        """
        if type(previous) is not DaemonHandoverPlan:
            msg = "The pre-change daemon observation is invalid."
            raise HeinzelError(msg)
        if not previous.active or previous.platform != self._platform or previous.unit_signature is None:
            return DaemonHandoverRequest(requested=False, reason="The service was not active before the change.")
        try:
            current = self.capture_handover()
        except (OSError, HeinzelError):
            return DaemonHandoverRequest(
                requested=False, reason="The owned service identity changed; no handover was requested."
            )
        if not current.active or current.unit_signature != previous.unit_signature:
            return DaemonHandoverRequest(
                requested=False, reason="The service state changed; no handover was requested."
            )
        request_id = secrets.token_hex(32)
        boot = self._current_boot_identity()
        fence = self._current_birth_fence()
        data: dict[str, str | int] = {
            "request_id": request_id,
            "boot": boot or "",
            "unit_signature": previous.unit_signature,
        }
        if fence is not None:
            data.update({f"birth_{key}": value for key, value in fence.items()})
        if mutation is not None:
            data["mutation_binding"] = mutation.binding(data)
        self._write_state("handover-request.json", data)
        try:
            self._service_manager().kickstart(
                ManagerJob(LABEL if self._platform == "darwin" else f"{SYSTEMD_NAME}.timer")
            )
        except ManagerRefused as exc:
            msg = f"The daemon handover request failed: {exc.detail}"
            raise HeinzelError(msg) from exc
        return DaemonHandoverRequest(
            requested=True,
            reason="The manager accepted the request; a later service tick must establish what ran.",
            request_id=request_id,
        )

    def _service_manager(self) -> ServiceManager:
        """Launchd or systemd for the daemon's own job, every command through the owned manager gate."""
        if self._platform == "darwin":
            return LaunchdServiceManager(
                self.owned_manager_command, uid=self._uid(), base=["/bin/launchctl"], subject="daemon"
            )
        return SystemdServiceManager(
            self.owned_manager_command, base=["/usr/bin/systemctl", "--user"], subject="daemon"
        )

    def handover_observation(self, request_id: str) -> DaemonHandoverObservation | None:
        """Read the current request's manager-bound tick observation, or remain pending."""
        request = self._read_state("handover-request.json")
        boot = request.get("boot")
        if (
            not re.fullmatch(r"[a-f0-9]{64}", request_id)
            or request.get("request_id") != request_id
            or not isinstance(boot, str)
            or not boot
            or self._current_boot_identity() != boot
            or self._continuity_fingerprint is None
            or self._handover_pending()
        ):
            return None
        path = self.state_dir / "handover-observation.json"
        try:
            with path.open("rb") as source:
                raw = source.read(MAX_OBSERVATION_BYTES + 1)
            if len(raw) > MAX_OBSERVATION_BYTES:
                return None
            result = TypeAdapter(DaemonHandoverObservation).validate_json(raw, strict=True)
            heartbeat = self._read_state("heartbeat.json")
            tick_at = datetime.fromisoformat(result.tick_at)
            age = self._clock() - tick_at
            if (
                result.request_id != request_id
                or result.old_unit_signature != request.get("unit_signature")
                or result.new_unit_signature != self._handover_signature()
                or not re.fullmatch(r"[a-f0-9]{64}", result.fingerprint)
                or len(result.contributions) > MAX_HANDOVER_CONTRIBUTIONS
                or not heartbeat_covers(heartbeat, result.tick_at, result.version)
                or age < timedelta(0)
                or age > timedelta(minutes=STALE_TICKS * self._interval())
            ):
                return None
        except (OSError, ValueError, TypeError, HeinzelError):
            return None
        try:
            return result if self._continuity_fingerprint() == result.fingerprint else None
        except Exception:  # noqa: BLE001 - a changed or unavailable local observation remains pending
            return None

    def assess_handover(
        self,
        request_id: str,
        expected_version: str,
        expected_fingerprint: str,
        expected_contributions: tuple[tuple[str, str, str], ...],
    ) -> DaemonHandoverAssessment:
        """Compare a fresh active service observation with an explicit caller-owned target.

        This does not compare the caller's imported version or rewrite the historical install marker.

        Raises:
            HeinzelError: If the expected target is malformed or unbounded.
        """
        if (
            not expected_version
            or len(expected_version) > MAX_BOOT_IDENTITY
            or not re.fullmatch(r"[a-f0-9]{64}", expected_fingerprint)
            or len(expected_contributions) > MAX_HANDOVER_CONTRIBUTIONS
            or any(
                len(row) != HANDOVER_ROW_FIELDS or any(not item or len(item) > MAX_HANDOVER_TEXT for item in row)
                for row in expected_contributions
            )
            or len(set(expected_contributions)) != len(expected_contributions)
        ):
            msg = "The expected daemon handover target is invalid."
            raise HeinzelError(msg)
        try:
            if not self.capture_handover().active:
                return DaemonHandoverAssessment(status="pending", observed=None)
            observed = self.handover_observation(request_id)
        except (OSError, HeinzelError):
            observed = None
        if observed is None:
            return DaemonHandoverAssessment(status="pending", observed=None)
        matched = (
            observed.version == expected_version
            and observed.fingerprint == expected_fingerprint
            and sorted(observed.contributions) == sorted(expected_contributions)
        )
        return DaemonHandoverAssessment(status="matched" if matched else "mismatched", observed=observed)

    def idle_package_handover_matches(self, request: dict[str, str | int], binary: Path) -> bool:
        """Verify the exact same-boot request and its unchanged, idle, active manager registration.

        Raises:
            HeinzelError: If the manager authority or loaded unit cannot be verified.
        """
        boot = self._current_boot_identity()
        fence = self._request_birth_fence(request)
        birth = self._process_birth()
        return (
            self._platform == "darwin"
            and self._uid() == os.getuid()
            and bool(boot)
            and request.get("boot") == boot
            and fence is not None
            and birth is not None
            and _process_birth.after_request(fence, birth)
            and self._request_unit_matches(request)
            and self.owned_unit_state(binary)
            and self.owned_manager_state(binary) == ManagerState(registered=True, active=True, enabled=True)
        )

    def _request_unit_matches(self, request: dict[str, str | int]) -> bool:
        signature = request.get("unit_signature")
        try:
            return isinstance(signature, str) and self._handover_signature() == signature
        except (OSError, HeinzelError):
            return False

    def _handover_signature(self, marker_override: bytes | None = None) -> str:
        """Bind an observation to the existing private unit and install marker, without following links.

        Raises:
            HeinzelError: If the unit cannot be safely observed.
        """
        digest = hashlib.sha256()
        try:
            for path in [*self._unit_paths(), self.state_dir / "install.json"]:
                with _open_verified_handover_file(path) as handle:
                    info = os.fstat(handle.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.getuid()
                        or info.st_mode & 0o022
                        or info.st_size > MAX_HANDOVER_FILE_BYTES
                    ):
                        _invalid_handover()
                    payload = handle.read(MAX_HANDOVER_FILE_BYTES + 1)
                    after = os.fstat(handle.fileno())
                    if len(payload) != info.st_size or (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    ) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                        _invalid_handover()
                if path == self.state_dir / "install.json" and marker_override is not None:
                    payload = marker_override
                digest.update(str(path).encode() + b"\0" + payload + b"\0")
        except OSError as exc:
            msg = "The existing daemon unit could not be verified; no handover was requested."
            raise HeinzelError(msg) from exc
        return digest.hexdigest()
