"""The connector module: whether the tools hz reads through are connected, and a live test on demand (ADR 0055)."""

from typing import Annotated

from pydantic import Field

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
from heinzel.modules.system.connector._render import render_report, render_test
from heinzel.modules.system.connector._service import ConnectorService

module = Module("connector", "Show whether each connector is signed in and serves something, and test one live.")
module.service(ConnectorService)

# Not the REST head: it has no authentication, and a test reaches the vendor as you (ADR 0047). Not MCP either: the
# account is the person's own login or address, which an agent's context doesn't need; MCP gets `status`.
HEADS = frozenset({"cli", "web"})

Name = Annotated[str, Field(description="The connector, as `hz system connector list` names it, e.g. `github`.")]


@module.operation(name="list", render=render_report, heads=HEADS)
def list_(service: ConnectorService) -> ConnectorReport:
    """List connectors with their sign-in, what they serve and their tools; offline."""
    return service.report()


@module.operation(render=render_report, heads={"mcp"})
def status(service: ConnectorService) -> ConnectorReport:
    """List connectors with their state, what they serve and their tools, without accounts; offline."""
    return service.report(accounts=False)


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
