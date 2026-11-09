"""Offline removal of one verified uv installation, with owned service cleanup and retained user work."""

import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from heinzel import Config, HeinzelError, Kernel, Progress, Settings
from heinzel.heads.mcp._ipc_registration import (
    McpRegistrationReceipt,
    mcp_registration_inventory,
    native_mcp_scope,
    verify_mcp_native_inventory,
)
from heinzel.kernel import (
    HostIntegrationScope,
    HostMaintenanceIntegration,
    HostServiceManager,
    Installation,
    MaintenanceLocks,
    OfflineUninstall,
    ServiceLet,
    UninstallResult,
    boot,
    host_maintenance_gate,
    host_maintenance_lock,
)
from heinzel.kernel._host_maintenance import HostIntegrationRemoval
from heinzel.kernel._installation_mutations import installation_mutation_pending
from heinzel.kernel._offline_uv import VerifiedUV, stage
from heinzel.kernel._uninstall_journal import RemovalCapture, RemovalJournal
from heinzel.modules.system.daemon._maintenance import DaemonManagerPort

if TYPE_CHECKING:
    from heinzel.modules.system.daemon._service import DaemonRemoval, DaemonService
    from heinzel.modules.ui._service import WebRemoval, WebService


def uninstall_installation(
    installation: Installation | None = None,
    *,
    daemon: "DaemonService | None" = None,
    web: "WebService | None" = None,
    on_recovery: Callable[[str], None] | None = None,
    maintenance: Kernel | None = None,
) -> UninstallResult:
    """Stop only verified owned services and remove the current standard tool, retaining all user work.

    This coordinator performs no kernel boot, discovery, update check, bootstrap or package resolution. Unsupported SDK,
    desktop-runtime and unmarked installations refuse locally. Failures after mutation retain a durable existing
    installation gate and never try a network rollback. The app may remain stopped for explicit local recovery.

    Raises:
        HeinzelError: If identity, service ownership, quiescence or removal cannot be established.
        OSError: If local authority cannot be read or written safely.
    """
    from heinzel.modules.system.daemon._service import DaemonService  # noqa: PLC0415 - only explicit uninstall
    from heinzel.modules.ui._profile import installation_overrides, user_profile  # noqa: PLC0415 - owned user scope
    from heinzel.modules.ui._service import WebService  # noqa: PLC0415 - cleanup does not require a preview flag

    installation = installation or Installation(Progress())
    store, binary = installation.tool_destination()
    if not installation.startup_state_matches():
        msg = (
            "This is not a verified standard Heinzel installation. "
            "Inspect its local installer and services; nothing was removed."
        )
        raise HeinzelError(msg)
    before, config = installation.startup_fingerprint(), Config.load(Path.home())
    settings = config.settings(Settings, "hz")
    if any(
        path.resolve().is_relative_to(installation.prefix)
        for path in (
            settings.data_dir,
            settings.state_dir,
            settings.cache_dir,
            settings.log_dir,
        )
    ):
        msg = "User work is inside the tool environment; move it with your local installer before uninstalling."
        raise HeinzelError(msg)
    if any(file.path.resolve().is_relative_to(installation.prefix) for file in config.files):
        msg = "Configuration is inside the tool environment; move it with your local installer before uninstalling."
        raise HeinzelError(msg)
    executable = stage(installation.prefix, settings.state_dir / "uninstall" / secrets.token_hex(16))
    kernel = Kernel(settings, config=config)  # No records and no discovery: configuration and Progress only.
    kernel.bind(Installation, installation)
    daemon = daemon or DaemonService.for_user(kernel)
    web = web or WebService(
        settings.state_dir,
        Path.home(),
        binary / "hz",
        prefix=installation.prefix,
        uv=WebService.removal_uv_hint(settings.state_dir),
        profile=user_profile(config, settings, Path.home(), installation.prefix, binary / "hz"),
        unsupported=installation_overrides(config),
        runner=lambda command: daemon.owned_manager_command(list(command)),
        which=lambda name: "/bin/launchctl" if name == "launchctl" else "/usr/bin/systemctl",
    )
    if daemon.state_dir.parent != web.directory.parent or installation.prefix != store / "heinzel":
        msg = "The service profile does not match this Heinzel installation; nothing was removed."
        raise HeinzelError(msg)
    if installation_mutation_pending(installation.prefix, daemon.state_dir.parent):
        msg = "An installation change requires local recovery before uninstalling Heinzel."
        raise HeinzelError(msg)
    context = _RemovalContext(
        installation,
        before,
        executable,
        binary,
        settings,
        daemon,
        web,
        maintenance or boot(builtin_only=True, cwd=Path.home(), maintenance=True),
    )
    return _compose_removal(context, on_recovery)


MAX_INTEGRATIONS = 512


@dataclass
class _RemovalContext:
    installation: Installation
    before: str
    executable: VerifiedUV
    binary: Path
    settings: Settings
    daemon: "DaemonService"
    web: "WebService"
    maintenance: Kernel


@dataclass(frozen=True, slots=True)
class _McpRemovalInventory:
    prefix: Path
    receipts: tuple[McpRegistrationReceipt, ...]
    scopes: tuple[HostIntegrationScope, ...]

    @classmethod
    def capture(cls, prefix: Path) -> "_McpRemovalInventory":
        receipts = mcp_registration_inventory(prefix)
        return cls(prefix, receipts, (native_mcp_scope(),) if receipts else ())

    def verify(self, manager: HostServiceManager) -> None:
        if mcp_registration_inventory(self.prefix) != self.receipts:
            msg = "The MCP registration inventory changed before removal."
            raise HeinzelError(msg)
        for scope in self.scopes:
            verify_mcp_native_inventory(self.prefix, self.receipts, manager, scope)


def _contributions(kernel: Kernel) -> list[HostMaintenanceIntegration]:
    return [
        kernel.resolve_builtin_service(service.provides, record.qualname)
        for record, service in kernel.lets(ServiceLet)
        if issubclass(service.provides, HostMaintenanceIntegration)
    ]


def _compose_removal(context: _RemovalContext, on_recovery: Callable[[str], None] | None) -> UninstallResult:
    installation, before, executable = context.installation, context.before, context.executable
    binary, settings, daemon, web = context.binary, context.settings, context.daemon, context.web
    maintenance = context.maintenance
    contributions = _contributions(maintenance)
    manager = DaemonManagerPort(daemon)
    inventory = [(owner, owner.inventory(installation.prefix, binary, manager)) for owner in contributions]
    integrations = tuple(item for _, items in inventory for item in items)
    mcp = _McpRemovalInventory.capture(installation.prefix)
    scopes = (*tuple(scope for owner in contributions for scope in owner.discovery_scopes()), *mcp.scopes)
    if len(integrations) > MAX_INTEGRATIONS or len({item.name for item in integrations}) != len(integrations):
        msg = "The host integration inventory is ambiguous or exceeds its budget."
        raise HeinzelError(msg)
    lock_paths = (
        host_maintenance_lock(installation.prefix),
        web.directory / "lifecycle.lock",
        daemon.state_dir / "lifecycle.lock",
        daemon.state_dir / "tick.lock",
        *(path for item in integrations for path in item.locks),
    )
    gate = host_maintenance_gate(installation.prefix)
    with installation.tool_transaction(), MaintenanceLocks(lock_paths) as locks:
        if any(owner.inventory(installation.prefix, binary, manager) != items for owner, items in inventory):
            msg = "The host integration inventory changed before removal; no service was stopped."
            raise HeinzelError(msg)
        mcp.verify(manager)
        plans = tuple(owner.guard(item, manager, locks) for owner, items in inventory for item in items)
        with (
            web.removal_guard(locks=locks) as remove_web,
            daemon.offline_removal_guard(
                binary, locks=locks, gate=gate, compatibility_gates=remove_web.compatibility_gates
            ) as daemon_removal,
        ):
            if not installation.startup_state_matches() or installation.startup_fingerprint() != before:
                msg = "The Heinzel installation changed during inspection; nothing was removed."
                raise HeinzelError(msg)
            manager_units = {**remove_web.manager_units, **daemon_removal.manager_units}
            if set(manager_units) & {item.name for item in integrations}:
                msg = "Host integration manager identities collide; nothing was stopped."
                raise HeinzelError(msg)
            files = (*remove_web.files, *daemon_removal.files, *(file for item in integrations for file in item.files))
            if len({file.path for file in files}) != len(files):
                msg = "Host integration file identities collide; nothing was stopped."
                raise HeinzelError(msg)
            if sys.platform == "linux":
                manager_units.update({item.name: item.manager_facts() for item in integrations})
            journal = RemovalJournal(
                executable,
                prefix=installation.prefix,
                binary=binary,
                state=settings.state_dir,
                captured=RemovalCapture(
                    files,
                    installation.tool_entrypoints(),
                    manager_units,
                    gate=gate,
                    locks=lock_paths,
                    labels=tuple(item.name for item in integrations),
                    integrations=integrations,
                    scopes=scopes,
                    compatibility_gates=remove_web.compatibility_gates,
                ),
            )
            if not installation.startup_state_matches() or installation.startup_fingerprint() != before:
                msg = "The Heinzel installation changed while preparing recovery; no service was stopped."
                raise HeinzelError(msg)

            def revalidate() -> None:
                if any(owner.inventory(installation.prefix, binary, manager) != items for owner, items in inventory):
                    msg = "The host integration inventory changed before native removal."
                    raise HeinzelError(msg)
                mcp.verify(manager)

            _apply_removal(
                installation, before, executable, (remove_web, daemon_removal, journal, plans, revalidate), on_recovery
            )

    return UninstallResult(daemon_removed=bool(daemon_removal.files), web_removed=bool(remove_web.files))


def _apply_removal(
    installation: Installation,
    before: str,
    executable: VerifiedUV,
    plan: tuple["WebRemoval", "DaemonRemoval", RemovalJournal, tuple[HostIntegrationRemoval, ...], Callable[[], None]],
    on_recovery: Callable[[str], None] | None,
) -> None:
    remove_web, daemon_removal, journal, integrations, revalidate = plan
    if on_recovery is not None:
        on_recovery(journal.command)
    try:
        journal.begin()
        for integration in integrations:
            journal.require_gate()
            integration.stop()
        journal.require_gate()
        remove_web.stop(journal.token)
        journal.require_gate()
        daemon_removal.stop(journal.token)
        revalidate()
        for integration in integrations:
            integration.quiescent()
        journal.require_gate()
        journal.phase("stopped")
        journal.phase("removing")
        journal.require_gate()
        installation.uninstall_offline(expected=before, executable=executable)
        journal.phase("removed")
        for integration in integrations:
            journal.require_gate()
            integration.finish()
        journal.require_gate()
        remove_web.finish()
        journal.require_gate()
        daemon_removal.finish()
        journal.complete()
    except (OSError, ValueError, HeinzelError) as error:
        msg = f"Offline removal needs inspection. User work was kept. Recovery without the SDK: {journal.command}"
        raise HeinzelError(msg) from error


class SystemOfflineUninstall(OfflineUninstall):
    """Built-in maintenance implementation; construction performs no account or provider work."""

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel

    def uninstall(self, *, on_recovery: Callable[[str], None] | None = None) -> UninstallResult:
        """Apply the existing recordless coordinator and every installation/service/recovery check."""
        return uninstall_installation(on_recovery=on_recovery, maintenance=self.kernel)
