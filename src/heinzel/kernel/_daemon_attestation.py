"""The service-managed daemon version observed by the latest tick."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class DaemonStatusAttestation(Protocol):
    """The version facts another module may check before enabling durable work."""

    installed: bool
    version: str | None
    installed_version: str | None
    version_mismatch: bool


@dataclass(frozen=True, slots=True)
class DaemonAttestation:
    """Read-only daemon status accessor shared across module boundaries."""

    status: Callable[[], DaemonStatusAttestation]
