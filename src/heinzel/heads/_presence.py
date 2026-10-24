"""Every head notes that hz was used today before it runs an operation for someone (ADR 0062).

The daemon head doesn't: the OS starts its ticks, nobody used hz. What is kept, and that it never costs an
operation, is the `Presence` provider's business; this only makes sure a failure there never reaches the caller.
"""

import logging

from heinzel.kernel import Kernel, Presence

log = logging.getLogger(__name__)


def mark_presence(kernel: Kernel, module: str) -> None:
    """Tell the kernel's `Presence`, if a module provides one, that an operation of `module` runs now."""
    if not kernel.provides(Presence):
        return
    try:
        kernel.resolve(Presence).mark(module)
    except Exception:
        log.debug("noting presence failed", exc_info=True)
