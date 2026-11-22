"""Every head notes that hz was used today before it runs an operation for someone (ADR 0081).

The functions live in the kernel, so a Heinzel's direct tools (ADR 0162) note it exactly as the heads do.
"""

from heinzel.kernel.session._presence_marks import mark_authenticated_presence, mark_presence

__all__ = ["mark_authenticated_presence", "mark_presence"]
