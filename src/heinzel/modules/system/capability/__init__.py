"""The capability module: which adapter serves each capability, and who reads and writes what (ADR 0028)."""

from heinzel import Check, Module
from heinzel.modules.system.capability._models import AdapterInfo, CapabilityInfo, CapabilityReport
from heinzel.modules.system.capability._render import render_report
from heinzel.modules.system.capability._service import CapabilityService

module = Module("capability", "Show which adapters serve which capabilities, and who reads and writes what.")
module.service(CapabilityService)


@module.operation(name="list", render=render_report)
def list_(service: CapabilityService) -> CapabilityReport:
    """List capabilities, their bindings and adapters, and every requirement with its adapter and hosts."""
    return service.report()


@module.info
def matrix(service: CapabilityService) -> dict[str, str]:
    """The matrix as a section of `hz system info`, `requester → capability:modes` to `adapter → hosts`."""
    return service.matrix()


@module.health
def capability(service: CapabilityService) -> list[Check]:
    """Every declared requirement and binding: up when an adapter serves it, degraded when unmet."""
    return service.checks()


__all__ = ["AdapterInfo", "CapabilityInfo", "CapabilityReport", "CapabilityService", "module"]
