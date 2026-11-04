"""The connector module: whether the tools hz reads through are connected, and a live test on demand (ADR 0055)."""

from typing import Annotated

from pydantic import Field
from rich.console import RenderableType

from heinzel import Module
from heinzel.modules.system.connector._models import (
    AdapterCandidate,
    CapabilityChoice,
    Connector,
    ConnectorCapability,
    ConnectorReport,
    ConnectorTest,
    Tool,
)
from heinzel.modules.system.connector._service import ConnectorService


def render_report(report: ConnectorReport) -> RenderableType:
    """A table of connectors with their state and what to run next, and one of who serves each capability."""
    from heinzel.modules.system.connector._render import render_report  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_report(report)


def render_test(test: ConnectorTest) -> RenderableType:
    """Whether the live test passed, how long it took, and each check."""
    from heinzel.modules.system.connector._render import render_test  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_test(test)


module = Module("connector", "Show whether each connector is signed in and serves something, and test one live.")
module.service(ConnectorService)

# Not the REST head: it has no authentication, and a test reaches the vendor as you (ADR 0047, ADR 0055).
HEADS = frozenset({"cli", "mcp", "web"})

Name = Annotated[str, Field(description="The connector, as `hz system connector list` names it, e.g. `github`.")]


@module.operation(name="list", render=render_report, heads=HEADS)
def list_(service: ConnectorService) -> ConnectorReport:
    """List connectors with their sign-in, what they serve and their tools; offline."""
    return service.report()


@module.operation(render=render_test, heads=HEADS)
def test(service: ConnectorService, name: Name) -> ConnectorTest:
    """Test a connector live: one cheap read-only call through its tool, with a short timeout."""
    return service.test(name)


__all__ = [
    "AdapterCandidate",
    "CapabilityChoice",
    "Connector",
    "ConnectorCapability",
    "ConnectorReport",
    "ConnectorService",
    "ConnectorTest",
    "Tool",
    "module",
]
