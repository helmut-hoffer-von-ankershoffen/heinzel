"""Acme, a made-up company: the root layer of the test tree (ADR 0040). It brings an adapter and recommends it."""

from heinzel import Module

module = Module("acme", "Acme's adapters and defaults: a made-up company for tests.")
module.layer()


@module.provides("tracker", modes={"read"})
def board() -> object:
    """Acme's tracker; never built in these tests."""
    return object()


module.playbook(
    "How Acme works: its tracker and calendar.",
    defaults={"capabilities.tracker": "acme", "capabilities.calendar": "acme"},
)
