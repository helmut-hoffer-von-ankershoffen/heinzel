"""Refresh the managed bundle from the verified post-update CLI, without loading plugins in the child."""

import re
import subprocess
import sys
from pathlib import Path

from heinzel import HeinzelError, Kernel, __version__
from heinzel.heads._desktop_installation import (
    DesktopInstallationError,
    DesktopInstallStatus,
    desktop_installation_record_exists,
    install_desktop,
)
from heinzel.heads._desktop_release import REPOSITORY, VERSION
from heinzel.kernel import (
    CorePin,
    DaemonInstallationLifecycle,
    DesktopHeadRefresh,
    HeadRefresh,
    Installation,
    boot,
    run_bounded_process,
    subprocess_environment,
)
from heinzel.kernel._uv_locate import with_search_path

_CHILD = "--_refresh-managed-desktop"
_ARGUMENTS = 2
_RETRY = "Run `hz app doctor`, then retry `hz system update`."
DESKTOP_UPDATE_SECONDS = 600.0
"""Allow for installer lock contention, release verification and downloads while bounding the whole child."""


def desktop_update(kernel: Kernel) -> DesktopHeadRefresh:
    """Only the macOS CLI owns this post-update operation."""
    return DesktopHeadRefresh(
        present=desktop_installation_record_exists,
        eligible=lambda target: _eligible_pin(kernel.resolve(Installation), target) is not None,
        refresh=lambda target, generation: _launch(kernel.resolve(Installation), target, generation),
    )


def _eligible_pin(installation: Installation, target: str) -> CorePin | None:
    if not VERSION.fullmatch(target):
        return None
    pin = installation.core_pin()
    if pin is None:
        return None
    if (
        pin.kind == "release"
        and pin.version == target
        and pin.subdirectory is None
        and pin.git is not None
        and pin.git.removesuffix(".git").casefold() == f"https://github.com/{REPOSITORY}"
    ):
        return pin
    return None


def _executable(installation: Installation, generation: str) -> Path:
    if installation.startup_fingerprint() == generation:
        executable = next((entry for entry in installation.tool_entrypoints() if entry.name == "hz"), None)
        if executable is not None and installation.startup_fingerprint() == generation:
            return executable
    msg = "The verified Core installation or its `hz` entry point changed."
    raise HeinzelError(msg)


def _launch(installation: Installation, target: str, generation: str) -> HeadRefresh:
    """Invoke the verified receipt-owned entry point. Child output never reaches the command's streams."""
    executable = _executable(installation, generation)
    environment = with_search_path(subprocess_environment())
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    child = run_bounded_process(
        [str(executable), _CHILD, target, generation],
        input=None,
        cwd=None,
        env=environment,
        timeout=DESKTOP_UPDATE_SECONDS,
        limit=16 * 1024,
    )
    if child.timed_out:
        return HeadRefresh(
            head="desktop",
            status="failed",
            detail="The desktop update timed out. Run `hz system update` again to check the bundle and retry.",
        )
    if child.returncode == 0 and not child.truncated:
        result = HeadRefresh.model_validate_json(child.stdout)
        if (
            result.head == "desktop"
            and result.version is None
            and result.status in {"on-disk", "pending", "failed", "not-installed"}
        ):
            return result
    return HeadRefresh(
        head="desktop",
        status="failed",
        detail=f"The installed hz {target} could not finish the desktop update. {_RETRY}",
    )


def _refresh(target: str, generation: str) -> HeadRefresh:
    if target != __version__:
        msg = "The desktop updater is running a different Core release than the completed update."
        raise HeinzelError(msg)
    kernel = boot(builtin_only=True)
    installation = kernel.resolve(Installation)
    lifecycle = kernel.resolve(DaemonInstallationLifecycle)
    with lifecycle.admission(), installation.tool_transaction():
        pin = _eligible_pin(installation, target)
        if pin is None:
            msg = "The installed Core source is outside the managed desktop release channel."
            raise HeinzelError(msg)
        installation.verify_installed_requirements([pin.requirement], expected_version=target)
        commit = installation.installed_core_git_commit()
        executable = _executable(installation, generation)
        if not desktop_installation_record_exists():
            return HeadRefresh(head="desktop", status="not-installed", detail="No managed desktop app is installed.")
        outcome = install_desktop(target, executable, update_only=True, explicit_update=True, expected_commit=commit)
        return _desktop_result(target, outcome)


def _desktop_result(target: str, outcome: DesktopInstallStatus) -> HeadRefresh:
    if outcome == "deferred":
        return HeadRefresh(
            head="desktop", status="pending", detail="The app is in use. Quit it, then run `hz system update` again."
        )
    if outcome == "absent":
        return HeadRefresh(head="desktop", status="not-installed", detail="No managed desktop app is installed.")
    if outcome == "updated":
        return HeadRefresh(head="desktop", status="on-disk", detail=f"Updated the bundle on disk to {target}.")
    if outcome == "current":
        return HeadRefresh(head="desktop", status="on-disk", detail=f"The bundle on disk is already at {target}.")
    if outcome == "newer":
        return HeadRefresh(
            head="desktop", status="on-disk", detail=f"Kept the bundle on disk, newer than Core {target}."
        )
    return HeadRefresh(head="desktop", status="failed", detail=f"The bundle on disk could not be verified. {_RETRY}")


def run_desktop_update(arguments: list[str]) -> int:
    """The private post-update vector. Emit one result for the initiating CLI to render."""
    if len(arguments) != _ARGUMENTS:
        return 2
    target, generation = arguments
    if not VERSION.fullmatch(target) or not re.fullmatch(r"[a-f0-9]{64}", generation):
        return 2
    try:
        result = _refresh(target, generation)
    except DesktopInstallationError as exc:
        detail = f"{exc.problem.happened} {exc.problem.update_action}" if exc.problem is not None else f"{exc} {_RETRY}"
        result = HeadRefresh(head="desktop", status="failed", detail=detail)
    except HeinzelError as exc:
        result = HeadRefresh(head="desktop", status="failed", detail=f"{exc} {_RETRY}")
    except (OSError, ValueError, subprocess.SubprocessError):
        result = HeadRefresh(head="desktop", status="failed", detail=f"The desktop update could not finish. {_RETRY}")
    sys.stdout.write(result.model_dump_json() + "\n")
    return 0
