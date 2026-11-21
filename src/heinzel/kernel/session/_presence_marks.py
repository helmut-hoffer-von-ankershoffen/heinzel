"""Every head notes that hz was used today before it runs an operation for someone (ADR 0081).

The daemon head doesn't: the OS starts its ticks, nobody used hz. What is kept, and that it never costs an
operation, is the `Presence` provider's business; this only makes sure a failure there never reaches the caller.
In the kernel so that a Heinzel's direct tools (ADR 0162) note it exactly as the heads do.
"""

import logging

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._identity import CurrentUser
from heinzel.kernel._kernel import Kernel
from heinzel.kernel.session._presence import Presence

log = logging.getLogger(__name__)


def mark_presence(kernel: Kernel, module: str) -> None:
    """Tell the kernel's `Presence`, if a module provides one, that an operation of `module` runs now."""
    if not kernel.provides(Presence):
        return
    try:
        kernel.resolve(Presence).mark(module)
    except Exception:
        log.debug("noting presence failed", exc_info=True)


def mark_authenticated_presence(kernel: Kernel, module: str) -> None:
    """Note activity only when a trusted head authenticated the caller, excluding daemon contexts."""
    try:
        identity = kernel.resolve(CurrentUser).context()
    except HeinzelError:
        return
    if identity.authentication != "daemon":
        mark_presence(kernel, module)
