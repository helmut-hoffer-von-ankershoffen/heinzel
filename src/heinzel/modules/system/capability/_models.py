"""Models of the capability module."""

from pydantic import BaseModel, Field

from heinzel.kernel import CapabilityUse, Mode


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
    hosts: list[str] = Field(description="Hosts it sends data to; empty when its configuration decides.")
    module: str = Field(description="Qualified name of the module that provides it.")


class CapabilityReport(BaseModel):
    """Capabilities, adapters and the matrix of who reads and writes what, through which adapter and hosts."""

    capabilities: list[CapabilityInfo] = Field(description="Every capability the core defines, with its binding.")
    adapters: list[AdapterInfo] = Field(description="Every installed adapter.")
    uses: list[CapabilityUse] = Field(
        description="Every declared requirement: requester, capability and modes, adapter, hosts, and what is unmet."
    )
