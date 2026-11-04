"""Source-bound ordinary channel services; registration and destination continuity are not human consent (ADR 0120)."""

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from heinzel import (
    ApprovalChannel,
    ChannelTarget,
    Config,
    HeinzelError,
    Kernel,
    NotificationChannel,
    ServiceLet,
    Settings,
)
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._features import feature_enabled

if TYPE_CHECKING:
    from heinzel.kernel import ModuleRecord
    from heinzel.kernel._source_loading import CapturedEntry


class ChannelBinding:
    """One exact captured transport, revalidated before and after each transport observation or effect."""

    def __init__(self, kernel: Kernel, name: str, kind: str) -> None:
        """Select an unambiguous registered channel without contacting it.

        Raises:
            HeinzelError: If source or registration is unavailable or ambiguous.
        """
        self.kernel = kernel
        self.contract: type[ApprovalChannel | NotificationChannel] = (
            ApprovalChannel if kind == "approval" else NotificationChannel
        )
        candidates = [
            (record, let)
            for record, let in kernel.lets(ServiceLet)
            if let.provides is not self.contract and issubclass(let.provides, self.contract)
        ]
        matches: list[tuple[ModuleRecord, CapturedEntry, ApprovalChannel | NotificationChannel]] = []
        discovery = kernel.source_discovery
        if discovery is None:
            msg = "Channel delivery requires a source-bound transport"
            raise HeinzelError(msg)
        for record, _let in candidates:
            capture = discovery.service_capture(kernel, record, self.contract)
            service = capture.service(kernel, record, self.contract)
            if service.name == name:
                matches.append((record, capture, service))
        if len(matches) != 1:
            msg = "Choose one available source-bound channel"
            raise HeinzelError(msg)
        self.record, self.capture, self.service = matches[0]
        self.name = name
        self.binding_id = arguments_hash(
            [
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

    def _check(self) -> None:
        config = Config.load(Path.home()).settings(Settings, "hz")
        if (
            not feature_enabled(self.kernel.settings, "connected_channels")
            or not feature_enabled(config, "connected_channels")
            or config.disabled_modules != self.kernel.settings.disabled_modules
            or self.capture.service(self.kernel, self.record, self.contract) is not self.service
            or self.service.name != self.name
        ):
            msg = "Channel delivery is disabled or its source changed"
            raise HeinzelError(msg)

    def call[T](self, action: Callable[[ApprovalChannel | NotificationChannel], T]) -> T:
        """Recheck registered source and feature consent around one call.

        Raises:
            HeinzelError: If the transport's continuity changed.
        """
        self._check()
        result = action(self.service)
        self._check()
        return result

    def target(self) -> ChannelTarget:
        """Observe a concrete destination; legacy channels fail closed.

        Raises:
            HeinzelError: If the channel cannot identify its posting destination and scope.
        """
        target = self.call(lambda service: service.delivery_target())
        if target is None:
            msg = "This channel cannot review its exact posting destination and scope"
            raise HeinzelError(msg)
        return ChannelTarget.model_validate_json(target.model_dump_json())

    def actor(self) -> str | None:
        """Observe the signed-in owner identity for approval channels; never read a reply's text for authority."""
        return self.call(lambda service: cast("ApprovalChannel", service).owner_actor())
