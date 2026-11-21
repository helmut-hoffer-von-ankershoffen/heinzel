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
    free_busy_people,
)
from heinzel.kernel.ai._agents import AgentRunner
from heinzel.kernel.ai._ai import AiAccess
from heinzel.kernel.ai._ai_models import AiModels

Mode = Literal["read", "write"]
MODES: tuple[Mode, ...] = ("read", "write")
NO_ADAPTER = "none"
"""The binding that says a capability is served by nothing, also when an adapter would be chosen automatically
(ADR 0055); no adapter may take the name."""
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
    title: str = ""
    """Its name for people, e.g. `Code host`: what settings and the web app show, without backticks."""
    purpose: str = ""
    """What it is for, in a few plain words after the title, e.g. `pull requests and reviews`."""
    automatic: bool = True
    """Whether its only installed adapter serves it without a binding (ADR 0055). Not for `mail`: reading a mailbox
    is the person's explicit choice, whatever happens to be installed."""

    listed: bool = True
    """Whether Settings and the connector page offer a binding for it. Not for `ai-models`: a person picks AI access,
    and each `ai-models` adapter is one of its options (ADR 0159)."""

    inspection_methods: frozenset[str] = frozenset()
    """Offline inventory methods, never live actions; available only through explicit module inspection grants."""

    optional: frozenset[str] = frozenset()
    """Methods added after adapters shipped (ADR 0160): an adapter that doesn't define one doesn't support it, as if it
    listed it in its `unsupported` attribute (ADR 0159), so it keeps loading."""

    core_only: frozenset[str] = frozenset()
    """Methods only hz itself calls, through its own trusted view (ADR 0160): no requester's handle offers them, in any
    mode, so no operation or playbook can talk to the user in their own direct messages or read their answers."""

    multiple: bool = False
    """Whether several adapters may serve it at once, bound as a list (ADR 0161): the first is the default."""
    routes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    """With several adapters: each method that acts on one item, with the parameter naming it. A call goes to the
    adapter whose links match the item's URL, that returned the item before, or that the `adapter:id` form names."""
    fan_out: frozenset[str] = frozenset()
    """With several adapters: read methods that return a list, asked of every adapter at once and merged."""

    def __post_init__(self) -> None:
        if not (self.optional | self.core_only) <= set(self.methods):
            msg = f"Capability {self.name!r} optional and core-only methods must be declared methods"
            raise ValueError(msg)
        if any(self.methods.get(name) != "read" for name in self.inspection_methods):
            msg = f"Capability {self.name!r} inspection methods must be declared read methods"
            raise ValueError(msg)
        if any(self.methods.get(name) != "read" for name in self.fan_out):
            msg = f"Capability {self.name!r} fan-out methods must be declared read methods"
            raise ValueError(msg)
        if not set(self.routes).isdisjoint(self.fan_out) or not set(self.routes) <= set(self.methods):
            msg = f"Capability {self.name!r}: routed and fan-out methods must be distinct declared methods"
            raise ValueError(msg)

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
                title="Agent runner",
                purpose="what runs a Heinzelmännchen's turn",
                protocol=AgentRunner,
                summary="Bounded agent sessions with read-only tools, reporting draft, tool calls and cost.",
                methods=MappingProxyType({"run": "read"}),
            ),
            Capability(
                name="ai-access",
                protocol=AiAccess,
                summary="How hz reaches AI: whether an option is there and signed in, and a cheap live check of it.",
                methods=MappingProxyType({"status": "read", "test": "read", "test_model": "read"}),
                inspection_methods=frozenset({"status"}),
                optional=frozenset({"test_model"}),
            ),
            Capability(
                name="ai-models",
                title="AI models",
                purpose="models a plugin reaches its own way, run by hz's own engine",
                protocol=AiModels,
                summary="Models a plugin reaches its own way: the models it offers, and a ready Pydantic AI model for "
                "one, which hz's engine runs; each adapter is also an AI access option of the same name.",
                methods=MappingProxyType({"models": "read", "model": "read"}),
                automatic=False,
                listed=False,
            ),
            Capability(
                name="calendar",
                title="Calendar",
                purpose="where your meetings and free time come from",
                protocol=Calendar,
                summary="The user's calendar: events in a window, with attendees and conferencing, free and busy "
                "times; create, move, cancel and answer events through publish operations.",
                methods=MappingProxyType(
                    {
                        "events": "read",
                        "free_busy": "read",
                        "create_event": "write",
                        "update_event": "write",
                        "cancel_event": "write",
                        "respond": "write",
                    }
                ),
                checks=MappingProxyType({("free_busy", "people"): free_busy_people}),
            ),
            Capability(
                name="chat",
                title="Chat",
                purpose="the channels your team talks in",
                protocol=Chat,
                summary="Channels people talk in: read their messages; post through publish operations; notify and ask "
                "the user in their own direct messages (ADR 0160).",
                methods=MappingProxyType(
                    {"messages": "read", "post": "write", "notify_self": "write", "ask_self": "write"}
                ),
                optional=frozenset({"notify_self", "ask_self"}),
                core_only=frozenset({"notify_self", "ask_self"}),
            ),
            Capability(
                name="code-host",
                title="Code host",
                purpose="pull requests and reviews",
                protocol=CodeHost,
                summary="Repositories and their merged pull requests on a code host.",
                methods=MappingProxyType({"merged_pull_requests": "read"}),
            ),
            Capability(
                name="directory",
                title="Directory",
                purpose="who people are and who reports to whom",
                protocol=Directory,
                summary="Who people are and who reports to whom: a directory of record, or guesses from 1:1s.",
                methods=MappingProxyType({"person": "read", "reports": "read"}),
            ),
            Capability(
                name="documents",
                title="Documents",
                purpose="the pages and docs you read and update",
                protocol=Documents,
                summary="Documents: read their text and search them; replace a marked section through publish "
                "operations. Several connectors may serve it at once.",
                methods=MappingProxyType({"read": "read", "replace_section": "write", "search": "read"}),
                multiple=True,
                routes=MappingProxyType({"read": "ref", "replace_section": "ref"}),
                fan_out=frozenset({"search"}),
                optional=frozenset({"search"}),
            ),
            Capability(
                name="mail",
                automatic=False,
                title="Mail",
                purpose="the mailbox messages and meeting summaries come from",
                protocol=Mail,
                summary="A mailbox the user reads: messages in a window by sender, subject or label, their text and "
                "conversations; drafts, sending a draft and filing through publish operations.",
                methods=MappingProxyType(
                    {
                        "messages": "read",
                        "read": "read",
                        "thread": "read",
                        "draft": "read",
                        "create_draft": "write",
                        "send_draft": "write",
                        "relabel": "write",
                    }
                ),
                checks=MappingProxyType({("messages", "query"): MailQuery.parse}),
            ),
            Capability(
                name="meetings",
                title="Meetings",
                purpose="the meetings you took part in and their transcripts",
                protocol=Meetings,
                summary="Meetings the user took part in, and their transcripts.",
                methods=MappingProxyType({"ended": "read", "transcripts": "read", "transcript": "read"}),
            ),
            Capability(
                name="team-graph",
                title="Team graph",
                purpose="how your team's promises flow and where they wait",
                protocol=TeamGraph,
                summary="The team's graph of kept promises: artifacts, Heinzelmännchen and where the flow waits.",
                methods=MappingProxyType({"snapshot": "read"}),
            ),
            Capability(
                name="tracker",
                title="Tracker",
                purpose="issues and tickets",
                protocol=Tracker,
                summary="Issues in an issue tracker: reading what changed; creating, claiming, moving, commenting on, "
                "attaching to and closing issues through publish operations.",
                methods=MappingProxyType(
                    {
                        "issues_updated": "read",
                        "create": "write",
                        "claim": "write",
                        "transition": "write",
                        "note": "write",
                        "attach": "write",
                        "close": "write",
                    }
                ),
            ),
        )
    }
)
"""Every capability the core defines, by name."""


def declared_unsupported(adapter: object, capability: Capability) -> frozenset[str]:
    """The methods of `capability` an adapter (class or instance) declares it doesn't support (ADR 0159).

    Read from its `unsupported` attribute, an iterable of method names; anything that isn't a method of the capability,
    or an attribute that isn't such an iterable, counts as nothing declared. An optional method the adapter doesn't
    define counts as declared too (ADR 0160).
    """
    absent = frozenset(name for name in capability.optional if not callable(getattr(adapter, name, None)))
    declared: object = getattr(adapter, "unsupported", ())
    if isinstance(declared, str | bytes) or not isinstance(declared, Iterable):
        return absent
    return absent | frozenset(name for name in cast("Iterable[object]", declared) if name in capability.methods)  # type: ignore[misc]


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
