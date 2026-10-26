"""Only an authenticated owning web instance can confirm its bound listener and loaded origin policy."""

from heinzel import CurrentUser, HeinzelError, LocalWebRuntime
from heinzel_tailscale._mapping import Mapping


class SharingReadiness:
    """The existing call identity plus tokenless in-process facts; no new credential or discovery registry."""

    def __init__(self, runtime: LocalWebRuntime, user: CurrentUser) -> None:
        """Use only facts supplied by the trusted head and its current authenticated request."""
        self.runtime = runtime
        self.user = user

    def verifies(self, mapping: Mapping) -> bool:
        """Whether this authenticated web instance actually loaded this listener and exact external origin."""
        if self.runtime.port != mapping.backend_port or mapping.origin not in self.runtime.loaded_origins:
            return False
        try:
            return self.user.context().authentication in {"web-session", "web-device"}
        except HeinzelError:
            return False
