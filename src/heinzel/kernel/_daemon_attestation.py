"""Read-only daemon status and separate trusted installation handover contracts."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


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


@dataclass(frozen=True, slots=True)
class DaemonHandoverPlan:
    """Opaque pre-change state, not a running-process or code attestation."""

    active: bool
    platform: str
    unit_signature: str | None


@dataclass(frozen=True, slots=True)
class DaemonHandoverRequest:
    """Manager request result; acceptance is not an execution observation."""

    requested: bool
    reason: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class DaemonHandoverObservation:
    """Manager-bound local tick metadata, not publisher or executable-code attestation."""

    request_id: str
    version: str
    fingerprint: str
    contributions: tuple[tuple[str, str, str], ...]
    tick_at: str
    old_unit_signature: str
    new_unit_signature: str


@dataclass(frozen=True, slots=True)
class DaemonHandoverAssessment:
    """A caller's explicit target compared with a fresh local tick observation."""

    status: Literal["pending", "matched", "mismatched"]
    observed: DaemonHandoverObservation | None


class DaemonMutationScope(Protocol):
    """Trusted, operation-scoped lifecycle mutation; it is not a human authorization."""

    @property
    def checkpoint_matched(self) -> bool:
        """Whether a continuity checkpoint matched before the mutation."""
        ...

    def capture(self) -> DaemonHandoverPlan:
        """Capture the pre-change daemon state once."""
        ...

    def verified_complete(self, expected: str) -> None:
        """Validate the caller's independently checked generation and durable checkpoint."""
        ...

    def abort_unchanged(self) -> None:
        """Finish without handover only when the original local identity remains unchanged."""
        ...

    def request(self, plan: DaemonHandoverPlan) -> DaemonHandoverRequest:
        """Request handover of the captured active service without reacquiring the lock."""
        ...


@dataclass(frozen=True, slots=True)
class DaemonInstallationLifecycle:
    """Trusted installer boundary; callers capture before mutation and request handover only after verification."""

    capture: Callable[[], DaemonHandoverPlan]
    request: Callable[[DaemonHandoverPlan], DaemonHandoverRequest]
    observation: Callable[[str], DaemonHandoverObservation | None]
    assess: Callable[[str, str, str, tuple[tuple[str, str, str], ...]], DaemonHandoverAssessment]
    admission: Callable[[], AbstractContextManager[None]]
    quiescent: Callable[[Path], AbstractContextManager[None]]
    recover_quiescent: Callable[[Path], None]
    mutation: Callable[[Callable[[], bool], Callable[[], str]], AbstractContextManager[DaemonMutationScope]]
