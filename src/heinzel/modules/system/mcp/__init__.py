"""Control one profile's managed MCP HTTP service from the CLI or authenticated web."""

import subprocess
from typing import Annotated

from pydantic import Field

from heinzel import HeinzelError, Module
from heinzel.kernel import HostMaintenanceIntegration
from heinzel.modules.system.mcp._records import Maintenance, refuse
from heinzel.modules.system.mcp._service import McpStatus, Service, installation, platform

module = Module("mcp", "Start, check and stop this profile's managed local MCP HTTP server.")
module.service(Service)


def maintenance() -> Maintenance:
    """Keep provider-free recovery available even when new starts are disabled."""
    return Maintenance(installation().prefix, platform())


module.service(HostMaintenanceIntegration, maintenance)


@module.operation(read_only=False, private=True, heads={"cli", "web"})
def start(
    service: Service,
    confirm: Annotated[
        bool, Field(description="Confirm starting one temporary loopback server for this profile.")
    ] = False,
) -> McpStatus:
    """Start the managed local MCP server after confirmation, or verify the same running generation.

    Raises:
        HeinzelError: If confirmation or owned lifecycle evidence is missing.
    """
    if not confirm:
        msg = "Confirm starting the managed MCP server for this profile. It will not start at login."
        raise HeinzelError(msg)
    try:
        return service.start()
    except (OSError, ValueError, subprocess.SubprocessError):
        refuse()


@module.operation(private=True, heads={"cli", "web"})
def check(service: Service) -> McpStatus:
    """Check the owned MCP server and authenticate its current endpoint without starting anything."""
    try:
        return service.check()
    except (HeinzelError, OSError, ValueError, subprocess.SubprocessError):
        return McpStatus(state="unverified")


@module.operation(read_only=False, private=True, heads={"cli", "web"})
def stop(
    service: Service,
    confirm: Annotated[
        bool, Field(description="Confirm stopping only the captured server owned by this profile.")
    ] = False,
) -> McpStatus:
    """Stop this profile's owned MCP server and verify its listener is closed.

    Raises:
        HeinzelError: If confirmation or scoped shutdown evidence is missing.
    """
    if not confirm:
        msg = "Confirm stopping this profile's owned MCP server."
        raise HeinzelError(msg)
    try:
        return service.stop()
    except (OSError, ValueError, subprocess.SubprocessError):
        refuse()
