"""Presence: that a person used hz on a day, on this machine (ADR 0062).

Heads note it when they run an operation for someone; the module that keeps the state streams (`system.state` in
the core) turns it into one line per local day, which its daemon job pushes to the person's own state repository.
The kernel holds only the base class, so heads can ask for it by type without knowing that module.
"""


class Presence:
    """Notes that hz was used today on this machine; a module provides a subclass (`system.state` in the core).

    A base class rather than a protocol, so the kernel injects it by type like `TelemetryLog`. Nothing finer than a
    day is ever kept: no time, no count, no command.
    """

    def mark(self, module: str) -> None:
        """Note that an operation of `module` (its qualified name) ran for someone today; nothing by default.

        Implementations are cheap, write at most once a day and never raise: a head calls this before every
        operation, and presence must never cost one.
        """
        del module
