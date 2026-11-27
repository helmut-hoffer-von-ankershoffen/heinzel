"""Ephemeral aggregate evidence for one workspace server, independent of pairing."""

from datetime import datetime, timedelta
from threading import Lock
from typing import Literal

from pydantic import BaseModel

FRESH_FOR = timedelta(hours=2)
RETAIN_FOR = timedelta(days=30)


class RecentClient(BaseModel):
    """One aggregate time band; no credential identities, counts or event timestamps."""

    kind: Literal["unknown"] = "unknown"
    platform: Literal["unknown"] = "unknown"
    presence: Literal["fresh", "stale"]


class RecentUse:
    """One server lifetime's latest authenticated API use, held only in memory.

    Names, user agents and arbitrary headers cannot establish trusted platform or flavor.
    No per-client history is retained. Restarting the server clears the observation.
    """

    def __init__(self) -> None:
        self._latest: datetime | None = None
        self._lock = Lock()

    def observe(self, now: datetime) -> None:
        """Record only after the head has authenticated a successful workspace request."""
        with self._lock:
            self._latest = now

    def recent(self, now: datetime) -> list[RecentClient]:
        """Expose a coarse aggregate; missing, future and expired evidence is absent."""
        with self._lock:
            latest = self._latest
        if latest is None or not timedelta(0) <= now - latest < RETAIN_FOR:
            return []
        return [RecentClient(presence="fresh" if now - latest <= FRESH_FOR else "stale")]


def recent_clients(observations: RecentUse, now: datetime) -> list[RecentClient]:
    """Read this server's aggregate observation, never its credential registry."""
    return observations.recent(now)
