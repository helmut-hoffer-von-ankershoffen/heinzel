"""Calendar owns exact profile inventory; system removal consumes only the public maintenance port."""

import os
import plistlib
import re
import shlex
import sys
from itertools import islice
from pathlib import Path

from pydantic import TypeAdapter

from heinzel import HeinzelError
from heinzel.kernel import (
    HostIntegration,
    HostMaintenanceIntegration,
    HostServiceManager,
    capture_owned_integration_file,
    host_maintenance_lock,
    verify_integration_directory,
)
from heinzel.modules.org._reminder_service import LABEL, Profile, definition, path_for

MAX_PROFILES = 512
MAX_DIRECTORY_ENTRIES = 10_000
MAX_MANAGER_BYTES = 1_000_000
MANAGER_COLUMNS = 3
KEYS = {"HZ_CONFIG_HOME", "HZ_CONFIG", "HZ_STATE_DIR", "HZ_DATA_DIR", "HZ_CACHE_DIR", "HZ_LOG_DIR"}


class CalendarMaintenance(HostMaintenanceIntegration):
    """Provider-free inventory of every recorded calendar profile sharing the selected SDK."""

    platform = str(sys.platform)

    def inventory(self, prefix: Path, binary: Path, manager: HostServiceManager) -> tuple[HostIntegration, ...]:
        home, platform = Path.home(), self.platform
        directory = home / "Library/LaunchAgents" if platform == "darwin" else home / ".config/systemd/user"
        result = [self._descriptor(path, prefix, binary) for path in self._files(directory, platform)]
        self._loaded(result, manager, platform)
        return tuple(sorted(result, key=lambda item: item.name))

    def _files(self, directory: Path, platform: str) -> list[Path]:
        files: list[Path] = []
        if os.path.lexists(directory):
            # Capturing any file below the directory proves physical parent ownership without following links.
            verify_integration_directory(directory)
            names = sorted(islice(directory.iterdir(), MAX_DIRECTORY_ENTRIES + 1))
            if len(names) > MAX_DIRECTORY_ENTRIES:
                self._refuse()
            files = [
                path
                for path in names
                if path.name.startswith(LABEL if platform == "darwin" else "hz-calendar-reminders-")
            ]
        if len(files) > MAX_PROFILES:
            self._refuse()
        return files

    def _descriptor(self, path: Path, prefix: Path, binary: Path) -> HostIntegration:
        home, platform = Path.home(), self.platform
        captured = capture_owned_integration_file(path, limit=256 * 1024)
        environment = self._environment(captured.content, platform)
        if set(environment) != KEYS:
            self._refuse()
        if any(
            value and (not Path(value).is_absolute() or ".." in Path(value).parts) for value in environment.values()
        ):
            self._refuse()
        if any(value and Path(value).resolve().is_relative_to(prefix) for value in environment.values()):
            self._refuse()
        profile = Profile(environment)
        executable = binary / "hz"
        if (
            path != path_for(platform, home, profile)
            or captured.content != definition(platform, executable, profile).encode()
        ):
            self._refuse()
        environment = environment | {"HZ_REMINDER_PROFILE": profile.identity}
        if platform == "darwin":
            environment["PATH"] = f"{binary}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        return HostIntegration(
            name=profile.label if platform == "darwin" else profile.unit,
            prefix=prefix,
            platform=platform,
            files=(captured,),
            locks=(
                host_maintenance_lock(prefix),
                Path(profile.environment["HZ_STATE_DIR"]) / "calendar-reminders/lifecycle.lock",
            ),
            unit=path,
            program=executable,
            arguments=(str(executable), "org", "calendar-reminder-worker"),
            environment=environment,
            directory=home,
        )

    def _environment(self, content: bytes, platform: str) -> dict[str, str]:
        environment: dict[str, str] = {}
        if platform == "darwin":
            values = plistlib.loads(content)
            environment = TypeAdapter(dict[str, str]).validate_python(values.get("EnvironmentVariables", {}))
            environment = {
                key: value for key, value in environment.items() if key not in {"PATH", "HZ_REMINDER_PROFILE"}
            }
        else:
            environment = {}
            for line in content.decode().splitlines():
                if line.startswith("Environment="):
                    entries = shlex.split(line.removeprefix("Environment="))
                    if len(entries) != 1:
                        self._refuse()
                    key, separator, value = entries[0].replace("%%", "%").partition("=")
                    if not separator or key in environment:
                        self._refuse()
                    environment[key] = value
            environment.pop("HZ_REMINDER_PROFILE", None)
        return environment

    def _loaded(self, result: list[HostIntegration], manager: HostServiceManager, platform: str) -> None:
        known = {item.name for item in result}
        command = (
            ["/bin/launchctl", "list"]
            if platform == "darwin"
            else [
                "/usr/bin/systemctl",
                "--user",
                "list-units",
                "--all",
                "--plain",
                "--no-legend",
                "--no-pager",
                "hz-calendar-reminders-*.service",
            ]
        )
        loaded = manager.command(command)
        if loaded.returncode or len(loaded.stdout.encode()) > MAX_MANAGER_BYTES:
            self._refuse()
        lines = loaded.stdout.splitlines()
        if platform == "darwin":
            if (
                not lines
                or lines[0].split() != ["PID", "Status", "Label"]
                or any(
                    len(line.split()) != MANAGER_COLUMNS
                    or not re.fullmatch(r"-|[1-9][0-9]{0,9}", line.split()[0])
                    or not re.fullmatch(r"-?[0-9]{1,10}", line.split()[1])
                    for line in lines[1:]
                )
                or len({line.split()[-1] for line in lines[1:]}) != len(lines) - 1
            ):
                self._refuse()
            labels = [line.split()[-1] for line in lines[1:] if line.split()[-1].startswith(LABEL)]
        else:
            labels = [line.split()[0] for line in lines if line.strip()]
            if any(not re.fullmatch(r"hz-calendar-reminders-[a-f0-9]{24}\.service", label) for label in labels):
                self._refuse()
        if any(label not in known for label in labels) or len(set(labels)) != len(labels):
            self._refuse()

    @staticmethod
    def _refuse() -> None:
        msg = "A calendar worker is unrecorded, foreign or changed; inspect its exact profile before uninstalling."
        raise HeinzelError(msg)
