"""Offline removal of one verified uv installation, with owned service cleanup and retained user work."""

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from heinzel import Config, HeinzelError, Kernel, Progress, Settings
from heinzel.kernel import Installation
from heinzel.kernel._installation_mutations import installation_mutation_pending
from heinzel.kernel._tool_uninstall import uv_identity

if TYPE_CHECKING:
    from heinzel.modules.system.daemon._service import DaemonService
    from heinzel.modules.ui._service import WebService


class UninstallResult(BaseModel):
    """Observed removal, never a promise that user data or other applications were deleted."""

    removed: Literal[True] = True
    data_retained: Literal[True] = True
    daemon_removed: bool
    web_removed: bool
    message: str = "Heinzel was uninstalled. Your data and configuration were kept."


def uninstall_installation(
    installation: Installation | None = None,
    *,
    daemon: "DaemonService | None" = None,
    web: "WebService | None" = None,
) -> UninstallResult:
    """Stop only verified owned services and remove the current standard tool, retaining all user work.

    No kernel boot, plugin discovery, update check, bootstrap or package resolution occurs. Unsupported SDK,
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
    uv_identity(installation.uv)
    if not installation.startup_state_matches():
        msg = (
            "This is not a verified standard Heinzel installation. "
            "Inspect its local installer and services; nothing was removed."
        )
        raise HeinzelError(msg)
    before = installation.startup_fingerprint()
    config = Config.load(Path.home())
    settings = config.settings(Settings, "hz")
    kernel = Kernel(settings, config=config)  # No records and no discovery: configuration and Progress only.
    kernel.bind(Installation, installation)
    daemon = daemon or DaemonService.for_user(kernel)
    web = web or WebService(
        settings.state_dir,
        Path.home(),
        binary / "hz",
        prefix=installation.prefix,
        uv=Path(installation.uv).absolute(),
        profile=user_profile(config, settings, Path.home(), installation.prefix, binary / "hz"),
        unsupported=installation_overrides(config),
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
        daemon.offline_removal_guard(binary) as removed_daemon,
    ):
        if not installation.startup_state_matches() or installation.startup_fingerprint() != before:
            msg = "The Heinzel installation changed during inspection; nothing was removed."
            raise HeinzelError(msg)
        removed_web = remove_web().changed
        installation.uninstall_offline(expected=before)
    return UninstallResult(daemon_removed=removed_daemon, web_removed=removed_web)


def main(arguments: list[str]) -> int:
    """Parse this recovery command before CLI discovery; help itself needs no installed tools or services."""
    parser = argparse.ArgumentParser(
        prog="hz system uninstall",
        description=(
            "Uninstall the verified Heinzel tool offline, stopping its owned services "
            "and keeping your data and configuration."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print the observed result as JSON")
    args = parser.parse_args(arguments)
    try:
        result = uninstall_installation()
    except (HeinzelError, OSError, ValueError) as error:
        sys.stderr.write(f"Heinzel was not fully uninstalled. {error}\n")
        return 1
    sys.stdout.write((result.model_dump_json() if args.json else result.message) + "\n")
    return 0
