"""Read-only facts supplied by a local app server; no listener, credential or restart authority."""

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock


@dataclass(frozen=True, slots=True)
class LocalWebRuntime:
    """The app server's actual endpoint and active origin policy; absent when this process hosts no web app.

    Heads bind this value when constructing their server. Plugins may read it without importing a head, guessing
    its port from configuration or receiving a bearer credential. It proves construction state only; a client must
    still verify the serving instance before advertising external readiness. The origin set can be replaced by an
    explicit owner-authorized operation such as the Tailscale sharing toggle; each replacement is a complete,
    validated immutable set, so requests observe either the old or the new policy.
    """

    port: int | None = None
    loaded_origins: frozenset[str] = frozenset()
    _on_origins_removed: Callable[[frozenset[str]], None] | None = field(default=None, repr=False, compare=False)
    _origin_lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def apply_origins(self, origins: frozenset[str]) -> None:
        """Atomically replace the active policy with a fully validated origin set."""
        from heinzel.kernel._settings import Settings  # noqa: PLC0415 - avoids a kernel package import cycle

        validated = Settings.model_validate({"ui_allowed_origins": origins}).ui_allowed_origins
        with self._origin_lock:
            removed = self.loaded_origins - validated
            object.__setattr__(self, "loaded_origins", validated)
            if removed and self._on_origins_removed is not None:
                self._on_origins_removed(removed)

    def register_if_origin_allowed(self, origin: str, register: Callable[[], None]) -> bool:
        """Register an external session atomically with the current origin policy replacement."""
        with self._origin_lock:
            if origin not in self.loaded_origins:
                return False
            register()
            return True
