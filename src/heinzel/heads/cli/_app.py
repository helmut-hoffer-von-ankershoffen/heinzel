"""Commands for the managed native desktop app."""

import sys
import time
from pathlib import Path
from subprocess import SubprocessError

import typer
from rich.console import Console

from heinzel import HeinzelError, Settings, __version__
from heinzel.heads.cli._render import message
from heinzel.kernel import KERNEL_SECTION, Installation, Kernel


def desktop_executable(kernel: Kernel) -> Path:
    """Find the receipt-owned CLI entry point for the installed desktop app.

    Raises:
        HeinzelError: If this installation does not own a verified hz command.
    """
    for entry in kernel.resolve(Installation).tool_entrypoints():
        if entry.name == "hz":
            return entry
    msg = "Install Heinzel with uv before installing its desktop app."
    raise HeinzelError(msg)


def _eligible_for_refresh(kernel: Kernel) -> bool:
    settings = kernel.config.reread().settings(Settings, KERNEL_SECTION)
    if not settings.update_check or not settings.auto_update or settings.update_channel != "stable":
        return False
    pin = kernel.resolve(Installation).core_pin()
    return pin is not None and (
        (pin.kind == "release" and pin.git is not None)
        or (pin.kind in {"index", "default"} and pin.git is None and not (pin.ref or "").startswith("=="))
    )


def _refresh_after_exit(kernel: Kernel) -> None:
    """Refresh an owned bundle when the outgoing native process is no longer using it."""
    if sys.platform != "darwin":
        return
    from heinzel.heads._desktop_installation import install_desktop  # noqa: PLC0415 - packaged app exit only

    deadline = time.monotonic() + 12
    while _eligible_for_refresh(kernel):
        state = install_desktop(__version__, desktop_executable(kernel), update_only=True)
        if state != "deferred" or time.monotonic() >= deadline:
            return
        time.sleep(0.25)


def desktop_group(kernel: Kernel, output: Console, error: Console) -> typer.Typer:
    """Build the app command group without loading installer code during CLI discovery."""
    app = typer.Typer(help="Manage the native Heinzel desktop app.", no_args_is_help=True)

    @app.command("install")
    def install() -> None:
        """Install the desktop app for this version of Heinzel.

        Raises:
            typer.Exit: If the app cannot be installed now.
        """
        from heinzel.heads._desktop_installation import install_desktop  # noqa: PLC0415 - native install only

        try:
            state = install_desktop(__version__, desktop_executable(kernel))
        except (HeinzelError, OSError, SubprocessError) as exc:
            error.print(message("Desktop installation failed.", str(exc)))
            raise typer.Exit(1) from exc
        if state == "deferred":
            error.print("Quit the Heinzel desktop app, then run hz app install again.")
            raise typer.Exit(1)
        output.print("Heinzel is in your Applications folder.")

    @app.command("uninstall")
    def uninstall() -> None:
        """Remove the managed desktop app and keep your Heinzel profile.

        Raises:
            typer.Exit: If the app cannot be removed safely.
        """
        from heinzel.heads._desktop_installation import uninstall_desktop  # noqa: PLC0415 - native removal only

        try:
            removed = uninstall_desktop()
        except (HeinzelError, OSError, SubprocessError) as exc:
            error.print(message("Desktop uninstall failed.", str(exc)))
            raise typer.Exit(1) from exc
        output.print("Heinzel desktop app removed." if removed else "The Heinzel desktop app is not installed.")

    @app.command("refresh", hidden=True)
    def refresh() -> None:
        """Refresh the managed app after an orderly native exit."""
        try:
            _refresh_after_exit(kernel)
        except (HeinzelError, OSError, SubprocessError) as exc:
            error.print(message("Desktop update failed.", str(exc)))

    return app
