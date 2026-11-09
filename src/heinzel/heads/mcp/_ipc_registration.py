"""Committed dormant MCP registrations under the SDK's host-maintenance authority."""

import hashlib
import os
import pwd
import re
import secrets
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from heinzel.heads.mcp._ipc_admission import McpGeneration
from heinzel.heads.mcp._ipc_approval import PrivateMcpPlan
from heinzel.heads.mcp._ipc_cli_review import require_local_mcp_terminal, review_mcp_lifecycle_from_terminal
from heinzel.heads.mcp._ipc_lifecycle import REGISTRATION_ID, ForegroundMcpLifecycle, ForegroundMcpRegistration
from heinzel.heads.mcp._ipc_native_plan import LINUX_UNIT_PREFIX, MAC_LABEL_PREFIX, McpNativePlan
from heinzel.heads.mcp._ipc_runtime_root import (
    InstalledMcpRuntime,
    _encode,
    _identity,
    _physical_directory,
    _read_record,
    _record,
    installation_directories,
)
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

if TYPE_CHECKING:
    from pydantic import JsonValue

    from heinzel.kernel import HostServiceManager

_REFUSED = "The committed MCP registration or its installed host could not be verified"
_FILES = re.compile(r"mcp-registration-([A-Za-z0-9_-]{1,96})\.(intent|commit)\Z", re.ASCII)
_INVENTORY_LIMIT = 10_000
_POLICY: Literal["foreground-transport-only"] = "foreground-transport-only"
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


class McpRegistrationReceipt(BaseModel):
    """A plain, bounded dormant record. It grants neither lifecycle consent nor lease authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    uid: int
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    nonce: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    prefix: str
    launcher: str
    profile: str
    directories: tuple[tuple[int, int], tuple[int, int]]
    lock_identity: tuple[int, int]
    generation: Literal[1] = 1
    policy: Literal["foreground-transport-only"] = _POLICY

    def data(self) -> bytes:
        """The canonical bytes covered by the exclusive commit marker."""
        return _encode(self.model_dump(mode="json"))


def _read_intent(directory: int, name: str) -> McpRegistrationReceipt | None:
    data = _read_record(directory, f"mcp-registration-{name}.intent")
    if data is None:
        return None
    receipt = McpRegistrationReceipt.model_validate_json(data)
    if receipt.id != name or receipt.data() != data:
        raise HeinzelError(_REFUSED)
    return receipt


def _commit_bytes(receipt: McpRegistrationReceipt) -> bytes:
    return _encode({"schema": 1, "intent_sha256": hashlib.sha256(receipt.data()).hexdigest()})


def _has_commit(directory: int, name: str, receipt: McpRegistrationReceipt | None) -> bool:
    data = _read_record(directory, f"mcp-registration-{name}.commit")
    if data is None:
        return False
    if receipt is None or data != _commit_bytes(receipt):
        raise HeinzelError(_REFUSED)
    return True


def _current_lock(directory: int) -> tuple[int, int]:
    descriptor = CurrentUser.open_private(directory, "lifecycle.lock", os.O_RDONLY)
    try:
        return _identity(descriptor)
    finally:
        os.close(descriptor)


def _verify_scope(
    directory: int, receipt: McpRegistrationReceipt, runtime: InstalledMcpRuntime, *, require_sdk_directory: bool = True
) -> None:
    with _physical_directory(runtime.state, private=True) as profile:
        observed_profile = _identity(profile)
    if (
        receipt.uid != os.getuid()
        or receipt.prefix != str(runtime.prefix)
        or receipt.launcher != str(runtime.prefix / "bin" / "hz")
        or receipt.profile != str(runtime.state)
        or receipt.lock_identity != _current_lock(directory)
        or receipt.directories[1] != observed_profile
        or (require_sdk_directory and receipt.directories != installation_directories(runtime))
    ):
        raise HeinzelError(_REFUSED)


def mcp_registration_inventory(prefix: Path) -> tuple[McpRegistrationReceipt, ...]:
    """Verify every dormant registration under the SDK lock, including interrupted publications.

    Raises:
        HeinzelError: If the inventory contains uncertain, uncommitted or changed authority.
    """
    authority = host_maintenance_directory(prefix)
    runtime = InstalledMcpRuntime(prefix, authority / "mcp-profile")
    try:
        with _physical_directory(authority, private=True) as directory, os.scandir(directory) as entries:
            names = [entry.name for entry in islice(entries, _INVENTORY_LIMIT + 1)]
            if len(names) > _INVENTORY_LIMIT:
                raise HeinzelError(_REFUSED)
            ids = set()
            for name in names:
                if name.startswith("mcp-registration-"):
                    match = _FILES.fullmatch(name)
                    if match is None:
                        raise HeinzelError(_REFUSED)
                    ids.add(match[1])
            records = []
            for id_ in sorted(ids):
                receipt = _read_intent(directory, id_)
                if receipt is None or not _has_commit(directory, id_, receipt):
                    msg = "An MCP registration requires explicit recovery before changing the SDK."
                    raise HeinzelError(msg)
                _verify_scope(directory, receipt, runtime, require_sdk_directory=False)
                records.append(receipt)
            return tuple(records)
    except FileNotFoundError:
        # A missing authority directory is an empty namespace. A missing entry inside it is uncertainty.
        if os.path.lexists(authority):
            raise HeinzelError(_REFUSED) from None
        return ()
    except (OSError, ValueError):
        raise HeinzelError(_REFUSED) from None


def native_mcp_scope() -> HostIntegrationScope:
    """Select the OS user definition namespace without consulting the caller's HOME or config.

    Raises:
        HeinzelError: If the OS does not identify the current user's home.
    """
    try:
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        raise HeinzelError(_REFUSED) from None
    platform = str(sys.platform)
    if platform == "darwin":
        return HostIntegrationScope(home / "Library" / "LaunchAgents", MAC_LABEL_PREFIX, MAC_LABEL_PREFIX)
    if platform == "linux":
        return HostIntegrationScope(home / ".config" / "systemd" / "user", LINUX_UNIT_PREFIX, LINUX_UNIT_PREFIX)
    raise HeinzelError(_REFUSED)


def _require_native_absence(
    runtime: InstalledMcpRuntime, id_: str, manager: "HostServiceManager", scope: HostIntegrationScope
) -> None:
    plan = McpNativePlan.capture(runtime, id_)
    unit = scope.directory / (plan.name + ".plist" if plan.platform == "darwin" else plan.name)
    parent = scope.directory
    while not os.path.lexists(parent):
        parent = parent.parent
    with _physical_directory(parent):
        if os.path.lexists(unit) or plan.observe(manager) != "absent":
            raise HeinzelError(_REFUSED)


def verify_mcp_native_inventory(
    prefix: Path,
    records: tuple[McpRegistrationReceipt, ...],
    manager: "HostServiceManager",
    scope: HostIntegrationScope,
) -> None:
    """Refuse any loaded or on-disk unit claiming a dormant registration's exact native name."""
    for receipt in records:
        _require_native_absence(InstalledMcpRuntime(prefix, Path(receipt.profile)), receipt.id, manager, scope)


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
