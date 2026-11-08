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
from heinzel.heads._desktop_log import problem_fields, step
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
        error.print(say.LOST)
        raise typer.Exit(1)
    return location


def _print_notes(steps: StepReporter, notes: list[str]) -> None:
    """Say what the installer set aside or moved before it did its main work, as check-marked rows."""
    for note in notes:
        steps.row(note)


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
        error.print(message(say.CHECK_FAILED, str(exc)))
        raise typer.Exit(1) from exc
    if as_json:
        state = {"installed": False} if report is None else {"installed": True, **report.as_dict()}
        output.print_json(data={**state, "repairs": repairs})
    if report is None:
        error.print(say.NOT_INSTALLED_FOR_DOCTOR)
        raise typer.Exit(1)
    if not as_json:
        from heinzel.heads._desktop_installation import SYSTEM_APPLICATIONS  # noqa: PLC0415 - native check only

        seal_lines = report.lines()[1:]  # its "App: ..." line gives way to the copy module's, which names the folder
        for line in [*repairs, say.doctor_app(report.bundle, SYSTEM_APPLICATIONS), *seal_lines]:
            output.print(line, markup=False)
    if not report.opens:
        error.print(say.DOCTOR_FIX)
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
        header=say.installing(__version__, previous, folder, SYSTEM_APPLICATIONS, admin=admin),
        rows=say.install_rows(keep_quarantine=keep_quarantine),
    )
    return steps, folder


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
        SYSTEM_APPLICATIONS,
        LeftoverInstallError,
        install_desktop,
    )

    notes: list[str] = []
    steps, folder = _plan(error, keep_quarantine=keep_quarantine)

    def run(*, forget: bool) -> str:
        return install_desktop(
            __version__,
            desktop_executable(kernel),
            reporter=steps,
            keep_quarantine=keep_quarantine,
            replace=replace,
            forget=forget,
            notes=notes,
        )

    try:
        try:
            state = run(forget=yes)
        except LeftoverInstallError as exc:
            steps.close(complete=False)
            if not _confirm_forget(error, exc):
                _print_notes(steps, notes)
                _fail(error, exc, say.INSTALL_FAILED, details=details)
            state = run(forget=True)
    except KeyboardInterrupt as exc:
        steps.close(complete=False)
        error.print(say.CANCELLED_INSTALL)
        raise typer.Exit(130) from exc
    except (HeinzelError, OSError, SubprocessError) as exc:
        steps.close(complete=False)
        _print_notes(steps, notes)
        _fail(error, exc, say.INSTALL_FAILED, details=details)
    steps.close(complete=state in {"installed", "updated"})
    _print_notes(steps, notes)
    if state == "deferred":
        for line in say.app_running(_where_installed()).lines():
            error.print(line, markup=False)
        raise typer.Exit(1)
    location = _located(error)
    if state == "current":
        output.print(say.current(__version__, location), markup=False, soft_wrap=True)
        return
    if location.parent != folder:
        error.print(say.not_moved(location, SYSTEM_APPLICATIONS), markup=False, soft_wrap=True)
    output.print(say.done(location), markup=False, soft_wrap=True)


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
        output.print(say.INSTALLING_FIRST)
        with step("open", "install"):
            _install_app(kernel, output, error, details=details)
        location = desktop_location()
        if location is None:
            _say(error, say.lost())
    with step("open", "check", path=str(location)) as checked:
        report = inspect_bundle(location)
        checked.update({"quarantine": report.quarantine, "outcome": "ok" if report.opens else "would_not_open"})
        if not report.opens:
            checked.update(problem_fields(say.would_not_open(location)))
    if not report.opens:
        _say(error, say.would_not_open(location))
    if report.quarantine != "none":
        output.print(say.asks_once(), markup=False)
    with step("open", "launch", path=str(location)) as launched:
        result = subprocess.run(  # noqa: S603 - fixed system executable and the receipt-owned app path
            ["/usr/bin/open", str(location)], check=False, capture_output=True, text=True, timeout=30
        )
        if result.returncode:
            failed = problem_fields(say.open_failed(location))
            launched.update({"outcome": "failed", "returncode": result.returncode, **failed})
    if result.returncode:
        _say(error, say.open_failed(location))
    output.print(say.opened(location), markup=False, soft_wrap=True)
    return location


def _uninstall(output: Console, error: Console, *, details: bool = False) -> None:
    """Remove the app hz manages; a record whose app is gone is set aside instead.

    Raises:
        typer.Exit: If the app cannot be removed safely.
    """
    from heinzel.heads._desktop_installation import uninstall_desktop  # noqa: PLC0415 - native removal only

    notes: list[str] = []
    steps = StepReporter(error)
    try:
        removed = uninstall_desktop(notes=notes, reporter=steps)
    except KeyboardInterrupt as exc:
        error.print(say.CANCELLED_UNINSTALL)
        raise typer.Exit(130) from exc
    except (HeinzelError, OSError, SubprocessError) as exc:
        _print_notes(steps, notes)
        _fail(error, exc, say.UNINSTALL_FAILED, details=details)
    _print_notes(steps, notes)
    if removed is None:
        output.print(say.NOT_INSTALLED, markup=False, soft_wrap=True)
        return
    StepReporter(output).check(say.removed(removed))
    output.print(say.PROFILE_KEPT, markup=False, soft_wrap=True)


def _say(error: Console, problem: say.Problem) -> NoReturn:
    """Print a failure's two lines and exit 1.

    Raises:
        typer.Exit: Always, with 1.
    """
    for line in problem.lines():
        error.print(line, markup=False)
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
        _uninstall(output, error, details=details)

    @app.command("refresh", hidden=True)
    def refresh() -> None:
        """Refresh the managed app after an orderly native exit."""
        try:
            _refresh_after_exit(kernel)
        except (HeinzelError, OSError, SubprocessError) as exc:
            error.print(message(say.UPDATE_FAILED, str(exc)))

    return app
