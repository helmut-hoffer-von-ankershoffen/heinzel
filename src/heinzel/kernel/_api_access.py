"""Host-owned configured API identity and one-use provider construction contract."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConfiguredApiSnapshot:
    """Secret-free selected API configuration; the fingerprint is not a provider-account identity."""

    owner: str
    access: str
    model: str
    endpoint: str
    credential_fingerprint: str


class ConfiguredApiLease:
    """Private one-use lease; a provider must use the credential captured in the snapshot."""

    @property
    def snapshot(self) -> ConfiguredApiSnapshot:
        """Secret-free identity of the captured credential."""
        raise NotImplementedError

    def discard(self) -> None:
        """Drop the captured credential without constructing a provider."""
        raise NotImplementedError

    def build_approved(self, approved: ConfiguredApiSnapshot) -> Any:  # noqa: ANN401
        """Construct at most once, only for the exact approved snapshot."""
        raise NotImplementedError


class ConfiguredApiAccess:
    """Trusted host service for the already-selected, owner-bound configured API."""

    def capture(self) -> ConfiguredApiSnapshot:
        """Capture a secret-free identity without a provider call."""
        raise NotImplementedError

    def recheck(self, snapshot: ConfiguredApiSnapshot) -> None:
        """Reject if the current selected access or captured identity changed."""
        raise NotImplementedError

    def lease(self) -> ConfiguredApiLease:
        """Capture the credential once for an exact approved provider build."""
        raise NotImplementedError
