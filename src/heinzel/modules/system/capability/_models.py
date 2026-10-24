"""Models of the capability module."""

from typing import Literal

from pydantic import BaseModel, Field

from heinzel.kernel import Mode


class CapabilityInfo(BaseModel):
    """A capability the core defines and the adapter bound to it."""

    name: str = Field(description="Name in requirements and bindings, e.g. `tracker`.")
    summary: str = Field(description="What the capability covers.")
    modes: list[Mode] = Field(description="The modes it has: read, and write if any method writes.")
    bound: str | None = Field(description="The adapter bound in the `[capabilities]` table; none if unbound.")


class AdapterInfo(BaseModel):
    """An installed adapter."""

    adapter: str = Field(description="Name bindings use, e.g. `github`.")
    capability: str = Field(description="The capability it implements.")
    modes: list[Mode] = Field(description="The modes it serves.")
    hosts: list[str] = Field(description="Hosts it sends data to; `unknown (configured)` when configuration decides.")
    module: str = Field(description="Qualified name of the module that provides it.")
    distribution: str = Field(description="Distribution that ships the module.")


class CapabilityUse(BaseModel):
    """One row of the matrix: who needs which capability in which modes, through which adapter and hosts."""

    requester: str = Field(description="Who declared it: a module, or `<module> <operation or job>`.")
    kind: Literal["module", "operation", "job"] = Field(description="What the requester is.")
    requirement: str = Field(description="As declared, e.g. `documents:read` or `github:tracker:read`.")
    capability: str = Field(description="The capability, e.g. `tracker`.")
    modes: list[Mode] = Field(description="The modes declared.")
    adapter: str | None = Field(description="The adapter that serves it: the pinned or bound one; none if none can.")
    hosts: list[str] = Field(
        description="Hosts that adapter sends data to; `unknown (configured)` when its configuration decides."
    )
    problem: str | None = Field(description="Why the requirement is unmet; none when it is met.")
    optional: bool = Field(
        default=False, description="Whether the requester does without it when it is unmet, so that is no finding."
    )


class CapabilityReport(BaseModel):
    """Capabilities, adapters and the matrix of who reads and writes what, through which adapter and hosts."""

    capabilities: list[CapabilityInfo] = Field(description="Every capability the core defines, with its binding.")
    adapters: list[AdapterInfo] = Field(description="Every installed adapter.")
    uses: list[CapabilityUse] = Field(
        description="Every declared requirement: requester, capability and modes, adapter, hosts, and what is unmet."
    )
