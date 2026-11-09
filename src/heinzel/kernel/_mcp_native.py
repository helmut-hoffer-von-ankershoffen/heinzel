"""Read-only native namespace preflight for an exact MCP registration."""

import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._uninstall_recovery import manager_properties

if TYPE_CHECKING:
    from heinzel.kernel._host_maintenance import HostServiceManager
    from heinzel.kernel._mcp_storage import InstalledMcpRuntime

type NativeMcpState = Literal["not-checked", "absent", "conflict", "unavailable"]
_MAX_MANAGER_BYTES = 1_000_000
_LAUNCHD_ROW = re.compile(r"(?:-|[1-9][0-9]{0,9})\s+(?:-|\-?[0-9]{1,10})\s+(\S+)")
MAC_LABEL_PREFIX = "io.github.helmut-hoffer-von-ankershoffen.heinzel.mcp."
LINUX_UNIT_PREFIX = "hz-mcp-"


@dataclass(frozen=True, slots=True)
class McpNativePlan:
    """Name one deterministic native unit per installed profile and registration, without loading it."""

    platform: str
    name: str

    @classmethod
    def capture(cls, installation: "InstalledMcpRuntime", registration: str) -> "McpNativePlan":
        """Derive the future on-demand unit name from the trusted host selection."""
        identity = arguments_hash([str(installation.prefix), str(installation.state), registration])[:24]
        name = f"{MAC_LABEL_PREFIX}{identity}" if sys.platform == "darwin" else f"{LINUX_UNIT_PREFIX}{identity}.service"
        return cls(sys.platform, name)

    def observe(self, manager: "HostServiceManager | None") -> NativeMcpState:
        """Inspect only the verified user manager. An existing exact name needs ownership proof."""
        if manager is None:
            return "not-checked"
        try:
            if self.platform == "darwin":
                return self._launchd(manager)
            if self.platform == "linux":
                return self._systemd(manager)
        except (HeinzelError, OSError, ValueError):
            return "unavailable"
        return "unavailable"

    def _launchd(self, manager: "HostServiceManager") -> NativeMcpState:
        result = manager.command(["/bin/launchctl", "list"])
        if result.returncode or len(result.stdout.encode()) > _MAX_MANAGER_BYTES:
            return "unavailable"
        lines = result.stdout.splitlines()
        if not lines or lines[0].split() != ["PID", "Status", "Label"]:
            return "unavailable"
        labels = set()
        for row in lines[1:]:
            match = _LAUNCHD_ROW.fullmatch(row)
            if match is None or match[1] in labels:
                return "unavailable"
            labels.add(match[1])
        return "conflict" if self.name in labels else "absent"

    def _systemd(self, manager: "HostServiceManager") -> NativeMcpState:
        result = manager.command(
            [
                "/usr/bin/systemctl",
                "--user",
                "show",
                self.name,
                "--property=Id,LoadState,FragmentPath,DropInPaths,MainPID,ControlPID,Job",
            ]
        )
        if result.returncode:
            return "unavailable"
        values = manager_properties(result.stdout)
        absent = {
            "Id": self.name,
            "LoadState": "not-found",
            "FragmentPath": "",
            "DropInPaths": "",
            "MainPID": "0",
            "ControlPID": "0",
            "Job": "",
        }
        return "absent" if values == absent else "conflict"
