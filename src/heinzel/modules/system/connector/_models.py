"""Models of the connector module."""

from typing import Literal

from pydantic import BaseModel, Field

from heinzel import Check, Login
from heinzel.kernel import How, Mode

State = Literal["connected", "not-signed-in", "not-bound", "attention", "unknown"]
"""A connector's state from its offline checks (ADR 0055): signed in and serving something; its tool has no sign-in;
it serves no capability; another check needs attention; or nothing says, so only a live test can tell."""


class ConnectorCapability(BaseModel):
    """A capability one of the connector's adapters provides, and whether that adapter serves it."""

    capability: str = Field(description="The capability, e.g. `tracker`.")
    title: str = Field(description="Its name for people, e.g. `Tracker`.")
    adapter: str = Field(description="The connector's adapter for it, the name bindings use, e.g. `github`.")
    modes: list[Mode] = Field(description="The modes the adapter serves.")
    used: bool = Field(description="Whether this adapter serves the capability now.")
    bound: str | None = Field(description="The adapter that serves the capability now, this one or another; none.")
    how: How = Field(description="How that adapter was chosen.")


class Tool(BaseModel):
    """A capability method the connector implements: what hz can do through it."""

    capability: str = Field(description="The capability, e.g. `code-host`.")
    method: str = Field(description="The method, e.g. `merged_pull_requests`.")
    mode: Mode = Field(description="`read` or `write`; writes run only from publish operations.")
    summary: str = Field(description="What the method does, the first line of its docstring.")


class Connector(BaseModel):
    """A module that provides adapters, with its state, sign-in, tools and the commands that connect it."""

    name: str = Field(description="Qualified name of the module, e.g. `github`; `hz system connector test` takes it.")
    title: str = Field(description="Its name for people, e.g. `GitHub`.")
    summary: str = Field(description="What it connects, the module's summary.")
    distribution: str = Field(description="The distribution that ships it.")
    builtin: bool = Field(description="Whether it is part of the core rather than a plugin.")
    state: State = Field(description="Connected, not signed in, not bound, needs attention, or unknown until tested.")
    login: Login | None = Field(description="What its checks found about its sign-in; none if its tool has none.")
    capabilities: list[ConnectorCapability] = Field(description="The capabilities its adapters provide.")
    tools: list[Tool] = Field(description="The capability methods its adapters implement, with their modes.")
    checks: list[Check] = Field(description="Its offline health checks, as doctor runs them.")
    connect: list[str] = Field(description="Command lines that sign its tool in, run by the person in a terminal.")
    bind: list[str] = Field(description="Command lines that make it serve the capabilities it doesn't serve yet.")
    testable: bool = Field(description="Whether it has a live test (`hz system connector test`).")


class AdapterCandidate(BaseModel):
    """An adapter that can serve a capability, and the state of its connector."""

    adapter: str = Field(description="The name bindings use, e.g. `github`.")
    connector: str = Field(description="Qualified name of the module that provides it.")
    title: str = Field(description="Its connector's name for people, e.g. `GitHub`.")
    state: State = Field(description="Its connector's state.")


class CapabilityChoice(BaseModel):
    """A capability, the adapter serving it and how it was chosen, and every adapter that could."""

    capability: str = Field(description="The capability, e.g. `code-host`.")
    title: str = Field(description="Its name for people, e.g. `Code host`.")
    purpose: str = Field(description="What it is for, in a few plain words, e.g. `pull requests and reviews`.")
    key: str = Field(description="Its configuration key, e.g. `capabilities.code_host`.")
    bound: str | None = Field(description="The adapter that serves it now; none.")
    how: How = Field(description="How that adapter was chosen.")
    problem: str | None = Field(
        description="Why the chosen adapter can't serve it, e.g. not installed; none if it can."
    )
    candidates: list[AdapterCandidate] = Field(description="Every installed adapter that provides it.")
    find: str = Field(description="The command line that finds a plugin providing it.")


class ConnectorReport(BaseModel):
    """Every connector, and for every capability who serves it and who could."""

    connectors: list[Connector] = Field(description="Every module that provides adapters, by title.")
    capabilities: list[CapabilityChoice] = Field(description="Every capability the core defines, by title.")


class ConnectorTest(BaseModel):
    """The outcome of a connector's live test: one cheap read-only call through its tool."""

    name: str = Field(description="Qualified name of the module tested.")
    passed: bool = Field(description="Whether every live check is up.")
    latency_ms: int = Field(description="How long the test took, in milliseconds.")
    login: Login | None = Field(description="Who the tool says it is signed in as, if it says.")
    checks: list[Check] = Field(description="The live checks: what was called and what came back.")
