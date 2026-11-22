"""One lock-held daemon mutation and the held plan of an owned daemon's removal."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from heinzel import HeinzelError
from heinzel.kernel import DaemonHandoverPlan, DaemonHandoverRequest
from heinzel.kernel.install._tool_uninstall import OwnedFile


@dataclass(frozen=True, slots=True)
class MutationAuthority:
    pending_path: Path
    token_matches: Callable[[str], bool]
    capture: Callable[[], DaemonHandoverPlan]
    request: Callable[[DaemonHandoverPlan], DaemonHandoverRequest]
    fingerprint: Callable[[], str | None] | None
    checkpoint: Callable[[], bool]
    had_checkpoint: bool
    original_fingerprint: str
    original_identity: str
    rollback_identity: Callable[[], str]


class MutationScope:
    """One lock-held mutation; only trusted host callers can supply checkpoint evidence."""

    def __init__(self, token: str, authority: MutationAuthority) -> None:
        self.token = token
        self.authority = authority
        self.active = True
        self.plan: DaemonHandoverPlan | None = None
        self.expected: str | None = None
        self.requested = False
        self.aborted = False

    @property
    def checkpoint_matched(self) -> bool:
        self._require_active()
        return self.authority.had_checkpoint

    def _require_active(self) -> None:
        if not self.active or not self.authority.token_matches(self.token):
            msg = "The daemon mutation scope is unavailable or its pending token changed."
            raise HeinzelError(msg)

    def capture(self) -> DaemonHandoverPlan:
        self._require_active()
        if self.plan is not None:
            msg = "The daemon mutation state was already captured."
            raise HeinzelError(msg)
        self.plan = self.authority.capture()
        return self.plan

    def verified_complete(self, expected: str) -> None:
        self._require_active()
        if self.plan is None or self.expected is not None or not re.fullmatch(r"[a-f0-9]{64}", expected):
            msg = "The verified daemon mutation generation is invalid or already recorded."
            raise HeinzelError(msg)
        self._verify(expected)
        self.expected = expected

    def _verify(self, expected: str) -> None:
        fingerprint = self.authority.fingerprint
        if (
            fingerprint is None
            or fingerprint() != expected
            or self.authority.checkpoint() is not self.authority.had_checkpoint
            or fingerprint() != expected
        ):
            msg = "The installation generation or its durable checkpoint could not be verified."
            raise HeinzelError(msg)
        self._require_active()

    def abort_unchanged(self) -> None:
        self._require_active()
        if self.plan is None or self.expected is not None or self.requested:
            msg = "The mutation cannot be aborted after verification or handover."
            raise HeinzelError(msg)
        self._verify(self.authority.original_fingerprint)
        if self.authority.rollback_identity() != self.authority.original_identity:
            msg = "The installation identity changed; explicit recovery is required."
            raise HeinzelError(msg)
        self._require_active()
        self.authority.pending_path.unlink()
        self.aborted = True
        self.active = False

    def request(self, plan: DaemonHandoverPlan) -> DaemonHandoverRequest:
        self._require_active()
        if self.expected is None or self.plan is not plan or self.requested or self.aborted:
            msg = "The daemon handover does not match this verified mutation."
            raise HeinzelError(msg)
        self._verify(self.expected)
        result = self.authority.request(plan)
        if plan.active and not result.requested:
            msg = "The captured active daemon could not be handed over; mutation recovery is required."
            raise HeinzelError(msg)
        self.requested = True
        return result

    def finish_without_handover(self) -> None:
        self._require_active()
        if self.expected is None or self.requested or self.aborted:
            msg = "The mutation was not verified, or it was already handed over."
            raise HeinzelError(msg)
        self._verify(self.expected)
        self.authority.pending_path.unlink()
        self.aborted = True  # nothing is left to commit; the daemon keeps running what it loaded

    def commit(self) -> None:
        if self.aborted:
            return
        self._require_active()
        if self.expected is None or not self.requested:
            msg = "The mutation was not verified and handed over; explicit recovery is required."
            raise HeinzelError(msg)
        self._verify(self.expected)
        self.authority.pending_path.unlink()


@dataclass(frozen=True)
class DaemonRemoval:
    """One held owned daemon plan; its durable coordinator controls stop and final cleanup."""

    files: tuple[OwnedFile, ...]
    stop: Callable[[str], None]
    finish: Callable[[], None]
    manager_units: dict[str, dict[str, object]]
