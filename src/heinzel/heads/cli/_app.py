"""Commands for the managed native desktop app."""

import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from subprocess import SubprocessError
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from heinzel import HeinzelError, Settings, __version__
from heinzel.heads import _desktop_messages as say
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


def _print_location(output: Console, error: Console) -> None:
    """Print where the app really is, checked on disk, and why it is not in /Applications when it is not.

    Raises:
        typer.Exit: If the installed app cannot be found where the receipt says.
    """
    from heinzel.heads._desktop_installation import (  # noqa: PLC0415 - native install only
        SYSTEM_APPLICATIONS,
        desktop_location,
        system_applications_usable,
    )

    location = desktop_location()
    if location is None:
        error.print("The desktop app was installed but cannot be found. Run hz app doctor.")
        raise typer.Exit(1)
    output.print(f"Heinzel is installed at {location}.", markup=False)
    if location.parent == SYSTEM_APPLICATIONS:
        return
    if system_applications_usable():
        output.print(
            f"It could not be moved to {SYSTEM_APPLICATIONS} this time: Heinzelmännchen was running, another "
            f"{location.name} is already there, or your home folder is on another volume. Quit the app, move the "
            "other copy to the Trash, then run hz app install again: it moves the app for you.",
            markup=False,
        )
    else:
        output.print(
            f"It is in your own Applications folder because this user cannot write to {SYSTEM_APPLICATIONS} "
            "without an administrator password; Finder, Launchpad and Spotlight find it there too. To have it in "
            f"{SYSTEM_APPLICATIONS}, make this account an administrator and run hz app install again: it moves "
            "the app for you.",
            markup=False,
        )


def _print_notes(output: Console, notes: list[str]) -> None:
    """Say what the installer set aside or moved before it did its main work."""
    for note in notes:
        output.print(note, markup=False)


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


def _doctor(output: Console, error: Console, *, fix: bool, as_json: bool = False) -> None:
    """Print the installed app's macOS state, repairing it first when asked.

    Raises:
        typer.Exit: If the app is missing or macOS would refuse to open it.
    """
    from heinzel.heads._desktop_installation import doctor_desktop  # noqa: PLC0415 - native check only

    try:
        report, repairs = doctor_desktop(fix=fix)
    except (HeinzelError, OSError, SubprocessError) as exc:
        if as_json:
            output.print_json(data={"installed": None, "error": str(exc)})
        error.print(message("Desktop check failed.", str(exc)))
        raise typer.Exit(1) from exc
    if as_json:
        state = {"installed": False} if report is None else {"installed": True, **report.as_dict()}
        output.print_json(data={**state, "repairs": repairs})
    if report is None:
        error.print("The Heinzel desktop app is not installed. Run hz app install.")
        raise typer.Exit(1)
    if not as_json:
        for line in [*repairs, *report.lines()]:
            output.print(line, markup=False)
    if not report.opens:
        error.print("Run hz app doctor --fix to repair it.")
        raise typer.Exit(1)


_DETAILS_HELP = "Also print the installer's full diagnosis (paths and internal state) when something fails."


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _fail(error: Console, exc: Exception, fallback: str, *, details: bool) -> NoReturn:
    """Print a failure as two plain lines, the diagnosis only with --details or HZ_DEBUG=1, and exit 1.

    Raises:
        typer.Exit: Always, with 1.
    """
    problem = getattr(exc, "problem", None)
    if problem is None:
        error.print(message(fallback, str(exc)))
    else:
        for line in problem.lines():
            error.print(line, markup=False)
        if details or os.environ.get("HZ_DEBUG"):
            error.print(str(exc), markup=False)
    raise typer.Exit(1) from exc


def _confirm_forget(error: Console, exc: HeinzelError) -> bool:
    """In a terminal, say what was found and ask before forgetting the earlier install; elsewhere, do not ask.

    Raises:
        typer.Exit: If the person answers no.
    """
    problem = getattr(exc, "problem", None)
    if problem is None or not _interactive():
        return False
    error.print(problem.happened, markup=False)
    if not typer.confirm(say.FORGET_PROMPT, default=True):
        raise typer.Exit(1)
    return True


def _install_app(  # noqa: PLR0913 - the options of hz app install, shared with hz app open
    kernel: Kernel,
    output: Console,
    error: Console,
    *,
    keep_quarantine: bool = False,
    replace: bool = False,
    yes: bool = False,
    details: bool = False,
) -> None:
    """Install or refresh the app, asking once in a terminal before forgetting an install whose app is gone.

    Raises:
        typer.Exit: If the app cannot be installed now.
    """
    from heinzel.heads._desktop_installation import (  # noqa: PLC0415 - native install only
        LeftoverInstallError,
        install_desktop,
    )

    notes: list[str] = []

    def run(*, forget: bool) -> str:
        return install_desktop(
            __version__,
            desktop_executable(kernel),
            keep_quarantine=keep_quarantine,
            replace=replace,
            forget=forget,
            notes=notes,
        )

    try:
        try:
            state = run(forget=yes)
        except LeftoverInstallError as exc:
            if not _confirm_forget(error, exc):
                _print_notes(output, notes)
                _fail(error, exc, "Desktop installation failed.", details=details)
            state = run(forget=True)
    except (HeinzelError, OSError, SubprocessError) as exc:
        _print_notes(output, notes)
        _fail(error, exc, "Desktop installation failed.", details=details)
    _print_notes(output, notes)
    if state == "deferred":
        for line in say.app_running(_where_installed()).lines():
            error.print(line, markup=False)
        raise typer.Exit(1)
    _print_location(output, error)
    if state != "current":
        output.print(_trust_note(keep_quarantine=keep_quarantine))


def _where_installed() -> Path:
    from heinzel.heads._desktop_installation import (  # noqa: PLC0415 - native install only
        SYSTEM_APPLICATIONS,
        desktop_location,
        system_applications_usable,
    )

    location = desktop_location()
    if location is not None:
        return location.parent
    return SYSTEM_APPLICATIONS if system_applications_usable() else Path.home() / "Applications"


def _open_app(kernel: Kernel, output: Console, error: Console, *, details: bool = False) -> Path:
    """Open the app hz manages, installing it first when it is not there yet; never launch a broken copy.

    Returns:
        The app that was opened.

    Raises:
        typer.Exit: If it cannot be installed, would not open, or `open` fails.
    """
    from heinzel.heads._desktop_installation import desktop_location  # noqa: PLC0415 - native only
    from heinzel.heads._desktop_seal import inspect_bundle  # noqa: PLC0415 - native only

    location = desktop_location()
    if location is None:
        output.print("Not installed yet, installing…")
        _install_app(kernel, output, error, details=details)
        location = desktop_location()
        if location is None:
            for line in say.Problem(f"{say.APP} was installed but cannot be found.", "Run hz app doctor.").lines():
                error.print(line, markup=False)
            raise typer.Exit(1)
    report = inspect_bundle(location)
    if not report.opens:
        for line in say.would_not_open(location).lines():
            error.print(line, markup=False)
        raise typer.Exit(1)
    if report.quarantine != "none":
        output.print(say.asks_once(), markup=False)
    result = subprocess.run(  # noqa: S603 - fixed system executable and the receipt-owned app path
        ["/usr/bin/open", str(location)], check=False, capture_output=True, text=True, timeout=30
    )
    if result.returncode:
        for line in say.open_failed(location).lines():
            error.print(line, markup=False)
        raise typer.Exit(1)
    output.print(f"Opened {location}", markup=False)
    return location


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
        replace: Annotated[
            bool,
            typer.Option(
                "--replace",
                help="Move a Heinzelmaennchen.app that hz app install did not install to the Trash (nothing is "
                "deleted), then install a verified copy.",
            ),
        ] = False,
        yes: Annotated[
            bool,
            typer.Option("--yes", "-y", help="Forget an earlier install whose app is gone, without asking."),
        ] = False,
        details: Annotated[bool, typer.Option("--details", help=_DETAILS_HELP)] = False,
    ) -> None:
        """Install the desktop app for this version of Heinzel.

        The app is unsigned and not notarized by Apple. Heinzel checks the download's sha256 and build
        provenance, makes sure its ad-hoc code seal is valid, and by default does not quarantine it.

        Raises:
            typer.Exit: If the app cannot be installed now.
        """
        _install_app(kernel, output, error, keep_quarantine=keep_quarantine, replace=replace, yes=yes, details=details)

    @app.command("open")
    def open_app(
        details: Annotated[bool, typer.Option("--details", help=_DETAILS_HELP)] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Print the opened app's path as JSON on stdout.")] = False,
    ) -> None:
        """Open the desktop app, installing it first when it is not there yet.

        Raises:
            typer.Exit: If the app cannot be installed or would not open.
        """
        location = _open_app(kernel, error if as_json else output, error, details=details)
        if as_json:
            output.print_json(data={"opened": str(location)})

    @app.command("doctor")
    def doctor(
        fix: Annotated[
            bool,
            typer.Option("--fix", help="Reseal a broken app ad hoc and remove the quarantine mark Heinzel added."),
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Print the state as JSON on stdout.")] = False,
    ) -> None:
        """Check whether macOS will open the installed desktop app, without launching it.

        Raises:
            typer.Exit: If the app is missing or macOS would refuse to open it.
        """
        _doctor(output, error, fix=fix, as_json=as_json)

    @app.command("uninstall")
    def uninstall(details: Annotated[bool, typer.Option("--details", help=_DETAILS_HELP)] = False) -> None:
        """Remove the managed desktop app and keep your Heinzel profile.

        Raises:
            typer.Exit: If the app cannot be removed safely.
        """
        from heinzel.heads._desktop_installation import uninstall_desktop  # noqa: PLC0415 - native removal only

        notes: list[str] = []
        try:
            removed = uninstall_desktop(notes=notes)
        except (HeinzelError, OSError, SubprocessError) as exc:
            _print_notes(output, notes)
            _fail(error, exc, "Desktop uninstall failed.", details=details)
        _print_notes(output, notes)
        output.print(
            f"Heinzel desktop app removed from {removed}." if removed else "The Heinzel desktop app is not installed.",
            markup=False,
        )

    @app.command("refresh", hidden=True)
    def refresh() -> None:
        """Refresh the managed app after an orderly native exit."""
        try:
            _refresh_after_exit(kernel)
        except (HeinzelError, OSError, SubprocessError) as exc:
            error.print(message("Desktop update failed.", str(exc)))

    return app
