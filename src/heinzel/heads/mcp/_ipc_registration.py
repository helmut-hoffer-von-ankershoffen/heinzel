"""Committed dormant MCP registrations under the SDK's host-maintenance authority."""

import hashlib
import os
import secrets
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from heinzel.heads.mcp._ipc_admission import McpGeneration
from heinzel.heads.mcp._ipc_approval import PrivateMcpPlan
from heinzel.heads.mcp._ipc_cli_review import require_local_mcp_terminal, review_mcp_lifecycle_from_terminal
from heinzel.heads.mcp._ipc_lifecycle import REGISTRATION_ID, ForegroundMcpLifecycle, ForegroundMcpRegistration
from heinzel.kernel import (
    CurrentUser,
    HeinzelError,
    HostIntegrationScope,
    Installation,
    MaintenanceLocks,
    Progress,
    host_maintenance_directory,
    host_maintenance_lock,
    installation_mutation_pending,
)
from heinzel.kernel._logging import get_logger, step
from heinzel.kernel._mcp_native import McpNativePlan
from heinzel.kernel._mcp_registry import (
    _POLICY,
    _REFUSED,
    McpRegistrationReceipt,
    _commit_bytes,
    _current_lock,
    _has_commit,
    _read_intent,
    _require_native_absence,
    _verify_scope,
)
from heinzel.kernel._mcp_storage import InstalledMcpRuntime, _physical_directory, _record, installation_directories

if TYPE_CHECKING:
    from pydantic import JsonValue

    from heinzel.kernel import HostServiceManager

_REVIEW_SECONDS = 60.0
_GRANT_SECONDS = 3600.0
log = get_logger("mcp")


@dataclass(frozen=True, slots=True)
class InstalledMcpAuthority:
    """The existing standard SDK's verified installed script, interpreter and startup generation."""

    sdk: Installation = field(repr=False)
    fingerprint: str

    @property
    def runtime(self) -> InstalledMcpRuntime:
        """Select a private SDK-scoped foreground profile, independent of HOME, HZ_* and project configuration."""
        return InstalledMcpRuntime(self.sdk.prefix, host_maintenance_directory(self.sdk.prefix) / "mcp-profile")

    @property
    def launcher(self) -> Path:
        """The exact bin/hz script verified by the installed SDK's continuity record."""
        return self.sdk.prefix / "bin" / "hz"

    def require_current(self) -> None:
        """Refuse changed installed bytes, ownership or pending SDK mutation.

        Raises:
            HeinzelError: If this SDK no longer matches the captured installed generation.
        """
        if (
            installation_mutation_pending(self.sdk.prefix, self.runtime.state)
            or not self.sdk.startup_state_matches()
            or self.sdk.startup_fingerprint() != self.fingerprint
        ):
            raise HeinzelError(_REFUSED)


def installed_mcp_authority() -> InstalledMcpAuthority:
    """Resolve the actual installed CLI before discovery or reading a registration.

    Raises:
        HeinzelError: If there is no local terminal or immutable, standard installed SDK.
    """
    require_local_mcp_terminal()
    sdk = Installation(Progress())
    prefix = sdk.prefix
    executable = Path(sys.executable)
    store, binary = sdk.tool_destination()
    if (
        prefix != store / "heinzel"
        or not Path(__file__).resolve(strict=True).is_relative_to(prefix)
        or executable.parent != prefix / "bin"
        or not executable.samefile(prefix / "bin" / "python")
        or binary / "hz" not in sdk.tool_entrypoints()
        or not os.access(prefix / "bin" / "hz", os.X_OK)
        or not sdk.startup_state_matches()
    ):
        raise HeinzelError(_REFUSED)
    authority = InstalledMcpAuthority(sdk, sdk.startup_fingerprint())
    authority.require_current()
    return authority


@dataclass(frozen=True, slots=True)
class InstalledMcpRegistry:
    """The installed CLI's verified SDK, dormant receipts and existing native-manager inspection port."""

    authority: InstalledMcpAuthority
    manager: "HostServiceManager"
    native_scope: HostIntegrationScope

    def _require_native_absence(self, id_: str) -> None:
        _require_native_absence(self.authority.runtime, id_, self.manager, self.native_scope)

    def _review(self, action: str, id_: str, receipt: McpRegistrationReceipt | None) -> float:
        deadline = time.monotonic() + _REVIEW_SECONDS
        runtime = self.authority.runtime
        offer: dict[str, JsonValue] = {
            "kind": "foreground-lifecycle",
            "action": action,
            "action_id": secrets.token_urlsafe(24),
            "registration": id_,
            "generation": 1,
            "policy": _POLICY,
            "sdk": str(runtime.prefix),
            "sdk_fingerprint": self.authority.fingerprint,
            "launcher": str(self.authority.launcher),
            "profile": str(runtime.state),
            "native_label": McpNativePlan.capture(runtime, id_).name,
            "review_expires_at": deadline,
            "prepared_intent": None if receipt is None else hashlib.sha256(receipt.data()).hexdigest(),
        }
        review_mcp_lifecycle_from_terminal(offer)
        return deadline

    def publish(self, action: Literal["register", "recover"], id_: str) -> None:
        """Record a new dormant intent or explicitly complete a verified interrupted one.

        Raises:
            HeinzelError: If exact approval, SDK identity, manager absence or the transaction cannot be proven.
        """
        with step(log, "mcp.ipc.registration", operation="ipc", step=action):
            self._publish(action, id_)

    def _publish(self, action: Literal["register", "recover"], id_: str) -> None:
        if not REGISTRATION_ID.fullmatch(id_):
            raise HeinzelError(_REFUSED)
        runtime = self.authority.runtime
        lock = host_maintenance_lock(runtime.prefix)
        self.authority.require_current()
        with MaintenanceLocks((lock,)) as locks, _physical_directory(lock.parent, private=True) as directory:
            prior = _read_intent(directory, id_)
            committed = _has_commit(directory, id_, prior)
            if (action == "register") != (prior is None) or committed:
                raise HeinzelError(_REFUSED)
            self._require_native_absence(id_)
            deadline = self._review(action, id_, prior)
            self.authority.require_current()
            self._require_native_absence(id_)
            if prior != _read_intent(directory, id_) or _has_commit(directory, id_, prior):
                raise HeinzelError(_REFUSED)
            lock_identity = locks.identity(lock)
            if _current_lock(directory) != lock_identity or time.monotonic() >= deadline:
                raise HeinzelError(_REFUSED)
            if prior is None:
                with suppress(FileExistsError):  # This is bounded storage, with no lifecycle authority.
                    os.mkdir(runtime.state.name, mode=0o700, dir_fd=directory)
                os.fsync(directory)
                prior = McpRegistrationReceipt(
                    uid=os.getuid(),
                    id=id_,
                    nonce=secrets.token_hex(32),
                    fingerprint=self.authority.fingerprint,
                    prefix=str(runtime.prefix),
                    launcher=str(self.authority.launcher),
                    profile=str(runtime.state),
                    directories=installation_directories(runtime),
                    lock_identity=lock_identity,
                )
                _record(directory, f"mcp-registration-{id_}.intent", prior.data())
            _verify_scope(directory, prior, runtime)
            if prior.fingerprint != self.authority.fingerprint or locks.identity(lock) != prior.lock_identity:
                raise HeinzelError(_REFUSED)
            intent = CurrentUser.open_private(directory, f"mcp-registration-{id_}.intent", os.O_RDONLY)
            try:
                os.fsync(intent)
            finally:
                os.close(intent)
            if time.monotonic() >= deadline:
                raise HeinzelError(_REFUSED)
            _record(directory, f"mcp-registration-{id_}.commit", _commit_bytes(prior))

    def _committed(self, id_: str) -> McpRegistrationReceipt:
        runtime = self.authority.runtime
        self.authority.require_current()
        with _physical_directory(host_maintenance_directory(runtime.prefix), private=True) as directory:
            receipt = _read_intent(directory, id_)
            if receipt is None or not _has_commit(directory, id_, receipt):
                raise HeinzelError(_REFUSED)
            _verify_scope(directory, receipt, runtime)
            if receipt.fingerprint != self.authority.fingerprint:
                raise HeinzelError(_REFUSED)
            return receipt

    def host(self, id_: str) -> ForegroundMcpLifecycle:
        """Load only a committed exact dormant registration, without starting it or granting vendor rights.

        Raises:
            HeinzelError: If the committed registration and current installed scope do not agree.
        """
        if not REGISTRATION_ID.fullmatch(id_):
            raise HeinzelError(_REFUSED)
        receipt = self._committed(id_)

        def current() -> None:
            if self._committed(id_) != receipt:
                raise HeinzelError(_REFUSED)
            self._require_native_absence(id_)

        plan = PrivateMcpPlan.capture(
            McpGeneration(id_, receipt.generation),
            receipt.nonce,
            0,
            time.monotonic() + _GRANT_SECONDS,
            {"dispatch": {}},
        )
        registration = ForegroundMcpRegistration.capture(self.authority.runtime, plan, verify_binding=current)
        return ForegroundMcpLifecycle(registration, manager=self.manager)
