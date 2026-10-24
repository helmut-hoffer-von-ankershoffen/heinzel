"""Capabilities: named protocols the core defines, with the access mode of every method (ADR 0028).

A requester declares what it needs as a requirement string, `"tracker:read"` or `"documents:read,write"`; an adapter
declares what it provides. Requirements name capabilities, never vendors, so a binding stays swappable; a vendor pin
(`"google-workspace:documents:read"`) is allowed where a plugin truly needs one adapter, and is shown as such.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, cast

from heinzel.kernel._agents import AgentRunner
from heinzel.kernel._protocols import (
    Calendar,
    Chat,
    CodeHost,
    Directory,
    Documents,
    Mail,
    MailQuery,
    Meetings,
    TeamGraph,
    Tracker,
)

Mode = Literal["read", "write"]
MODES: tuple[Mode, ...] = ("read", "write")
_ADAPTER = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class Capability:
    """A capability: its name in requirements and bindings, its protocol, and the mode each method needs."""

    name: str
    protocol: type[Any]
    summary: str
    methods: Mapping[str, Mode] = field(default_factory=lambda: MappingProxyType({}))
    """Every method of the protocol with the mode a handle needs to call it."""
    checks: Mapping[tuple[str, str], Callable[[str], object]] = field(default_factory=lambda: MappingProxyType({}))
    """Checks of `str` arguments by (method, argument), raising `ValueError`: what a contract's `args` are held to
    before any run, e.g. a mail query's syntax."""

    @property
    def modes(self) -> frozenset[Mode]:
        """The modes this capability has: `read`, and `write` if any method writes."""
        return frozenset(self.methods.values())


CAPABILITIES: Mapping[str, Capability] = MappingProxyType(
    {
        capability.name: capability
        for capability in (
            Capability(
                name="agent-runner",
                protocol=AgentRunner,
                summary="Bounded agent sessions with read-only tools, reporting draft, tool calls and cost.",
                methods=MappingProxyType({"run": "read"}),
            ),
            Capability(
                name="calendar",
                protocol=Calendar,
                summary="The user's calendar: events in a window, with attendees and conferencing.",
                methods=MappingProxyType({"events": "read"}),
            ),
            Capability(
                name="chat",
                protocol=Chat,
                summary="Channels people talk in: read their messages; post through publish operations.",
                methods=MappingProxyType({"messages": "read", "post": "write"}),
            ),
            Capability(
                name="code-host",
                protocol=CodeHost,
                summary="Repositories and their merged pull requests on a code host.",
                methods=MappingProxyType({"merged_pull_requests": "read"}),
            ),
            Capability(
                name="directory",
                protocol=Directory,
                summary="Who people are and who reports to whom: a directory of record, or guesses from 1:1s.",
                methods=MappingProxyType({"person": "read", "reports": "read"}),
            ),
            Capability(
                name="documents",
                protocol=Documents,
                summary="Documents: read their text; replace a marked section through publish operations.",
                methods=MappingProxyType({"read": "read", "replace_section": "write"}),
            ),
            Capability(
                name="mail",
                protocol=Mail,
                summary="A mailbox the user reads: messages in a window by sender, subject or label, and their text.",
                methods=MappingProxyType({"messages": "read", "read": "read"}),
                checks=MappingProxyType({("messages", "query"): MailQuery.parse}),
            ),
            Capability(
                name="meetings",
                protocol=Meetings,
                summary="Meetings the user took part in, and their transcripts.",
                methods=MappingProxyType({"ended": "read", "transcripts": "read", "transcript": "read"}),
            ),
            Capability(
                name="team-graph",
                protocol=TeamGraph,
                summary="The team's graph of kept promises: artifacts, Heinzelmännchen and where the flow waits.",
                methods=MappingProxyType({"snapshot": "read"}),
            ),
            Capability(
                name="tracker",
                protocol=Tracker,
                summary="Issues in an issue tracker.",
                methods=MappingProxyType({"issues_updated": "read"}),
            ),
        )
    }
)
"""Every capability the core defines, by name."""


def capability_of(protocol: Any) -> Capability | None:  # noqa: ANN401 - any annotation
    """The capability whose protocol is `protocol`, or None."""
    return next((c for c in CAPABILITIES.values() if c.protocol is protocol), None)


@dataclass(frozen=True, slots=True)
class Requirement:
    """A capability someone needs, in which modes, optionally pinned to one adapter."""

    capability: str
    modes: frozenset[Mode]
    adapter: str | None = None
    """A vendor pin: only this adapter will do, whatever is bound."""

    def __str__(self) -> str:
        """The requirement as it is written: `documents:read,write`, `google-workspace:documents:read`."""
        modes = ",".join(mode for mode in MODES if mode in self.modes)
        return f"{self.adapter}:{self.capability}:{modes}" if self.adapter else f"{self.capability}:{modes}"

    @classmethod
    def parse(cls, text: str) -> "Requirement":
        """Read a requirement string: `<capability>:<modes>` or `<adapter>:<capability>:<modes>`.

        Raises:
            ValueError: If the capability is unknown, a mode is missing or unknown, or the capability lacks the mode.
        """
        parts = text.strip().split(":")
        if len(parts) not in {2, 3}:
            msg = f"Requirement {text!r} must read `<capability>:<modes>`, e.g. `tracker:read` or `documents:read`"
            raise ValueError(msg)
        adapter = parts[0] if len(parts) == 3 else None  # noqa: PLR2004 - adapter, capability, modes
        name, modes_text = parts[-2], parts[-1]
        if adapter is not None and not _ADAPTER.fullmatch(adapter):
            msg = f"Requirement {text!r}: adapter {adapter!r} must match {_ADAPTER.pattern}"
            raise ValueError(msg)
        capability = CAPABILITIES.get(name)
        if capability is None:
            msg = f"Requirement {text!r}: unknown capability {name!r}; known: {', '.join(sorted(CAPABILITIES))}"
            raise ValueError(msg)
        words = [word.strip() for word in modes_text.split(",") if word.strip()]
        if not words or any(word not in MODES for word in words):
            msg = f"Requirement {text!r}: modes must be `read`, `write` or `read,write`"
            raise ValueError(msg)
        modes: frozenset[Mode] = frozenset(cast("Mode", word) for word in words)
        if missing := modes - capability.modes:
            msg = f"Requirement {text!r}: {name} has no {', '.join(sorted(missing))} mode"
            raise ValueError(msg)
        return cls(capability=name, modes=modes, adapter=adapter)


def parse_requirements(requires: Iterable[str]) -> tuple[Requirement, ...]:
    """Parse requirement strings, merging the modes of repeated capabilities.

    Raises:
        ValueError: If a requirement is malformed, or one capability is pinned to two adapters.
    """
    merged: dict[str, Requirement] = {}
    for text in requires:
        requirement = Requirement.parse(text)
        earlier = merged.get(requirement.capability)
        if earlier is not None:
            if earlier.adapter != requirement.adapter:
                msg = f"Requirement {text!r} conflicts with {earlier}: one adapter per capability"
                raise ValueError(msg)
            requirement = Requirement(requirement.capability, earlier.modes | requirement.modes, earlier.adapter)
        merged[requirement.capability] = requirement
    return tuple(merged.values())
