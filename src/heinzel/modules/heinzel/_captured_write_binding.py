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
from heinzel.kernel.ai._features import feature_enabled

OPERATION = "insert-draft"
NEW_FILE = "new:"
"""How a proposed new file's reference starts; it names no existing document, so the default connector creates it."""


class SelectedWriteService(CapturedWriteService):
    """Hold the selected provider record and recheck configuration before every lease action.

    A source-captured provider runs under the same continuity checks as its reads. This verifies registration
    continuity, not publisher identity, and it is not a sandbox.

    While several documents connectors are on (ADR 0161), `adapter` names the one this service writes through, as
    `Kernel.capability_route` chose it for a document; None is the default. `owning` finds it for a reference.
    """

    def __init__(self, kernel: Kernel, adapter: str | None = None) -> None:
        self.kernel = kernel
        self.adapter = adapter
        self.record, self.registration = kernel.selected_capability_provider("documents", adapter)
        self.service: CapturedWriteService | None = None
        self.binding_id = self._binding_id()

    @staticmethod
    def _operations(service: CapturedWriteService) -> frozenset[str]:
        # An inherited/instance attribute or mutable collection is not an explicit implementation declaration.
        operations = vars(type(service)).get("supported_operations")
        if (
            type(operations) is not frozenset
            or operations != frozenset({OPERATION})
            or "supported_operations" in vars(service)
        ):
            msg = "The selected provider has no supported captured-write declaration"
            raise HeinzelError(msg)
        return frozenset({OPERATION})

    def _binding_id(self) -> str:
        discovery = self.kernel.source_discovery
        if discovery is None:
            msg = "Conditional writes require an installed source-bound provider"
            raise HeinzelError(msg)
        binding = discovery.binding(self.kernel, "documents", CapturedWriteService, self.adapter)
        capture = binding.capture
        service = capture.service(self.kernel, self.record, CapturedWriteService)
        operations = self._operations(service)
        if self.service is not None and service is not self.service:
            msg = "The selected document provider service changed"
            raise HeinzelError(msg)
        self.service = service
        identity = (
            "documents",
            type(service).__module__,
            type(service).__qualname__,
            *sorted(operations),
            self.record.qualname,
            self.record.origin.distribution or "",
            self.record.origin.version or "",
            str(capture.finder.snapshot.root),
            capture.finder.snapshot.digest(),
            *sorted(capture.approved_runtime),
        )
        bound = self.kernel.capability_settings.bound_all("documents")
        if len(bound) > 1:
            # Which connector holds the document, and which are on, are part of what the owner approves (ADR 0161).
            identity = (*identity, "adapter", self.registration.adapter, "bound", *bound)
        return hashlib.sha256("\0".join(identity).encode()).hexdigest()

    def owning(self, reference: str) -> "SelectedWriteService":
        """The write service of the documents connector that holds `reference`, routed as its reads are (ADR 0161).

        A proposed new file names no document, so the default connector creates it.

        Raises:
            HeinzelError: If the reference names none or several connectors, or the one holding it can't write.
        """
        adapter = (
            None
            if reference.startswith(NEW_FILE)
            else self.kernel.capability_route("documents", "replace_section", {"ref": reference})
        )
        if adapter is None and self.adapter is None:
            return self
        if adapter is not None and adapter == self.registration.adapter:
            return self
        try:
            owner = SelectedWriteService(self.kernel, adapter)
            owner.check_available()
        except HeinzelError:
            name = adapter or self.kernel.capability_settings.bound("documents") or "documents"
            msg = f"The {name} connector holds this document and can't take reviewed writes"
            raise HeinzelError(msg) from None
        return owner

    def _resolved(self) -> CapturedWriteService:
        config = Config.load(Path.home())
        if not feature_enabled(self.kernel.settings, "captured_writes") or not feature_enabled(
            config.settings(Settings, "hz"), "captured_writes"
        ):
            msg = "Captured writes are not enabled"
            raise HeinzelError(msg)
        current = config.settings(CapabilitySettings, "capabilities")
        original = self.kernel.capability_settings
        record, registration = self.kernel.selected_capability_provider("documents", self.adapter)
        if (
            current.bound_all("documents") != original.bound_all("documents")
            or current.configured("documents") != original.configured("documents")
            or current.defaulted("documents") != original.defaulted("documents")
            or config.settings(Settings, "hz").disabled_modules != self.kernel.settings.disabled_modules
            or record is not self.record
            or registration is not self.registration
            or self._binding_id() != self.binding_id
        ):
            msg = "The selected document provider or write binding changed"
            raise HeinzelError(msg)
        discovery = self.kernel.source_discovery
        if discovery is not None:
            binding = discovery.binding(self.kernel, "documents", CapturedWriteService, self.adapter)
            service = binding.capture.service(self.kernel, self.record, CapturedWriteService)
        else:
            msg = "Conditional writes require an installed source-bound provider"
            raise HeinzelError(msg)
        if self.service is not None and service is not self.service:
            msg = "The selected document provider service changed"
            raise HeinzelError(msg)
        self._operations(service)
        self.service = service
        return service

    def check(self, draft: CapturedWriteDraft) -> None:
        """Whether `draft` still goes to this exact provider and binding.

        Raises:
            HeinzelError: If the provider, binding or connector holding the draft's document changed.
        """
        if self.owning(draft.reference) is not self:
            msg = "The selected document provider or write binding changed"
            raise HeinzelError(msg)
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
        """Observe where a draft would go; this writes nothing and needs no approval.

        Raises:
            HeinzelError: If selected source, declaration, configuration or service continuity changes, or another
                connector holds `reference`.
        """
        if self.owning(reference) is not self:
            msg = "Another documents connector holds this document"
            raise HeinzelError(msg)
        service = self._resolved()
        result = service.target(reference)
        if self._resolved() is not service:
            msg = "The selected document provider service changed"
            raise HeinzelError(msg)
        return CapturedWriteTarget.model_validate_json(result.model_dump_json())

    def open_lease(self, draft: CapturedWriteDraft) -> CapturedWriteLease:
        """Open only an exact currently selected provider; the lease itself is not authority.

        Raises:
            HeinzelError: If selected provider continuity cannot be established.
        """
        owner = self.owning(draft.reference)
        if owner is not self:
            # The connector holding the document now writes it; its binding must be the one the owner approved.
            return owner.open_lease(draft)
        self.check(draft)
        service = self.service
        if service is None:
            msg = "The selected document provider service is unavailable"
            raise HeinzelError(msg)
        lease = service.open_lease(draft)
        try:
            self.check(draft)
        except BaseException:
            lease.close()
            raise
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
