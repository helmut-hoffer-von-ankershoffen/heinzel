"""Prompt-free offline maintenance parsing before any built-in or external discovery."""

import argparse
import sys
from pathlib import Path

from heinzel.kernel import HeinzelError, OfflineUninstall, boot


def _show_recovery(command: str) -> None:
    sys.stderr.write(f"Offline recovery if interrupted: {command}\n")


def main(arguments: list[str]) -> int:
    """Parse help and invalid arguments first, then select only the trusted built-in maintenance port."""
    parser = argparse.ArgumentParser(
        prog="hz system uninstall",
        description=(
            "Uninstall the verified Heinzel tool offline, stopping its owned services "
            "and keeping your data, configuration, agent skills and notifier."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print the observed result as JSON")
    args = parser.parse_args(arguments)
    try:
        kernel = boot(builtin_only=True, cwd=Path.home())
        service = kernel.resolve_builtin_service(OfflineUninstall, "system")
        result = service.uninstall(on_recovery=_show_recovery)
    except (HeinzelError, OSError, ValueError) as error:
        sys.stderr.write(f"Heinzel was not fully uninstalled. {error}\n")
        return 1
    sys.stdout.write((result.model_dump_json() if args.json else result.message) + "\n")
    return 0
