"""The argument vectors of the web job for systemd (`systemctl --user`, the user's own instance).

The lifecycle service decides what to do and reads the answers; this file alone spells the manager's command lines,
exactly as the service ran them before (the ServiceManager port's systemd half, `heinzel._adapter_service`).
"""

from dataclasses import dataclass

MANAGER = "systemctl"
"""The program name the service looks up."""
PATH = "/usr/bin/systemctl"
"""Where Linux distributions keep it; an installed tool uses this fixed path, never a lookup."""
INSPECTED = "--property=LoadState,FragmentPath,DropInPaths,ExecStart,MainPID"


@dataclass(frozen=True)
class SystemdWebCommands:
    """The web job's commands for `executable` (systemctl) in the user instance, for the unit `unit`."""

    executable: str
    unit: str

    def base(self) -> list[str]:
        """The manager and its fixed flag, for the ServiceManager adapter."""
        return [self.executable, "--user"]

    def inspect(self) -> list[str]:
        """`systemctl --user show <unit>` with the properties that prove its identity."""
        return [*self.base(), "show", self.unit, INSPECTED]

    def load(self, definition: str) -> list[list[str]]:  # noqa: ARG002 - systemd finds the unit by name
        """`systemctl --user daemon-reload`, then `enable --now <unit>`."""
        return [[*self.base(), "daemon-reload"], [*self.base(), "enable", "--now", self.unit]]

    def start(self, definition: str) -> list[str]:  # noqa: ARG002 - systemd finds the unit by name
        """`systemctl --user start <unit>`."""
        return [*self.base(), "start", self.unit]

    def unload(self) -> list[str]:
        """`systemctl --user disable --now <unit>`."""
        return [*self.base(), "disable", "--now", self.unit]

    def reload(self) -> list[str] | None:
        """`systemctl --user daemon-reload`, after a definition changed on disk."""
        return [*self.base(), "daemon-reload"]

    def stop_state(self) -> list[str] | None:
        """`systemctl --user show <unit> --property=ActiveState,MainPID`."""
        return [*self.base(), "show", self.unit, "--property=ActiveState,MainPID"]
