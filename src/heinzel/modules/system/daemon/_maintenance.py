"""Public host-manager port backed by the daemon's existing fixed session authority."""

import subprocess

from heinzel import Kernel
from heinzel.kernel import HostServiceManager
from heinzel.modules.system.daemon._service import DaemonService


class SystemHostServiceManager(HostServiceManager):
    """Construction reads only the user profile and local installation, never account/provider services."""

    def __init__(self, kernel: Kernel) -> None:
        self.service = DaemonService.for_user(kernel)

    def command(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        """Retain the existing fixed executable, bounded output and before/after UID/session/bus checks."""
        return self.service.owned_manager_command(arguments)
