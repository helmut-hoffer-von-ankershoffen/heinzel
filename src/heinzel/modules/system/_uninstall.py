"""Offline removal of one verified uv installation, with owned service cleanup and retained user work."""

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from heinzel import Config, HeinzelError, Kernel, Progress, Settings
from heinzel.kernel import Installation, OfflineUninstall, UninstallResult
from heinzel.kernel._installation_mutations import installation_mutation_pending
from heinzel.kernel._offline_uv import VerifiedUV, stage
from heinzel.kernel._uninstall_journal import RemovalJournal

if TYPE_CHECKING:
    from heinzel.modules.system.daemon._service import DaemonRemoval, DaemonService
    from heinzel.modules.ui._service import WebRemoval, WebService


def uninstall_installation(
    installation: Installation | None = None,
    *,
    daemon: "DaemonService | None" = None,
    web: "WebService | None" = None,
    on_recovery: Callable[[str], None] | None = None,
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
    with (
        installation.tool_transaction(),
        web.removal_guard() as remove_web,
        daemon.offline_removal_guard(binary) as daemon_removal,
    ):
        if not installation.startup_state_matches() or installation.startup_fingerprint() != before:
            msg = "The Heinzel installation changed during inspection; nothing was removed."
            raise HeinzelError(msg)
        journal = RemovalJournal(
            executable,
            prefix=installation.prefix,
            binary=binary,
            state=settings.state_dir,
            captured=(
                (*remove_web.files, *daemon_removal.files),
                installation.tool_entrypoints(),
                {**remove_web.manager_units, **daemon_removal.manager_units},
            ),
        )
        if not installation.startup_state_matches() or installation.startup_fingerprint() != before:
            msg = "The Heinzel installation changed while preparing recovery; no service was stopped."
            raise HeinzelError(msg)
        _apply_removal(installation, before, executable, (remove_web, daemon_removal, journal), on_recovery)

    return UninstallResult(daemon_removed=bool(daemon_removal.files), web_removed=bool(remove_web.files))


def _apply_removal(
    installation: Installation,
    before: str,
    executable: VerifiedUV,
    plan: tuple["WebRemoval", "DaemonRemoval", RemovalJournal],
    on_recovery: Callable[[str], None] | None,
) -> None:
    remove_web, daemon_removal, journal = plan
    if on_recovery is not None:
        on_recovery(journal.command)
    try:
        journal.begin()
        remove_web.stop()
        daemon_removal.stop(journal.token)
        journal.phase("stopped")
        journal.phase("removing")
        installation.uninstall_offline(expected=before, executable=executable)
        journal.phase("removed")
        remove_web.finish()
        daemon_removal.finish()
        journal.complete()
    except (OSError, ValueError, HeinzelError) as error:
        msg = f"Offline removal needs inspection. User work was kept. Recovery without the SDK: {journal.command}"
        raise HeinzelError(msg) from error


class SystemOfflineUninstall(OfflineUninstall):
    """Built-in maintenance implementation; construction performs no account or provider work."""

    def uninstall(self, *, on_recovery: Callable[[str], None] | None = None) -> UninstallResult:
        """Apply the existing recordless coordinator and every installation/service/recovery check."""
        return uninstall_installation(on_recovery=on_recovery)
