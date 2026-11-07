"""Commands for the managed native desktop app."""

import platform
import time
from pathlib import Path
from subprocess import SubprocessError
from typing import Annotated

import typer
from rich.console import Console

from heinzel import HeinzelError, Settings, __version__
from heinzel.heads import _desktop_copy as copy
from heinzel.heads.cli._progress import StepReporter
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


def _located(error: Console) -> Path:
    """Where the app really is after an install, checked on disk.

    Raises:
        typer.Exit: If the installed app cannot be found where the receipt says.
    """
    from heinzel.heads._desktop_installation import desktop_location  # noqa: PLC0415 - native install only

    location = desktop_location()
    if location is None:
        error.print(copy.LOST)
        raise typer.Exit(1)
    return location


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
        error.print(message(copy.CHECK_FAILED, str(exc)))
        raise typer.Exit(1) from exc
    if as_json:
        state = {"installed": False} if report is None else {"installed": True, **report.as_dict()}
        output.print_json(data={**state, "repairs": repairs})
    if report is None:
        error.print(copy.NOT_INSTALLED_FOR_DOCTOR)
        raise typer.Exit(1)
    if not as_json:
        from heinzel.heads._desktop_installation import SYSTEM_APPLICATIONS  # noqa: PLC0415 - native check only

        seal_lines = report.lines()[1:]  # its "App: ..." line gives way to the copy module's, which names the folder
        for line in [*repairs, copy.doctor_app(report.bundle, SYSTEM_APPLICATIONS), *seal_lines]:
            output.print(line, markup=False)
    if not report.opens:
        error.print(copy.DOCTOR_FIX)
        raise typer.Exit(1)


def _plan(error: Console, *, keep_quarantine: bool) -> tuple[StepReporter, Path]:
    """The install's rows and the folder the installer will pick, named in the first line with the reason.

    The installer picks the system folder for an administrator, else the one the app is in, else the user's own.
    """
    from heinzel.heads._desktop_installation import (  # noqa: PLC0415 - native install only
        SYSTEM_APPLICATIONS,
        desktop_installed,
        system_applications_usable,
    )

    admin = system_applications_usable()
    installed = desktop_installed()
    previous = None if installed is None else installed[1]
    if admin or (installed is not None and installed[0].parent == SYSTEM_APPLICATIONS):
        folder = SYSTEM_APPLICATIONS
    else:
        folder = Path.home().resolve() / "Applications"
    steps = StepReporter(
        error,
        header=copy.installing(__version__, previous, folder, SYSTEM_APPLICATIONS, admin=admin),
        rows=copy.install_rows(keep_quarantine=keep_quarantine),
    )
    return steps, folder


def _install(kernel: Kernel, output: Console, error: Console, *, keep_quarantine: bool = False) -> None:
    from heinzel.heads._desktop_installation import (  # noqa: PLC0415 - native install only
        SYSTEM_APPLICATIONS,
        install_desktop,
    )

    steps: StepReporter | None = None
    try:
        steps, folder = _plan(error, keep_quarantine=keep_quarantine)
        state = install_desktop(
            __version__, desktop_executable(kernel), reporter=steps, keep_quarantine=keep_quarantine
        )
    except KeyboardInterrupt as exc:
        if steps is not None:
            steps.close(complete=False)
        error.print(copy.CANCELLED_INSTALL)
        raise typer.Exit(130) from exc
    except (HeinzelError, OSError, SubprocessError) as exc:
        if steps is not None:
            steps.close(complete=False)
        error.print(message(copy.INSTALL_FAILED, str(exc)))
        raise typer.Exit(1) from exc
    steps.close(complete=state in {"installed", "updated"})
    if state == "deferred":
        error.print(copy.DEFERRED)
        raise typer.Exit(1)
    location = _located(error)
    if state == "current":
        output.print(copy.current(__version__, location), markup=False, soft_wrap=True)
        return
    if location.parent != folder:
        error.print(copy.not_moved(location, SYSTEM_APPLICATIONS), markup=False, soft_wrap=True)
    output.print(copy.done(location), markup=False, soft_wrap=True)


def _uninstall(output: Console, error: Console) -> None:
    from heinzel.heads._desktop_installation import uninstall_desktop  # noqa: PLC0415 - native removal only

    steps = StepReporter(error)
    try:
        removed = uninstall_desktop(reporter=steps)
    except KeyboardInterrupt as exc:
        error.print(copy.CANCELLED_UNINSTALL)
        raise typer.Exit(130) from exc
    except (HeinzelError, OSError, SubprocessError) as exc:
        error.print(message(copy.UNINSTALL_FAILED, str(exc)))
        raise typer.Exit(1) from exc
    if removed is None:
        output.print(copy.NOT_INSTALLED, markup=False, soft_wrap=True)
        return
    StepReporter(output).check(copy.removed(removed))
    output.print(copy.PROFILE_KEPT, markup=False, soft_wrap=True)


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
        _install(kernel, output, error, keep_quarantine=keep_quarantine)

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
    def uninstall() -> None:
        """Remove the managed desktop app and keep your Heinzel profile."""
        _uninstall(output, error)

    @app.command("refresh", hidden=True)
    def refresh() -> None:
        """Refresh the managed app after an orderly native exit."""
        try:
            _refresh_after_exit(kernel)
        except (HeinzelError, OSError, SubprocessError) as exc:
            error.print(message(copy.UPDATE_FAILED, str(exc)))

    return app
