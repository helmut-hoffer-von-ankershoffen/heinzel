"""How the CLI head presents connectors and their live tests."""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Status
from heinzel.modules.system.connector._models import CapabilityChoice, ConnectorReport, ConnectorTest, State

STATES: dict[State, tuple[str, str]] = {
    "connected": ("connected", "green"),
    "not-signed-in": ("not signed in", "yellow"),
    "not-bound": ("not bound", "yellow"),
    "attention": ("needs attention", "yellow"),
    "unknown": ("not tested", "dim"),
}


def _connectors(report: ConnectorReport) -> Table:
    table = Table(title="Connectors")
    for column in ("Connector", "State", "Serves", "Next"):
        table.add_column(column, overflow="fold")
    for connector in report.connectors:
        label, style = STATES[connector.state]
        if connector.login is not None and connector.login.account and connector.state == "connected":
            label = f"connected as {connector.login.account}"
        serves = ", ".join(use.title for use in connector.capabilities if use.used) or "-"
        if connector.state == "not-signed-in":
            then = connector.connect
        elif connector.state == "not-bound":
            then = connector.bind
        else:
            then = [f"hz system connector test {connector.name}"] if connector.testable else []
        table.add_row(Text(connector.title), Text(label, style=style), Text(serves), Text("\n".join(then)))
    return table


def _served(choice: CapabilityChoice) -> str:
    if choice.how == "off":
        return "none (turned off)"
    served = "-" if choice.bound is None else choice.bound
    if choice.how in {"automatic", "default"}:
        served += f" ({choice.how})"
    return f"{served}: {choice.problem}" if choice.problem else served


def render_report(report: ConnectorReport) -> RenderableType:
    """A table of connectors with their state and what to run next, and one of who serves each capability."""
    capabilities = Table(title="Which connector serves what")
    for column in ("Capability", "Served by", "Could be served by"):
        capabilities.add_column(column, overflow="fold")
    for choice in report.capabilities:
        could = ", ".join(c.adapter for c in choice.candidates) or f"nothing installed: {choice.find}"
        capabilities.add_row(Text(f"{choice.title}: {choice.purpose}"), Text(_served(choice)), Text(could))
    return Group(_connectors(report), capabilities)


def render_test(test: ConnectorTest) -> RenderableType:
    """Whether the live test passed, how long it took, and each check."""
    marks = {Status.UP: "[green]✔[/green]", Status.DEGRADED: "[yellow]▲[/yellow]", Status.DOWN: "[red]✖[/red]"}
    verdict = "passed" if test.passed else "failed"
    table = Table(title=f"{test.name}: the live test {verdict} in {test.latency_ms} ms")
    for column in ("", "Check", "Detail", "Fix"):
        table.add_column(column, overflow="fold")
    for check in test.checks:
        table.add_row(marks[check.status], Text(check.name), Text(check.detail), Text(check.fix))
    return table
