"""The argument vectors of the web job for launchd (`launchctl`, one user session `gui/<uid>`).

The lifecycle service decides what to do and reads the answers; this file alone spells the manager's command lines,
exactly as the service ran them before (the ServiceManager port's launchd half, `heinzel._adapter_service`).
"""

from dataclasses import dataclass

MANAGER = "launchctl"
"""The program name the service looks up."""
PATH = "/bin/launchctl"
"""Where macOS keeps it; an installed tool uses this fixed path, never a lookup."""


@dataclass(frozen=True)
class LaunchdWebCommands:
    """The web job's commands for `executable` (launchctl) in the session of `uid`, for the job `label`."""

    executable: str
    uid: int
    label: str

    def base(self) -> list[str]:
        """The manager itself, for the ServiceManager adapter."""
        return [self.executable]

    def inspect(self) -> list[str]:
        """`launchctl print gui/<uid>/<label>`: the loaded job's identity."""
        return [self.executable, "print", f"gui/{self.uid}/{self.label}"]

    def listing(self) -> list[str]:
        """`launchctl list`: every job, to tell absence from a failed request."""
        return [self.executable, "list"]

    def session(self) -> tuple[list[str], list[str]]:
        """`launchctl managername` and `manageruid`: the desktop session and its owner."""
        return [self.executable, "managername"], [self.executable, "manageruid"]

    def load(self, definition: str) -> list[list[str]]:
        """`launchctl bootstrap gui/<uid> <plist>`, which also starts it."""
        return [self.start(definition)]

    def start(self, definition: str) -> list[str]:
        """`launchctl bootstrap gui/<uid> <plist>`: hand the job to launchd again."""
        return [self.executable, "bootstrap", f"gui/{self.uid}", definition]

    def unload(self) -> list[str]:
        """`launchctl bootout gui/<uid>/<label>`."""
        return [self.executable, "bootout", f"gui/{self.uid}/{self.label}"]

    def reload(self) -> list[str] | None:
        """Launchd reads definitions on bootstrap: nothing to reload."""
        return None

    def stop_state(self) -> list[str] | None:
        """Launchd says stopped by not knowing the job: no separate query."""
        return None
