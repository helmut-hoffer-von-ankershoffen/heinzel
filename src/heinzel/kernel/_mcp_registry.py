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
    descriptor_identity,
    encode_record,
    installation_directories,
    physical_directory,
    read_record,
)

REGISTRATION_REFUSED = "The committed MCP registration or its installed host could not be verified"
_FILES = re.compile(r"mcp-registration-([A-Za-z0-9_-]{1,96})\.(intent|commit)\Z", re.ASCII)
_INVENTORY_LIMIT = 10_000
FOREGROUND_TRANSPORT_POLICY: Literal["foreground-transport-only"] = "foreground-transport-only"


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
    policy: Literal["foreground-transport-only"] = FOREGROUND_TRANSPORT_POLICY

    def data(self) -> bytes:
        """The canonical bytes covered by the exclusive commit marker."""
        return encode_record(self.model_dump(mode="json"))


def read_registration_intent(directory: int, name: str) -> McpRegistrationReceipt | None:
    """Read the canonical intent for this exact dormant registration if present.

    Raises:
        HeinzelError: If the stored intent cannot be verified.
        OSError: If the stored intent cannot be read.
        ValueError: If the stored intent does not validate.
    """
    data = read_record(directory, f"mcp-registration-{name}.intent")
    if data is None:
        return None
    receipt = McpRegistrationReceipt.model_validate_json(data)
    if receipt.id != name or receipt.data() != data:
        raise HeinzelError(REGISTRATION_REFUSED)
    return receipt


def registration_commit_bytes(receipt: McpRegistrationReceipt) -> bytes:
    """Bind the exclusive commit marker to the exact canonical intent bytes.

    Raises:
        HeinzelError: If the registration record exceeds its bound.
    """
    return encode_record({"schema": 1, "intent_sha256": hashlib.sha256(receipt.data()).hexdigest()})


def has_registration_commit(directory: int, name: str, receipt: McpRegistrationReceipt | None) -> bool:
    """Check whether the marker proves the exact dormant intent was committed.

    Raises:
        HeinzelError: If an existing marker does not match a verified intent.
        OSError: If the marker cannot be read.
    """
    data = read_record(directory, f"mcp-registration-{name}.commit")
    if data is None:
        return False
    if receipt is None or data != registration_commit_bytes(receipt):
        raise HeinzelError(REGISTRATION_REFUSED)
    return True


def registration_lock_identity(directory: int) -> tuple[int, int]:
    """Observe the retained SDK lifecycle lock identity for a registration receipt.

    Raises:
        HeinzelError: If the lock fails private-file checks.
        OSError: If the lock cannot be opened or inspected.
    """
    descriptor = CurrentUser.open_private(directory, "lifecycle.lock", os.O_RDONLY)
    try:
        return descriptor_identity(descriptor)
    finally:
        os.close(descriptor)


def verify_registration_scope(
    directory: int, receipt: McpRegistrationReceipt, runtime: InstalledMcpRuntime, *, require_sdk_directory: bool = True
) -> None:
    """Bind a dormant receipt to the live SDK, profile and maintenance lock.

    Raises:
        HeinzelError: If the receipt no longer matches its installation.
        OSError: If the profile or lock cannot be inspected.
    """
    with physical_directory(runtime.state, private=True) as profile:
        observed_profile = descriptor_identity(profile)
    if (
        receipt.uid != os.getuid()
        or receipt.prefix != str(runtime.prefix)
        or receipt.launcher != str(runtime.prefix / "bin" / "hz")
        or receipt.profile != str(runtime.state)
        or receipt.lock_identity != registration_lock_identity(directory)
        or receipt.directories[1] != observed_profile
        or (require_sdk_directory and receipt.directories != installation_directories(runtime))
    ):
        raise HeinzelError(REGISTRATION_REFUSED)


def mcp_registration_inventory(prefix: Path) -> tuple[McpRegistrationReceipt, ...]:
    """Verify every dormant registration under the SDK lock, including interrupted publications.

    Raises:
        HeinzelError: If the inventory contains uncertain, uncommitted or changed authority.
    """
    authority = host_maintenance_directory(prefix)
    runtime = InstalledMcpRuntime(prefix, authority / "mcp-profile")
    try:
        with physical_directory(authority, private=True) as directory, os.scandir(directory) as entries:
            names = [entry.name for entry in islice(entries, _INVENTORY_LIMIT + 1)]
            if len(names) > _INVENTORY_LIMIT:
                raise HeinzelError(REGISTRATION_REFUSED)
            ids: set[str] = set()
            for name in names:
                if name.startswith("mcp-registration-"):
                    match = _FILES.fullmatch(name)
                    if match is None:
                        raise HeinzelError(REGISTRATION_REFUSED)
                    ids.add(match[1])
            records: list[McpRegistrationReceipt] = []
            for id_ in sorted(ids):
                receipt = read_registration_intent(directory, id_)
                if receipt is None or not has_registration_commit(directory, id_, receipt):
                    msg = "An MCP registration requires explicit recovery before changing the SDK."
                    raise HeinzelError(msg)
                verify_registration_scope(directory, receipt, runtime, require_sdk_directory=False)
                records.append(receipt)
            return tuple(records)
    except FileNotFoundError:
        # A missing authority directory is an empty namespace. A missing entry inside it is uncertainty.
        if os.path.lexists(authority):
            raise HeinzelError(REGISTRATION_REFUSED) from None
        return ()
    except (OSError, ValueError):
        raise HeinzelError(REGISTRATION_REFUSED) from None


def native_mcp_scope() -> HostIntegrationScope:
    """Select the OS user definition namespace without consulting the caller's HOME or config.

    Raises:
        HeinzelError: If the OS does not identify the current user's home.
    """
    try:
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        raise HeinzelError(REGISTRATION_REFUSED) from None
    platform = str(sys.platform)
    if platform == "darwin":
        return HostIntegrationScope(home / "Library" / "LaunchAgents", MAC_LABEL_PREFIX, MAC_LABEL_PREFIX)
    if platform == "linux":
        return HostIntegrationScope(home / ".config" / "systemd" / "user", LINUX_UNIT_PREFIX, LINUX_UNIT_PREFIX)
    raise HeinzelError(REGISTRATION_REFUSED)


def require_native_absence(
    runtime: InstalledMcpRuntime, id_: str, manager: "HostServiceManager", scope: HostIntegrationScope
) -> None:
    """Require the verified user service manager to report no matching native definition.

    Raises:
        HeinzelError: If native absence cannot be verified.
    """
    plan = McpNativePlan.capture(runtime, id_)
    unit = scope.directory / (plan.name + ".plist" if plan.platform == "darwin" else plan.name)
    parent = scope.directory
    while not os.path.lexists(parent):
        parent = parent.parent
    with physical_directory(parent):
        if os.path.lexists(unit) or plan.observe(manager) != "absent":
            raise HeinzelError(REGISTRATION_REFUSED)


def verify_mcp_native_inventory(
    prefix: Path,
    records: tuple[McpRegistrationReceipt, ...],
    manager: "HostServiceManager",
    scope: HostIntegrationScope,
) -> None:
    """Refuse any loaded or on-disk unit claiming a dormant registration's exact native name."""
    for receipt in records:
        require_native_absence(InstalledMcpRuntime(prefix, Path(receipt.profile)), receipt.id, manager, scope)
