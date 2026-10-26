"""Read-only facts supplied by a local app server; no listener, credential or restart authority."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocalWebRuntime:
    """The app server's actual endpoint and loaded origin policy; absent when this process hosts no web app.

    Heads bind this value when constructing their server. Plugins may read it without importing a head, guessing
    its port from configuration or receiving a bearer credential. It proves construction state only; a client must
    still verify the serving instance before advertising external readiness.
    """

    port: int | None = None
    loaded_origins: frozenset[str] = frozenset()
