"""Opt-in user service definitions for calendar reminders (ADR 0126)."""

import hashlib
import json
import os
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from heinzel import Config, HeinzelError, Settings
from heinzel.kernel import user_config_dir

LABEL = "io.github.helmut-hoffer-von-ankershoffen.heinzel.calendar-reminders"
LAUNCHCTL_NO_SERVICE = 113
ARGS = ("org", "calendar-reminder-worker")


def profile_environment() -> dict[str, str]:
    """Bind only configuration and storage locations, never credentials or cached consent."""
    config = Config.load(Path.home())
    settings = config.settings(Settings, "hz")
    environment = {"HZ_CONFIG_HOME": str(user_config_dir().expanduser().resolve()), "HZ_CONFIG": ""}
    if explicit := os.environ.get("HZ_CONFIG"):
        environment["HZ_CONFIG"] = str(Path(explicit).expanduser().resolve())
    for field in ("state_dir", "data_dir", "cache_dir", "log_dir"):
        environment[f"HZ_{field.upper()}"] = str(getattr(settings, field).expanduser().resolve())
    return environment


@dataclass(frozen=True)
class Profile:
    """One immutable selection used for every step of a supervisor action."""

    environment: dict[str, str]

    @classmethod
    def selected(cls) -> "Profile":
        """Capture resolved user configuration and storage locations once."""
        return cls(profile_environment())

    @property
    def identity(self) -> str:
        """The stable identity of these locations, independent of editable policy."""
        return hashlib.sha256(json.dumps(self.environment, sort_keys=True).encode()).hexdigest()[:24]

    @property
    def label(self) -> str:
        """This profile's launchd label."""
        return f"{LABEL}.{self.identity}"

    @property
    def unit(self) -> str:
        """This profile's systemd unit."""
        return f"hz-calendar-reminders-{self.identity}.service"


def verify_binding() -> None:
    """Refuse a worker launched without the exact supervisor profile binding.

    Raises:
        HeinzelError: If the selected locations differ from the installed worker's binding.
    """
    if os.environ.get("HZ_REMINDER_PROFILE") != Profile.selected().identity:
        msg = "The calendar worker is not bound to this profile. Use Calendar reminder setup."
        raise HeinzelError(msg)


def definition(platform: str, executable: Path, profile: Profile | None = None) -> str:
    """Render a user-owned service that restarts the bounded worker at login and after exit.

    Raises:
        HeinzelError: If this platform has no supported supervisor.
    """
    profile = profile or Profile.selected()
    environment = profile.environment | {"HZ_REMINDER_PROFILE": profile.identity}
    if platform == "darwin":
        return plistlib.dumps(
            {
                "Label": profile.label,
                "ProgramArguments": [str(executable), *ARGS],
                "RunAtLoad": True,
                "KeepAlive": True,
                "ThrottleInterval": 5,
                "ProcessType": "Background",
                "Nice": 10,
                "StandardOutPath": "/dev/null",
                "StandardErrorPath": "/dev/null",
                "WorkingDirectory": str(Path.home()),
                "EnvironmentVariables": environment
                | {"PATH": f"{executable.parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
            },
            sort_keys=True,
        ).decode()
    if platform == "linux":
        environment_lines = "".join(
            f"Environment={json.dumps(key + '=' + value).replace('%', '%%')}\n"
            for key, value in sorted(environment.items())
        )
        command = shlex.join([str(executable), *ARGS]).replace("%", "%%").replace("$", "$$")
        return (
            "[Unit]\nDescription=Heinzel calendar reminders\n\n[Service]\nType=simple\n"
            f"WorkingDirectory={json.dumps(str(Path.home())).replace('%', '%%')}\n"
            f"{environment_lines}ExecStart={command}\nRestart=always\nRestartSec=1\nNice=10\n"
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


def path_for(platform: str, home: Path, profile: Profile | None = None) -> Path:
    """The service file belonging to this user and this feature.

    Raises:
        HeinzelError: If this platform has no supported supervisor.
    """
    profile = profile or Profile.selected()
    if platform == "darwin":
        return home / "Library" / "LaunchAgents" / f"{profile.label}.plist"
    if platform == "linux":
        return home / ".config" / "systemd" / "user" / profile.unit
    msg = f"Calendar reminders do not support {platform}."
    raise HeinzelError(msg)


def install() -> str:
    """Install and load the service; refuse to overwrite an existing file.

    Raises:
        HeinzelError: If the platform, installation or service manager is unavailable.
    """
    platform, home = sys.platform, Path.home()
    profile = Profile.selected()
    path = path_for(platform, home, profile)
    executable = _shim()
    content = definition(platform, executable, profile)
    if path.exists() or path.is_symlink():
        _owned(path, content)
        observed = status(profile)
        if observed == "running":
            return "Calendar reminder service is already running for this profile."
        if observed == "loaded":
            return "Calendar reminder service is loaded for this profile; running has not been observed."
    else:
        _write(path, content)
    _load(platform, path, profile)
    if status(profile) not in {"running", "loaded"}:
        msg = "The reminder service file is installed, but loading could not be verified. Retry setup to recover."
        raise HeinzelError(msg)
    return "Calendar reminder service installed. Enable Calendar reminders in Notifications settings to allow reads."


def _owned(path: Path, content: str) -> os.stat_result:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            matches = (
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid()
                and not info.st_mode & 0o022
                and stream.read() == content
            )
    except OSError as exc:
        msg = "The reminder service file could not be verified safely; it was left alone."
        raise HeinzelError(msg) from exc
    if not matches:
        msg = "The reminder service file is not an unchanged file owned by this profile; it was left alone."
        raise HeinzelError(msg)
    return info


def _write(path: Path, content: str) -> None:
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


def _load(platform: str, path: Path, profile: Profile) -> None:
    commands = (
        [["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]]
        if platform == "darwin"
        else [
            ["/usr/bin/systemctl", "--user", "daemon-reload"],
            ["/usr/bin/systemctl", "--user", "enable", "--now", profile.unit],
        ]
    )
    for command in commands:
        if _run(command).returncode != 0:
            msg = "The calendar reminder service could not be loaded; retry setup to recover this profile safely."
            raise HeinzelError(msg)


def uninstall() -> str:
    """Stop and remove only a byte-matching unit owned by this feature.

    Raises:
        HeinzelError: If the unit was modified or the service manager cannot stop it.
    """
    platform = sys.platform
    profile = Profile.selected()
    path = path_for(platform, Path.home(), profile)
    if not path.exists() and not path.is_symlink():
        return "Calendar reminder service is not installed for this profile."
    owned = _owned(path, definition(platform, _shim(), profile))
    command = (
        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{profile.label}"]
        if platform == "darwin"
        else ["/usr/bin/systemctl", "--user", "disable", "--now", profile.unit]
    )
    stopped = _run(command)
    if stopped.returncode != 0 and not _absent(platform, profile):
        msg = "The calendar reminder service could not be stopped; its file was left in place."
        raise HeinzelError(msg)
    if _owned(path, definition(platform, _shim(), profile))[:7] != owned[:7]:
        msg = "The reminder service file changed during removal; it was left alone."
        raise HeinzelError(msg)
    path.unlink()
    if platform == "linux" and _run(["/usr/bin/systemctl", "--user", "daemon-reload"]).returncode != 0:
        msg = "The reminder service stopped and its file was removed, but systemd reload failed."
        raise HeinzelError(msg)
    return "Calendar reminder service stopped and removed."


def status(profile: Profile | None = None) -> str:
    """Report the service manager's observation without installing or changing anything."""
    try:
        platform = sys.platform
        profile = profile or Profile.selected()
        path = path_for(platform, Path.home(), profile)
        if path.is_symlink():
            return "unknown"
        if not path.exists():
            return "not installed"
        _owned(path, definition(platform, _shim(), profile))
        command = (
            ["/bin/launchctl", "print", f"gui/{os.getuid()}/{profile.label}"]
            if platform == "darwin"
            else ["/usr/bin/systemctl", "--user", "is-active", "--quiet", profile.unit]
        )
        result = _run(command)
        if result.returncode != 0:
            return "unknown"
        observed = "loaded" if platform == "darwin" and "state = running" not in result.stdout else "running"
    except (OSError, HeinzelError):
        return "unknown"
    return observed


def _absent(platform: str, profile: Profile) -> bool:
    """Accept only the supervisor's explicit missing-unit result, never a generic failure."""
    command = (
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{profile.label}"]
        if platform == "darwin"
        else ["/usr/bin/systemctl", "--user", "show", "--property=LoadState", "--value", profile.unit]
    )
    result = _run(command)
    if platform == "darwin":
        return result.returncode == LAUNCHCTL_NO_SERVICE and "Could not find service" in result.stderr
    return result.returncode == 0 and result.stdout.strip() == "not-found"
