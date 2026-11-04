"""Explicit candidate-only legacy daemon retirement, independent of the installed old CLI."""

import argparse
import sys
from pathlib import Path

from heinzel import Config, HeinzelError, Progress, Settings
from heinzel.heads import install
from heinzel.heads._tool_install import resolve_target
from heinzel.kernel import (
    DaemonLegacyRecovery,
    DaemonRecoveryPlan,
    DaemonRecoveryResult,
    DaemonRecoverySelection,
    Installation,
    boot,
    private_read,
)


def recovery() -> DaemonLegacyRecovery:
    """Bind the independently verified persistent standard target before resolving recovery.

    Raises:
        HeinzelError: If the current standard target or continuity is unknown.
    """
    uv = install.uv_executable()
    target = resolve_target(uv, create=False)
    config = Config.load(Path.home())
    settings = config.settings(Settings, "hz")
    receipt = settings.data_dir / "standard-app.json"
    content = private_read(receipt).encode()
    profile = install.AppProfile.model_validate_json(content)
    installation = Installation(Progress(), prefix=target.prefix, uv=uv)
    installation.verify_tool_destination(target.store, target.binary)
    if profile.format != install.PROFILE_FORMAT or not installation.startup_state_matches():
        msg = "The persistent standard app cannot be verified; no legacy recovery was authorized."
        raise HeinzelError(msg)
    kernel = boot(settings.model_copy(update={"plugins_enabled": False}))
    kernel.bind(Installation, installation)
    kernel.bind(DaemonRecoverySelection, DaemonRecoverySelection(receipt, content))
    return kernel.resolve(DaemonLegacyRecovery)


def main(argv: list[str] | None = None) -> int:
    """Parse only explicit recovery commands; help and malformed input never resolve authority."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="Inspect exact legacy integration without changing it.")
    retire = commands.add_parser("retire", help="Retire only the exact digest shown by plan; preserve data.")
    retire.add_argument("--confirm", required=True, metavar="PLAN_SHA256")
    restore = commands.add_parser("restore", help="Plan or confirm restoration of an interrupted retirement.")
    restore.add_argument("transaction", metavar="RETIREMENT_SHA256")
    restore.add_argument("--confirm", metavar="RESTORE_PLAN_SHA256")
    args = parser.parse_args(argv)
    try:
        service = recovery()
        result: DaemonRecoveryPlan | DaemonRecoveryResult
        if args.command == "plan":
            result = service.plan()
        elif args.command == "retire":
            result = service.retire(args.confirm)
        elif args.confirm is None:
            result = service.restore_plan(args.transaction)
        else:
            result = service.restore(args.transaction, args.confirm)
        sys.stdout.write(result.model_dump_json(indent=2) + "\n")
    except (HeinzelError, OSError, ValueError, TypeError, KeyError):
        sys.stderr.write(
            "Legacy daemon recovery refused: authority is unknown, changed or requires explicit recovery.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
