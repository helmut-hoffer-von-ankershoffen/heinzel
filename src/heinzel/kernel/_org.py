"""Where the person's team repository is checked out: what modules ask for by type (proposed onboarding ADR).

A team keeps its shared state in a Git repository: roster, people files, team contracts, policies. One module knows
which team a person joined and where its checkout is, and provides it as an `OrgContext`; others read it, so that
`workflow` finds `team:` contracts without knowing how a person joined. The kernel knows neither Git nor joining.
"""

from pathlib import Path


class OrgContext:
    """The team a person belongs to; a module provides a subclass (the `team` module in the core).

    A base class rather than a protocol, so that the kernel can inject it by type like any other service. Only what
    readers need today is here; org, company and site layers add methods when they exist. The team repository only
    holds data: nothing in it binds a capability, stores a credential, installs a plugin or schedules a contract.
    """

    def team_checkout(self) -> Path | None:
        """The local checkout of the person's team repository; None when they haven't joined one.

        Raises:
            NotImplementedError: Always; subclasses read the person's configuration.
        """
        raise NotImplementedError
