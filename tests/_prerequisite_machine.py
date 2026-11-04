"""A described machine for the prerequisite engine (ADR 0112): files that answer `--version`, a package manager that
installs more of them, and a log of every command; nothing real is looked at or started."""

import os
from pathlib import Path

from heinzel.kernel import ProcessOutcome
from heinzel.modules.system.prerequisite._host import ToolHost

BREW = "/opt/homebrew/bin/brew"


class Machine:
    """A machine with files that answer `--version`, a package manager that installs them, and a log of what ran."""

    def __init__(self, platform: str = "darwin", *, path: str = "/usr/bin:/bin", present: dict[str, str] | None = None):
        self.platform = platform
        self.environment = {"PATH": path, "HOME": "/home/test"}
        self.files: dict[str, str] = dict(present or {})
        self.commands: list[list[str]] = []
        self.environments: list[dict[str, str]] = []
        self.provides: dict[str, dict[str, str]] = {}
        self.brew_code = 0
        self.brew_timeout = False
        self.developer_tools = True
        self.unstartable: set[str] = set()
        self.probe_codes: dict[str, int] = {}
        self.probe_timeouts: set[str] = set()
        self.probe_truncated: set[str] = set()
        self.failing_packages: set[str] = set()
        self.provides_when_failing: dict[str, dict[str, str]] = {}

    def host(self) -> ToolHost:
        return ToolHost(
            platform=self.platform,
            environment=self.environment,
            execute=self.execute,
            which=self.which,
            is_executable=lambda path: path in self.files,
            brew_locations=(BREW, "/usr/local/bin/brew"),
        )

    def which(self, name: str, path: str) -> str | None:
        return next((f"{d}/{name}" for d in path.split(os.pathsep) if f"{d}/{name}" in self.files), None)

    def execute(self, command: list[str], **options: object) -> ProcessOutcome:
        self.commands.append(command)
        self.environments.append(dict(options["env"]))  # type: ignore[call-overload]
        program = Path(command[0]).name
        if program in self.unstartable:
            raise FileNotFoundError(command[0])
        if program == "xcode-select":
            return ProcessOutcome(0 if self.developer_tools else 2, "/Library/Developer\n", "")
        if program == "brew":
            if self.brew_timeout:
                return ProcessOutcome(None, "", "")
            packages = command[3:]
            failed = self.brew_code != 0 or bool(self.failing_packages & set(packages))
            for package in packages:
                if not failed or package in self.provides_when_failing:
                    self.files.update(self.provides.get(package, {}) | self.provides_when_failing.get(package, {}))
            return ProcessOutcome(self.brew_code or int(failed), "", "")
        if program in self.probe_timeouts:
            return ProcessOutcome(None, "", "")
        return ProcessOutcome(
            self.probe_codes.get(program, 0), self.files[command[0]], "", truncated=program in self.probe_truncated
        )


def ready_everywhere(*absent: str) -> ToolHost:
    """A Linux host on which every tool but `absent` answers a version, for suites that are about something else.

    Nothing real runs: `brew` and `xcode-select` are refused outright, any other program prints a version.
    """

    def execute(command: list[str], **options: object) -> ProcessOutcome:
        name = Path(command[0]).name
        if name in {"brew", "xcode-select"}:
            msg = f"A test reached the real {name}; describe the machine with a fake ToolHost"
            raise AssertionError(msg)
        del options
        return ProcessOutcome(0, f"{name} version 9.9.9\n", "")

    return ToolHost(
        platform="linux",
        execute=execute,
        which=lambda name, path: None if name in absent else f"/usr/bin/{name}",
        is_executable=lambda path: Path(path).name not in absent,
        brew_locations=(),
    )
