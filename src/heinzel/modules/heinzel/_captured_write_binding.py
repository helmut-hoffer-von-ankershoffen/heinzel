"""Provider selection for reviewed conditional writes; a selection is continuity, never authority."""

import hashlib
from pathlib import Path

from heinzel import Config, HeinzelError, Settings
from heinzel.kernel import (
    CapabilitySettings,
    CapturedWriteAcknowledgement,
    CapturedWriteDraft,
    CapturedWriteLease,
    CapturedWriteReview,
    CapturedWriteService,
    CapturedWriteTarget,
    Kernel,
)

OPERATION = "insert-draft"


class SelectedWriteService(CapturedWriteService):
    """Hold the selected provider record and recheck configuration before every lease action.

    A source-captured provider runs under the same continuity checks as its reads. This verifies registration
    continuity, not publisher identity, and it is not a sandbox.
    """

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel
        self.record, self.registration = kernel.selected_capability_provider("documents")
        self.binding_id = hashlib.sha256(
            f"{self.record.qualname}\0{self.record.origin.distribution}\0{self.record.origin.version}".encode()
        ).hexdigest()
        self.service: CapturedWriteService | None = None

    def _resolved(self) -> CapturedWriteService:
        config = Config.load(Path.home())
        current = config.settings(CapabilitySettings, "capabilities")
        original = self.kernel.capability_settings
        record, registration = self.kernel.selected_capability_provider("documents")
        if (
            current.bound("documents") != original.bound("documents")
            or current.configured("documents") != original.configured("documents")
            or current.defaulted("documents") != original.defaulted("documents")
            or config.settings(Settings, "hz").disabled_modules != self.kernel.settings.disabled_modules
            or record is not self.record
            or registration is not self.registration
        ):
            msg = "The selected document provider or write binding changed"
            raise HeinzelError(msg)
        discovery = self.kernel.source_discovery
        if discovery is not None:
            binding = discovery.binding(self.kernel, "documents", CapturedWriteService)
            service = binding.capture.service(self.kernel, self.record, CapturedWriteService)
        else:
            _, service = self.kernel.selected_capability_service(CapturedWriteService, "documents")
        if self.service is not None and service is not self.service:
            msg = "The selected document provider service changed"
            raise HeinzelError(msg)
        self.service = service
        return service

    def check(self, draft: CapturedWriteDraft) -> None:
        self._resolved()
        if (
            draft.provider != self.record.qualname
            or draft.binding_id != self.binding_id
            or draft.operation != OPERATION
        ):
            msg = "The selected document provider or write binding changed"
            raise HeinzelError(msg)

    def check_available(self) -> None:
        """Whether the selected provider can write at all.

        Raises:
            HeinzelError: If it has no write service.
        """
        if type(self._resolved()).target is CapturedWriteService.target:
            msg = "This provider does not support reviewed conditional writes"
            raise HeinzelError(msg)

    def target(self, reference: str) -> CapturedWriteTarget:
        """Observe where a draft would go; this writes nothing and needs no approval."""
        return self._resolved().target(reference)

    def open_lease(self, draft: CapturedWriteDraft) -> CapturedWriteLease:
        """Open only an exact currently selected provider; the lease itself is not authority.

        Raises:
            HeinzelError: If selected provider continuity cannot be established.
        """
        self.check(draft)
        service = self.service
        if service is None:
            msg = "The selected document provider service is unavailable"
            raise HeinzelError(msg)
        lease = service.open_lease(draft)
        return _SelectedLease(self, draft, lease)


class _SelectedLease(CapturedWriteLease):
    def __init__(self, selected: SelectedWriteService, draft: CapturedWriteDraft, lease: CapturedWriteLease) -> None:
        self.selected, self.draft, self.lease = selected, draft, lease

    def review(self) -> CapturedWriteReview:
        self.selected.check(self.draft)
        result = self.lease.review()
        self.selected.check(self.draft)
        return result

    def write(self) -> CapturedWriteAcknowledgement:
        self.selected.check(self.draft)
        result = self.lease.write()
        self.selected.check(self.draft)
        return result

    def close(self) -> None:
        self.lease.close()
        self.selected.check(self.draft)
