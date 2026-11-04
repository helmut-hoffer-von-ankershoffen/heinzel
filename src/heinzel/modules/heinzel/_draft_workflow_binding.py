"""Exact captured SDK service bindings for the separately published draft workflow (ADR 0127)."""

import hashlib
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from heinzel import Config, HeinzelError, Kernel, NotificationChannel, ServiceLet, Settings
from heinzel.kernel import CapabilitySettings
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._features import feature_enabled
from heinzel.kernel._services import ServiceContract


class _Lease(Protocol):
    def close(self) -> None: ...


class DraftWorkflowBinding[T: ServiceContract]:
    """One selected captured service, including its explicit declaration and installed source/runtime identity."""

    def __init__(self, kernel: Kernel, capability: str, contract: type[T], operation: str) -> None:
        """Capture an exact selection without performing a provider action.

        Raises:
            HeinzelError: If no explicit installed source-bound implementation is available.
        """
        self.kernel, self.capability, self.contract, self.operation = kernel, capability, contract, operation
        self.record, self.registration = kernel.selected_capability_provider(capability)
        self.service: T | None = None
        self.digest = self._identity()

    def _identity(self) -> str:
        discovery = self.kernel.source_discovery
        if discovery is None:
            msg = "Draft workflows require installed source-bound providers"
            raise HeinzelError(msg)
        capture = discovery.binding(self.kernel, self.capability, self.contract).capture
        service = capture.service(self.kernel, self.record, self.contract)
        operations = vars(type(service)).get("supported_operations")
        if (
            type(operations) is not frozenset
            or operations != frozenset({self.operation})
            or "supported_operations" in vars(service)
            or (self.service is not None and service is not self.service)
        ):
            msg = "The draft workflow provider has no exact implementation declaration"
            raise HeinzelError(msg)
        self.service = service
        values = (
            self.capability,
            self.operation,
            self.contract.__module__,
            self.contract.__qualname__,
            type(service).__module__,
            type(service).__qualname__,
            self.record.qualname,
            self.record.origin.distribution or "",
            self.record.origin.version or "",
            str(capture.finder.snapshot.root),
            capture.finder.snapshot.digest(),
            *sorted(capture.approved_runtime),
        )
        return hashlib.sha256("\0".join(values).encode()).hexdigest()

    def current(self) -> T:
        """Recheck feature consent, configuration, registration and source before using a captured credential.

        Raises:
            HeinzelError: If any component of the selected source or runtime changed.
        """
        config = Config.load(Path.home())
        settings = config.settings(Settings, "hz")
        if not feature_enabled(self.kernel.settings, "connected_frontdoor") or not feature_enabled(
            settings, "connected_frontdoor"
        ):
            msg = "Connected draft workflows are not enabled"
            raise HeinzelError(msg)
        configured = config.settings(CapabilitySettings, "capabilities")
        original = self.kernel.capability_settings
        record, registration = self.kernel.selected_capability_provider(self.capability)
        if (
            configured.bound(self.capability) != original.bound(self.capability)
            or configured.configured(self.capability) != original.configured(self.capability)
            or configured.defaulted(self.capability) != original.defaulted(self.capability)
            or settings.disabled_modules != self.kernel.settings.disabled_modules
            or record is not self.record
            or registration is not self.registration
            or self._identity() != self.digest
            or self.service is None
        ):
            msg = "The captured draft workflow provider changed"
            raise HeinzelError(msg)
        return self.service

    def call[R](self, action: Callable[[T], R]) -> R:
        """Fence one provider observation with exact source checks.

        Raises:
            HeinzelError: If continuity is unavailable before or after the call.
        """
        try:
            result = action(self.current())
            self.current()
        except Exception:  # noqa: BLE001 - untrusted adapter failures may contain credentials or private content
            msg = "The captured provider call is unavailable"
            raise HeinzelError(msg) from None
        return result

    @contextmanager
    def lease[L: _Lease](self, open_lease: Callable[[T], L]) -> Generator[L]:
        """Always quiesce an opened captured lease, even after feature revocation or source drift.

        Yields:
            The exact captured lease, while its source remains current.

        Raises:
            HeinzelError: If continuity is lost; cleanup still executes first.
        """
        try:
            lease = open_lease(self.current())
        except Exception:  # noqa: BLE001 - opening an adapter credential may expose private diagnostic details
            msg = "The captured provider lease is unavailable"
            raise HeinzelError(msg) from None
        try:
            self.current()
            yield lease
        finally:
            # Closing must not be skipped merely because permission or source checks now fail.
            try:
                lease.close()
            except Exception:  # noqa: BLE001 - cleanup failure must not disclose provider credentials
                msg = "The captured provider lease did not close cleanly"
                raise HeinzelError(msg) from None
            finally:
                self.current()


class DraftChannelBinding[T: NotificationChannel]:
    """One exact source-captured workflow transport, distinct from another role in the same provider module."""

    def __init__(self, kernel: Kernel, name: str, contract: type[T], operation: str) -> None:
        """Refuse legacy transports and ambiguous names without executing any mutation.

        Raises:
            HeinzelError: If an exact registered captured implementation is unavailable.
        """
        self.kernel, self.name, self.contract, self.operation = kernel, name, contract, operation
        discovery = kernel.source_discovery
        if discovery is None:
            msg = "Draft notices require an installed source-bound channel"
            raise HeinzelError(msg)
        matches = []
        for record, let in kernel.lets(ServiceLet):
            if let.provides is contract or not issubclass(let.provides, contract):
                continue
            capture = discovery.service_capture(kernel, record, contract)
            service = capture.service(kernel, record, contract)
            if service.name == name:
                matches.append((record, capture, service))
        if len(matches) != 1:
            msg = "Choose one explicitly implemented draft workflow channel"
            raise HeinzelError(msg)
        self.record, self.capture, self.service = matches[0]
        self.digest = arguments_hash(
            [
                contract.__module__,
                contract.__qualname__,
                operation,
                self.record.qualname,
                self.record.origin.distribution,
                self.record.origin.version,
                str(self.capture.finder.snapshot.root),
                self.capture.finder.snapshot.digest(),
                ",".join(sorted(self.capture.approved_runtime)),
                type(self.service).__module__,
                type(self.service).__qualname__,
            ]
        )
        self.current()

    def current(self) -> T:
        """Recheck exact operation declaration, source, registration, name and current feature consent.

        Raises:
            HeinzelError: If any part of this source-bound transport changed.
        """
        config = Config.load(Path.home()).settings(Settings, "hz")
        declaration = vars(type(self.service)).get("supported_operations")
        if (
            not all(
                feature_enabled(settings, feature)
                for settings in (self.kernel.settings, config)
                for feature in ("connected_frontdoor", "connected_channels")
            )
            or config.disabled_modules != self.kernel.settings.disabled_modules
            or type(declaration) is not frozenset
            or declaration != frozenset({self.operation})
            or "supported_operations" in vars(self.service)
            or self.capture.service(self.kernel, self.record, self.contract) is not self.service
            or self.service.name != self.name
        ):
            msg = "The draft workflow channel consent or captured implementation changed"
            raise HeinzelError(msg)
        return self.service

    def call[R](self, action: Callable[[T], R]) -> R:
        """Fence one exact transport observation or effect with source checks.

        Raises:
            HeinzelError: If source or registration continuity changed.
        """
        try:
            result = action(self.current())
            self.current()
        except Exception:  # noqa: BLE001 - untrusted adapter failures may contain credentials or private content
            msg = "The captured provider call is unavailable"
            raise HeinzelError(msg) from None
        return result
