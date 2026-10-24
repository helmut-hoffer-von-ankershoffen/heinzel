"""How the CLI head presents the capability report."""

from rich.console import Group, RenderableType
from rich.table import Table

from heinzel.modules.system.capability._models import CapabilityReport


def render_report(report: CapabilityReport) -> RenderableType:
    """The bindings, the adapters and the matrix of requester → capability:modes → adapter → hosts."""
    capabilities = Table(title="Capabilities")
    for column in ("Capability", "Modes", "Bound to", "Summary"):
        capabilities.add_column(column, overflow="fold")
    for capability in report.capabilities:
        capabilities.add_row(capability.name, ",".join(capability.modes), capability.bound or "-", capability.summary)
    adapters = Table(title="Adapters")
    for column in ("Adapter", "Capability", "Modes", "Hosts", "Module"):
        adapters.add_column(column, overflow="fold")
    for adapter in report.adapters:
        hosts = ", ".join(adapter.hosts) or "set by its configuration"
        adapters.add_row(adapter.adapter, adapter.capability, ",".join(adapter.modes), hosts, adapter.module)
    uses = Table(title="Who reads and writes what")
    for column in ("Requester", "Needs", "Adapter", "Hosts", "Problem"):
        uses.add_column(column, overflow="fold")
    for use in report.uses:
        problem = f"[yellow]{use.problem}[/yellow]" if use.problem else ""
        uses.add_row(use.requester, use.requirement, use.adapter or "-", ", ".join(use.hosts), problem)
    return Group(capabilities, adapters, uses)
