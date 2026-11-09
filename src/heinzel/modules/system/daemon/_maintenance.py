"""Public host-manager port backed by the daemon's existing fixed session authority."""

import subprocess
from dataclasses import dataclass

from heinzel import Kernel
from heinzel.kernel import HostServiceManager
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService


@dataclass
class DaemonManagerPort(HostServiceManager):
    """Use an existing daemon's fixed native manager authority."""

    service: DaemonService

    def command(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        """Retain the existing fixed executable, bounded output and before/after UID/session/bus checks."""
        return self.service.owned_manager_command(arguments)


class SystemHostServiceManager(DaemonManagerPort):
    """Construction never loads accounts, providers or project configuration."""

    def __init__(self, kernel: Kernel, *, recordless: bool = False) -> None:
        service = (
            DaemonService(kernel, kernel.settings, DaemonSettings()) if recordless else DaemonService.for_user(kernel)
        )
        super().__init__(service)
