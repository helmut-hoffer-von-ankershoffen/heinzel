"""What heads may use of the lifecycle context: web supervision and the restart verification.

Heads import this file, never a private one (the "Heads only know what the kernel discovered" contract). The package
`__init__` stays lazy for the POSIX bootstrap; this file imports eagerly, as the heads did before.
"""

from heinzel.modules.system.lifecycle._supervision import (
    SUPERVISED_LOG,
    SupervisedWeb,
    WebPublication,
    redirect_supervised_log,
    restart_when_settled,
)
from heinzel.modules.system.lifecycle._verify import (
    ExpectedLifecycle,
    Lifecycle,
    LifecycleVerified,
    Restart,
    loaded_settings,
    mismatches,
    requires_owner_session,
)

__all__ = [
    "SUPERVISED_LOG",
    "ExpectedLifecycle",
    "Lifecycle",
    "LifecycleVerified",
    "Restart",
    "SupervisedWeb",
    "WebPublication",
    "loaded_settings",
    "mismatches",
    "redirect_supervised_log",
    "requires_owner_session",
    "restart_when_settled",
]
