"""The connector module: whether the tools hz reads through are connected, and a live test on demand (ADR 0055)."""

from typing import Annotated

from pydantic import Field
from rich.console import RenderableType

from heinzel import Module, Prompter
from heinzel.modules.system.connector._models import (
    Access,
    AdapterCandidate,
    CapabilityChoice,
    Connector,
    ConnectorCapability,
    ConnectorReport,
    ConnectorTest,
    SignInFlow,
    Tool,
)
from heinzel.modules.system.connector._service import ConnectorService
from heinzel.modules.system.connector._sign_in import SignIns


def render_report(report: ConnectorReport) -> RenderableType:
    """A table of connectors with their state and what to run next, and one of who serves each capability."""
    from heinzel.modules.system.connector._render import render_report  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_report(report)


def render_test(test: ConnectorTest) -> RenderableType:
    """Whether the live test passed, how long it took, and each check."""
    from heinzel.modules.system.connector._render import render_test  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_test(test)


module = Module("connector", "Show whether each connector is signed in and serves something, and test one live.")
module.service(SignIns)
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


# Sign-ins are the person's own (ADR 0136): never on MCP, where an agent could start one, nor the open REST head.
SIGN_IN_HEADS = frozenset({"cli", "web"})
Live = Annotated[bool, Field(description="Ask the vendor too: confirms the account and which grants hold.")]
Allow = Annotated[
    list[str] | None,
    Field(description="A grant to add, such as `calendar-write`, as `access` lists them; repeat for each."),
]
Wait = Annotated[
    bool,
    Field(
        description=(
            "Wait here until it ends, opening the consent page in this machine's browser; only where a person is "
            "told (the terminal). Elsewhere it returns at once: poll `flow`, open the page with `open`."
        )
    ),
]
Flow = Annotated[str, Field(description="The sign-in, by the id `connect` returned.")]


@module.operation(heads=SIGN_IN_HEADS, private=True)
def access(service: ConnectorService, name: Name, live: Live = False) -> Access:
    """Show who a connector is signed in as, which of its grants hold, and its latest sign-in here."""
    return service.access(name, live=live)


@module.operation(heads=SIGN_IN_HEADS, private=True, read_only=False)
def connect(
    sign_ins: SignIns,
    prompter: Prompter,
    name: Name,
    allow: Allow = None,
    wait: Wait = True,
) -> SignInFlow:
    """Sign a connector in through its vendor's own page, keeping what it holds and adding what you allow."""
    # Nobody is told on the web head: never hold a worker for the consent, nor open the page without a click.
    return sign_ins.connect(name, frozenset(allow or ()), wait=wait and prompter.interactive, prompter=prompter)


@module.operation(name="flow", heads=SIGN_IN_HEADS, private=True)
def flow_(sign_ins: SignIns, flow: Flow) -> SignInFlow:
    """Show where a sign-in started here is: waiting for your consent, or how it ended."""
    return sign_ins.flow(flow)


@module.operation(heads=SIGN_IN_HEADS, private=True, read_only=False)
def cancel(sign_ins: SignIns, flow: Flow) -> SignInFlow:
    """Stop a sign-in that is waiting for consent; nothing changes in your account."""
    return sign_ins.cancel(flow)


@module.operation(name="open", heads=SIGN_IN_HEADS, private=True, read_only=False)
def open_(sign_ins: SignIns, flow: Flow) -> SignInFlow:
    """Open a sign-in's consent page in the browser of the machine hz runs on, where the vendor returns to."""
    return sign_ins.open(flow)


@module.operation(heads=SIGN_IN_HEADS, private=True, read_only=False)
def disconnect(service: ConnectorService, sign_ins: SignIns, name: Name) -> Access:
    """Sign a connector's tool out through its own command; reading and writing through it stop."""
    return sign_ins.disconnect(name, service.title(name))


__all__ = [
    "Access",
    "AdapterCandidate",
    "CapabilityChoice",
    "Connector",
    "ConnectorCapability",
    "ConnectorReport",
    "ConnectorService",
    "ConnectorTest",
    "SignInFlow",
    "SignIns",
    "Tool",
    "module",
]
