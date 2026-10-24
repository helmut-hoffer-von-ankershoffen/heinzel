"""Pluggy hook specifications. Modules implement them; most module authors never need to know."""

from collections.abc import Sequence

import pluggy

from heinzel.kernel._lets import Let

PROJECT_NAME = "heinzel"

hookspec = pluggy.HookspecMarker(PROJECT_NAME)
hookimpl = pluggy.HookimplMarker(PROJECT_NAME)


class Spec:
    """Hooks the kernel calls on every registered module."""

    @hookspec
    def heinzel_lets(self) -> Sequence[Let]:
        """Return the lets the module advertises."""
        raise NotImplementedError
