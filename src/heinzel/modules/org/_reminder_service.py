"""Opt-in user service definitions for calendar reminders (ADR 0126)."""

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from heinzel import HeinzelError

LABEL = "io.github.helmut-hoffer-von-ankershoffen.heinzel.calendar-reminders"
UNIT = "hz-calendar-reminders.service"
ARGS = ("org", "calendar-reminder-worker")


def definition(platform: str, executable: Path) -> str:
    """Render a user-owned service that restarts the bounded worker at login and after exit.

    Raises:
        HeinzelError: If this platform has no supported supervisor.
    """
    if platform == "darwin":
        return plistlib.dumps(
            {
                "Label": LABEL,
                "ProgramArguments": [str(executable), *ARGS],
                "RunAtLoad": True,
                "KeepAlive": True,
                "ThrottleInterval": 5,
                "ProcessType": "Background",
                "Nice": 10,
                "StandardOutPath": "/dev/null",
                "StandardErrorPath": "/dev/null",
                "EnvironmentVariables": {"PATH": f"{executable.parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
            },
            sort_keys=True,
        ).decode()
    if platform == "linux":
        command = shlex.join([str(executable), *ARGS]).replace("%", "%%").replace("$", "$$")
        return (
            "[Unit]\nDescription=Heinzel calendar reminders\n\n[Service]\nType=simple\n"
            f"ExecStart={command}\nRestart=always\nRestartSec=1\nNice=10\n"
            "StandardOutput=null\nStandardError=journal\n\n[Install]\nWantedBy=default.target\n"
        )
    msg = f"Calendar reminders do not support {platform}."
    raise HeinzelError(msg)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"Cannot run {command[0]}: {type(exc).__name__}."
        raise HeinzelError(msg) from exc


def _shim() -> Path:
    uv = shutil.which("uv")
    if uv is None:
        msg = "Install uv and the hz tool before installing reminders."
        raise HeinzelError(msg)
    result = _run([uv, "tool", "dir", "--bin"])
    executable = Path(result.stdout.strip()) / "hz"
    if result.returncode != 0 or not executable.is_absolute() or not executable.exists():
        msg = "Cannot find the installed hz tool shim; install hz as a uv tool first."
        raise HeinzelError(msg)
    return executable


def path_for(platform: str, home: Path) -> Path:
    """The service file belonging to this user and this feature.

    Raises:
        HeinzelError: If this platform has no supported supervisor.
    """
    if platform == "darwin":
        return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if platform == "linux":
        return home / ".config" / "systemd" / "user" / UNIT
    msg = f"Calendar reminders do not support {platform}."
    raise HeinzelError(msg)


def install() -> str:
    """Install and load the service; refuse to overwrite an existing file.

    Raises:
        HeinzelError: If the platform, installation or service manager is unavailable.
    """
    platform, home = sys.platform, Path.home()
    path = path_for(platform, home)
    executable = _shim()
    content = definition(platform, executable)
    if path.exists() or path.is_symlink():
        msg = "The calendar reminder service file already exists; uninstall it before installing again."
        raise HeinzelError(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary, path)  # never replace an existing unit, even in a race
    except FileExistsError as exc:
        msg = "The calendar reminder service file appeared; it was not overwritten."
        raise HeinzelError(msg) from exc
    finally:
        temporary.unlink(missing_ok=True)
    commands = (
        [["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]]
        if platform == "darwin"
        else [
            ["/usr/bin/systemctl", "--user", "daemon-reload"],
            ["/usr/bin/systemctl", "--user", "enable", "--now", UNIT],
        ]
    )
    for command in commands:
        if _run(command).returncode != 0:
            msg = "The calendar reminder service could not be loaded; inspect the service file before retrying."
            raise HeinzelError(msg)
    verify = (
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]
        if platform == "darwin"
        else ["/usr/bin/systemctl", "--user", "is-active", "--quiet", UNIT]
    )
    if _run(verify).returncode != 0:
        msg = "The calendar reminder service was installed, but its running state could not be verified."
        raise HeinzelError(msg)
    return "Calendar reminder service installed. Enable Calendar reminders in Notifications settings to allow reads."


def uninstall() -> str:
    """Stop and remove only a byte-matching unit owned by this feature.

    Raises:
        HeinzelError: If the unit was modified or the service manager cannot stop it.
    """
    platform = sys.platform
    path = path_for(platform, Path.home())
    if not path.exists() or path.is_symlink():
        msg = "The calendar reminder service file is missing or is a link; it was left alone."
        raise HeinzelError(msg)
    if path.read_text(encoding="utf-8") != definition(platform, _shim()):
        msg = "The calendar reminder service file was modified; it was left alone."
        raise HeinzelError(msg)
    command = (
        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]
        if platform == "darwin"
        else ["/usr/bin/systemctl", "--user", "disable", "--now", UNIT]
    )
    if _run(command).returncode != 0:
        msg = "The calendar reminder service could not be stopped; its file was left in place."
        raise HeinzelError(msg)
    path.unlink()
    if platform == "linux" and _run(["/usr/bin/systemctl", "--user", "daemon-reload"]).returncode != 0:
        msg = "The reminder service stopped and its file was removed, but systemd reload failed."
        raise HeinzelError(msg)
    return "Calendar reminder service stopped and removed."


def status() -> str:
    """Report the service manager's observation without installing or changing anything."""
    try:
        platform = sys.platform
        path = path_for(platform, Path.home())
        if path.is_symlink():
            return "unknown"
        if not path.exists():
            return "not installed"
        if path.read_text(encoding="utf-8") != definition(platform, _shim()):
            return "unknown"
        command = (
            ["/bin/launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]
            if platform == "darwin"
            else ["/usr/bin/systemctl", "--user", "is-active", "--quiet", UNIT]
        )
        result = _run(command)
        if result.returncode != 0:
            return "unknown"
        observed = "loaded" if platform == "darwin" and "state = running" not in result.stdout else "running"
    except (OSError, HeinzelError):
        return "unknown"
    return observed
