"""Public, ephemeral evidence for one workspace server, independent of pairing.

Import this submodule directly: an updating process can retain an older
``heinzel.kernel`` package in memory while loading this file from the new tree.
Keep it independent of kernel exports and discovery.
"""

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from threading import Lock
from typing import Literal
from urllib.parse import unquote

from pydantic import AwareDatetime, BaseModel, Field

FRESH_FOR = timedelta(hours=2)
RETAIN_FOR = timedelta(days=30)
MAX_OBSERVATIONS = 50
MAX_HEADER = 2400  # Three 64-scalar fields can each require twelve ASCII bytes per scalar.
MAX_FIELD = 64


class ClientLabels(BaseModel):
    """Bounded request claims, never authentication or authoritative platform."""

    flavor: Literal["native", "electron", "unknown"] = "unknown"
    machine: str = Field(default="unknown", max_length=MAX_FIELD)
    app_version: str = Field(default="unknown", max_length=MAX_FIELD)
    backend_version: str = Field(default="unknown", max_length=MAX_FIELD)


def parse_client_labels(headers: Mapping[str, str]) -> ClientLabels | None:
    """Read the historical desktop header grammar, without guessing from user agents."""
    text = headers.get("x-heinzel-client", "")
    if not text or len(text) > MAX_HEADER:
        return None
    values: dict[str, str] = {}
    seen: set[str] = set()
    for part in text.split(";"):
        key, separator, raw = part.partition("=")
        value = unquote(raw.strip())
        if key in seen:
            return None
        seen.add(key)
        folded = value.casefold()
        # Do not reflect credential-shaped values, controls or markup into readouts.
        if (
            separator
            and re.fullmatch(r"[\w .()'\u2019+-]{1,64}", value)
            and not any(marker in folded for marker in ("hzd_", "hzw_", "bearer"))
            and not re.search(r"[a-z0-9_-]{32,}", value, re.IGNORECASE)
        ):
            values[key] = value
    if values.get("flavor") not in {"native", "electron"}:
        return None
    return ClientLabels(
        flavor="native" if values["flavor"] == "native" else "electron",
        machine=values.get("machine", "unknown"),
        app_version=values.get("app", "unknown"),
        backend_version=values.get("backend", "unknown"),
    )


class ClientObservation(BaseModel):
    """Recent request evidence; neither a live connection nor a credential inventory."""

    labels: ClientLabels
    label_provenance: Literal["self-reported"] = "self-reported"
    identity_provenance: Literal["device-credential", "session-observation"]
    device_id: str | None = None
    platform: Literal["unknown"] = "unknown"
    last_seen: AwareDatetime
    age_seconds: int = Field(ge=0)
    presence: Literal["fresh", "stale"]


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
        """Start with no observed client activity."""
        self._latest: datetime | None = None
        self._observations: dict[tuple[str, ...], tuple[ClientLabels, str | None, datetime]] = {}
        self._lock = Lock()

    def observe(self, now: datetime) -> None:
        """Record only after the head has authenticated a successful workspace request."""
        with self._lock:
            self._latest = now

    def observe_labels(self, now: datetime, labels: ClientLabels, device_id: str | None = None) -> None:
        """Group by validated device ID when available; session label collisions are intentional."""
        key = ("device", device_id) if device_id else ("session", labels.flavor, labels.machine)
        with self._lock:
            self._prune(now)
            self._observations.pop(key, None)
            self._observations[key] = (labels.model_copy(), device_id, now)
            while len(self._observations) > MAX_OBSERVATIONS:
                del self._observations[next(iter(self._observations))]

    def _prune(self, now: datetime) -> None:
        self._observations = {
            key: value for key, value in self._observations.items() if timedelta(0) <= now - value[2] < RETAIN_FOR
        }

    def observations(self, now: datetime) -> list[ClientObservation]:
        """Read bounded, expiring process-local observations with explicit age and provenance."""
        with self._lock:
            self._prune(now)
            return [
                ClientObservation(
                    labels=labels,
                    identity_provenance="device-credential" if device_id else "session-observation",
                    device_id=device_id,
                    last_seen=seen,
                    age_seconds=int((now - seen).total_seconds()),
                    presence="fresh" if now - seen <= FRESH_FOR else "stale",
                )
                for labels, device_id, seen in sorted(
                    self._observations.values(), key=lambda item: item[2], reverse=True
                )
            ]

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
