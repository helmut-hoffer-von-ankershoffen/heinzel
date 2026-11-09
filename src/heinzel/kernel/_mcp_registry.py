"""SDK maintenance inventory and exact dormant MCP registration records."""

import hashlib
import os
import pwd
import re
import sys
from itertools import islice
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._host_maintenance import HostIntegrationScope, HostServiceManager, host_maintenance_directory
from heinzel.kernel._identity import CurrentUser
from heinzel.kernel._mcp_native import LINUX_UNIT_PREFIX, MAC_LABEL_PREFIX, McpNativePlan
from heinzel.kernel._mcp_storage import (
    InstalledMcpRuntime,
    _encode,
    _identity,
    _physical_directory,
    _read_record,
    installation_directories,
)

_REFUSED = "The committed MCP registration or its installed host could not be verified"
_FILES = re.compile(r"mcp-registration-([A-Za-z0-9_-]{1,96})\.(intent|commit)\Z", re.ASCII)
_INVENTORY_LIMIT = 10_000
_POLICY: Literal["foreground-transport-only"] = "foreground-transport-only"


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
