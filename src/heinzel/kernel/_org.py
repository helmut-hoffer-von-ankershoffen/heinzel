"""Where the person's team repository is checked out, and what it recommends: what modules ask for by type.

A team keeps its shared state in a Git repository: roster, people files, team contracts, policies. One module knows
which team a person joined and where its checkout is, and provides it as an `OrgContext`; others read it, so that
`workflow` finds `team:` contracts without knowing how a person joined. The kernel knows neither Git nor joining.

A team repository may recommend plugins, capability bindings, credential names and contracts (proposed onboarding
ADR). A recommendation is shown with its origin and applied only when the person accepts it, to their own user
configuration (`hz org sync`): nothing in a repository binds, installs, stores or schedules anything by itself.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RecommendationKind = Literal["plugin", "binding", "credential", "contract"]


@dataclass(frozen=True, kw_only=True, slots=True)
class Recommendation:
    """One thing a team repository recommends to its members."""

    kind: RecommendationKind
    name: str
    """A plugin distribution, a capability, a credential as `service/name`, or a team contract's name."""
    value: str | None = None
    """The adapter a binding names, or the `sha256:<hex>` of a contract's file; None for the other kinds."""
    origin: str
    """Where it comes from, for the person to judge, e.g. `team payments (https://...), hz.yaml`."""

    @property
    def id(self) -> str:
        """How the person accepts it: `<kind>:<name>`, e.g. `binding:documents`."""
        return f"{self.kind}:{self.name}"


class OrgContext:
    """The team a person belongs to; a module provides a subclass (the `team` module in the core).

    A base class rather than a protocol, so that the kernel can inject it by type like any other service. Only what
    readers need today is here. The team repository only
    holds data: nothing in it binds a capability, stores a credential, installs a plugin or schedules a contract.
    """

    def team_checkout(self) -> Path | None:
        """The local checkout of the person's team repository; None when they haven't joined one.

        Raises:
            NotImplementedError: Always; subclasses read the person's configuration.
        """
        raise NotImplementedError

    def recommendations(self) -> tuple[Recommendation, ...]:
        """What the person's team repository recommends; nothing by default."""
        return ()

    def layer(self) -> str | None:
        """The layer the person's team declares (ADR 0040), by its module name or its plugin's name.

        E.g. `acme-payments-checkout` or `heinzel-acme-payments-checkout`; the kernel stacks the installed
        layers from it (`Kernel.layer_stack`). None, the default, when nothing
        declares one; the kernel then takes the only team layer installed.

        Raises:
            HeinzelError: If the team's declaration can't be read; the kernel reports it and stacks what is installed.
        """
        return None


@dataclass(frozen=True, kw_only=True, slots=True)
class TeamPromise:
    """What one team contract promises the team: the artifact it keeps, what it reads, and when (ADR 0054).

    The part of a team contract every member sees and holds it to; its body, its drafts and how its user runs it stay
    with that user.
    """

    contract: str
    """Its qualified name, `team:<name>`."""
    title: str
    version: str
    """The author's `version` when it has one, else the file's `sha256:<hex>`."""
    schedule: str | None
    """Five cron fields in the runner's local time, or a shortcut such as `@daily`; None when it runs only by hand."""
    writes: str
    """The artifact it keeps true, as inputs name it: `contract:team/<name>#<output>`."""
    reads: tuple[str, ...] = ()
    """The team artifacts it reads, as `contract:team/<name>#<output>`; capability reads are not team artifacts."""
    publishes: tuple[str, ...] = ()
    """The publish operations its draft is meant for, `<module>.<operation>`."""


class TeamPromises:
    """The promises your team's contracts make: a module provides a subclass (`workflow` in the core)."""

    def promises(self) -> tuple[TeamPromise, ...]:
        """Every valid team contract's promise, by contract name; nothing without a team.

        Raises:
            NotImplementedError: Always; subclasses read the team's contracts.
        """
        raise NotImplementedError
