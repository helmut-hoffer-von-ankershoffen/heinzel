"""Commands for the managed native desktop app."""

import platform
import time
from pathlib import Path
from subprocess import SubprocessError
from typing import Annotated

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
    if platform.system() != "Darwin":
        return
    from heinzel.heads._desktop_installation import install_desktop  # noqa: PLC0415 - packaged app exit only

    deadline = time.monotonic() + 12
    while _eligible_for_refresh(kernel):
        state = install_desktop(__version__, desktop_executable(kernel), update_only=True)
        if state != "deferred" or time.monotonic() >= deadline:
            return
        time.sleep(0.25)


def _trust_note(*, keep_quarantine: bool) -> str:
    """Say honestly what was checked and how macOS will treat the first open."""
    opening = (
        "; it is marked as downloaded, so open it once via System Settings, Privacy & Security, Open Anyway."
        if keep_quarantine
        else ", so it opens with a double-click."
    )
    return (
        "It is an unsigned app from this project, not notarized by Apple. Heinzel verified the download's "
        f"sha256, its build provenance and its code seal{opening}"
    )


def _doctor(output: Console, error: Console, *, fix: bool) -> None:
    """Print the installed app's macOS state, repairing it first when asked.

    Raises:
        typer.Exit: If the app is missing or macOS would refuse to open it.
    """
    from heinzel.heads._desktop_installation import doctor_desktop  # noqa: PLC0415 - native check only

    try:
        report, repairs = doctor_desktop(fix=fix)
    except (HeinzelError, OSError, SubprocessError) as exc:
        error.print(message("Desktop check failed.", str(exc)))
        raise typer.Exit(1) from exc
    if report is None:
        error.print("The Heinzel desktop app is not installed. Run hz app install.")
        raise typer.Exit(1)
    for line in [*repairs, *report.lines()]:
        output.print(line, markup=False)
    if not report.opens:
        error.print("Run hz app doctor --fix to repair it.")
        raise typer.Exit(1)


def desktop_group(kernel: Kernel, output: Console, error: Console) -> typer.Typer:
    """Build the app command group without loading installer code during CLI discovery."""
    app = typer.Typer(help="Manage the native Heinzel desktop app.", no_args_is_help=True)

    @app.command("install")
    def install(
        keep_quarantine: Annotated[
            bool,
            typer.Option(
                "--keep-quarantine",
                help="Mark the app as downloaded so macOS asks once before the first open (Open Anyway in "
                "System Settings, Privacy & Security).",
            ),
        ] = False,
    ) -> None:
        """Install the desktop app for this version of Heinzel.

        The app is unsigned and not notarized by Apple. Heinzel checks the download's sha256 and build
        provenance, makes sure its ad-hoc code seal is valid, and by default does not quarantine it.

        Raises:
            typer.Exit: If the app cannot be installed now.
        """
        from heinzel.heads._desktop_installation import install_desktop  # noqa: PLC0415 - native install only

        try:
            state = install_desktop(__version__, desktop_executable(kernel), keep_quarantine=keep_quarantine)
        except (HeinzelError, OSError, SubprocessError) as exc:
            error.print(message("Desktop installation failed.", str(exc)))
            raise typer.Exit(1) from exc
        if state == "deferred":
            error.print("Quit the Heinzel desktop app, then run hz app install again.")
            raise typer.Exit(1)
        output.print("Heinzel is in your Applications folder.")
        if state != "current":
            output.print(_trust_note(keep_quarantine=keep_quarantine))

    @app.command("doctor")
    def doctor(
        fix: Annotated[
            bool,
            typer.Option("--fix", help="Reseal a broken app ad hoc and remove the quarantine mark Heinzel added."),
        ] = False,
    ) -> None:
        """Check whether macOS will open the installed desktop app, without launching it.

        Raises:
            typer.Exit: If the app is missing or macOS would refuse to open it.
        """
        _doctor(output, error, fix=fix)

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
